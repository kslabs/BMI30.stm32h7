import unittest
from pathlib import Path
import gzip
import io
import json
from unittest.mock import patch

from analyze_ring_passes import analyze, labelled_rate, read_complete_lines


class PassEvidenceTests(unittest.TestCase):
    def header(self):
        return {'kind':'start','channel':'upper','tag':'Casino','distance_cm':30}

    def sample(self, mono, upper_time=1009, lower_time=1011):
        return {'kind':'sample','observed_mono':mono,'epoch':1000+mono,
                'status':{'ok':True,'connection':{'connected':True},
                          'detector':{'active':True,'fire_snapshot0':{'seq':9,'timestamp':upper_time},
                                      'fire_snapshot1':{'seq':4,'timestamp':lower_time}}}}

    def finish(self, result='miss'):
        return {'kind':'pass_end','pass_id':1,'human_result':result,'start_mono':10,
                'end_mono':12,'observed_mono':12.15,'epoch':1012.15}

    def start(self, pass_id=1, mono=10):
        return {'kind': 'pass_start', 'pass_id': pass_id, 'start_mono': mono}

    def test_stale_snapshot_and_other_channel_do_not_become_upper_hit(self):
        result=analyze([self.header(),self.start(),self.sample(10.2),self.sample(11.5),self.finish()])
        p=result['passes'][0]
        self.assertEqual(p['human_result'],'miss')
        self.assertEqual(p['selected_channel_fire_snapshots'],[])
        self.assertEqual(len(p['other_channel_fire_snapshots']),1)

    def test_snapshot_observed_after_result_uses_actual_event_timestamp(self):
        result=analyze([self.header(),self.start(),self.sample(12.3,upper_time=1011),self.finish()])
        p=result['passes'][0]
        self.assertEqual(len(p['selected_channel_fire_snapshots']),1)
        self.assertEqual(p['human_result'],'miss')  # Preserve disagreement.
        self.assertEqual(p['sample_count'],0)

    def test_unfinished_start_and_orphan_result_are_not_inferred_misses(self):
        result=analyze([self.header(),{'kind':'pass_start','pass_id':1,'start_mono':10},
                        {'kind':'ignored_mark','action':'hit','reason':'no_open_pass'}])
        self.assertEqual(result['human_counts'],{})
        self.assertEqual(result['pending_pass_ids'],[1])
        self.assertEqual(len(result['ignored_marks']),1)
        self.assertFalse(result['recording_complete'])

    def test_human_rate_preserves_disagreement_and_excludes_unknown_marks(self):
        hit = self.finish('hit')
        miss = {**self.finish('miss'), 'pass_id': 2, 'start_mono': 20, 'end_mono': 22}
        unknown = {**self.finish('unlabelled'), 'pass_id': 3, 'start_mono': 30, 'end_mono': 32}
        result = analyze([self.header(), self.start(), self.sample(12.3, upper_time=1011), hit,
                          self.start(2, 20), miss, self.start(3, 30), unknown,
                          {'kind': 'pass_start', 'pass_id': 4, 'start_mono': 40}])
        rate = result['human_reported_detection']
        self.assertEqual((rate['hits'], rate['misses'], rate['labelled_passes']), (1, 1, 2))
        self.assertEqual(rate['rate'], .5)
        self.assertEqual(result['human_event_evidence']['hit_with_event_evidence'], 1)
        self.assertEqual(result['human_event_evidence']['miss_without_event_evidence'], 1)
        self.assertEqual(result['pending_pass_ids'], [4])
        self.assertIsNone(labelled_rate(0, 0)['rate'])
        self.assertLess(labelled_rate(30, 0)['wilson_95'][0], .9)

    def test_counter_reset_is_unknown_and_unchanged_counter_does_not_prove_miss(self):
        a, b = self.sample(10.2), self.sample(11.5)
        a['status']['detector']['fire_seq0'] = 10
        b['status']['detector']['fire_seq0'] = 2
        row = analyze([self.header(), self.start(), a, b, self.finish()])['passes'][0]
        self.assertIsNone(row['selected_counter_delta_in_sampled_interval'])
        self.assertFalse(row['selected_channel_event_observed'])
        b['status']['detector']['fire_seq0'] = 10
        result = analyze([self.header(), self.start(), a, b, self.finish()])
        self.assertEqual(result['passes'][0]['selected_counter_delta_in_sampled_interval'], 0)
        self.assertEqual(result['human_event_evidence'], {'miss_without_event_evidence': 1})

    def test_api_failure_or_explicit_incomplete_end_is_not_complete_recording(self):
        for extra in ({'kind': 'waveform_error', 'error': 'timeout'},
                      {'kind': 'input_error', 'error': 'SYN_DROPPED'}):
            result = analyze([self.header(), extra, {'kind': 'end', 'observer_complete': True}])
            self.assertFalse(result['recording_complete'])
        self.assertFalse(analyze([self.header(), {'kind': 'end', 'observer_complete': False}])['recording_complete'])

    def test_reads_compressed_marks_without_turning_orphan_result_into_a_pass(self):
        rows = [self.header(), {'kind': 'ignored_mark', 'action': 'hit', 'reason': 'no_open_pass'}]
        payload = gzip.compress(('\n'.join(json.dumps(row) for row in rows) + '\n').encode())
        with patch('builtins.open', return_value=io.BytesIO(payload)):
            path = Path('capture.jsonl.gz')
            result = analyze(read_complete_lines(path))
            self.assertEqual(result['passes'], [])
            self.assertEqual(len(result['ignored_marks']), 1)


if __name__=='__main__':
    unittest.main()
