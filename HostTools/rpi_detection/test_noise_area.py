"""Exercise real patched detector, persistence, API and frame metadata functions."""
import __future__
import ast
from collections import deque
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
from types import MethodType, SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch
from urllib.request import Request

import numpy as np

from bmi30_base_width import base_width_decision, base_width_enabled, base_width_limit


ROOT = Path(os.getenv('NOISE_AREA_TEST_ROOT', Path(__file__).resolve().parents[2]))
CANDIDATE = Path(os.getenv('NOISE_AREA_TEST_DIR', ROOT / '.tmp/noise-area-20261014/candidate'))


@contextmanager
def test_directory():
    folder = ROOT / '.tmp/noise-area-20261014' / ('case-' + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        for file in folder.iterdir():
            file.unlink()
        folder.rmdir()


def functions(filename, names, namespace):
    tree = ast.parse((CANDIDATE / filename).read_text(encoding='utf8'))
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace


def engine():
    tree = ast.parse((CANDIDATE / 'engine.py').read_text(encoding='utf8'))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith('_det_clamp')}
    names.update({'_rt_product_abs_array', '_rt_detector_noise_area_mask', '_rt_detector_noise_area_percent',
                  '_rt_detector_noise_divisions', '_rt_detector_relative_noise_reference',
                  '_rt_detector_quarter_noise_supported', '_rt_detector_current_noise_supported',
                  '_set_detector_channel_settings', '_det_reset_noise_accumulators',
                  '_det_parse_bool', '_det_confirmation_change_mode', '_rt_detector_apply_settings_env', 'load_det_channel_settings',
                  'save_det_channel_settings', '_smooth_mode_value'})
    names.add('_rt_detector_peak_index_bound')
    ns = dict(np=np, os=os, time=time, json=json, deque=deque,
              base_width_enabled=base_width_enabled, base_width_limit=base_width_limit,
              common_mode_settings=lambda s: {k:v for k,v in s.items() if k.startswith('common_mode')},
              COMMON_MODE_DEFAULTS={}, SMOOTH_MODE_NAMES=('off', 'med3', 'med5', 'avg7', 'avg9', 'avg11'),
              _RT_DETECT_LEVEL_MAX=2147483647, load_det_ratio=lambda: 2.,
              _rt_tag_response_filter_filename=lambda v: str(v),
              _rt_detector_named_filters_active=lambda: False)
    return functions('engine.py', names, ns)


def portal():
    tree = ast.parse((CANDIDATE / 'portal.py').read_text(encoding='utf8'))
    names = {'_float_form_value', '_int_form_value', '_bool_form_value', '_noise_unit_value', '_det_confirmation_change_mode',
             '_validate_sample_diversity',
             '_normalize_tag_response_filter_slots', '_tag_response_filter_filename',
             '_normalize_tag_detection_config', 'tag_detection_config_from_form',
             'save_tag_detection_config', 'load_tag_detection_config',
             'load_tag_detection_config_from_core', 'apply_tag_detection_config_to_core'}
    ns = dict(os=os, json=json, time=time, Request=Request, CORE_SERVICE_URL='http://localhost:8765',
              list_tag_response_filter_files=lambda: [])
    for name in ['DEFAULT_TAG_DETECTION_CONFIG', 'BARKHAUSEN_TAG_KEYS', 'CORE_TAG_DETECTION_KEYS']:
        node = next(n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and
                    (getattr(n, 'target', None) is not None and getattr(n.target, 'id', '') == name or
                     any(getattr(t, 'id', '') == name for t in getattr(n, 'targets', []))))
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'portal constants', 'exec',
                     flags=__future__.annotations.compiler_flag), ns)
    return functions('portal.py', names, ns)


class NoiseAreaTests(unittest.TestCase):
    def setUp(self):
        self.ns = engine()

    def test_count_peak_exclusion_and_rotation_for_all_percentages(self):
        mask = self.ns['_rt_detector_noise_area_mask']
        for n in (4, 5, 199, 200, 201, 600):
            for percent in range(5, 96):
                reference = mask(n, 0, percent)
                self.assertEqual(np.count_nonzero(reference), max(1, min(n-1, (n*percent+50)//100)))
                self.assertFalse(reference[0])
                for peak in (1, n//2, n-1):
                    np.testing.assert_array_equal(mask(n, peak, percent), np.roll(reference, peak))

    def test_expansion_is_nested(self):
        for n in (5, 199, 200, 600):
            previous = np.zeros(n, dtype=bool)
            for percent in range(5, 96):
                current = self.ns['_rt_detector_noise_area_mask'](n, n-1, percent)
                self.assertFalse(np.any(previous & ~current))
                previous = current

    def test_boundaries_move_towards_peak_with_increased_coverage(self):
        for percent, first, stop in [(25, 75, 125), (50, 50, 150), (80, 20, 180), (95, 5, 195)]:
            mask = self.ns['_rt_detector_noise_area_mask'](200, 0, percent)
            np.testing.assert_array_equal(np.flatnonzero(mask), np.arange(first, stop))

    def test_default_matches_previous_six_sector_algorithm_at_working_lengths(self):
        for n in (200, 600):
            for peak in range(n):
                indices = np.arange(n)
                parts = (((indices-peak)*6+n//2) % (n*6)) // n
                distance = np.minimum((indices-peak)%n, (peak-indices)%n)
                legacy = (distance > 40) & (np.minimum(parts, 6-parts) > 1)
                np.testing.assert_array_equal(self.ns['_rt_detector_noise_area_mask'](n, peak, 50), legacy)

    def test_noise_and_snr_verdict_change_when_interference_enters_area(self):
        data = np.ones(200)
        data[0], data[25] = 100, 60
        fn = self.ns['_rt_detector_quarter_noise_supported']
        with patch.dict(os.environ, BMI30_RT_NOISE_AREA_PERCENT='50'):
            passed, noise, *_ = fn(data, 0, 100, 2.)
            self.assertTrue(passed); self.assertEqual(noise, 1.)
        with patch.dict(os.environ, BMI30_RT_NOISE_AREA_PERCENT='80'):
            passed, noise, *_ = fn(data, 0, 100, 2.)
            self.assertFalse(passed); self.assertEqual(noise, 60.)

    def test_boundary_samples_and_strict_ratio(self):
        data = np.ones(200); data[0] = 100
        data[49], data[150] = 99, 99
        with patch.dict(os.environ, BMI30_RT_NOISE_AREA_PERCENT='50'):
            self.assertEqual(self.ns['_rt_detector_relative_noise_reference'](data, 0)[0], 1.)
            data[50] = 50
            self.assertFalse(self.ns['_rt_detector_quarter_noise_supported'](data, 0, 100, 2.)[0])

    def test_short_buffer_rejected_and_percentage_clamped(self):
        for n in range(4):
            self.assertFalse(self.ns['_rt_detector_noise_area_mask'](n, 0).any())
            self.assertFalse(self.ns['_rt_detector_quarter_noise_supported'](np.ones(n), 0, 1, 2.)[0])
        for value, expected in [('bad', 50), ('-1', 5), ('100', 95)]:
            with patch.dict(os.environ, BMI30_RT_NOISE_AREA_PERCENT=value):
                self.assertEqual(self.ns['_rt_detector_noise_area_percent'](), expected)

    def test_apply_resets_histories_confirmations_and_worker_once(self):
        s = SimpleNamespace(_det_noise_area_percent=50, _det_base_width_enabled=False, _det_base_width_max=24)
        for name in ('_set_detector_channel_settings', '_det_reset_noise_accumulators'):
            setattr(s, name, MethodType(self.ns[name], s))
        s._rt_detector_worker_reset_for_stream = Mock()
        s._clear_web_pre_fire = Mock()
        s._detector_settings_snapshot = lambda: {'noise_area_percent':s._det_noise_area_percent}
        for ch in (0, 1):
            for kind in ('noise', 'snr'):
                setattr(s, f'_det_{kind}_hist{ch}', deque([(1., 10.)]))
            setattr(s, f'_det_exceed{ch}', 3)
            for key in ('peaks', 'source_seqs', 'products', 'frames'):
                setattr(s, f'_det_confirm_{key}{ch}', (1, 2, 3))
        with patch.dict(os.environ):
            self.assertEqual(s._set_detector_channel_settings({'noise_area_percent':80}, persist=False)['noise_area_percent'],80)
            self.assertEqual(os.environ['BMI30_RT_NOISE_AREA_PERCENT'], '80')
            for ch in (0,1):
                self.assertEqual(getattr(s,f'_det_exceed{ch}'),0)
                self.assertFalse(getattr(s,f'_det_noise_hist{ch}'))
                self.assertFalse(getattr(s,f'_det_snr_hist{ch}'))
                for key in ('peaks','source_seqs','products','frames'):
                    self.assertEqual(getattr(s,f'_det_confirm_{key}{ch}'),())
            s._rt_detector_worker_reset_for_stream.assert_called_once()
            s._set_detector_channel_settings({'noise_area_percent':80},persist=False)
            s._rt_detector_worker_reset_for_stream.assert_called_once()

    def test_core_reload_reads_nested_portal_and_persisted_runtime_settings(self):
        with test_directory() as folder, patch.dict(os.environ):
            path = Path(folder)/'config.json';self.ns['BMI30_CONFIG_FILE'] = str(path)
            path.write_text(json.dumps({'gain':17,'tag_detection':{'noise_area_percent':75}}))
            self.assertEqual(self.ns['load_det_channel_settings']()['noise_area_percent'],75)
            self.ns['save_det_channel_settings']({'noise_area_percent':85})
            self.assertEqual(self.ns['load_det_channel_settings']()['noise_area_percent'],85)
            self.assertEqual(json.loads(path.read_text())['gain'],17)


class PortalAndFrameTests(unittest.TestCase):
    def test_form_save_only_reload_and_live_apply_payload(self):
        ns=portal();stored={'gain':17,'operation':{'rpi_avg_n':12},'noise_area_percent':90}
        ns['_load_config_json']=lambda:copy.deepcopy(stored)
        ns['_save_config_json']=lambda cfg:stored.update(cfg)
        ns['load_tag_detection_config_from_core']=lambda:None
        cfg=ns['tag_detection_config_from_form']({'noise_area_percent':'80'})
        self.assertEqual(cfg['noise_area_percent'],80)
        ns['save_tag_detection_config'](cfg)
        self.assertEqual(ns['load_tag_detection_config']()['noise_area_percent'],80)
        self.assertEqual(stored['noise_area_percent'],80)
        self.assertEqual(stored['gain'],17);self.assertEqual(stored['operation']['rpi_avg_n'],12)
        captured=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"ok":true}'
        ns['urlopen']=lambda request,**kw:captured.append(json.loads(request.data)) or Response()
        self.assertTrue(ns['apply_tag_detection_config_to_core'](cfg)[0])
        self.assertEqual(captured[0]['params']['noise_area_percent'],80)

    def test_form_limits_and_default(self):
        fn=portal()['tag_detection_config_from_form']
        for form,expected in [({},50),({'noise_area_percent':'0'},5),({'noise_area_percent':'100'},95),({'noise_area_percent':'bad'},50)]:
            self.assertEqual(fn(form)['noise_area_percent'],expected)

    def test_live_and_saved_frames_keep_their_original_geometry(self):
        ns=functions('core.py',{'_review_frame_metadata'},dict(np=np,base_width_decision=base_width_decision,
                     _product_display_scale=lambda:10.))
        data=np.ones(200);data[80]=100
        scope=SimpleNamespace(_det_noise_area_percent=90,_det_base_width_max=24,_det_base_width_enabled=False,
                              _rt_prod_raw_len0=200,_rt_prod_raw0=data)
        metadata=ns['_review_frame_metadata']
        self.assertEqual(metadata({},200,scope)['prod_peak0']['noise_area_percent'],90)
        saved=metadata({'anchor':{'snapshot':{'noise_area_percent':60}},'prod0':data},200,scope)
        self.assertEqual(saved['prod_peak0']['noise_area_percent'],60)
        self.assertEqual(saved['prod_peak0']['sample_count'],200)


if __name__ == '__main__':
    unittest.main()
