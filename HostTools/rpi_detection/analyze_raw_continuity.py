"""Independently inspect a passive capture without changing a device or recorder.

Wire fields follow usb_vendor_app.c vnd_frame_hdr_t and usb_stream.py HDR_FMT:
32 bytes LE, CRC16-CCITT-FALSE over header[0:30]+u16 payload. Timestamp is
HAL_GetTick in the inspected firmware (milliseconds); raw ticks remain visible.
`reserved` is a source/DMA generation, not an ADC-sample counter. Its increments
depend on stream mode, averaging and FIFO policy. No ADC-loss count is inferred.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from collections import Counter, OrderedDict, deque
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import struct

HEADER = struct.Struct('<HBBIIHHIIIHH')
UINT32 = 1 << 32
HALF32 = 1 << 31
AVERAGE_DEPTHS = (2, 4, 8, 16, 24, 32, 40, 48, 56, 64)


def delta32(current, previous):
    """Both modulo-u32 and signed short-distance deltas; half range is ambiguous."""
    unsigned = (int(current)-int(previous)) & 0xffffffff
    signed = unsigned if unsigned < HALF32 else unsigned-UINT32
    return unsigned, signed


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Distribution:
    """Exact count/extrema/mean plus bounded deterministic quantile sampling."""
    def __init__(self, capacity=4096):
        self.count = 0
        self.minimum = self.maximum = None
        self.mean = self.m2 = 0.
        self.sample = []
        self.capacity = capacity
        self.random = random.Random(0)

    def add(self, value):
        if not finite(value):
            return
        self.count += 1
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        difference = value-self.mean
        self.mean += difference/self.count
        self.m2 += difference*(value-self.mean)
        if len(self.sample) < self.capacity:
            self.sample.append(value)
        else:
            index = self.random.randrange(self.count)
            if index < self.capacity:
                self.sample[index] = value

    def summary(self):
        ordered = sorted(self.sample)
        def percentile(fraction):
            if not ordered:
                return None
            position = fraction*(len(ordered)-1)
            low = math.floor(position)
            high = math.ceil(position)
            return ordered[low]+(ordered[high]-ordered[low])*(position-low)
        return {'count': self.count, 'min': self.minimum, 'max': self.maximum,
                'mean': self.mean if self.count else None,
                'stddev': math.sqrt(max(0,self.m2/self.count)) if self.count else None,
                'p50': percentile(.5), 'p95': percentile(.95), 'p99': percentile(.99),
                'quantile_sample_size': len(ordered), 'quantiles_approximate': self.count>len(ordered)}


class DeltaStats:
    def __init__(self):
        self.signed = Distribution()
        self.unsigned = Distribution()
        self.values = Counter()
        self.other = 0
        self.classes = Counter()
        self.forward_delta_total = 0

    def add(self, current, previous):
        unsigned, signed = delta32(current, previous)
        self.signed.add(signed)
        self.unsigned.add(unsigned)
        if signed in self.values or len(self.values)<1024:
            self.values[signed] += 1
        else:
            self.other += 1
        category = ('duplicate' if unsigned==0 else 'half_range_ambiguous' if unsigned==HALF32
                    else 'forward' if signed>0 else 'backward_or_reset')
        self.classes[category] += 1
        if category=='forward':
            self.forward_delta_total += unsigned
        if signed>0 and current<previous:
            self.classes['forward_wrap'] += 1
        return unsigned, signed, category

    def summary(self):
        return {'signed':self.signed.summary(), 'unsigned_modulo':self.unsigned.summary(),
                'most_common_signed_deltas':{str(k):v for k,v in self.values.most_common(32)},
                'histogram_other_observations':self.other, 'classes':dict(self.classes),
                'forward_delta_total':self.forward_delta_total}


class Headroom:
    def __init__(self, near_rail_margin):
        self.margin = near_rail_margin
        self.count = self.total = 0
        self.minimum = self.maximum = None
        self.zero = self.full = self.near_low = self.near_high = 0

    def add(self, payload):
        for (value,) in struct.iter_unpack('<H', payload):
            self.count += 1
            self.total += value
            self.minimum = value if self.minimum is None else min(value,self.minimum)
            self.maximum = value if self.maximum is None else max(value,self.maximum)
            self.zero += value==0
            self.full += value==65535
            self.near_low += value<=self.margin
            self.near_high += value>=65535-self.margin

    def summary(self):
        mean = self.total/self.count if self.count else None
        return {'samples':self.count, 'min_u16':self.minimum, 'max_u16':self.maximum,
                'mean_u16':mean, 'mean_offset_from_32768':mean-32768 if mean is not None else None,
                'zero_samples':self.zero, 'full_scale_samples':self.full,
                'near_rail_margin_u16':self.margin, 'near_low_samples':self.near_low,
                'near_high_samples':self.near_high,
                'minimum_rail_margin_u16':min(self.minimum,65535-self.maximum) if self.count else None}


class FrameDecoder:
    """Streaming reassembly independent of recorder WireCheck; no payload archive."""
    def __init__(self, on_frame, on_problem, max_samples=8192):
        self.on_frame, self.on_problem = on_frame, on_problem
        self.max_samples = max_samples
        self.buffer = bytearray()
        self.position = self.total_bytes = 0
        self.segments = deque()
        self.counts = Counter()

    def _discard(self, count):
        del self.buffer[:count]
        self.position += count
        while self.segments and self.segments[0][0]<=self.position:
            self.segments.popleft()

    def feed(self, payload, timing):
        if not payload:
            return
        self.buffer.extend(payload)
        self.total_bytes += len(payload)
        self.segments.append((self.total_bytes,timing))
        while True:
            pos = self.buffer.find(b'\x5a\xa5')
            if pos<0:
                discard = len(self.buffer)-(1 if self.buffer[-1:]==b'\x5a' else 0)
                self.counts['non_frame_bytes'] += discard
                self._discard(discard)
                return
            if pos:
                self.counts['non_frame_bytes'] += pos
                self._discard(pos)
            if len(self.buffer)<HEADER.size:
                return
            magic, version, flags, seq, ticks, samples, zones, offset, length, dma, r2, crc = HEADER.unpack_from(self.buffer)
            if version!=1 or flags & 3 not in (1,2) or not 1<=samples<=self.max_samples:
                self.counts['rejected_magic_candidates'] += 1
                self._discard(1)
                continue
            size = HEADER.size+2*samples
            if len(self.buffer)<size:
                return
            header = bytes(self.buffer[:30])
            frame_payload = bytes(self.buffer[HEADER.size:size])
            calculated = binascii.crc_hqx(header+frame_payload,0xffff)
            context = {'wire_offset':self.position, 'seq':seq,'channel':0 if flags&1 else 1,
                       'samples':samples,'reserved':dma,'reserved2':r2,'timestamp_ticks':ticks}
            if not flags & 4 or calculated!=crc:
                kind = 'crc_missing_candidates' if not flags & 4 else 'crc_invalid_candidates'
                self.counts[kind] += 1
                self.on_problem(kind,{**context,'crc_expected':crc,'crc_calculated':calculated})
                # Re-scan instead of trusting a potentially corrupted payload length.
                self._discard(1)
                continue
            self.counts['crc_verified_frames'] += 1
            first_timing = self.segments[0][1] if self.segments else timing
            frame = {**context,'version':version,'flags':flags,'phase':r2&1,'buffer_index':r2&7,
                     'zone_count':zones,'zone1_offset':offset,'zone1_length':length,
                     'header_timing':first_timing,'completion_timing':timing}
            self.on_frame(frame,frame_payload)
            self._discard(size)

    def break_stream(self):
        self.counts['fragment_bytes_discarded_after_record_error'] += len(self.buffer)
        self._discard(len(self.buffer))

    def summary(self):
        return {**dict(self.counts),'wire_bytes':self.total_bytes,
                'trailing_fragment_bytes':len(self.buffer),
                'trailing_fragment_starts_with_magic':self.buffer[:2]==b'\x5a\xa5'}


class ContinuityGroup:
    def __init__(self, analyzer, channel, phase=None):
        self.analyzer, self.channel, self.phase = analyzer, channel, phase
        self.count = 0
        self.previous = self.first = self.last = None
        self.seq, self.dma, self.ticks = DeltaStats(), DeltaStats(), DeltaStats()
        self.kernel_interval = Distribution()
        self.host_interval = Distribution()
        self.host_minus_kernel_interval = Distribution()
        self.samples, self.phases, self.buffers = Counter(), Counter(), Counter()
        self.r2_bits = Counter()
        self.repeated_phases = self.seq_forward_jumps = self.unobserved_seq_numbers = 0
        self.seq_equals_reserved = 0
        self.headroom = Headroom(analyzer.near_rail_margin)

    def add(self, frame, payload):
        self.count += 1
        self.seq_equals_reserved += frame['seq']==frame['reserved']
        self.first = self.first or frame
        self.last = frame
        self.samples[frame['samples']] += 1
        self.phases[frame['phase']] += 1
        self.buffers[frame['buffer_index']] += 1
        for bit in range(3,16):
            if frame['reserved2'] & 1<<bit:
                self.r2_bits[bit] += 1
        self.headroom.add(payload)
        previous = self.previous
        if previous is not None:
            unsigned,signed,category = self.seq.add(frame['seq'],previous['seq'])
            self.dma.add(frame['reserved'],previous['reserved'])
            _,tick_delta,tick_category = self.ticks.add(frame['timestamp_ticks'],previous['timestamp_ticks'])
            if self.phase is None:
                if signed>1:
                    self.seq_forward_jumps += 1
                    self.unobserved_seq_numbers += signed-1
                    self.analyzer.problem('stream_seq_forward_jump',self._context(frame,previous,delta=signed))
                elif category!='forward':
                    self.analyzer.problem('stream_seq_'+category,self._context(frame,previous,delta=signed))
                if frame['phase']==previous['phase']:
                    self.repeated_phases += 1
                    self.analyzer.problem('same_phase_adjacent',self._context(frame,previous))
                if tick_category in ('backward_or_reset','half_range_ambiguous'):
                    self.analyzer.problem('timestamp_'+tick_category,self._context(frame,previous,delta=tick_delta))
            interval_values = {}
            for key, target in (('usb_at',self.kernel_interval),('host_monotonic',self.host_interval)):
                a,b = frame['completion_timing'].get(key),previous['completion_timing'].get(key)
                if finite(a) and finite(b):
                    value = (a-b)*1000
                    target.add(value)
                    interval_values[key] = value
                    if self.phase is None and (value<0 or value>self.analyzer.large_interval_ms):
                        self.analyzer.problem(key+'_interval',self._context(frame,previous,interval_ms=value))
            if len(interval_values)==2:
                self.host_minus_kernel_interval.add(interval_values['host_monotonic']-interval_values['usb_at'])
        self.previous = frame

    def _context(self, frame, previous, **extra):
        return {'channel':self.channel,'seq':frame['seq'],'previous_seq':previous['seq'],
                'phase':frame['phase'],'previous_phase':previous['phase'],
                'reserved':frame['reserved'],'previous_reserved':previous['reserved'],
                'wire_offset':frame['wire_offset'],**extra}

    def summary(self):
        def edge(frame):
            if frame is None:
                return None
            return {k:frame[k] for k in ('seq','timestamp_ticks','reserved','reserved2','phase','samples','wire_offset','completion_timing')}
        return {'crc_verified_frames':self.count,'first':edge(self.first),'last':edge(self.last),
                'seq_equals_reserved_frames':self.seq_equals_reserved,
                'sample_count_frames':dict(self.samples),'phase_frames':dict(self.phases),'buffer_index_frames':dict(self.buffers),
                'reserved2_high_bit_set_frames':dict(self.r2_bits),
                'stream_seq_deltas':self.seq.summary(),'dma_reserved_deltas':self.dma.summary(),
                'timestamp_tick_deltas':self.ticks.summary(),
                'stream_seq_forward_jumps':self.seq_forward_jumps if self.phase is None else None,
                'unobserved_sequence_numbers_between_verified_frames':self.unobserved_seq_numbers if self.phase is None else None,
                'same_phase_adjacent':self.repeated_phases if self.phase is None else None,
                'kernel_completion_interval_ms':self.kernel_interval.summary(),
                'host_reader_interval_ms':self.host_interval.summary(),
                'host_minus_kernel_interval_ms':self.host_minus_kernel_interval.summary(),
                'received_u16_headroom':self.headroom.summary()}


class ContinuityAnalyzer:
    def __init__(self, near_rail_margin=64, large_interval_ms=100., anomaly_limit=100):
        self.near_rail_margin,self.large_interval_ms = near_rail_margin,large_interval_ms
        self.anomaly_limit = anomaly_limit
        self.records,self.anomalies = Counter(),Counter()
        self.examples = []
        self.example_kinds = Counter()
        self.decoder = FrameDecoder(self.frame,self.problem)
        self.channels = [ContinuityGroup(self,ch) for ch in (0,1)]
        self.phases = [[ContinuityGroup(self,ch,p) for p in (0,1)] for ch in (0,1)]
        self.ready,self.end = None,None
        self.transfer_previous = None
        self.transfer_kernel,self.transfer_host,self.transfer_delay_delta = Distribution(),Distribution(),Distribution()
        self.transfer_lengths = Counter()
        self.settings = OrderedDict()
        self.recorded_stream_modes = set()
        self.acquisition_profiles = OrderedDict()
        self.acquisition_profile_overflow = 0
        self.status_first,self.status_last = None,None
        self.pending = [OrderedDict(),OrderedDict()]
        self.pairs = Counter()
        self.pair_ticks,self.pair_dma,self.pair_kernel,self.pair_host = Distribution(),Distribution(),Distribution(),Distribution()

    def problem(self, kind, context):
        self.anomalies[kind] += 1
        if len(self.examples)<self.anomaly_limit and self.example_kinds[kind]<max(1,self.anomaly_limit//10):
            self.examples.append({'kind':kind,**context})
            self.example_kinds[kind] += 1

    def frame(self, frame, payload):
        ch = frame['channel']
        self.channels[ch].add(frame,payload)
        self.phases[ch][frame['phase']].add(frame,payload)
        seq = frame['seq']
        other = self.pending[1-ch].pop(seq,None)
        if other is None:
            self.pending[ch][seq] = frame
            if len(self.pending[ch])>2048:
                self.pending[ch].popitem(last=False)
                self.pairs['unmatched_evicted_channel_'+str(ch)] += 1
            return
        a,b = (frame,other) if ch==0 else (other,frame)
        self.pairs['equal_seq_pairs'] += 1
        self.pairs['phase_match' if a['phase']==b['phase'] else 'phase_mismatch'] += 1
        self.pairs['sample_count_match' if a['samples']==b['samples'] else 'sample_count_mismatch'] += 1
        self.pair_ticks.add(delta32(b['timestamp_ticks'],a['timestamp_ticks'])[1])
        self.pair_dma.add(delta32(b['reserved'],a['reserved'])[1])
        for key,target in (('usb_at',self.pair_kernel),('host_monotonic',self.pair_host)):
            left,right = a['completion_timing'].get(key),b['completion_timing'].get(key)
            if finite(left) and finite(right):
                target.add((right-left)*1000)

    def status(self, status):
        if not isinstance(status,dict):
            return
        self.status_first = self.status_first or status
        self.status_last = status
        mode = status.get('mode')
        if isinstance(mode,dict) and isinstance(mode.get('stream_mode'),int):
            self.recorded_stream_modes.add(mode['stream_mode'])
        averaging = status.get('averaging') or {}
        profile = {key:mode.get(key) if isinstance(mode,dict) else None
                   for key in ('selected','stream_mode','base_buf_len','avg_n','rpi_avg_n')}
        profile['raspberry_depth'] = averaging.get('depth') if isinstance(averaging,dict) else None
        profile_key = json.dumps(profile,sort_keys=True,separators=(',',':'))
        if profile_key in self.acquisition_profiles:
            self.acquisition_profiles[profile_key]['snapshots'] += 1
        elif len(self.acquisition_profiles)<128:
            self.acquisition_profiles[profile_key] = {'profile':profile,'snapshots':1}
        else:
            self.acquisition_profile_overflow += 1
        settings = {'mode':mode,'raspberry_averaging':{key:averaging.get(key)
                    for key in ('depth','policy','active','detector_depth','display_only')}}
        key = json.dumps(settings,sort_keys=True,separators=(',',':'))
        if key in self.settings:
            self.settings[key]['snapshots'] += 1
        elif len(self.settings)<128:
            self.settings[key] = {'settings':settings,'snapshots':1}
        else:
            self.problem('settings_variant_limit',{})

    def record(self, record, line_number):
        kind = record.get('kind','unknown')
        if self.end is not None:
            self.problem('record_after_end',{'line':line_number,'record_kind':kind})
        self.records[kind] += 1
        if kind=='ready':
            self.ready = self.ready or record
        if kind=='end':
            self.end = record
        self.status(record.get('status'))
        if kind!='transfer':
            return
        timing = {'transfer_index':self.records['transfer'],'usb_at':record.get('usb_at'),
                  'host_monotonic':record.get('monotonic'),'host_epoch':record.get('at')}
        previous = self.transfer_previous
        if previous is not None:
            intervals = {}
            for key,target in (('usb_at',self.transfer_kernel),('host_monotonic',self.transfer_host)):
                a,b = timing.get(key),previous.get(key)
                if finite(a) and finite(b):
                    intervals[key] = (a-b)*1000
                    target.add(intervals[key])
            if len(intervals)==2:
                self.transfer_delay_delta.add(intervals['host_monotonic']-intervals['usb_at'])
        self.transfer_previous = timing
        if record.get('result',0)!=0:
            self.problem('usb_completion_error',{'line':line_number,'result':record.get('result')})
        if record.get('truncated') or record.get('captured')!=record.get('length'):
            self.problem('usb_transfer_truncated',{'line':line_number,'length':record.get('length'),'captured':record.get('captured')})
        try:
            data = base64.b64decode(record['data'],validate=True)
        except (ValueError,TypeError,KeyError,binascii.Error):
            self.problem('invalid_transfer_base64',{'line':line_number})
            self.decoder.break_stream()
            return
        if len(data)!=record.get('captured'):
            self.problem('recorded_payload_length_mismatch',{'line':line_number,'decoded_bytes':len(data),'captured':record.get('captured')})
        self.transfer_lengths[len(data)] += 1
        self.decoder.feed(data,timing)

    def summary(self):
        remote = (self.end or {}).get('summary',{})
        metadata = (self.ready or {}).get('metadata',{})
        integrity = {'ready_records':self.records['ready'],'end_records':self.records['end'],
            'remote_capture_complete':remote.get('capture_complete'),
            'queue_dropped':remote.get('queue_dropped',0) if self.end else None,
            'kernel_dropped':remote.get('kernel_dropped') if self.end else None,
            'remote_errors':remote.get('errors'),'transfers_match_remote_count':self.records['transfer']==remote.get('transfers'),
            'verified_frames_match_remote': [c.count for c in self.channels]==remote.get('wire',{}).get('crc_verified_frames'),
            'config_unchanged':remote.get('config_unchanged'),'core_process_unchanged':remote.get('core_process_unchanged'),
            'monitor_restored':remote.get('monitor_restored'),
            'record_structure_valid':bool(self.records['ready']==1 and self.records['end']==1
                and not any(self.anomalies[k] for k in ('record_after_end','invalid_json_record',
                    'invalid_transfer_base64','recorded_payload_length_mismatch')))}
        transport_keys = ('seq_gap_ch0','seq_gap_ch1','seq_dup_ch0','seq_dup_ch1','crc_bad','magic_bad','host_rx_ack_fail')
        before = (self.status_first or {}).get('stream_stats',{})
        after = (self.status_last or {}).get('stream_stats',{})
        counter_deltas = {k:after[k]-before[k] if finite(after.get(k)) and finite(before.get(k))
                          and after[k]>=before[k] else None for k in transport_keys}
        stream_modes = self.recorded_stream_modes
        def source_coverage(group):
            reason = ('requires_only_recorded_stream_mode_0' if stream_modes!={0}
                      else 'requires_every_verified_seq_equal_reserved' if group.seq_equals_reserved!=group.count
                      else 'requires_at_least_two_frames' if group.count<2
                      else 'counter_reset_backward_or_half_range_ambiguity' if
                        group.dma.classes['backward_or_reset'] or group.dma.classes['half_range_ambiguous']
                      else None)
            if reason is not None:
                return {'available':False,'reason':reason}
            span = group.dma.forward_delta_total+1
            observed = group.count-group.dma.classes['duplicate']
            return {'available':True,'source_counter':'reserved uint32, verified equal to wire seq in recorded mode0',
                    'first_reserved':group.first['reserved'],'last_reserved':group.last['reserved'],
                    'wrap_safe_generation_span_inclusive':span,
                    'crc_verified_observed_generations':observed,
                    'observed_generation_fraction':observed/span,
                    'unobserved_generations_inside_span':span-observed,
                    'scope':'Only first..last observed source generations; no physical ADC-loss inference or edge extrapolation'}
        return {'format':'bmi30-raw-continuity-v1','wire_contract':{
            'header':'<HBBIIHHIIIHH','header_bytes':HEADER.size,'payload':'unsigned little-endian u16',
            'crc':'CRC16-CCITT-FALSE, init0xffff, header[0:30]+payload',
            'phase':'reserved2 & 1','buffer_index':'reserved2 & 7',
            'timestamp':'raw uint32 HAL_GetTick ticks; inspected firmware uses milliseconds',
            'counter_delta':'u32 modulo plus signed short-distance; exactly2^31 ambiguous',
            'source_evidence':['USB_DEVICE/App/usb_vendor_app.c:vnd_frame_hdr_t and vnd_pack_frame_reserved2',
                               'host/usb_vendor/usb_stream.py:HDR_FMT/HDR_SIZE/VF_CRC']},
            'identity':{k:metadata.get(k) for k in ('hostname','serial','root','bundle_version','engine_sha256','usb_parser_sha256','core_pid')},
            'record_counts':dict(self.records),'recording_integrity':integrity,
            'decoder':self.decoder.summary(),'recorded_input_settings':list(self.settings.values()),
            'recorded_stream_modes':sorted(stream_modes),
            'recorded_acquisition_profiles':list(self.acquisition_profiles.values()),
            'acquisition_profile_overflow':self.acquisition_profile_overflow,
            'api_transport_counter_deltas':counter_deltas,
            'transfer_timing':{'kernel_completion_interval_ms':self.transfer_kernel.summary(),
                'host_reader_interval_ms':self.transfer_host.summary(),
                'host_minus_kernel_interval_ms':self.transfer_delay_delta.summary(),
                'captured_bytes_per_transfer':dict(self.transfer_lengths)},
            'channels':{str(ch):{**self.channels[ch].summary(),
                'source_generation_coverage':source_coverage(self.channels[ch]),
                'per_phase':{str(p):self.phases[ch][p].summary() for p in (0,1)}} for ch in (0,1)},
            'equal_seq_cross_channel':{**dict(self.pairs),'unmatched_at_end':[len(p) for p in self.pending],
                'timestamp_B_minus_A_ticks':self.pair_ticks.summary(),'reserved_B_minus_A':self.pair_dma.summary(),
                'kernel_completion_B_minus_A_ms':self.pair_kernel.summary(),'host_reader_B_minus_A_ms':self.pair_host.summary()},
            'observations':dict(self.anomalies),'observation_examples':self.examples,
            'large_interval_reporting_threshold_ms':self.large_interval_ms,
            'limitations':[
                'Received wire payload is after firmware processing, not proof of true raw ADC samples.',
                'No ADC-loss count inferred: DMA/source generations and phase steps depend on mode, averaging and FIFO policy.',
                'Stream-sequence jumps count unobserved logical numbers between CRC-verified frames, not missing ADC acquisitions.',
                'Repeated phases and DMA step distributions are observations; they are not automatically failures.',
                'Kernel and host intervals are USB completion/reader timing, not ADC acquisition timing or absolute USB latency.',
                'Several frames in one transfer legitimately have zero completion interval; capture edges may contain partial frames.',
                'CRC-invalid magic candidates can include false markers during resynchronization; raw bytes remain in the source archive.',
                'Equal-sequence cross-channel comparisons do not establish source simultaneity outside a verified firmware mode.',
                'Rail-valued u16 samples describe received data and do not alone prove physical ADC clipping.',
                'Percentile sampling is bounded and approximate when quantiles_approximate is true; extrema/counts are exact.'
            ]}


def analyze_capture(path, **options):
    analyzer = ContinuityAnalyzer(**options)
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024*1024):
            digest.update(block)
    opener = gzip.open if path.suffix=='.gz' else open
    archive_error = None
    try:
        with opener(path,'rt',encoding='utf-8') as stream:
            for number,line in enumerate(stream,1):
                try:
                    record = json.loads(line)
                    if not isinstance(record,dict):
                        raise ValueError('JSON record is not an object')
                    analyzer.record(record,number)
                except (ValueError,TypeError,KeyError) as exc:
                    analyzer.problem('invalid_json_record',{'line':number,'error':type(exc).__name__})
    except (OSError,EOFError,UnicodeError) as exc:
        archive_error = type(exc).__name__+': '+str(exc)
        analyzer.problem('archive_read_error',{'error':archive_error})
    report = analyzer.summary()
    report.update(source_archive=str(path.resolve()),source_sha256=digest.hexdigest(),
                  source_bytes=path.stat().st_size,archive_read_complete=archive_error is None,
                  archive_error=archive_error)
    return report


def strict_roi_verdict(report, target_hz=400., rate_tolerance_percent=2.):
    """Pure qualification of recorded mode1 ROI source frames, not physical ADC.

    Rate and USB timing remain informative: complete data delivered in a burst
    must not become a false source-loss failure. A damaged monitor/archive is
    INCOMPLETE, rather than proof that the device skipped source generations.
    """
    if not finite(target_hz) or target_hz<=0 or not finite(rate_tolerance_percent) or not 0<=rate_tolerance_percent<100:
        raise ValueError('Invalid informative rate target/tolerance')
    checks = {}
    warnings = []
    def check(name, condition, observed=None, required=True):
        checks[name] = {'status':'UNKNOWN' if condition is None else 'PASS' if condition else 'FAIL',
                        'required_for_source_continuity':required,'observed':observed}
    integrity = report.get('recording_integrity') or {}
    archive_fields = {'archive_read_complete':report.get('archive_read_complete'),
                      **{key:integrity.get(key) for key in ('record_structure_valid','transfers_match_remote_count',
                          'verified_frames_match_remote','config_unchanged','core_process_unchanged','monitor_restored')}}
    errors = integrity.get('remote_errors')
    archive_fields.update(queue_dropped=integrity.get('queue_dropped'),kernel_dropped=integrity.get('kernel_dropped'),
                          remote_errors=errors)
    known = all(value is not None for value in archive_fields.values())
    capture_ok = (all(archive_fields[key] is True for key in ('archive_read_complete','record_structure_valid',
                    'transfers_match_remote_count','verified_frames_match_remote','config_unchanged',
                    'core_process_unchanged','monitor_restored'))
                  and archive_fields['queue_dropped']==0 and archive_fields['kernel_dropped']==0 and errors==[])
    check('recording_integrity',capture_ok if known else None,archive_fields)
    decoder = report.get('decoder') or {}
    crc_bad = decoder.get('crc_invalid_candidates',0)
    crc_missing = decoder.get('crc_missing_candidates',0)
    check('crc_verified_input',crc_bad==0 and crc_missing==0,
          {'crc_invalid_candidates':crc_bad,'crc_missing_candidates':crc_missing})
    modes = report.get('recorded_stream_modes')
    check('mode1_without_stm32_averaging',modes==[1] if modes is not None else None,
          {'recorded_stream_modes':modes,'note':'mode1 bypasses AVG_ROI; saved avg_n does not itself enable averaging'})
    channels = report.get('channels') or {}
    counts = []
    for channel in ('0','1'):
        row = channels.get(channel) or {}
        count = row.get('crc_verified_frames')
        counts.append(count)
        check('channel_'+channel+'_enough_frames',count>=2 if isinstance(count,int) else None,count)
        sample_counts = row.get('sample_count_frames')
        actual_samples = ({int(key):value for key,value in sample_counts.items()}
                          if isinstance(sample_counts,dict) else None)
        check('channel_'+channel+'_roi200',actual_samples=={200:count} if actual_samples is not None and count else None,actual_samples)
        for field,label in (('stream_seq_deltas','usb_seq'),('dma_reserved_deltas','source_dma')):
            stats = (row.get(field) or {}).get('signed') or {}
            ready = isinstance(count,int) and count>=2 and all(stats.get(key) is not None for key in ('count','min','max'))
            check('channel_'+channel+'_'+label+'_plus1',
                  stats['count']==count-1 and stats['min']==stats['max']==1 if ready else None,
                  {key:stats.get(key) for key in ('count','min','max')})
        repeated = row.get('same_phase_adjacent')
        phase_counts = row.get('phase_frames') or {}
        phase0,phase1 = phase_counts.get('0',phase_counts.get(0,0)),phase_counts.get('1',phase_counts.get(1,0))
        check('channel_'+channel+'_phase_alternation',
              repeated==0 and phase0+phase1==count and abs(phase0-phase1)<=1 if count and repeated is not None else None,
              {'same_phase_adjacent':repeated,'phase_frames':phase_counts})
        timing = row.get('kernel_completion_interval_ms') or {}
        average = timing.get('mean')
        rate = 1000./average if finite(average) and average>0 else None
        tolerance = target_hz*rate_tolerance_percent/100
        check('channel_'+channel+'_informative_rate',abs(rate-target_hz)<=tolerance if rate is not None else None,
              {'observed_hz':rate,'target_hz':target_hz,'tolerance_percent':rate_tolerance_percent},required=False)
        threshold = report.get('large_interval_reporting_threshold_ms',100.)
        maximum = timing.get('max')
        if finite(maximum) and finite(threshold) and maximum>threshold:
            warnings.append({'kind':'usb_delivery_pause','channel':channel,'max_kernel_interval_ms':maximum,
                             'reporting_threshold_ms':threshold,'note':'Reporting threshold, not an ADC completeness condition or promised SLA'})
    pairs = report.get('equal_seq_cross_channel') or {}
    paired = pairs.get('equal_seq_pairs')
    unmatched = pairs.get('unmatched_at_end')
    edges = []
    for edge in ('first','last'):
        left,right = (channels.get('0') or {}).get(edge),(channels.get('1') or {}).get(edge)
        edges.append(delta32(right['seq'],left['seq'])[1] if left and right and 'seq' in left and 'seq' in right else None)
    pair_counts_known = (isinstance(paired,int) and paired>0 and all(isinstance(n,int) and n>=2 for n in counts)
                         and isinstance(unmatched,list) and len(unmatched)==2 and all(isinstance(n,int) for n in unmatched)
                         and all(value is not None for value in edges))
    if pair_counts_known:
        full_overlap = (pairs.get('phase_match',0)==paired and pairs.get('sample_count_match',0)==paired
            and pairs.get('phase_mismatch',0)==0 and pairs.get('sample_count_mismatch',0)==0
            and all(0<=n<=2 for n in unmatched) and sum(unmatched)==sum(counts)-2*paired
            and paired>=min(counts)-1 and all(abs(value)<=1 for value in edges)
            and pairs.get('unmatched_evicted_channel_0',0)==0 and pairs.get('unmatched_evicted_channel_1',0)==0)
    else:
        full_overlap = None
    check('cross_channel_full_overlap_and_phase',full_overlap,
          {'matched_pairs':paired,'frame_counts':counts,'unmatched_edges':unmatched,'first_last_seq_B_minus_A':edges})
    reserved_difference = pairs.get('reserved_B_minus_A') or {}
    known = isinstance(paired,int) and paired>0 and all(reserved_difference.get(key) is not None for key in ('count','min','max'))
    check('cross_channel_same_source_dma',
          reserved_difference['count']==paired and reserved_difference['min']==reserved_difference['max']==0 if known else None,
          {key:reserved_difference.get(key) for key in ('count','min','max')})
    tick_difference = pairs.get('timestamp_B_minus_A_ticks') or {}
    if tick_difference.get('min') not in (None,0) or tick_difference.get('max') not in (None,0):
        warnings.append({'kind':'pair_timestamp_difference','observed':tick_difference,
                         'note':'Header time difference is not by itself evidence of missing source generations'})
    profile_names = ('mode1_without_stm32_averaging','channel_0_roi200','channel_1_roi200')
    capture_names = ('recording_integrity','crc_verified_input')
    unknown = any(row['required_for_source_continuity'] and row['status']=='UNKNOWN' for row in checks.values())
    incomplete = unknown or any(checks[key]['status']!='PASS' for key in profile_names+capture_names)
    failed = [key for key,row in checks.items() if row['required_for_source_continuity'] and row['status']=='FAIL']
    verdict = 'INCOMPLETE' if incomplete else 'FAIL' if failed else 'PASS'
    return {'profile':'mode1 ROI200 without STM32 averaging','verdict':verdict,
            'source_frames_complete':True if verdict=='PASS' else False if verdict=='FAIL' else None,
            'checks':checks,'required_failed_checks':failed,'warnings':warnings,
            'scope':'CRC-verified recorded DMA/source-generation frame continuity; no claim about physical ADC samples',
            'rate_and_timing_policy':'Informative rate/timing checks never reject complete source data merely delivered late'}


def strict_average_verdict(report, expected_n, rate_tolerance_percent=2., *, expected_rpi_depth=1):
    """Qualify AVG outputs from every status and each channel/phase DMA stride.

    This does not prove the numerical mean used every underlying ADC sample;
    it qualifies the recorded averaged cohorts and their source metadata.
    Wire frames precede Raspberry averaging: expected_rpi_depth validates the
    saved runtime profile only and never multiplies the STM source DMA stride.
    """
    if type(expected_n) is not int or expected_n not in AVERAGE_DEPTHS:
        raise ValueError('Unsupported expected STM32 average depth')
    if type(expected_rpi_depth) is not int or not 1 <= expected_rpi_depth <= 12:
        raise ValueError('Expected Raspberry average depth must be an integer1..12')
    base = strict_roi_verdict(report,target_hz=400./expected_n,rate_tolerance_percent=rate_tolerance_percent)
    keep = {'recording_integrity','crc_verified_input','cross_channel_full_overlap_and_phase',
            'cross_channel_same_source_dma'}
    for channel in ('0','1'):
        keep.update('channel_'+channel+'_'+suffix for suffix in ('enough_frames','roi200','usb_seq_plus1','informative_rate'))
    checks = {key:row for key,row in base['checks'].items() if key in keep}
    def check(name,condition,observed=None):
        checks[name] = {'status':'UNKNOWN' if condition is None else 'PASS' if condition else 'FAIL',
                        'required_for_source_continuity':True,'observed':observed}
    profiles = report.get('recorded_acquisition_profiles')
    overflow = report.get('acquisition_profile_overflow')
    profile_known = isinstance(profiles,list) and bool(profiles) and overflow==0
    valid = profile_known
    if profile_known:
        for item in profiles:
            profile = item.get('profile') or {}
            valid = bool(valid and type(profile.get('selected')) is int and profile['selected'] in (5,6)
                and type(profile.get('stream_mode')) is int and profile['stream_mode']==2
                and type(profile.get('base_buf_len')) is int and profile['base_buf_len']==200
                and type(profile.get('avg_n')) is int and profile['avg_n']==expected_n
                and type(profile.get('rpi_avg_n')) is int and profile['rpi_avg_n']==expected_rpi_depth
                and type(profile.get('raspberry_depth')) is int and profile['raspberry_depth']==expected_rpi_depth
                and isinstance(item.get('snapshots'),int) and item['snapshots']>0)
    check('every_status_has_expected_avg_profile',valid if profile_known else None,
          {'expected_n':expected_n,'expected_rpi_depth':expected_rpi_depth,
           'recorded_profiles':profiles,'profile_overflow':overflow})
    channels = report.get('channels') or {}
    for channel in ('0','1'):
        phases = (channels.get(channel) or {}).get('per_phase') or {}
        for phase in ('0','1'):
            group = phases.get(phase,phases.get(int(phase))) or {}
            count = group.get('crc_verified_frames')
            # Capture edges are not extrapolated: at least two outputs of each
            # phase are needed to test a full within-phase source stride.
            available = isinstance(count,int) and count>=2
            check('channel_'+channel+'_phase_'+phase+'_has_two_outputs',True if available else None,count)
            stats = (group.get('dma_reserved_deltas') or {}).get('signed') or {}
            known = available and all(stats.get(key) is not None for key in ('count','min','max'))
            check('channel_'+channel+'_phase_'+phase+'_dma_stride',
                  stats['count']==count-1 and stats['min']==stats['max']==2*expected_n if known else None,
                  {'expected_stride':2*expected_n,**{key:stats.get(key) for key in ('count','min','max')}})
    profile_keys = ('every_status_has_expected_avg_profile','channel_0_roi200','channel_1_roi200')
    incomplete = (any(row['required_for_source_continuity'] and row['status']=='UNKNOWN' for row in checks.values())
                  or any(checks[key]['status']!='PASS' for key in profile_keys+('recording_integrity','crc_verified_input')))
    failed = [key for key,row in checks.items() if row['required_for_source_continuity'] and row['status']=='FAIL']
    verdict = 'INCOMPLETE' if incomplete else 'FAIL' if failed else 'PASS'
    return {'profile':f'mode2 AVG_ROI200, Raspberry depth{expected_rpi_depth}',
            'expected_stm32_average':expected_n,'expected_raspberry_depth':expected_rpi_depth,
            'verdict':verdict,'averaged_output_cohorts_complete':True if verdict=='PASS' else False if verdict=='FAIL' else None,
            'checks':checks,'required_failed_checks':failed,'warnings':base['warnings'],
            'scope':'Recorded AVG output cohorts/source metadata; not proof all internal raw samples contributed to the numerical mean',
            'rate_and_timing_policy':base['rate_and_timing_policy']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--near-rail-margin',type=int,default=64)
    parser.add_argument('--large-interval-ms',type=float,default=100.)
    parser.add_argument('--anomaly-limit',type=int,default=100)
    parser.add_argument('--strict-source-continuity',action='store_true',help='Add mode1 ROI200 source qualification; rate/timing remain informative')
    parser.add_argument('--strict-average',type=int,choices=AVERAGE_DEPTHS,help='Qualify mode2 STM AVG wire cohorts at this actual depth')
    parser.add_argument('--expect-rpi-depth',type=int,choices=range(1,13),default=None,
                        help='Expected saved Raspberry depth, default1; requires --strict-average. Wire DMA stride remains2N.')
    args = parser.parse_args()
    if args.expect_rpi_depth is not None and args.strict_average is None:
        parser.error('--expect-rpi-depth requires --strict-average')
    if not 0<=args.near_rail_margin<=32767 or not 0<args.large_interval_ms<1e9 or not 0<=args.anomaly_limit<=10000:
        parser.error('Invalid reporting limits')
    report = analyze_capture(args.capture,near_rail_margin=args.near_rail_margin,
                             large_interval_ms=args.large_interval_ms,anomaly_limit=args.anomaly_limit)
    if args.strict_source_continuity:
        report['strict_source_continuity'] = strict_roi_verdict(report)
    if args.strict_average is not None:
        report['strict_average'] = strict_average_verdict(report,args.strict_average,
            expected_rpi_depth=1 if args.expect_rpi_depth is None else args.expect_rpi_depth)
    output = args.output or args.capture.with_name('continuity.json')
    if output.resolve()==args.capture.resolve():
        parser.error('Output must differ from the source archive')
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output.resolve()),'archive_read_complete':report['archive_read_complete'],
        'crc_verified_frames':{ch:r['crc_verified_frames'] for ch,r in report['channels'].items()},
        'observations':report['observations'],'decoder':report['decoder']},ensure_ascii=False,indent=2))
    return 0 if report['archive_read_complete'] else 1


if __name__=='__main__':
    raise SystemExit(main())
