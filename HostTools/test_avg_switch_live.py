#!/usr/bin/env python3
import argparse
import struct
import time
import usb.core
import usb.util

VND_CMD_SET_WINDOWS = 0x10
VND_CMD_SET_PROFILE = 0x14
VND_CMD_SET_BLOCK_HZ = 0x11
VND_CMD_SET_FULL_MODE = 0x13
VND_CMD_SET_STREAM_MODE = 0x1A
VND_CMD_SET_ASYNC = 0x18
VND_CMD_SET_CHMODE = 0x19
VND_CMD_START_STREAM = 0x20
VND_CMD_STOP_STREAM = 0x21


def le16(v: int) -> bytes:
    return bytes((v & 0xFF, (v >> 8) & 0xFF))


def send_cmd(dev, ep_out: int, payload: bytes):
    dev.write(ep_out, payload, timeout=1000)


def claim_vendor_interface(dev, intf: int):
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    cfg = dev.get_active_configuration()
    try:
        if dev.is_kernel_driver_active(intf):
            dev.detach_kernel_driver(intf)
    except (NotImplementedError, usb.core.USBError, AttributeError):
        pass
    usb.util.claim_interface(dev, intf)
    try:
        dev.set_interface_altsetting(interface=intf, alternate_setting=1)
    except Exception:
        pass
    return cfg


def read_frames_for_secs(dev, ep_in: int, secs: float, timeout_ms: int):
    end_t = time.time() + secs
    got_a = 0
    got_b = 0
    in_errors = 0
    while time.time() < end_t:
        try:
            data = bytes(dev.read(ep_in, 16384, timeout=timeout_ms))
        except usb.core.USBError:
            in_errors += 1
            continue

        off = 0
        n = len(data)
        while off + 24 <= n:
            if data[off] != 0x5A or data[off + 1] != 0xA5:
                off += 1
                continue
            flags = data[off + 3]
            ns = data[off + 12] | (data[off + 13] << 8)
            frame_len = 24 + ns * 2
            if off + frame_len > n:
                break
            if (flags & 0x80) == 0:
                base = flags & 0x03
                if base == 0x01:
                    got_a += 1
                elif base == 0x02:
                    got_b += 1
            off += frame_len
    return got_a, got_b, in_errors


def main():
    ap = argparse.ArgumentParser(description="Switch AVG_N live without reset/start-stop per step")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=0xCAFE)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=0x4001)
    ap.add_argument("--intf", type=int, default=2)
    ap.add_argument("--ep-in", type=lambda x: int(x, 0), default=0x83)
    ap.add_argument("--ep-out", type=lambda x: int(x, 0), default=0x03)
    ap.add_argument("--profile", type=int, default=1)
    ap.add_argument("--secs", type=float, default=8.0)
    ap.add_argument("--settle-ms", type=int, default=250)
    ap.add_argument("--timeout", type=int, default=1000)
    ap.add_argument("--avg-list", type=str, default="8,16,24,32,40,48,56,64")
    args = ap.parse_args()

    dev = usb.core.find(idVendor=args.vid, idProduct=args.pid)
    if dev is None:
        raise SystemExit("Device not found")
    claim_vendor_interface(dev, args.intf)

    send_cmd(dev, args.ep_out, struct.pack("<BHHHH", VND_CMD_SET_WINDOWS, 280, 200, 280, 200))
    send_cmd(dev, args.ep_out, bytes([VND_CMD_SET_PROFILE, args.profile & 0xFF]))
    send_cmd(dev, args.ep_out, bytes([VND_CMD_SET_BLOCK_HZ]) + le16(200))
    send_cmd(dev, args.ep_out, bytes([VND_CMD_SET_FULL_MODE, 1]))
    send_cmd(dev, args.ep_out, bytes([VND_CMD_SET_CHMODE, 2]))
    send_cmd(dev, args.ep_out, bytes([VND_CMD_SET_ASYNC, 1]))

    send_cmd(dev, args.ep_out, bytes([VND_CMD_STOP_STREAM]))
    time.sleep(0.2)
    send_cmd(dev, args.ep_out, bytes([VND_CMD_START_STREAM]))
    time.sleep(0.2)

    avg_list = [int(x.strip()) for x in args.avg_list.split(",") if x.strip()]
    print("avg_n,actual_pairs_fps,expected_400_div_N,ratio_to_400N,in_errors")
    for n in avg_list:
        if n < 1:
            n = 1
        if n > 64:
            n = 64
        send_cmd(dev, args.ep_out, bytes([VND_CMD_SET_STREAM_MODE, 2, n & 0xFF]))
        time.sleep(max(0.0, args.settle_ms / 1000.0))

        t0 = time.time()
        a, b, errs = read_frames_for_secs(dev, args.ep_in, args.secs, args.timeout)
        elapsed = max(0.001, time.time() - t0)
        pairs = min(a, b)
        fps = pairs / elapsed
        expected = 400.0 / float(n)
        ratio = fps / expected if expected > 0 else 0.0
        print(f"{n},{fps:.3f},{expected:.3f},{ratio:.3f},{errs}")

    try:
        send_cmd(dev, args.ep_out, bytes([VND_CMD_STOP_STREAM]))
    except Exception:
        pass


if __name__ == "__main__":
    main()
