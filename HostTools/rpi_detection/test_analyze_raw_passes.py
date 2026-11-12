"""Passive raw sessions need an explicit antenna and trustworthy pass boundaries."""
import copy
import unittest

from analyze_ring_passes import analyze


class RawPassTests(unittest.TestCase):
    def recording(self):
        status = {'ok': True, 'connection': {'connected': True},
                  'detector': {'active': True, 'fire_seq0': 3, 'fire_seq1': 9,
                               'fire_snapshot0': {'seq': 3, 'timestamp': 1009}}}
        later = copy.deepcopy(status)
        later['detector']['fire_seq0'] = 4
        later['detector']['fire_snapshot0'] = {'seq': 4, 'timestamp': 1011}
        return [
            {'kind': 'ready', 'at': 1000, 'monotonic': 0, 'metadata': {'serial': 'identified'}, 'status': status},
            {'kind': 'pass_start', 'pass_id': 1, 'start_mono': 10, 'at': 1010, 'monotonic': 10},
            {'kind': 'status', 'status': status, 'at': 1010.2, 'monotonic': 10.2},
            {'kind': 'status', 'status': later, 'at': 1011.2, 'monotonic': 11.2},
            {'kind': 'pass_end', 'pass_id': 1, 'start_mono': 10, 'end_mono': 12,
             'observed_mono': 12, 'epoch': 1012, 'human_result': 'hit'},
            {'kind': 'end', 'at': 1013, 'monotonic': 13, 'status': later,
             'summary': {'capture_complete': True, 'ground_truth_complete': True, 'errors': []}},
        ]

    def test_raw_adapter_requires_explicit_channel_and_preserves_user_labels(self):
        rows = self.recording()
        with self.assertRaisesRegex(ValueError, 'explicit'):
            analyze(rows)
        result = analyze(rows, channel='upper', tag='Casino', distance_cm=40)
        self.assertEqual(result['source_format'], 'passive_raw_wire')
        self.assertEqual((result['tag'], result['distance_cm'], result['channel']), ('Casino', 40, 'upper'))
        self.assertTrue(result['recording_complete'])
        self.assertEqual(result['human_reported_detection']['rate'], 1)
        self.assertTrue(result['passes'][0]['selected_channel_event_observed'])
        self.assertEqual(result['passes'][0]['selected_counter_delta_in_sampled_interval'], 1)
        self.assertEqual(result['passes'][0]['selected_channel_fire_snapshots'][0]['seq'], 4)
        lower = analyze(rows, channel='lower')
        self.assertIsNone(lower['tag'])
        self.assertFalse(lower['passes'][0]['selected_channel_event_observed'])
        self.assertEqual(lower['human_event_evidence'], {'hit_without_event_evidence': 1})

    def test_no_start_no_final_and_explicit_incompleteness(self):
        rows = self.recording()
        with self.assertRaisesRegex(ValueError, 'recording start'):
            analyze(rows[1:], channel='upper')
        no_end = analyze(rows[:-1], channel='upper')
        self.assertFalse(no_end['recording_complete'])
        no_passes = analyze([r for r in rows if r['kind'] not in ('pass_start', 'pass_end')], channel='upper')
        self.assertFalse(no_passes['ground_truth_complete'])
        for field in ('capture_complete', 'ground_truth_complete'):
            changed = copy.deepcopy(rows)
            changed[-1]['summary'][field] = False
            self.assertFalse(analyze(changed, channel='upper')['recording_complete'])

    def test_orphan_end_missing_start_and_reused_ids_never_enter_rate(self):
        rows = self.recording()
        orphan = {**rows[-2], 'kind': 'orphan_pass_end', 'pass_id': 99, 'human_result': 'miss'}
        result = analyze(rows[:-1] + [orphan, rows[-1]], channel='upper')
        self.assertEqual(result['human_reported_detection']['labelled_passes'], 1)
        self.assertEqual(len(result['ignored_marks']), 1)
        self.assertFalse(result['ground_truth_complete'])
        no_start = analyze([row for row in rows if row['kind'] != 'pass_start'], channel='upper')
        self.assertEqual(no_start['human_reported_detection']['labelled_passes'], 0)
        self.assertEqual(no_start['excluded_human_counts'], {'hit': 1})
        for duplicate in (rows[1], rows[-2]):
            result = analyze(rows[:-1] + [copy.deepcopy(duplicate), rows[-1]], channel='upper')
            self.assertEqual(result['human_reported_detection']['labelled_passes'], 0)
            self.assertFalse(result['ground_truth_complete'])

    def test_invalid_and_overlapping_windows_remain_visible_and_excluded(self):
        rows = self.recording()
        bad = copy.deepcopy(rows)
        bad[-2]['end_mono'] = 9
        result = analyze(bad, channel='upper')
        self.assertEqual(result['human_counts'], {'hit': 1})
        self.assertEqual(result['human_reported_detection']['labelled_passes'], 0)
        self.assertIn('invalid_pass_times', result['invalid_marks'][0]['reasons'])
        second_start = {'kind': 'pass_start', 'pass_id': 2, 'start_mono': 11}
        second_end = {**rows[-2], 'pass_id': 2, 'start_mono': 11, 'end_mono': 13, 'human_result': 'miss'}
        result = analyze(rows[:-1] + [second_start, second_end, rows[-1]], channel='upper')
        self.assertEqual(result['human_reported_detection']['labelled_passes'], 0)
        self.assertEqual(len(result['invalid_marks']), 2)


if __name__ == '__main__':
    unittest.main()
