"""Fresh ADC evidence, burst guard and latency regressions without live I/O.

Run with BMI30_DETECTOR_CANDIDATE pointing at the audited core/engine copies.
"""
import ast
from collections import deque
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np
from bmi30_dual_detector import DualWindowHistory

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = Path(os.environ.get('BMI30_DETECTOR_CANDIDATE', ROOT/'.tmp/fresh-pair-detector-20261016/candidate'))


def functions(path, names):
    tree = ast.parse(path.read_text(encoding='utf8'))
    nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    ns = dict(np=np, deque=deque, DualWindowHistory=DualWindowHistory)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns


NS = functions(CANDIDATE/'engine.py', ['_RtDetectorInputHistory', '_rt_burst_rejection_reason'])
LATENCY = functions(CANDIDATE/'core.py', ['_detector_latency_estimate'])['_detector_latency_estimate']


class FreshPairsTests(unittest.TestCase):
    def setUp(self):
        self.h = NS['_RtDetectorInputHistory']()

    def feed(self, seq, phase=None, channel=0, samples=None, stream=1, generation=1):
        if phase is None:
            phase = seq & 1
        if samples is None:
            samples = np.full(200, seq & 0xffff, dtype=np.int32)
        return self.h.feed(channel, samples, phase, (stream, seq, seq*40, phase, len(samples)), generation)

    def test_each_check_requires_a_fresh_packet_with_bounded_reuse(self):
        used = []
        for seq in range(20):
            if self.feed(seq):
                block, stamps = self.h.snapshot(200)
                self.assertEqual((stamps[0][3]&1, stamps[1][3]&1), (0, 1))
                used.extend([stamps[0][1], stamps[1][1]])
        self.assertEqual(len(used), 38)
        self.assertEqual(set(used), set(range(20)))
        self.assertEqual(max(used.count(seq) for seq in set(used)), 2)
        self.assertEqual(self.h.counts, [19, 0])

    def test_same_phase_replaced_not_paired(self):
        self.assertFalse(self.feed(0, 0))
        self.assertFalse(self.feed(1, 0))
        self.assertTrue(self.feed(2, 1))
        _, stamps = self.h.snapshot(200)
        self.assertEqual([x[1] for x in stamps[:2]], [1, 2])
        self.assertEqual(self.h.same_phase[0], 1)

    def test_duplicate_and_gap_reset(self):
        self.feed(0)
        self.assertFalse(self.feed(0))
        self.assertFalse(self.feed(1))
        self.assertTrue(self.feed(2))
        self.assertFalse(self.feed(5))
        self.assertTrue(self.feed(6))
        _, stamps = self.h.snapshot(200)
        self.assertEqual(sorted(x[1] for x in stamps[:2]), [5, 6])
        self.assertEqual(self.h.discontinuities[0], 2)

    def test_wrap_and_generation(self):
        self.assertFalse(self.feed(0xffffffff))
        self.assertTrue(self.feed(0))
        self.assertFalse(self.feed(1, generation=2))
        self.assertFalse(self.feed(2, stream=2, generation=2))
        self.assertTrue(self.feed(3, stream=2, generation=2))

    def test_channel_independence_and_immutable_snapshot(self):
        raw = np.full(200, 33333, dtype=np.int32)
        self.feed(0, samples=raw)
        self.feed(50, channel=1)
        raw[:] = 0
        self.assertTrue(self.feed(1))
        block, _ = self.h.snapshot(200)
        self.assertTrue(np.all(block[0] == 33333))
        self.assertTrue(np.all(block[2] == 32768))
        self.assertFalse(block[0].flags.writeable)
        self.assertTrue(self.feed(51, channel=1))
        self.assertTrue(np.all(block[0] == 33333))

    def test_production_pipeline_uses_raw_before_rolling_mean(self):
        tree = ast.parse((CANDIDATE/'engine.py').read_text(encoding='utf8'))
        reader = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_reader_thread_func')
        code = ast.unparse(reader)
        for ch in (0, 1):
            self.assertLess(code.index(f'rt_history.feed({ch}, rt_input{ch}'), code.index(f'ch{ch}, rpi_ready{ch} = average_rpi_frame'))
            self.assertNotIn(f'rt_pending.append(({ch}, ch{ch}, seqv))', code)
        worker = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_rt_detector_process_main')
        parent = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_rt_detector_drain_results')
        self.assertIn('int(source_seq) - int(previous_source_seq)', ast.unparse(worker))
        self.assertIn('& 4294967295 == 1', ast.unparse(worker))
        self.assertIn('& 4294967295 == 1', ast.unparse(parent))


class BurstGateTests(unittest.TestCase):
    def reason(self, **overrides):
        args = dict(level=900, reference_noise=100, max_ratio=8, amplitude_ok=True,
                    position_ok=True, noise_ok=True, shape_ok=True, base_ok=True)
        args.update(overrides)
        return NS['_rt_burst_rejection_reason'](**args)

    def test_valid_strong_tag_is_not_blanked(self):
        self.assertEqual(self.reason(level=1e12), '')

    def test_invalid_strong_transient_reasons(self):
        for flag, reason in [('noise_ok', 'noise'), ('shape_ok', 'shape'), ('base_ok', 'base_width')]:
            self.assertEqual(self.reason(**{flag: False}), reason)

    def test_ratio_is_noise_multiple_not_threshold_multiple(self):
        self.assertEqual(self.reason(level=900, noise_ok=False), 'noise')
        self.assertEqual(self.reason(level=800, noise_ok=False), '')

    def test_irrelevant_or_disabled_and_invalid_reference(self):
        for change in [dict(amplitude_ok=False), dict(position_ok=False), dict(max_ratio=0),
                       dict(reference_noise=0), dict(reference_noise=float('nan'))]:
            self.assertEqual(self.reason(noise_ok=False, **change), '')

    def test_gate_does_not_require_named_filters(self):
        tree = ast.parse((CANDIDATE/'engine.py').read_text(encoding='utf8'))
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_update_realtime_prevbuf_detection')
        gates = [node for node in ast.walk(method) if isinstance(node, ast.If) and ast.unparse(node.test) == 'gate_enabled']
        self.assertEqual(len(gates), 1)
        self.assertIn('_rt_burst_rejection_reason', ast.unparse(gates[0]))


class LatencyTests(unittest.TestCase):
    def estimate(self, counts=(5, 5), avg=16, rpi=8, frequency=200, enabled=(True, True)):
        scope = SimpleNamespace(avg_n=avg, rpi_avg_n=rpi, stream_mode=2, freq_hz=frequency, _rt_det_latency={'total_ms': 13})
        settings = {f'det_confirm_count{ch}': count for ch, count in enumerate(counts)}
        settings.update({f'det_channel_enabled{ch}': on for ch, on in enumerate(enabled)})
        return LATENCY(scope, settings)

    def test_first_pair_and_four_fresh_arrivals(self):
        e = self.estimate()
        self.assertEqual(e['estimated_ms'], 240)
        self.assertEqual(e['channel_confirmation_ms'], [160, 160])
        self.assertEqual(e['full_input_ms'], [240, 240])
        self.assertEqual(e['first_pair_ms'], 80)
        self.assertEqual(self.estimate(avg=8)['estimated_ms'], 120)
        self.assertIsNone(e['hard_limit_ms'])

    def test_settings_change_estimate_without_limiting(self):
        self.assertEqual(self.estimate(counts=(2, 5), enabled=(True, False))['estimated_ms'], 120)
        self.assertEqual(self.estimate(counts=(20, 5), avg=32)['estimated_ms'], 1680)
        self.assertEqual(self.estimate(counts=(1, 1))['estimated_ms'], 80)
        self.assertEqual(self.estimate(rpi=1)['estimated_ms'], self.estimate(rpi=8)['estimated_ms'])
        self.assertIsNone(self.estimate(enabled=(False, False))['estimated_ms'])


if __name__ == '__main__':
    unittest.main()
