"""Direction gate, live confirmation chains, migration and form/API coverage."""
import ast
import copy
import json
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch

import test_confirmation_growth as legacy
import test_noise_area as config


class DirectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        legacy.GrowthTests.setUpClass()
        cls.ns = legacy.GrowthTests.ns
        cls.ns['_snapshot'] = lambda s: {'confirm_change_mode':s._det_confirm_change_mode,
            'confirm_growth_enabled':s._det_confirm_growth_enabled, 'confirm_growth_ratio':s._det_confirm_growth_ratio}

    def gate(self, first, last, mode, ratio=2):
        return self.ns['_rt_detector_confirmation_growth'](([first], [last]), (0, 0), 2, mode, ratio)

    def test_all_modes_and_exact_boundaries(self):
        for mode, accepted in [('up', {2, 2.001}), ('down', {.4999, .5}),
                               ('either', {.4999, .5, 2, 2.001}), ('off', {.4999, .5, .5001, 1, 1.9999, 2, 2.001})]:
            for factor in (.4999, .5, .5001, 1, 1.9999, 2, 2.001):
                with self.subTest(mode=mode, factor=factor):
                    diag = self.gate(1000, 1000*factor, mode)
                    self.assertEqual(diag['mode'], mode)
                    self.assertEqual(diag['ok'], factor in accepted)

    def test_one_coefficient_is_symmetric_for_either(self):
        for ratio in (1, 1.5, 2, 3, 100):
            for first, last in [(100, 100*ratio), (100*ratio, 100)]:
                self.assertTrue(self.gate(first, last, 'either', ratio)['ok'])
                if ratio > 1:
                    self.assertFalse(self.gate(first, (first+last)/2, 'either', ratio)['ok'])

    def test_realtime_windows_use_direction_for_both_antennas(self):
        for mode in ('up', 'down', 'either', 'off'):
            for ch in (0, 1):
                for descending in (False, True):
                    case = legacy.GrowthTests()
                    case.ns = self.ns
                    case.setUp()
                    case.scope._det_confirm_change_mode = mode
                    case.scope._det_confirm_growth_enabled = mode != 'off'
                    case.feed([2000, 5000, 1000] if descending else [1000, 500, 2000], ch)
                    expected = mode in ('either', 'off') or mode == ('down' if descending else 'up')
                    self.assertEqual(bool(case.fired), expected, (mode, ch, descending))
                    self.assertEqual(getattr(case.scope, f'_det_last_confirm_growth{ch}')['mode'], mode)

    def test_direction_change_resets_chains_and_worker_even_while_enabled(self):
        case = legacy.GrowthTests(); case.ns = self.ns; case.setUp()
        s = case.scope
        s._det_confirm_change_mode = 'up'
        s._det_review_history = None
        s._det_noise_avg0 = 4321
        resets = []
        s._rt_detector_worker_reset_for_stream = lambda reason: resets.append(reason)
        s._detector_settings_snapshot = MethodType(self.ns['_snapshot'], s)
        for mode in ('down', 'either', 'off', 'up'):
            case.feed([1000, 1200])
            s._det_review_history = None
            with patch.dict(self.ns, common_mode_settings=lambda settings: {}):
                s._set_detector_channel_settings({'confirm_change_mode':mode}, persist=False)
            self.assertEqual(s._det_confirm_change_mode, mode)
            self.assertEqual(s._det_confirm_growth_enabled, mode != 'off')
            self.assertEqual(s._det_confirm_products0, ())
            self.assertFalse(s._det_hold0)
            self.assertEqual(s._det_noise_avg0, 4321)
        self.assertEqual(len(resets), 4)

    def test_invalid_mode_and_one_confirmation_rejected_before_mutation(self):
        for mode in ('down', 'either'):
            case = legacy.GrowthTests(); case.ns = self.ns; case.setUp()
            with self.assertRaisesRegex(ValueError, 'at least two'):
                case.scope._set_detector_channel_settings({'confirm_change_mode':mode, 'confirm0':1}, persist=False)
            self.assertFalse(hasattr(case.scope, '_det_confirm_change_mode'))
        for mode in ('bad', '', True):
            with self.assertRaisesRegex(ValueError, 'Invalid confirmation'):
                self.gate(1000, 2000, mode) if isinstance(mode, str) else self.ns['_det_confirmation_change_mode'](mode)

    def test_invalid_amplitudes_fail_closed_in_new_directions(self):
        for mode in ('down', 'either'):
            for invalid in (0, -1, float('inf'), float('nan')):
                for first, last in ((invalid, 1000), (1000, invalid)):
                    diag = self.gate(first, last, mode)
                    self.assertFalse(diag['ok'])
                    json.dumps(diag, allow_nan=False)


class MigrationTests(unittest.TestCase):
    def test_checkbox_combinations_roundtrip_and_single_factor(self):
        ns = config.portal(); stored = {'gain':17, 'operation':{'rpi_avg_n':8}}
        ns['_load_config_json'] = lambda: copy.deepcopy(stored)
        ns['_save_config_json'] = lambda cfg: stored.update(cfg)
        ns['load_tag_detection_config_from_core'] = lambda: None
        engine = config.engine()
        with config.test_directory() as directory:
            file = directory/'settings.json'; engine['BMI30_CONFIG_FILE'] = str(file)
            for up, down, mode in ((0,0,'off'), (1,0,'up'), (0,1,'down'), (1,1,'either')):
                cfg = ns['tag_detection_config_from_form']({'confirm_change_up':str(up), 'confirm_change_down':str(down),
                                                          'confirm_growth_ratio':'2.5'})
                self.assertEqual(cfg['confirm_change_mode'], mode)
                ns['save_tag_detection_config'](cfg)
                self.assertEqual(ns['load_tag_detection_config']()['confirm_change_mode'], mode)
                file.write_text(json.dumps(stored), encoding='utf-8')
                self.assertEqual(engine['load_det_channel_settings']()['confirm_change_mode'], mode)
                engine['save_det_channel_settings']({'confirm_growth_ratio':3.5})
                reloaded = engine['load_det_channel_settings']()
                self.assertEqual(reloaded['confirm_change_mode'], mode)
                self.assertEqual(reloaded['confirm_growth_ratio'], 3.5)
                self.assertEqual(reloaded['confirm_growth_enabled'], mode != 'off')
            self.assertEqual(stored['operation'], {'rpi_avg_n':8})
            self.assertEqual(stored['gain'], 17)

    def test_legacy_bool_maps_up_or_off_and_explicit_mode_wins(self):
        portal, engine = config.portal(), config.engine()
        with config.test_directory() as directory:
            file = directory/'config.json'; engine['BMI30_CONFIG_FILE'] = str(file)
            for old in (False, True, '0', '1'):
                expected = 'up' if str(old).lower() in ('true','1') else 'off'
                for nested in (False, True):
                    raw = {'confirm_growth_enabled':old,'confirm_growth_ratio':1.5}
                    file.write_text(json.dumps({'tag_detection':raw} if nested else raw), encoding='utf-8')
                    for cfg in (portal['_normalize_tag_detection_config'](raw), engine['load_det_channel_settings']()):
                        self.assertEqual(cfg['confirm_change_mode'], expected)
                        self.assertEqual(cfg['confirm_growth_ratio'], 1.5)
            for mode in ('up','down','either','off'):
                for old in (False, True):
                    raw = {'confirm_change_mode':mode,'confirm_growth_enabled':old}
                    file.write_text(json.dumps({'tag_detection':raw}), encoding='utf-8')
                    for cfg in (portal['_normalize_tag_detection_config'](raw), engine['load_det_channel_settings']()):
                        self.assertEqual(cfg['confirm_change_mode'], mode)
                        self.assertEqual(cfg['confirm_growth_enabled'], mode != 'off')

    def test_legacy_partial_write_updates_both_copies_of_mode(self):
        engine = config.engine()
        with config.test_directory() as directory:
            file = directory/'config.json'; engine['BMI30_CONFIG_FILE'] = str(file)
            file.write_text(json.dumps({'confirm_change_mode':'down', 'confirm_growth_ratio':1.5,
                'tag_detection':{'confirm_change_mode':'down','confirm_growth_enabled':True}}), encoding='utf-8')
            for enabled, mode in ((False, 'off'), (True, 'up')):
                engine['save_det_channel_settings']({'confirm_growth_enabled':enabled})
                saved = json.loads(file.read_text(encoding='utf-8'))
                self.assertEqual(saved['confirm_change_mode'], mode)
                self.assertEqual(saved['tag_detection']['confirm_change_mode'], mode)
                self.assertEqual(engine['load_det_channel_settings']()['confirm_growth_ratio'], 1.5)

    def test_core_startup_status_and_portal_live_apply_keep_direction(self):
        portal = config.portal(); requests = []
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self): return b'{"ok":true}'
        portal['urlopen'] = lambda request, **kw: requests.append(json.loads(request.data)) or Reply()
        tree = ast.parse((config.CANDIDATE/'core.py').read_text(encoding='utf-8'))
        node = next(n for n in ast.walk(tree) if isinstance(n,ast.Dict) and {'noise_estimator','confirm_change_mode'}.issubset({getattr(k,'value',None) for k in n.keys}))
        status = compile(ast.Expression(body=node), 'status', 'eval')
        with config.test_directory() as directory:
            file = directory/'config.json'
            core = config.functions('core.py', {'_load_default_detector_settings'}, {'json':json,'CONFIG_JSON':str(file)})
            for mode in ('up','down','either','off'):
                cfg = portal['tag_detection_config_from_form']({'confirm_change_mode':mode, 'confirm_growth_ratio':'1.5'})
                file.write_text(json.dumps({'tag_detection':cfg}), encoding='utf-8')
                self.assertEqual(core['_load_default_detector_settings']()['confirm_change_mode'], mode)
                values = eval(status, {'det_settings':cfg,'scope':SimpleNamespace()})
                self.assertEqual(values['confirm_change_mode'], mode)
                self.assertTrue(portal['apply_tag_detection_config_to_core'](cfg)[0])
                self.assertEqual(requests[-1]['params']['confirm_change_mode'], mode)
                self.assertEqual(requests[-1]['params']['confirm_growth_ratio'], 1.5)

    def test_invalid_form_does_not_save(self):
        ns = config.portal()
        with self.assertRaisesRegex(ValueError, 'Invalid confirmation'):
            ns['tag_detection_config_from_form']({'confirm_change_mode':'unknown'})


if __name__ == '__main__':
    unittest.main()
