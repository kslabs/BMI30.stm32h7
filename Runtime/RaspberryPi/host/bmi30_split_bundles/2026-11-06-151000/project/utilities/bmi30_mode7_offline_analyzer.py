#!/usr/bin/env python3
"""Offline BMI30 mode 6 baseline and mode 7 score explorer.

This script intentionally does not import the live engine module. Importing the
engine can initialize Qt/serial state; for offline analysis we keep the math
small, explicit, and side-effect free.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np


ADC_CENTER = 32768
DETECT_LEVEL_MAX = (1 << 63) - 1
DEFAULT_RECORDING = Path("host/player_recordings/player_raw_20260703_182716_Casino_02.npz")
DEFAULT_OUT_DIR = Path("docs/mode7_offline_analysis")
SCORE_KINDS = (
    "SUM_POWER",
    "PRODUCT_POS",
    "PRODUCT_ABS",
    "COMBINED",
    "BIPOLAR_GUIDED",
    "BIPOLAR_PAIR",
    "BIPOLAR_P2P",
    "BIPOLAR_ENERGY",
)
CHANNEL_NAMES = ("upper", "lower")


def _bool_env(name: str, default: bool) -> bool:
    try:
        raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
        return raw not in ("0", "false", "no", "off")
    except Exception:
        return bool(default)


def _int_env(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), int(value))
    if maximum is not None:
        value = min(int(maximum), int(value))
    return int(value)


def _float_env(name: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    if not math.isfinite(value):
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), float(value))
    if maximum is not None:
        value = min(float(maximum), float(value))
    return float(value)


def _clamp_detector_level(value: Any, default: int = 0, minimum: int = 0) -> int:
    try:
        if isinstance(value, (int, np.integer)):
            val = int(value)
        else:
            val = int(round(float(value)))
    except Exception:
        val = int(default)
    if val < int(minimum):
        val = int(minimum)
    if val > DETECT_LEVEL_MAX:
        return int(DETECT_LEVEL_MAX)
    return int(val)


def _product_abs_array(prod_arr: Any) -> np.ndarray:
    try:
        arr = np.asarray(prod_arr)
        if arr.size <= 0:
            return arr
        if np.issubdtype(arr.dtype, np.integer):
            try:
                if int(np.min(arr)) >= 0:
                    return arr
            except Exception:
                pass
            return np.abs(arr)
        arr = arr.astype(np.float64, copy=False)
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
        try:
            if float(np.min(arr)) >= 0.0:
                return arr
        except Exception:
            pass
        return np.abs(arr)
    except Exception:
        return np.asarray([], dtype=np.float64)


def _named_filter_enabled(name: str, default: bool = False) -> bool:
    key = "BMI30_DETECT_FILTER_" + str(name or "").strip().upper()
    return _bool_env(key, default)


def _named_filters_active() -> bool:
    return bool(
        _named_filter_enabled("CASINO", True)
        or _named_filter_enabled("BARKHAUSEN", False)
        or _named_filter_enabled("MICROWIRE", False)
        or _named_filter_enabled("PAPER", False)
    )


def _mark_window_bounds(n: int) -> tuple[int, int]:
    n = max(0, int(n))
    if n <= 0:
        return 0, 0
    if not _bool_env("BMI30_RT_MARK_WINDOW_GUARD", True):
        return 0, n
    start_raw = str(os.getenv("BMI30_RT_MARK_WINDOW_START", "") or "").strip()
    end_raw = str(os.getenv("BMI30_RT_MARK_WINDOW_END", "") or "").strip()
    if start_raw:
        try:
            start = int(start_raw)
        except Exception:
            start = int(round(0.05 * float(n)))
    else:
        start = int(round(_float_env("BMI30_RT_MARK_WINDOW_START_FRAC", 0.05) * float(n)))
    if end_raw:
        try:
            end = int(end_raw)
        except Exception:
            end = int(round(0.45 * float(n)))
    else:
        end = int(round(_float_env("BMI30_RT_MARK_WINDOW_END_FRAC", 0.45) * float(n)))
    start = max(0, min(n - 1, int(start)))
    end = max(start + 1, min(n, int(end)))
    return int(start), int(end)


def _peak_supported(prod_abs: np.ndarray, peak_idx: int, peak_value: float) -> bool:
    if not _bool_env("BMI30_RT_IMPULSE_GUARD", True):
        return True
    try:
        if prod_abs is None or prod_abs.size <= 0:
            return False
        peak_value = float(peak_value)
        if not math.isfinite(peak_value) or peak_value <= 0.0:
            return False
        peak_idx = max(0, min(int(peak_idx), int(prod_abs.size) - 1))
        win = _int_env("BMI30_RT_IMPULSE_WIN", 7, 1)
        min_width = _int_env("BMI30_RT_IMPULSE_MIN_WIDTH", 3, 1)
        frac = _float_env("BMI30_RT_IMPULSE_FRAC", 0.30, 0.000001, 1.0)
        half = max(1, win // 2)
        lo = max(0, peak_idx - half)
        hi = min(int(prod_abs.size), peak_idx + half + 1)
        local = np.asarray(prod_abs[lo:hi])
        if local.size <= 0:
            return False
        support = int(np.count_nonzero(local >= (peak_value * frac)))
        return support >= min_width
    except Exception:
        return True


def mode6_product_display(current: np.ndarray, previous: np.ndarray, shift: int = 0) -> np.ndarray | None:
    try:
        n = min(int(len(current)), int(len(previous)))
    except Exception:
        n = 0
    if n <= 1:
        return None
    try:
        cur = np.asarray(current[:n], dtype=np.int64) - np.int64(ADC_CENTER)
        prev = np.asarray(previous[:n], dtype=np.int64) - np.int64(ADC_CENTER)
        out = np.zeros(n, dtype=np.int64)
        shift = int(shift or 0)
        if shift >= 0:
            end = n - shift
            if end > 0:
                np.multiply(cur[:end], prev[shift : shift + end], out=out[:end])
                np.abs(out[:end], out=out[:end])
        else:
            start = -shift
            if start < n:
                np.multiply(cur[start:n], prev[: n - start], out=out[start:n])
                np.abs(out[start:n], out=out[start:n])
        return out
    except Exception:
        return None


def mode6_product_current_prev(
    current: np.ndarray,
    previous: np.ndarray,
    max_shift: int,
    shift_penalty: float,
) -> dict[str, Any] | None:
    """Mirror the mode 6 realtime prevbuf/product selector used by the engine."""

    try:
        n = min(int(len(current)), int(len(previous)))
    except Exception:
        n = 0
    if n <= 1:
        return None
    cur = np.asarray(current[:n], dtype=np.int64) - np.int64(ADC_CENTER)
    prev = np.asarray(previous[:n], dtype=np.int64) - np.int64(ADC_CENTER)
    filters_active = bool(_named_filters_active())
    if filters_active:
        search_start, search_end = _mark_window_bounds(n)
        if search_end <= search_start:
            search_start, search_end = 0, n
    else:
        search_start, search_end = 0, n

    def segment_peak(seg_abs: np.ndarray, base_idx: int) -> tuple[int, float, bool]:
        try:
            if seg_abs is None or int(seg_abs.size) <= 0:
                return 0, -1.0, False
            base_idx = int(base_idx)
            lo = max(0, int(search_start) - base_idx)
            hi = min(int(seg_abs.size), int(search_end) - base_idx)
            if hi <= lo:
                return 0, -1.0, False
            view = seg_abs[lo:hi]
            if view.size <= 0:
                return 0, -1.0, False
            local_idx = lo + int(np.argmax(view))
            metric = float(seg_abs[local_idx])
            supported = True if not filters_active else _peak_supported(seg_abs, int(local_idx), float(metric))
            return int(local_idx), float(metric), bool(supported)
        except Exception:
            return 0, -1.0, False

    max_shift = min(abs(int(max_shift or 0)), n - 1)
    shift_penalty = float(shift_penalty)
    if not math.isfinite(shift_penalty) or shift_penalty < 0.0:
        shift_penalty = 0.02
    if max_shift <= 0:
        shift = 0
        prod_abs = np.empty(n, dtype=np.int64)
        np.multiply(cur, prev, out=prod_abs)
        np.abs(prod_abs, out=prod_abs)
        local_idx, peak_value, peak_supported = segment_peak(prod_abs, 0)
        peak_idx = int(local_idx)
    else:
        best_shift = 0
        best_metric = -1.0
        best_score = -1.0
        best_idx = 0
        best_supported = True
        for shift_try in range(-max_shift, max_shift + 1):
            if shift_try >= 0:
                end = n - shift_try
                if end <= 0:
                    continue
                seg_abs = np.abs(cur[:end] * prev[shift_try : shift_try + end])
                base_idx = 0
            else:
                start = -shift_try
                if start >= n:
                    continue
                seg_abs = np.abs(cur[start:] * prev[: n - start])
                base_idx = start
            local_idx, metric, supported = segment_peak(seg_abs, base_idx)
            score = float(metric)
            if shift_penalty > 0.0:
                score = score / (1.0 + (float(abs(int(shift_try))) * float(shift_penalty)))
            if not bool(supported):
                score *= 0.25
            if score > best_score:
                best_metric = metric
                best_score = score
                best_shift = shift_try
                best_idx = base_idx + local_idx
                best_supported = bool(supported)
        shift = int(best_shift)
        peak_idx = int(best_idx)
        peak_value = float(max(0.0, best_metric))
        peak_supported = bool(best_supported)
        prod_abs = mode6_product_display(current, previous, int(shift))
    level = int(_clamp_detector_level(peak_value)) if bool(peak_supported) else 0
    return {
        "level": int(level),
        "shift": int(shift),
        "peak_idx": int(peak_idx),
        "peak_value": float(peak_value),
        "prod_arr": prod_abs,
        "peak_supported": bool(peak_supported),
    }


def _shift_like_mode6(values: np.ndarray, shift: int, n: int) -> np.ndarray:
    out = np.zeros(n, dtype=np.float64)
    shift = int(shift or 0)
    if shift >= 0:
        end = n - shift
        if end > 0:
            out[:end] = values[shift : shift + end]
    else:
        start = -shift
        if start < n:
            out[start:n] = values[: n - start]
    return out


def _window_sum(values: np.ndarray, radius: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size <= 0:
        return arr
    radius = max(0, int(radius))
    if radius <= 0:
        return arr.copy()
    kernel = np.ones((2 * radius) + 1, dtype=np.float64)
    return np.convolve(arr, kernel, mode="same")


def _window_count(n: int, radius: int) -> np.ndarray:
    if n <= 0:
        return np.asarray([], dtype=np.float64)
    return _window_sum(np.ones(n, dtype=np.float64), radius)


def _moving_average(values: np.ndarray, width: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size <= 0:
        return arr
    width = max(1, min(int(width), int(arr.size)))
    if width <= 1:
        return arr.copy()
    kernel = np.ones(width, dtype=np.float64) / float(width)
    return np.convolve(arr, kernel, mode="same")


def _highpass(values: np.ndarray, energy_radius: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    width = max((4 * max(1, int(energy_radius))) + 1, 31)
    if width % 2 == 0:
        width += 1
    width = min(width, max(1, int(arr.size)))
    return arr - _moving_average(arr, width)


def _robust_floor(curve: np.ndarray) -> float:
    vals = np.asarray(curve, dtype=np.float64)
    vals = vals[np.isfinite(vals)]
    if vals.size <= 0:
        return 1.0
    vals = vals[vals >= 0.0]
    if vals.size <= 0:
        return 1.0
    if vals.size > 10:
        cutoff = float(np.percentile(vals, 80.0))
        trimmed = vals[vals <= cutoff]
        if trimmed.size:
            vals = trimmed
    floor = float(np.median(vals))
    if not math.isfinite(floor) or floor <= 0.0:
        positive = vals[vals > 0.0]
        if positive.size:
            floor = float(np.percentile(positive, 25.0))
    if not math.isfinite(floor) or floor <= 0.0:
        floor = 1.0
    return float(floor)


def _local_center(values: np.ndarray, radius: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    counts = np.maximum(_window_count(int(arr.size), radius), 1.0)
    return arr - (_window_sum(arr, radius) / counts)


def _sum_power_curve(sum_signal: np.ndarray, radius: int, local_dc: bool) -> np.ndarray:
    signal = np.asarray(sum_signal, dtype=np.float64)
    if not local_dc:
        return _window_sum(signal * signal, radius)
    counts = np.maximum(_window_count(int(signal.size), radius), 1.0)
    s1 = _window_sum(signal, radius)
    s2 = _window_sum(signal * signal, radius)
    return np.maximum(0.0, s2 - ((s1 * s1) / counts))


def _product_pos_curve(product_signal: np.ndarray, radius: int, local_dc: bool) -> np.ndarray:
    signal = _local_center(product_signal, radius) if local_dc else np.asarray(product_signal, dtype=np.float64)
    return _window_sum(np.maximum(signal, 0.0), radius)


def _product_abs_curve(product_signal: np.ndarray, radius: int, local_dc: bool) -> np.ndarray:
    signal = _local_center(product_signal, radius) if local_dc else np.asarray(product_signal, dtype=np.float64)
    return _window_sum(np.abs(signal), radius)


def _bipolar_signal(sum_signal: np.ndarray, radius: int, local_dc: bool) -> np.ndarray:
    signal = np.asarray(sum_signal, dtype=np.float64)
    return _local_center(signal, radius) if local_dc else signal


def _bipolar_curves(sum_signal: np.ndarray, radius: int, local_dc: bool) -> tuple[np.ndarray, np.ndarray]:
    signal = _bipolar_signal(sum_signal, radius, local_dc)
    n = int(signal.size)
    radius = max(0, int(radius))
    if n <= 0:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.float64)
    if radius <= 0:
        local_max = signal.astype(np.float64, copy=False)
        local_min = signal.astype(np.float64, copy=False)
    else:
        padded = np.pad(signal, (radius, radius), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, (2 * radius) + 1)
        local_max = np.nanmax(windows, axis=1)
        local_min = np.nanmin(windows, axis=1)
    p2p = np.maximum(0.0, local_max - local_min)
    energy = np.zeros(n, dtype=np.float64)
    mask = (local_max > 0.0) & (local_min < 0.0)
    energy[mask] = p2p[mask] * np.minimum(local_max[mask], -local_min[mask])
    return p2p, energy


def _bipolar_pair_curve(sum_signal: np.ndarray, radius: int, local_dc: bool) -> np.ndarray:
    signal = _bipolar_signal(sum_signal, radius, local_dc)
    n = int(signal.size)
    curve = np.zeros(n, dtype=np.float64)
    radius = max(1, int(radius))
    for half_gap in range(1, radius + 1):
        if (2 * half_gap) + 1 > n:
            break
        left = signal[: n - (2 * half_gap)]
        right = signal[(2 * half_gap) :]
        opposite = (left * right) < 0.0
        if not np.any(opposite):
            continue
        diff = np.abs(left - right)
        balance = np.minimum(np.abs(left), np.abs(right))
        score = diff * balance
        center_start = half_gap
        view = curve[center_start : center_start + score.size]
        np.maximum(view, np.where(opposite, score, 0.0), out=view)
    return curve


def _bipolar_guided_curve(sum_signal: np.ndarray, product_signal: np.ndarray, radius: int, local_dc: bool) -> np.ndarray:
    pair_curve = _bipolar_pair_curve(sum_signal, radius, local_dc)
    if pair_curve.size <= 0:
        return pair_curve
    sum_curve = _sum_power_curve(sum_signal, radius, local_dc)
    pos_curve = _product_pos_curve(product_signal, radius, local_dc)
    strength = (sum_curve / _robust_floor(sum_curve)) + (pos_curve / _robust_floor(pos_curve))
    pair_norm = pair_curve / _robust_floor(pair_curve)
    return pair_norm * strength


def _bipolar_stats(sum_signal: np.ndarray, center_idx: int, radius: int, local_dc: bool) -> dict[str, float | int]:
    signal = _bipolar_signal(sum_signal, radius, local_dc)
    n = int(signal.size)
    if n <= 0:
        return {
            "sum_max_idx": 0,
            "sum_min_idx": 0,
            "sum_mid_idx": 0.0,
            "sum_lobe_gap": 0,
            "sum_max_value": 0.0,
            "sum_min_value": 0.0,
            "sum_p2p": 0.0,
        }
    center_idx = max(0, min(int(center_idx), n - 1))
    radius = max(0, int(radius))
    lo = max(0, center_idx - radius)
    hi = min(n, center_idx + radius + 1)
    local = signal[lo:hi]
    if local.size <= 0:
        return {
            "sum_max_idx": center_idx,
            "sum_min_idx": center_idx,
            "sum_mid_idx": float(center_idx),
            "sum_lobe_gap": 0,
            "sum_max_value": 0.0,
            "sum_min_value": 0.0,
            "sum_p2p": 0.0,
        }
    max_idx = lo + int(np.nanargmax(local))
    min_idx = lo + int(np.nanargmin(local))
    max_value = float(signal[max_idx])
    min_value = float(signal[min_idx])
    return {
        "sum_max_idx": int(max_idx),
        "sum_min_idx": int(min_idx),
        "sum_mid_idx": float((float(max_idx) + float(min_idx)) / 2.0),
        "sum_lobe_gap": int(abs(int(max_idx) - int(min_idx))),
        "sum_max_value": float(max_value),
        "sum_min_value": float(min_value),
        "sum_p2p": float(max_value - min_value),
    }


def _energy_curve(sum_signal: np.ndarray, product_signal: np.ndarray, kind: str, radius: int, local_dc: bool) -> np.ndarray:
    kind = str(kind or "PRODUCT_POS").strip().upper()
    if kind == "SUM_POWER":
        return _sum_power_curve(sum_signal, radius, local_dc)
    if kind == "PRODUCT_ABS":
        return _product_abs_curve(product_signal, radius, local_dc)
    if kind == "BIPOLAR_GUIDED":
        return _bipolar_guided_curve(sum_signal, product_signal, radius, local_dc)
    if kind == "BIPOLAR_PAIR":
        return _bipolar_pair_curve(sum_signal, radius, local_dc)
    if kind == "BIPOLAR_P2P":
        return _bipolar_curves(sum_signal, radius, local_dc)[0]
    if kind == "BIPOLAR_ENERGY":
        return _bipolar_curves(sum_signal, radius, local_dc)[1]
    if kind == "COMBINED":
        sum_curve = _sum_power_curve(sum_signal, radius, local_dc)
        pos_curve = _product_pos_curve(product_signal, radius, local_dc)
        return (sum_curve / _robust_floor(sum_curve)) + (pos_curve / _robust_floor(pos_curve))
    return _product_pos_curve(product_signal, radius, local_dc)


def analyze_phase_pair_v7(
    even_u16: np.ndarray,
    odd_u16: np.ndarray,
    max_shift: int = 12,
    energy_radius: int = 12,
    dc_mode: str = "OFF",
    score_kind: str = "PRODUCT_POS",
) -> dict[str, Any] | None:
    """Analyze one even/odd pair for the experimental mode 7 algorithm."""

    try:
        n = min(int(len(even_u16)), int(len(odd_u16)))
    except Exception:
        n = 0
    if n <= 1:
        return None
    max_shift = min(abs(int(max_shift or 0)), n - 1)
    energy_radius = max(0, int(energy_radius or 0))
    dc_mode = str(dc_mode or "OFF").strip().upper()
    if dc_mode not in ("OFF", "LOCAL", "HIGHPASS", "BOTH"):
        dc_mode = "OFF"
    score_kind = str(score_kind or "PRODUCT_POS").strip().upper()
    if score_kind not in SCORE_KINDS:
        score_kind = "PRODUCT_POS"

    even_signed = np.asarray(even_u16[:n], dtype=np.float64) - float(ADC_CENTER)
    odd_signed = np.asarray(odd_u16[:n], dtype=np.float64) - float(ADC_CENTER)
    odd_inv = -odd_signed
    even_work = even_signed.copy()
    odd_work = odd_inv.copy()
    if dc_mode in ("HIGHPASS", "BOTH"):
        even_work = _highpass(even_work, energy_radius)
        odd_work = _highpass(odd_work, energy_radius)
    local_dc = dc_mode in ("LOCAL", "BOTH")

    best: dict[str, Any] | None = None
    for shift in range(-max_shift, max_shift + 1):
        odd_inv_aligned = _shift_like_mode6(odd_work, shift, n)
        sum_signal = even_work + odd_inv_aligned
        product_signal = even_work * odd_inv_aligned
        curve = _energy_curve(sum_signal, product_signal, score_kind, energy_radius, local_dc)
        if curve.size <= 0:
            continue
        peak_idx = int(np.nanargmax(curve))
        best_energy = float(curve[peak_idx])
        if not math.isfinite(best_energy):
            continue
        if (
            best is None
            or best_energy > float(best["best_energy"])
            or (
                best_energy == float(best["best_energy"])
                and abs(int(shift)) < abs(int(best["best_shift"]))
            )
        ):
            floor = _robust_floor(curve)
            bipolar = _bipolar_stats(sum_signal, peak_idx, energy_radius, local_dc)
            best = {
                "best_shift": int(shift),
                "best_peak_idx": int(peak_idx),
                "best_energy": float(best_energy),
                "noise_floor": float(floor),
                "score_norm": float(best_energy / floor) if floor > 0 else 0.0,
                "energy_curve": curve,
                "odd_inv_aligned": odd_inv_aligned,
                "sum_signal": sum_signal,
                "product_signal": product_signal,
                "bipolar": bipolar,
            }
    if best is None:
        return None
    return {
        "even_signed": even_signed,
        "odd_signed": odd_signed,
        "odd_inv": odd_inv,
        "best_shift": int(best["best_shift"]),
        "odd_inv_aligned": best["odd_inv_aligned"],
        "sum_signal": best["sum_signal"],
        "product_signal": best["product_signal"],
        "energy_curve": best["energy_curve"],
        "best_peak_idx": int(best["best_peak_idx"]),
        "best_energy": float(best["best_energy"]),
        "noise_floor": float(best["noise_floor"]),
        "score_norm": float(best["score_norm"]),
        "sum_max_idx": int(best["bipolar"]["sum_max_idx"]),
        "sum_min_idx": int(best["bipolar"]["sum_min_idx"]),
        "sum_mid_idx": float(best["bipolar"]["sum_mid_idx"]),
        "sum_lobe_gap": int(best["bipolar"]["sum_lobe_gap"]),
        "sum_max_value": float(best["bipolar"]["sum_max_value"]),
        "sum_min_value": float(best["bipolar"]["sum_min_value"]),
        "sum_p2p": float(best["bipolar"]["sum_p2p"]),
        "dc_filter_mode": dc_mode,
        "score_kind": score_kind,
    }


def _scalar(data: Any, name: str, default: int = 0) -> int:
    try:
        return int(np.asarray(data[name]).reshape(-1)[0])
    except Exception:
        return int(default)


def _str_scalar(data: Any, name: str, default: str = "") -> str:
    try:
        return str(np.asarray(data[name]).reshape(-1)[0])
    except Exception:
        return str(default)


def _merge_channel(prev: str, channel: str) -> str:
    prev = _label_channel_value(prev)
    channel = _label_channel_value(channel)
    if not prev:
        return channel
    if not channel:
        return prev
    if prev == channel:
        return prev
    return "both"


def _label_channel_value(value: Any) -> str:
    try:
        raw = str(value or "").strip().lower()
    except Exception:
        raw = ""
    if raw in ("both", "all", "u+l", "l+u", "upper+lower", "lower+upper", "upper,lower", "lower,upper", "2"):
        return "both"
    if raw in ("upper", "u", "0", "adc0", "ch0"):
        return "upper"
    if raw in ("lower", "l", "1", "adc1", "ch1"):
        return "lower"
    return ""


def _load_labels(data: Any) -> dict[str, Any]:
    marks_by_packet: dict[int, str] = {}
    mark_text_by_packet: dict[int, str] = {}
    ranges_by_packet: dict[int, dict[str, list[dict[str, int]]]] = {}
    deleted_ranges_by_packet: dict[int, dict[str, list[dict[str, int]]]] = {}
    payload: dict[str, Any] = {}
    try:
        if "user_labels_json" in set(getattr(data, "files", []) or []):
            raw = np.asarray(data["user_labels_json"]).reshape(-1)[0]
            payload = json.loads(str(raw or "{}"))
    except Exception:
        payload = {}
    for item in payload.get("marks", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            packet_idx = int(item.get("packet_idx", -1))
        except Exception:
            continue
        if packet_idx < 0:
            continue
        mark = str(item.get("mark", "") or "").strip().lower()
        channel = _label_channel_value(item.get("channel", item.get("tag_channel", "")))
        if mark == "tag" and channel:
            marks_by_packet[packet_idx] = _merge_channel(marks_by_packet.get(packet_idx, ""), channel)
            mark_text_by_packet[packet_idx] = "tag"
        elif mark:
            mark_text_by_packet[packet_idx] = mark[:32]
    for item in payload.get("ranges", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            packet_idx = int(item.get("packet_idx", -1))
            start = int(item.get("start", 0) or 0)
            end = int(item.get("end", start + 1) or (start + 1))
            peak = int(item.get("peak", (start + end) // 2) or ((start + end) // 2))
        except Exception:
            continue
        if packet_idx < 0:
            continue
        if end < start:
            start, end = end, start
        if end <= start:
            end = start + 1
        channel = _label_channel_value(item.get("channel", "upper")) or "upper"
        if channel == "both":
            target_channels = ("upper", "lower")
        else:
            target_channels = (channel,)
        for ch_name in target_channels:
            ranges_by_packet.setdefault(packet_idx, {}).setdefault(ch_name, []).append(
                {"start": max(0, start), "end": max(0, end), "peak": max(0, peak)}
            )
            marks_by_packet[packet_idx] = _merge_channel(marks_by_packet.get(packet_idx, ""), ch_name)
            mark_text_by_packet.setdefault(packet_idx, "tag")
    for item in payload.get("deleted_ranges", []) or payload.get("deleted_markers", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            packet_idx = int(item.get("packet_idx", -1))
            start = int(item.get("start", 0) or 0)
            end = int(item.get("end", start + 1) or (start + 1))
            peak = int(item.get("peak", (start + end) // 2) or ((start + end) // 2))
        except Exception:
            continue
        if packet_idx < 0:
            continue
        if end < start:
            start, end = end, start
        if end <= start:
            end = start + 1
        channel = _label_channel_value(item.get("channel", "upper")) or "upper"
        if channel == "both":
            target_channels = ("upper", "lower")
        else:
            target_channels = (channel,)
        for ch_name in target_channels:
            deleted_ranges_by_packet.setdefault(packet_idx, {}).setdefault(ch_name, []).append(
                {"start": max(0, start), "end": max(0, end), "peak": max(0, peak)}
            )
    return {
        "payload": payload,
        "marks_by_packet": marks_by_packet,
        "mark_text_by_packet": mark_text_by_packet,
        "ranges_by_packet": ranges_by_packet,
        "deleted_ranges_by_packet": deleted_ranges_by_packet,
    }


def _first_range(ranges_by_packet: dict[int, dict[str, list[dict[str, int]]]], packet_idx: int, channel: str) -> dict[str, int] | None:
    ranges = (ranges_by_packet.get(int(packet_idx), {}) or {}).get(channel, []) or []
    return ranges[0] if ranges else None


def _peak_in_ranges(
    ranges_by_packet: dict[int, dict[str, list[dict[str, int]]]],
    packet_idx: int,
    channel: str,
    peak_idx: int,
    tolerance: int = 2,
) -> bool:
    ranges = (ranges_by_packet.get(int(packet_idx), {}) or {}).get(channel, []) or []
    for item in ranges:
        start = int(item.get("start", 0)) - int(tolerance)
        end = int(item.get("end", 0)) + int(tolerance)
        if start <= int(peak_idx) <= end:
            return True
    return False


def _is_labeled_channel(label_channel: str, channel: str) -> bool:
    label_channel = _label_channel_value(label_channel)
    return bool(label_channel == "both" or label_channel == channel)


def _fmt_float(value: Any, digits: int = 6) -> str:
    try:
        val = float(value)
    except Exception:
        return ""
    if not math.isfinite(val):
        return ""
    return f"{val:.{digits}g}"


def _summarize_score(rows: list[dict[str, Any]], score_kind: str, channel: str) -> dict[str, Any]:
    norm_key = f"v7_{score_kind.lower()}_{channel}_norm"
    peak_key = f"v7_{score_kind.lower()}_{channel}_peak"
    labeled: list[float] = []
    unlabeled: list[float] = []
    peak_hits = 0
    peak_total = 0
    for row in rows:
        raw = row.get(norm_key, "")
        try:
            score = float(raw)
        except Exception:
            continue
        if not math.isfinite(score):
            continue
        is_label = _is_labeled_channel(str(row.get("label_channel", "")), channel)
        if is_label:
            labeled.append(score)
            has_range = str(row.get(f"{channel}_range_start", "")) != ""
            if has_range:
                peak_total += 1
                if str(row.get(f"v7_{score_kind.lower()}_{channel}_peak_in_range", "")) == "1":
                    peak_hits += 1
        else:
            unlabeled.append(score)
    thresholds = sorted(set(labeled + unlabeled))
    best: dict[str, Any] = {
        "threshold": 0.0,
        "tp": 0,
        "fp": 0,
        "fn": len(labeled),
        "tn": len(unlabeled),
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
    }
    for threshold in thresholds:
        tp = sum(1 for score in labeled if score >= threshold)
        fp = sum(1 for score in unlabeled if score >= threshold)
        fn = len(labeled) - tp
        tn = len(unlabeled) - fp
        precision = float(tp) / float(tp + fp) if (tp + fp) else 0.0
        recall = float(tp) / float(tp + fn) if (tp + fn) else 0.0
        f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        better = False
        if f1 > float(best["f1"]):
            better = True
        elif f1 == float(best["f1"]):
            if fp < int(best["fp"]):
                better = True
            elif fp == int(best["fp"]) and recall > float(best["recall"]):
                better = True
        if better:
            best = {
                "threshold": float(threshold),
                "tp": int(tp),
                "fp": int(fp),
                "fn": int(fn),
                "tn": int(tn),
                "precision": float(precision),
                "recall": float(recall),
                "f1": float(f1),
            }
    no_false_threshold = (max(unlabeled) + 1e-9) if unlabeled else 0.0
    no_false_hits = sum(1 for score in labeled if score >= no_false_threshold)
    missed_at_best = []
    for row in rows:
        if not _is_labeled_channel(str(row.get("label_channel", "")), channel):
            continue
        try:
            score = float(row.get(norm_key, ""))
        except Exception:
            continue
        if math.isfinite(score) and score < float(best["threshold"]):
            missed_at_best.append(int(row["packet_idx"]))
    labeled_arr = np.asarray(labeled, dtype=np.float64)
    unlabeled_arr = np.asarray(unlabeled, dtype=np.float64)
    return {
        "score_kind": score_kind,
        "channel": channel,
        "labeled_count": int(len(labeled)),
        "unlabeled_count": int(len(unlabeled)),
        "labeled_median": float(np.median(labeled_arr)) if labeled_arr.size else 0.0,
        "labeled_min": float(np.min(labeled_arr)) if labeled_arr.size else 0.0,
        "unlabeled_median": float(np.median(unlabeled_arr)) if unlabeled_arr.size else 0.0,
        "unlabeled_max": float(np.max(unlabeled_arr)) if unlabeled_arr.size else 0.0,
        "best_f1_threshold": best,
        "no_false_threshold": float(no_false_threshold),
        "no_false_labeled_hits": int(no_false_hits),
        "peak_in_range": {"hits": int(peak_hits), "total": int(peak_total)},
        "missed_labeled_packets_at_best_threshold": missed_at_best[:50],
    }


def _pick_recommendations(score_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for item in score_summaries:
        by_kind.setdefault(str(item["score_kind"]), []).append(item)
    ranking = []
    for kind, items in by_kind.items():
        if not items:
            continue
        mean_f1 = float(np.mean([float(x["best_f1_threshold"]["f1"]) for x in items]))
        mean_recall = float(np.mean([float(x["best_f1_threshold"]["recall"]) for x in items]))
        fp_total = int(sum(int(x["best_f1_threshold"]["fp"]) for x in items))
        peak_hits = int(sum(int(x["peak_in_range"]["hits"]) for x in items))
        peak_total = int(sum(int(x["peak_in_range"]["total"]) for x in items))
        peak_rate = float(peak_hits) / float(peak_total) if peak_total else 0.0
        ranking.append(
            {
                "score_kind": kind,
                "mean_f1": mean_f1,
                "mean_recall": mean_recall,
                "fp_total": fp_total,
                "peak_in_range_rate": peak_rate,
            }
        )
    ranking.sort(key=lambda x: (-float(x["mean_f1"]), int(x["fp_total"]), -float(x["mean_recall"])))
    return {
        "ranking": ranking,
        "recommended_primary_score": ranking[0]["score_kind"] if ranking else "",
        "recommended_diagnostic_score": ranking[1]["score_kind"] if len(ranking) > 1 else "",
    }


def analyze_recording(recording_path: Path, max_shift: int, energy_radius: int, dc_mode: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    with np.load(recording_path, allow_pickle=False) as data:
        samples = np.asarray(data["samples"], dtype=np.uint16)
        lengths = np.asarray(data["lengths"], dtype=np.int32)
        channels = np.asarray(data["channels"], dtype=np.uint8)
        phases = np.asarray(data["phases"], dtype=np.uint8)
        packet_count = int(len(lengths))
        labels = _load_labels(data)
        marks_by_packet = labels["marks_by_packet"]
        mark_text_by_packet = labels["mark_text_by_packet"]
        ranges_by_packet = labels["ranges_by_packet"]
        deleted_ranges_by_packet = labels["deleted_ranges_by_packet"]
        state: dict[tuple[int, int], np.ndarray] = {}
        state_len: dict[tuple[int, int], int] = {}
        baseline_latest: dict[int, dict[str, Any] | None] = {0: None, 1: None}
        v7_latest: dict[tuple[str, int], dict[str, Any] | None] = {}
        shift_penalty = _float_env("BMI30_RT_SHIFT_SCORE_PENALTY", 0.02, 0.0)

        for packet_idx in range(packet_count):
            n = max(0, min(int(lengths[packet_idx]), int(samples.shape[1])))
            src_ch = 0 if int(channels[packet_idx]) == 0 else 1
            phase = int(phases[packet_idx]) & 1
            if n > 0:
                state[(src_ch, phase)] = np.array(samples[packet_idx, :n], dtype=np.uint16, copy=True)
                state_len[(src_ch, phase)] = int(n)
            base_len = max(state_len.values(), default=n)
            label_channel = str(marks_by_packet.get(packet_idx, "") or "")
            mark = str(mark_text_by_packet.get(packet_idx, "") or "")
            row: dict[str, Any] = {
                "packet_idx": int(packet_idx),
                "source_channel": CHANNEL_NAMES[src_ch],
                "source_phase": int(phase),
                "label_channel": label_channel,
                "mark": mark,
                "base_len": int(base_len),
                "deleted_range_count": int(sum(len(v) for v in (deleted_ranges_by_packet.get(packet_idx, {}) or {}).values())),
            }
            for ch_idx, ch_name in enumerate(CHANNEL_NAMES):
                range_item = _first_range(ranges_by_packet, packet_idx, ch_name)
                row[f"{ch_name}_range_start"] = "" if range_item is None else int(range_item["start"])
                row[f"{ch_name}_range_end"] = "" if range_item is None else int(range_item["end"])
                row[f"{ch_name}_range_peak"] = "" if range_item is None else int(range_item["peak"])
                even = state.get((ch_idx, 0))
                odd = state.get((ch_idx, 1))
                if even is None or odd is None:
                    row[f"old_level_{ch_name}"] = ""
                    row[f"old_peak_{ch_name}"] = ""
                    row[f"old_shift_{ch_name}"] = ""
                    row[f"old_peak_value_{ch_name}"] = ""
                    for kind in SCORE_KINDS:
                        prefix = f"v7_{kind.lower()}_{ch_name}"
                        row[f"{prefix}_score"] = ""
                        row[f"{prefix}_norm"] = ""
                        row[f"{prefix}_peak"] = ""
                        row[f"{prefix}_shift"] = ""
                        row[f"{prefix}_noise"] = ""
                        row[f"{prefix}_sum_max"] = ""
                        row[f"{prefix}_sum_min"] = ""
                        row[f"{prefix}_sum_mid"] = ""
                        row[f"{prefix}_sum_gap"] = ""
                        row[f"{prefix}_sum_p2p"] = ""
                        row[f"{prefix}_peak_in_range"] = ""
                    continue
                made = mode6_product_current_prev(even, odd, int(max_shift), float(shift_penalty))
                baseline_latest[ch_idx] = made
                row[f"old_level_{ch_name}"] = "" if made is None else int(made["level"])
                row[f"old_peak_{ch_name}"] = "" if made is None else int(made["peak_idx"])
                row[f"old_shift_{ch_name}"] = "" if made is None else int(made["shift"])
                row[f"old_peak_value_{ch_name}"] = "" if made is None else _fmt_float(made["peak_value"], 8)
                for kind in SCORE_KINDS:
                    prefix = f"v7_{kind.lower()}_{ch_name}"
                    res = analyze_phase_pair_v7(even, odd, int(max_shift), int(energy_radius), dc_mode, kind)
                    v7_latest[(kind, ch_idx)] = res
                    if res is None:
                        row[f"{prefix}_score"] = ""
                        row[f"{prefix}_norm"] = ""
                        row[f"{prefix}_peak"] = ""
                        row[f"{prefix}_shift"] = ""
                        row[f"{prefix}_noise"] = ""
                        row[f"{prefix}_sum_max"] = ""
                        row[f"{prefix}_sum_min"] = ""
                        row[f"{prefix}_sum_mid"] = ""
                        row[f"{prefix}_sum_gap"] = ""
                        row[f"{prefix}_sum_p2p"] = ""
                        row[f"{prefix}_peak_in_range"] = ""
                        continue
                    peak_idx = int(res["best_peak_idx"])
                    row[f"{prefix}_score"] = _fmt_float(res["best_energy"], 8)
                    row[f"{prefix}_norm"] = _fmt_float(res["score_norm"], 8)
                    row[f"{prefix}_peak"] = peak_idx
                    row[f"{prefix}_shift"] = int(res["best_shift"])
                    row[f"{prefix}_noise"] = _fmt_float(res["noise_floor"], 8)
                    row[f"{prefix}_sum_max"] = int(res.get("sum_max_idx", 0) or 0)
                    row[f"{prefix}_sum_min"] = int(res.get("sum_min_idx", 0) or 0)
                    row[f"{prefix}_sum_mid"] = _fmt_float(res.get("sum_mid_idx", 0.0), 4)
                    row[f"{prefix}_sum_gap"] = int(res.get("sum_lobe_gap", 0) or 0)
                    row[f"{prefix}_sum_p2p"] = _fmt_float(res.get("sum_p2p", 0.0), 8)
                    row[f"{prefix}_peak_in_range"] = "1" if _peak_in_ranges(ranges_by_packet, packet_idx, ch_name, peak_idx) else "0"
            rows.append(row)
            baseline_rows.append(
                {
                    "packet_idx": row["packet_idx"],
                    "label_channel": row["label_channel"],
                    "old_level_upper": row.get("old_level_upper", ""),
                    "old_level_lower": row.get("old_level_lower", ""),
                    "old_peak_upper": row.get("old_peak_upper", ""),
                    "old_peak_lower": row.get("old_peak_lower", ""),
                    "old_shift_upper": row.get("old_shift_upper", ""),
                    "old_shift_lower": row.get("old_shift_lower", ""),
                    "old_peak_value_upper": row.get("old_peak_value_upper", ""),
                    "old_peak_value_lower": row.get("old_peak_value_lower", ""),
                }
            )

        score_summaries = []
        for kind in SCORE_KINDS:
            for channel in CHANNEL_NAMES:
                score_summaries.append(_summarize_score(rows, kind, channel))
        recommendations = _pick_recommendations(score_summaries)
        metadata = {
            "recording": str(recording_path),
            "schema": _scalar(data, "schema"),
            "source": _str_scalar(data, "source"),
            "created_at": _str_scalar(data, "created_at"),
            "packet_count": packet_count,
            "freq_hz": _scalar(data, "freq_hz"),
            "stream_mode": _scalar(data, "stream_mode"),
            "mode_selected": _scalar(data, "mode_selected"),
            "avg_n": _scalar(data, "avg_n"),
            "max_shift": int(max_shift),
            "energy_radius": int(energy_radius),
            "dc_mode": str(dc_mode).upper(),
            "shift_penalty": float(shift_penalty),
            "label_mark_count": int(len(marks_by_packet)),
            "label_range_packet_count": int(len(ranges_by_packet)),
        }
    return {
        "metadata": metadata,
        "rows": rows,
        "baseline_rows": baseline_rows,
        "score_summaries": score_summaries,
        "recommendations": recommendations,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        seen: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.append(key)
        fieldnames = seen
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_summary(path: Path, result: dict[str, Any], scores_csv: Path, baseline_csv: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "metadata": result["metadata"],
        "outputs": {
            "scores_csv": str(scores_csv),
            "baseline_csv": str(baseline_csv),
        },
        "score_summaries": result["score_summaries"],
        "recommendations": result["recommendations"],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", nargs="?", default=str(DEFAULT_RECORDING), help="NPZ raw player recording")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Directory for CSV/JSON outputs")
    parser.add_argument("--max-shift", type=int, default=_int_env("BMI30_RT_DET_MAX_SHIFT", 12, 0, 80))
    parser.add_argument("--energy-radius", type=int, default=12)
    parser.add_argument("--dc-mode", choices=("OFF", "LOCAL", "HIGHPASS", "BOTH"), default="OFF")
    args = parser.parse_args()

    recording_path = Path(args.recording)
    if not recording_path.exists():
        raise SystemExit(f"recording not found: {recording_path}")
    out_dir = Path(args.out_dir)
    stem = recording_path.stem
    tag = f"shift{int(args.max_shift)}_r{int(args.energy_radius)}_{str(args.dc_mode).lower()}"
    scores_csv = out_dir / f"{stem}_mode7_scores_{tag}.csv"
    baseline_csv = out_dir / f"{stem}_mode6_baseline_{tag}.csv"
    summary_json = out_dir / f"{stem}_mode7_summary_{tag}.json"

    result = analyze_recording(recording_path, int(args.max_shift), int(args.energy_radius), str(args.dc_mode))
    _write_csv(baseline_csv, result["baseline_rows"])
    _write_csv(scores_csv, result["rows"])
    _write_summary(summary_json, result, scores_csv, baseline_csv)

    rec = result["recommendations"]
    print(f"recording: {recording_path}")
    print(f"packets: {result['metadata']['packet_count']}")
    print(f"baseline_csv: {baseline_csv}")
    print(f"scores_csv: {scores_csv}")
    print(f"summary_json: {summary_json}")
    print(f"recommended_primary_score: {rec.get('recommended_primary_score', '')}")
    print(f"recommended_diagnostic_score: {rec.get('recommended_diagnostic_score', '')}")
    for item in rec.get("ranking", []):
        print(
            "score_rank:"
            f" {item['score_kind']}"
            f" mean_f1={float(item['mean_f1']):.4f}"
            f" mean_recall={float(item['mean_recall']):.4f}"
            f" fp_total={int(item['fp_total'])}"
            f" peak_in_range={float(item['peak_in_range_rate']):.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
