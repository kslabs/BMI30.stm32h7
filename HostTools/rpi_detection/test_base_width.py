import ast
from collections import deque
import importlib.util
import os
from pathlib import Path
import sys
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

if os.getenv('BASE_WIDTH_TEST_COMMON'):
    common_path = Path(os.environ['BASE_WIDTH_TEST_COMMON']).resolve()
    spec = importlib.util.spec_from_file_location('_base_width_test_actual_common', common_path)
    actual_common = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = actual_common
    spec.loader.exec_module(actual_common)
    base_width_decision = actual_common.base_width_decision
    base_width_enabled = actual_common.base_width_enabled
    base_width_limit = actual_common.base_width_limit
    measure_base_width = actual_common.measure_base_width
else:
    from bmi30_base_width import base_width_decision, base_width_enabled, base_width_limit, measure_base_width


def pulse(width, floor=0, center=80):
    x = np.arange(200)
    left = center-width//2
    right = left+width
    # Linear flanks make the exact sampled base unambiguous.
    signal = np.where(x <= center, (x-left)/(center-left), (right-x)/(right-center))
    if floor:
        signal = np.abs(signal)  # Valleys above zero with rising neighbours.
    else:
        signal = np.maximum(0, signal)
    return signal*1000+floor


class GeometryTests(unittest.TestCase):
    def test_exact_limits(self):
        for width in (10, 23, 24, 25, 99, 100, 101):
            result = base_width_decision(pulse(width), 80, 24, True, noise_level=1.)
            self.assertEqual(result['width'], width)
            self.assertEqual(result['ok'], width <= 24)

    def test_above_zero_valleys(self):
        result = base_width_decision(pulse(24, floor=123), 80, 24, True, noise_level=124.)
        self.assertEqual((result['left'], result['right'], result['width']), (68, 92, 24))
        self.assertEqual((result['left_kind'], result['right_kind']), ('minimum', 'minimum'))
        self.assertTrue(result['ok'])

    def test_mixed_zero_and_minimum(self):
        result = measure_base_width([0, 2, 5, 9, 4, 1, 3, 8], 3, noise_level=2.)
        self.assertEqual((result['left'], result['right'], result['width']), (0, 5, 5))

    def test_plateaus(self):
        result = measure_base_width([5, 1, 1, 3, 8, 8, 4, 2, 2, 5], 4, noise_level=3.)
        self.assertEqual((result['left'], result['right']), (2, 7))
        self.assertEqual(measure_base_width([0, 1, 3, 3, 1, 0], 3, noise_level=1.)['width'], 5)

    def test_no_wrapping_or_invented_edges(self):
        for arr, peak in [([1, 4, 9, 4, 1], 2), ([9, 3, 0], 0), ([0, 3, 9], 2), ([2]*7, 3)]:
            self.assertFalse(base_width_decision(arr, peak, 24, True, noise_level=2.)['ok'])

    def test_shift_padding_is_not_a_zero(self):
        self.assertTrue(measure_base_width([0, 1, 5, 2, 0, 0], 2, noise_level=1.)['valid'])
        self.assertFalse(measure_base_width([0, 1, 5, 2, 0, 0], 2, 2, noise_level=1.)['valid'])
        self.assertFalse(measure_base_width([0, 0, 2, 5, 1, 0], 3, -2, noise_level=1.)['valid'])

    def test_invalid_data_and_disabled_compatibility(self):
        for arr, peak in [(None, 0), ([], 0), ([0, 0, 0], 1), ([0, float('nan'), 5, 0], 2), ([0, 5, float('inf'), 0], 1), ([0, 5, 0], 5)]:
            self.assertFalse(base_width_decision(arr, peak, 24, True, noise_level=1.)['ok'])
            self.assertTrue(base_width_decision(arr, peak, 24, False, noise_level=1.)['ok'])

    def test_amplitude_does_not_change_width(self):
        for scale in (0.001, 1, 1e6):
            self.assertEqual(measure_base_width(pulse(24)*scale, 80, noise_level=scale)['width'], 24)

    @unittest.skipUnless(os.getenv('BASE_WIDTH_TEST_COMMON'), 'Integrated helper selected only with BASE_WIDTH_TEST_COMMON')
    def test_actual_integrated_helper_is_selected(self):
        self.assertIs(base_width_decision.__globals__, actual_common.__dict__)
        self.assertEqual(Path(base_width_decision.__code__.co_filename).resolve(), common_path)

    def test_first_valley_above_or_equal_noise_is_skipped(self):
        product = [0, 1, 2, 7, 6, 8, 20, 8, 6, 7, 2, 1, 0]
        for noise in (3., 6.):
            result = base_width_decision(product, 6, 10, True, noise_level=noise, noise_source='test')
            self.assertEqual((result['left'], result['right'], result['width']), (0, 12, 12))
            self.assertEqual(result['reason'], 'too_wide')
            self.assertFalse(result['ok'])
            self.assertEqual(result['noise_level'], noise)
            self.assertEqual(result['noise_source'], 'test')
        self.assertEqual(measure_base_width(product, 6, noise_level=6.01)['width'], 4)

    def test_rejected_valley_ascent_waits_for_a_new_descent(self):
        product = [0, 10, 20, 8, 6, 7, 8, 9, 5, 2, 3, 0]
        result = measure_base_width(product, 2, noise_level=3.)
        self.assertEqual((result['left'], result['right']), (0, 9))
        self.assertEqual(result['right_kind'], 'minimum')

    def test_below_noise_is_not_a_threshold_crossing_width(self):
        result = measure_base_width([0, 1, 2, 3, 4, 20, 4, 3, 2, 1, 0], 5, noise_level=3.5)
        self.assertEqual((result['left'], result['right'], result['width']), (0, 10, 10))

    def test_skipped_flat_minimum_and_accepted_flat_minimum_keep_inner_edges(self):
        product = [0, 1, 2, 7, 6, 6, 8, 20, 8, 6, 6, 7, 2, 1, 0]
        self.assertEqual(measure_base_width(product, 7, noise_level=3.)['width'], 14)
        result = measure_base_width(product, 7, noise_level=7.)
        self.assertEqual((result['left'], result['right']), (5, 9))

    def test_mirror_symmetry_with_different_qualified_bases(self):
        product = [0, 1, 2, 7, 6, 8, 20, 8, 2, 4, 5, 0]
        direct = measure_base_width(product, 6, noise_level=3.)
        mirrored = measure_base_width(product[::-1], len(product)-7, noise_level=3.)
        self.assertEqual(direct['width'], mirrored['width'])
        self.assertEqual(mirrored['left'], len(product)-1-direct['right'])
        self.assertEqual(mirrored['right'], len(product)-1-direct['left'])

    def test_missing_nonpositive_or_nonfinite_noise_fails_closed_only_when_enabled(self):
        for value in (None, 0, -1, float('nan'), float('inf'), -float('inf'), '', 'bad'):
            for enabled in (True, False):
                result = base_width_decision(pulse(24), 80, 24, enabled, noise_level=value, noise_source='unknown')
                self.assertFalse(result['valid'])
                self.assertEqual(result['reason'], 'invalid_noise')
                self.assertEqual(result['ok'], not enabled)
                self.assertEqual(result['noise_source'], 'unknown')
                if isinstance(value, float) and not np.isfinite(value):
                    self.assertIsNone(result['noise_level'])

    def test_no_qualified_base_before_positive_edge(self):
        result = base_width_decision([10, 8, 6, 8, 20, 8, 6, 8, 10], 4, 100, True, noise_level=3.)
        self.assertFalse(result['valid'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'open_edge')

    def test_initial_ascent_is_not_accepted_as_a_peak(self):
        result = measure_base_width([0, 10, 8, 4, 1, 0], 2, noise_level=100.)
        self.assertFalse(result['valid'])
        self.assertEqual(result['left_kind'], 'not_peak')

    def test_range_and_bool(self):
        for value, expected in [(0,10), (10,10), (24,24), (100,100), (101,100), ('bad',24), (float('nan'),24)]:
            self.assertEqual(base_width_limit(value), expected)
        self.assertFalse(base_width_enabled('false'))
        self.assertTrue(base_width_enabled('on'))


def engine_functions():
    path = Path(os.environ['BASE_WIDTH_TEST_ENGINE'])
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'_rt_clamp_detector_level', '_rt_detector_confirmation_step', '_rt_detector_confirmation_diversity',
             '_rt_detector_confirmation_growth', '_det_confirmation_change_mode',
             '_det_note_confirmation_step', '_det_break_confirmation_chains', '_rt_burst_rejection_reason',
             '_rt_detector_optional_impulse_guard',
             '_rt_reference_noise_statistics', '_det_accumulated_reference_noise',
             '_rt_detector_peak_position_supported', '_rt_detector_peak_index_bound',
             '_update_realtime_prevbuf_detection', '_set_detector_channel_settings'}
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and
             (node.name in names or node.name.startswith('_det_clamp') or node.name == '_det_parse_bool')]
    namespace = dict(np=np, time=time, os=os, deque=deque, base_width_decision=base_width_decision,
                     base_width_limit=base_width_limit, base_width_enabled=base_width_enabled,
                     _RT_DETECT_LEVEL_MAX=2147483647)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


@unittest.skipUnless(os.getenv('BASE_WIDTH_TEST_ENGINE'), 'Set BASE_WIDTH_TEST_ENGINE to the patched engine')
class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = engine_functions()
        class History:
            def note(self, *args): pass
        cls.ns.update(
            _rt_detector_named_filters_active=lambda: False,
            _rt_detector_reference_quarter_noise=lambda *a: (1., 0, (1.,)*6),
            _rt_detector_current_noise_supported=lambda *a, **k: (True, 1., 0, (1.,)*6),
            common_mode_combined_decision=lambda *a, **k: (True, {'enabled': False, 'blocked': False}),
            DetectionReviewHistory=History,
        )

    def setUp(self):
        self.fired = []
        self.scope = s = SimpleNamespace(_det_enabled=True, _det_base_width_enabled=True,
                                         _det_base_width_max=24, _det_confirm_phase_gate=3,
                                         _beep_fire_count=0, _det_loss_s=0)
        for name, func in self.ns.items():
            if name in ('_update_realtime_prevbuf_detection', '_set_detector_channel_settings'):
                setattr(s, name, MethodType(func, s))
        s._rt_detection_active = lambda: True
        s._det_channel_ratio = lambda ch: 2.
        s._det_accumulated_reference_noise = lambda *a: 1.
        s._det_accumulated_signal_noise_ratio = lambda *a: None
        s._det_channel_trigger_level = lambda *a: 2
        s._det_channel_burst_gate_enabled = lambda *a: False
        s._det_channel_confirm_count = lambda *a: 2
        s._adc_enable_flags = lambda: (True, True)
        s._rt_detector_level_scale = lambda: 1.
        s._mark_web_detection_fire = lambda a,b: self.fired.append((a,b))
        for name in ('_clear_web_pre_fire', '_mark_web_pre_fire', '_player_record_note_detection',
                     '_rt_apply_detection_outputs', '_rt_stop_detection_outputs', '_capture_trigger'):
            setattr(s, name, lambda *a, **k: None)
        def note(ch, previous_count, previous_peak, count, peak, exceed, contiguous, **kw):
            old = getattr(s, f'_det_confirm_peaks{ch}', ())
            setattr(s, f'_det_confirm_peaks{ch}', (old+(peak,)) if count > 1 else ((peak,) if count else ()))
        s._det_note_confirmation_step = note

    def send(self, width, ch=0, **kw):
        self.scope._update_realtime_prevbuf_detection(ch, 100, peak_idx=80, peak_value=1000,
                                                     prod_arr=pulse(width), **kw)

    def raised_valleys(self):
        product = np.zeros(200)
        product[70:91] = [0, 1, 2, 3, 4, 5, 6, 7, 6, 8, 20, 8, 6, 7, 6, 5, 4, 3, 2, 1, 0]
        return product

    def send_product(self, product, ch=0):
        self.scope._update_realtime_prevbuf_detection(ch, int(np.max(product)), peak_idx=80,
            peak_value=float(np.max(product)), prod_arr=product)

    def test_noise_average_return_is_fresh_and_channel_specific(self):
        self.scope._det_base_width_max = 10
        self.scope._det_noise_avg0 = self.scope._det_noise_avg1 = 99999.
        self.scope._det_accumulated_reference_noise = lambda ch, *a: (3., 7.)[ch]
        for ch in (0, 1):
            self.send_product(self.raised_valleys(), ch)
            info = getattr(self.scope, f'_det_last_base_width{ch}')
            self.assertEqual(info['noise_level'], (3., 7.)[ch])
            self.assertEqual(info['noise_source'], 'accumulated_reference')
            self.assertEqual(info['width'], (20, 4)[ch])
            self.assertEqual(getattr(self.scope, f'_det_exceed{ch}'), ch)

    def test_higher_than_noise_minimum_breaks_chain_on_both_channels(self):
        self.scope._det_base_width_max = 10
        self.scope._det_accumulated_reference_noise = lambda *a: 3.
        for ch in (0, 1):
            self.send(10, ch)
            self.assertEqual(getattr(self.scope, f'_det_exceed{ch}'), 1)
            self.send_product(self.raised_valleys(), ch)
            self.assertEqual(getattr(self.scope, f'_det_exceed{ch}'), 0)
            self.assertEqual(getattr(self.scope, f'_det_confirm_peaks{ch}'), ())
            self.assertEqual(getattr(self.scope, f'_det_last_base_width{ch}')['reason'], 'too_wide')

    def test_invalid_noise_breaks_existing_confirmation_on_both_channels(self):
        for invalid in (0., -1., float('nan'), float('inf')):
            for ch in (0, 1):
                self.scope._det_accumulated_reference_noise = lambda *a: 1.
                self.send(24, ch)
                self.assertEqual(getattr(self.scope, f'_det_exceed{ch}'), 1)
                self.scope._det_accumulated_reference_noise = lambda *a: invalid
                self.send(24, ch)
                self.assertEqual(getattr(self.scope, f'_det_exceed{ch}'), 0)
                self.assertEqual(getattr(self.scope, f'_det_last_base_width{ch}')['reason'], 'invalid_noise')

    def test_actual_production_noise_estimator_feeds_the_base_gate(self):
        s = self.scope
        s._det_accumulated_reference_noise = MethodType(self.ns['_det_accumulated_reference_noise'], s)
        s._det_noise_window_seconds = lambda: 10.
        for ch in (0, 1):
            refs = iter((1., 2., 10.))
            def reference(*a):
                value = next(refs)
                return value, 0, (value,)*6
            with patch.dict(self.ns, {'_rt_detector_reference_quarter_noise': reference}):
                for _ in range(3):
                    self.send(24, ch)
            expected = max(float(np.mean([1., 2., 10.])), float(np.percentile([1., 2., 10.], 95.)))
            self.assertAlmostEqual(getattr(s, f'_det_last_base_width{ch}')['noise_level'], expected)
            self.assertAlmostEqual(getattr(s, f'_det_noise_avg{ch}'), expected)

    def test_all_engine_callers_supply_noise_context_and_live_uses_accumulated_value(self):
        tree = ast.parse(Path(os.environ['BASE_WIDTH_TEST_ENGINE']).read_text(encoding='utf-8'))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == 'base_width_decision']
        self.assertTrue(calls)
        for call in calls:
            keywords = {item.arg: item.value for item in call.keywords}
            self.assertIn('noise_level', keywords)
            self.assertIn('noise_source', keywords)
        live = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                    and n.name == '_update_realtime_prevbuf_detection')
        width_call = next(n for n in ast.walk(live) if isinstance(n, ast.Call)
                          and isinstance(n.func, ast.Name) and n.func.id == 'base_width_decision')
        accumulated_call = next(n for n in ast.walk(live) if isinstance(n, ast.Call)
                                and isinstance(n.func, ast.Attribute) and n.func.attr == '_det_accumulated_reference_noise')
        self.assertLess(accumulated_call.lineno, width_call.lineno)
        self.assertEqual(next(k.value.id for k in width_call.keywords if k.arg == 'noise_level'), 'noise_avg')

    def test_wide_frame_breaks_chain_on_both_channels(self):
        for ch in (0, 1):
            self.send(24, ch)
            self.assertEqual(getattr(self.scope, f'_det_exceed{ch}'), 1)
            self.send(25, ch, noise_ok_in=True, shape_ok_in=True, guard_checked_in=True)
            self.assertEqual(getattr(self.scope, f'_det_exceed{ch}'), 0)
            self.assertEqual(getattr(self.scope, f'_det_confirm_peaks{ch}'), ())
            self.assertEqual(getattr(self.scope, f'_det_last_base_width{ch}')['reason'], 'too_wide')
            self.send(24, ch)
            self.assertFalse(any(item[ch] for item in self.fired))
            self.send(24, ch)
            self.assertTrue(any(item[ch] for item in self.fired))

    def test_disabled_allows_wide(self):
        self.scope._det_base_width_enabled = False
        self.send(25); self.send(25)
        self.assertEqual(self.fired, [(True, False)])

    def test_burst_gate_without_named_filters_and_valid_tag_after_blank(self):
        s = self.scope
        s._det_channel_burst_gate_enabled = lambda *a: True
        s._det_channel_burst_max_ratio = lambda *a: 8
        s._det_channel_burst_blank_s = lambda *a: .08
        s._det_channel_burst_gate_active = lambda ch, now: getattr(s, f'_det_burst_gate_until{ch}', 0) > now
        self.send(25)
        self.assertEqual(s._det_burst_gate_reason0, 'base_width')
        self.assertEqual(s._det_burst_gate_count0, 1)
        self.assertGreater(s._det_burst_gate_until0, time.time())
        self.assertLessEqual(s._det_burst_gate_until0-time.time(), .081)
        self.send(24)
        self.assertFalse(self.fired)
        self.assertEqual(s._det_exceed0, 0)
        s._det_burst_gate_until0 = time.time()-1
        self.send(24)
        self.send(24)
        self.assertEqual(self.fired, [(True, False)])
        self.assertEqual(s._det_burst_gate_count0, 1)

    def test_required_impulse_guard_resets_confirmation_and_fails_closed(self):
        self.scope._det_impulse_guard_enabled = True
        self.send(24, dual_guard_required_in=True, source_seq_in=10)
        self.assertFalse(self.fired)
        self.assertEqual(self.scope._det_exceed0, 0)
        self.assertEqual(self.scope._dual_rejected0, 1)
        def send_guard(seq, allowed, evidence_seq=None):
            self.send(24, dual_guard_required_in=True, source_seq_in=seq,
                common_mode_meta_in={'timing': {'impulse_guard': {
                    'allowed': allowed, 'source_seq': seq if evidence_seq is None else evidence_seq,
                    'reason': 'distributed_support' if allowed else 'single_packet_dominance'}}})
        send_guard(11, True)
        self.assertEqual(self.scope._det_exceed0, 1)
        send_guard(12, False)
        self.assertEqual(self.scope._det_exceed0, 0)
        send_guard(13, True, evidence_seq=12)
        self.assertEqual(self.scope._det_exceed0, 0)
        send_guard(14, True)
        send_guard(15, True)
        self.assertEqual(self.fired, [(True, False)])

    def test_missing_product_is_rejected_even_with_worker_accept(self):
        self.scope._update_realtime_prevbuf_detection(0, 100, peak_idx=80, prod_arr=None,
                                                     noise_ok_in=True, shape_ok_in=True, guard_checked_in=True)
        self.assertEqual(self.scope._det_exceed0, 0)
        self.assertFalse(self.scope._det_last_base_width0['ok'])

    def test_settings_change_resets_chain_and_fences_worker(self):
        resets = []
        self.ns['common_mode_settings'] = lambda settings: settings
        self.scope._rt_detector_worker_reset_for_stream = lambda reason: resets.append(reason)
        self.scope._detector_settings_snapshot = lambda: dict(base_width_max=self.scope._det_base_width_max,
                                                              base_width_enabled=self.scope._det_base_width_enabled)
        self.send(24)
        snapshot = self.scope._set_detector_channel_settings({'base_width_max': 10}, persist=False)
        self.assertEqual(snapshot['base_width_max'], 10)
        self.assertEqual(self.scope._det_exceed0, 0)
        self.assertEqual(self.scope._det_confirm_peaks0, ())
        self.assertEqual(resets, ['detector_gate_settings_changed'])
        self.scope._set_detector_channel_settings({'base_width_max': 10}, persist=False)
        self.assertEqual(len(resets), 1)
        self.scope._set_detector_channel_settings({'base_width_max': 999, 'base_width_enabled': 'false'}, persist=False)
        self.assertEqual(self.scope._det_base_width_max, 100)
        self.assertFalse(self.scope._det_base_width_enabled)


@unittest.skipUnless(os.getenv('BASE_WIDTH_TEST_CORE'), 'Set BASE_WIDTH_TEST_CORE to the patched core')
class FrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(os.environ['BASE_WIDTH_TEST_CORE'])
        tree = ast.parse(path.read_text(encoding='utf-8'))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_review_frame_metadata')
        node.decorator_list = []
        ns = dict(np=np, base_width_decision=base_width_decision, _product_display_scale=lambda: 10.)
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'), ns)
        cls.metadata = staticmethod(ns['_review_frame_metadata'])
        cls.ns = ns

    def test_live_uses_unclipped_samples_in_both_scales(self):
        raw = pulse(24)*1e6
        s = SimpleNamespace(_det_base_width_max=24, _det_base_width_enabled=True,
                            _rt_prod_raw_len0=200, _rt_prod_raw0=raw, _phase_shift_adc0=0,
                            _det_noise_avg0=1e6)
        for normalized in (False, True):
            s.xcorr_norm_enabled = normalized
            s._xcorr_saved_prod0 = raw if normalized else np.clip(raw/10, 0, 65535)
            result = self.metadata({},200,s)['prod_peak0']
            self.assertEqual(result['sample'],80)
            self.assertEqual(result['base_width']['width'],24)
            self.assertTrue(result['base_width']['ok'])

    def test_saved_event_uses_its_original_decision(self):
        snapshot = dict(peak_index=80,base_width=base_width_decision(pulse(24),80,24,True,
            noise_level=1., noise_source='accumulated_reference'))
        sources = dict(anchor={'snapshot':snapshot},anchor_channel=0,prod0=pulse(24))
        s = SimpleNamespace(_det_base_width_max=10, _det_base_width_enabled=True)
        result = self.metadata(sources,200,s)['prod_peak0']
        self.assertEqual(result['base_width'],snapshot['base_width'])

    def raised_valleys(self):
        product = np.zeros(200)
        product[70:91] = [0, 1, 2, 3, 4, 5, 6, 7, 6, 8, 20, 8, 6, 7, 6, 5, 4, 3, 2, 1, 0]
        return product

    def test_live_both_channels_use_own_noise_in_raw_and_display_fallback(self):
        product = self.raised_valleys()
        for raw in (True, False):
            s = SimpleNamespace(_det_base_width_max=10, _det_base_width_enabled=True,
                _det_noise_avg0=3., _det_noise_avg1=7., xcorr_norm_enabled=False)
            if raw:
                sources = {'prod0': product, 'prod1': product}
            else:
                sources = {}
                s._xcorr_saved_prod0 = product/10
                s._xcorr_saved_prod1 = product/10
            result = self.metadata(sources, 200, s)
            for ch in (0, 1):
                peak = result[f'prod_peak{ch}'];base = peak['base_width']
                self.assertEqual(peak['raw_amplitude'], 20.)
                self.assertEqual(base['width'], (20, 4)[ch])
                self.assertEqual(base['ok'], bool(ch))
                self.assertEqual(base['noise_source'], 'accumulated_reference')
                self.assertEqual(base['noise_level'], (3., 7.)[ch]/(1 if raw else 10))

    def test_live_missing_noise_is_explicitly_invalid(self):
        s = SimpleNamespace(_det_base_width_max=24, _det_base_width_enabled=True)
        base = self.metadata({'prod0': pulse(24)}, 200, s)['prod_peak0']['base_width']
        self.assertFalse(base['valid'])
        self.assertFalse(base['ok'])
        self.assertEqual(base['reason'], 'invalid_noise')

    def test_frozen_peak_and_width_do_not_recompute_against_current_noise(self):
        product = self.raised_valleys()
        saved = base_width_decision(product, 80, 30, True, noise_level=3., noise_source='accumulated_reference')
        product[10] = 100  # A stronger out-of-domain peak must not replace stored evidence.
        snapshot = {'peak_index': 80, 'channel': 0, 'base_width': saved, 'noise_avg': 3.}
        sources = {'anchor': {'snapshot': snapshot}, 'anchor_channel': 0, 'prod0': product}
        s = SimpleNamespace(_det_base_width_max=10, _det_base_width_enabled=True, _det_noise_avg0=99999.)
        with patch.dict(self.ns, {'base_width_decision': Mock(side_effect=AssertionError('frozen decision recomputed'))}):
            info = self.metadata(sources, 200, s)['prod_peak0']
        self.assertEqual(info['sample'], 80)
        self.assertEqual(info['base_width'], saved)

    def test_confirmation_uses_frozen_per_frame_noise_not_current_scope(self):
        product = self.raised_valleys()
        frames = [{'decision': {'channel': 0, 'peak_index': 80, 'noise_avg': noise},
                   'product0': product, 'product1': product, 'shifts': (0, 0),
                   'noise_levels': (99999., noise), 'synchronized': True} for noise in (3., 7.)]
        sources = {'anchor_channel': 0, 'prod0': product, 'prod1': product,
            'confirmation_products0': (product, product), 'confirmation_products1': (product, product),
            'anchor': {'snapshot': {'channel': 0, 'peak_index': 80,
                                    'base_width': {'limit': 10, 'enabled': True}},
                       'confirmation_frames': frames}}
        s = SimpleNamespace(_det_base_width_max=100, _det_base_width_enabled=False,
                            _det_noise_avg0=99999., _det_noise_avg1=99999.)
        info = self.metadata(sources, 200, s)
        for ch in (0, 1):
            widths = [row['base_width'] for row in info[f'confirmation_peak_values{ch}']]
            self.assertEqual([row['noise_level'] for row in widths], [3., 7.])
            self.assertEqual([row['width'] for row in widths], [20, 4])
            self.assertEqual([row['ok'] for row in widths], [False, True])
            self.assertTrue(all(row['limit'] == 10 and row['enabled'] for row in widths))

    def test_missing_peer_confirmation_does_not_shift_reference_noise_alignment(self):
        product = self.raised_valleys()
        frames = [
            {'decision': {'channel': 0, 'peak_index': 80, 'noise_avg': 3.}, 'product0': product,
             'noise_levels': (3., 1.), 'shifts': (0, 0), 'synchronized': False},
            {'decision': {'channel': 0, 'peak_index': 80, 'noise_avg': 7.},
             'product0': product, 'product1': product, 'noise_levels': (7., 7.),
             'shifts': (0, 0), 'synchronized': True},
        ]
        sources = {'anchor_channel': 0, 'confirmation_products0': (product, product),
            'confirmation_products1': (product,), 'anchor': {'confirmation_frames': frames,
                'snapshot': {'channel': 0, 'base_width': {'limit': 10, 'enabled': True}}}}
        s = SimpleNamespace(_det_noise_avg0=99999., _det_noise_avg1=99999.)
        info = self.metadata(sources, 200, s)
        self.assertEqual(len(info['confirmation_peak_values0']), 2)
        self.assertEqual(len(info['confirmation_peak_values1']), 1)
        reference = info['confirmation_peak_values1'][0]['base_width']
        self.assertEqual(reference['noise_level'], 7.)
        self.assertEqual(reference['width'], 4)
        self.assertTrue(reference['ok'])

    def test_confirmation_missing_frozen_noise_never_uses_live_noise(self):
        product = self.raised_valleys()
        frames = [{'decision': {'channel': 0, 'peak_index': 80}, 'product0': product,
                   'product1': product, 'shifts': (0, 0)}]
        sources = {'anchor_channel': 0, 'confirmation_products0': (product,), 'confirmation_products1': (product,),
            'anchor': {'confirmation_frames': frames, 'snapshot': {'base_width': {'limit': 30, 'enabled': True}}}}
        info = self.metadata(sources, 200, SimpleNamespace(_det_noise_avg0=99999., _det_noise_avg1=99999.))
        for ch in (0, 1):
            base = info[f'confirmation_peak_values{ch}'][0]['base_width']
            self.assertFalse(base['ok'])
            self.assertEqual(base['reason'], 'invalid_noise')
            self.assertIsNone(base['noise_level'])

    def test_confirmation_uses_original_shift(self):
        product = np.asarray([0., 1., 5., 2., 0., 0.])
        frames = [{'decision': {'channel': 0, 'peak_index': 2, 'noise_avg': 3., 'shift': 2},
                   'product0': product, 'shifts': (2, 0)}]
        sources = {'anchor_channel': 0, 'confirmation_products0': (product,),
            'anchor': {'confirmation_frames': frames, 'snapshot': {'base_width': {'limit': 24, 'enabled': True}}}}
        info = self.metadata(sources, 6, SimpleNamespace(_phase_shift_adc0=0, _det_noise_avg0=99999.))
        base = info['confirmation_peak_values0'][0]['base_width']
        self.assertFalse(base['valid'])
        self.assertEqual(base['right_kind'], 'open_edge')


if __name__ == '__main__':
    unittest.main()
