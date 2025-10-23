#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enable diagnostic mode (SET_FULL_MODE=0) on the USB Vendor interface,
send START, then read several IN packets and print brief info.

Requires: pyusb (pip install pyusb) and WinUSB/libusb bound to Vendor interface on Windows.
"""
import sys
import time
import usb.core
import usb.util

VID = 0xCAFE
PID = 0x4001
OUT_EP = 0x03
IN_EP  = 0x83
READ_COUNT = 10
READ_TIMEOUT_MS = 1000

CMD_SET_FULL_MODE = 0x13
CMD_SET_BLOCK_HZ  = 0x11
CMD_START         = 0x20
CMD_GET_STATUS    = 0x30


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
        print(f"[ERR] Device not found VID=0x{VID:04X} PID=0x{PID:04X}")
        sys.exit(1)
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    cfg, intf = find_iface_with_eps(dev)
    if cfg is None:
        print("[ERR] Vendor interface (0x03/0x83) not found; check driver binding")
        sys.exit(2)
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except Exception:
        pass
    usb.util.claim_interface(dev, intf.bInterfaceNumber)

    # Enable diagnostic mode and set a moderate block rate
    try:
        dev.write(OUT_EP, bytes([CMD_SET_FULL_MODE, 0x00]), timeout=500)
        print("[HOST] SET_FULL_MODE(0=DIAG) written")
    except Exception as e:
        print(f"[HOST][WARN] SET_FULL_MODE failed: {e}")
    try:
        dev.write(OUT_EP, bytes([CMD_SET_BLOCK_HZ, 60, 0x00]), timeout=500)  # 60 Hz
        print("[HOST] SET_BLOCK_HZ(60) written")
    except Exception as e:
        print(f"[HOST][WARN] SET_BLOCK_HZ failed: {e}")

    # START and request STAT
    try:
        dev.write(OUT_EP, bytes([CMD_START]), timeout=500)
        print("[HOST] START written")
        dev.write(OUT_EP, bytes([CMD_GET_STATUS]), timeout=500)
        print("[HOST] GET_STATUS queued")
    except Exception as e:
        print(f"[ERR] START/GET_STATUS failed: {e}")

    got = 0
    t0 = time.time()
    while got < READ_COUNT and (time.time() - t0) < 15:
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
            print(f"[HOST_RX] ep=0x{IN_EP:02X} len={len(ba)} type={ftype} head={head}")
            got += 1
        except usb.core.USBError as e:
            msg = str(e).lower()
            if (getattr(e, 'errno', None) in (10060, 110, 60)) or ('timed out' in msg):
                print("[HOST_RX] timeout")
                continue
            print(f"[HOST_RX][ERR] {e}")
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
