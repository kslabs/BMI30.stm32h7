#!/usr/bin/env python3
"""Bench control/readback for lease-protected BMI30 RS-485 wire modes.

Production Raspberry software must issue these EP0 requests from the existing
USB owner and renew a reduced mode only while its IP receiver heartbeat and
frame-continuity checks are healthy. This standalone utility is for bring-up.
"""

import argparse
import json
import struct
import time

import usb.core


CMD_SET_WIRE_MODE = 0x45
CMD_GET_SYNC_DIAG = 0x46
SYN1 = struct.Struct("<4sBBBBHBBIIIIIIHHiiIIIIIIIIBBH")

MODE_BY_NAME = {
    "full": 0,
    "priority": 1,
    "sync-only": 2,
}
MODE_NAME = {value: key for key, value in MODE_BY_NAME.items()}
FALLBACK_NAME = {
    0: "none",
    1: "lease_expired",
    2: "usb_stop",
    3: "host_clear",
    4: "usb_disconnect",
}


def choose_device(vid, pid, serial_number, index):
    devices = list(usb.core.find(find_all=True, idVendor=vid, idProduct=pid) or [])
    if serial_number:
        devices = [dev for dev in devices
                   if getattr(dev, "serial_number", None) == serial_number]
    if not devices:
        raise SystemExit("BMI30 USB device not found")
    if index < 0 or index >= len(devices):
        raise SystemExit(f"--index must be 0..{len(devices) - 1}")
    return devices[index]


def set_mode(dev, mode, lease_ms, transport_epoch):
    payload = struct.pack("<BBHI", 1, mode, lease_ms, transport_epoch)
    written = dev.ctrl_transfer(0x40, CMD_SET_WIRE_MODE, 0, 0,
                                payload, timeout=500)
    if int(written) != len(payload):
        raise RuntimeError(f"SET_WIRE_MODE wrote {written}, expected {len(payload)}")


def read_diag(dev):
    raw = bytes(dev.ctrl_transfer(0xC0, CMD_GET_SYNC_DIAG, 0, 0,
                                  SYN1.size, timeout=500))
    if len(raw) != SYN1.size:
        raise RuntimeError(f"SYN1 length={len(raw)}, expected {SYN1.size}")
    fields = SYN1.unpack(raw)
    if fields[0] != b"SYN1" or fields[1] != 1:
        raise RuntimeError("invalid SYN1 signature/version")
    keys = (
        "signature", "version", "wire_mode", "sync_role", "phase_relation",
        "flags", "node_id", "node_count", "boot_id", "transport_epoch",
        "lease_remaining_ms", "sync_age_ms", "sync_edge_count", "buffer_count",
        "active_samples", "buffer_rate_hz", "phase_error_ticks",
        "control_error_ticks", "sync_period_ticks", "tim5_tick_hz",
        "uart_error_count", "sync_rejected_early_count",
        "sync_tx_deferred_count", "sync_tx_coalesced_count",
        "sync_tx_delay_max_ticks", "wire_fallback_count",
        "last_fallback_reason", "regular_reply_divisor",
    )
    result = dict(zip(keys, fields))
    result["signature"] = result["signature"].decode("ascii")
    result["wire_mode_name"] = MODE_NAME.get(result["wire_mode"], "unknown")
    result["last_fallback_name"] = FALLBACK_NAME.get(
        result["last_fallback_reason"], "unknown")
    tick_hz = result["tim5_tick_hz"]
    if tick_hz:
        result["phase_error_us"] = result["phase_error_ticks"] * 1_000_000.0 / tick_hz
        result["sync_tx_delay_max_us"] = (
            result["sync_tx_delay_max_ticks"] * 1_000_000.0 / tick_hz)
    return result


def print_diag(dev):
    print(json.dumps(read_diag(dev), ensure_ascii=False, indent=2))


def wait_mode(dev, mode, transport_epoch, timeout_s=0.5):
    deadline = time.monotonic() + timeout_s
    while True:
        diag = read_diag(dev)
        if (diag["wire_mode"] == mode and
                diag["transport_epoch"] == transport_epoch and
                not (diag["flags"] & 0x0400)):
            return diag
        if time.monotonic() >= deadline:
            raise RuntimeError("wire-mode transition/readback timeout")
        time.sleep(0.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("status", *MODE_BY_NAME))
    parser.add_argument("--vid", type=lambda value: int(value, 0), default=0xCAFE)
    parser.add_argument("--pid", type=lambda value: int(value, 0), default=0x4001)
    parser.add_argument("--serial")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--lease-ms", type=int, default=3000)
    parser.add_argument("--renew-ms", type=int, default=1000)
    parser.add_argument("--transport-epoch", type=lambda value: int(value, 0),
                        default=None)
    parser.add_argument("--keepalive", action="store_true",
                        help="renew until Ctrl-C, then restore FULL")
    args = parser.parse_args()

    dev = choose_device(args.vid, args.pid, args.serial, args.index)
    if args.mode == "status":
        print_diag(dev)
        return

    mode = MODE_BY_NAME[args.mode]
    lease_ms = 0 if mode == 0 else args.lease_ms
    if mode != 0 and not 250 <= lease_ms <= 60000:
        raise SystemExit("--lease-ms must be 250..60000 for a reduced mode")
    if args.renew_ms <= 0 or (mode != 0 and args.renew_ms >= lease_ms):
        raise SystemExit("--renew-ms must be positive and less than --lease-ms")
    epoch = args.transport_epoch
    if epoch is None:
        epoch = int(time.time_ns() // 1_000_000) & 0xFFFFFFFF

    set_mode(dev, mode, lease_ms, epoch)
    print(json.dumps(wait_mode(dev, mode, epoch), ensure_ascii=False, indent=2))
    if not args.keepalive or mode == 0:
        return

    try:
        while True:
            time.sleep(args.renew_ms / 1000.0)
            set_mode(dev, mode, lease_ms, epoch)
            diag = wait_mode(dev, mode, epoch)
            if diag["wire_mode"] != mode or diag["transport_epoch"] != epoch:
                raise RuntimeError("wire-mode readback no longer matches request")
    finally:
        try:
            set_mode(dev, MODE_BY_NAME["full"], 0, epoch)
        finally:
            print(json.dumps(wait_mode(dev, MODE_BY_NAME["full"], epoch),
                             ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
