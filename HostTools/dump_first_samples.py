#!/usr/bin/env python3
"""
Dump first N samples from M frames for selected channel (A or B).
Defaults: channel=B, frames=10, samples=20, ns=300.

Usage examples:
  py -3 HostTools/dump_first_samples.py
  py -3 HostTools/dump_first_samples.py --channel B --frames 10 --samples 20 --ns 300
"""

import sys, time, struct, argparse
from typing import Optional
import usb.core, usb.util

VENDOR=0xCAFE
PRODUCT=0x4001
INTERFACE=2
EP_OUT=0x03
EP_IN=0x83

# Commands
CMD_START=0x20
CMD_STOP=0x21
CMD_SET_WINDOWS=0x10
CMD_SET_BLOCK_HZ=0x11
CMD_SET_TRUNC_SAMPLES=0x16
CMD_SET_FRAME_SAMPLES=0x17
CMD_SET_FULL_MODE=0x13
CMD_SET_PROFILE=0x14
CMD_SET_CHMODE=0x19  # 0=A-only, 1=B-only, 2=both
CMD_SET_ASYNC_MODE=0x18

HDR_SIZE=32

def le16(x:int):
    return [x & 0xFF, (x >> 8) & 0xFF]

def parse_hdr(b: bytes):
    if len(b) < HDR_SIZE:
        return None
    magic, ver, flags, seq, ts, ns, zc = struct.unpack_from('<HBBIIHH', b, 0)
    return {
        'magic': magic,
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'ts': ts,
        'ns': ns,
    }

def find_dev():
    global INTERFACE
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
    if dev is None:
        raise SystemExit("Device not found")
    try:
        dev.set_configuration()
    except Exception:
        pass
    try:
        cfg = dev.get_active_configuration()
    except Exception:
        cfg = None
    picked_intf = None
    if cfg is not None:
        for intf in cfg:
            eps = [ep.bEndpointAddress for ep in intf]
            if (EP_IN in eps) and (EP_OUT in eps):
                picked_intf = intf
                break
    if picked_intf is not None:
        INTERFACE = picked_intf.bInterfaceNumber
        try:
            usb.util.claim_interface(dev, INTERFACE)
        except Exception:
            pass
        try:
            dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
        except Exception:
            pass
    else:
        try:
            usb.util.claim_interface(dev, INTERFACE)
        except Exception:
            pass
        try:
            dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
        except Exception:
            pass
    return dev

def send_cmd(dev, data: bytes):
    dev.write(EP_OUT, data, timeout=1000)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--channel', choices=['A','B'], default='B')
    ap.add_argument('--frames', type=int, default=10)
    ap.add_argument('--samples', type=int, default=20)
    ap.add_argument('--ns', type=int, default=300)
    ap.add_argument('--profile', type=int, default=1)
    args = ap.parse_args()

    dev = find_dev()

    # Configure minimal stream for selected channel
    try:
        send_cmd(dev, bytes([CMD_SET_PROFILE, args.profile & 0xFF]))
    except Exception:
        pass
    try:
        send_cmd(dev, bytes([CMD_SET_FULL_MODE, 1]))
    except Exception:
        pass
    try:
        send_cmd(dev, bytes([CMD_SET_ASYNC_MODE, 1]))
    except Exception:
        pass
    try:
        send_cmd(dev, bytes([CMD_SET_FRAME_SAMPLES] + le16(args.ns)))
    except Exception:
        pass
    # Select channel: A-only or B-only
    ch_val = 0x00 if args.channel=='A' else 0x01
    try:
        send_cmd(dev, bytes([CMD_SET_CHMODE, ch_val]))
    except Exception:
        pass
    # Optional block rate hint
    try:
        send_cmd(dev, bytes([CMD_SET_BLOCK_HZ] + le16(200 if args.profile==1 else 300)))
    except Exception:
        pass

    # Clear any pending data
    try:
        while True:
            data = dev.read(EP_IN, 4096, timeout=100)
            if not data:
                break
    except Exception:
        pass

    # START
    try:
        send_cmd(dev, bytes([CMD_START]))
    except Exception as e:
        print("START failed:", e)
        return 1

    wanted_flag = 0x01 if args.channel=='A' else 0x02
    printed = 0
    print(f"Dumping first {args.samples} samples from {args.frames} frames of channel {args.channel}...")
    try:
        while printed < args.frames:
            try:
                data = dev.read(EP_IN, 4096, timeout=1000)
            except usb.core.USBError as e:
                if getattr(e, 'errno', None) == 110:
                    continue
                print("USB error:", e)
                continue
            b = bytes(data)
            h = parse_hdr(b)
            if not h or h['magic'] != 0xA55A:
                continue
            if h['flags'] != wanted_flag:
                continue
            ns = min(h['ns'], args.ns)
            payload = b[HDR_SIZE:HDR_SIZE + ns*2]
            n = min(args.samples, ns)
            vals = [payload[2*i] | (payload[2*i+1]<<8) for i in range(n)]
            print(f"[{printed+1:02d}] seq={h['seq']} ts={h['ts']} ns={ns} samples[0:{n}]={vals}")
            printed += 1
    finally:
        try:
            send_cmd(dev, bytes([CMD_STOP]))
        except Exception:
            pass
        try:
            usb.util.release_interface(dev, INTERFACE)
        except Exception:
            pass
    return 0

if __name__ == '__main__':
    sys.exit(main())
