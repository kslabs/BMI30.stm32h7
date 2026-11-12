"""Tests of the actual temporal estimator, reset boundaries, and parent detector gate."""
import __future__
import ast
from collections import deque
import os
from pathlib import Path
import time
from types import MethodType, SimpleNamespace
import unittest

import numpy as np

ROOT=Path(__file__).resolve().parents[2]
CANDIDATE=Path(os.getenv('NOISE_AREA_TEST_DIR',ROOT/'.tmp/noise-percentile-20261015/candidate'))


def functions():
    path=CANDIDATE/'engine.py'
    tree=ast.parse(path.read_text(encoding='utf8'))
    names={'_rt_reference_noise_statistics','_det_accumulated_reference_noise',
           '_det_reset_noise_accumulators','_det_channel_trigger_level','_det_clamp_noise_window',
           '_det_noise_window_seconds','_rt_clamp_detector_level'}
    nodes=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in names]
    ns=dict(np=np,time=time,deque=deque,_RT_DETECT_LEVEL_MAX=2147483647)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec',
                 flags=__future__.annotations.compiler_flag),ns)
    return ns


class PercentileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns=functions()

    def scope(self,window=5):
        s=SimpleNamespace(_det_noise_window_s=window)
        for name in ['_det_accumulated_reference_noise','_det_reset_noise_accumulators','_det_noise_window_seconds']:
            setattr(s,name,MethodType(self.ns[name],s))
        return s

    def test_linear_percentile_matches_known_ramp_and_small_windows(self):
        fn=self.ns['_rt_reference_noise_statistics']
        self.assertEqual(fn(range(101)),(50.,95.,95.,101))
        self.assertEqual(fn([0,100]),(50.,95.,95.,2))
        self.assertEqual(fn([42]),(42.,42.,42.,1))
        self.assertEqual(fn([]),(0.,0.,0.,0))

    def test_mean_floor_limits_sensitivity_to_single_rare_outlier(self):
        mean,p95,estimate,count=self.ns['_rt_reference_noise_statistics']([10]*124+[1000])
        self.assertAlmostEqual(mean,17.92)
        self.assertEqual(p95,10)
        self.assertEqual(estimate,mean)
        self.assertLess(estimate,1000)
        self.assertEqual(count,125)

    def test_filter_invalid_values_and_nonnegative_floor(self):
        self.assertEqual(self.ns['_rt_reference_noise_statistics']([float('nan'),float('inf'),-1]),(0.,0.,0.,0))
        mean,p95,estimate,n=self.ns['_rt_reference_noise_statistics']([0,10,float('nan'),-2])
        self.assertEqual((mean,p95,estimate,n),(5.,9.5,9.5,2))

    def test_both_channels_have_independent_history_and_diagnostics(self):
        s=self.scope()
        self.assertEqual(s._det_accumulated_reference_noise(0,10,now=100),10)
        self.assertEqual(s._det_accumulated_reference_noise(1,100,now=100),100)
        self.assertAlmostEqual(s._det_accumulated_reference_noise(0,30,now=101),29)
        self.assertEqual(s._det_noise_mean0,20)
        self.assertEqual(s._det_noise_p950,29)
        self.assertEqual(s._det_noise_samples0,2)
        self.assertEqual(s._det_noise_mean1,100)
        self.assertEqual(s._det_noise_samples1,1)

    def test_inclusive_window_edge_and_expiry_remove_old_peak(self):
        s=self.scope(5)
        s._det_accumulated_reference_noise(0,100,now=10)
        self.assertAlmostEqual(s._det_accumulated_reference_noise(0,0,now=15),95)
        self.assertEqual(s._det_accumulated_reference_noise(0,0,now=15.001),0)
        self.assertEqual(s._det_noise_samples0,2)

    def test_window_can_shorten_and_expand_without_cross_channel_mixing(self):
        s=self.scope(5)
        for t in range(5):s._det_accumulated_reference_noise(0,10*(t+1),now=t)
        s._det_noise_window_s=1
        self.assertAlmostEqual(s._det_accumulated_reference_noise(0,60,now=5),59.5)
        self.assertEqual(s._det_noise_samples0,2)
        s._det_noise_window_s=10
        self.assertAlmostEqual(s._det_accumulated_reference_noise(0,70,now=6),69)
        self.assertEqual(s._det_noise_samples0,3)

    def test_reset_clears_all_statistics_and_old_histories(self):
        s=self.scope()
        for ch in (0,1):
            s._det_accumulated_reference_noise(ch,100,now=10)
            setattr(s,f'_det_snr_hist{ch}',deque([(10,100,1)]))
        s._det_reset_noise_accumulators()
        for ch in (0,1):
            for kind in ['mean','p95','samples','avg','ref']:
                self.assertEqual(getattr(s,f'_det_noise_{kind}{ch}'),0)
            self.assertFalse(getattr(s,f'_det_noise_hist{ch}'))
            self.assertFalse(getattr(s,f'_det_snr_hist{ch}'))
        self.assertEqual(s._det_accumulated_reference_noise(0,1,now=100),1)

    def test_bad_current_values_do_not_produce_nonfinite_thresholds(self):
        for value in ['bad',float('nan'),float('inf'),-100]:
            s=self.scope()
            self.assertEqual(s._det_accumulated_reference_noise(0,value,now=1),0)
            self.assertEqual(s._det_noise_samples0,1)

    def test_recorded_noise_spatial_and_instant_gate_algorithms_are_unchanged(self):
        source_path=Path(os.getenv('NOISE_PERCENTILE_BASE_ENGINE',ROOT/'.tmp/noise-percentile-20261015/source/engine.py'))
        if not source_path.exists():self.skipTest('Baseline source not supplied')
        trees=[ast.parse(path.read_text(encoding='utf8')) for path in [source_path,CANDIDATE/'engine.py']]
        names=['_rt_detector_noise_area_mask','_rt_detector_relative_noise_reference',
               '_rt_detector_current_noise_supported',
               '_det_manual_curve_ratio','_det_channel_ratio','_det_channel_trigger_level']
        for name in names:
            nodes=[next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name) for tree in trees]
            self.assertEqual(ast.dump(nodes[0]),ast.dump(nodes[1]),name)

    def test_actual_parent_uses_percentile_to_reject_weak_candidate_then_accepts_clear_signal(self):
        from test_base_width import EngineTests
        fixture=EngineTests(methodName='test_disabled_allows_wide')
        EngineTests.setUpClass();fixture.setUp()
        s=fixture.scope
        for name in ['_det_accumulated_reference_noise','_det_noise_window_seconds','_det_channel_trigger_level']:
            setattr(s,name,MethodType(self.ns[name],s))
        s._det_channel_auto_enabled=lambda ch:False
        s._det_manual_curve_ratio=lambda ch,noise:2.
        s._det_noise_window_s=5
        now=time.time()
        s._det_noise_hist0=deque([(now-.5,1.)]*115+[(now-.5,80.)]*10)
        fixture.send(24);fixture.send(24)
        self.assertEqual(s._det_noise_avg0,80)
        self.assertGreater(s._det_trigger0,100)
        self.assertFalse(fixture.fired)
        s._det_noise_hist0=deque([(time.time()-.1,1.)]*100)
        fixture.send(24);fixture.send(24)
        self.assertEqual(s._det_noise_avg0,1)
        self.assertEqual(fixture.fired,[(True,False)])


if __name__=='__main__':unittest.main()
