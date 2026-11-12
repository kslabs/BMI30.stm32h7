#!/usr/bin/env python3
from __future__ import annotations
import usb.core, usb.util, struct, time, sys
from typing import Optional

VID=0xCAFE; PID=0x4001
EP_IN=0x83; EP_OUT=0x03

CMD_SET_PROFILE   = 0x14
CMD_SET_FULL_MODE = 0x13
CMD_START_STREAM  = 0x20
CMD_STOP_STREAM   = 0x21

MAGIC=0xA55A
HDR_SIZE=32
HDR_FMT='<H B B I I H H I I I H H'

def open_dev():
    d=usb.core.find(idVendor=VID, idProduct=PID)
    if d is None:
        raise SystemExit('device not found')
    # detach possible kernel drivers on first few interfaces
    for i in range(0, 6):
        try:
            if d.is_kernel_driver_active(i):
                try: d.detach_kernel_driver(i)
                except Exception: pass
        except Exception:
            pass
    # ensure configuration active
    try:
        cfg = d.get_active_configuration()
    except usb.core.USBError:
        d.set_configuration()
        cfg = d.get_active_configuration()
    # pick interface with our EPs
    intf=None
    for it in cfg:
        eps=[e.bEndpointAddress for e in it.endpoints()]
        if EP_IN in eps and EP_OUT in eps:
            intf=it; break
    if intf is None:
        raise SystemExit('vendor intf with EP 0x83/0x03 not found')
    try:
        usb.util.claim_interface(d, intf.bInterfaceNumber)
    except Exception:
        pass
    return d, intf

def write_cmd(dev, cmd:int, payload:bytes=b''):
    pkt=bytes([cmd])+payload
    return dev.write(EP_OUT, pkt, timeout=1000)

def main():
    try:
        dev, intf = open_dev()
    except usb.core.USBError as e:
        if getattr(e, 'errno', None) == 16:
            print('[ERR] Interface busy by another process. Закройте GUI/скрипты и повторите.')
            return 2
        raise
    try:
        # Set desired profile 2 (300 Hz / 912 samples) and full mode
        try:
            write_cmd(dev, CMD_SET_PROFILE, bytes([2]))
            time.sleep(0.03)
            write_cmd(dev, CMD_SET_FULL_MODE, bytes([1]))
            time.sleep(0.03)
        except Exception:
            pass
        write_cmd(dev, CMD_START_STREAM)
        # Optional poke STAT once (some FW gates by permit_once)
        try:
            dev.write(EP_OUT, bytes([0x30]), timeout=500)  # CMD_GET_STATUS
        except Exception:
            pass
        t0 = time.time()
        buf = bytearray()
        frames_printed = 0
        MAGIC_LE = b"\x5A\xA5"
        # Read up to a few frames and dump first 100 samples per frame
        last_progress = time.time()
        only_stat_count = 0
        fallback_profile_done = False
        while frames_printed < 6 and time.time() - t0 < 12.0:
            try:
                data = bytes(dev.read(EP_IN, 4096, timeout=800))
            except usb.core.USBError as e:
                if getattr(e, 'errno', None) == 110:  # timeout
                    continue
                # EPIPE/ENODEV
                raise
            if not data:
                continue
            # STAT coalesced?
            if data[:4] == b'STAT':
                print(f"STAT len={len(data)} head={data[:16].hex()}")
                if len(data) > 64:
                    data = data[64:]
                else:
                    only_stat_count += 1
                    # nudge the device if we see only STATs for too long
                    if (time.time() - last_progress) > 2.0 and only_stat_count >= 2:
                        try:
                            write_cmd(dev, CMD_START_STREAM)
                            print('[nudge] re-START sent')
                            last_progress = time.time()
                        except Exception:
                            pass
                    continue
            else:
                # debug head if not a framed buffer yet
                if len(data) < HDR_SIZE:
                    print(f"CHUNK len={len(data)} head={data[:16].hex()}")
            buf.extend(data)
            # Try to deframe one or more frames
            while True:
                if len(buf) < HDR_SIZE:
                    break
                if not (buf[0] == 0x5A and buf[1] == 0xA5):
                    idx = buf.find(MAGIC_LE)
                    if idx == -1:
                        del buf[:max(0, len(buf) - 1)]
                        break
                    else:
                        del buf[:idx]
                        if len(buf) < HDR_SIZE:
                            break
                hdr = bytes(buf[:HDR_SIZE])
                try:
                    (magic, ver, flags, seq, ts, total_samples, zc, z1o, z1l, r1, r2, crc) = struct.unpack(HDR_FMT, hdr)
                except struct.error:
                    break
                if magic != MAGIC:
                    del buf[0]
                    continue
                payload_len = int(total_samples) * 2
                frame_total = HDR_SIZE + payload_len
                if len(buf) < frame_total:
                    break
                payload = bytes(buf[HDR_SIZE:frame_total])
                del buf[:frame_total]
                # Skip the TEST frame (0x80) if any
                if (flags & 0x80) and total_samples == 8:
                    print(f"[TEST] seq={seq} total=8")
                    continue
                ch = 0 if (flags & 0x01) else (1 if (flags & 0x02) else -1)
                if ch == -1:
                    # unknown flags, skip
                    continue
                # First 100 samples (or less) as signed 16-bit LE
                n = min(100, len(payload) // 2)
                samples = list(struct.unpack('<' + 'h' * n, payload[:n * 2]))
                print(f"FRAME seq={seq} ch={ch} total={total_samples} n_print={n}")
                print(' '.join(str(x) for x in samples))
                frames_printed += 1
                last_progress = time.time()
            # if no frames for a while, try STOP/START once
            if (time.time() - last_progress) > 3.5 and frames_printed == 0:
                try:
                    write_cmd(dev, CMD_STOP_STREAM)
                    time.sleep(0.05)
                    # try current profile again; if already tried, fallback to profile 1 (200 Hz)
                    if not fallback_profile_done:
                        write_cmd(dev, CMD_SET_PROFILE, bytes([2]))
                    else:
                        write_cmd(dev, CMD_SET_PROFILE, bytes([1]))
                    time.sleep(0.02)
                    write_cmd(dev, CMD_SET_FULL_MODE, bytes([1]))
                    time.sleep(0.02)
                    write_cmd(dev, CMD_START_STREAM)
                    print('[recover] STOP→SET_PROFILE→FULL→START sent')
                    last_progress = time.time()
                except Exception:
                    pass
            # If still no frames after another interval, switch to profile 1 once
            if (time.time() - t0) > 7.0 and frames_printed == 0 and not fallback_profile_done:
                try:
                    write_cmd(dev, CMD_STOP_STREAM)
                    time.sleep(0.03)
                    write_cmd(dev, CMD_SET_PROFILE, bytes([1]))
                    time.sleep(0.02)
                    write_cmd(dev, CMD_SET_FULL_MODE, bytes([1]))
                    time.sleep(0.02)
                    write_cmd(dev, CMD_START_STREAM)
                    print('[fallback] switched to profile 1 (200 Hz)')
                    fallback_profile_done = True
                    last_progress = time.time()
                except Exception:
                    pass
        # stop stream
        try:
            write_cmd(dev, CMD_STOP_STREAM)
        except Exception:
            pass
        if frames_printed == 0:
            print('[WARN] No working frames read. Возможно поток не идёт или устройство занято.')
            return 1
        return 0
    finally:
        try:
            usb.util.release_interface(dev, intf.bInterfaceNumber)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass

if __name__=='__main__':
    sys.exit(main())
