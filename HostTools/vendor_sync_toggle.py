#!/usr/bin/env python3
"""Toggle SYNC mode via Vendor OUT EP (0x03).
Usage:
  python HostTools/vendor_sync_toggle.py --start --seq master:5 slave:10 master:5 --stop
"""
import argparse
import time
import usb.core
import usb.util

VND_OUT = 0x03
VND_IN = 0x83
CMD_SET_SYNC_MODE = 0x1D
CMD_START = 0x20
CMD_STOP = 0x21

MODE_MAP = {
    "master": 0,
    "slave": 1,
    "off": 2,
}


def find_dev(vid, pid):
    return usb.core.find(idVendor=vid, idProduct=pid)


def claim_vendor_if(d):
    cfg = d.get_active_configuration()
    for intf in cfg:
        addrs = [e.bEndpointAddress for e in intf]
        if VND_OUT in addrs and VND_IN in addrs:
            try:
                if d.is_kernel_driver_active(intf.bInterfaceNumber):
                    d.detach_kernel_driver(intf.bInterfaceNumber)
            except NotImplementedError:
                pass
            usb.util.claim_interface(d, intf.bInterfaceNumber)
            try:
                d.set_interface_altsetting(interface=intf.bInterfaceNumber, alternate_setting=1)
            except Exception:
                pass
            return intf
    raise RuntimeError("Vendor interface not found (0x03/0x83)")


def send_cmd(dev, cmd, payload=b""):
    dev.write(VND_OUT, bytes([cmd]) + payload, timeout=500)


def parse_seq(seq_str):
    seq = []
    for part in seq_str.split():
        if ":" not in part:
            raise ValueError(f"Bad seq item: {part}")
        mode, sec = part.split(":", 1)
        mode = mode.strip().lower()
        if mode not in MODE_MAP:
            raise ValueError(f"Bad mode: {mode}")
        sec = float(sec)
        seq.append((MODE_MAP[mode], mode, sec))
    return seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vid", type=lambda x: int(x, 16), default=0xCAFE)
    ap.add_argument("--pid", type=lambda x: int(x, 16), default=0x4001)
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--seq", type=str, default="master:5 slave:10")
    args = ap.parse_args()

    dev = find_dev(args.vid, args.pid)
    if not dev:
        raise SystemExit("Device not found")
    dev.set_configuration()
    claim_vendor_if(dev)

    if args.start:
        print("[CMD] START", flush=True)
        send_cmd(dev, CMD_START)
        time.sleep(0.2)

    seq = parse_seq(args.seq)
    for mode_val, mode_name, sec in seq:
        print(f"[CMD] SET_SYNC_MODE {mode_name} for {sec}s", flush=True)
        send_cmd(dev, CMD_SET_SYNC_MODE, bytes([mode_val]))
        time.sleep(sec)

    if args.stop:
        print("[CMD] STOP", flush=True)
        try:
            send_cmd(dev, CMD_STOP)
        except Exception:
            pass


if __name__ == "__main__":
    main()
