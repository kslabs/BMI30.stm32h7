"""The user priority is lexicographic; no hidden false-positive cap or score."""
import gzip
import json
import unittest

from analyze_raw_pass_features import DEFAULT_HOST, ENGINE_NAME, sha256
from rank_raw_feature_candidates import run
from test_raw_pass_features import settings, temporary_session


class RankingTests(unittest.TestCase):
    def test_detection_window_maximum_precedes_control_minimum_and_is_not_fire_count(self):
        with temporary_session() as folder:
            config = settings()
            config.update(noise_window_s=10., ratio_noise_max_u16=10.)
            profile = {'settings': config, 'channels': {
                'upper': {'auto_threshold': False, 'threshold': 1.6, 'threshold_high': 1.3},
                'lower': {'auto_threshold': False, 'threshold': 2.2, 'threshold_high': 1.6}}}
            source = {'recorded_profile': profile, 'source_metadata': {'engine_sha256': sha256(DEFAULT_HOST/ENGINE_NAME)}}
            (folder/'passes-report.json').write_text(json.dumps(source), encoding='utf8')
            windows = [{'source': 'passes', 'window_id': i} for i in (1, 2, 3)]
            windows += [{'source': 'before', 'window_id': 1}, {'source': 'after', 'window_id': 1}]
            (folder/'comparison.json').write_text(json.dumps({'same_recorded_profiles': True, 'rpi_depths': [1],
                                                             'windows': windows, 'limitations': []}), encoding='utf8')
            for name, values in [('passes', [165., 155., 120.]), ('before', [190.]), ('after', [115.])]:
                with gzip.open(folder/(name+'-features.jsonl.gz'), 'wt', encoding='utf8') as stream:
                    for wid, product in enumerate(values, 1):
                        peak = {'product': product, 'noise_ref': 100., 'base_width': {'ok': True}}
                        history = {'noise_avg': 100.}
                        row = {'channel': 'upper', 'rpi_depth': 1, 'window_id': wid, 'global': peak,
                               'inside_gate': peak, 'global_noise_history': history, 'inside_noise_history': history,
                               'global_peak_position_ok': True}
                        stream.write(json.dumps(row)+'\n')
            result = run(folder, folder/'ranking.json')
            self.assertEqual(result['best_objective_values'], {'candidate_positive_windows': 3, 'background_positive_bins': 1})
            self.assertEqual(result['baseline_first_reference']['candidate_positive_windows'], 1)
            self.assertTrue(all(r['manual_curve_low_high'] == [1.2, 1.0] for r in result['all_lexicographic_winners']))
            self.assertFalse(result['independent_fire_events_evaluated'])
            self.assertTrue(result['not_a_detection_rate'])
            # Zero false bins never outranks a larger marked-window count.
            ranks = result['ranked_candidates']
            self.assertTrue(any(r['background_positive_bins'] == 0 for r in ranks))
            self.assertEqual(ranks[0]['candidate_positive_windows'], 3)


if __name__ == '__main__':
    unittest.main()
