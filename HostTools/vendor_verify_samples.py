#!/usr/bin/env python3
import sys, time, struct, argparse
import usb.core, usb.util

VENDOR=0xCAFE
PRODUCT=0x4001
INTERFACE=2
EP_OUT=0x03
EP_IN=0x83

# Commands (match firmware)
CMD_START=0x20
CMD_STOP=0x21
CMD_SET_WINDOWS=0x10
CMD_SET_BLOCK_HZ=0x11
CMD_SET_TRUNC_SAMPLES=0x16
CMD_SET_FRAME_SAMPLES=0x17
CMD_SET_FULL_MODE=0x13
CMD_SET_PROFILE=0x14

HDR_SIZE=32

def le16(x:int):
    return [x & 0xFF, (x >> 8) & 0xFF]


def find_dev():
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
    if dev is None:
        raise SystemExit("Device not found")
    # On Windows with WinUSB backend, kernel detach is not applicable; guard for NotImplementedError
    try:
        if hasattr(dev, 'is_kernel_driver_active') and dev.is_kernel_driver_active(INTERFACE):
            try:
                dev.detach_kernel_driver(INTERFACE)
            except Exception:
                pass
    except Exception:
        # Ignore unsupported ops on this platform
        pass
    dev.set_configuration()
    try:
        usb.util.claim_interface(dev, INTERFACE)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
    except Exception:
        pass
    return dev


def send_cmd(dev, data: bytes):
    dev.write(EP_OUT, data, timeout=1000)


def read_frame(dev, timeout_ms=3000):
    data = dev.read(EP_IN, 2048, timeout=timeout_ms)  # HS max packet multiple
    return bytes(data)


def parse_hdr(b: bytes):
    if len(b) < HDR_SIZE:
        return None
    magic, ver, flags, seq, ts, ns, zc = struct.unpack_from('<HBBIIHH', b, 0)
    return {
        'magic': magic,
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'ts': ts,
        'ns': ns,
    }


def verify_samples(payload: bytes, ns: int) -> int:
    # payload = ns * 2 bytes LE
    errs = 0
    for i in range(ns):
        if 2*i+1 >= len(payload):
            errs += 1
            break
        v = payload[2*i] | (payload[2*i+1] << 8)
        expected = i+1
        if v != expected:
            errs += 1
            if errs <= 10:
                print(f"Mismatch at {i}: got {v}, expected {expected}")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', type=int, default=5, help='how many A/B pairs to verify')
    ap.add_argument('--ns', type=int, default=300, help='expected samples per channel')
    ap.add_argument('--profile', type=int, default=2)
    args = ap.parse_args()

    dev = find_dev()
    print(f"[HOST] Claimed intf {INTERFACE}")

    # Configure
    win_payload = [CMD_SET_WINDOWS] + le16(100) + le16(args.ns) + le16(700) + le16(args.ns)
    send_cmd(dev, bytes(win_payload))
    send_cmd(dev, bytes([CMD_SET_BLOCK_HZ] + le16(200)))
    send_cmd(dev, bytes([CMD_SET_FRAME_SAMPLES] + le16(args.ns)))
    send_cmd(dev, bytes([CMD_SET_FULL_MODE, 1]))
    send_cmd(dev, bytes([CMD_SET_PROFILE, args.profile]))

    # START
    send_cmd(dev, bytes([CMD_START]))

    ok_pairs = 0
    fail_pairs = 0
    last_seq = None

    try:
        while ok_pairs + fail_pairs < args.pairs:
            # Read A
            fa = read_frame(dev)
            ha = parse_hdr(fa)
            if not ha or ha['magic'] != 0xA55A or ha['flags'] != 0x01:
                print("Unexpected A frame header", ha)
                fail_pairs += 1
                continue
            if last_seq is not None and ha['seq'] != last_seq:
                # new pair
                pass
            last_seq = ha['seq']
            pa = fa[HDR_SIZE:HDR_SIZE+ha['ns']*2]
            ea = verify_samples(pa, args.ns)

            # Read B
            fb = read_frame(dev)
            hb = parse_hdr(fb)
            if not hb or hb['magic'] != 0xA55A or hb['flags'] != 0x02 or hb['seq'] != last_seq:
                print("Unexpected B frame header", hb)
                fail_pairs += 1
                continue
            pb = fb[HDR_SIZE:HDR_SIZE+hb['ns']*2]
            eb = verify_samples(pb, args.ns)

            if ea == 0 and eb == 0:
                ok_pairs += 1
                if ok_pairs <= 3:
                    print(f"Pair seq={last_seq} OK (ns={args.ns})")
            else:
                fail_pairs += 1
                print(f"Pair seq={last_seq} FAIL: A errs={ea}, B errs={eb}")

        print(f"RESULT: ok_pairs={ok_pairs} fail_pairs={fail_pairs}")
    finally:
        try:
            send_cmd(dev, bytes([CMD_STOP]))
        except Exception:
            pass
        usb.util.release_interface(dev, INTERFACE)
        try:
            dev.attach_kernel_driver(INTERFACE)
        except Exception:
            pass

if __name__ == '__main__':
    main()
