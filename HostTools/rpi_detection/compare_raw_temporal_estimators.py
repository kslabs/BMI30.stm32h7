"""Focused mean/trim/median/coherent-energy feature comparison; NOT FIRE replay."""
from __future__ import annotations

import argparse
import base64
from collections import Counter, OrderedDict
import gzip
import json
from pathlib import Path

import numpy as np

from analyze_raw_continuity import Distribution, FrameDecoder
from analyze_raw_pass_features import DEFAULT_HOST, DSP, prepare, profile, records, sha256
from bmi30_temporal_features import METHODS, make_temporal_product
from sweep_raw_pass_averaging import NoiseThreshold, capped_windows

CURVES = ((1.6, 1.3), (1.4, 1.2))
DEPTHS = (8, 12)


class Group:
    def __init__(self):
        self.count = 0
        self.product, self.snr, self.noise = Distribution(), Distribution(), Distribution()
        self.global_indices = Counter()
        self.positive = Counter()
        self.total_curve = np.zeros(200)
        self.maximum_curve = np.zeros(200)
        self.best = None


def analyze_source(path, name, marked, output):
    ready, end, passes, invalid = prepare(path)
    if ready['status']['mode']['avg_n'] != 4:
        raise ValueError('STM4 must be held constant')
    settings = ready['status']['detector']['settings']
    if (settings['peak_index_min'], settings['peak_index_max']) != (20, 66):
        raise ValueError('This focused comparison preserves the actual20..66 gate')
    dsp = DSP(DEFAULT_HOST, settings, ready['metadata']['engine_sha256'])
    histories = {n: type(dsp.history)() for n in DEPTHS}
    models = {(n, method): NoiseThreshold(dsp, ready['status']['detector']['channels']) for n in DEPTHS for method in METHODS}
    if marked:
        windows = capped_windows(passes, 5.)
    else:
        start = ready['monotonic']+12.
        count = int((end['monotonic']-start)//5.)
        windows = [{'pass_id': i+1, 'start_mono': start+i*5., 'end_mono': start+(i+1)*5.,
                    'duration_s': 5., 'human_result': None} for i in range(count)]
    if not windows:
        raise ValueError('No diagnostic windows')
    numeric_start = min(w['start_mono'] for w in windows)-12.
    numeric_end = max(w['end_mono'] for w in windows)
    groups, issues, previous, gaps = {}, [], {}, []
    count = Counter()
    smooth_cache = OrderedDict()
    feature_file = gzip.open(output/(name+'-features.jsonl.gz'), 'wt', encoding='utf8', compresslevel=1)
    smooth = lambda x: dsp.ns['_rt_detector_smooth_array'](x, settings['smooth_mode'])
    product_display = dsp.ns['_rt_detector_product_display']

    def problem(kind, detail):
        issues.append({'kind': kind, **detail})
        for history in histories.values():
            history.__init__()
        smooth_cache.clear()

    def on_frame(f, payload):
        ch, phase = f['channel'], f['phase']
        count[ch] += 1
        old = previous.get((ch, phase))
        mono = f['completion_timing']['host_monotonic']
        if old and ((f['reserved']-old['reserved']) & 0xffffffff) != 8:
            gaps.append({'channel': ch, 'phase': phase, 'seq': f['seq'],
                         'start_receive_mono': old['completion_timing']['host_monotonic'], 'end_receive_mono': mono})
        previous[(ch, phase)] = f
        if ch != 0 or mono < numeric_start or mono >= numeric_end:
            return
        if f['samples'] != 200:
            problem('unexpected_roi', {'seq': f['seq']})
            return
        raw = np.frombuffer(payload, dtype='<u2').astype(np.int32)
        smooth_cache[f['seq']] = smooth(raw.astype(np.float64))
        while len(smooth_cache) > 64:
            smooth_cache.popitem(last=False)
        stamp = (1, f['seq'], f['timestamp_ticks'], f['reserved2'], 200)
        window = next((w for w in windows if w['start_mono'] <= mono < w['end_mono']), None)
        for n, history in histories.items():
            if not history.feed(0, raw, phase, stamp, 0, n):
                continue
            originals = history.raw_windows([0])[0]
            raw_windows = np.asarray(originals['samples'])
            filtered = np.asarray([[smooth_cache[int(seq)] for seq in phase_seqs] for phase_seqs in originals['sequences']])
            for method in METHODS:
                made = make_temporal_product(raw_windows, method, smooth, product_display,
                                             filtered if method == 'coherent_energy' else None)
                product = made['product']
                peak = 20+int(np.argmax(product[20:67]))
                global_peak = int(np.argmax(product))
                amplitude = float(product[peak])
                noise, sector, _ = dsp.ns['_rt_detector_relative_noise_reference'](product, peak)
                width = dsp.base_width(product, peak, settings['base_width_max'], settings['base_width_enabled'], 0)
                level = dsp.ns['_rt_detector_level_from_peak'](amplitude, 'prod', 16384.25)
                model = models[(n, method)]
                history_values = model.feed(noise, mono, 0, amplitude, True, width['ok'], level=level)
                if window is None:
                    continue
                allowed = {}
                for low, high in CURVES:
                    model.scope._det_ratio0, model.scope._det_ratio_high0 = low, high
                    ratio = model.scope._det_manual_curve_ratio(0, history_values['noise_avg'])
                    threshold = model.ns['_det_channel_trigger_level'](model.scope, 0, history_values['noise_avg'], ratio, level)
                    allowed[str((low, high))] = bool(noise > 0 and history_values['noise_avg'] > 0 and width['ok']
                                                   and level >= threshold and amplitude > noise*ratio)
                # Restore actual baseline for the next history feed.
                model.scope._det_ratio0, model.scope._det_ratio_high0 = CURVES[0]
                row = {'source': name, 'window_id': window['pass_id'], 'depth': n, 'method': method,
                       'receive_mono': mono, 'elapsed_in_window_s': mono-window['start_mono'], 'seq': f['seq'],
                       'peak': peak, 'product': amplitude, 'level': level, 'global_peak': global_peak,
                       'noise_ref': float(noise), 'noise_sector': int(sector), 'base_width': width,
                       'inside_snr': amplitude/noise if noise > 0 else None,
                       'noise_history': history_values, 'scalar_screens': allowed}
                feature_file.write(json.dumps(row, separators=(',', ':'))+'\n')
                group = groups.setdefault((window['pass_id'], n, method), Group())
                group.count += 1
                group.product.add(amplitude)
                group.snr.add(row['inside_snr'])
                group.noise.add(noise)
                group.global_indices[global_peak] += 1
                for curve, value in allowed.items():
                    group.positive[curve] += value
                group.total_curve += product
                np.maximum(group.maximum_curve, product, out=group.maximum_curve)
                if group.best is None or amplitude > group.best[0]:
                    group.best = (amplitude, row, np.array(product, copy=True))

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
                problem('length_mismatch', {})
                decoder.break_stream()
                continue
            decoder.feed(data, {'host_monotonic': record['monotonic'], 'usb_at': record['usb_at'], 'host_epoch': record['at']})
    finally:
        feature_file.close()
    rows, arrays = [], {}
    for (wid, n, method), group in groups.items():
        window = next(w for w in windows if w['pass_id'] == wid)
        direct_gap = any(g['start_receive_mono']-.1 < window['end_mono'] and
                         g['end_receive_mono']+.1 >= window['start_mono'] for g in gaps)
        history_gap = any(g['start_receive_mono']-.1 < window['end_mono'] and
                          g['end_receive_mono']+settings['noise_window_s']+(n-1)*.02+.1 >= window['start_mono'] for g in gaps)
        rows.append({'source': name, 'window_id': wid, 'depth': n, 'method': method,
                     'duration_s': window['duration_s'], 'human_result': window.get('human_result'),
                     'start_mono': window['start_mono'], 'end_mono': window['end_mono'],
                     'cohort_gap_in_window': direct_gap, 'noise_history_affected_by_source_gap': history_gap,
                     'frames': group.count, 'inside_product': group.product.summary(), 'inside_snr': group.snr.summary(),
                     'noise_ref': group.noise.summary(), 'global_peak_indices': dict(group.global_indices),
                     'scalar_positive_frames': dict(group.positive),
                     'best': group.best[1]})
        prefix = f'{name}_w{wid}_n{n}_{method}'
        arrays[prefix+'_mean_product'] = group.total_curve/group.count
        arrays[prefix+'_maximum_product'] = group.maximum_curve
        arrays[prefix+'_best_product'] = group.best[2]
    np.savez_compressed(output/(name+'-curves.npz'), **arrays)
    expected = end.get('summary', {}).get('wire', {}).get('crc_verified_frames')
    return {'source': name, 'archive': str(Path(path).resolve()), 'archive_sha256': sha256(path),
            'recorded_profile': profile(ready['status']), 'source_metadata': ready['metadata'],
            'wire_counts': [count[0], count[1]], 'wire_monitor_integrity': bool(end['summary']['capture_complete'] and expected == [count[0],count[1]] and not issues),
            'cohort_gaps': gaps, 'issues': issues, 'invalid_marks': invalid, 'windows': rows}


def run(capture, before, after, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    sources = []
    for name, path, marked in [('passes', capture, True), ('before', before, False), ('after', after, False)]:
        print(json.dumps({'processing': name, 'methods': METHODS, 'depths': DEPTHS}), flush=True)
        result = analyze_source(path, name, marked, output)
        (output/(name+'-report.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
        sources.append(result)
    rows = [w for source in sources for w in source['windows']]
    ranking = []
    for n in DEPTHS:
        for method in METHODS:
            for curve in CURVES:
                selected = [r for r in rows if r['depth'] == n and r['method'] == method]
                p = [r for r in selected if r['source'] == 'passes']
                b = [r for r in selected if r['source'] != 'passes']
                ranking.append({'depth': n, 'method': method, 'manual_curve': list(curve), 'search_gate': [20, 66],
                                'candidate_positive_windows': sum(r['scalar_positive_frames'].get(str(curve),0)>0 for r in p),
                                'background_positive_bins': sum(r['scalar_positive_frames'].get(str(curve),0)>0 for r in b),
                                'marked_windows': len(p), 'control_bins': len(b)})
    ranking.sort(key=lambda r: (-r['candidate_positive_windows'], r['background_positive_bins']))
    result = {'format': 'bmi30-focused-nonlinear-feature-comparison-v1', 'analysis_phase': 1, 'exact_replay': False,
              'independent_fire_events_evaluated': False, 'stm32_depth': 4, 'depths': DEPTHS, 'methods': METHODS,
              'objective': {'priority_1': 'candidate_positive_windows DESC', 'priority_2_for_equal_priority_1': 'background_positive_bins ASC', 'weighted_score': None, 'false_positive_cap': None},
              'source_archives': [{k:s[k] for k in ('source','archive_sha256','wire_monitor_integrity')} for s in sources],
              'same_recorded_profiles': all(s['recorded_profile'] == sources[0]['recorded_profile'] for s in sources[1:]),
              'ranked_candidates': ranking, 'windows': rows,
              'algorithm_hypotheses': {'mean': 'Exact production np.rint phase mean, then production spatialavg9 and abs product',
                  'trim1': 'Pointwise remove one minimum and one maximum; mean remaining N-2, np.rint, spatialavg9, absproduct',
                  'median': 'Pointwise temporal median, np.rint, spatialavg9, absproduct',
                  'coherent_energy': 'Spatialavg9 float64 on each original packet; signed U=((sumx)^2-sumx^2)/(N(N-1)) perphase; combine sqrt(max(Ue,0)*max(Uo,0)); ADC^2 units, no hidden sign veto'},
              'limitations': ['Scalar screens/window counts are not detector FIRE events, detection rate or independent alarms.',
                  'Original cold10s max(mean,P95) and manual curve on every reconstructed pair at receive time, not live worker history.',
                  'Input already includes STM4 rounding/DC correction. These are new algorithms on that input, not raw ADC recovery.',
                  'Coherent own-TX/interference survives U too. Candidate peak search remains20..66; full noise geometry remains present.',
                  'Negative U exists for noise and is rectified only during phase combination; rectification can bias background upward.',
                  'U uses float spatial filtering per original packet; other methods use original production integer smoothing after temporal aggregation.',
                  'No FIR/HOLD/confirmations/worker scheduling or parser drops replay; all human results remain unlabelled.',
                  'Five-second marked horizons and separate five-second no-tag bins after twelve-second cold warmup.',
                  'Full source recording has one shared cohort gap before first marked start; this cannot explain every failed physical attempt.',
                  'Gap margins/history flags are conservative quality annotations, not an exact claim about the subsequent detector state.',
                  'Coherent estimator assumes stable sample position, phase and amplitude within each window; moving tags can violate this.']}
    (output/'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps({'ranked_candidates':ranking,'same_recorded_profiles':result['same_recorded_profiles']}),flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.capture,args.before,args.after,args.output)


if __name__ == '__main__':
    main()
