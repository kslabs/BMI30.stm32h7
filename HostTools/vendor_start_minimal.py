#!/usr/bin/env python3
# Minimal START + read A-only if firmware defaults permit; avoids any pre-START OUT writes beyond START.
import usb.core, usb.util, time, os, sys, argparse, struct
VID = int(os.getenv('VND_VID','0xCAFE'),16)
PID = int(os.getenv('VND_PID','0x4001'),16)
IN_EP = int(os.getenv('VND_EP_IN','0x83'),16)
OUT_EP = int(os.getenv('VND_EP_OUT','0x03'),16)
IFACE = int(os.getenv('VND_INTF','2'))
TIMEOUT = int(os.getenv('VND_READ_TIMEOUT','3000'))
ap = argparse.ArgumentParser(description='Minimal START+read for vendor IF#2')
ap.add_argument('--window', type=float, default=float(os.getenv('VND_READ_WINDOW_SEC','10')), help='Read window seconds')
args = ap.parse_args()
try:
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print('[MIN][ERR] device not found')
        sys.exit(1)
    try:
        dev.set_configuration()
    except Exception:
        pass
    # Claim/alt=1
    try:
        usb.util.claim_interface(dev, IFACE)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=IFACE, alternate_setting=1)
    except Exception as e:
        print('[MIN][WARN] SetInterface alt=1:', e)
    time.sleep(0.15)
    # Configure windows minimally, then START
    try:
        payload = struct.pack('<BHHHH', 0x10, 100, 300, 700, 300)
        dev.write(OUT_EP, payload, timeout=1000)
        print('[MIN] SET_WINDOWS sent')
        time.sleep(0.02)
    except Exception as e:
        print('[MIN][WARN] SET_WINDOWS failed:', e)
    # A-only + async
    try:
        dev.write(OUT_EP, bytes([0x18, 0x01]), timeout=1000)
        print('[MIN] SET_ASYNC=1 sent')
        time.sleep(0.01)
        dev.write(OUT_EP, bytes([0x19, 0x00]), timeout=1000)
        print('[MIN] SET_CHMODE=A-only sent')
        time.sleep(0.01)
    except Exception as e:
        print('[MIN][WARN] SET_ASYNC/CHMODE failed:', e)
    dev.write(OUT_EP, bytes([0x20]), timeout=1000)
    print('[MIN] START sent')
    t0 = time.time(); n_a = n_b = n_stat = 0
    buf = bytearray()
    last = t0
    while time.time() - t0 < args.window:
        try:
            chunk = bytes(dev.read(IN_EP, 512, timeout=TIMEOUT))
            buf += chunk
            while True:
                if len(buf) < 4: break
                if buf[:4] == b'STAT':
                    if len(buf) < 52: break
                    n_stat += 1; buf = buf[52:]
                    continue
                if buf[0:3] == b"\x5A\xA5\x01":
                    if len(buf) < 16: break
                    samples = buf[12] | (buf[13]<<8)
                    l = 32 + samples*2
                    if len(buf) < l: break
                    fl = buf[3]
                    if fl == 0x01: n_a += 1
                    elif fl == 0x02: n_b += 1
                    buf = buf[l:]
                    continue
                i_stat = buf.find(b'STAT')
                i_hdr = buf.find(b"\x5A\xA5\x01")
                i = i_stat if (i_stat != -1 and (i_hdr == -1 or i_stat < i_hdr)) else i_hdr
                if i > 0: buf = buf[i:]; continue
                break
        except usb.core.USBError as e:
            pass
        now = time.time()
        if now - last >= 1.0:
            last = now
            print(f"[MIN][TICK] t={now-t0:.1f}s A={n_a} B={n_b} STAT={n_stat}")
            sys.stdout.flush()
    print(f"[MIN][SUMMARY] A={n_a} B={n_b} STAT={n_stat}")
    sys.exit(0)
except Exception as e:
    print('[MIN][ERR]', e)
    sys.exit(2)
