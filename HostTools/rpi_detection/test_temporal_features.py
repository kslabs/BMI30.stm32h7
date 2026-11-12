"""Signal/impulse/phase-centre tests for the experimental temporal estimators."""
import unittest
import itertools

import numpy as np

from bmi30_temporal_features import METHODS, coherent_energy, make_temporal_product, temporal_estimate


def identity(x):
    return x


def product(a, b, shift=0):
    return np.abs((np.asarray(a, dtype=np.int64)-32768)*(np.asarray(b, dtype=np.int64)-32768))


class TemporalTests(unittest.TestCase):
    def test_stable_coherent_signal_amplitude_and_product_are_preserved(self):
        for n in (8, 12):
            raw = np.full((2, n, 200), 32768, dtype=np.int64)
            raw[0, :, 35], raw[1, :, 35] = 32868, 32688
            for method in METHODS:
                with self.subTest(n=n, method=method):
                    result = make_temporal_product(raw, method, identity, product)
                    self.assertEqual(result['product'][35], 8000)
                    self.assertEqual(result['current'][0], 32768)

    def test_one_large_impulse_has_no_cross_time_coherent_energy(self):
        raw = np.full((2, 8, 200), 32768, dtype=np.int64)
        raw[:, 0, 35] += 800
        self.assertEqual(make_temporal_product(raw, 'mean', identity, product)['product'][35], 10000)
        for method in ('trim1', 'median', 'coherent_energy'):
            self.assertEqual(make_temporal_product(raw, method, identity, product)['product'][35], 0)

    def test_signed_energy_can_be_negative_and_is_rectified_only_for_combination(self):
        raw = np.full((2, 8, 200), 32768, dtype=np.int64)
        raw[:, ::2, 35] += 100
        raw[:, 1::2, 35] -= 100
        self.assertLess(coherent_energy(raw[0], identity)[35], 0)
        result = make_temporal_product(raw, 'coherent_energy', identity, product)
        self.assertEqual(result['product'][35], 0)
        self.assertEqual(result['phase_energy_negative_counts'], [1, 1])

    def test_trim_is_pointwise_one_minimum_one_maximum_and_rint_matches_mean(self):
        raw = np.array([[32768, 32778], [32770, 32776], [32772, 32774], [32774, 32772], [33000, 32000]])
        np.testing.assert_array_equal(temporal_estimate(raw, 'trim1'), [32772, 32774])
        np.testing.assert_array_equal(temporal_estimate(np.array([[32768], [32769]]), 'mean'), [32768])
        with self.assertRaisesRegex(ValueError, 'at least5'):
            temporal_estimate(raw[:4], 'trim1')

    def test_cached_original_spatial_smoothing_is_equivalent(self):
        rng = np.random.default_rng(123)
        raw = rng.integers(32000, 33500, (2, 12, 200))
        def smooth(row):
            result = row.copy()
            result[1:-1] = np.convolve(row, np.ones(3)/3, 'valid')
            return result
        cached = np.asarray([[smooth(row.astype(np.float64)) for row in phase] for phase in raw])
        a = make_temporal_product(raw, 'coherent_energy', smooth, product)
        b = make_temporal_product(raw, 'coherent_energy', smooth, product, cached)
        np.testing.assert_array_equal(a['product'], b['product'])

    def test_coherent_transmitter_will_also_survive_and_no_sign_veto_is_hidden(self):
        raw = np.full((2, 8, 200), 32768, dtype=np.int64)
        raw[:, :, 0] += 500
        result = make_temporal_product(raw, 'coherent_energy', identity, product)
        self.assertEqual(result['product'][0], 250000)

    def test_unbiased_energy_exactly_over_independent_zero_mean_noise_ensemble(self):
        for signal in (0., 2.):
            estimates = []
            for noise in itertools.product((-1., 1.), repeat=4):
                raw = 32768.+signal+np.asarray(noise)[:, None]
                estimates.append(coherent_energy(raw, identity)[0])
            self.assertAlmostEqual(float(np.mean(estimates)), signal*signal)


if __name__ == '__main__':
    unittest.main()
