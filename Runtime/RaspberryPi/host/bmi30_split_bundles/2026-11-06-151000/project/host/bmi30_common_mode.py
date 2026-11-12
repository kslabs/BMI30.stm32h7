"""Optional post-product rejection and bounded pairing of physical ADC results.

No hardware, configuration files or environment are accessed by this module.
The original detector thresholds remain the reference for the extra gate.
"""
from __future__ import annotations

import math
from collections import deque
from copy import deepcopy
import numpy as np


COMMON_MODE_DEFAULTS = {
    'common_mode_enabled': False,
    'common_mode_require_pair': True,
    'common_mode_same_lag': True,
    'common_mode_strength': 100.0,
    'common_mode_balance': 1.0,
    'common_mode_radius': 0,
    'common_mode_min_snr': 1.5,
    'common_mode_residual_threshold': 100.0,
}
_LIMITS = {
    'common_mode_strength': (0.0, 100.0),
    'common_mode_balance': (0.1, 10.0),
    'common_mode_radius': (0, 6),
    'common_mode_min_snr': (0.0, 10.0),
    'common_mode_residual_threshold': (5.0, 100.0),
}


def common_mode_settings(source=None):
    source = source if isinstance(source, dict) else {}
    result = dict(COMMON_MODE_DEFAULTS)
    for key in ('common_mode_enabled', 'common_mode_require_pair', 'common_mode_same_lag'):
        value = source.get(key, result[key])
        result[key] = (value.strip().lower() in ('1', 'true', 'yes', 'on')
                       if isinstance(value, str) else bool(value))
    for key, (lo, hi) in _LIMITS.items():
        try:
            value = float(source.get(key, result[key]))
            if not math.isfinite(value):
                raise ValueError('nonfinite')
        except (ValueError, TypeError, OverflowError):
            value = result[key]
        value = max(lo, min(hi, value))
        result[key] = int(value) if key == 'common_mode_radius' else round(value, 3)
    return result


def common_mode_decision(product, neighbor, peak_index, trigger, neighbor_noise,
                         channel, settings, synchronized=False, valid_reference=None):
    """Return the extra candidate gate; no peak-normalized graph values allowed.

    Radius is a bounded maximum envelope, never a circular shift. A secondary
    neighbor peak contributes at its own phase even if its global peak is elsewhere.
    Only the original candidate is tested; raw noise adaptation cannot collapse
    when subtraction zeros most of the background.
    """
    cfg = common_mode_settings(settings)
    diag = {'enabled': cfg['common_mode_enabled'], 'applied': False, 'blocked': False,
            'synchronized': bool(synchronized), 'reason': 'disabled'}
    if not cfg['common_mode_enabled'] or cfg['common_mode_strength'] == 0:
        if cfg['common_mode_enabled']:
            diag['reason'] = 'zero_strength'
        return True, diag
    if not synchronized:
        diag['reason'] = 'unpaired'
        diag['blocked'] = cfg['common_mode_require_pair']
        return not diag['blocked'], diag
    try:
        p = np.asarray(product, dtype=np.float64)
        q = np.asarray(neighbor, dtype=np.float64)
        idx = int(peak_index)
        if p.ndim != 1 or q.shape != p.shape or not 0 <= idx < p.size:
            raise ValueError('shape')
        if not (np.all(np.isfinite(p)) and np.all(np.isfinite(q))) or np.any(p < 0) or np.any(q < 0):
            raise ValueError('invalid product')
        noise = float(neighbor_noise)
        threshold = float(trigger)
        if not math.isfinite(noise) or noise < 0 or not math.isfinite(threshold):
            raise ValueError('invalid threshold')
        radius = cfg['common_mode_radius']
        lo, hi = max(0, idx-radius), min(q.size, idx+radius+1)
        if valid_reference is not None:
            valid = np.asarray(valid_reference, dtype=bool)
            if valid.shape != q.shape or not valid[idx]:
                raise ValueError('unsupported reference sample')
            available = np.flatnonzero(valid[lo:hi]) + lo
        else:
            available = np.arange(lo, hi)
        neighbor_idx = int(available[np.argmax(q[available])])
        reference = float(q[neighbor_idx])
        coefficient = cfg['common_mode_strength']/100.0
        coefficient *= cfg['common_mode_balance'] if int(channel) == 0 else 1.0/cfg['common_mode_balance']
        required = max(1.0, threshold * cfg['common_mode_residual_threshold']/100.0)
        diag.update(original=float(p[idx]), neighbor=reference, neighbor_index=neighbor_idx,
                    neighbor_noise=noise, coefficient=coefficient, required=required)
        # Zero reference never provides evidence for a common-mode event.
        if reference <= 0 or reference < cfg['common_mode_min_snr'] * max(noise, 1.0):
            diag['reason'] = 'weak_neighbor'
            diag['residual'] = float(p[idx])
            return True, diag
        residual = max(0.0, float(p[idx]) - coefficient*reference)
        passed = bool(residual >= required)
        diag.update(applied=True, blocked=not passed, residual=residual,
                    reason='residual_pass' if passed else 'common_mode')
        return passed, diag
    except (ValueError, TypeError, IndexError, OverflowError):
        diag['reason'] = 'invalid_reference'
        diag['blocked'] = cfg['common_mode_require_pair']
        return not diag['blocked'], diag


def common_mode_combined_decision(product, neighbor, peak_index, trigger, neighbor_noise,
                                  channel, settings, synchronized=False,
                                  aligned_product=None, aligned_noise=0., aligned_valid=None,
                                  pair_reason=None):
    """An additional same-lag check may reject; it never cancels an existing veto."""
    cfg = common_mode_settings(settings)
    allowed, independent = common_mode_decision(
        product, neighbor, peak_index, trigger, neighbor_noise, channel, cfg, synchronized)
    diag = dict(independent)
    if pair_reason and not synchronized:
        diag['pair_reason'] = str(pair_reason)
    if not cfg['common_mode_enabled'] or not cfg['common_mode_strength'] or not synchronized:
        return allowed, diag
    if cfg['common_mode_same_lag']:
        aligned_allowed, aligned = common_mode_decision(
            product, aligned_product, peak_index, trigger, aligned_noise, channel, cfg,
            synchronized, valid_reference=aligned_valid)
        if allowed and not aligned_allowed:
            diag = dict(aligned)
            diag['reason'] = 'same_lag_' + aligned['reason']
        diag.update(independent=independent, same_lag=aligned,
                    independent_allowed=bool(allowed), same_lag_allowed=bool(aligned_allowed))
        allowed = bool(allowed and aligned_allowed)
        diag['blocked'] = not allowed
    return allowed, diag


def review_window(frame, frames):
    """Freeze one time-aligned window; missing peers remain explicitly missing."""
    window = dict(frame)
    window['confirmation_frames'] = tuple(frames)
    for ch in (0, 1):
        window[f'confirmation_products{ch}'] = tuple(f.get(f'product{ch}') for f in frames)
    return window


class DetectionReviewHistory:
    """Bounded passive context for rejected candidates; never changes detection."""
    def __init__(self):
        self.recent = (deque(maxlen=6), deque(maxlen=6))
        self.events = {}
        self.latest = None
        self.seq = 0

    def clear(self):
        for channel in self.recent:
            channel.clear()

    def note(self, channel, frame, required, contiguous, rejected):
        ch = int(channel)
        recent = self.recent[ch]
        if not contiguous:
            recent.clear()
        recent.append(frame)
        if not rejected:
            return
        self.seq += 1
        frames = tuple(recent)[-max(1, min(6, int(required))):]
        saved = review_window(frame, frames)
        decision = deepcopy(frame['decision'])
        decision.update(seq=self.seq, review_seq=self.seq, review_channel=ch,
                        event_kind='rejected', fired=False, rejected=True,
                        context_frames=len(frames), context_required=int(required))
        saved.update(review_seq=self.seq, fire_channel=ch, fire_timestamp=decision['timestamp'],
                     snapshot=decision)
        events = dict(self.events)
        events[self.seq] = saved
        for old in sorted(events)[:-32]:
            events.pop(old, None)
        self.events = events
        self.latest = (self.seq, decision, saved)


class CommonModePairer:
    """Wait at most one source frame / 150 ms for the other physical ADC.

    Off mode never uses this class. Missing peers release a marked result so the
    configured gate can reset confirmations. At most one result per ADC is retained; a new result
    for that ADC flushes its older pending result first, preserving source order.
    Generation changes must clear the pairer without releasing old results.
    """
    def __init__(self, max_wait_s=0.15):
        self.max_wait_s = max_wait_s
        self.pending = {}

    def clear(self):
        self.pending.clear()

    def flush(self, now, force=False):
        ready = []
        for ch, (result, key, started) in list(self.pending.items()):
            if force or now-started >= self.max_wait_s:
                ready.append((result, {'synchronized': False, 'reason': 'pair_timeout'}))
                del self.pending[ch]
        return ready

    @staticmethod
    def phase_key(meta, ch):
        try:
            stamps = meta['common_mode_phase_stamps']
            pair = tuple(tuple(stamps[ch*2+p]) for p in (0, 1))
            # stamp = stream token, seq, device timestamp, reserved2, length
            if any(len(s) != 5 or s[0] != meta['stream_token'] or s[4] < 2 for s in pair):
                return None
            delta = (pair[0][1]-pair[1][1]) & 0xffffffff
            if delta not in (1, 0xffffffff):
                return None
            if meta.get(f'source_seq{ch}') not in (pair[0][1], pair[1][1]):
                return None
            return pair
        except (KeyError, TypeError, IndexError):
            return None

    def feed(self, result, meta, now):
        ready = self.flush(now)
        ch = int(result[0])
        if ch in self.pending:
            old, _, _ = self.pending.pop(ch)
            ready.append((old, {'synchronized': False, 'reason': 'missing_peer'}))
        key = self.phase_key(meta, ch)
        if key is None or any(result[i] is None for i in (6,14,15)):
            ready.append((result, {'synchronized': False, 'reason': 'missing_phase_metadata'}))
            return ready
        self.pending[ch] = (result, key, now)
        other = self.pending.get(1-ch)
        if other is None or other[1] != key:
            return ready
        first = self.pending.pop(0)[0]
        second = self.pending.pop(1)[0]
        block = (first[14], first[15], second[14], second[15])
        products = (first[6], second[6])
        for original in (first, second):
            combined = list(original)
            combined[16], combined[17] = block, products
            ready.append((tuple(combined), {'synchronized': True, 'phase_stamps': key,
                                           'shifts': (int(first[3]), int(second[3]))}))
        return ready


# Pulse base-width gate (canonical source: HostTools/rpi_detection/bmi30_base_width.py).
"""Measure the base of the displayed detector-product lobe, in sample intervals.

Use the product and a finite positive noise level in the same amplitude units.
Walk outwards to the first zero or local minimum strictly below that noise.
A higher/equal minimum is skipped; a flat minimum uses its inner edge. A
positive array edge is not a measured base. Shift padding is excluded. No
amplitude fraction, smoothing, or circular wrap is introduced.
"""
from math import isfinite


def base_width_limit(value=24):
    try:
        return max(10, min(100, int(value)))
    except (ValueError, TypeError, OverflowError):
        return 24


def base_width_enabled(value=False):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def measure_base_width(product, peak_index, shift=0, noise_level=None, noise_source=None):
    result = dict(valid=False, left=None, right=None, width=None,
                  left_kind=None, right_kind=None, reason="invalid_noise",
                  noise_level=None, noise_source=noise_source)
    try:
        noise = float(noise_level)
        if isfinite(noise):
            result["noise_level"] = noise
        if not isfinite(noise) or noise <= 0:
            return result
    except (TypeError, ValueError, OverflowError):
        return result
    result["reason"] = "invalid_signal"
    try:
        n, peak, shift = len(product), int(peak_index), int(shift)
        start, stop = max(0, -shift), min(n, n-shift)
        if not start <= peak < stop:
            return result
        top = float(product[peak])
        if not isfinite(top) or top <= 0:
            return result

        def boundary(direction):
            previous, bottom, descended, ever_descended = top, peak, False, False
            for i in range(peak + direction, stop if direction > 0 else start - 1, direction):
                value = float(product[i])
                if not isfinite(value):
                    return None, "invalid_signal"
                if value <= 0:
                    return i, "zero"
                if value < previous:
                    bottom, descended, ever_descended = i, True, True
                elif value > previous:
                    if descended:
                        if float(product[bottom]) < noise:
                            return bottom, "minimum"
                        # This valley is above/equal to noise. A following
                        # ascent is not a new minimum; await a fresh descent.
                        descended = False
                    elif not ever_descended:
                        return None, "not_peak"
                previous = value
            return None, "open_edge"

        left, left_kind = boundary(-1)
        right, right_kind = boundary(1)
        result.update(left=left, right=right, left_kind=left_kind, right_kind=right_kind)
        if left is None or right is None:
            result["reason"] = left_kind if left is None else right_kind
            return result
        result.update(valid=True, width=right-left, reason="measured")
    except (ValueError, TypeError, IndexError, OverflowError):
        pass
    return result


def base_width_decision(product, peak_index, maximum=24, enabled=False, shift=0,
                        noise_level=None, noise_source=None):
    result = measure_base_width(product, peak_index, shift, noise_level=noise_level,
                                noise_source=noise_source)
    enabled, maximum = base_width_enabled(enabled), base_width_limit(maximum)
    result.update(enabled=enabled, limit=maximum,
                  ok=not enabled or (result["valid"] and result["width"] <= maximum))
    if enabled and result["valid"] and not result["ok"]:
        result["reason"] = "too_wide"
    return result
