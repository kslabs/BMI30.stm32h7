#!/usr/bin/env python3
import sys, time, struct, argparse, statistics
import usb.core, usb.util

VENDOR=0xCAFE
PRODUCT=0x4001
INTERFACE=2
EP_OUT=0x03
EP_IN=0x83

# Commands
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
    # Windows/WinUSB safe guards
    try:
        if hasattr(dev, 'is_kernel_driver_active') and dev.is_kernel_driver_active(INTERFACE):
            try:
                dev.detach_kernel_driver(INTERFACE)
            except Exception:
                pass
    except Exception:
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
    data = dev.read(EP_IN, 2048, timeout=timeout_ms)
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', type=int, default=200, help='how many A/B pairs to sample')
    ap.add_argument('--ns', type=int, default=300, help='samples per channel expected')
    ap.add_argument('--profile', type=int, default=2)
    args = ap.parse_args()

    dev = find_dev()
    print(f"[HOST] Claimed intf {INTERFACE}")

    # Configure to Ns=300 and full mode
    win_payload = [CMD_SET_WINDOWS] + le16(100) + le16(args.ns) + le16(700) + le16(args.ns)
    send_cmd(dev, bytes(win_payload))
    send_cmd(dev, bytes([CMD_SET_FRAME_SAMPLES] + le16(args.ns)))
    send_cmd(dev, bytes([CMD_SET_FULL_MODE, 1]))
    send_cmd(dev, bytes([CMD_SET_PROFILE, args.profile]))

    send_cmd(dev, bytes([CMD_START]))

    ts_list = []  # device timestamps (ms) for A frames
    host_t_list = []  # host monotonic for A frames
    last_seq = None

    try:
        while len(ts_list) < args.pairs:
            fa = read_frame(dev, timeout_ms=2000)
            ha = parse_hdr(fa)
            if not ha or ha['magic'] != 0xA55A:
                continue
            if ha['flags'] != 0x01:  # A only
                continue
            # Keep only first A per pair seq
            if last_seq is None or ha['seq'] != last_seq:
                ts_list.append(ha['ts'])
                host_t_list.append(time.perf_counter())
                last_seq = ha['seq']

        # Compute device-based Hz (delta of HAL ticks)
        dts = [ts_list[i+1] - ts_list[i] for i in range(len(ts_list)-1)]
        # Handle wrap-around (HAL tick is 32-bit ms). If negative, add 2^32.
        dts = [(dt + (1<<32)) if dt < 0 else dt for dt in dts]
        avg_ms = statistics.mean(dts) if dts else 0.0
        med_ms = statistics.median(dts) if dts else 0.0
        hz_dev_avg = 1000.0/avg_ms if avg_ms > 0 else 0.0
        hz_dev_med = 1000.0/med_ms if med_ms > 0 else 0.0

        # Host-based Hz (arrival spacing for A frames)
        hts = [ (host_t_list[i+1] - host_t_list[i]) * 1000.0 for i in range(len(host_t_list)-1) ]
        h_avg = statistics.mean(hts) if hts else 0.0
        h_med = statistics.median(hts) if hts else 0.0
        hz_host_avg = 1000.0/h_avg if h_avg > 0 else 0.0
        hz_host_med = 1000.0/h_med if h_med > 0 else 0.0

        print(f"PAIRS={len(ts_list)}; Device tick: avg={avg_ms:.3f} ms med={med_ms:.3f} ms -> {hz_dev_avg:.2f} Hz (avg), {hz_dev_med:.2f} Hz (med)")
        print(f"Host recv:     avg={h_avg:.3f} ms med={h_med:.3f} ms -> {hz_host_avg:.2f} Hz (avg), {hz_host_med:.2f} Hz (med)")

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
