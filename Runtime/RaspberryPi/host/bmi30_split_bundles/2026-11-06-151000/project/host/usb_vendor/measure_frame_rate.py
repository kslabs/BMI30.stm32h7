#!/usr/bin/env python3
"""measure_frame_rate.py — измеряет частоту прихода стерео-кадров от Vendor Bulk интерфейса.

Usage:
  python -m usb_vendor.measure_frame_rate --duration 15 --warmup 2 --profile 1

Выводит:
  - Общее число стереопар
  - FPS (средний, min/max, p50/p90/p99 интервалы)
  - Кол-во пауз > X секунд
  - Примеры первых байт payload для A/B (опц.)
"""
from __future__ import annotations
import argparse, time, statistics as stats

try:
    from .usb_stream import USBStream  # type: ignore
except Exception:
    from usb_stream import USBStream  # type: ignore


def percentiles(values: list[float], qs=(50,90,99)):
    if not values:
        return {q: None for q in qs}
    s = sorted(values)
    out = {}
    for q in qs:
        k = (len(s)-1) * (q/100.0)
        f = int(k)
        c = min(f+1, len(s)-1)
        if f == c:
            out[q] = s[f]
        else:
            out[q] = s[f] + (s[c]-s[f])*(k-f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--duration', type=float, default=15.0, help='Длительность измерения, сек')
    ap.add_argument('--warmup', type=float, default=2.0, help='Прогрев, сек (кадры не считаются)')
    ap.add_argument('--profile', type=int, default=1, help='1=200 Гц, 2=300 Гц (если поддерживается)')
    ap.add_argument('--full', action='store_true', help='Включить full mode при старте')
    ap.add_argument('--frame-samples', type=int, default=None, help='Запросить размер кадра (u16) перед START, например 10 для ~20 FPS @200Hz')
    ap.add_argument('--show-bytes', action='store_true', help='Печатать первые 16 int16 из A/B для первых кадров')
    ap.add_argument('--pause-threshold', type=float, default=5.0, help='Порог длинной паузы, сек')
    args = ap.parse_args()

    stream = USBStream(profile=args.profile, full=args.full, test_as_data=False, frame_samples=args.frame_samples)
    print(f"[open] stream profile={args.profile} full={args.full} ns={args.frame_samples}")

    t0 = time.time()
    warm_until = t0 + args.warmup
    end_t = t0 + args.duration + args.warmup

    last_pair_t = None
    intervals = []
    pauses = 0
    pairs = 0

    shown = 0

    try:
        while True:
            now = time.time()
            if now >= end_t:
                break
            pair = stream.get_stereo(timeout=0.5)
            if not pair:
                continue
            a, b = pair
            now = time.time()
            if now < warm_until:
                last_pair_t = now
                continue
            pairs += 1
            if args.show_bytes and shown < 2:
                import numpy as np
                ch0 = np.frombuffer(a.payload, dtype='<i2')
                ch1 = np.frombuffer(b.payload, dtype='<i2')
                print('[A] len=', len(ch0), 'first16=', ch0[:16].tolist())
                print('[B] len=', len(ch1), 'first16=', ch1[:16].tolist())
                shown += 1
            if last_pair_t is not None:
                dt = now - last_pair_t
                intervals.append(dt)
                if dt > args.pause_threshold:
                    pauses += 1
            last_pair_t = now
    finally:
        try:
            stream.close()
        except Exception:
            pass

    if intervals:
        avg = sum(intervals)/len(intervals)
        mn = min(intervals)
        mx = max(intervals)
        p = percentiles(intervals)
        fps = 1.0/avg if avg > 0 else 0.0
        print(f"pairs={pairs} fps_avg={fps:.2f} dt_avg={avg*1000:.1f}ms dt_min={mn*1000:.1f}ms dt_max={mx:.1f}s pauses>{args.pause_threshold}s={pauses}")
        print(f"percentiles: p50={p[50]:.3f}s p90={p[90]:.3f}s p99={p[99]:.3f}s")
    else:
        print(f"pairs={pairs} (интервалы не набраны). Возможно, пришёл только TEST/STAT или поток не стартовал.")

if __name__ == '__main__':
    main()
