#!/usr/bin/env python3
# Send START (0x20) over USB Vendor OUT (0x03) to trigger streaming, wait a bit, then STOP (0x21).
# Requires: pyusb (pip install pyusb) and WinUSB/libusb driver bound to the Vendor interface.

import sys, time, usb.core, usb.util

VND_OUT = 0x03
CMD_START = 0x20
CMD_STOP  = 0x21

def find_dev(vid=None, pid=None):
    if vid and pid:
        d = usb.core.find(idVendor=vid, idProduct=pid)
        if d: return d
    # fallback: first device with interface that has both 0x03 OUT and 0x83 IN
    for d in usb.core.find(find_all=True):
        try:
            cfg = d.get_active_configuration()
        except Exception:
            continue
        for intf in cfg:
            addrs = [e.bEndpointAddress for e in intf]
            if 0x03 in addrs and 0x83 in addrs:
                return d
    return None

def claim_vendor_if(d):
    cfg = d.get_active_configuration()
    for intf in cfg:
        addrs = [e.bEndpointAddress for e in intf]
        if 0x03 in addrs and 0x83 in addrs:
            if d.is_kernel_driver_active(intf.bInterfaceNumber):
                d.detach_kernel_driver(intf.bInterfaceNumber)
            usb.util.claim_interface(d, intf.bInterfaceNumber)
            return intf
    raise RuntimeError("Vendor interface not found (0x03/0x83)")

if __name__ == '__main__':
    vid = pid = None
    if len(sys.argv) == 3:
        vid = int(sys.argv[1], 16)
        pid = int(sys.argv[2], 16)
        print(f"Using VID=0x{vid:04X} PID=0x{pid:04X}")
    elif len(sys.argv) not in (1,3):
        print("Usage: vendor_usb_start_only.py [VID PID]")
        sys.exit(2)
    if vid is None and pid is None:
        vid, pid = 0xCAFE, 0x4001
    d = find_dev(vid, pid)
    if not d:
        print("Device not found")
        sys.exit(1)
    d.set_configuration()
    claim_vendor_if(d)
    print("Sending START...")
    d.write(VND_OUT, bytes([CMD_START]), timeout=200)
    time.sleep(1.0)
    print("Sending STOP...")
    try:
        d.write(VND_OUT, bytes([CMD_STOP]), timeout=200)
    except Exception:
        pass
    print("Done.")
