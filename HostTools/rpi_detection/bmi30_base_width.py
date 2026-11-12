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
