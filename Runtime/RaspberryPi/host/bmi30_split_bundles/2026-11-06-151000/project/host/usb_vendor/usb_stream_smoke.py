#!/usr/bin/env python3
from __future__ import annotations
import sys, time, os

# Используем наш транспорт USBStream, чтобы проверить, что поток реально приходит
try:
    from usb_vendor.usb_stream import USBStream
except Exception as e:
    # добавим родительскую папку в sys.path и попробуем ещё раз
    try:
        sys.path.append(os.path.dirname(os.path.dirname(__file__)))
        from usb_vendor.usb_stream import USBStream
    except Exception as e2:
        print(f"[ERR] cannot import USBStream: {e2}")
        sys.exit(2)


def main():
    us = USBStream(profile=1, full=True)
    print("[smoke] opened")
    pairs = 0
    t0 = time.time()
    # Ждём до 5 секунд первые 3 пары
    while time.time() - t0 < 5.0 and pairs < 3:
        p = us.get_stereo(timeout=0.7)
        if not p:
            continue
        a, b = p
        print(f"[smoke] pair seq={a.seq} samplesA={a.samples} samplesB={b.samples}")
        pairs += 1
        if pairs == 1:
            # маленькая пауза, чтобы успел прийти ещё кадр
            time.sleep(0.2)
    print(f"[smoke] result pairs={pairs} test_seen={getattr(us,'test_seen',None)} stat_len={(0 if getattr(us,'last_stat',None) is None else len(us.last_stat))}")
    try:
        us.close()
    except Exception:
        pass
    print("[smoke] closed")
    return 0 if pairs >= 1 else 1


if __name__ == '__main__':
    raise SystemExit(main())
