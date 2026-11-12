"""Compare original Raspberry means on marked USB windows and no-tag controls.

This is a scalar/waveform diagnostic, never a FIRE or detection-rate replay.
Production DSP definitions are loaded by analyze_raw_pass_features, with the
actual saved STM4 wire packets held constant. The diagnostic per-pass horizon
is capped equally, including a long final unlabelled interval.
"""
from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
import time

import numpy as np

from analyze_raw_continuity import FrameDecoder
from analyze_raw_pass_features import (CHANNELS, DEFAULT_HOST, DSP, WindowSummary,
                                       extract_definitions, prepare, profile, records, sha256)

DEPTHS = (1, 2, 3, 4, 6, 8, 12)
RATIOS = (1.2, 1.3, 1.4, 1.6, 2.0, 2.2, 3.0, 4.0)


class NoiseThreshold:
    """Original scalar history/threshold methods on cold receive-time histories."""
    def __init__(self, dsp, recorded_channels):
        ns = dict(dsp.ns, time=time)
        extract_definitions(dsp.engine, {'_rt_reference_noise_statistics', '_det_clamp_ratio'}, ns)
        tree = ast.parse(dsp.engine.read_text(encoding='utf8'))
        names = ('_det_accumulated_reference_noise', '_det_noise_display_units',
                 '_det_manual_curve_ratio', '_det_channel_trigger_level')
        definitions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names}
        nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
        nodes.extend(definitions[name] for name in names)
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(dsp.engine), 'exec'), ns)
        scope = SimpleNamespace(_det_ratio_noise_max_u16=dsp.settings['ratio_noise_max_u16'])
        for ch, name in enumerate(CHANNELS):
            config = recorded_channels[name]
            if config['auto_threshold']:
                raise ValueError('This bounded comparison supports the recorded manual thresholds only')
            setattr(scope, '_det_ratio'+str(ch), config['threshold'])
            setattr(scope, '_det_ratio_high'+str(ch), config['threshold_high'])
        scope._det_noise_window_seconds = lambda: dsp.settings['noise_window_s']
        scope._rt_detector_level_scale = lambda: (32768.*32768.)/65535.
        scope._det_noise_display_units = lambda value: ns['_det_noise_display_units'](scope, value)
        scope._det_ratio_noise_max_u16_value = lambda: scope._det_ratio_noise_max_u16
        scope._det_manual_curve_ratio = lambda ch, value: ns['_det_manual_curve_ratio'](scope, ch, value)
        scope._det_channel_auto_enabled = lambda ch: False
        self.ns, self.scope = ns, scope

    def feed(self, noise, now, ch, peak, position_ok, width_ok, level=None):
        avg = self.ns['_det_accumulated_reference_noise'](self.scope, ch, noise, now)
        ratio = self.scope._det_manual_curve_ratio(ch, avg)
        level = peak if level is None else level
        trigger = self.ns['_det_channel_trigger_level'](self.scope, ch, avg, ratio, int(level))
        instant_ok = bool(noise > 0 and peak > noise*ratio)
        return {'noise_avg': avg, 'noise_mean': getattr(self.scope, '_det_noise_mean'+str(ch)),
                'noise_p95': getattr(self.scope, '_det_noise_p95'+str(ch)), 'manual_ratio': ratio,
                'trigger': trigger, 'above_history_threshold': bool(level >= trigger),
                'instant_noise_ok': instant_ok,
                'scalar_candidate_screen': bool(noise > 0 and avg > 0 and level >= trigger
                                                and instant_ok and position_ok and width_ok)}


def capped_windows(passes, horizon_s):
    return [{**p, 'recorded_end_mono': p['end_mono'],
             'end_mono': min(p['end_mono'], p['start_mono']+horizon_s),
             'duration_s': min(p['end_mono']-p['start_mono'], horizon_s)} for p in passes]


class Aggregate(WindowSummary):
    def __init__(self):
        super().__init__()
        self.global_indices, self.inside_indices, self.width_reasons = Counter(), Counter(), Counter()
        self.global_screen = Counter()
        self.secondary_screen = Counter()
        self.masked_secondary_screen = Counter()
        self.product_total = np.zeros(200, dtype=np.float64)
        self.product_maximum = np.zeros(200, dtype=np.float64)
        self.history_screen = Counter()

    def add(self, row, arrays):
        super().add(row, arrays)
        global_, inside = row['global'], row['inside_gate']
        self.global_indices[global_['index']] += 1
        self.inside_indices[inside['index']] += 1
        self.width_reasons[global_['base_width']['reason']] += 1
        for branch in ('global', 'inside'):
            self.history_screen[branch+'_above_history_threshold'] += row[branch+'_noise_history']['above_history_threshold']
            self.history_screen[branch+'_scalar_candidate_screen'] += row[branch+'_noise_history']['scalar_candidate_screen']
        self.history_screen['global_edge_peak_0_3_196_199'] += global_['index'] < 4 or global_['index'] >= 196
        for ratio in RATIOS:
            inside_ok = inside['instant_snr'] is not None and inside['instant_snr'] > ratio and inside['base_width']['ok']
            self.secondary_screen[str(ratio)] += inside_ok
            self.masked_secondary_screen[str(ratio)] += inside_ok and not row['global_peak_position_ok']
            self.global_screen[str(ratio)] += bool(row['global_peak_position_ok'] and global_['base_width']['ok']
                                                   and global_['instant_snr'] is not None and global_['instant_snr'] > ratio)
        product = arrays[-1]
        self.product_total += product
        np.maximum(self.product_maximum, product, out=self.product_maximum)

    def summary(self):
        return {**super().summary(), 'global_peak_indices': dict(self.global_indices),
                'inside_peak_indices': dict(self.inside_indices), 'global_base_width_reasons': dict(self.width_reasons),
                'cold_noise_history_scalar_screens': dict(self.history_screen),
                'global_peak_instant_snr_and_static_gates_frames': dict(self.global_screen),
                'secondary_inside_peak_instant_snr_and_width_frames': dict(self.secondary_screen),
                'secondary_inside_peak_screen_masked_by_outside_global_frames': dict(self.masked_secondary_screen)}


def analyze_source(path, name, is_passes, output, channels=(0,), horizon_s=5., depths=DEPTHS):
    ready, end, passes, invalid = prepare(path)
    dsp = DSP(DEFAULT_HOST, ready['status']['detector']['settings'], ready['metadata']['engine_sha256'])
    if ready['status']['mode']['avg_n'] != 4:
        raise ValueError('This comparison holds STM32 depth4 constant')
    histories = {depth: type(dsp.history)() for depth in depths}
    models = {(depth, branch): NoiseThreshold(dsp, ready['status']['detector']['channels'])
              for depth in depths for branch in ('global', 'inside')}
    if is_passes:
        windows = capped_windows(passes, horizon_s)
    else:
        start, stop = ready['monotonic']+12., end['monotonic']
        count = int((stop-start)//horizon_s)
        windows = [{'pass_id': i+1, 'start_mono': start+i*horizon_s, 'end_mono': start+(i+1)*horizon_s,
                    'duration_s': horizon_s, 'human_result': None, 'mark_window_complete': True}
                   for i in range(count)]
    groups, gaps, issues, previous = {}, [], [], {}
    frames, pair_counts, same_phase = Counter(), Counter(), Counter()
    last = {}
    series = gzip.open(output/(name+'-features.jsonl.gz'), 'wt', encoding='utf-8')

    def problem(kind, detail):
        issues.append({'kind': kind, **detail})
        for history in histories.values():
            history.__init__()

    def on_frame(frame, payload):
        ch, phase = frame['channel'], frame['phase']
        frames[ch] += 1
        mono = frame['completion_timing']['host_monotonic']
        old = previous.get((ch, phase))
        if old and ((frame['reserved']-old['reserved']) & 0xffffffff) != 8:
            gaps.append({'channel': CHANNELS[ch], 'phase': phase, 'seq': frame['seq'],
                         'previous_seq': old['seq'], 'dma': frame['reserved'],
                         'dma_delta': (frame['reserved']-old['reserved']) & 0xffffffff,
                         'start_receive_mono': old['completion_timing']['host_monotonic'], 'end_receive_mono': mono})
        previous[(ch, phase)] = frame
        if ch not in channels:
            return
        if frame['samples'] != 200:
            problem('unexpected_roi_length', {'channel': ch, 'seq': frame['seq']})
            return
        if ch in last and last[ch] == phase:
            same_phase[ch] += 1
        last[ch] = phase
        window = next((w for w in windows if w['start_mono'] <= mono < w['end_mono']), None)
        raw = np.frombuffer(payload, dtype='<u2').astype(np.int32)
        stamp = (1, frame['seq'], frame['timestamp_ticks'], frame['reserved2'], len(raw))
        for depth, history in histories.items():
            if not history.feed(ch, raw, phase, stamp, 0, depth=depth):
                continue
            feature, smooth, product = dsp.pair_features(history.pairs[ch], 0)
            for branch, source_key, position_ok in (('global', 'global', feature['global_peak_position_ok']),
                                                     ('inside', 'inside_gate', True)):
                peak = feature[source_key]
                feature[branch+'_noise_history'] = models[(depth, branch)].feed(
                    peak['noise_ref'], mono, ch, peak['product'], position_ok, peak['base_width']['ok'])
            if window is None:
                continue
            row = {'source': name, 'window_id': window['pass_id'], 'channel': CHANNELS[ch],
                   'rpi_depth': depth, 'receive_mono': mono, 'elapsed_in_window_s': mono-window['start_mono'],
                   'seq': frame['seq'], 'dma': frame['reserved'], **feature}
            series.write(json.dumps(row, separators=(',', ':'))+'\n')
            group = groups.setdefault((window['pass_id'], ch, depth), Aggregate())
            group.add(row, (*history.pairs[ch], *smooth, product))
            pair_counts[(ch, depth)] += 1

    decoder = FrameDecoder(on_frame, problem)
    try:
        for record in records(path):
            if record.get('kind') != 'transfer':
                continue
            if record.get('result') or record.get('truncated'):
                problem('transfer_error', {})
                decoder.break_stream()
                continue
            data = base64.b64decode(record['data'], validate=True)
            if len(data) != record.get('captured') or record.get('captured') != record.get('length'):
                problem('transfer_length_mismatch', {})
                decoder.break_stream()
                continue
            decoder.feed(data, {'usb_at': record['usb_at'], 'host_monotonic': record['monotonic'], 'host_epoch': record['at']})
    finally:
        series.close()
    rows, representatives, arrays = [], [], {}
    for (wid, ch, depth), group in sorted(groups.items()):
        window = next(w for w in windows if w['pass_id'] == wid)
        affected = [g for g in gaps if g['start_receive_mono']-.1 < window['end_mono']
                    and g['end_receive_mono']+.1 >= window['start_mono']]
        noise_affected = [g for g in gaps if g['start_receive_mono']-.1 < window['end_mono']
                          and g['end_receive_mono']+dsp.settings['noise_window_s']+(depth-1)*.02+.1 >= window['start_mono']]
        row = {'source': name, 'window_id': wid, 'channel': CHANNELS[ch], 'rpi_depth': depth,
               'kind': 'marked_start_horizon' if is_passes else 'separate_no_tag_control_bin',
               'human_result': window.get('human_result'), 'start_mono': window['start_mono'],
               'end_mono': window['end_mono'], 'duration_s': window['duration_s'],
               'recorded_end_mono': window.get('recorded_end_mono'),
               'cohort_gap_affected': bool(affected), 'gap_observations': affected,
               'noise_history_affected_by_source_gap': bool(noise_affected), **group.summary()}
        rows.append(row)
        prefix = f'{name}_w{wid}_{CHANNELS[ch]}_n{depth}'
        arrays[prefix+'_mean_product'] = group.product_total/group.count
        arrays[prefix+'_maximum_product'] = group.product_maximum
        for kind, (_, feature, traces) in group.representatives.items():
            key = prefix+'_'+kind
            for field, trace in zip(('mean_even', 'mean_odd', 'smooth_even', 'smooth_odd', 'product'), traces):
                arrays[key+'_'+field] = trace
            representatives.append({'array_key_prefix': key, 'selection': kind, **feature})
    np.savez_compressed(output/(name+'-waveforms.npz'), **arrays)
    expected = end.get('summary', {}).get('wire', {}).get('crc_verified_frames')
    integrity = bool(end.get('summary', {}).get('capture_complete') and expected == [frames[0], frames[1]] and not issues)
    return {'name': name, 'source_archive': str(Path(path).resolve()), 'source_sha256': sha256(path),
            'source_metadata': ready['metadata'], 'recorded_profile': profile(ready['status']),
            'numerical_sources_sha256': dsp.source_hashes, 'wire_counts': [frames[0], frames[1]],
            'wire_monitor_integrity': integrity, 'cohort_gaps': gaps, 'issues': issues,
            'invalid_marks': invalid, 'window_count': len(windows), 'windows': rows, 'representatives': representatives}


def run(capture, before, after, output, depths=DEPTHS, horizon_s=5., channels=(0,)):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    sources = []
    for name, path, is_passes in [('passes', capture, True), ('before', before, False), ('after', after, False)]:
        print(json.dumps({'processing': name, 'rpi_depths': depths}), flush=True)
        source = analyze_source(path, name, is_passes, output, channels, horizon_s, depths)
        (output/(name+'-report.json')).write_text(json.dumps(source, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        sources.append(source)
    compatible = all(s['recorded_profile'] == sources[0]['recorded_profile'] for s in sources[1:])
    result = {'format': 'bmi30-rpi-mean-screening-v1', 'exact_replay': False,
              'rpi_depths': list(depths), 'stm32_depth_held_constant': 4, 'window_horizon_s': horizon_s,
              'channels': [CHANNELS[ch] for ch in channels], 'same_recorded_profiles': compatible,
              'fixed_rx_shifts': [0, 0], 'fixed_rx_policy_source': 'Operator verified separately; not inferred from waveform',
              'sources': [{'name': s['name'], 'source_sha256': s['source_sha256'], 'wire_monitor_integrity': s['wire_monitor_integrity'],
                           'window_count': s['window_count'], 'cohort_gap_observations': len(s['cohort_gaps'])} for s in sources],
              'windows': [w for s in sources for w in s['windows']],
              'limitations': ['No FIRE/HOLD, confirmations, burst, worker scheduling or output replay.',
                  'Original 10s max(mean,P95) and manual threshold methods run on cold histories of ALL reconstructed pairs at receive time; this is not the live worker history or processing clock.',
                  'Static screens are frame features, never successful detections or detection probability.',
                  'Inside peak is descriptive on the same globally selected shift, not the current production peak search.',
                  'Depths use the exact original phase-separated rolling mean with int32 np.rint and immutable originals.',
                  'STM4/DC processing is already baked into the wire; neither can be undone.',
                  'Marked start windows are operator-defined attempts, with unlabelled outcomes and uncertain physical timing/count.',
                  'Each marked window is capped; the long final stop tail is excluded from ranking.',
                  'Controls are split into equal-duration bins after twelve seconds of local mean/noise warmup.',
                  'Averaging can spread a brief signal in time; features outside capped horizons are not interpreted as no signal.',
                  'The margins around gaps are heuristic; the complete series remains source-continuity FAIL if any cohort gap exists.']}
    (output/'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--depths', nargs='+', type=int, choices=DEPTHS, default=DEPTHS)
    parser.add_argument('--horizon-s', type=float, default=5.)
    parser.add_argument('--channels', nargs='+', choices=CHANNELS, default=['upper'])
    args = parser.parse_args()
    if not 0 < args.horizon_s <= 10:
        parser.error('Horizon must be within 0..10 seconds')
    result = run(args.capture, args.before, args.after, args.output, tuple(args.depths), args.horizon_s,
                 tuple(CHANNELS.index(ch) for ch in args.channels))
    print(json.dumps({k: v for k, v in result.items() if k not in ('windows', 'limitations')}))


if __name__ == '__main__':
    main()
