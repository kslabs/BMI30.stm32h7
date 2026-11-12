"""Bounded synchronous production detector model; never exact worker replay.

Only saved ROI200 / frozen DC / fixed RX0 / named/common/impulse/growth-off
captures are supported. Counts new accepted detector FIRE callbacks, not peaks
or HOLD samples. Human CENTER windows remain provisional, unlabelled windows.
"""
from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter, deque
from copy import deepcopy
import json
import math
from pathlib import Path
from types import MethodType, SimpleNamespace

import numpy as np

from analyze_raw_continuity import Distribution, FrameDecoder
from analyze_raw_pass_features import DEFAULT_HOST, DSP, ENGINE_NAME, extract_definitions, prepare, records, sha256
from bmi30_temporal_features import METHODS, make_temporal_product


METHODS_SCOPE = {
    '_update_realtime_prevbuf_detection', '_det_note_confirmation_step',
    '_det_accumulated_reference_noise', '_det_accumulated_signal_noise_ratio',
    '_det_noise_window_seconds', '_det_channel_ratio', '_det_channel_auto_enabled',
    '_det_manual_curve_ratio', '_det_noise_display_units', '_det_ratio_noise_max_u16_value',
    '_det_auto_floor_u16_value', '_det_auto_slope_value', '_det_channel_trigger_level',
    '_det_channel_confirm_count', '_rt_detector_level_scale',
    '_det_channel_burst_gate_enabled', '_det_channel_burst_blank_s',
    '_det_channel_burst_max_ratio', '_det_channel_burst_gate_active',
    '_det_output_active', '_det_output_min_s',
}


class Clock:
    def __init__(self):
        self.epoch = self.mono = 0.

    def time(self):
        return self.epoch

    def monotonic(self):
        return self.mono


def production_namespace(host, settings, expected_hash, clock):
    dsp = DSP(host, settings, expected_hash)
    ns = dsp.ns
    ns.update(time=clock, deepcopy=deepcopy, math=math,
              _RT_PRODUCT_FULLSCALE_U16_SCALE=(32768.*32768.)/65535.)
    common_tree = ast.parse((Path(host)/'bmi30_common_mode.py').read_text(encoding='utf-8'))
    for node in common_tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                ns[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    extract_definitions(Path(host)/'bmi30_common_mode.py',
                        {'base_width_decision', 'common_mode_combined_decision', 'DetectionReviewHistory'}, ns)
    ns['isfinite'] = np.isfinite
    tree = ast.parse(dsp.engine.read_text(encoding='utf-8'))
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in METHODS_SCOPE]
    if {n.name for n in nodes} != METHODS_SCOPE:
        raise ValueError('Production scope methods are missing or ambiguous')
    globals_needed = {'_rt_reference_noise_statistics', '_rt_clamp_detector_level',
                      '_rt_detector_confirmation_step', '_rt_detector_confirmation_diversity',
                      '_rt_detector_confirmation_growth', '_det_confirmation_change_mode',
                      '_rt_burst_rejection_reason', '_rt_detector_current_noise_supported',
                      '_rt_product_default_scale'}
    globals_needed.update(n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                          and n.name.startswith('_det_clamp'))
    globals_needed.add('_det_parse_bool')
    top_names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    globals_needed.update(x.id for node in nodes for x in ast.walk(node)
                          if isinstance(x, ast.Name) and x.id in top_names)
    extract_definitions(dsp.engine, globals_needed, ns)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[])),
                 str(dsp.engine), 'exec'), ns)
    return dsp, ns


class EventModel:
    """Real state mutation; only external outputs/capture sinks are replaced."""
    def __init__(self, status, engine_hash, host=DEFAULT_HOST, depth=1, ratios=None,
                 method='mean', loss_seconds=0., confirm_upper=None, noise_area_percent=None,
                 peak_search='global'):
        cfg = deepcopy(status['detector']['settings'])
        if noise_area_percent is not None:
            if type(noise_area_percent) is not int or not 5 <= noise_area_percent <= 95:
                raise ValueError('Noise area must be an integer5..95')
            cfg['noise_area_percent'] = noise_area_percent
        if confirm_upper is not None and (type(confirm_upper) is not int or not 1 <= confirm_upper <= 8):
            raise ValueError('Upper confirmations must be an integer1..8')
        if any(cfg.get(k) for k in ('common_mode_enabled', 'impulse_guard_enabled',
                                   'confirm_sample_diversity', 'confirm_growth_enabled')):
            raise ValueError('Only common/impulse/diversity/growth-off profiles are supported')
        if cfg.get('confirm_change_mode', 'off') != 'off':
            raise ValueError('Amplitude-change gate must be off')
        if method not in METHODS or not 1 <= depth <= 12:
            raise ValueError('Unsupported temporal method/depth')
        self.clock = Clock()
        self.dsp, self.ns = production_namespace(host, cfg, engine_hash, self.clock)
        if peak_search not in ('global', 'inside'):
            raise ValueError('Unsupported peak search policy')
        self.peak_search = peak_search
        if peak_search == 'inside':
            candidate_dir = Path(__file__).resolve().parents[2]/'.tmp/casino-s07-35cm-20261104/bounded-peak-search'
            candidate = candidate_dir/'engine.staged.py'
            manifest = json.loads((candidate_dir/'candidate.json').read_text(encoding='utf-8'))
            if (manifest['source_engine_sha256'] != engine_hash or
                    manifest['candidate_engine_sha256'] != sha256(candidate)):
                raise ValueError('Bounded peak candidate provenance mismatch')
            old_getenv = self.ns['os'].getenv
            bounds = {'BMI30_RT_PEAK_INDEX_MIN': str(cfg['peak_index_min']),
                      'BMI30_RT_PEAK_INDEX_MAX': str(cfg['peak_index_max'])}
            self.ns['os'] = SimpleNamespace(getenv=lambda key, default=None: bounds.get(key, old_getenv(key, default)))
            extract_definitions(candidate, {'_rt_detector_product_current_prev'}, self.ns)
            self.dsp.source_hashes['bounded_peak_candidate'] = sha256(candidate)
        self.depth, self.method = depth, method
        self.scope = s = SimpleNamespace()
        for name in METHODS_SCOPE:
            setattr(s, name, MethodType(self.ns[name], s))
        # No mocked threshold/noise/confirmation methods. These are real values
        # from the saved settings; UI, hardware and recording are inert sinks.
        for name in ('_clear_web_pre_fire', '_mark_web_pre_fire', '_player_record_note_detection',
                     '_rt_apply_detection_outputs', '_rt_stop_detection_outputs', '_capture_trigger',
                     '_capture_record_frame', '_capture_start_post', '_group_led_apply_detection',
                     '_fire_beep', '_beep_hold_start', '_beep_hold_filter_step', '_device_set_dc_adapt'):
            setattr(s, name, lambda *a, **kw: None)
        s._rt_detection_active = lambda: True
        s._adc_enable_flags = lambda: tuple(bool(status['detector']['channels'][n].get('enabled', True))
                                          for n in ('upper', 'lower'))
        s._det_enabled = True
        s.base_buf_len = s.max_samples = 200
        s._det_level_scale = float(status['detector'].get('display_scale', (32768.*32768.)/65535.))
        s._beep_fire_count = s._freeze_fire_count = 0
        s._det_loss_s, s._det_loss_ratio = float(loss_seconds), 1.4
        s._beep_min_duration_s = float(status.get('sound', {}).get('minimum_duration_ms', 150))/1000.
        s._det_freeze_dc_enable = False
        for field in ('confirm_phase_gate', 'confirm_sample_diversity', 'confirm_change_mode',
                      'confirm_growth_enabled', 'confirm_growth_ratio', 'impulse_guard_enabled',
                      'base_width_enabled', 'base_width_max', 'peak_index_min', 'peak_index_max',
                      'noise_window_s', 'ratio_noise_max_u16', 'auto_floor_u16', 'auto_slope'):
            setattr(s, '_det_'+field, cfg[field])
        s._det_common_mode = {k: v for k, v in cfg.items() if k.startswith('common_mode')}
        for ch, name in enumerate(('upper', 'lower')):
            cc = status['detector']['channels'][name]
            for key, value in (('ratio', cc['threshold']), ('ratio_high', cc['threshold_high']),
                               ('threshold_auto', cc['auto_threshold']), ('burst_gate', cc['burst_gate']),
                               ('burst_blank_s', cc['burst_blank_s']), ('burst_max_ratio', cc['burst_max_ratio'])):
                setattr(s, '_det_'+key+str(ch), value)
            setattr(s, '_rt_det_confirm_count'+str(ch), cc['confirm_count'])
        if ratios is not None:
            if len(ratios) != 2 or any(not np.isfinite(x) or x < 1 for x in ratios):
                raise ValueError('Manual ratios must be finite and >= 1')
            s._det_ratio0, s._det_ratio_high0 = map(float, ratios)
        if confirm_upper is not None:
            s._rt_det_confirm_count0 = confirm_upper
        self.events, self.recent = [], (deque(maxlen=64), deque(maxlen=64))
        self.hold_changes, self.last_hold = [[], []], [False, False]
        self.previous_seq = [None, None]
        self.counts = Counter()
        s._mark_web_detection_fire = self._new_fire

    def _new_fire(self, upper, lower):
        # Production calls this only on a previously unheld accepted channel.
        for ch, fired in enumerate((upper, lower)):
            if fired:
                self.events.append({'channel': ch, 'monotonic': self.clock.mono,
                                    'epoch': self.clock.epoch, 'source_seq': self.previous_seq[ch]})

    def decision(self, channel, made, current, previous, seq, mono, epoch):
        self.clock.mono, self.clock.epoch = mono, epoch
        old = self.previous_seq[channel]
        contiguous = old is not None and ((seq-old) & 0xffffffff) == 1
        self.previous_seq[channel] = seq
        level, shift, peak, value, product = made
        s = self.scope
        s._update_realtime_prevbuf_detection(channel, int(level), int(shift), int(peak), float(value), product,
            candidate_current_in=current, candidate_previous_in=previous,
            confirmation_contiguous_in=contiguous, source_seq_in=seq, det_seq=self.counts['jobs'])
        self.counts['jobs'] += 1
        state = {'monotonic': mono, 'source_seq': seq,
                 'level': int(getattr(s, f'_det_last_lvl{channel}', 0)),
                 'peak': int(getattr(s, f'_phase_peak_idx_adc{channel}', -1)),
                 'noise_avg': float(getattr(s, f'_det_noise_avg{channel}', 0)),
                 'trigger': int(getattr(s, f'_det_thr{channel}', 0)),
                 'candidate': bool(getattr(s, f'_det_last_candidate{channel}', False)),
                 'confirm': int(getattr(s, f'_det_exceed{channel}', 0)),
                 'hold': bool(getattr(s, f'_det_hold{channel}', False))}
        self.recent[channel].append(state)
        for ch in (0, 1):
            held = bool(getattr(s, f'_det_hold{ch}', False))
            if held != self.last_hold[ch]:
                self.hold_changes[ch].append((mono, held))
                self.last_hold[ch] = held
        return state

    def frame(self, frame, payload):
        if frame['samples'] != 200:
            raise ValueError('Expected ROI200')
        ch, seq, phase = frame['channel'], frame['seq'], frame['phase']
        self.counts['frames_'+str(ch)] += 1
        raw = np.frombuffer(payload, dtype='<u2').astype(np.int32)
        stamp = (1, seq, frame['timestamp_ticks'], frame['reserved2'], 200)
        h = self.dsp.history
        if not h.feed(ch, raw, phase, stamp, 0, depth=self.depth):
            return
        smooth = self.ns['_rt_detector_smooth_array']
        if self.method == 'mean':
            even, odd = (smooth(x, self.dsp.settings['smooth_mode']) for x in h.pairs[ch])
            made = self.ns['_rt_detector_product_current_prev'](
                even, odd, 0, 'prod', self.scope._rt_detector_level_scale(), fixed_shift=0)
        else:
            window = h.raw_windows([ch])[ch]
            changed = make_temporal_product(window['samples'], self.method,
                        lambda x: smooth(x, self.dsp.settings['smooth_mode']),
                        self.ns['_rt_detector_product_display'])
            even, odd, product = changed['current'], changed['previous'], changed['product']
            lo, hi = ((int(self.dsp.settings['peak_index_min']), int(self.dsp.settings['peak_index_max'])+1)
                      if self.peak_search == 'inside' else (0, len(product)))
            peak = lo+int(np.argmax(product[lo:hi])); value = float(product[peak])
            level = self.ns['_rt_detector_level_from_peak'](value, 'prod', self.scope._rt_detector_level_scale())
            made = (level, 0, peak, value, product)
        timing = frame['completion_timing']
        self.decision(ch, made, even, odd, seq, timing['host_monotonic'], timing['host_epoch'])


def compare_status(model, status, mono, accum):
    d = status['detector']
    for ch in (0, 1):
        candidates = [x for x in model.recent[ch] if 0 <= mono-x['monotonic'] <= .15]
        if not candidates:
            continue
        level, peak = int(d['lvl'+str(ch)]), int(d['peak_index'+str(ch)])
        # This accommodates unknown live worker delay; it is not timestamp proof.
        state = min(candidates, key=lambda x: abs(x['level']-level)/max(1, abs(level))+(x['peak'] != peak))
        acc = accum[ch]; acc['samples'] += 1
        acc['exact_level_peak'] += state['level'] == level and state['peak'] == peak
        acc['candidate_matches'] += state['candidate'] == bool(d['candidate'+str(ch)])
        acc['confirm_matches'] += state['confirm'] == int(d['confirm_seen'+str(ch)])
        for field, source in (('noise_avg', 'noise_avg'), ('trigger', 'trigger')):
            observed = float(d[source+str(ch)])
            acc[field].add(abs(state[field]-observed)/max(1., abs(observed)))


def count_event_windows(windows, events):
    results = [{'pass_id': w['pass_id'], 'new_upper_fire': False, 'new_upper_events': 0,
                'human_result_original': w['human_result']} for w in windows]
    for event in events:
        if event['channel'] != 0:
            continue
        selected = [i for i, w in enumerate(windows)
                    if w['start_mono'] <= event['monotonic'] < w['end_mono']]
        if len(selected) > 1:
            raise ValueError('Overlapping windows cannot share one FIRE event')
        if selected:
            row = results[selected[0]]
            row['new_upper_fire'] = True
            row['new_upper_events'] += 1
    return results


def validate_baseline(report, archive_sha, engine_sha):
    if (report.get('archive_sha256') != archive_sha or
            report.get('source_hashes', {}).get(ENGINE_NAME) != engine_sha or
            report.get('rpi_depth') != 1 or report.get('temporal_method') != 'mean' or
            report.get('manual_upper_ratios') is not None or
            report.get('baseline_zero_fire_matches') is not True or
            report.get('actual_fire_counter_deltas', [None])[0] != 0 or
            report.get('model_new_fire_events_by_channel', [None])[0] != 0):
        raise ValueError('Counterfactual requires the matching zero-FIRE production baseline report')


def interval_coverage(intervals, start, end):
    clipped = sorted((max(start, lo), min(end, hi)) for lo, hi in intervals if hi > start and lo < end)
    total, previous_end = 0., start
    for lo, hi in clipped:
        total += max(0., hi-max(lo, previous_end))
        previous_end = max(previous_end, hi)
    return total


def activity_summary(model, start, end, warmup_s=10.):
    channels = []
    for ch in (0, 1):
        intervals, opened = [], None
        for mono, held in model.hold_changes[ch]:
            if held:
                opened = mono
            elif opened is not None:
                intervals.append((opened, mono)); opened = None
        if opened is not None:
            intervals.append((opened, end))
        events = [e for e in model.events if e['channel'] == ch]
        timer = [(e['monotonic'], e['monotonic']+model.scope._beep_min_duration_s) for e in events]
        warm = min(end, start+warmup_s)
        channels.append({'new_fire_total': len(events),
            'new_fire_before_warmup': sum(e['monotonic'] < warm for e in events),
            'new_fire_after_warmup': sum(e['monotonic'] >= warm for e in events),
            'native_hold_seconds': interval_coverage(intervals, start, end),
            'native_hold_seconds_after_warmup': interval_coverage(intervals, warm, end),
            'minimum_output_envelope_seconds': interval_coverage(intervals+timer, start, end),
            'minimum_output_envelope_seconds_after_warmup': interval_coverage(intervals+timer, warm, end),
            'native_hold_intervals': intervals})
    return {'warmup_exclusion_s_in_separate_diagnostic_only': warmup_s,
            'whole_exposure_s': end-start, 'after_warmup_exposure_s': max(0., end-start-warmup_s),
            'ranking_total_FP_is_not_replaced': True, 'channels': channels,
            'scope': 'Synchronous detector latch and minimum-timer envelope; no physical optic/sound/relay gating'}


def run_capture(path, host=DEFAULT_HOST, depth=1, ratios=None, method='mean', loss_seconds=0.,
                confirm_upper=None, noise_area_percent=None, peak_search='global'):
    ready, end, windows, invalid = prepare(path)
    status = ready['status']; mode = status['mode']
    if mode['selected'] != 6 or mode['avg_n'] != 4:
        raise ValueError('This bounded model is pinned to current mode6 / STM4 captures')
    # Saved wire data already includes frozen STM correction; no live DC model.
    for row in records(path):
        if isinstance(row.get('status'), dict) and row['status'].get('dc_guard', {}).get('settle_ms') != 0:
            raise ValueError('Every saved status must show frozen DC=0')
    model = EventModel(status, ready['metadata']['engine_sha256'], host, depth, ratios, method, loss_seconds,
                       confirm_upper, noise_area_percent, peak_search)
    t0 = ready['monotonic']; problems = []
    accum = [dict(samples=0, exact_level_peak=0, candidate_matches=0, confirm_matches=0,
                  noise_avg=Distribution(), trigger=Distribution()) for _ in (0, 1)]
    decoder = FrameDecoder(model.frame, lambda kind, detail: problems.append({'kind': kind, **detail}))
    actual_first, actual_last = None, None
    for row in records(path):
        if row['kind'] == 'transfer':
            data = base64.b64decode(row['data'], validate=True)
            if row.get('result') or row.get('truncated') or len(data) != row.get('captured'):
                raise ValueError('Invalid USB transfer')
            decoder.feed(data, {'host_monotonic': row['monotonic'], 'host_epoch': row['at'], 'usb_at': row['usb_at']})
        elif row['kind'] in ('ready', 'status', 'end') and isinstance(row.get('status'), dict):
            dc = row['status']['detector']
            fire = [int(dc['fire_seq0']), int(dc['fire_seq1'])]
            if actual_first is None:
                actual_first = fire
            actual_last = fire
            if row['monotonic']-t0 >= max(10., float(status['detector']['settings']['noise_window_s'])):
                compare_status(model, row['status'], row['monotonic'], accum)
    if problems or decoder.summary().get('crc_invalid_candidates'):
        raise ValueError('Frame decoder integrity failed')
    results = count_event_windows(windows, model.events)
    conformance = []
    for acc in accum:
        count = acc['samples']
        conformance.append({**{k: v for k, v in acc.items() if k not in ('noise_avg', 'trigger')},
            'exact_level_peak_fraction': acc['exact_level_peak']/count if count else None,
            'candidate_match_fraction': acc['candidate_matches']/count if count else None,
            'confirm_match_fraction': acc['confirm_matches']/count if count else None,
            'noise_avg_relative_error': acc['noise_avg'].summary(), 'trigger_relative_error': acc['trigger'].summary()})
    summary = json.loads((Path(path).parent/'summary.json').read_text(encoding='utf-8'))
    duration = float(end['summary']['duration_s'])
    continuity = Path(path).parent/'continuity.json'
    quality = json.loads(continuity.read_text(encoding='utf-8')).get('strict_average', {}).get('verdict') if continuity.exists() else None
    return {'capture': str(path), 'archive_sha256': sha256(path), 'model': 'bounded_synchronous_production_state_machine',
            'exact_replay': False, 'rpi_depth': depth, 'temporal_method': method, 'manual_upper_ratios': ratios,
            'fixed_rx_policy': [0, 0],
            'upper_confirmations_override': confirm_upper, 'noise_area_percent_override': noise_area_percent,
            'peak_search': peak_search,
            'loss_seconds_assumption': loss_seconds, 'source_hashes': model.dsp.source_hashes,
            'capture_complete': summary.get('capture_complete'), 'strict_average_source_quality': quality,
            'actual_fire_counter_deltas': [b-a for a, b in zip(actual_first, actual_last)],
            'model_new_fire_events_by_channel': [sum(e['channel'] == ch for e in model.events) for ch in (0, 1)],
            'upper_windows_with_new_fire': sum(r['new_upper_fire'] for r in results),
            'upper_marked_windows': len(results), 'windows': results, 'events': model.events,
            'processed': dict(model.counts), 'input_history': model.dsp.history.diagnostics(),
            'activity': activity_summary(model, t0, t0+duration),
            'intermediate_comparison_after_warmup': conformance,
            'intermediate_match_policy': 'Best preceding state within150ms unknown-worker-lag band; informative, not exact scheduling validation',
            'invalid_marks': invalid,
            'limitations': ['Initial live noise deque, worker job grouping/order/apply timestamps not recorded',
                'All CRC-verified fresh wire pairs processed synchronously; no exact worker replay',
                'No per-window state resets; full recorded lead-in included',
                'FIRE callback is accepted detector latch transition; physical sound/relay/optic outputs not simulated',
                'CENTER windows are provisional attempt windows; original unlabelled outcomes remain untouched',
                'Temporal alternatives run on already STM4-averaged/DC-corrected payload, not original ADC',
                'Loss delay is an explicit assumption because status omits its runtime environment override']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--host-dir', type=Path, default=DEFAULT_HOST)
    parser.add_argument('--rpi-depth', type=int, choices=range(1, 13), default=1)
    parser.add_argument('--upper-ratios', type=float, nargs=2)
    parser.add_argument('--method', choices=METHODS, default='mean')
    parser.add_argument('--loss-seconds', type=float, default=0.)
    parser.add_argument('--confirm-upper', type=int, choices=range(1, 9))
    parser.add_argument('--noise-area-percent', type=int, choices=range(5, 96))
    parser.add_argument('--peak-search', choices=('global', 'inside'), default='global')
    parser.add_argument('--background', action='append', type=Path, default=[])
    parser.add_argument('--background-confirmed', action='store_true')
    parser.add_argument('--baseline-validation', type=Path,
                        help='Matching zero-FIRE baseline JSON; required for temporal/ratio alternatives')
    args = parser.parse_args()
    if args.background and not args.background_confirmed:
        parser.error('Background files require explicit physical absence confirmation')
    if not np.isfinite(args.loss_seconds) or args.loss_seconds < 0:
        parser.error('loss seconds must be finite and nonnegative')
    changed = (args.rpi_depth != 1 or args.method != 'mean' or args.upper_ratios is not None
               or args.confirm_upper is not None or args.noise_area_percent is not None
               or args.peak_search != 'global' or args.loss_seconds != 0.)
    if changed:
        if not args.baseline_validation:
            parser.error('Counterfactual options require --baseline-validation')
        baseline = json.loads(args.baseline_validation.read_text(encoding='utf-8'))
        validate_baseline(baseline, sha256(args.capture), sha256(args.host_dir/ENGINE_NAME))
    params = (args.host_dir, args.rpi_depth, args.upper_ratios, args.method, args.loss_seconds,
              args.confirm_upper, args.noise_area_percent, args.peak_search)
    result = run_capture(args.capture, *params)
    backgrounds = [run_capture(path, *params) for path in args.background]
    result['confirmed_backgrounds'] = backgrounds
    result['ranking_counts'] = {'TP_window_proxy': result['upper_windows_with_new_fire'],
        'FP_new_upper_events_confirmed_background': sum(r['model_new_fire_events_by_channel'][0] for r in backgrounds),
        'FP_new_upper_events_confirmed_background_after10s': sum(r['activity']['channels'][0]['new_fire_after_warmup'] for r in backgrounds),
        'operator_physical_trial_count_verified': False}
    result['ranking_policy'] = 'maximize TP_window_proxy first, then minimize confirmed-background new FIRE events'
    result['baseline_validation'] = str(args.baseline_validation) if changed else None
    result['baseline_zero_fire_matches'] = (result['actual_fire_counter_deltas'][0] == 0 and
        result['model_new_fire_events_by_channel'][0] == 0) if not changed else None
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({'output': str(args.output), 'counts': result['ranking_counts'],
                      'baseline_zero_fire_matches': result['baseline_zero_fire_matches'], 'exact_replay': False}))


if __name__ == '__main__':
    main()
