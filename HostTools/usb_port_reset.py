#!/usr/bin/env python3
import usb.core, time
VID,PID=0xCAFE,0x4001
dev=usb.core.find(idVendor=VID,idProduct=PID)
if dev is None:
    print('[USB-PORT-RESET] device not found'); raise SystemExit(1)
print('[USB-PORT-RESET] calling dev.reset()')
try:
    dev.reset()
    print('[USB-PORT-RESET] reset() returned, waiting 2.5s...')
    time.sleep(2.5)
except Exception as e:
    print('[USB-PORT-RESET][ERR]', e)
    raise SystemExit(2)
