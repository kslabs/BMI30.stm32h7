"""Regress captured reference data through real detector and frame serializers."""
import __future__
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from test_base_width import EngineTests, pulse, base_width_decision
from bmi30_common_mode import review_window


class AutoFreezePairTests(unittest.TestCase):
    def fixture(self):
        EngineTests.setUpClass()
        test = EngineTests(methodName='test_disabled_allows_wide')
        test.setUp()
        frames = []
        test.scope._det_review_history = SimpleNamespace(note=lambda ch, frame, *args: frames.append(frame))
        return test, frames

    def test_independent_arrivals_keep_both_antennas_without_a_sync_claim(self):
        for ch in (0, 1):
            test, frames = self.fixture()
            block = tuple(np.full(200, 32000+i) for i in range(4))
            products = (pulse(24), pulse(22)*2)
            test.send(24, ch, candidate_current_in=block[ch*2], candidate_previous_in=block[ch*2+1],
                      candidate_frame_block_in=block, candidate_product_block_in=products,
                      common_mode_meta_in={'synchronized': False})
            frame = frames[-1]
            self.assertFalse(frame['synchronized'])
            np.testing.assert_array_equal(frame[f'product{ch}'], pulse(24))
            np.testing.assert_array_equal(frame[f'product{1-ch}'], products[1-ch])
            for i, key in enumerate(('data0_even','data0_odd','data1_even','data1_odd')):
                np.testing.assert_array_equal(frame[key], block[i])
            block[0][:] = 0
            products[1-ch][:] = 0
            self.assertGreater(frame['data0_even'][0], 0)
            self.assertGreater(frame[f'product{1-ch}'].max(), 0)

    def test_missing_peer_stays_missing(self):
        for block, products in [(None, None), ((None,)*4, (None,)*2),
                                ((np.ones(200),)*4, None)]:
            test, frames = self.fixture()
            test.send(24, candidate_frame_block_in=block, candidate_product_block_in=products)
            self.assertNotIn('product1', frames[-1])
            self.assertNotIn('data1_even', frames[-1])

    def test_confirmation_and_fire_decisions_do_not_depend_on_reference_capture(self):
        for ch in (0, 1):
            baseline, baseline_frames = self.fixture()
            captured, captured_frames = self.fixture()
            block = tuple(np.full(200, 32000+i) for i in range(4))
            for width in (24, 25, 24, 24):
                baseline.send(width, ch)
                captured.send(width, ch, candidate_frame_block_in=block,
                              candidate_product_block_in=(pulse(width), pulse(width)))
                self.assertEqual(baseline.fired, captured.fired)
                self.assertEqual(baseline_frames[-1]['decision'], captured_frames[-1]['decision'] | {
                    'timestamp': baseline_frames[-1]['decision']['timestamp']})
            self.assertEqual(captured.fired, [(ch == 0, ch == 1)])

    def test_confirmation_window_carries_reference_for_every_step(self):
        test, frames = self.fixture()
        block = tuple(np.full(200, 32000+i) for i in range(4))
        for _ in range(5):
            test.send(24, candidate_frame_block_in=block,
                      candidate_product_block_in=(pulse(24), pulse(22)))
        saved = review_window(frames[-1], frames)
        for ch in (0, 1):
            self.assertEqual(len(saved[f'confirmation_products{ch}']), 5)
            self.assertTrue(all(p is not None for p in saved[f'confirmation_products{ch}']))


class FrameSerializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path(os.environ['BASE_WIDTH_TEST_CORE']).read_text(encoding='utf8'))
        original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScopeApi')
        methods = {'frame','frame_binary','_try_data_lock','_frame_array_values','_frame_values',
                   '_frame_product_group_peak','_frame_norm_stats','_pack_frame_binary',
                   '_fire_product_source','_fire_frame_source','_fire_frame_sources','_review_frame_metadata'}
        original.body = [n for n in original.body if isinstance(n,ast.FunctionDef) and n.name in methods]
        namespace = dict(np=np, os=os, json=json, base_width_decision=base_width_decision,
                         _product_display_scale=lambda: 10., _product_default_scale=lambda: 10.,
                         _json_safe=lambda x: x)
        exec(compile(ast.Module(body=[original],type_ignores=[]), 'actual_core', 'exec',
                     flags=__future__.annotations.compiler_flag), namespace)
        cls.api_class = namespace['ScopeApi']

    def test_json_and_binary_both_event_channels_and_normalization(self):
        for ch in (0, 1):
            for normalized in (False, True):
                helper = AutoFreezePairTests()
                test, frames = helper.fixture()
                block = tuple(np.full(200, 32000+i) for i in range(4))
                for _ in range(5):
                    test.send(24, ch, candidate_current_in=block[ch*2], candidate_previous_in=block[ch*2+1],
                              candidate_frame_block_in=block, candidate_product_block_in=(pulse(24),pulse(22)))
                saved = review_window(frames[-1], frames)
                saved.update(fire_channel=ch, fire_seq=7, snapshot={'seq':7}, fire_timestamp=123.)
                scope = SimpleNamespace(base_buf_len=200,max_samples=200,last_frame_t=124.,
                    num_group=SimpleNamespace(checkedId=lambda:6),xcorr_norm_enabled=normalized)
                setattr(scope, f'_web_det_fire_frames{ch}', {7:saved})
                api = self.api_class.__new__(self.api_class);api.scope = scope
                kwargs = {f'fire_seq{ch}':7}
                result = api.frame(**kwargs)
                self.assertTrue(result['exact_event_frame'])
                for name in ('data0_even','data0_odd','data1_even','data1_odd','prod0','prod1'):
                    self.assertEqual(len(result[name]),200,name)
                for channel in (0,1):self.assertEqual(len(result[f'prod_confirm{channel}']),5)
                reference = result[f'reference_snapshot{1-ch}']
                self.assertTrue(reference['reference_only']);self.assertFalse(reference['fired'])
                self.assertFalse(reference['synchronized'])
                raw = api.frame_binary(channels='both',**kwargs)
                length = int.from_bytes(raw[4:8],'little')
                header = json.loads(raw[8:8+length]);offset = (8+length+1)&~1
                for name in header['arrays']:
                    values = np.frombuffer(raw,dtype='<u2',count=header['count'],offset=offset)
                    self.assertEqual(len(values),200)
                    if name in result:np.testing.assert_array_equal(values,result[name])
                    offset += header['count']*2
                self.assertEqual(offset,len(raw))
                self.assertIn('prod0',header['arrays']);self.assertIn('prod1',header['arrays'])


if __name__ == '__main__':
    unittest.main()
