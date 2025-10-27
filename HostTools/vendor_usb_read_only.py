#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read-only: claim Vendor interface (0x03/0x83) and read several IN packets from EP 0x83.
Does NOT send START. Useful when START is sent over CDC (e.g., COM7).

Requires WinUSB/libusb driver bound to Vendor interface on Windows.
"""
import os
import sys
import time
import usb.core
import usb.util

VID = int(os.getenv('VND_VID', '0xCAFE'), 16)
PID = int(os.getenv('VND_PID', '0x4001'), 16)
OUT_EP = 0x03
IN_EP  = 0x83
READ_COUNT = int(os.getenv('VND_READ_COUNT', '6'))
READ_TIMEOUT_MS = int(os.getenv('VND_READ_TIMEOUT', '1000'))
LOG_PATH = os.getenv('VND_HOST_LOG', 'HostTools/host_rx.log')

def log(s: str):
    print(s)
    try:
        d = os.path.dirname(LOG_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(s + "\n")
    except Exception:
        pass

def find_iface_with_eps(dev, out_ep=OUT_EP, in_ep=IN_EP):
    for cfg in dev:
        for intf in cfg:
            eps = [ep.bEndpointAddress for ep in intf]
            if out_ep in eps and in_ep in eps:
                return cfg, intf
    return None, None

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        log(f"[ERR] Device not found VID=0x{VID:04X} PID=0x{PID:04X}")
        sys.exit(1)

    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass

    cfg, intf = find_iface_with_eps(dev)
    if cfg is None:
        log("[ERR] Vendor interface (0x03/0x83) not found; check driver binding")
        sys.exit(2)

    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except Exception:
        pass
    usb.util.claim_interface(dev, intf.bInterfaceNumber)

    got = 0
    t0 = time.time()
    while got < READ_COUNT and (time.time() - t0) < 10:
        try:
            buf = dev.read(IN_EP, 512, timeout=READ_TIMEOUT_MS)
            ba = bytes(buf)
            head = ' '.join(f"{b:02X}" for b in ba[:4])
            ftype = 'UNK'
            if len(ba) >= 4:
                if ba[0:4] == b'STAT':
                    ftype = 'STAT'
                elif ba[0] == 0x5A and ba[1] == 0xA5 and ba[2] == 0x01:
                    flags = ba[3]
                    if flags & 0x80:
                        ftype = 'TEST'
                    elif flags == 0x01:
                        ftype = 'A'
                    elif flags == 0x02:
                        ftype = 'B'
            log(f"[HOST_RX] ep=0x{IN_EP:02X} len={len(ba)} type={ftype} head={head}")
            got += 1
        except usb.core.USBError as e:
            if e.errno is None and e.args and 'timed out' in str(e).lower():
                log("[HOST_RX] timeout")
                continue
            log(f"[HOST_RX][ERR] {e}")
            break

    try:
        usb.util.release_interface(dev, intf.bInterfaceNumber)
    except Exception:
        pass
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass

if __name__ == '__main__':
    main()
