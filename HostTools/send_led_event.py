#!/usr/bin/env python3
import argparse
import struct
import time

import usb.core
import usb.util

VND_CMD_LED_EVENT = 0x35
VND_LED_EVENT_CHANNEL_B = 0x01
VND_LED_EVENT_CHANNEL_A = 0x02


def find_device(vid: int, pid: int) -> usb.core.Device:
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        raise SystemExit(f"Device VID=0x{vid:04X} PID=0x{pid:04X} not found")
    try:
        dev.set_configuration()
    except Exception:
        pass
    return dev


def claim_interface(dev: usb.core.Device, intf_num: int) -> None:
    try:
        if dev.is_kernel_driver_active(intf_num):
            try:
                dev.detach_kernel_driver(intf_num)
            except Exception:
                pass
    except Exception:
        pass
    try:
        usb.util.claim_interface(dev, intf_num)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=intf_num, alternate_setting=1)
    except Exception:
        pass


def send_event(dev: usb.core.Device, ep_out: int, event_id: int, duration_ms: int) -> None:
    payload = struct.pack("<BBH", VND_CMD_LED_EVENT, event_id & 0xFF, duration_ms & 0xFFFF)
    dev.write(ep_out, payload, timeout=1000)


def main() -> None:
    ap = argparse.ArgumentParser(description="Send visible WS2812 event pattern to STM32 host interface")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=0xCAFE)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=0x4001)
    ap.add_argument("--intf", type=int, default=2)
    ap.add_argument("--ep-out", type=lambda x: int(x, 0), default=0x03)
    ap.add_argument("--event", choices=["B", "A", "demo"], default="demo")
    ap.add_argument("--duration-ms", type=int, default=1600)
    ap.add_argument("--gap-ms", type=int, default=500)
    args = ap.parse_args()

    dev = find_device(args.vid, args.pid)
    claim_interface(dev, args.intf)

    if args.event == "demo":
        send_event(dev, args.ep_out, VND_LED_EVENT_CHANNEL_B, args.duration_ms)
        print(f"Sent B event for {args.duration_ms} ms")
        time.sleep(max(args.gap_ms, 0) / 1000.0)
        send_event(dev, args.ep_out, VND_LED_EVENT_CHANNEL_A, args.duration_ms)
        print(f"Sent A event for {args.duration_ms} ms")
    elif args.event == "B":
        send_event(dev, args.ep_out, VND_LED_EVENT_CHANNEL_B, args.duration_ms)
        print(f"Sent B event for {args.duration_ms} ms")
    else:
        send_event(dev, args.ep_out, VND_LED_EVENT_CHANNEL_A, args.duration_ms)
        print(f"Sent A event for {args.duration_ms} ms")


if __name__ == "__main__":
    main()
