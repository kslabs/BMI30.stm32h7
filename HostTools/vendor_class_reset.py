#!/usr/bin/env python3
# Issue Vendor control reset to the composite device (soft or deep).
# Default: deep reset (0x7F) to fully reopen endpoints (IF#2 alt=1 will need to be set again by host reader).
import os, sys, usb.core, usb.util
VID = int(os.getenv('VND_VID','0xCAFE'),16)
PID = int(os.getenv('VND_PID','0x4001'),16)
REQ = 0x7F  # 0x7E soft, 0x7F deep
try:
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"[RST][ERR] Device 0x{VID:04X}:0x{PID:04X} not found")
        sys.exit(1)
    bm = usb.util.build_request_type(usb.util.CTRL_OUT, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_INTERFACE)
    # wIndex can be any; class code handles it regardless of interface when vendor request matches
    dev.ctrl_transfer(bm, REQ, 0, 2, None, timeout=500)
    print(f"[RST] Vendor DEEP_RESET (0x{REQ:02X}) sent OK")
    sys.exit(0)
except Exception as e:
    print(f"[RST][ERR] {e}")
    sys.exit(2)
