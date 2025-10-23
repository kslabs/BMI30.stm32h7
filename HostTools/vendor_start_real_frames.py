#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Старт реальных (ADC) кадров в full_mode=1 с опциональным усечением числа выборок.
"""
import sys, time, argparse, usb.core, usb.util

VID = 0xCAFE
PID = 0x4001
IF_NUM = 2
EP_OUT = 0x03
EP_IN  = 0x83

CMD_SET_FULL_MODE      = 0x13
CMD_SET_PROFILE        = 0x14
CMD_SET_TRUNC_SAMPLES  = 0x16
CMD_START              = 0x20
CMD_STOP               = 0x21

HDR_SIZE = 32

class USBDev:
    def __init__(self):
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            raise SystemExit("Device not found")
        try: self.dev.set_configuration()
        except Exception: pass
        try: usb.util.claim_interface(self.dev, IF_NUM)
        except Exception: pass
    def write(self, data: bytes, timeout=500):
        return self.dev.write(EP_OUT, data, timeout)
    def read(self, size: int, timeout=200):
        try:
            return self.dev.read(EP_IN, size, timeout)
        except usb.core.USBTimeoutError:
            return None
        except usb.core.USBError as e:
            if getattr(e, 'errno', None) in (110, 10060):
                return None
            raise

def parse_frame(buf: bytes):
    if len(buf) < HDR_SIZE:
        return {"type":"SHORT","len":len(buf)}
    if buf[0:4] == b'STAT':
        return {"type":"STAT","len":len(buf)}
    magic = buf[0] | (buf[1]<<8)
    if magic != 0xA55A:
        return {"type":"RAW","len":len(buf)}
    flags = buf[3]
    seq = int.from_bytes(buf[4:8], 'little')
    ts  = int.from_bytes(buf[8:12], 'little')
    ns  = int.from_bytes(buf[12:14], 'little')
    frame_type = 'TEST' if (flags & 0x80) else ('ADC0' if (flags & 0x01) else ('ADC1' if (flags & 0x02) else 'UNK'))
    return {"type":frame_type,"seq":seq,"ns":ns,"ts":ts,"len":len(buf)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--trunc', type=int, default=0)
    ap.add_argument('--count', type=int, default=12)
    ap.add_argument('--profile', type=int, default=2)
    ap.add_argument('--no-stop', action='store_true')
    ap.add_argument('--first-timeout', type=float, default=2.0)
    args = ap.parse_args()

    dev = USBDev(); print("Device opened")
    dev.write(bytes([CMD_SET_FULL_MODE, 1])); time.sleep(0.01)
    prof = 1 if args.profile == 1 else 2
    dev.write(bytes([CMD_SET_PROFILE, prof])); time.sleep(0.01)

    if args.trunc:
        if args.trunc < 16:
            print("[WARN] trunc too small -> 16")
            args.trunc = 16
        lo = args.trunc & 0xFF; hi = (args.trunc >> 8) & 0xFF
        dev.write(bytes([CMD_SET_TRUNC_SAMPLES, lo, hi])); time.sleep(0.01)
        print(f"SET_TRUNC_SAMPLES {args.trunc}")

    dev.write(bytes([CMD_START])); print("START sent")

    shown = 0
    start_time = time.time()
    first_frame_deadline = start_time + args.first_timeout
    last_info_print = 0.0

    while shown < args.count:
        pkt = dev.read(2048, timeout=300)
        if pkt is None:
            now = time.time()
            if shown == 0 and now > first_frame_deadline:
                print("[ERROR] no frames received in first-timeout window")
                break
            if now - last_info_print > 1.2 and shown == 0:
                print("[INFO] waiting for first frame...")
                last_info_print = now
            continue
        pkt = bytes(pkt)
        info = parse_frame(pkt)
        t = info.get('type')
        if t == 'STAT':
            continue
        print(f"[{shown+1}] {t} seq={info.get('seq')} ns={info.get('ns')} len={info.get('len')} ts={info.get('ts')}")
        shown += 1

    dt = time.time() - start_time
    print(f"Captured {shown} frames in {dt:.2f}s")

    if not args.no_stop:
        dev.write(bytes([CMD_STOP])); print("STOP sent")
    # Ошибочный сценарий: ни одного кадра
    if shown == 0:
        print("[FAIL] no data frames received")
        sys.exit(2)

if __name__ == '__main__':
    main()
