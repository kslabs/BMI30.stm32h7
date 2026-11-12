import unittest

import numpy as np

from bmi30_dual_detector import DualWindowHistory, evaluate_impulse_guard


def product(a, b, shift=0):
    a = np.asarray(a, dtype=np.int64)-32768
    b = np.asarray(b, dtype=np.int64)-32768
    out = np.zeros(len(a), dtype=np.int64)
    if shift >= 0:
        out[:len(a)-shift or None] = np.abs(a[:len(a)-shift or None]*b[shift:])
    else:
        out[-shift:] = np.abs(a[-shift:]*b[:shift])
    return out


def noise(values, peak):
    mask = np.abs(np.arange(len(values))-peak) > 12
    return float(np.mean(values[mask])), 0, ()


class HistoryTests(unittest.TestCase):
    def feed(self, h, seq, phase=None, depth=8, ch=0, generation=1, samples=None):
        if phase is None:
            phase = seq & 1
        if samples is None:
            samples = np.full(200, 32768+(seq&1023), dtype=np.int32)
        return h.feed(ch, samples, phase, (1, seq, (seq*40)&0xffffffff, phase, 200), generation, depth)

    def test_exact_phase_mean_and_original_provenance(self):
        h = DualWindowHistory()
        for seq in range(15):
            self.assertFalse(self.feed(h, seq))
        self.assertTrue(self.feed(h, 15))
        block, stamps = h.snapshot(200)
        self.assertTrue(np.all(block[0] == np.rint(32768+np.arange(0, 16, 2).mean())))
        self.assertTrue(np.all(block[1] == np.rint(32768+np.arange(1, 16, 2).mean())))
        raw = h.raw_windows([0])[0]
        self.assertEqual(raw['sequences'], (tuple(range(0,16,2)), tuple(range(1,16,2))))
        self.assertEqual([stamp[1] for stamp in stamps[:2]], [14,15])
        self.assertTrue(self.feed(h, 16))
        self.assertEqual(raw['sequences'][0][0], 0)
        self.assertFalse(raw['samples'][0][0].flags.writeable)

    def test_gap_resets_one_adc_without_contaminating_other(self):
        h = DualWindowHistory()
        for seq in range(16):
            for ch in (0,1):self.feed(h, seq, ch=ch)
        self.assertFalse(self.feed(h, 30, ch=0))
        self.assertTrue(self.feed(h, 16, ch=1))
        self.assertEqual(h.diagnostics()['window_counts'], [1,0,8,8])

    def test_phase_repeat_is_not_a_detection_pair(self):
        h = DualWindowHistory()
        for seq in range(16):self.feed(h, seq)
        self.assertFalse(self.feed(h, 16, phase=1))
        self.assertTrue(self.feed(h, 17, phase=0))
        self.assertEqual(h.same_phase[0], 1)

    def test_depth_or_generation_change_requires_warmup(self):
        h = DualWindowHistory()
        for seq in range(16):self.feed(h, seq)
        self.assertFalse(self.feed(h, 16, depth=4))
        self.assertFalse(self.feed(h, 17, depth=4, generation=2))
        self.assertEqual(h.diagnostics()['window_counts'], [0,1,0,0])


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(85)
        self.raw = np.rint(32768+self.rng.normal(0,30,(2,8,200))).astype(np.int32)

    def check(self, raw=None, **overrides):
        raw = self.raw if raw is None else raw
        depth = raw.shape[1]
        seqs = (tuple(range(0,2*depth,2)), tuple(range(1,2*depth,2)))
        means = np.rint(raw.astype(np.int64).sum(axis=1)/depth).astype(np.int32)
        window = dict(depth=depth, last_seq=2*depth-1, samples=raw, sequences=seqs)
        args = dict(window=window, source_seq=2*depth-1, full_pair=tuple(means),
                    product=product(*means), peak=100, shift=0, smooth=lambda x:x,
                    make_product=product, measure_noise=noise)
        args.update(overrides)
        return evaluate_impulse_guard(**args)

    def test_one_packet_that_carries_peak_is_vetoed(self):
        self.raw[0,3,98:103] += 15000
        # The other phase supplies only ordinary noise, just as in a one-phase
        # impulse; no requirement for a second outlier is hidden in the test.
        d = self.check()
        self.assertFalse(d['allowed'])
        self.assertEqual(d['reason'], 'single_packet_dominance')
        self.assertEqual(d['worst']['excluded_seq'], 6)

    def test_distributed_weak_signal_survives(self):
        self.raw[:,:,98:103] += 35
        d = self.check()
        self.assertTrue(d['allowed'], d)
        self.assertEqual(d['checks'], 16)
        self.assertEqual(d['reason'], 'distributed_support')

    def test_distributed_strong_signal_and_opposite_phase_sign(self):
        self.raw[0,:,98:103] += 1000
        self.raw[1,:,98:103] -= 1000
        self.assertTrue(self.check()['allowed'])

    def test_no_full_amplitude_threshold_is_required_after_exclusion(self):
        self.raw[:,:,98:103] += 35
        d = self.check()
        hypothetical_threshold = d['full_peak']*.99
        self.assertLess(d['worst']['remaining_peak'], hypothetical_threshold)
        self.assertTrue(d['allowed'])

    def test_missing_mismatched_or_replayed_evidence_fails_closed(self):
        for change in [dict(window=None), dict(source_seq=99), dict(full_pair=(np.zeros(200),np.zeros(200)))]:
            self.assertFalse(self.check(**change)['allowed'])
        means = np.rint(self.raw.mean(axis=1)).astype(np.int32)
        window = dict(depth=8,last_seq=15,samples=self.raw,sequences=(tuple(range(0,16,2)),tuple(range(0,16,2))))
        self.assertFalse(self.check(window=window)['allowed'])

    def test_roi_does_not_jump_to_an_unrelated_surviving_peak(self):
        self.raw[0,3,98:103] += 15000
        self.raw[:,:,170:175] += 1000
        self.assertFalse(self.check()['allowed'])

    def test_depth_one_relies_on_existing_fresh_confirmations(self):
        self.raw[:,:,98:103] += 100
        d = self.check(self.raw[:,:1])
        self.assertTrue(d['allowed'])
        self.assertEqual(d['reason'], 'depth_one_fresh_confirmations')


if __name__ == '__main__':
    unittest.main()
