"""Phase-pair waveform diagnostics from passive USB captures; NOT detector replay.

Runs selected, unmodified numerical helpers from a hash-matched saved engine.
No Qt/engine imports, devices, commands, confirmation/HOLD/output simulation or
predicted hits. In-gate alternatives describe the same product, not a second
production peak search. All time windows use recorded host receive monotonic
time, which differs from physical acquisition time and human reaction time.
"""
from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter, deque
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from analyze_raw_continuity import Distribution, FrameDecoder
from analyze_ring_passes import eligible_passes, normalize_records


DEFAULT_HOST = (Path(__file__).resolve().parents[2] / 'FirmwareReleases/RaspberryPi/'
                '2026-11-03-172055/review/bundle/project/host')
ENGINE_NAME = 'BMI30.200.py.2026-11-03-172055'
CHANNELS = ('upper', 'lower')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024*1024):
            digest.update(block)
    return digest.hexdigest()


def records(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            if not line.endswith('\n'):
                raise ValueError(f'Incomplete JSONL line {number}')
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f'Non-object JSONL line {number}')
                yield row


def extract_definitions(path, wanted, namespace):
    """Compile only selected top-level definitions and their local dependencies."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    definitions = {n.name: n for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    needed, pending = set(wanted), list(wanted)
    while pending:
        node = definitions[pending.pop()]
        for name in {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}:
            if name in definitions and name not in needed:
                needed.add(name)
                pending.append(name)
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    nodes.extend(n for n in tree.body if getattr(n, 'name', None) in needed)
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(path), 'exec'), namespace)
    return sorted(needed)


class DSP:
    def __init__(self, host, settings, expected_engine_hash=None):
        host = Path(host)
        self.engine = host / ENGINE_NAME
        if expected_engine_hash is not None and (not isinstance(expected_engine_hash, str) or len(expected_engine_hash) != 64):
            raise ValueError('A valid captured engine SHA is required')
        if expected_engine_hash and sha256(self.engine) != expected_engine_hash:
            raise ValueError('Capture engine SHA does not match selected numerical source')
        if any(settings.get('tag_filter_slot'+str(i)) for i in (1, 2, 3)):
            raise ValueError('This bounded diagnostic supports empty named-filter slots only')
        if any(settings.get('filter_'+k) for k in ('casino', 'barkhausen', 'microwire', 'paper')):
            raise ValueError('Named-filter profile requires a separate diagnostic review')
        env = {'BMI30_RT_NOISE_DIVISIONS': str(settings['noise_divisions']),
               'BMI30_RT_NOISE_AREA_PERCENT': str(settings['noise_area_percent']),
               'BMI30_RT_SHIFT_SCORE_PENALTY': str(settings['phase_shift_penalty'])}
        ns = {'np': np, 'deque': deque, 'lru_cache': lru_cache,
              'os': SimpleNamespace(getenv=lambda key, default=None: env.get(key, default)),
              '_RT_TAG_RESPONSE_FILTER_SLOTS': ('', '', ''),
              '_RT_DETECT_LEVEL_MAX': (1 << 63)-1,
              'SMOOTH_MODE_NAMES': ('off', 'med3', 'med5', 'avg7', 'avg9', 'avg11')}
        wanted = {'_rt_detector_smooth_array', '_rt_detector_product_current_prev',
                  '_rt_detector_relative_noise_reference', '_rt_detector_peak_position_supported'}
        self.helpers = extract_definitions(self.engine, wanted, ns)
        common = {'np': np, 'isfinite': np.isfinite}
        extract_definitions(host/'bmi30_common_mode.py', {'base_width_decision'}, common)
        dual = {'np': np, 'deque': deque}
        extract_definitions(host/'bmi30_dual_detector.py', {'DualWindowHistory'}, dual)
        self.ns, self.base_width, self.history = ns, common['base_width_decision'], dual['DualWindowHistory']()
        self.settings = settings
        if not 0 <= int(settings['peak_index_min']) <= int(settings['peak_index_max']) < 200:
            raise ValueError('Peak bounds must form an inclusive interval inside ROI200')
        self.source_hashes = {name: sha256(host/name) for name in
                              (ENGINE_NAME, 'bmi30_common_mode.py', 'bmi30_dual_detector.py')}

    def pair_features(self, pair, shift):
        s, ns = self.settings, self.ns
        smooth = [ns['_rt_detector_smooth_array'](x, s['smooth_mode']) for x in pair]
        made = ns['_rt_detector_product_current_prev'](
            *smooth, s['phase_max_shift'], 'prod', 16384.25, fixed_shift=shift)
        if made is None:
            raise ValueError('Production product helper returned no result')
        level, actual_shift, peak, amplitude, product = made
        lo, hi = int(s['peak_index_min']), int(s['peak_index_max'])
        mask = (np.arange(len(product)) >= lo) & (np.arange(len(product)) <= hi)

        def describe(index):
            if index is None:
                return None
            amplitude = float(product[index])
            noise, sector, _ = ns['_rt_detector_relative_noise_reference'](product, index)
            return {'index': int(index), 'product': amplitude, 'noise_ref': float(noise),
                    'noise_sector': int(sector), 'instant_snr': amplitude/noise if noise > 0 else None,
                    'base_width': self.base_width(product, index, s['base_width_max'],
                                                  s['base_width_enabled'], actual_shift)}

        def maximum(selected):
            indices = np.flatnonzero(selected)
            return int(indices[np.argmax(product[indices])]) if len(indices) else None

        feature = {'shift': int(actual_shift), 'level_raw_product': int(level),
                   'global': describe(peak), 'inside_gate': describe(maximum(mask)),
                   'outside_gate': describe(maximum(~mask)),
                   'global_peak_position_ok': bool(ns['_rt_detector_peak_position_supported'](
                       peak, len(product), lo, hi))}
        return feature, smooth, product


def profile(status):
    d = status.get('detector') or {}
    keys = ('enabled', 'confirm_count', 'threshold', 'threshold_high', 'auto_threshold',
            'noise_up', 'noise_down', 'noise_unit', 'burst_gate', 'burst_blank_s', 'burst_max_ratio')
    return {'mode': status.get('mode'), 'settings': d.get('settings'),
            'smoothing': status.get('smoothing'),
            'depth': (status.get('averaging') or {}).get('depth'),
            'channels': {name: {k: cfg.get(k) for k in keys}
                         for name, cfg in (d.get('channels') or {}).items()}}


def prepare(path):
    metadata = [r for r in records(path) if r.get('kind') != 'transfer']
    starts = [r for r in metadata if r.get('kind') == 'ready']
    ends = [r for r in metadata if r.get('kind') == 'end']
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError('Exactly one ready and one final end are required')
    ready, end = starts[0], ends[0]
    if not ready.get('metadata', {}).get('engine_sha256'):
        raise ValueError('Capture must identify the engine SHA')
    initial = profile(ready['status'])
    if any(profile(r['status']) != initial for r in metadata if isinstance(r.get('status'), dict)):
        raise ValueError('Recorded input/detector profile changed within the capture')
    m = ready['status']['mode']
    if (m.get('stream_mode'), m.get('rpi_avg_n'), initial['depth'], m.get('base_buf_len')) != (2, 1, 1, 200):
        raise ValueError('This diagnostic supports AVG_ROI200 with Raspberry depth1 only')
    normalized, _, _ = normalize_records(metadata, channel='upper')
    passes, invalid, pending = eligible_passes(normalized)
    for window in passes:
        window['mark_window_complete'] = True
    # Keep the final physical start useful for diagnostics without inventing an
    # end button or outcome. Its provisional boundary is recording termination.
    for pid in pending:
        matching = [r for r in normalized if r.get('kind') == 'pass_start' and r.get('pass_id') == pid]
        if (type(pid) is int and pid > 0 and len(matching) == 1
                and isinstance(matching[0].get('start_mono'), (int, float))
                and matching[0]['start_mono'] < end['monotonic']
                and not any(r.get('kind') == 'pass_start' and
                            isinstance(r.get('start_mono'), (int, float)) and
                            r['start_mono'] > matching[0]['start_mono'] for r in normalized)):
            passes.append({'pass_id': pid, 'start_mono': matching[0]['start_mono'],
                           'end_mono': end['monotonic'], 'human_result': 'unlabelled',
                           'mark_window_complete': False})
    ignored = [r for r in metadata if r.get('kind') in ('ignored_mark', 'orphan_pass_end')]
    if len(passes) > 1000:
        raise ValueError('Too many pass windows for bounded representatives')
    return ready, end, passes, {'invalid': invalid, 'pending': pending, 'ignored': ignored,
                              'all_recorded_pass_marks': [r for r in metadata if r.get('kind') in ('pass_start', 'pass_end', 'orphan_pass_end', 'ignored_mark')]}


class WindowSummary:
    def __init__(self):
        self.count = 0
        self.metrics = {key: Distribution() for key in
                        ('global_product', 'inside_product', 'outside_product', 'global_snr', 'inside_snr', 'noise_ref')}
        self.outside_wins = 0
        self.representatives = {}

    def add(self, row, arrays):
        self.count += 1
        self.outside_wins += not row['global_peak_position_ok']
        global_, inside, outside = row['global'], row['inside_gate'], row['outside_gate']
        values = {'global_product': global_['product'], 'inside_product': inside['product'],
                  'outside_product': outside['product'] if outside else None, 'global_snr': global_['instant_snr'],
                  'inside_snr': inside['instant_snr'], 'noise_ref': global_['noise_ref']}
        for key, value in values.items():
            self.metrics[key].add(value)
        for kind, score in (('global_product', global_['product']), ('inside_snr', inside['instant_snr'])):
            if score is not None and (kind not in self.representatives or score > self.representatives[kind][0]):
                self.representatives[kind] = (score, dict(row), tuple(np.array(x, copy=True) for x in arrays))

    def summary(self):
        return {'fresh_pairs': self.count, 'global_peak_outside_gate': self.outside_wins,
                'features': {key: value.summary() for key, value in self.metrics.items()}}


def run(path, output, host=DEFAULT_HOST, fixed_shifts=None, gap_margin_s=.1):
    """Write a NEW offline diagnostic directory, never edit capture or runtime."""
    path, output = Path(path), Path(output)
    ready, end, passes, invalid = prepare(path)
    dsp = DSP(host, ready['status']['detector']['settings'], ready['metadata'].get('engine_sha256'))
    depth = int(ready['status']['mode']['avg_n'])
    if not (fixed_shifts is not None and len(fixed_shifts) == 2 and all(type(x) is int and abs(x) < 200 for x in fixed_shifts)):
        raise ValueError('Two explicit fixed RX shifts inside ROI are required')
    if not 0 <= gap_margin_s <= 10:
        raise ValueError('Gap margin must be within 0..10 seconds')
    output.mkdir(parents=True, exist_ok=False)
    groups, gaps, issues, previous, last, frame_counts = {}, [], [], {}, {}, Counter()
    t0 = ready['monotonic']
    stats = {'fresh_pairs': Counter(), 'same_phase_repeats': Counter()}
    feature_stream = gzip.open(output/'features.jsonl.gz', 'wt', encoding='utf-8')

    def problem(kind, detail):
        issues.append({'kind': kind, **detail})
        dsp.history.__init__()  # Do not bridge unverifiable transport input.

    def on_frame(frame, payload):
        ch, phase = frame['channel'], frame['phase']
        frame_counts[ch] += 1
        if frame['samples'] != 200:
            problem('unexpected_roi_length', {'channel': ch, 'seq': frame['seq'], 'samples': frame['samples']})
            return
        mono = frame['completion_timing']['host_monotonic']
        prior = previous.get((ch, phase))
        if prior and ((frame['reserved']-prior['reserved']) & 0xffffffff) != 2*depth:
            gaps.append({'channel': CHANNELS[ch], 'phase': phase,
                         'previous_seq': prior['seq'], 'seq': frame['seq'],
                         'previous_dma': prior['reserved'], 'dma': frame['reserved'],
                         'dma_delta': (frame['reserved']-prior['reserved']) & 0xffffffff,
                         'expected_stride': 2*depth,
                         'start_receive_mono': prior['completion_timing']['host_monotonic'],
                         'end_receive_mono': mono, 'elapsed_s': mono-t0})
        previous[(ch, phase)] = frame
        old = last.get(ch)
        if old and old['phase'] == phase:
            stats['same_phase_repeats'][CHANNELS[ch]] += 1
        if old and ((frame['seq']-old['seq']) & 0xffffffff) != 1:
            problem('wire_seq_discontinuity', {'channel': ch, 'seq': frame['seq']})
        last[ch] = frame
        raw = np.frombuffer(payload, dtype='<u2').astype(np.int32)
        stamp = (1, frame['seq'], frame['timestamp_ticks'], frame['reserved2'], len(raw))
        if not dsp.history.feed(ch, raw, phase, stamp, 0, depth=1):
            return
        pair = dsp.history.pairs[ch]
        feature, smooth, product = dsp.pair_features(pair, fixed_shifts[ch])
        window = next((p for p in passes if p['start_mono'] <= mono < p['end_mono']), None)
        pid = window['pass_id'] if window else None
        row = {'channel': CHANNELS[ch], 'seq': frame['seq'], 'dma': frame['reserved'],
               'updated_phase': phase, 'receive_mono': mono, 'elapsed_s': mono-t0,
               'firmware_timestamp_ticks': frame['timestamp_ticks'],
               'pair_source_seqs_even_odd': [s[1] for s in dsp.history.stamps[ch]],
               'pass_id': pid, **feature}
        feature_stream.write(json.dumps(row, separators=(',', ':'))+'\n')
        key = (pid, ch)
        group = groups.setdefault(key, WindowSummary())
        group.add(row, (*pair, *smooth, product))
        stats['fresh_pairs'][CHANNELS[ch]] += 1

    decoder = FrameDecoder(on_frame, problem)
    transfers = 0
    try:
        for record in records(path):
            if record.get('kind') != 'transfer':
                continue
            transfers += 1
            if record.get('result') or record.get('truncated'):
                problem('transfer_error_or_truncation', {'transfer': transfers})
                decoder.break_stream()
                continue
            data = base64.b64decode(record['data'], validate=True)
            if len(data) != record.get('captured') or record.get('captured') != record.get('length'):
                problem('transfer_length_mismatch', {'transfer': transfers})
                decoder.break_stream()
                continue
            decoder.feed(data, {'usb_at': record['usb_at'], 'host_monotonic': record['monotonic'],
                                'host_epoch': record['at'], 'transfer_index': transfers})
    finally:
        feature_stream.close()

    def overlapping(lo, hi):
        return [g for g in gaps if g['start_receive_mono']-gap_margin_s < hi
                and g['end_receive_mono']+gap_margin_s >= lo]

    rows, arrays, representatives = [], {}, []
    for (pid, ch), group in groups.items():
        window = next((p for p in passes if p['pass_id'] == pid), None)
        row = {'pass_id': pid, 'channel': CHANNELS[ch],
               'window_kind': 'marked_pass' if window else 'outside_valid_pass_windows', **group.summary()}
        if window:
            row.update(human_result=window['human_result'], human_label_channel='upper',
                       human_label_scope='One physical upper-channel pass; duplicated lower context is not a second outcome',
                       mark_window_complete=window['mark_window_complete'], start_mono=window['start_mono'],
                       end_mono=window['end_mono'], cohort_gap_observations=overlapping(window['start_mono'], window['end_mono']))
            row['acquisition_affected_by_cohort_gap'] = bool(row['cohort_gap_observations'])
        rows.append(row)
        for kind, (_, feature, traces) in group.representatives.items():
            key = f"{'pass'+str(pid) if pid is not None else 'outside'}_{CHANNELS[ch]}_{kind}"
            for name, trace in zip(('wire_even', 'wire_odd', 'smooth_even', 'smooth_odd', 'product'), traces):
                arrays[key+'_'+name] = trace
            representatives.append({'array_key_prefix': key, 'selection': kind, **feature})
    np.savez_compressed(output/'representatives.npz', **arrays)
    remote = end.get('summary') or {}
    counts = [frame_counts[ch] for ch in (0, 1)]
    verified = counts == (remote.get('wire') or {}).get('crc_verified_frames')
    result = {'format': 'bmi30-pass-waveform-features-v1', 'exact_replay': False,
              'source_archive': str(path.resolve()), 'source_sha256': sha256(path),
              'metadata': ready['metadata'], 'recorded_profile': profile(ready['status']),
              'numerical_sources_sha256': dsp.source_hashes, 'extracted_engine_helpers': dsp.helpers,
              'alignment_policy': {'fixed_shifts': list(fixed_shifts), 'source': 'explicit_operator_argument_not_derived_from_waveforms'},
              'time_basis': 'host USB receive monotonic, not physical ADC acquisition or exact tag position',
              'gap_margin_s': gap_margin_s, 'gap_margin_policy': 'heuristic for screening; no equivalence guarantee for subsequent noise history',
              'capture_complete_reported': remote.get('capture_complete') is True,
              'wire_frame_counts': counts, 'wire_counts_match_recorder': verified,
              'diagnostic_input_complete': bool(remote.get('capture_complete') and verified and not issues),
              'diagnostic_input_complete_scope': 'Recorded wire/monitor integrity; cohort continuity is separate and is NOT implied',
              'source_cohort_gaps_observed': bool(gaps),
              'decoder': decoder.summary(), 'issues': issues, 'cohort_gaps': gaps,
              'pair_diagnostics': {k: dict(v) for k, v in stats.items()}, 'invalid_pass_marks': invalid,
              'windows': rows, 'representatives': representatives,
              'limitations': [
                  'No detector initial noise/history, threshold, confirmations, burst blanking, worker scheduling, HOLD or outputs replayed.',
                  'No predicted hit/miss or false-alarm rate; human labels remain independent.',
                  'All CRC-verified wire packets are processed, including packets the live worker may not have processed.',
                  'Inside-gate maximum is descriptive on the globally selected shift; production still selects its global peak.',
                  'Outside valid marked windows includes lead-in, return motions, invalid marks and background; it is not automatically a confirmed no-tag control.',
                  'Gap observations are per channel/phase; a shared A/B event is duplicated and must not be counted as two independent losses.',
                  'Source-DMA cohort continuity does not prove every physical ADC sample contributed to each numerical mean.',
                  'Receive-time overlap/margin is a quality flag, not authorization to remove unsuccessful physical passes from the detection rate.',
                  'Representatives select maximum global product and maximum inside SNR; they do not characterize all frames in a pass.',
              ]}
    (output/'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New local diagnostic directory')
    parser.add_argument('--host-dir', type=Path, default=DEFAULT_HOST)
    parser.add_argument('--fixed-shifts', nargs=2, type=int, required=True, metavar=('UPPER', 'LOWER'),
                        help='Explicit verified RX policy; auto-alignment captures are outside this bounded tool')
    parser.add_argument('--gap-margin-s', type=float, default=.1)
    args = parser.parse_args()
    result = run(args.capture, args.output, args.host_dir, tuple(args.fixed_shifts), args.gap_margin_s)
    print(json.dumps({'output': str(args.output), 'diagnostic_input_complete': result['diagnostic_input_complete'],
                      'wire_frame_counts': result['wire_frame_counts'], 'cohort_gap_observations': len(result['cohort_gaps']),
                      'diagnostic_pass_windows': len({r['pass_id'] for r in result['windows'] if r['pass_id'] is not None}),
                      'exact_replay': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
