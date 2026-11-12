import ast
from functools import lru_cache
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np
import bmi30_phase_adaptation as policy


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.getenv('BMI30_TEST_TEMP') or None)
        self.path = Path(self.temp.name)/'config.json'
        self.path.write_text(json.dumps({'tx_phase_by_usb_serial': {'board': [1, 0]}, 'gain': 17}))
        self.env = patch.dict(os.environ, BMI30_CONFIG_JSON=str(self.path));self.env.start()
        self.scope = SimpleNamespace(_det_last_shift0=4, _det_last_shift1=-7,
                                     _rt_detector_worker_reset_for_stream=Mock())
        policy.initialize(self.scope)

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def test_disable_captures_both_phases_and_preserves_tx_settings(self):
        result = policy.set_enabled(self.scope, False)
        self.assertEqual(result['fixed_shifts'], [4, -7])
        self.scope._det_last_shift0=-10;self.scope._det_last_shift1=11
        self.assertEqual([policy.fixed_shift(self.scope,ch) for ch in (0,1)], [4,-7])
        self.assertEqual(json.loads(self.path.read_text())['tx_phase_by_usb_serial'], {'board':[1,0]})
        self.assertEqual(json.loads(self.path.read_text())['gain'],17)
        self.scope._rt_detector_worker_reset_for_stream.assert_called_once()

    def test_idempotent_disable_does_not_recapture_rx(self):
        policy.set_enabled(self.scope,False)
        self.scope._det_last_shift0=99
        self.assertEqual(policy.set_enabled(self.scope,False)['fixed_shifts'],[4,-7])
        self.assertEqual(self.scope._rt_detector_worker_reset_for_stream.call_count,1)

    def test_restart_restores_fixed_phase_without_new_search(self):
        policy.set_enabled(self.scope,False)
        scope=SimpleNamespace(_det_last_shift0=0,_det_last_shift1=0)
        policy.initialize(scope)
        self.assertFalse(policy.state(scope)['enabled'])
        self.assertEqual([policy.fixed_shift(scope,ch) for ch in (0,1)],[4,-7])

    def test_enable_releases_both_phases(self):
        policy.set_enabled(self.scope,False)
        self.assertTrue(policy.set_enabled(self.scope,True)['enabled'])
        self.assertIsNone(policy.fixed_shift(self.scope,0))
        self.assertIsNone(policy.fixed_shift(self.scope,1))

    def test_invalid_boolean_does_not_modify_policy_or_config(self):
        before=self.path.read_bytes()
        for value in (None,0,1,'false'):
            with self.assertRaises(ValueError):policy.set_enabled(self.scope,value)
        self.assertEqual(self.path.read_bytes(),before)
        self.scope._rt_detector_worker_reset_for_stream.assert_not_called()

    def test_worker_uses_request_snapshot(self):
        self.assertIsNone(policy.fixed_shift({'rx_phase_fixed_shifts':None},0))
        self.assertEqual(policy.fixed_shift({'rx_phase_fixed_shifts':[6,-3]},1),-3)


class ProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=Path(os.environ['BMI30_TEST_ENGINE']).read_text(encoding='utf8')
        tree=ast.parse(source)
        names={'_rt_detector_shift_indices','_rt_detector_product_current_prev','_rt_detector_product_display',
               '_rt_detector_level_from_peak','_rt_clamp_detector_level'}
        body=[node for node in tree.body if (isinstance(node,ast.FunctionDef) and node.name in names)
              or (isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='_RT_DETECT_LEVEL_MAX' for x in node.targets))]
        namespace={'np':np,'os':os,'lru_cache':lru_cache,
                   '_rt_detector_named_filters_active':lambda:False}
        exec(compile(ast.Module(body=body,type_ignores=[]),'<deployed detector functions>','exec'),namespace)
        cls.compute=staticmethod(namespace['_rt_detector_product_current_prev'])

    def test_fixed_phase_is_retained_when_received_alignment_changes(self):
        current=np.full(80,32768,dtype=np.uint16);previous=current.copy()
        current[25]=35000;previous[30]=35000
        self.assertEqual(self.compute(current,previous,12,'prod',1)[1],5)
        previous[30]=32768;previous[18]=35000
        self.assertEqual(self.compute(current,previous,12,'prod',1)[1],-7)
        fixed=self.compute(current,previous,12,'prod',1,fixed_shift=5)
        self.assertEqual(fixed[1],5)
        self.assertEqual(fixed[3],0)

    def test_fixed_negative_shift_and_short_frame_bounds(self):
        a=np.full(40,32768,dtype=np.uint16);b=a.copy();a[21]=35000;b[18]=35000
        result=self.compute(a,b,12,'prod',1,fixed_shift=-3)
        self.assertEqual(result[1],-3);self.assertGreater(result[3],0)
        self.assertEqual(self.compute(a,b,12,'prod',1,fixed_shift=100)[1],39)


if __name__=='__main__':unittest.main()
