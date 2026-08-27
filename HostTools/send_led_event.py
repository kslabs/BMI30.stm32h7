#!/usr/bin/env python3
import argparse
import struct
import time

import usb.core
import usb.util

VND_CMD_LED_EVENT = 0x35
VND_CMD_SET_LED_PATTERN = 0x3B
VND_CMD_SET_OPTIC_REACTION_SOURCE = 0x44
LED_PATTERN_NAMES = {
    "OFF": 0,
    "UP_RED_1": 1,
    "UP_RED_2": 2,
    "UP_YELLOW_1": 3,
    "UP_YELLOW_2": 4,
    "DOWN_RED_1": 5,
    "DOWN_RED_2": 6,
    "DOWN_YELLOW_1": 7,
    "DOWN_YELLOW_2": 8,
    "IN_RED_1": 9,
    "IN_RED_2": 10,
    "IN_YELLOW_1": 11,
    "IN_YELLOW_2": 12,
    "OUT_RED": 13,
    "OUT_YELLOW": 14,
    "UA_DEMO": 15,
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


def send_event(dev: usb.core.Device, ep_out: int, pattern_id: int, duration_ms: int) -> None:
    payload = struct.pack("<BBH", VND_CMD_LED_EVENT, pattern_id & 0xFF, duration_ms & 0xFFFF)
    dev.write(ep_out, payload, timeout=1000)


def send_pattern(dev: usb.core.Device, ep_out: int, pattern_id: int) -> None:
    dev.write(ep_out, bytes([VND_CMD_SET_LED_PATTERN, pattern_id & 0xFF]), timeout=1000)


def send_optic_reaction_source(dev: usb.core.Device, ep_out: int, source_id: int) -> None:
    dev.write(ep_out, bytes([VND_CMD_SET_OPTIC_REACTION_SOURCE, source_id & 0xFF]), timeout=1000)


def main() -> None:
    ap = argparse.ArgumentParser(description="Send visible WS2812 event pattern to STM32 host interface")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=0xCAFE)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=0x4001)
    ap.add_argument("--intf", type=int, default=2)
    ap.add_argument("--ep-out", type=lambda x: int(x, 0), default=0x03)
    ap.add_argument("--event", choices=["B", "A", "BOTH", "SPLIT_IN", "SPLIT_OUT", "demo"], default=None,
                    help="legacy aliases for a timed pattern")
    ap.add_argument("--pattern", choices=sorted(LED_PATTERN_NAMES), default=None,
                    help="store selected pattern ID with 0x3B; does not illuminate")
    ap.add_argument("--trigger-pattern", choices=sorted(LED_PATTERN_NAMES), default=None,
                    help="show this pattern temporarily with 0x35")
    source_group = ap.add_mutually_exclusive_group()
    source_group.add_argument("--source-id", type=int, choices=range(32), default=None,
                              help="set an additional system-LED remote optic source ID with 0x44")
    source_group.add_argument("--disable-source", action="store_true",
                              help="disable remote optic reaction with 0x44/0xFF; local stays enabled")
    ap.add_argument("--duration-ms", type=int, default=1600)
    ap.add_argument("--gap-ms", type=int, default=500)
    args = ap.parse_args()

    dev = find_device(args.vid, args.pid)
    claim_interface(dev, args.intf)

    source_changed = False
    if args.source_id is not None:
        send_optic_reaction_source(dev, args.ep_out, args.source_id)
        print(f"System LED optic source set to device ID {args.source_id}")
        source_changed = True
    elif args.disable_source:
        send_optic_reaction_source(dev, args.ep_out, 0xFF)
        print("System LED optic reaction disabled")
        source_changed = True

    if args.pattern is not None:
        pattern_id = LED_PATTERN_NAMES[args.pattern]
        send_pattern(dev, args.ep_out, pattern_id)
        print(f"Stored LED pattern {args.pattern} ({pattern_id}); output remains off")
        return

    if args.trigger_pattern is not None:
        pattern_id = LED_PATTERN_NAMES[args.trigger_pattern]
        send_event(dev, args.ep_out, pattern_id, args.duration_ms)
        print(f"Triggered {args.trigger_pattern} ({pattern_id}) for {args.duration_ms} ms")
        return

    if source_changed and args.event is None:
        return

    if args.event in (None, "demo"):
        send_event(dev, args.ep_out, LED_PATTERN_NAMES["UP_RED_1"], args.duration_ms)
        print(f"Sent B event for {args.duration_ms} ms")
        time.sleep(max(args.gap_ms, 0) / 1000.0)
        send_event(dev, args.ep_out, LED_PATTERN_NAMES["DOWN_RED_1"], args.duration_ms)
        print(f"Sent A event for {args.duration_ms} ms")
    elif args.event == "B":
        send_event(dev, args.ep_out, LED_PATTERN_NAMES["UP_RED_1"], args.duration_ms)
        print(f"Sent B event for {args.duration_ms} ms")
    elif args.event == "BOTH":
        send_event(dev, args.ep_out, LED_PATTERN_NAMES["IN_RED_1"], args.duration_ms)
        print(f"Sent BOTH event for {args.duration_ms} ms")
    elif args.event == "SPLIT_IN":
        send_event(dev, args.ep_out, LED_PATTERN_NAMES["IN_RED_1"], args.duration_ms)
        print(f"Sent SPLIT_IN event for {args.duration_ms} ms")
    elif args.event == "SPLIT_OUT":
        send_event(dev, args.ep_out, LED_PATTERN_NAMES["OUT_RED"], args.duration_ms)
        print(f"Sent SPLIT_OUT event for {args.duration_ms} ms")
    else:
        send_event(dev, args.ep_out, LED_PATTERN_NAMES["DOWN_RED_1"], args.duration_ms)
        print(f"Sent A event for {args.duration_ms} ms")


if __name__ == "__main__":
    main()
