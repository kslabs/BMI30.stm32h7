#!/usr/bin/env python3
import argparse
import struct
import sys

import usb.core
import usb.util


VID = 0xCAFE
PID = 0x4001
INTF = 2
CMD_GET_LCD_STATUS = 0x38
RESP_LEN = 24

FLAG_SIGNAL_ALIVE = 0x0001
FLAG_SYNC_OK_VISUAL = 0x0002
FLAG_COLOR_LOCKED = 0x0004
FLAG_DISPLAY_FALLBACK = 0x0008
FLAG_HOST_FORCED = 0x0010

MODE_NAMES = {
    0: "MASTER",
    1: "SLAVE",
    2: "OFF",
}

COLOR_NAMES = {
    0: "BLACK",
    1: "RED",
    2: "GREEN",
    3: "YELLOW",
    4: "BLUE",
    5: "CYAN",
    6: "WHITE",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Read LCD sync indicator snapshot via EP0")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=VID)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=PID)
    ap.add_argument("--intf", type=int, default=INTF)
    ap.add_argument("--timeout", type=int, default=1000)
    args = ap.parse_args()

    dev = usb.core.find(idVendor=args.vid, idProduct=args.pid)
    if dev is None:
        print(f"[ERR] Device not found VID=0x{args.vid:04X} PID=0x{args.pid:04X}")
        return 1

    try:
        dev.set_configuration()
    except Exception:
        pass

    try:
        if dev.is_kernel_driver_active(args.intf):
            dev.detach_kernel_driver(args.intf)
    except Exception:
        pass

    try:
        usb.util.claim_interface(dev, args.intf)
    except Exception:
        pass

    try:
        dev.set_interface_altsetting(args.intf, 1)
    except Exception:
        pass

    try:
        ret = dev.ctrl_transfer(
            bmRequestType=0xC1,
            bRequest=CMD_GET_LCD_STATUS,
            wValue=0,
            wIndex=args.intf,
            data_or_wLength=RESP_LEN,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"[ERR] GET_LCD_STATUS failed: {exc}")
        return 2

    data = bytes(ret)
    if len(data) < RESP_LEN:
        print(f"[ERR] LCD status too short: {len(data)} bytes")
        return 3

    (
        sig,
        version,
        raw_mode,
        display_mode,
        display_value,
        slave_count,
        node_id,
        color_id,
        display_char,
        color_rgb565,
        flags,
        sync_age_ms,
        text,
    ) = struct.unpack("<4sBBBBBBBBHHI4s", data[:RESP_LEN])

    sig_s = sig.decode("ascii", errors="ignore")
    text_s = text.decode("ascii", errors="ignore").rstrip("\x00")
    char_s = chr(display_char) if 32 <= display_char <= 126 else "?"

    print(f"=== LCD STATUS v{version} ===")
    print(f"signature: {sig_s}")
    print(f"text: {text_s!r}")
    print(f"raw_mode: {raw_mode} ({MODE_NAMES.get(raw_mode, 'UNKNOWN')})")
    print(f"display_mode: {display_mode} ({MODE_NAMES.get(display_mode, 'UNKNOWN')})")
    print(f"display_char: {char_s}")
    print(f"display_value: {display_value}")
    print(f"slave_count: {slave_count}")
    print(f"node_id: {node_id}")
    print(f"color: {COLOR_NAMES.get(color_id, 'UNKNOWN')} (id={color_id}, rgb565=0x{color_rgb565:04X})")
    print(f"flags: 0x{flags:04X}")
    print(f"signal_alive: {'yes' if (flags & FLAG_SIGNAL_ALIVE) else 'no'}")
    print(f"sync_ok_visual: {'yes' if (flags & FLAG_SYNC_OK_VISUAL) else 'no'}")
    print(f"color_locked: {'yes' if (flags & FLAG_COLOR_LOCKED) else 'no'}")
    print(f"display_fallback: {'yes' if (flags & FLAG_DISPLAY_FALLBACK) else 'no'}")
    print(f"host_forced: {'yes' if (flags & FLAG_HOST_FORCED) else 'no'}")
    if sync_age_ms == 0xFFFFFFFF:
        print("sync_age_ms: n/a")
    else:
        print(f"sync_age_ms: {sync_age_ms}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
