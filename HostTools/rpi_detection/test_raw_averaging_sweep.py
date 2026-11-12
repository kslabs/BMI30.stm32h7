"""Production mean arithmetic, independence and fair marked-window horizons."""
import unittest

import numpy as np

from analyze_raw_pass_features import DEFAULT_HOST, DSP
from sweep_raw_pass_averaging import DEPTHS, NoiseThreshold, capped_windows
from test_raw_pass_features import settings


class SweepTests(unittest.TestCase):
    def test_all_requested_depths_exact_phase_separated_means_and_rounding(self):
        dsp = DSP(DEFAULT_HOST, settings())
        for depth in DEPTHS:
            with self.subTest(depth=depth):
                history = type(dsp.history)()
                raw_by_phase = [[], []]
                for i in range(2*depth+8):
                    phase = i % 2
                    raw = np.full(200, 32768+(i*3 % 11), dtype=np.int32)
                    raw_by_phase[phase].append(raw)
                    ready = history.feed(0, raw, phase, (1, i, i, phase, 200), 0, depth)
                    self.assertEqual(ready, i >= 2*depth-1)
                    if ready:
                        for ph in (0, 1):
                            expected = np.rint(np.mean(raw_by_phase[ph][-depth:], axis=0)).astype(np.int32)
                            np.testing.assert_array_equal(history.pairs[0][ph], expected)

    def test_parity_and_channel_cannot_share_mean_history(self):
        dsp = DSP(DEFAULT_HOST, settings())
        history = type(dsp.history)()
        for seq in range(10):
            phase = seq % 2
            for ch in (0, 1):
                value = 32768+ch*100+phase*20+seq
                history.feed(ch, np.full(200, value, dtype=np.int32), phase,
                             (1, seq, seq, phase, 200), 0, 3)
        for ch in (0, 1):
            self.assertEqual(int(history.pairs[ch][0][0]), 32768+ch*100+6)
            self.assertEqual(int(history.pairs[ch][1][0]), 32768+ch*100+20+7)

    def test_short_impulse_attenuated_before_product_not_after_product(self):
        dsp = DSP(DEFAULT_HOST, settings())
        history = type(dsp.history)()
        for seq in range(8):
            phase = seq % 2
            raw = np.full(200, 32768, dtype=np.int32)
            if seq in (6, 7):
                raw[40] += 120
            history.feed(0, raw, phase, (1, seq, seq, phase, 200), 0, 4)
        self.assertEqual(int(history.pairs[0][0][40]-32768), 30)
        self.assertEqual(int(history.pairs[0][1][40]-32768), 30)
        # A post-product mean would give 3600; real mean-before-product gives900.
        self.assertEqual(int((history.pairs[0][0][40]-32768)*(history.pairs[0][1][40]-32768)), 900)

    def test_last_93_seconds_and_short_windows_get_equal_finite_horizons(self):
        windows = capped_windows([{'pass_id': 1, 'start_mono': 10., 'end_mono': 17.},
                                  {'pass_id': 48, 'start_mono': 100., 'end_mono': 193.},
                                  {'pass_id': 2, 'start_mono': 20., 'end_mono': 22.}], 5.)
        self.assertEqual([w['duration_s'] for w in windows], [5., 5., 2.])
        self.assertEqual(windows[1]['recorded_end_mono'], 193.)

    def test_sequence_gap_needs_refill_instead_of_bridging_history(self):
        dsp = DSP(DEFAULT_HOST, settings())
        history = type(dsp.history)()
        raw = np.full(200, 32768, dtype=np.int32)
        for seq in range(8):
            history.feed(0, raw, seq % 2, (1, seq, seq, seq % 2, 200), 0, 4)
        self.assertFalse(history.feed(0, raw, 0, (1, 10, 10, 0, 200), 0, 4))
        self.assertIsNone(history.pairs[0])
        self.assertEqual(history.discontinuities[0], 1)

    def test_original_noise_history_expires_and_peak_gate_still_rejects(self):
        config = settings()
        config.update(noise_window_s=10., ratio_noise_max_u16=10.)
        dsp = DSP(DEFAULT_HOST, config)
        channels = {'upper': {'auto_threshold': False, 'threshold': 1.6, 'threshold_high': 1.3},
                    'lower': {'auto_threshold': False, 'threshold': 2.2, 'threshold_high': 1.6}}
        model = NoiseThreshold(dsp, channels)
        first = model.feed(100., 0., 0, 1000., False, True)
        self.assertTrue(first['above_history_threshold'])
        self.assertFalse(first['scalar_candidate_screen'])
        second = model.feed(1000., 1., 0, 2000., True, True)
        self.assertEqual(second['noise_mean'], 550.)
        self.assertEqual(second['noise_p95'], 955.)
        self.assertTrue(second['scalar_candidate_screen'])
        after_expiry = model.feed(100., 12., 0, 1000., True, True)
        self.assertEqual(after_expiry['noise_avg'], 100.)
        self.assertGreater(after_expiry['manual_ratio'], 1.59)

    def test_fractional_new_product_uses_production_rounded_level_for_history_gate(self):
        config = settings()
        config.update(noise_window_s=10., ratio_noise_max_u16=10.)
        dsp = DSP(DEFAULT_HOST, config)
        channels = {'upper': {'auto_threshold': False, 'threshold': 1.6, 'threshold_high': 1.3},
                    'lower': {'auto_threshold': False, 'threshold': 2.2, 'threshold_high': 1.6}}
        model = NoiseThreshold(dsp, channels)
        level = dsp.ns['_rt_detector_level_from_peak'](.6, 'prod', 16384.25)
        self.assertEqual(level, 1)
        row = model.feed(.3, 0., 0, .6, True, True, level=level)
        self.assertEqual(row['trigger'], 1)
        self.assertTrue(row['scalar_candidate_screen'])


if __name__ == '__main__':
    unittest.main()
