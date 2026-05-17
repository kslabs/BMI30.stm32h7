#!/usr/bin/env python3
import argparse
import struct
import time

import usb.core
import usb.util

VND_CMD_LED_EVENT = 0x35
VND_CMD_SET_LED_PATTERN = 0x3B
VND_LED_EVENT_CHANNEL_B = 0x01
VND_LED_EVENT_CHANNEL_A = 0x02
VND_LED_EVENT_BOTH = 0x03
VND_LED_EVENT_SPLIT_IN = 0x04
VND_LED_EVENT_SPLIT_OUT = 0x05

LED_PATTERN_NAMES = {
    "OFF": 0,
    "IDLE_BREATHE": 1,
    "STREAMING": 2,
    "SYNC_PULSE": 3,
    "UART_RX": 4,
    "TUNE": 5,
    "RECOVERY": 6,
    "HARD_RESET": 7,
    "EVENT_B_UP": 8,
    "EVENT_A_DOWN": 9,
    "EVENT_BOTH_ALT": 10,
    "EVENT_SPLIT_IN": 11,
    "EVENT_SPLIT_OUT": 12,
    "TEST_DRIP": 13,
    "TEST_SCOPE_RGB": 14,
    "TEST_BLUE": 15,
    "TEST_COLOR_CYCLE": 16,
}


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


def send_pattern(dev: usb.core.Device, ep_out: int, pattern_id: int) -> None:
    dev.write(ep_out, bytes([VND_CMD_SET_LED_PATTERN, pattern_id & 0xFF]), timeout=1000)


def main() -> None:
    ap = argparse.ArgumentParser(description="Send visible WS2812 event pattern to STM32 host interface")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=0xCAFE)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=0x4001)
    ap.add_argument("--intf", type=int, default=2)
    ap.add_argument("--ep-out", type=lambda x: int(x, 0), default=0x03)
    ap.add_argument("--event", choices=["B", "A", "BOTH", "SPLIT_IN", "SPLIT_OUT", "demo"], default="demo")
    ap.add_argument("--pattern", choices=sorted(LED_PATTERN_NAMES), default=None)
    ap.add_argument("--duration-ms", type=int, default=1600)
    ap.add_argument("--gap-ms", type=int, default=500)
    args = ap.parse_args()

    dev = find_device(args.vid, args.pid)
    claim_interface(dev, args.intf)

    if args.pattern is not None:
        pattern_id = LED_PATTERN_NAMES[args.pattern]
        send_pattern(dev, args.ep_out, pattern_id)
        print(f"Set LED pattern {args.pattern} ({pattern_id})")
        return

    if args.event == "demo":
        send_event(dev, args.ep_out, VND_LED_EVENT_CHANNEL_B, args.duration_ms)
        print(f"Sent B event for {args.duration_ms} ms")
        time.sleep(max(args.gap_ms, 0) / 1000.0)
        send_event(dev, args.ep_out, VND_LED_EVENT_CHANNEL_A, args.duration_ms)
        print(f"Sent A event for {args.duration_ms} ms")
    elif args.event == "B":
        send_event(dev, args.ep_out, VND_LED_EVENT_CHANNEL_B, args.duration_ms)
        print(f"Sent B event for {args.duration_ms} ms")
    elif args.event == "BOTH":
        send_event(dev, args.ep_out, VND_LED_EVENT_BOTH, args.duration_ms)
        print(f"Sent BOTH event for {args.duration_ms} ms")
    elif args.event == "SPLIT_IN":
        send_event(dev, args.ep_out, VND_LED_EVENT_SPLIT_IN, args.duration_ms)
        print(f"Sent SPLIT_IN event for {args.duration_ms} ms")
    elif args.event == "SPLIT_OUT":
        send_event(dev, args.ep_out, VND_LED_EVENT_SPLIT_OUT, args.duration_ms)
        print(f"Sent SPLIT_OUT event for {args.duration_ms} ms")
    else:
        send_event(dev, args.ep_out, VND_LED_EVENT_CHANNEL_A, args.duration_ms)
        print(f"Sent A event for {args.duration_ms} ms")


if __name__ == "__main__":
    main()
