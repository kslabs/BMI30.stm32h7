"""Regress native detector precision and reject missing/zero reference noise."""
import ast
import os
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from test_noise_area import engine, functions, CANDIDATE


def native_functions():
    ns = engine()
    ns.update(_RT_DETECT_LEVEL_MAX=(1 << 63)-1, _rt_product_default_scale=lambda: 16384.250003814755)
    return functions('engine.py', {'_rt_clamp_detector_level', '_rt_detector_level_from_peak',
        '_rt_detector_product_current_prev', '_rt_detector_product_display', '_rt_update_product_plot'}, ns)


class NativeScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = native_functions()

    def test_int64_multiply_does_not_wrap_and_level_does_not_depend_on_display_scale(self):
        current = np.array([0, 32769, 32767, 65535], dtype=np.uint16)
        previous = np.array([0, 32767, 32769, 65535], dtype=np.uint16)
        for scale in [1., 16384.250003814755, 1e9]:
            result = self.ns['_rt_detector_product_current_prev'](current, previous, 0, 'prod', scale)
            level, shift, idx, peak, product = result
            self.assertEqual(product.dtype, np.dtype('int64'))
            np.testing.assert_array_equal(product, [1073741824, 1, 1, 1073676289])
            self.assertEqual((level, shift, idx, peak), (1073741824, 0, 0, 1073741824.))
            self.assertEqual(self.ns['_rt_detector_level_from_peak'](1., 'prod', scale), 1)

    def test_float_product_noise_retains_64_bit_fraction_and_low_level(self):
        fn = self.ns['_rt_product_abs_array']
        source = np.array([16777217.25, -16777219.75, 1e-12], dtype=np.float64)
        result = fn(source)
        self.assertEqual(result.dtype, np.dtype('float64'))
        np.testing.assert_array_equal(result, np.abs(source))
        data = np.full(200, 16777217.25, dtype=np.float64)
        data[0] = 16777219.75
        self.assertEqual(self.ns['_rt_detector_relative_noise_reference'](data, 0)[0], 16777217.25)

    def test_raw_plot_snapshot_keeps_native_values_while_graph_is_scaled(self):
        tree = ast.parse((CANDIDATE/'engine.py').read_text(encoding='utf8'))
        for ch in (0, 1):
            node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Attribute) and t.attr == f'_rt_prod_raw{ch}' for t in n.targets))
            self.assertEqual(next(k.value.attr for k in node.value.keywords if k.arg == 'dtype'), 'float64')
        s = SimpleNamespace(_rt_prod_raw0=np.zeros(4, dtype=np.float64), max_samples=4,
                            _service_mode=True, xcorr_norm_enabled=False,
                            _rt_product_display_scale=lambda: 16384.250003814755)
        raw = np.array([1., 16777217.25, 1073741824., 0.])
        self.ns['_rt_update_product_plot'](s, 0, raw)
        np.testing.assert_array_equal(s._rt_prod_raw0, raw)
        self.assertEqual(s._rt_prod_raw_len0, 4)
        self.assertEqual(float(s._xcorr_saved_prod0.max()), 65535.)
        self.assertGreater(s._xcorr_saved_prod0[0], 0)
        np.testing.assert_array_equal(raw, [1., 16777217.25, 1073741824., 0.])

    def test_zero_reference_rejected_but_small_positive_reference_is_not_rounded_to_zero(self):
        fn = self.ns['_rt_detector_quarter_noise_supported']
        for noise in [0., 1e-8, .1, 1.]:
            data = np.full(200, noise); data[0] = 100.
            with patch.dict(os.environ, BMI30_RT_NOISE_AREA_PERCENT='66'):
                passed, ref, part, _ = fn(data, 0, 100., 3.)
            self.assertEqual(passed, noise > 0, noise)
            self.assertEqual(ref, noise)
            self.assertGreaterEqual(part, 0)

    def fixture(self):
        from test_base_width import EngineTests
        EngineTests.setUpClass()
        test = EngineTests(methodName='test_disabled_allows_wide')
        test.setUp()
        return test

    def test_parent_rejects_zero_noise_even_when_history_and_worker_would_allow(self):
        for ch in (0, 1):
            test = self.fixture()
            test.scope._det_accumulated_reference_noise = lambda *a: 50.
            test.ns['_rt_detector_reference_quarter_noise'] = lambda *a: (0., 0, (0.,)*6)
            for _ in range(5): test.send(24, ch, noise_ok_in=True, guard_checked_in=True)
            self.assertFalse(test.fired)
            self.assertFalse(getattr(test.scope, f'_det_noise_valid{ch}'))
            self.assertFalse(getattr(test.scope, f'_det_last_noise_ok{ch}'))
            self.assertEqual(getattr(test.scope, f'_det_exceed{ch}'), 0)

    def test_zero_noise_breaks_chain_and_positive_noise_can_confirm_again(self):
        test = self.fixture()
        test.send(24)
        self.assertEqual(test.scope._det_exceed0, 1)
        test.ns['_rt_detector_reference_quarter_noise'] = lambda *a: (0., 0, (0.,)*6)
        test.send(24)
        self.assertEqual(test.scope._det_exceed0, 0)
        self.assertFalse(test.fired)
        test.ns['_rt_detector_reference_quarter_noise'] = lambda *a: (1., 0, (1.,)*6)
        test.send(24)
        self.assertFalse(test.fired)
        test.send(24)
        self.assertEqual(test.fired, [(True, False)])

    def test_missing_or_invalid_reference_never_uses_signal_as_its_own_threshold(self):
        for reference, part in [(0., 0), (float('nan'), 0), (float('inf'), 0), (1., -1)]:
            test = self.fixture()
            test.scope._det_accumulated_reference_noise = lambda *a: 0.
            test.ns['_rt_detector_reference_quarter_noise'] = lambda *a: (reference, part, (reference,)*6)
            for _ in range(3): test.send(24)
            self.assertFalse(test.fired)
            self.assertFalse(test.scope._det_noise_valid0)

    def test_late_worker_zero_reference_cannot_override_positive_parent_reference(self):
        test = self.fixture()
        test.ns['_rt_detector_current_noise_supported'] = lambda *a, **k: (True, 0., 0, (0.,)*6)
        for _ in range(3): test.send(24)
        self.assertFalse(test.fired)
        self.assertFalse(test.scope._det_noise_valid0)


if __name__ == '__main__':
    unittest.main()
