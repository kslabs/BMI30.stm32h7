#!/usr/bin/env python3
"""Dump the raw USB ADC frames and print header diagnostics.

This script connects to the BMI30 device, optionally sends the standard
start sequence (STOP + configuration + START), and then reads a limited
number of incoming USB frames. For each valid ADC frame it prints the
header fields, including the diagnostic parity markers that are currently
forced in firmware (flags bit 7 and reserved2).

Usage examples:
  py -3 HostTools/dump_raw_frames.py             # start stream, dump 8 frames
  py -3 HostTools/dump_raw_frames.py --frames 4  # only four frames
  py -3 HostTools/dump_raw_frames.py --no-start  # attach to existing stream
"""

import argparse
import struct
import sys
import time
from typing import Optional

import usb.core
import usb.util

VENDOR = 0xCAFE
PRODUCT = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83
HDR_SIZE = 32
MAX_SAMPLES = 4096

CMD_START = 0x20
CMD_STOP = 0x21
CMD_SET_ASYNC_MODE = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14


def crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def find_device() -> usb.core.Device:
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
    if dev is None:
        raise SystemExit(f"Device {VENDOR:04X}:{PRODUCT:04X} not found")

    def warn(label: str, err: Exception) -> None:
        print(f"[USB][WARN] {label}: {err}")

    try:
        dev.set_configuration()
    except Exception as e:  # already configured is fine
        warn("set_configuration", e)

    try:
        usb.util.claim_interface(dev, INTERFACE)
    except Exception as e:
        warn("claim_interface", e)

    try:
        dev.set_interface_altsetting(INTERFACE, 1)
    except Exception as e:
        warn("set_interface_altsetting(alt=1)", e)

    try:
        cfg = dev.get_active_configuration()
        intf = cfg[(INTERFACE, 1)]
        eps = [ep.bEndpointAddress for ep in intf]
        if EP_IN not in eps or EP_OUT not in eps:
            warn("endpoint_check", f"expected EP_OUT=0x{EP_OUT:02X}, EP_IN=0x{EP_IN:02X}, got {eps}")
    except Exception as e:
        warn("endpoint_check", e)

    return dev


def send_cmd(dev: usb.core.Device, cmd_byte: int, data: Optional[bytes] = None) -> bool:
    pkt = bytearray([cmd_byte])
    if data:
        pkt.extend(data)
    try:
        dev.write(EP_OUT, pkt, timeout=500)
        return True
    except Exception as e:
        print(f"[ERROR] send_cmd({cmd_byte:02X}): {e}")
        return False


def parse_hdr(buf: bytes):
    if len(buf) < HDR_SIZE:
        return None
    try:
        magic, ver, flags, seq, ts, total_samples, zone_cnt, zone_off, zone_len, reserved, reserved2, crc16 = struct.unpack_from(
            '<HBBIIHHIIIHH', buf, 0
        )
    except struct.error:
        return None
    return {
        'magic': magic,
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'ts': ts,
        'ns': total_samples,
        'zone_cnt': zone_cnt,
        'zone_off': zone_off,
        'zone_len': zone_len,
        'reserved': reserved,
        'reserved2': reserved2,
        'crc16': crc16,
    }


def dump_frames(args: argparse.Namespace) -> None:
    dev = find_device()
    started = False

    try:
        if not args.no_start:
            started = True
            print("[CMD] Stopping previous stream...")
            send_cmd(dev, CMD_STOP)
            time.sleep(0.2)

            print("[CMD] Configuring device...")
            # Mode byte: bit0=async(0=paired,1=async), bit7=strict_pairing(1=strict,0=independent)
            # For 400Hz even/odd mode we need: async=0 (paired), strict_pairing=1 → mode=0x80
            mode_byte = 0x80 if not args.async_mode else 0x01
            send_cmd(dev, CMD_SET_ASYNC_MODE, bytes([mode_byte]))
            time.sleep(0.05)
            send_cmd(dev, CMD_SET_CHMODE, bytes([args.ch_mode]))
            time.sleep(0.05)
            send_cmd(dev, CMD_SET_FULL_MODE, bytes([0x01]))
            time.sleep(0.05)
            send_cmd(dev, CMD_SET_PROFILE, bytes([args.profile]))
            time.sleep(0.05)

            print("[CMD] Sending START...")
            if not send_cmd(dev, CMD_START):
                print("[WARN] START command failed")
            time.sleep(0.2)

        frame_goal = args.frames
        frame_count = 0
        stat_skip = 0
        rx_buffer = bytearray()
        start_ts = time.time()

        print(f"[INFO] Waiting for {frame_goal} ADC frames...")

        while frame_count < frame_goal:
            try:
                chunk = dev.read(EP_IN, args.packet_size, timeout=args.rx_timeout)
            except usb.core.USBError as e:
                if getattr(e, 'errno', None) in (110, 10060) or 'timed out' in str(e).lower():
                    continue
                print(f"[USB] Read error: {e}")
                break

            if not chunk:
                continue

            rx_buffer.extend(chunk)

            while True:
                if len(rx_buffer) < 4:
                    break

                if rx_buffer[0:4] == b'STAT':
                    if len(rx_buffer) >= 84:
                        rx_buffer = rx_buffer[84:]
                        stat_skip += 1
                        continue
                    if len(rx_buffer) >= 64:
                        rx_buffer = rx_buffer[64:]
                        stat_skip += 1
                        continue
                    if len(rx_buffer) >= 52:
                        rx_buffer = rx_buffer[52:]
                        stat_skip += 1
                        continue
                    break

                if rx_buffer[0] != 0x5A or rx_buffer[1] != 0xA5 or rx_buffer[2] != 0x01:
                    rx_buffer.pop(0)
                    continue

                if len(rx_buffer) < HDR_SIZE:
                    break

                total_samples = rx_buffer[12] | (rx_buffer[13] << 8)
                if total_samples == 0 or total_samples > MAX_SAMPLES:
                    rx_buffer.pop(0)
                    continue

                frame_len = HDR_SIZE + total_samples * 2
                if len(rx_buffer) < frame_len:
                    break

                frame_bytes = bytes(rx_buffer[:frame_len])
                del rx_buffer[:frame_len]

                hdr = parse_hdr(frame_bytes)
                if not hdr or hdr['magic'] != 0xA55A:
                    continue

                payload = frame_bytes[HDR_SIZE:HDR_SIZE + hdr['ns'] * 2]
                crc_ok = True
                if hdr['flags'] & 0x04:
                    crc_ok = crc16_ccitt(payload) == hdr['crc16']

                flags = hdr['flags']
                parity_flag = (flags >> 7) & 0x01
                parity_res2 = hdr['reserved2'] & 0x01
                ch_mask = flags & 0x03
                channel = 'A' if ch_mask == 0x01 else ('B' if ch_mask == 0x02 else f"0x{ch_mask:02X}")

                print(f"[{frame_count:03d}] ch={channel} flags=0x{flags:02X} seq={hdr['seq']} reserved=0x{hdr['reserved']:04X} "
                      f"reserved2=0x{hdr['reserved2']:04X} parity_flag={parity_flag} parity_res2={parity_res2} "
                      f"ns={hdr['ns']} crc=0x{hdr['crc16']:04X} crc_ok={'OK' if crc_ok else 'FAIL'}")

                if frame_count < args.hex_frames:
                    header_hex = ' '.join(f"{b:02X}" for b in frame_bytes[:HDR_SIZE])
                    print(f"      hdr: {header_hex}")

                frame_count += 1

                if frame_count >= frame_goal:
                    break

        elapsed = time.time() - start_ts
        print(f"\n[INFO] Collected {frame_count} frame(s) in {elapsed:.2f}s, skipped {stat_skip} status packet(s)")

    finally:
        if started:
            print("[CMD] Sending STOP...")
            send_cmd(dev, CMD_STOP)
            time.sleep(0.1)
        try:
            usb.util.release_interface(dev, INTERFACE)
        except Exception:
            pass
        usb.util.dispose_resources(dev)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump raw BMI30 USB frames")
    parser.add_argument('--frames', type=int, default=8, help='How many ADC frames to dump (default: 8)')
    parser.add_argument('--hex-frames', type=int, default=4, help='How many frames should also print header hex (default: 4)')
    parser.add_argument('--profile', type=int, default=0, help='Profile ID to apply when starting stream (default: 0)')
    parser.add_argument('--async', dest='async_mode', action='store_true', help='Enable async mode (default off)')
    parser.add_argument('--ch-mode', type=int, default=0x02, help='Channel mode byte for CMD_SET_CHMODE (default: 0x02 = both A+B)')
    parser.add_argument('--rx-timeout', type=int, default=200, help='USB read timeout in ms (default: 200)')
    parser.add_argument('--packet-size', type=int, default=4096, help='Endpoint read size in bytes (default: 4096)')
    parser.add_argument('--no-start', action='store_true', help='Attach to existing stream, do not send stop/config/start commands')

    args = parser.parse_args()

    try:
        dump_frames(args)
    except KeyboardInterrupt:
        print("\n[MAIN] Interrupted by user")
    except Exception as exc:
        print(f"[FATAL] {exc}")
        sys.exit(1)


if __name__ == '__main__':
    main()
