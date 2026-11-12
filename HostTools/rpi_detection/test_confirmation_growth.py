"""Exercise real candidate gates, confirmation chains and portal persistence."""
import ast
import copy
import json
import os
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import patch_confirmation_growth as build
import test_base_width as base
import test_noise_area as config


class GrowthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.EngineTests.setUpClass()
        cls.ns = base.EngineTests.ns

    def setUp(self):
        base.EngineTests.setUp(self)
        s = self.scope
        s._det_confirm_growth_enabled = True
        s._det_confirm_growth_ratio = 2.0
        s._det_impulse_guard_enabled = False
        s._det_channel_confirm_count = lambda ch: 3
        for name in ('_det_note_confirmation_step', '_det_break_confirmation_chains'):
            setattr(s, name, MethodType(self.ns[name], s))
        self.sequence = 0

    def feed(self, amplitudes, channel=0, **kwargs):
        for amplitude in amplitudes:
            self.sequence += 1
            peak = kwargs.pop('peak', 80)
            self.scope._update_realtime_prevbuf_detection(channel, 100, peak_idx=peak,
                peak_value=amplitude, prod_arr=base.pulse(24, center=peak)*amplitude/1000,
                source_seq_in=self.sequence, **kwargs)

    def test_exact_boundary_and_full_window_for_each_channel(self):
        for ch in (0, 1):
            self.feed([1000, 4000], ch)
            self.assertFalse(any(event[ch] for event in self.fired))
            self.feed([2000], ch)
            self.assertTrue(any(event[ch] for event in self.fired))
            diag = getattr(self.scope, f'_det_last_confirm_growth{ch}')
            self.assertEqual((diag['first'], diag['last'], diag['ratio']), (1000, 2000, 2))
            self.assertEqual(diag['reason'], 'growth_confirmed')
            decision = getattr(self.scope, f'_det_confirm_frames{ch}')[-1]['decision']
            self.assertEqual(decision['confirm_growth'], diag)

    def test_below_boundary_rejected_and_window_slides_after_veto(self):
        self.feed([1000, 1100, 1999.9])
        self.assertFalse(self.fired)
        self.assertEqual(self.scope._det_growth_rejected0, 1)
        self.feed([2199.9])  # Last three now start with 1100.
        self.assertFalse(self.fired)
        self.feed([4000])  # Last three are 1999.9, 2199.9, 4000.
        self.assertEqual(self.fired, [(True, False)])
        self.assertEqual(self.scope._det_confirm_source_seqs0, (3, 4, 5))
        self.assertEqual(len(self.scope._det_confirm_products0), 3)

    def test_flat_and_falling_responses_never_pass_or_reuse_old_first_peak(self):
        self.feed([1000]*20 + [900, 800, 700, 600])
        self.assertFalse(self.fired)
        self.assertEqual(len(self.scope._det_confirm_products0), 3)
        self.assertEqual(self.scope._det_exceed0, 3)

    def test_off_preserves_flat_detection(self):
        self.scope._det_confirm_growth_enabled = False
        self.feed([1000]*3)
        self.assertEqual(self.fired, [(True, False)])
        self.assertEqual(self.scope._det_last_confirm_growth0['reason'], 'disabled')

    def test_custom_ratio(self):
        self.scope._det_confirm_growth_ratio = 1.5
        self.feed([1000, 1400, 1500])
        self.assertEqual(self.fired, [(True, False)])

    def test_channels_do_not_share_first_amplitude(self):
        self.feed([1000, 1200], 0)
        self.feed([3000, 4000, 5999], 1)
        self.assertFalse(self.fired)
        self.feed([2000], 0)
        self.assertEqual(self.fired, [(True, False)])

    def test_gap_miss_and_phase_reset_discard_old_first(self):
        for restart in ('gap', 'miss', 'phase'):
            with self.subTest(restart=restart):
                self.setUp()
                self.feed([1000, 1500])
                if restart == 'miss':
                    self.scope._update_realtime_prevbuf_detection(0, 0, peak_idx=80, prod_arr=base.pulse(24))
                    self.feed([2000])
                elif restart == 'gap':
                    self.feed([2000], confirmation_contiguous_in=False)
                else:
                    self.feed([2000], peak=90)
                self.assertEqual(len(self.scope._det_confirm_products0), 1)
                peak = 90 if restart == 'phase' else 80
                self.feed([2500, 3999], peak=peak)
                self.assertFalse(self.fired)

    def test_existing_hold_does_not_require_repeated_doubling(self):
        self.feed([1000, 1500, 2000])
        count = len(self.fired)
        self.feed([2000]*5)
        self.assertTrue(self.scope._det_hold0)
        self.assertEqual(len(self.fired), count)

    def test_does_not_bypass_diversity_or_other_primary_filters(self):
        self.scope._det_confirm_sample_diversity = 1
        self.feed([1000, 1500, 2000])
        self.assertFalse(self.fired)
        self.feed([3000], peak=81)
        self.assertEqual(self.fired, [(True, False)])

    def test_settings_reset_both_chains_and_hold_once_preserving_noise(self):
        s = self.scope
        self.feed([1000, 1500], 0)
        self.feed([3000, 3500], 1)
        resets = []
        s._det_review_history = None
        s._det_noise_avg0 = 12345
        s._detector_settings_snapshot = lambda: {'confirm_growth_enabled': s._det_confirm_growth_enabled,
                                                 'confirm_growth_ratio': s._det_confirm_growth_ratio}
        s._rt_detector_worker_reset_for_stream = lambda reason: resets.append(reason)
        with patch.dict(self.ns, common_mode_settings=lambda settings: {k:v for k,v in settings.items() if k.startswith('common_mode')}):
            for params in ({'confirm_growth_enabled': False}, {'confirm_growth_enabled': '1'}, {'confirm_growth_ratio': 3.0}):
                s._set_detector_channel_settings(params, persist=False)
                for ch in (0, 1):
                    self.assertEqual(getattr(s, f'_det_exceed{ch}'), 0)
                    self.assertFalse(getattr(s, f'_det_hold{ch}'))
                    self.assertEqual(getattr(s, f'_det_confirm_products{ch}'), ())
                self.assertEqual(s._det_noise_avg0, 12345)
            self.assertEqual(len(resets), 3)
            s._set_detector_channel_settings({'confirm_growth_ratio': 3}, persist=False)
            self.assertEqual(len(resets), 3)

    def test_one_confirmation_rejected_before_mutation(self):
        for params in ({'confirm0':1}, {'count1':1}, {'lower_confirm':1}):
            with self.assertRaisesRegex(ValueError, 'at least two'):
                self.scope._set_detector_channel_settings(params, persist=False)
            self.assertTrue(self.scope._det_confirm_growth_enabled)

    def test_invalid_or_missing_amplitudes_fail_closed(self):
        gate = self.ns['_rt_detector_confirmation_growth']
        for value in (0, -100, float('nan'), float('inf')):
            for products in (([value], [1000]), ([1000], [value])):
                result = gate(products, (0, 0), 2, True)
                self.assertFalse(result['ok'])
                json.dumps(result, allow_nan=False)
        for products, peaks in (((), ()), ((None, None), (0,0)), (([100], [200]), (9,0))):
            self.assertFalse(gate(products, peaks, 2, True)['ok'])
        self.assertTrue(gate(None, None, 2, False)['ok'])

    def test_selected_peak_and_unrounded_product_units(self):
        gate = self.ns['_rt_detector_confirmation_growth']
        # Unrelated larger peaks and rounded UI values cannot authorize growth.
        first, last = [1000.49, 99999], [99999, 2000.97]
        self.assertFalse(gate((first, last), (0, 1), 2, True)['ok'])
        last[1] = 2000.98
        self.assertTrue(gate((first, last), (0, 1), 2, True)['ok'])

    def test_off_matches_baseline_across_mixed_frames(self):
        path = build.BASE/'host/BMI30.200.py.2026-10-16-1743'
        with patch.dict(os.environ, BASE_WIDTH_TEST_ENGINE=str(path)):
            baseline = base.engine_functions()
        baseline.update({key:value for key,value in self.ns.items() if key not in baseline})
        instances = []
        for ns in (baseline, self.ns):
            instance = type('Case', (), {})()
            instance.ns = ns
            base.EngineTests.setUp(instance)
            instance.scope._det_confirm_growth_enabled = False
            instance.scope._det_note_confirmation_step = MethodType(ns['_det_note_confirmation_step'], instance.scope)
            instances.append(instance)
        rng = np.random.default_rng(21021)
        for seq in range(500):
            ch, peak = seq % 2, int(rng.integers(78, 84))
            amplitude = float(rng.uniform(100, 5000))
            level = 0 if seq % 13 == 0 else 100
            for instance in instances:
                instance.scope._update_realtime_prevbuf_detection(ch, level, peak_idx=peak,
                    prod_arr=base.pulse(24, center=peak)*amplitude/1000, source_seq_in=seq,
                    confirmation_contiguous_in=seq % 31 != 0)
            self.assertEqual(instances[0].fired, instances[1].fired)
            for channel in (0,1):
                for key in ('exceed', 'hold', 'confirm_peaks'):
                    self.assertEqual(getattr(instances[0].scope, f'_det_{key}{channel}', None),
                                     getattr(instances[1].scope, f'_det_{key}{channel}', None))
        self.assertTrue(instances[0].fired)


class PersistenceTests(unittest.TestCase):
    def test_form_storage_core_reload_and_partial_save(self):
        ns = config.portal()
        stored = {'gain': 17, 'operation': {'rpi_avg_n': 8}}
        ns['_load_config_json'] = lambda: copy.deepcopy(stored)
        ns['_save_config_json'] = lambda cfg: stored.update(cfg)
        ns['load_tag_detection_config_from_core'] = lambda: None
        engine = config.engine()
        with config.test_directory() as directory:
            file = directory/'config.json'
            engine['BMI30_CONFIG_FILE'] = str(file)
            for form, enabled, ratio in (({},False,2), ({'confirm_growth_enabled':'1','confirm_growth_ratio':'2.5'},True,2.5),
                                          ({'confirm_growth_enabled':'0','confirm_growth_ratio':'3'},False,3)):
                cfg = ns['tag_detection_config_from_form'](form)
                ns['save_tag_detection_config'](cfg)
                self.assertIs(ns['load_tag_detection_config']()['confirm_growth_enabled'], enabled)
                file.write_text(json.dumps(stored))
                loaded = engine['load_det_channel_settings']()
                self.assertIs(loaded['confirm_growth_enabled'], enabled)
                self.assertEqual(loaded['confirm_growth_ratio'], ratio)
                engine['save_det_channel_settings']({'confirm_growth_enabled': not enabled})
                persisted = json.loads(file.read_text())
                self.assertIs(persisted['tag_detection']['confirm_growth_enabled'], not enabled)
                self.assertEqual(persisted['confirm_growth_ratio'], ratio)
            self.assertEqual(stored['gain'], 17)
            self.assertEqual(stored['operation']['rpi_avg_n'], 8)

    def test_numeric_normalization_matches_core_and_missing_config_defaults(self):
        portal, engine = config.portal(), config.engine()
        for value, expected in (('bad',2), ('nan',2), ('inf',2), ('-inf',2), (-1,1), (101,100), ('2.34567',2.3457)):
            cfg = portal['tag_detection_config_from_form']({'confirm_growth_ratio':value})
            self.assertEqual(cfg['confirm_growth_ratio'], expected)
            self.assertEqual(engine['_det_clamp_float'](value,2,1,100), expected)

    def test_live_apply_sends_explicit_false_and_correct_ratio(self):
        ns, requests = config.portal(), []
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"ok":true}'
        ns['urlopen'] = lambda request, **kw: requests.append(json.loads(request.data)) or Reply()
        for enabled in (False, True):
            cfg = ns['tag_detection_config_from_form']({'confirm_growth_enabled':str(int(enabled)), 'confirm_growth_ratio':'2.5'})
            self.assertTrue(ns['apply_tag_detection_config_to_core'](cfg)[0])
            self.assertIs(requests[-1]['params']['confirm_growth_enabled'], enabled)
            self.assertEqual(requests[-1]['params']['confirm_growth_ratio'], 2.5)

    def test_live_readback_retains_core_values(self):
        ns = config.portal()
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self): return json.dumps({'detector': {'settings': {'confirm_growth_enabled':True,'confirm_growth_ratio':3.5}}}).encode()
        ns['urlopen'] = lambda *a, **kw: Reply()
        loaded = ns['load_tag_detection_config_from_core']()
        self.assertTrue(loaded['confirm_growth_enabled'])
        self.assertEqual(loaded['confirm_growth_ratio'], 3.5)

    def test_portal_refuses_one_confirmation_before_saving(self):
        ns = config.portal()
        cfg = ns['tag_detection_config_from_form']({'confirm_growth_enabled':'1','confirm0':'1'})
        with self.assertRaisesRegex(ValueError, 'at least two'):
            ns['save_tag_detection_config'](cfg)


class CoreIntegrationTests(unittest.TestCase):
    def test_startup_mapping_keeps_saved_growth_configuration(self):
        with config.test_directory() as directory:
            file = directory/'config.json'
            ns = config.functions('core.py', {'_load_default_detector_settings'},
                                  {'json':json, 'CONFIG_JSON':str(file)})
            for enabled in (False, True):
                for nested in (False, True):
                    settings = {'confirm_growth_enabled':enabled, 'confirm_growth_ratio':3.5}
                    file.write_text(json.dumps({'tag_detection':settings} if nested else settings))
                    self.assertEqual(ns['_load_default_detector_settings'](),settings)

    def test_status_api_exposes_real_engine_settings_and_defaults(self):
        tree = ast.parse((config.CANDIDATE/'core.py').read_text(encoding='utf8'))
        node = next(n for n in ast.walk(tree) if isinstance(n,ast.Dict) and
                    {'noise_estimator','confirm_sample_diversity','confirm_growth_enabled','confirm_growth_ratio'}.issubset(
                        {getattr(k,'value',None) for k in n.keys}))
        expression = compile(ast.Expression(body=node),'core status settings','eval')
        for settings, enabled, ratio in (({},False,2),
                ({'confirm_growth_enabled':True,'confirm_growth_ratio':3.5},True,3.5)):
            values = eval(expression, {'det_settings':settings,'scope':SimpleNamespace()})
            self.assertIs(values['confirm_growth_enabled'],enabled)
            self.assertEqual(values['confirm_growth_ratio'],ratio)


if __name__ == '__main__':
    unittest.main()
