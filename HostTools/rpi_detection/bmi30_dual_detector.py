"""Phase-separated averaging with an independent single-packet influence test.

This module has no hardware, settings or output side effects. The sensitive
detector uses the full means. The guard deliberately does not impose the raw
detector's complete amplitude threshold on each unaveraged packet.
"""
from collections import deque

import numpy as np


class DualWindowHistory:
    """Immutable original packets and rolling means, separately per ADC/phase."""
    def __init__(self):
        self.key = None
        self.depth = 1
        self.previous = [None, None]
        self.last_seq = [None, None]
        self.pairs = [None, None]
        self.stamps = [None, None]
        self.windows = {}
        self.totals = {}
        self.means = {}
        self.counts = [0, 0]
        self.same_phase = [0, 0]
        self.discontinuities = [0, 0]

    def _clear_channel(self, ch):
        self.previous[ch] = None
        self.pairs[ch] = self.stamps[ch] = None
        for phase in (0, 1):
            for mapping in (self.windows, self.totals, self.means):
                mapping.pop((ch, phase), None)

    def feed(self, channel, samples, phase, stamp, generation, depth=1):
        depth = int(depth)
        if not 1 <= depth <= 12:
            raise ValueError('Raspberry depth outside 1..12')
        key = (stamp[0], int(generation), len(samples), depth)
        if key != self.key:
            self.__init__()
            self.key, self.depth = key, depth
        ch, phase = int(channel), int(phase) & 1
        seq = int(stamp[1]) & 0xffffffff
        last = self.last_seq[ch]
        if last is not None and ((seq-last) & 0xffffffff) != 1:
            self._clear_channel(ch)
            self.discontinuities[ch] += 1
            if seq == last:
                return False
        self.last_seq[ch] = seq
        current = np.array(samples, dtype=np.int32, copy=True)
        current.setflags(write=False)
        slot = (ch, phase)
        if slot not in self.windows:
            self.windows[slot] = deque(maxlen=depth)
            self.totals[slot] = np.zeros(len(samples), dtype=np.int64)
        ring, total = self.windows[slot], self.totals[slot]
        if len(ring) == depth:
            total -= ring[0][0]
        ring.append((current, tuple(stamp)))
        total += current
        mean = np.rint(total/len(ring)).astype(np.int32)
        mean.setflags(write=False)
        self.means[slot] = mean
        previous = self.previous[ch]
        self.previous[ch] = (current, tuple(stamp), phase)
        adjacent = previous is not None and previous[2] != phase
        ready = all(len(self.windows.get((ch, p), ())) == depth for p in (0, 1))
        if not adjacent or not ready:
            if previous is not None and previous[2] == phase:
                self.same_phase[ch] += 1
            self.pairs[ch] = self.stamps[ch] = None
            return False
        self.pairs[ch] = (self.means[(ch, 0)], self.means[(ch, 1)])
        self.stamps[ch] = (tuple(stamp), previous[1]) if phase == 0 else (previous[1], tuple(stamp))
        self.counts[ch] += 1
        return True

    def snapshot(self, sample_count):
        blank = np.full(sample_count, 32768, dtype=np.int32)
        return (tuple(x for pair in self.pairs for x in (pair or (blank, blank))),
                tuple(x for pair in self.stamps for x in (pair or (None, None))))

    def raw_windows(self, channels):
        result = {}
        for ch in channels:
            rings = [self.windows.get((int(ch), phase), ()) for phase in (0, 1)]
            if any(len(ring) != self.depth for ring in rings) or self.pairs[int(ch)] is None:
                continue
            result[int(ch)] = {'depth': self.depth, 'last_seq': self.last_seq[int(ch)],
                'samples': tuple(tuple(frame[0] for frame in ring) for ring in rings),
                'sequences': tuple(tuple(int(frame[1][1]) & 0xffffffff for frame in ring) for ring in rings)}
        return result

    def diagnostics(self):
        return {'pairs': list(self.counts), 'same_phase_rejected': list(self.same_phase),
                'sequence_discontinuities': list(self.discontinuities), 'depth': self.depth,
                'window_counts': [len(self.windows.get((ch, p), ())) for ch in (0, 1) for p in (0, 1)]}


def evaluate_impulse_guard(window, source_seq, full_pair, product, peak, shift,
                           smooth, make_product, measure_noise):
    """Recompute the candidate after excluding each original packet in turn.

    Exclusion changes N to N-1; each counterfactual is renormalized and has
    its own measured noise. Only severe collapse (75% of noise-subtracted
    support AND over half the peak) vetoes. A mere crossing below the full
    detector threshold is deliberately insufficient. The fixed +/-2 sample
    ROI and original RX shift prevent searching for a different noise peak.
    """
    diag = {'version': 1, 'allowed': False, 'reason': 'missing_raw_window',
            'source_seq': int(source_seq) if source_seq is not None else None,
            'retained_signal_min': 0.25, 'retained_peak_min': 0.5}
    if not isinstance(window, dict):
        return diag
    try:
        depth = int(window['depth'])
        raw = np.asarray(window['samples'], dtype=np.int32)
        seqs = np.asarray(window['sequences'], dtype=np.uint64)
        seq = int(source_seq) & 0xffffffff
        n = len(product)
        if (not 1 <= depth <= 12 or raw.shape != (2, depth, n) or seqs.shape != (2, depth)
                or int(window['last_seq']) != seq or len(set(map(int, seqs.ravel()))) != 2*depth
                or seq not in seqs[:, -1] or ((seq-1) & 0xffffffff) not in seqs[:, -1]
                or max((seq-int(x)) & 0xffffffff for x in seqs.ravel()) > 4*depth):
            raise ValueError('raw provenance mismatch')
        # Exact reproduction binds the guard to the transported primary means.
        means = np.rint(raw.astype(np.int64).sum(axis=1)/depth).astype(np.int32)
        if not all(np.array_equal(smooth(means[p]), full_pair[p]) for p in (0, 1)):
            raise ValueError('raw/averaged payload mismatch')
        lo, hi = max(0, int(peak)-2), min(n, int(peak)+3)
        if hi <= lo:
            raise ValueError('invalid ROI')
        full_peak = float(np.max(product[lo:hi]))
        full_noise = float(measure_noise(product, int(peak))[0])
        if not np.isfinite(full_peak+full_noise) or full_peak <= 0 or full_noise <= 0:
            raise ValueError('invalid primary noise')
        diag.update(depth=depth, sequences=[[int(x) for x in row] for row in seqs],
                    peak=int(peak), shift=int(shift), full_peak=full_peak, full_noise=full_noise)
        raw_pair = tuple(smooth(raw[p, -1]) for p in (0, 1))
        raw_product = make_product(*raw_pair, int(shift))
        raw_noise = float(measure_noise(raw_product, int(peak))[0])
        raw_peak = float(np.max(raw_product[lo:hi]))
        diag['raw_branch'] = {'peak_same_roi': raw_peak, 'noise': raw_noise,
                              'snr_same_roi': raw_peak/raw_noise if raw_noise > 0 else None}
        if depth == 1:
            return dict(diag, allowed=True, reason='depth_one_fresh_confirmations', checks=0)
        totals = raw.astype(np.int64).sum(axis=1)
        full_signal = max(1.0, full_peak-full_noise)
        worst = None
        for phase in (0, 1):
            for index in range(depth):
                reduced = np.rint((totals[phase]-raw[phase, index])/(depth-1)).astype(np.int32)
                pair = list(full_pair)
                pair[phase] = smooth(reduced)
                remaining = make_product(*pair, int(shift))
                remaining_peak = float(np.max(remaining[lo:hi]))
                remaining_noise = float(measure_noise(remaining, int(peak))[0])
                if not np.isfinite(remaining_peak+remaining_noise) or remaining_noise < 0:
                    raise ValueError('invalid counterfactual noise')
                retained_peak = remaining_peak/full_peak
                retained_signal = max(0.0, remaining_peak-remaining_noise)/full_signal
                collapsed = retained_peak < .5 and retained_signal < .25
                value = {'phase': phase, 'excluded_seq': int(seqs[phase, index]),
                         'retained_peak': retained_peak, 'retained_signal': retained_signal,
                         'remaining_peak': remaining_peak, 'remaining_noise': remaining_noise,
                         'remaining_snr': remaining_peak/remaining_noise if remaining_noise > 0 else None,
                         'collapsed': bool(collapsed)}
                if worst is None or (value['collapsed'], -value['retained_signal']) > (worst['collapsed'], -worst['retained_signal']):
                    worst = value
        return dict(diag, allowed=not worst['collapsed'],
                    reason='single_packet_dominance' if worst['collapsed'] else 'distributed_support',
                    checks=2*depth, worst=worst)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        return dict(diag, reason='invalid_raw_window', error=str(exc))
