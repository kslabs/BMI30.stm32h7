"""Cohort-aligned *approximate* STM8/16 packets from verified STM4 ROI200.

The caller must verify STM4, frozen DC0 and RX0 from its independent metadata.
This module checks wire sequence/DMA/phase lineage, never recovers ADC samples,
and never simulates STM8/16 LatestWins, USB queues or acquisition timestamps.
"""
from collections import Counter
from copy import deepcopy

import numpy as np

MASK = (1 << 32)-1
HALF = 1 << 31


class CohortAverager:
    """Group2/4 nonoverlapping STM4 blocks, with one shared phase/ADC origin.

    ``on_reset(reason, detail)`` must break downstream histories/confirmations.
    Call ``reset`` on external CRC/transfer/parser errors. ``offset`` skips that
    many full paired-phase STM4 cohorts after each newly established origin.
    Synthetic seq is consecutive per ADC and is never reused after a reset.
    """
    def __init__(self, group_n, offset=0, on_reset=None):
        if type(group_n) is not int or group_n not in (2, 4):
            raise ValueError('Only group2(STM8) and group4(STM16) are supported')
        if type(offset) is not int or not 0 <= offset < group_n:
            raise ValueError('Offset must be in0..group_n-1')
        self.group_n, self.offset, self.on_reset = group_n, offset, on_reset
        self.epoch = 0
        self.stats = Counter()
        self.reset_counts = Counter()
        self.anchors = []
        self.synthetic_seq = [0, 0]
        self._clear()

    def _clear(self):
        self.anchor = None
        self.pending = []
        self.groups = {}
        self.last_seq = {}
        self.last_dma = {}

    def reset(self, reason, detail=None):
        """Discard every incomplete ADC/phase group and seek a fresh anchor."""
        detail = dict(detail or {})
        self.stats['discarded_partial_group_packets'] += sum(len(g) for g in self.groups.values())
        self.stats['discarded_unanchored_packets'] += len(self.pending)
        self.reset_counts[str(reason)] += 1
        self.epoch += 1
        self._clear()
        if self.on_reset is not None:
            self.on_reset(str(reason), dict(detail, epoch=self.epoch))

    def _input(self, frame, payload):
        for key in ('channel', 'phase', 'seq', 'reserved', 'timestamp_ticks', 'reserved2', 'samples'):
            if key not in frame or isinstance(frame[key], bool) or not isinstance(frame[key], (int, np.integer)):
                raise ValueError('Missing/noninteger frame field: '+key)
        if frame['channel'] not in (0, 1) or frame['phase'] not in (0, 1):
            raise ValueError('Expected ADC0/1 and phase0/1')
        if frame['samples'] != 200 or len(payload) != 400:
            raise ValueError('Only complete ROI200 u16 payloads are supported')
        if frame['reserved2'] & 1 != frame['phase']:
            raise ValueError('Phase disagrees with transported reserved2')
        if not isinstance(frame.get('completion_timing'), dict):
            raise ValueError('Source completion timing is required')
        mono = frame['completion_timing'].get('host_monotonic')
        if not isinstance(mono, (int, float)) or not np.isfinite(mono):
            raise ValueError('Finite source host_monotonic is required')
        return deepcopy(frame), np.frombuffer(payload, dtype='<u2').astype(np.int64)

    def _try_anchor(self):
        cohorts = {}
        for f, samples in self.pending:
            key = (int(f['reserved']) & MASK, int(f['phase']))
            cohorts.setdefault(key, {})[int(f['channel'])] = (f, samples)
        for (dma, phase), pair in cohorts.items():
            mate = cohorts.get(((dma+1) & MASK, 1-phase))
            if len(pair) != 2 or mate is None or len(mate) != 2:
                continue
            a, b = pair[0][0], pair[1][0]
            c, d = mate[0][0], mate[1][0]
            if (a['seq'], a['timestamp_ticks']) != (b['seq'], b['timestamp_ticks']):
                continue
            if (c['seq'], c['timestamp_ticks']) != (d['seq'], d['timestamp_ticks']):
                continue
            if ((int(c['seq'])-int(a['seq'])) & MASK) != 1:
                continue
            self.anchor = {'first_phase':phase, 'dma_by_phase':{phase:dma, 1-phase:(dma+1) & MASK},
                           'first_source_seq':int(a['seq']) & MASK, 'epoch':self.epoch}
            self.anchors.append(deepcopy(self.anchor))
            pending, self.pending = self.pending, []
            for f, samples in pending:
                delta = (int(f['reserved'])-self.anchor['dma_by_phase'][int(f['phase'])]) & MASK
                if delta >= HALF:
                    self.stats['initial_partial_packets_skipped'] += 1
                else:
                    # Only the two first complete phase cohorts can be present
                    # when the anchor is first established; group_n>=2 emits0.
                    made = self._group(f, samples)
                    if made is not None:
                        raise RuntimeError('Unexpected output while establishing shared anchor')
            return

    def _group(self, frame, samples):
        ch, phase = int(frame['channel']), int(frame['phase'])
        delta = (int(frame['reserved'])-self.anchor['dma_by_phase'][phase]) & MASK
        if delta >= HALF or delta % 8:
            raise ValueError('Source DMA is not aligned to the common STM4 origin')
        cycle = delta//8
        expected_seq = (self.anchor['first_source_seq']+2*cycle+(phase != self.anchor['first_phase'])) & MASK
        if int(frame['seq']) & MASK != expected_seq:
            raise ValueError('ADC/phase source sequence disagrees with the common cohort origin')
        if cycle < self.offset:
            self.stats['offset_packets_skipped'] += 1
            return None
        index, position = divmod(cycle-self.offset, self.group_n)
        key = (ch, phase)
        group = self.groups.setdefault(key, [])
        if len(group) != position:
            raise ValueError('A partial synthetic cohort would bridge a source gap')
        group.append((frame, samples))
        if len(group) < self.group_n:
            return None
        total = np.sum([item[1] for item in group], axis=0, dtype=np.int64)
        averaged = ((total+self.group_n//2)//self.group_n).astype('<u2')
        output = deepcopy(frame)
        output.update(seq=self.synthetic_seq[ch], synthetic=True, synthetic_stm_n=4*self.group_n,
                      crc_validity='not_applicable_synthetic', anchor_epoch=self.epoch)
        if 'flags' in output:
            output['flags'] = int(output['flags']) & ~4
        output['provenance'] = {'source_stm_n':4, 'group_n':self.group_n, 'offset':self.offset,
            'group_index':index, 'anchor':deepcopy(self.anchor),
            'source_sequences':[int(f['seq']) & MASK for f,_ in group],
            'source_dma':[int(f['reserved']) & MASK for f,_ in group],
            'source_phases':[int(f['phase']) for f,_ in group],
            'source_timestamp_ticks':[int(f['timestamp_ticks']) for f,_ in group],
            'source_completion_timings':[deepcopy(f['completion_timing']) for f,_ in group],
            'rounded_STM4_input':True, 'source_ADC_recovered':False,
            'rounding_uncertainty':'Synthetic STM8/16 may differ by1u16 count per point from direct raw averaging; later rounding can further differ.',
            'timestamp_policy':'Completion/header timestamp of last constituent STM4 packet; not a reconstructed acquisition clock.'}
        self.synthetic_seq[ch] = (self.synthetic_seq[ch]+1) & MASK
        self.stats['synthetic_packets_ch'+str(ch)] += 1
        self.groups[key] = []
        return output, averaged.tobytes()

    def feed(self, frame, payload):
        """Return one synthetic frame/payload orNone; no devices or I/O."""
        try:
            frame, samples = self._input(frame, payload)
        except (ValueError, TypeError) as error:
            self.reset('invalid_source_frame', {'error':str(error)})
            raise
        self.stats['input_packets'] += 1
        ch, phase, seq, dma = int(frame['channel']), int(frame['phase']), int(frame['seq']) & MASK, int(frame['reserved']) & MASK
        previous = self.last_seq.get(ch)
        previous_dma = self.last_dma.get((ch, phase))
        if previous is not None and ((seq-previous) & MASK) != 1:
            self.reset('source_USB_sequence_gap', {'channel':ch, 'before':previous, 'after':seq})
        elif previous_dma is not None and ((dma-previous_dma) & MASK) != 8:
            self.reset('source_phase_DMA_stride', {'channel':ch, 'phase':phase, 'before':previous_dma, 'after':dma})
        self.last_seq[ch], self.last_dma[(ch, phase)] = seq, dma
        if self.anchor is None:
            self.pending.append((frame, samples))
            if len(self.pending) > 32:
                self.pending.pop(0); self.stats['initial_unanchored_packets_dropped'] += 1
            self._try_anchor()
            return None
        try:
            return self._group(frame, samples)
        except ValueError as error:
            self.reset('source_cohort_alignment', {'error':str(error), 'channel':ch, 'seq':seq, 'dma':dma})
            self.last_seq[ch], self.last_dma[(ch, phase)] = seq, dma
            self.pending.append((frame, samples))
            return None

    def diagnostics(self):
        return {'group_n':self.group_n, 'synthetic_stm_n':4*self.group_n, 'offset':self.offset,
            'anchor_policy':'First complete two-ADC opposite-phase cohorts with DMAends+1 and USBseq+1; common DMA/USB origin for all ADC/phases.',
            'anchor_established':self.anchor is not None, 'current_anchor':deepcopy(self.anchor),
            'anchor_count':len(self.anchors), 'anchors':deepcopy(self.anchors), 'epoch':self.epoch,
            'counts':dict(self.stats), 'resets':dict(self.reset_counts),
            'pending_unanchored_packets':len(self.pending),
            'partial_group_packets':sum(len(g) for g in self.groups.values()),
            'caller_must_verify':['STM4','DC frozen0','RX fixed0','CRC-verified original frames'],
            'exact_raw_ADC_reconstruction':False, 'latest_wins_or_USB_worker_simulated':False}
