#!/usr/bin/env python3
import usb.core, usb.util, sys
VID=0xCAFE; PID=0x4001
REQ_SOFT=0x7E; REQ_DEEP=0x7F
try:
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("[ERR] Device not found")
        sys.exit(1)
    try:
        dev.set_configuration()
    except Exception:
        pass
    # Try alt=0 then alt=1 flip to ensure control path serviced by our class
    try:
        dev.set_interface_altsetting(interface=2, alternate_setting=0)
    except Exception:
        pass
    bm_out_dev = usb.util.build_request_type(usb.util.CTRL_OUT, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_DEVICE)
    # Send deep reset (0x7F)
    dev.ctrl_transfer(bm_out_dev, REQ_DEEP, 0, 0, None, timeout=500)
    print("[HOST] DEEP_RESET sent via EP0")
    # Small delay for device to reinit endpoints
    import time; time.sleep(0.2)
    try:
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        print("[HOST] SetInterface(IF#2, alt=1) OK")
    except Exception as e:
        print(f"[HOST][WARN] SetInterface alt=1: {e}")
    sys.exit(0)
except usb.core.USBError as e:
    print(f"[ERR] USB error: {e}")
    sys.exit(2)
