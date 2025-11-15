#!/usr/bin/env python3
"""
Marker probe: send distinct requested frame sample sizes from host and observe actual frame ns/payload.
This helps attribute which side (host request vs firmware defaults/truncation) dictates effective samples per frame.

Usage:
  py -3 HostTools/marker_probe.py --req 333 444 555 666 --channel B --profile 1 --async 1
"""
import argparse, struct, time
import usb.core, usb.util

VENDOR=0xCAFE
PRODUCT=0x4001
INTERFACE=2
EP_OUT=0x03
EP_IN=0x83

# Commands
CMD_START=0x20
CMD_STOP=0x21
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

def send_cmd(dev, data: bytes, label: str = "CMD"):
    try:
        dev.write(EP_OUT, data, timeout=1000)
    except Exception as e:
        print(f"[{label}][ERR] {e}")
        raise

def drain_in(dev, attempts=10):
    for _ in range(attempts):
        try:
            dev.read(EP_IN, 4096, timeout=50)
        except Exception:
            break

def read_one_frame(dev, wanted_flag: int, timeout_ms=1500):
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        try:
            data = dev.read(EP_IN, 4096, timeout=500)
        except Exception:
            continue
        b = bytes(data)
        h = parse_hdr(b)
        if not h or h['magic'] != 0xA55A:
            continue
        if wanted_flag and h['flags'] != wanted_flag:
            continue
        payload_len = len(b) - HDR_SIZE
        return h, payload_len
    return None, 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--req', type=int, nargs='+', default=[333,444,555,666], help='Requested ns values to probe via CMD_SET_FRAME_SAMPLES')
    ap.add_argument('--channel', choices=['A','B','both'], default='B')
    ap.add_argument('--profile', type=int, default=1)
    ap.add_argument('--async', dest='async_mode', type=int, default=1)
    ap.add_argument('--block-hz', type=int, default=300)
    args = ap.parse_args()

    dev = find_dev()

    # Configure stream
    try:
        send_cmd(dev, bytes([CMD_SET_PROFILE, args.profile & 0xFF]), label='SET_PROFILE')
    except Exception:
        pass
    try:
        send_cmd(dev, bytes([CMD_SET_FULL_MODE, 1]), label='SET_FULL')
    except Exception:
        pass
    try:
        send_cmd(dev, bytes([CMD_SET_ASYNC_MODE, 1 if args.async_mode else 0]), label='SET_ASYNC')
    except Exception:
        pass
    ch_val = 0x02 if args.channel=='both' else (0x00 if args.channel=='A' else 0x01)
    try:
        send_cmd(dev, bytes([CMD_SET_CHMODE, ch_val]), label='SET_CHMODE')
    except Exception:
        pass
    try:
        send_cmd(dev, bytes([CMD_SET_BLOCK_HZ] + le16(args.block_hz)), label='SET_BLOCK_HZ')
    except Exception:
        pass

    print("# HOST marker probe: REQ_NS -> GOT_NS (payload bytes) ")
    print("# Note: firmware markers (e.g., 301/306/307/308) differ from host markers (333/444/555/666)")

    for ns in args.req:
        # Stop any ongoing stream and drain
        try:
            send_cmd(dev, bytes([CMD_STOP]), label='STOP')
        except Exception:
            pass
        drain_in(dev)
        try:
            send_cmd(dev, bytes([CMD_SET_FRAME_SAMPLES] + le16(ns)), label=f'SET_NS({ns})')
        except Exception:
            pass
        # Start and read one frame
        try:
            send_cmd(dev, bytes([CMD_START]), label='START')
        except Exception as e:
            print(f"[START][ERR] {e}")
            continue
        wanted_flag = 0 if args.channel=='both' else (0x01 if args.channel=='A' else 0x02)
        h, payload_len = read_one_frame(dev, wanted_flag)
        try:
            send_cmd(dev, bytes([CMD_STOP]), label='STOP')
        except Exception:
            pass
        if not h:
            print(f"REQ={ns} -> NO_FRAME")
        else:
            got_ns = h['ns']
            print(f"REQ={ns} -> GOT_NS={got_ns} (payload={payload_len})")

if __name__ == '__main__':
    main()
