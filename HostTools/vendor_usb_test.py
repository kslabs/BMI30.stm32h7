#!/usr/bin/env python3
# Host-side test for USB Vendor interface (bulk OUT 0x03, IN 0x83)
# Requires: pyusb (pip install pyusb)
# On Windows, install WinUSB driver for the Vendor interface (use Zadig) if needed.

import sys
import time
import struct
import usb.core
import usb.util

VND_OUT = 0x03
VND_IN  = 0x83

CMD_START = 0x20
CMD_STOP  = 0x21
CMD_GET_STATUS = 0x30

MAG0 = 0x5A
MAG1 = 0xA5


def find_device(vid=None, pid=None):
    dev = None
    if vid and pid:
        print(f"Using VID=0x{vid:04X} PID=0x{pid:04X}")
        dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        # Fallback: first HS device with 3 interfaces and vendor IF (class=0xFF)
        for d in usb.core.find(find_all=True):
            try:
                cfg = d.get_active_configuration()
            except Exception:
                continue
            # naive pick
            dev = d
            break
    return dev


def claim_vendor_interface(dev):
    # Find interface with two bulk endpoints: OUT 0x03, IN 0x83
    cfg = dev.get_active_configuration()
    vnd_if = None
    for intf in cfg:
        eps = [e for e in intf]
        addrs = [e.bEndpointAddress for e in eps]
        if VND_OUT in addrs and VND_IN in addrs:
            vnd_if = intf
            break
    if vnd_if is None:
        raise RuntimeError("Vendor interface (EP 0x03/0x83) not found")
    if dev.is_kernel_driver_active(vnd_if.bInterfaceNumber):
        dev.detach_kernel_driver(vnd_if.bInterfaceNumber)
    usb.util.claim_interface(dev, vnd_if.bInterfaceNumber)
    return vnd_if


def send_cmd(dev, data: bytes, timeout=200):
    return dev.write(VND_OUT, data, timeout=timeout)


def read_exact(dev, n, timeout=1000):
    data = bytes()
    while len(data) < n:
        chunk = dev.read(VND_IN, n - len(data), timeout=timeout)
        data += bytes(chunk)
    return data


def read_frame(dev, timeout=2000):
    # Read header (32 bytes)
    hdr = read_exact(dev, 32, timeout=timeout)
    if hdr[0] != MAG0 or hdr[1] != MAG1:
        raise RuntimeError(f"Bad magic: {hdr[:4].hex()}")
    ver = hdr[2]
    flags = hdr[3]
    (seq, ts, total_samples) = struct.unpack_from('<IIH', hdr, 4)
    payload = read_exact(dev, total_samples * 2, timeout=timeout)
    return {
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'ts': ts,
        'samples': total_samples,
        'payload_len': len(payload)
    }


def main():
    vid = None
    pid = None
    if len(sys.argv) == 3:
        try:
            vid = int(sys.argv[1], 16)
            pid = int(sys.argv[2], 16)
            print(f"Using VID=0x{vid:04X} PID=0x{pid:04X}")
        except Exception:
            print("Usage: vendor_usb_test.py [VID PID]  (hex, e.g. 0483 5740)")
            sys.exit(2)
    elif len(sys.argv) not in (1,3):
        print("Usage: vendor_usb_test.py [VID PID]  (hex, e.g. 0483 5740)")
        sys.exit(2)

    # Default to project VID/PID if not specified
    if vid is None and pid is None:
        vid, pid = 0xCAFE, 0x4001
    dev = find_device(vid, pid)
    if dev is None:
        print("Device not found")
        sys.exit(1)
    dev.set_configuration()
    claim_vendor_interface(dev)
    print("Device ready")

    print("START")
    send_cmd(dev, bytes([CMD_START]))
    # Expect one test frame
    f = read_frame(dev, timeout=2000)
    print("TEST:", f)
    if not (f['ver']==1 and f['flags']==0x80 and f['samples']==8):
        print("Unexpected test frame header", f)
    # Expect a pair A/B (1856 each if samples=912)
    a = read_frame(dev, timeout=2000)
    b = read_frame(dev, timeout=2000)
    print("A:", a)
    print("B:", b)
    if not (a['flags']==0x01 and b['flags']==0x02 and a['seq']==b['seq'] and a['samples']==b['samples']):
        print("Unexpected pair headers")
    print("STOP")
    send_cmd(dev, bytes([CMD_STOP]))


if __name__ == '__main__':
    main()
