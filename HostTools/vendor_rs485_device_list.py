#!/usr/bin/env python3
"""Read the replicated RS485 RPI-number/IP map and print it as JSON."""

import argparse
import ipaddress
import json
import struct
import time

import usb.core


CMD_SET_RS485_ID = 0x3D
CMD_REQUEST_RS485_IDENT = 0x3F
CMD_GET_RS485_IDENT = 0x40
CMD_SET_RPI_INFO = 0x42
RID1_SIZE = 32
RID1_SCAN_ACTIVE = 0x0020
RID1_LOCAL = 0x0008
RID1_RECENT = 0x0010
RID1_MASTER = 0x0080
RID1_RPI_NUMBER_VALID = 0x0200
RID1_DEVICE_ID_ASSIGNED = 0x0400


def read_ident(dev, catalog_id):
    raw = bytes(dev.ctrl_transfer(0xC0, CMD_GET_RS485_IDENT,
                                  catalog_id, 0, RID1_SIZE, timeout=500))
    if len(raw) != RID1_SIZE:
        raise RuntimeError(f"RID1[{catalog_id}] length={len(raw)}, expected 32")
    sig, version, node_id, flags, short_raw, ip_raw, page_mask, last_ms, rpi_number = \
        struct.unpack("<4sBBH10s4sIIH", raw)
    if sig != b"RID1" or version != 1:
        raise RuntimeError(f"RID1[{catalog_id}] invalid signature/version")
    short_id = short_raw.split(b"\0", 1)[0].decode("ascii", errors="replace")
    return {
        "catalog_id": catalog_id,
        "rpi_number": rpi_number,
        "node_id": node_id,
        "device_id_assigned": bool(flags & RID1_DEVICE_ID_ASSIGNED),
        "short_id": short_id,
        "ip": ".".join(str(value) for value in ip_raw),
        "flags": flags,
        "complete": bool(flags & 0x0004),
        "recent": bool(flags & 0x0010),
        "selected_slave": bool(flags & 0x0040),
        "master": bool(flags & 0x0080),
        "rpi_number_valid": bool(flags & RID1_RPI_NUMBER_VALID),
        "node_id_conflict": bool(flags & 0x0100),
        "seen_page_mask": page_mask,
        "last_ms": last_ms,
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vid", type=lambda value: int(value, 0), default=0xCAFE)
    parser.add_argument("--pid", type=lambda value: int(value, 0), default=0x4001)
    parser.add_argument("--serial")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="seconds to wait when this USB device is the master")
    parser.add_argument("--device-id", type=int,
                        help="assign this local STM32 device ID (0..31)")
    parser.add_argument("--rpi-number", type=int,
                        help="publish this local RPI number (0..65535)")
    parser.add_argument("--ip",
                        help="publish this local RPI IPv4 address")
    parser.add_argument("--no-scan", action="store_true",
                        help="only read the catalog already replicated to this device")
    args = parser.parse_args()

    dev = choose_device(args.vid, args.pid, args.serial, args.index)
    if args.device_id is not None:
        if not 0 <= args.device_id <= 31:
            raise SystemExit("--device-id must be 0..31")
        dev.ctrl_transfer(0x40, CMD_SET_RS485_ID,
                          args.device_id, 0, None, timeout=500)
    if (args.rpi_number is None) != (args.ip is None):
        raise SystemExit("--rpi-number and --ip must be provided together")
    if args.rpi_number is not None:
        if not 0 <= args.rpi_number <= 0xFFFF:
            raise SystemExit("--rpi-number must be 0..65535")
        ip_raw = ipaddress.IPv4Address(args.ip).packed
        payload = struct.pack("<H", args.rpi_number) + ip_raw
        dev.ctrl_transfer(0x40, CMD_SET_RPI_INFO,
                          0, 0, payload, timeout=500)

    local = read_ident(dev, 0xFF)
    if not args.no_scan:
        dev.ctrl_transfer(0x40, CMD_REQUEST_RS485_IDENT, 0, 0, None, timeout=500)
        # A master starts immediately and exposes SCAN_ACTIVE. A slave reads
        # its continuously replicated cache; its request is intentionally a
        # no-op because free-running RS485 request frames are not allowed.
        if local["flags"] & RID1_MASTER:
            deadline = time.monotonic() + max(0.1, args.timeout)
            saw_active = False
            while True:
                state = read_ident(dev, 0xFF)
                saw_active = saw_active or bool(state["flags"] & RID1_SCAN_ACTIVE)
                if saw_active and not (state["flags"] & RID1_SCAN_ACTIVE):
                    break
                if time.monotonic() >= deadline:
                    raise SystemExit("RS485 identity scan timeout on master")
                time.sleep(0.1)

    rows = []
    for catalog_id in range(32):
        row = read_ident(dev, catalog_id)
        if row["flags"] & (RID1_DEVICE_ID_ASSIGNED | 0x0001 | 0x0002 |
                           RID1_LOCAL | RID1_RECENT | RID1_MASTER):
            rows.append(row)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
