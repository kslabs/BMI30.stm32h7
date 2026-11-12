"""Run actual candidate engine and portal functions without hardware I/O."""
import copy
import json
import os
from pathlib import Path
from types import MethodType
import unittest

import test_base_width as base
import test_noise_area as config


class DiversityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.EngineTests.setUpClass()
        cls.ns = base.EngineTests.ns

    def setUp(self):
        base.EngineTests.setUp(self)
        self.next_sequence = 0
        s = self.scope
        s._rt_det_confirm_count0 = s._rt_det_confirm_count1 = 5
        s._det_channel_confirm_count = lambda ch: getattr(s, f'_rt_det_confirm_count{ch}')
        s._det_confirm_phase_gate = 22
        s._det_confirm_sample_diversity = 1
        for name in ('_det_note_confirmation_step', '_det_break_confirmation_chains'):
            setattr(s, name, MethodType(self.ns[name], s))

    def feed(self, peaks, channel=0, **kwargs):
        for peak in peaks:
            seq = self.next_sequence
            self.next_sequence += 1
            self.scope._update_realtime_prevbuf_detection(channel, 100, peak_idx=peak,
                prod_arr=base.pulse(24, center=peak), source_seq_in=seq, **kwargs)

    def test_zero_preserves_identical_five(self):
        self.scope._det_confirm_sample_diversity = 0
        self.feed([92]*5)
        self.assertEqual(self.fired, [(True, False)])

    def test_one_rejects_identical_and_accepts_single_one_sample_move(self):
        self.feed([92]*15)
        self.assertFalse(self.fired)
        self.assertEqual(self.scope._det_exceed0, 5)
        self.assertEqual(self.scope._det_diversity_rejected0, 11)
        self.feed([93])
        self.assertEqual(self.fired, [(True, False)])
        self.assertEqual(self.scope._det_confirm_peaks0, (92, 92, 92, 92, 93))
        self.assertEqual(self.scope._det_confirm_source_seqs0, (11, 12, 13, 14, 15))
        self.assertEqual(len(self.scope._det_confirm_frames0), 5)

    def test_two_means_three_distinct_not_span_two(self):
        self.scope._det_confirm_sample_diversity = 2
        self.feed([90, 92, 90, 92, 90])
        self.assertFalse(self.fired)
        self.feed([91])
        self.assertEqual(self.fired, [(True, False)])

    def test_old_positions_age_out_of_window(self):
        self.scope._det_confirm_sample_diversity = 2
        self.feed([90, 91, 91, 91, 91, 91, 92])
        self.assertFalse(self.fired)  # 90 has already left the last five.
        self.assertEqual(self.scope._det_confirm_peaks0, (91, 91, 91, 91, 92))
        self.feed([93])
        self.assertEqual(self.fired, [(True, False)])

    def test_valid_moving_chain_fires_on_fifth_without_added_frames(self):
        self.feed([143, 144, 144, 145])
        self.assertFalse(self.fired)
        self.feed([145])
        self.assertEqual(self.fired, [(True, False)])

    def test_miss_and_frame_gap_start_new_chain(self):
        self.feed([90, 91, 92, 93])
        self.scope._update_realtime_prevbuf_detection(0, 0, peak_idx=94, prod_arr=base.pulse(24))
        self.feed([94]*5)
        self.assertFalse(self.fired)
        self.feed([95], confirmation_contiguous_in=False)
        self.assertEqual(self.scope._det_confirm_peaks0, (95,))
        self.assertFalse(self.fired)

    def test_channels_are_independent(self):
        self.feed([80]*5, 0)
        self.feed([80, 80, 81, 81, 81], 1)
        self.assertEqual(self.fired, [(False, True)])
        self.assertFalse(getattr(self.scope, '_det_hold0', False))

    def test_phase_gate_is_still_required(self):
        self.feed([80, 80, 80, 80, 105])
        self.assertFalse(self.fired)
        self.assertEqual(self.scope._det_confirm_peaks0, (105,))

    def test_invalid_combination_rejected_before_any_change(self):
        s = self.scope
        for values in ({'confirm_sample_diversity':5}, {'confirm0':1}, {'confirm_phase_gate':0}):
            with self.assertRaises(ValueError): s._set_detector_channel_settings(values, persist=False)
            self.assertEqual(s._det_confirm_sample_diversity, 1)
            self.assertEqual(s._rt_det_confirm_count0, 5)
            self.assertEqual(s._det_confirm_phase_gate, 22)

    def test_settings_change_resets_chain_and_fences_queued_results(self):
        s = self.scope
        self.ns['common_mode_settings'] = lambda settings: settings
        s._detector_settings_snapshot = lambda: {'confirm_sample_diversity':s._det_confirm_sample_diversity}
        resets = []
        s._rt_detector_worker_reset_for_stream = lambda reason: resets.append(reason)
        self.feed([80, 80, 81, 81])
        s._det_review_history = None
        s._set_detector_channel_settings({'confirm_sample_diversity':2}, persist=False)
        self.assertEqual(s._det_exceed0, 0)
        self.assertEqual(s._det_confirm_peaks0, ())
        self.assertEqual(s._det_confirm_source_seqs0, ())
        self.assertEqual(resets, ['detector_gate_settings_changed'])
        s._set_detector_channel_settings({'confirm_sample_diversity':2}, persist=False)
        self.assertEqual(len(resets), 1)

    def test_four_requires_all_five_different(self):
        self.scope._det_confirm_sample_diversity = 4
        self.feed([90, 91, 92, 93, 93])
        self.assertFalse(self.fired)
        self.feed([94, 95, 96, 97])
        self.assertEqual(self.fired, [(True, False)])


class PersistenceTests(unittest.TestCase):
    def test_portal_form_save_reload_live_payload_and_validation(self):
        ns = config.portal()
        stored = {'gain':17, 'operation':{'rpi_avg_n':8}}
        ns['_load_config_json'] = lambda: copy.deepcopy(stored)
        ns['_save_config_json'] = lambda cfg: stored.update(cfg)
        ns['load_tag_detection_config_from_core'] = lambda: None
        form = {'confirm0':'5', 'confirm1':'5', 'confirm_phase_gate':'22', 'confirm_sample_diversity':'2'}
        cfg = ns['tag_detection_config_from_form'](form)
        ns['save_tag_detection_config'](cfg)
        self.assertEqual(ns['load_tag_detection_config']()['confirm_sample_diversity'], 2)
        self.assertEqual(stored['confirm_sample_diversity'], 2)
        self.assertEqual(stored['gain'], 17)
        self.assertEqual(stored['operation'], {'rpi_avg_n':8})
        with self.assertRaises(ValueError): ns['save_tag_detection_config'](dict(cfg, confirm0=2))
        self.assertEqual(stored['tag_detection']['confirm0'], 5)
        # Use the real payload builder, with only network transport stubbed.
        requests = []
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return b'{"ok":true}'
        def urlopen(request, **kw):
            requests.append(json.loads(request.data)); return Reply()
        ns['urlopen'] = urlopen
        ns['apply_tag_detection_config_to_core'](cfg)
        self.assertEqual(requests[0]['params']['confirm_sample_diversity'], 2)

    def test_engine_reload_and_nested_configuration_stay_consistent(self):
        ns = config.engine()
        with config.test_directory() as directory:
            path = directory/'settings.json'
            ns['BMI30_CONFIG_FILE'] = str(path)
            path.write_text(json.dumps({'gain':17, 'tag_detection':{'confirm_sample_diversity':2}}))
            self.assertEqual(ns['load_det_channel_settings']()['confirm_sample_diversity'], 2)
            ns['save_det_channel_settings']({'confirm_sample_diversity':1})
            self.assertEqual(ns['load_det_channel_settings']()['confirm_sample_diversity'], 1)
            self.assertEqual(json.loads(path.read_text())['tag_detection']['confirm_sample_diversity'], 1)


if __name__ == '__main__': unittest.main()
