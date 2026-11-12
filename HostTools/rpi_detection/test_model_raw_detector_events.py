"""Production state-machine controls, independent of recorded zero-FIRE data."""
import ast
from collections import deque
import gzip
import json
from pathlib import Path
import unittest

import numpy as np

from model_raw_detector_events import DEFAULT_HOST, ENGINE_NAME, EventModel, records, count_event_windows, validate_baseline, interval_coverage, activity_summary


ROOT = Path(__file__).resolve().parents[2]
CAPTURE = ROOT/'.tmp/casino-s07-35cm-20261104/passes-35cm-4-1-01/capture.jsonl.gz'


class EventControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ready = next(r for r in records(CAPTURE) if r['kind'] == 'ready')

    def setUp(self):
        self.model = EventModel(self.ready['status'], self.ready['metadata']['engine_sha256'])
        self.seq = 0

    def feed(self, accepted=True, seq=None):
        self.seq = self.seq+1 if seq is None else seq
        x = np.arange(200)
        product = np.full(200, 100., dtype=np.float64)
        if accepted:
            product += np.maximum(0., 1.-abs(x-40)/6.)*10000.
            product[34] = product[46] = 0.  # Measured zero crossings, nonzero remote noise.
        peak = int(np.argmax(product)); amplitude = float(product[peak])
        made = (int(amplitude), 0, peak, amplitude, product)
        trace = np.full(200, 32778, dtype=np.int32)
        return self.model.decision(0, made, trace, trace, self.seq, self.seq*.02, 1000.+self.seq*.02)

    def test_three_confirmations_one_fire_hold_no_extra_release_then_new_fire(self):
        self.feed(); self.feed()
        self.assertEqual(len(self.model.events), 0)
        self.feed()
        self.assertEqual(len(self.model.events), 1)
        for _ in range(8):
            self.feed()
        self.assertEqual(len(self.model.events), 1)
        self.assertTrue(self.model.scope._det_hold0)
        self.feed(False)
        self.assertFalse(self.model.scope._det_hold0)
        self.feed(); self.feed(); self.feed()
        self.assertEqual(len(self.model.events), 2)

    def test_logical_sequence_gap_breaks_confirmation_without_mocked_noise(self):
        self.feed(); self.feed(); self.feed(seq=10)
        self.assertEqual(len(self.model.events), 0)
        self.assertEqual(self.model.scope._det_exceed0, 1)
        self.feed(); self.feed()
        self.assertEqual(len(self.model.events), 1)
        self.assertGreater(self.model.scope._det_noise_avg0, 0)
        self.assertGreater(self.model.scope._det_thr0, self.model.scope._det_noise_avg0)

    def test_actual_temporal_noise_window_expires_and_threshold_is_not_constant(self):
        s = self.model.scope
        first = s._det_accumulated_reference_noise(0, 100., now=100.)
        second = s._det_accumulated_reference_noise(0, 1000., now=101.)
        self.assertGreater(second, first)
        self.assertGreater(s._det_channel_trigger_level(0, second, 1.6),
                           s._det_channel_trigger_level(0, first, 1.6))
        self.assertEqual(s._det_accumulated_reference_noise(0, 50., now=112.), 50.)

    def test_wire_phase_pairs_use_production_smoothing_and_emit_one_new_event(self):
        x = np.arange(200)
        raw = np.full(200, 32778, dtype=np.int32)
        raw[20:61] = 32768
        raw += np.rint(np.maximum(0., 1.-abs(x-40)/6.)*100.).astype(np.int32)
        payload = raw.astype('<u2').tobytes()
        for seq in range(1, 9):
            frame = {'samples': 200, 'channel': 0, 'seq': seq, 'phase': seq & 1,
                     'timestamp_ticks': seq*10, 'reserved2': seq & 1,
                     'completion_timing': {'host_monotonic': seq*.01, 'host_epoch': 1000.+seq*.01}}
            self.model.frame(frame, payload)
        self.assertEqual(len(self.model.events), 1)
        self.assertEqual(self.model.events[0]['source_seq'], 4)
        self.assertEqual(self.model.counts['jobs'], 7)

    def test_unsupported_profile_and_wrong_source_hash_fail_closed(self):
        changed = json.loads(json.dumps(self.ready['status']))
        changed['detector']['settings']['common_mode_enabled'] = True
        with self.assertRaises(ValueError):
            EventModel(changed, self.ready['metadata']['engine_sha256'])
        with self.assertRaises(ValueError):
            EventModel(self.ready['status'], '0'*64)

    def test_new_events_assign_once_at_window_boundary_and_hold_is_not_an_event(self):
        windows = [{'pass_id': 1, 'start_mono': 1., 'end_mono': 2., 'human_result': 'unlabelled'},
                   {'pass_id': 2, 'start_mono': 2., 'end_mono': 3., 'human_result': 'unlabelled'}]
        result = count_event_windows(windows, [{'channel': 0, 'monotonic': 2.},
                                               {'channel': 0, 'monotonic': 2.5}])
        self.assertFalse(result[0]['new_upper_fire'])
        self.assertTrue(result[1]['new_upper_fire'])
        self.assertEqual(result[1]['new_upper_events'], 2)
        self.assertEqual(sum(x['new_upper_fire'] for x in result), 1)
        with self.assertRaises(ValueError):
            count_event_windows([windows[1], windows[1]], [{'channel': 0, 'monotonic': 2.}])

    def test_counterfactual_baseline_is_bound_to_capture_and_exact_engine(self):
        report = {'archive_sha256': 'a', 'source_hashes': {ENGINE_NAME: 'b'}, 'rpi_depth': 1,
                  'temporal_method': 'mean', 'manual_upper_ratios': None,
                  'baseline_zero_fire_matches': True, 'actual_fire_counter_deltas': [0, 2],
                  'model_new_fire_events_by_channel': [0, 3]}
        validate_baseline(report, 'a', 'b')
        for archive, engine in (('wrong', 'b'), ('a', 'wrong')):
            with self.assertRaises(ValueError):
                validate_baseline(report, archive, engine)
        report['model_new_fire_events_by_channel'][0] = 1
        with self.assertRaises(ValueError):
            validate_baseline(report, 'a', 'b')

    def test_confirmation_override_changes_only_new_fire_latency(self):
        self.model = EventModel(self.ready['status'], self.ready['metadata']['engine_sha256'], confirm_upper=1)
        self.feed()
        self.assertEqual(len(self.model.events), 1)
        self.feed(); self.feed()
        self.assertEqual(len(self.model.events), 1)
        self.assertEqual(self.ready['status']['detector']['channels']['upper']['confirm_count'], 3)

    def test_noise_area_override_uses_real_geometry_without_mutating_saved_profile(self):
        original = self.ready['status']['detector']['settings']['noise_area_percent']
        model = EventModel(self.ready['status'], self.ready['metadata']['engine_sha256'], noise_area_percent=30)
        mask = model.ns['_rt_detector_noise_area_mask'](200, 40)
        self.assertEqual(int(mask.sum()), 60)
        self.assertEqual(model.dsp.settings['noise_area_percent'], 30)
        self.assertEqual(self.ready['status']['detector']['settings']['noise_area_percent'], original)

    def test_staged_inside_search_keeps_outside_product_and_changes_new_fire(self):
        old = EventModel(self.ready['status'], self.ready['metadata']['engine_sha256'], noise_area_percent=30)
        new = EventModel(self.ready['status'], self.ready['metadata']['engine_sha256'],
                         noise_area_percent=30, peak_search='inside')
        x = np.arange(200)
        raw = np.full(200, 32778, dtype=np.int32)
        raw[20:61] = 32768
        raw += np.rint(np.maximum(0., 1.-abs(x-40)/6.)*100.).astype(np.int32)
        raw[0] = 33768
        traces = [m.ns['_rt_detector_smooth_array'](raw, 4) for m in (old, new)]
        made = [m.ns['_rt_detector_product_current_prev'](trace, trace, 0, 'prod', 16384.25, fixed_shift=0)
                for m, trace in zip((old, new), traces)]
        self.assertEqual(made[0][2], 0)
        self.assertEqual(made[1][2], 40)
        np.testing.assert_array_equal(made[0][4], made[1][4])
        self.assertGreater(made[1][4][0], made[1][4][40])
        for seq in range(1, 5):
            for model, values, trace in zip((old, new), made, traces):
                model.decision(0, values, trace, trace, seq, seq*.02, 1000.+seq*.02)
        self.assertEqual(len(old.events), 0)
        self.assertEqual(len(new.events), 1)

    def test_hold_duration_and_warmup_diagnostic_do_not_turn_held_samples_into_events(self):
        self.feed(); self.feed(); self.feed()
        for _ in range(8):
            self.feed()
        self.feed(False)
        result = activity_summary(self.model, 0., 20., warmup_s=10.)
        upper = result['channels'][0]
        self.assertEqual(upper['new_fire_total'], 1)
        self.assertEqual(upper['new_fire_after_warmup'], 0)
        self.assertGreater(upper['native_hold_seconds'], .1)
        self.assertLess(upper['native_hold_seconds'], .3)
        self.assertEqual(interval_coverage([(1., 4.), (2., 6.)], 3., 5.), 2.)
        self.assertTrue(result['ranking_total_FP_is_not_replaced'])


if __name__ == '__main__':
    unittest.main()
