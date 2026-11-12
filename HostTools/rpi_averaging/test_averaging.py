import ast
import json
import os
from pathlib import Path
from contextlib import contextmanager
import threading
import uuid
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import bmi30_averaging as avg


@contextmanager
def test_directory():
    base = Path(os.getenv('BMI30_TEST_TEMP') or Path(__file__).resolve().parents[2]/'.tmp/rpi-averaging-20261013/tests')
    base.mkdir(parents=True, exist_ok=True)
    folder = base/('case-'+uuid.uuid4().hex)
    folder.mkdir()
    try:
        yield folder
    finally:
        for file in folder.iterdir():file.unlink()
        folder.rmdir()


class RollingTests(unittest.TestCase):
    def test_one_is_exact_bypass_without_ring_allocation(self):
        ring = avg.RollingMean()
        data = np.array([0, 32768, 65535], dtype=np.uint16)
        result, ready = ring.push(0, 0, data, 1, 'stream')
        self.assertIs(result, data)
        self.assertTrue(ready)
        self.assertEqual(ring.state(1)['bytes'], 0)

    def test_all_depths_match_explicit_last_packets_and_keep_adc_phase_separate(self):
        for depth in range(2, 13):
            ring, expected = avg.RollingMean(), {}
            for cycle in range(28):
                for ch in (0, 1):
                    for phase in (0, 1):
                        data = np.array([ch*10000+phase*1000+cycle*7, 65535-cycle], dtype=np.uint16)
                        key = (ch, phase)
                        expected.setdefault(key, []).append(data)
                        result, ready = ring.push(ch, phase, data, depth, 'stream')
                        want = np.rint(np.mean(expected[key][-depth:], axis=0)).astype(np.int32)
                        np.testing.assert_array_equal(result, want)
                        self.assertEqual(ready, len(expected[key]) >= depth and len(expected.get((ch,1-phase),[])) >= depth)

    def test_full_window_before_detector_history_then_each_packet(self):
        tree = ast.parse(Path(os.environ['BMI30_TEST_ENGINE']).read_text(encoding='utf8'))
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == '_RtDetectorInputHistory')
        ns = {'np': np}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual detector history>', 'exec'), ns)
        ring, history, processed = avg.RollingMean(), ns['_RtDetectorInputHistory'](), []
        for seq in range(32):
            phase = seq & 1
            result, ready = ring.push(0, phase, np.full(200, 32000+phase*1000+seq), 12, 'stream', seq)
            if ready and history.feed(0, result, phase, (42,seq,0,0,200), ring.epoch):
                processed.append(seq)
        self.assertEqual(processed, list(range(24,32)))
        self.assertEqual(ring.state(12)['bytes'], 22400)

    def test_context_depth_and_length_changes_discard_history(self):
        ring = avg.RollingMean()
        for key, depth, length in [('first',8,200),('second',8,200),('second',2,200),('second',2,600)]:
            for i in range(20):
                ring.push(0, i&1, np.full(length,100), depth, key)
            epoch = ring.epoch
            result, ready = ring.push(0,0,np.full(length+1,200),depth,key)
            self.assertGreater(ring.epoch, epoch)
            self.assertFalse(ready)
            self.assertTrue(np.all(result == 200))

    def test_sequence_gap_restarts_fill_and_uint32_wrap_is_contiguous(self):
        ring = avg.RollingMean()
        for seq in [0xfffffffc,0xfffffffd,0xfffffffe,0xffffffff,0,1]:
            result, ready = ring.push(0,seq&1,np.full(5,100),2,'stream',seq)
        self.assertTrue(ready)
        result, ready = ring.push(0,1,np.full(5,300),2,'stream',5)
        self.assertFalse(ready)
        self.assertEqual(ring.state(2)['counts'], [0,1,0,0])
        self.assertTrue(np.all(result == 300))

    def test_invalid_depths_are_rejected(self):
        for value in (None,True,False,0,13,-1,2.5,'2.5',1.0):
            with self.assertRaises(ValueError): avg.normalize_depth(value)
        for value in range(1,13): self.assertEqual(avg.normalize_depth(str(value)),value)

    def test_independent_noise_reduces_by_sqrt_eight_and_short_pulse_is_diluted(self):
        rng, ring, outputs, noise = np.random.default_rng(17), avg.RollingMean(), [], []
        for i in range(2000):
            data = np.rint(32768 + rng.normal(0,120,200)).astype(np.int32)
            noise.append(data)
            result, ready = ring.push(0,i&1,data,8,'stream')
            if ready: outputs.append(result)
        gain = float(np.std(noise)/np.std(outputs))
        self.assertTrue(2.70 < gain < 2.95, gain)
        ring = avg.RollingMean()
        for i in range(16):ring.push(0,i&1,np.full(200,32768),8,'stream')
        result, ready = ring.push(0,0,np.full(200,33568),8,'stream')
        self.assertTrue(ready)
        self.assertTrue(np.all(result == 32868))


class SettingsTests(unittest.TestCase):
    def test_return_from_raw_mode_ignores_legacy_learned_sequence_step(self):
        # Raw/latest mode can learn a DMA sequence stride > 1. AVG_ROI headers
        # use consecutive stream_seq instead, even on the same USB connection.
        for depth in (2, 8, 12):
            scope = SimpleNamespace(rpi_avg_n=depth, stream_mode=0, avg_n=16,
                                    freq_hz=200, step_a=8, step_b=16,
                                    _rpi_averaging=avg.RollingMean())
            stream = object()
            raw = np.full(600, 32768)
            for seq in range(0, 160, 8):
                for ch in (0, 1):
                    result, ready = avg.apply_frame(scope, ch, (seq//8)&1,
                                                   raw, SimpleNamespace(seq=seq), stream)
                    self.assertIs(result, raw)
                    self.assertTrue(ready)
            scope.stream_mode = 2
            for seq in range(2*depth+6):
                for ch in (0, 1):
                    result, ready = avg.apply_frame(scope, ch, seq&1,
                        np.full(200, 32000+ch*100+seq), SimpleNamespace(seq=seq), stream)
                    if seq >= 2*depth-1:
                        self.assertTrue(ready, (depth, ch, seq, scope._rpi_averaging.state(depth)))
            self.assertEqual(scope._rpi_averaging.state(depth)['counts'], [depth]*4)
            epoch = scope._rpi_averaging.epoch
            _, ready = avg.apply_frame(scope, 0, 0, np.full(200, 32000),
                                       SimpleNamespace(seq=2*depth+8), stream)
            self.assertFalse(ready)
            self.assertGreater(scope._rpi_averaging.epoch, epoch)

    def test_load_persistence_change_and_reset_preserve_unrelated_settings(self):
        with test_directory() as temp:
            path = Path(temp)/'config.json'
            config = {'gain':17,'avg_n':24,'rx_phase_adaptation':{'enabled':False},'operation':{'rpi_avg_n':6}}
            path.write_text(json.dumps(config))
            with patch.dict(os.environ, BMI30_CONFIG_JSON=str(path)):
                scope = SimpleNamespace(data_lock=threading.RLock(), _rt_detector_worker_reset_for_stream=Mock())
                avg.initialize(scope)
                self.assertEqual(scope.rpi_avg_n,6)
                self.assertEqual(avg.set_depth(scope,8),8)
                scope._rt_detector_worker_reset_for_stream.assert_called_once()
                avg.set_depth(scope,8)
                self.assertEqual(scope._rt_detector_worker_reset_for_stream.call_count,1)
                self.assertEqual(json.loads(path.read_text()),config)

    def test_raw_mode_bypasses_saved_extra_average(self):
        scope = SimpleNamespace(rpi_avg_n=8,stream_mode=0,avg_n=24,freq_hz=200,_rpi_averaging=avg.RollingMean())
        data = np.full(600,32000)
        result, ready = avg.apply_frame(scope,0,0,data,SimpleNamespace(seq=0),object())
        self.assertIs(result,data);self.assertTrue(ready)

    def test_actual_portal_saves_both_values_and_loads_new_instance(self):
        tree = ast.parse(Path(os.environ['BMI30_TEST_PORTAL']).read_text(encoding='utf8'))
        names = {'save_default_avg_n','load_default_rpi_avg_n','_normalize_rpi_avg_n'}
        body = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        with test_directory() as temp:
            path = Path(temp)/'config.json'
            path.write_text(json.dumps({'avg_n':24,'gain':17,'operation':{'sound':True}}))
            ns = {'_normalize_avg_n':lambda value,default:int(value),'DEFAULT_AVG_N':24,
                  '_load_config_json':lambda:json.loads(path.read_text()),
                  '_save_config_json':lambda payload:path.write_text(json.dumps(payload)),
                  'time':__import__('time')}
            exec(compile(ast.Module(body=body,type_ignores=[]),'<actual portal settings>','exec'),ns)
            ns['save_default_avg_n'](16,12)
            cfg = json.loads(path.read_text())
            self.assertEqual(cfg['avg_n'],16);self.assertEqual(cfg['rpi_avg_n'],12)
            self.assertEqual(cfg['operation']['rpi_avg_n'],12)
            self.assertTrue(cfg['operation']['sound']);self.assertEqual(cfg['gain'],17)
            self.assertEqual(ns['load_default_rpi_avg_n'](),12)
            with patch.dict(os.environ,BMI30_CONFIG_JSON=str(path)):
                scope = SimpleNamespace();avg.initialize(scope)
                self.assertEqual(scope.rpi_avg_n,12)


if __name__ == '__main__':unittest.main()
