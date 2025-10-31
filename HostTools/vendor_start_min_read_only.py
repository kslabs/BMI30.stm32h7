#!/usr/bin/env python3
# Minimal vendor reader: configure, START, then read frames only (no GET_STATUS)

import time
import struct
import usb.core
import usb.util

VID = 0xCAFE
PID = 0x4001
IFACE_INDEX = 2
OUT_EP = 0x03
IN_EP  = 0x83
READ_TIMEOUT_MS = 3000
DURATION_S = 10.0
LOG_PATH = 'HostTools/min_read.log'

def main():
    # tiny logger
    def log(s: str):
        print(s)
        try:
            with open(LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(s + "\n")
        except Exception:
            pass

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        log("[MIN] Device not found")
        return 2
    try:
        dev.set_configuration()
    except Exception:
        pass
    # Find/claim interface
    claim_idx = IFACE_INDEX
    try:
        usb.util.claim_interface(dev, IFACE_INDEX)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=claim_idx, alternate_setting=1)
        time.sleep(0.1)
    except Exception:
        pass
    # Basic config: windows, rate, async=1, chmode=0(A-only)
    try:
        payload = struct.pack('<BHHHH', 0x10, 100, 300, 700, 300)
        dev.write(OUT_EP, payload, timeout=1000)
    except Exception:
        pass
    try:
        payload = struct.pack('<BH', 0x11, 200)
        dev.write(OUT_EP, payload, timeout=1000)
    except Exception:
        pass
    for cmd in [(0x18, 0x01), (0x19, 0x00), (0x13, 0x01), (0x14, 0x02)]:
        try:
            dev.write(OUT_EP, bytes(cmd), timeout=1000)
        except Exception:
            pass
    # START
    dev.write(OUT_EP, bytes([0x20]), timeout=1000)
    log("[MIN] START sent; reading...")

    # Read loop
    start = time.time()
    rx = bytearray()
    cnt_a = cnt_b = cnt_stat = 0
    timeouts = 0
    while time.time() - start < DURATION_S:
        try:
            chunk = bytes(dev.read(IN_EP, 512, timeout=READ_TIMEOUT_MS))
            rx += chunk
            # Extract frames
            while True:
                if len(rx) < 4:
                    break
                if rx[0:4] == b'STAT':
                    flen = 64 if len(rx) >= 64 else (52 if len(rx) >= 52 else 0)
                    if flen == 0 or len(rx) < flen:
                        break
                    rx = rx[flen:]
                    cnt_stat += 1
                    continue
                if rx[0] == 0x5A and len(rx) >= 16 and rx[1] == 0xA5 and rx[2] == 0x01:
                    total_samples = rx[12] | (rx[13] << 8)
                    flen = 32 + total_samples * 2
                    if len(rx) < flen:
                        break
                    flags = rx[3]
                    ftype = 'A' if flags == 0x01 else ('B' if flags == 0x02 else 'X')
                    if ftype == 'A':
                        cnt_a += 1
                    elif ftype == 'B':
                        cnt_b += 1
                    rx = rx[flen:]
                    continue
                # resync
                idx_stat = rx.find(b'STAT')
                idx_hdr = rx.find(b"\x5A\xA5\x01")
                idx = -1
                if idx_stat != -1 and (idx_hdr == -1 or idx_stat < idx_hdr):
                    idx = idx_stat
                elif idx_hdr != -1:
                    idx = idx_hdr
                if idx > 0:
                    rx = rx[idx:]
                    continue
                break
        except usb.core.USBError as e:
            if getattr(e, 'errno', None) in (10060, 110, 60) or 'timed out' in str(e).lower():
                timeouts += 1
                continue
            log(f"[MIN][ERR] {e}")
            break

    # STOP
    try:
        dev.write(OUT_EP, bytes([0x21]), timeout=500)
    except Exception:
        pass
    log(f"[MIN][SUMMARY] A={cnt_a} B={cnt_b} STAT={cnt_stat} timeouts={timeouts}")

if __name__ == '__main__':
    main()
