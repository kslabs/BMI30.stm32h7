#!/usr/bin/env python3
"""Control the independent BMI30 relay over EP0 (firmware 1.2.50+)."""
import argparse
import json
import struct

MAX_DURATION_MS = 0x7FFFFFFF


def read_status(dev):
    raw = bytes(dev.ctrl_transfer(0xC0, 0x4C, 0, 0, 20, timeout=500))
    if len(raw) != 20 or raw[:5] != b'RLY1\x01':
        raise RuntimeError('Invalid RLY1 response; firmware 1.2.50+ is required')
    remaining, maximum = struct.unpack_from('<II', raw, 12)
    enabled, active, pin, mode = raw[5:9]
    if (max(enabled, active, pin) > 1 or mode > 2 or raw[9:12] != b'\0'*3
            or maximum != MAX_DURATION_MS
            or (not enabled and (active or mode))
            or active != int(mode != 0)
            or (mode == 0 and remaining != 0)
            or (mode == 1 and not 1 <= remaining <= maximum)
            or (mode == 2 and remaining != 0xFFFFFFFF)):
        raise RuntimeError('Invalid RLY1 state')
    return dict(enabled=bool(enabled), active=bool(active), pc1=pin, mode=mode,
                remaining_ms=remaining, max_duration_ms=maximum)


def _set_bool(dev, opcode, value):
    if not isinstance(value, int) or value not in (0, 1):
        raise ValueError('Relay flag must be 0 or 1')
    if dev.ctrl_transfer(0x40, opcode, int(value), 0, None, timeout=500) != 0:
        raise RuntimeError('Unexpected relay control transfer length')


def set_enabled(dev, enabled):
    _set_bool(dev, 0x4A, enabled)


def set_test(dev, active):
    _set_bool(dev, 0x4D, active)


def trigger(dev, duration_ms):
    if not isinstance(duration_ms, int) or not 0 <= duration_ms <= MAX_DURATION_MS:
        raise ValueError('duration_ms must be an integer in 0..0x7FFFFFFF')
    payload = struct.pack('<I', duration_ms)
    if dev.ctrl_transfer(0x40, 0x4B, 0, 0, payload, timeout=500) != len(payload):
        raise RuntimeError('Incomplete relay event transfer')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', type=int, choices=(0, 1))
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--pulse-ms', type=int)
    action.add_argument('--test', type=int, choices=(0, 1))
    action.add_argument('--stop', action='store_true')
    parser.add_argument('--serial')
    parser.add_argument('--index', type=int)
    parser.add_argument('--vid', type=lambda s: int(s, 0), default=0xCAFE)
    parser.add_argument('--pid', type=lambda s: int(s, 0), default=0x4001)
    args = parser.parse_args()
    if args.pulse_ms is not None and not 0 <= args.pulse_ms <= MAX_DURATION_MS:
        parser.error('--pulse-ms must be 0..0x7FFFFFFF')
    if args.enable == 0 and (args.test == 1 or (args.pulse_ms or 0) > 0):
        parser.error('Cannot start a relay event/test while disabling permission')
    import usb.util
    from vendor_tx_phase import choose_device
    dev = choose_device(args.vid, args.pid, args.serial, args.index)
    try:
        state = read_status(dev)  # Probe support before any changes.
        permission = state['enabled'] if args.enable is None else bool(args.enable)
        if not permission and (args.test == 1 or (args.pulse_ms or 0) > 0):
            parser.error('Relay is disabled; explicitly use --enable 1 first')
        if args.enable is not None:
            set_enabled(dev, args.enable)
        if args.pulse_ms is not None or args.stop:
            trigger(dev, 0 if args.stop else args.pulse_ms)
        if args.test is not None:
            set_test(dev, args.test)
        print(json.dumps(read_status(dev), indent=2))
    finally:
        usb.util.dispose_resources(dev)


if __name__ == '__main__':
    main()
