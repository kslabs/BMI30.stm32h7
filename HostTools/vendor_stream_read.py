#!/usr/bin/env python3
# Minimal Vendor streaming reader for STM32H7 (Bulk OUT cmds @0x03, IN data @0x83)
# - Sends SET_PROFILE/SET_FRAME_SAMPLES/SET_FULL_MODE then START
# - Reads frames, verifies strict A→B ordering (B immediately after A),
#   allows STAT only between pairs, prints brief stats and FPS.
# - Optionally requests STAT with GET_STATUS as a keepalive.

import sys, struct, time, argparse
import usb.core, usb.util

# Commands (must match firmware)
VND_CMD_START_STREAM      = 0x20
VND_CMD_STOP_STREAM       = 0x21
VND_CMD_GET_STATUS        = 0x30
VND_CMD_SET_WINDOWS       = 0x10
VND_CMD_SET_BLOCK_HZ      = 0x11
VND_CMD_SET_TRUNC_SAMPLES = 0x16
VND_CMD_SET_FRAME_SAMPLES = 0x17
VND_CMD_SET_FULL_MODE     = 0x13
VND_CMD_SET_PROFILE       = 0x14

MAGIC = 0xA55A


def find_device(vid, pid):
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        raise SystemExit(f"Device VID=0x{vid:04x} PID=0x{pid:04x} not found")
    try:
        dev.set_configuration()
    except Exception:
        pass
    return dev


def claim_interface(dev, intf_num):
    cfg = dev.get_active_configuration()
    intf = usb.util.find_descriptor(cfg, bInterfaceNumber=intf_num)
    if intf is None:
        raise SystemExit(f"Interface #{intf_num} not found")
    # On Windows, is_kernel_driver_active may be unimplemented; proceed best-effort
    try:
        if dev.is_kernel_driver_active(intf_num):
            try:
                dev.detach_kernel_driver(intf_num)
            except Exception:
                pass
    except NotImplementedError:
        pass
    try:
        usb.util.claim_interface(dev, intf_num)
    except Exception:
        pass
    # Ensure alternate setting 1 is selected to activate Vendor endpoints
    try:
        dev.set_interface_altsetting(interface=intf_num, alternate_setting=1)
    except Exception as e:
        # On some stacks alt may already be 1 or unsupported to set explicitly; proceed
        pass
    return intf


def send_cmd(dev, ep_out, data: bytes):
    dev.write(ep_out, data, timeout=1000)


def le16(v):
    return struct.pack('<H', v)


def le32(v):
    return struct.pack('<I', v)


def parse_frame(buf: bytes):
    if len(buf) < 32:
        return None
    magic, ver, flags, seq, ts, total_samples, zone_cnt = struct.unpack_from('<HBBIIHH', buf, 0)[:7]
    if magic != MAGIC:
        return None
    total = 32 + total_samples * 2
    if total != len(buf):
        # Allow short reads with extra zero padding on some stacks
        if len(buf) < total:
            return None
        buf = buf[:total]
    return {
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'ts': ts,
        'ns': total_samples,
        'len': len(buf),
        'raw': buf,
    }

def parse_stat(buf: bytes):
    if len(buf) < 64 or buf[:4] != b'STAT':
        return None
    st = {}
    st['ver'] = buf[4]
    st['cur_samples'] = int.from_bytes(buf[6:8], 'little')
    st['frame_bytes'] = int.from_bytes(buf[8:10], 'little')
    st['test_frames'] = int.from_bytes(buf[10:12], 'little')
    st['produced_seq'] = int.from_bytes(buf[12:16], 'little')
    st['sent0'] = int.from_bytes(buf[16:20], 'little')
    st['sent1'] = int.from_bytes(buf[20:24], 'little')
    st['dbg_tx_cplt'] = int.from_bytes(buf[24:28], 'little')
    st['dbg_partial'] = int.from_bytes(buf[28:32], 'little')
    st['dbg_size_mismatch'] = int.from_bytes(buf[32:36], 'little')
    st['dma0'] = int.from_bytes(buf[36:40], 'little')
    st['dma1'] = int.from_bytes(buf[40:44], 'little')
    st['wr'] = int.from_bytes(buf[44:48], 'little')
    st['flags_rt'] = int.from_bytes(buf[48:50], 'little')
    st['flags2'] = int.from_bytes(buf[50:52], 'little')
    st['sending_ch'] = buf[52]
    st['pair_idx'] = int.from_bytes(buf[54:56], 'little')
    st['last_tx_len'] = int.from_bytes(buf[56:58], 'little')
    st['cur_stream_seq'] = int.from_bytes(buf[58:62], 'little')
    return st


def main():
    ap = argparse.ArgumentParser(description='Vendor stream reader (Bulk IN 0x83, OUT 0x03)')
    ap.add_argument('--vid', type=lambda x: int(x,0), default=0xCAFE)
    ap.add_argument('--pid', type=lambda x: int(x,0), default=0x4001)
    ap.add_argument('--intf', type=int, default=2, help='Vendor interface number')
    ap.add_argument('--ep-in', type=lambda x: int(x,0), default=0x83)
    ap.add_argument('--ep-out', type=lambda x: int(x,0), default=0x03)
    ap.add_argument('--profile', type=int, default=2, help='1=A(200Hz), 2=B(default)')
    ap.add_argument('--frame-samples', type=int, default=10, help='Samples per channel per frame (A and B)')
    ap.add_argument('--full-mode', type=int, default=1, help='1=ADC mode, 0=diagnostic')
    ap.add_argument('--block-hz', type=int, default=200, help='ADC block rate hint')
    ap.add_argument('--frames', type=int, default=200)
    ap.add_argument('--timeout', type=int, default=300, help='IN timeout ms')
    # Windows (time windows) configuration like in quick-start script
    ap.add_argument('--win0-start', type=int, default=100)
    ap.add_argument('--win0-len', type=int, default=300)
    ap.add_argument('--win1-start', type=int, default=700)
    ap.add_argument('--win1-len', type=int, default=300)
    ap.add_argument('--status-interval', type=float, default=0.5, help='Request GET_STATUS every N seconds (0=off)')
    ap.add_argument('--ctrl-status', action='store_true', help='Use control transfer for GET_STATUS (works even mid-pair)')
    ap.add_argument('--ab-strict', action='store_true', help='Fail if A→B ordering is violated or STAT appears mid-pair')
    ap.add_argument('--quiet', action='store_true', help='Reduce per-frame prints, show only summary and warnings')
    args = ap.parse_args()

    dev = find_device(args.vid, args.pid)
    intf = claim_interface(dev, args.intf)
    ep_in = args.ep_in
    ep_out = args.ep_out
    print(f"Opened VID=0x{args.vid:04X} PID=0x{args.pid:04X} IF#{args.intf} IN=0x{ep_in:02X} OUT=0x{ep_out:02X}")

    # Configure
    # Set windows first (some firmware profiles expect non-zero windows to start streaming)
    try:
        payload = struct.pack('<BHHHH', VND_CMD_SET_WINDOWS, args.win0_start, args.win0_len, args.win1_start, args.win1_len)
        send_cmd(dev, ep_out, payload)
    except Exception:
        pass
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_PROFILE, args.profile & 0xFF]))
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_BLOCK_HZ]) + le16(args.block_hz))
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_FRAME_SAMPLES]) + le16(args.frame_samples))
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_FULL_MODE, 1 if args.full_mode else 0]))

    # Start
    send_cmd(dev, ep_out, bytes([VND_CMD_START_STREAM]))

    want_frames = args.frames
    got_a = got_b = tests = 0
    expect_b = False
    last_status = 0.0
    last_seq = None
    first_seq = None
    first_pair_time = None
    last_pair_time = None

    try:
        t0 = time.time()
        # Реассемблер входного потока: копим в acc и выдёргиваем STAT (64) и полные кадры (32+2*ns)
        acc = b""
        def pop(n: int):
            nonlocal acc
            out = acc[:n]
            acc = acc[n:]
            return out
        while got_a + got_b + tests < want_frames:
            # Periodically ask for status (device will send between pairs)
            now = time.time()
            if args.status_interval > 0 and (now - last_status) >= args.status_interval:
                try:
                    if args.ctrl_status:
                        # bmRequestType: 0xC0 (device-to-host, vendor, device)
                        raw = dev.ctrl_transfer(0xC0, VND_CMD_GET_STATUS, 0, 0, 64, timeout=300)
                        buf = bytes(raw)
                        st = parse_stat(buf)
                        if st and not args.quiet:
                            print(f"STAT[vnd-ctl] v{st['ver']} f2=0x{st['flags2']:04X} cur={st['cur_samples']} seq={st['cur_stream_seq']} sentA/B={st['sent0']}/{st['sent1']} wr={st['wr']} dma0/1={st['dma0']}/{st['dma1']} lastTX={st['last_tx_len']} send={st['sending_ch']} pair fs={st['pair_idx']>>8}/{st['pair_idx']&0xFF}")
                        elif not args.quiet:
                            print("STAT[vnd-ctl]", buf[:16].hex(), "len=", len(buf))
                    else:
                        send_cmd(dev, ep_out, bytes([VND_CMD_GET_STATUS]))
                except usb.core.USBError as e:
                    if not args.quiet:
                        print("GET_STATUS err:", e)
                last_status = now

            # Читать крупнее, чтобы получить целый кадр HS (до ~2KB)
            try:
                chunk = dev.read(ep_in, 2048, timeout=args.timeout)
            except usb.core.USBError as e:
                if e.errno is None:
                    print(f"IN error: {e}")
                else:
                    print(f"IN timeout/err: {e}")
                # даже при таймауте пробуем выделить из буфера, если вдруг уже накопили
                chunk = b""
            acc += bytes(chunk)

            # Парсинг acc: возможен leading мусор — сдвигаем до 'STAT' или 0x5A 0xA5
            progressed = True
            while progressed:
                progressed = False
                # Выравнивание
                while len(acc) >= 2 and not (acc.startswith(b'STAT') or (acc[0] == 0x5A and acc[1] == 0xA5)):
                    acc = acc[1:]
                    progressed = True
                if len(acc) < 4:
                    break
                if acc.startswith(b'STAT'):
                    if len(acc) < 64:
                        break  # ждём полный STAT
                    st = pop(64)
                    if expect_b and args.ab_strict:
                        print("[VIOLATION] STAT received mid-pair while expecting B")
                        sys.exit(3)
                    if not args.quiet:
                        stp = parse_stat(st)
                        if stp:
                            print(f"STAT v{stp['ver']} f2=0x{stp['flags2']:04X} cur={stp['cur_samples']} seq={stp['cur_stream_seq']} sentA/B={stp['sent0']}/{stp['sent1']} wr={stp['wr']} dma0/1={stp['dma0']}/{stp['dma1']} lastTX={stp['last_tx_len']} send={stp['sending_ch']} pair fs={stp['pair_idx']>>8}/{stp['pair_idx']&0xFF}")
                        else:
                            print("STAT", st[:16].hex(), "len=64")
                    progressed = True
                    continue
                # Кадр: имеем минимум 32 байта на заголовок?
                if len(acc) < 32:
                    break
                if not (acc[0] == 0x5A and acc[1] == 0xA5):
                    # не распознали — сдвиг
                    acc = acc[1:]
                    progressed = True
                    continue
                # Достанем ns и длину кадра
                try:
                    total_samples = struct.unpack_from('<H', acc, 12)[0]
                except Exception:
                    break
                total_len = 32 + total_samples * 2
                # В DIAG-режиме устройство может паддировать кадры до кратности 512 (HS MPS)
                padded_len = total_len
                if not args.full_mode:
                    unit = 512  # HS max packet size
                    padded_len = ((total_len + (unit - 1)) // unit) * unit
                # Если в буфере уже есть весь паддированный кадр — заберём его целиком,
                # а для парсинга возьмём только полезную часть (без паддинга).
                if len(acc) >= padded_len and padded_len > total_len:
                    fbuf_full = pop(padded_len)
                    fbuf = fbuf_full[:total_len]
                elif len(acc) >= total_len:
                    fbuf = pop(total_len)
                else:
                    break  # ждём оставшиеся байты кадра
                fr = parse_frame(fbuf)
                if not fr:
                    if not args.quiet:
                        print("?? non-frame", len(fbuf))
                    progressed = True
                    continue
                fl = fr['flags']
                if fl & 0x80:
                    tests += 1
                    if not args.quiet:
                        print(f"TEST len={fr['len']}")
                    progressed = True
                    continue
                ch = 'A' if (fl & 0x01) else 'B'
                if ch == 'A':
                    got_a += 1
                    expect_b = True
                    last_seq = fr['seq']
                    if first_seq is None:
                        first_seq = fr['seq']
                        first_pair_time = time.time()
                    if not args.quiet:
                        print(f"A seq={fr['seq']} ns={fr['ns']} len={fr['len']}")
                else:
                    got_b += 1
                    if last_seq is not None and fr['seq'] != last_seq:
                        msg = f"B seq mismatch: got {fr['seq']} expected {last_seq}"
                        if args.ab_strict:
                            print("[VIOLATION]", msg)
                            sys.exit(4)
                        else:
                            print("[WARN]", msg)
                    expect_b = False
                    last_pair_time = time.time()
                    if not args.quiet:
                        print(f"B seq={fr['seq']} ns={fr['ns']} len={fr['len']}")
                progressed = True

        dt = time.time() - t0
    # FPS by completed pairs (seq increments)
        fps = 0.0
        if first_seq is not None and last_pair_time is not None and last_pair_time > first_pair_time:
            pairs = (fr['seq'] - first_seq + 1) if fr is not None else (got_b)
            if pairs > 0:
                fps = pairs / (last_pair_time - first_pair_time)
        print(f"Done. A={got_a} B={got_b} TEST={tests} time={dt:.2f}s pairs_fps≈{fps:.1f}")
    finally:
        try:
            send_cmd(dev, ep_out, bytes([VND_CMD_STOP_STREAM]))
        except Exception:
            pass
        try:
            usb.util.release_interface(dev, args.intf)
        except Exception:
            pass

if __name__ == '__main__':
    main()
