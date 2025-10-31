#!/usr/bin/env python3
# Send START (0x20) via control OUT (EP0) to Vendor interface
import os, sys, usb.core, usb.util
VID=int(os.getenv('VND_VID','0xCAFE'),16)
PID=int(os.getenv('VND_PID','0x4001'),16)
IFACE_INDEX=int(os.getenv('VND_INTF','2'))
VND_CMD_START=0x20

def main():
    dev=usb.core.find(idVendor=VID,idProduct=PID)
    if dev is None:
        print('[CTRL-START] Device not found')
        sys.exit(1)
    try:
        dev.set_configuration()
    except Exception:
        pass
    # bmRequestType: Host-to-Device, Vendor, Interface
    bm=usb.util.build_request_type(usb.util.CTRL_OUT, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_INTERFACE)
    try:
        dev.ctrl_transfer(bm, VND_CMD_START, 0, IFACE_INDEX, None, timeout=500)
        print('[CTRL-START] START sent via control OUT')
        sys.exit(0)
    except Exception as e:
        print('[CTRL-START][ERR]', e)
        sys.exit(2)

if __name__=='__main__':
    main()
