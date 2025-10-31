#!/usr/bin/env python3
import usb.core, usb.util
VID=0xCAFE; PID=0x4001
VND_CMD_GET_STATUS=0x30
bm = usb.util.build_request_type(usb.util.CTRL_IN, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_INTERFACE)
dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev:
    print('[ERR] not found'); raise SystemExit(1)
try:
    dev.set_configuration()
except Exception:
    pass
# Try both interface index 2 and 0 for wIndex
for idx in (2,0):
    try:
        data = dev.ctrl_transfer(bm, VND_CMD_GET_STATUS, 0, idx, 64, timeout=500)
        ba = bytes(data)
        print(f"[STAT][IF#{idx}]", ba[:16].hex(), 'len', len(ba))
    except Exception as e:
        print(f"[STAT][IF#{idx}][ERR] {e}")
