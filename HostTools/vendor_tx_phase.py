#!/usr/bin/env python3
"""Set/read independent BMI30 TX phases over EP0 without changing the stream."""

import argparse
import json
import time


CMD_SET_TX1_PHASE = 0x47
CMD_SET_TX2_PHASE = 0x48
CMD_GET_TX_PHASE = 0x49
TXP1_SIZE = 16
PHASES = {"in-phase": 0, "antiphase": 1}


def read_phase(dev):
    raw = bytes(dev.ctrl_transfer(0xC0, CMD_GET_TX_PHASE, 0, 0,
                                  TXP1_SIZE, timeout=500))
    if len(raw) != TXP1_SIZE or raw[:5] != b"TXP1\x01":
        raise RuntimeError("Invalid TXP1 response; firmware 1.2.46+ is required")
    if any(value > 1 for value in raw[5:9] + raw[10:14]):
        raise RuntimeError("Invalid TXP1 phase/pin value")
    flags = raw[9]
    return {
        "requested": [raw[5], raw[6]],
        "applied": [raw[7], raw[8]],
        "tx_requested": bool(flags & 1),
        "tx_enabled": bool(flags & 2),
        "streaming": bool(flags & 4),
        "pending": bool(flags & 8),
        "sync_phase": raw[10],
        "pa1": raw[11],
        "pa2": raw[12],
        "pc7": raw[13],
    }


def set_phase(dev, channel, phase):
    if channel not in (1, 2) or phase not in (0, 1):
        raise ValueError("channel must be 1/2 and phase must be 0/1")
    cmd = CMD_SET_TX1_PHASE if channel == 1 else CMD_SET_TX2_PHASE
    written = dev.ctrl_transfer(0x40, cmd, phase, 0, None, timeout=500)
    if int(written) != 0:
        raise RuntimeError("Unexpected SET_TX_PHASE control transfer length")


def wait_applied(dev, expected, timeout_s=0.5):
    deadline = time.monotonic() + timeout_s
    while True:
        state = read_phase(dev)
        if (state["requested"] == expected and state["applied"] == expected
                and not state["pending"]):
            return state
        if time.monotonic() >= deadline:
            raise RuntimeError(f"TX phase not applied: expected={expected}, state={state}")
        time.sleep(0.005)


def choose_device(vid, pid, serial_number, index):
    import usb.core

    devices = list(usb.core.find(find_all=True, idVendor=vid, idProduct=pid) or [])
    if serial_number:
        devices = [dev for dev in devices if dev.serial_number == serial_number]
    if not devices:
        raise RuntimeError("BMI30 USB device not found")
    if index is None:
        if len(devices) != 1:
            raise RuntimeError("Multiple BMI30 devices: select --serial or --index")
        index = 0
    if not 0 <= index < len(devices):
        raise ValueError(f"--index must be 0..{len(devices) - 1}")
    return devices[index]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ch1", choices=PHASES)
    parser.add_argument("--ch2", choices=PHASES)
    parser.add_argument("--serial", help="STM32 USB serial number")
    parser.add_argument("--index", type=int)
    parser.add_argument("--vid", type=lambda value: int(value, 0), default=0xCAFE)
    parser.add_argument("--pid", type=lambda value: int(value, 0), default=0x4001)
    args = parser.parse_args()

    import usb.util

    dev = choose_device(args.vid, args.pid, args.serial, args.index)
    try:
        # Check command support before changing anything on an older firmware.
        state = read_phase(dev)
        expected = state["requested"][:]
        for channel, name in enumerate((args.ch1, args.ch2), start=1):
            if name is not None:
                expected[channel - 1] = PHASES[name]
                set_phase(dev, channel, PHASES[name])
        if args.ch1 is not None or args.ch2 is not None:
            state = wait_applied(dev, expected)
        print(json.dumps(state, ensure_ascii=False, indent=2))
    finally:
        usb.util.dispose_resources(dev)


if __name__ == "__main__":
    main()
