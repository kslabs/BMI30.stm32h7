#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import usb.core, usb.util, sys
VID=0xCAFE; PID=0x4001

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    print("[ERR] Device not found")
    sys.exit(1)
try:
    dev.set_configuration()
except Exception:
    # On Windows with WinUSB/libusb, set_configuration may be unsupported; proceed anyway
    pass
print(f"Device: VID=0x{VID:04X} PID=0x{PID:04X} speed={'HS' if dev.bcdUSB>=0x0200 else 'FS'}")
for cfg in dev:
    print(f"Config {cfg.bConfigurationValue}: interfaces={cfg.bNumInterfaces}")
    for intf in cfg:
        eps = [f"0x{ep.bEndpointAddress:02X}({['OUT','IN'][(ep.bEndpointAddress>>7)&1]})" for ep in intf]
        print(f"  IF#{intf.bInterfaceNumber} cls=0x{intf.bInterfaceClass:02X} sub=0x{intf.bInterfaceSubClass:02X} proto=0x{intf.bInterfaceProtocol:02X} eps={eps}")
        try:
            attached = dev.is_kernel_driver_active(intf.bInterfaceNumber)
        except Exception:
            attached = None
        print(f"    kernel_driver_active={attached}")
