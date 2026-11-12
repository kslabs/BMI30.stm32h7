"""Lexicographic PHASE-1 scalar screening, never independent FIRE replay.

Priority: maximize marked windows with a scalar candidate, then minimize
separate no-tag positive bins among equal maxima. No weighted balance/FP cap.
One sustained scalar condition may span many windows: these are NOT detections.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

from analyze_raw_pass_features import DEFAULT_HOST, DSP, sha256
from sweep_raw_pass_averaging import NoiseThreshold

CURVES = ((1.6, 1.3), (1.8, 1.4), (2.0, 1.6), (1.5, 1.2), (1.4, 1.2),
          (1.3, 1.1), (1.2, 1.0), (1.1, 1.0), (1.0, 1.0))
BRANCHES = {'global': 'full200_maximum_then_position_gate20_66',
            'inside': 'search20_66_first_descriptive_counterfactual'}


def run(folder, output):
    folder, output = Path(folder), Path(output)
    comparison = json.loads((folder/'comparison.json').read_text(encoding='utf8'))
    if not comparison['same_recorded_profiles']:
        raise ValueError('Profiles differ between marked and control records')
    source = json.loads((folder/'passes-report.json').read_text(encoding='utf8'))
    recorded = source['recorded_profile']
    dsp = DSP(DEFAULT_HOST, recorded['settings'], source['source_metadata']['engine_sha256'])
    model = NoiseThreshold(dsp, recorded['channels'])
    candidates = {}
    for depth in comparison['rpi_depths']:
        for branch in BRANCHES:
            for low, high in CURVES:
                candidates[(depth, branch, low, high)] = {'marked': set(), 'controls': set(), 'frames': Counter(),
                                                        'marked_frames': Counter(), 'control_frames': Counter()}
    source_hashes = {}
    for name in ('passes', 'before', 'after'):
        path = folder/(name+'-features.jsonl.gz')
        source_hashes[path.name] = sha256(path)
        with gzip.open(path, 'rt', encoding='utf8') as stream:
            for line in stream:
                row = json.loads(line)
                if row['channel'] != 'upper':
                    continue
                for branch in BRANCHES:
                    peak = row['global' if branch == 'global' else 'inside_gate']
                    history = row[branch+'_noise_history']
                    position = row['global_peak_position_ok'] if branch == 'global' else True
                    for low, high in CURVES:
                        entry = candidates[(row['rpi_depth'], branch, low, high)]
                        entry['frames'][name] += 1
                        model.scope._det_ratio0, model.scope._det_ratio_high0 = low, high
                        ratio = model.scope._det_manual_curve_ratio(0, history['noise_avg'])
                        trigger = model.ns['_det_channel_trigger_level'](
                            model.scope, 0, history['noise_avg'], ratio, int(peak['product']))
                        allowed = bool(position and peak['base_width']['ok'] and peak['noise_ref'] > 0
                                       and history['noise_avg'] > 0 and peak['product'] >= trigger
                                       and peak['product'] > peak['noise_ref']*ratio)
                        if allowed:
                            if name == 'passes':
                                entry['marked'].add(row['window_id'])
                                entry['marked_frames'][row['window_id']] += 1
                            else:
                                entry['controls'].add((name, row['window_id']))
                                entry['control_frames'][(name, row['window_id'])] += 1
    ranked = []
    marked_total = len({r['window_id'] for r in comparison['windows'] if r['source'] == 'passes'})
    control_total = len({(r['source'], r['window_id']) for r in comparison['windows'] if r['source'] != 'passes'})
    for (depth, branch, low, high), entry in candidates.items():
        ranked.append({'rpi_depth': depth, 'stm32_depth': 4, 'search_domain': BRANCHES[branch],
                       'manual_curve_low_high': [low, high], 'peak_gate': [20, 66], 'base_width_max': 40,
                       'candidate_positive_windows': len(entry['marked']), 'marked_start_windows': marked_total,
                       'background_positive_bins': len(entry['controls']), 'background_control_bins': control_total,
                       'positive_window_ids': sorted(entry['marked']),
                       'background_positive_bin_ids': [list(x) for x in sorted(entry['controls'])],
                       'positive_frames_per_marked_window': {str(k):v for k,v in sorted(entry['marked_frames'].items())},
                       'positive_frames_per_control_bin': {str(k):v for k,v in sorted(entry['control_frames'].items())},
                       'feature_frame_counts': dict(entry['frames']),
                       'is_unchanged_parameter_baseline': depth == 1 and branch == 'global' and (low, high) == CURVES[0]})
    ranked.sort(key=lambda row: (-row['candidate_positive_windows'], row['background_positive_bins']))
    maximum = ranked[0]['candidate_positive_windows'] if ranked else None
    minimum_background = min((r['background_positive_bins'] for r in ranked if r['candidate_positive_windows'] == maximum), default=None)
    baseline = next(r for r in ranked if r['is_unchanged_parameter_baseline'])
    result = {'format': 'bmi30-lexicographic-feature-ranking-v1', 'analysis_phase': 1,
              'objective': {'priority_1': 'candidate_positive_marked_5s_windows DESC',
                            'priority_2_only_among_equal_priority_1': 'separate_no_tag_positive_5s_bins ASC',
                            'false_positive_cap': None, 'weighted_score': None},
              'exact_replay': False, 'independent_fire_events_evaluated': False,
              'not_a_detection_rate': True, 'baseline_first_reference': baseline,
              'candidate_grid': {'depths': comparison['rpi_depths'], 'curves': CURVES, 'domains': BRANCHES},
              'best_objective_values': {'candidate_positive_windows': maximum, 'background_positive_bins': minimum_background},
              'all_lexicographic_winners': [r for r in ranked if r['candidate_positive_windows'] == maximum and r['background_positive_bins'] == minimum_background],
              'ranked_candidates': ranked, 'source_feature_sha256': source_hashes,
              'limitations': comparison['limitations']+[
                  'A single sustained scalar condition can make several windows positive; it is never counted as several independent detector FIRE events here.',
                  'No human outcomes were inferred for unlabelled starts. Operator observed zero upper detections in this physical series.',
                  'These bins are control-window surrogates, not false-alarm events or a false-alarm rate.',
                  'Raspberry means retain the original input/processing cadence; they do not model STM8/16 cadence or CPU relief.',
                  'The grid and these recordings are exploratory training data. Choose actual firmware/parameter candidates for independent physical validation.']}
    if output.exists():
        raise FileExistsError(output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sweep', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.sweep, args.output)
    print(json.dumps({'best_objective_values': result['best_objective_values'],
                      'baseline': result['baseline_first_reference'],
                      'winners': result['all_lexicographic_winners']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
