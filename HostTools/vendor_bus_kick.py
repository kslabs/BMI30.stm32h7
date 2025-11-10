#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Small bus kick helper for the Vendor interface:
- Locate device by VID/PID (env VND_VID/VND_PID or defaults 0xCAFE/0x4001)
- Claim interface (env VND_INTF or 2)
- Toggle altsetting 1->0->1
- Clear halt on IN/OUT endpoints (env VND_EP_IN/VND_EP_OUT or 0x83/0x03)
Used between long test runs to reduce chance of lingering stalls on Windows/WinUSB.
"""
import os
import sys
import time
import usb.core
import usb.util

def main():
    vid = int(os.getenv('VND_VID', '0xCAFE'), 16)
    pid = int(os.getenv('VND_PID', '0x4001'), 16)
    intf = int(os.getenv('VND_INTF', '2'))
    ep_in = int(os.getenv('VND_EP_IN', '0x83'), 16)
    ep_out = int(os.getenv('VND_EP_OUT', '0x03'), 16)

    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        print('[BUS-KICK] Device not found, skipping')
        return 0
    try:
        dev.set_configuration()
    except Exception:
        pass
    try:
        if usb.util.get_string(dev, dev.iProduct):
            pass
    except Exception:
        pass
    try:
        usb.util.claim_interface(dev, intf)
    except Exception:
        pass
    # Toggle altsetting and clear halts
    try:
        try:
            dev.set_interface_altsetting(interface=intf, alternate_setting=0)
            time.sleep(0.03)
        except Exception:
            pass
        try:
            dev.set_interface_altsetting(interface=intf, alternate_setting=1)
            time.sleep(0.03)
        except Exception:
            pass
        try:
            dev.clear_halt(ep_in)
        except Exception:
            pass
        try:
            dev.clear_halt(ep_out)
        except Exception:
            pass
        print('[BUS-KICK] alt 0->1 toggled, halts cleared')
    finally:
        try:
            usb.util.release_interface(dev, intf)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass
    return 0

if __name__ == '__main__':
    sys.exit(main())
