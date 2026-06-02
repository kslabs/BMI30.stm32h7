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
VND_CMD_SET_ASYNC         = 0x18
VND_CMD_SET_CHMODE        = 0x19
VND_CMD_SET_STREAM_MODE   = 0x1A
VND_CMD_SET_TX_ENABLE     = 0x33
VND_CMD_SET_OPTIC_POWER   = 0x34
VND_CMD_SET_OPTIC_HOLD    = 0x39
VND_CMD_HOST_RX_ACK       = 0x36
VND_CMD_HOST_RX_CLEAR     = 0x37

VND_STFLAG_OPTIC_ACTIVE   = 0x0020

MAGIC = 0xA55A

STAT_LEN_V1 = 64
STAT_LEN_V4 = 96
STAT_LEN_V5 = 136


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


def send_cmd(dev, ep_out, data: bytes, timeout: int = 1000):
    dev.write(ep_out, data, timeout=int(timeout))


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
    if ver != 1:
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
    if len(buf) < STAT_LEN_V1 or buf[:4] != b'STAT':
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
    st['reserved3'] = int.from_bytes(buf[62:64], 'little')
    st['optic_power'] = (st['reserved3'] >> 8) & 0xFF
    st['optic_hold_seconds'] = (st['reserved3'] >> 2) & 0x3F
    st['optic_active_packed'] = 1 if (st['reserved3'] & 0x01) else 0
    st['tx_enable_packed'] = 1 if (st['reserved3'] & 0x02) else 0
    st['optic_active_flag'] = 1 if (st['flags_rt'] & VND_STFLAG_OPTIC_ACTIVE) else 0

    # v2/v3/v4 extensions (total 96 bytes)
    if len(buf) >= STAT_LEN_V4:
        st['stage_alt1_ms'] = int.from_bytes(buf[64:68], 'little')
        st['stage_start_ms'] = int.from_bytes(buf[68:72], 'little')
        st['stage_first_frame_ms'] = int.from_bytes(buf[72:76], 'little')
        st['ch_zero_buffers_A'] = int.from_bytes(buf[76:80], 'little')
        st['ch_zero_buffers_B'] = int.from_bytes(buf[80:84], 'little')
        st['now_ms'] = int.from_bytes(buf[84:88], 'little')
        st['last_full0_ms'] = int.from_bytes(buf[88:92], 'little')
        st['last_full1_ms'] = int.from_bytes(buf[92:96], 'little')
    if len(buf) >= STAT_LEN_V5 and st.get('ver', 0) >= 5:
        st['optic_hold_ds'] = int.from_bytes(buf[96:98], 'little')
        st['led_pattern'] = buf[98]
        st['sync_local_status'] = buf[99]
        st['sync_seen_mask'] = int.from_bytes(buf[100:104], 'little')
        st['sync_node_count'] = buf[104]
        st['sync_status_bytes'] = list(buf[105:136])
    return st


def stat_expected_len(acc: bytes) -> int:
    """Return expected STAT length based on version byte, if present."""
    if len(acc) < 5 or not acc.startswith(b'STAT'):
        return STAT_LEN_V1
    ver = acc[4]
    if ver >= 5:
        return STAT_LEN_V5
    return STAT_LEN_V4 if ver >= 2 else STAT_LEN_V1


def main():
    ap = argparse.ArgumentParser(description='Vendor stream reader (Bulk IN 0x83, OUT 0x03)')
    ap.add_argument('--vid', type=lambda x: int(x,0), default=0xCAFE)
    ap.add_argument('--pid', type=lambda x: int(x,0), default=0x4001)
    ap.add_argument('--intf', type=int, default=2, help='Vendor interface number')
    ap.add_argument('--ep-in', type=lambda x: int(x,0), default=0x83)
    ap.add_argument('--ep-out', type=lambda x: int(x,0), default=0x03)
    ap.add_argument('--profile', type=int, default=2, help='1=A(200Hz), 2=B(default)')
    ap.add_argument('--frame-samples', type=int, default=0, help='Samples per channel per frame (A and B). 0 = do not send SET_FRAME_SAMPLES')
    ap.add_argument('--full-mode', type=int, default=1, help='1=ADC mode, 0=diagnostic')
    ap.add_argument('--block-hz', type=int, default=200, help='ADC block rate hint')
    ap.add_argument('--frames', type=int, default=0, help='Stop after N frames (0=disabled, use --secs)')
    ap.add_argument('--secs', type=float, default=60.0, help='Stop after N seconds (used if --frames=0)')
    ap.add_argument('--timeout', type=int, default=300, help='IN timeout ms')
    ap.add_argument('--read-size', type=int, default=16384, help='Bulk IN read size (bytes)')
    # Windows (time windows) configuration like in quick-start script
    ap.add_argument('--win0-start', type=int, default=280)
    ap.add_argument('--win0-len', type=int, default=200)
    ap.add_argument('--win1-start', type=int, default=280)
    ap.add_argument('--win1-len', type=int, default=200)
    ap.add_argument('--status-interval', type=float, default=0.5, help='Request GET_STATUS every N seconds (0=off)')
    ap.add_argument('--rx-ack-interval', type=float, default=0.0, help='Send HOST_RX_ACK every N seconds when new A/B frames were parsed (0=off)')
    ap.add_argument('--ctrl-status', action='store_true', help='Use control transfer for GET_STATUS (works even mid-pair)')
    ap.add_argument('--status-print', action='store_true', help='Print periodic STAT lines even with --quiet')
    ap.add_argument('--print-errors', action='store_true', help='Print USB read/control errors even with --quiet')
    ap.add_argument('--ab-strict', action='store_true', help='Fail if A→B ordering is violated or STAT appears mid-pair')
    ap.add_argument('--seq-strict', action='store_true', help='Verify that completed pairs have strictly consecutive seq (no gaps/dupes)')
    ap.add_argument('--warmup-pairs', type=int, default=0, help='Ignore ordering/seq checks for the first N completed pairs after sync')
    ap.add_argument('--fail-fast', action='store_true', help='Exit immediately on first violation (default: count and continue)')
    ap.add_argument('--verify', choices=['pair', 'mono'], default=None, help='Verification mode: pair=A->B strict pairs, mono=single stream seq continuity')
    ap.add_argument('--stream-mode', type=int, default=0, help='0=latest (lossy), 1=LOSSLESS_ROI, 2=AVG_ROI')
    ap.add_argument('--avg-n', type=int, default=24, help='AVG_ROI parameter (16..64), used when --stream-mode=2')
    ap.add_argument('--async-mode', dest='async_mode', type=int, choices=[0, 1], default=None, help='Set ASYNC mode (0/1). In LOSSLESS_ROI firmware may force 0.')
    ap.add_argument('--chmode', type=int, choices=[0, 1, 2, 3], default=None, help='Set channel mode (firmware-defined).')
    ap.add_argument('--tx-enable', type=int, choices=[0, 1], default=None, help='Set external TX gate (0=disable, 1=enable) via CMD 0x33.')
    ap.add_argument('--optic-power', type=int, default=None, help='Set optical TX power (0..255) via CMD 0x34.')
    ap.add_argument('--optic-hold-s', type=float, default=None, help='Set optic active hold time in seconds via CMD 0x39 (0=default 3.0, step 0.1, max 60.0).')
    ap.add_argument('--quiet', action='store_true', help='Reduce per-frame prints, show only summary and warnings')
    args = ap.parse_args()

    verify_mode = args.verify
    if verify_mode is None:
        # If host requests both channels, verify strict pairs; otherwise verify monotonic seq stream.
        verify_mode = 'pair' if args.chmode == 2 else 'mono'

    status_verbose = (not args.quiet) or args.status_print
    error_verbose = (not args.quiet) or args.print_errors

    dev = find_device(args.vid, args.pid)
    intf = claim_interface(dev, args.intf)
    ep_in = args.ep_in
    ep_out = args.ep_out
    print(f"Opened VID=0x{args.vid:04X} PID=0x{args.pid:04X} IF#{args.intf} IN=0x{ep_in:02X} OUT=0x{ep_out:02X}")

    # Configure
    # IMPORTANT ORDERING: apply SET_STREAM_MODE first, then SET_WINDOWS.
    try:
        sm = int(args.stream_mode) & 0xFF
        if sm == 2:
            n = int(args.avg_n)
            if n < 16:
                n = 16
            if n > 64:
                n = 64
            send_cmd(dev, ep_out, bytes([VND_CMD_SET_STREAM_MODE, sm, n & 0xFF]))
        else:
            send_cmd(dev, ep_out, bytes([VND_CMD_SET_STREAM_MODE, sm]))
    except Exception:
        pass

    # Some firmware profiles expect non-zero windows to start streaming
    try:
        payload = struct.pack('<BHHHH', VND_CMD_SET_WINDOWS, args.win0_start, args.win0_len, args.win1_start, args.win1_len)
        send_cmd(dev, ep_out, payload)
    except Exception:
        pass
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_PROFILE, args.profile & 0xFF]))
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_BLOCK_HZ]) + le16(args.block_hz))
    if int(args.frame_samples) > 0:
        send_cmd(dev, ep_out, bytes([VND_CMD_SET_FRAME_SAMPLES]) + le16(int(args.frame_samples)))
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_FULL_MODE, 1 if args.full_mode else 0]))

    # Optional extra mode knobs
    try:
        if args.async_mode is not None:
            # LOSSLESS_ROI is expected to be synchronous; enforce 0 by default.
            val = 0 if int(args.stream_mode) == 1 else (1 if args.async_mode else 0)
            send_cmd(dev, ep_out, bytes([VND_CMD_SET_ASYNC, val & 0xFF]))
    except Exception:
        pass
    try:
        if args.chmode is not None:
            send_cmd(dev, ep_out, bytes([VND_CMD_SET_CHMODE, int(args.chmode) & 0xFF]))
    except Exception:
        pass
    try:
        if args.tx_enable is not None:
            send_cmd(dev, ep_out, bytes([VND_CMD_SET_TX_ENABLE, int(args.tx_enable) & 0xFF]))
    except Exception:
        pass
    try:
        if args.optic_power is not None:
            optic_power = int(args.optic_power)
            if optic_power < 0:
                optic_power = 0
            if optic_power > 255:
                optic_power = 255
            send_cmd(dev, ep_out, bytes([VND_CMD_SET_OPTIC_POWER, optic_power & 0xFF]))
    except Exception:
        pass
    try:
        if args.optic_hold_s is not None:
            optic_hold_ds = int(round(float(args.optic_hold_s) * 10.0))
            if optic_hold_ds < 0:
                optic_hold_ds = 0
            if optic_hold_ds > 600:
                optic_hold_ds = 600
            send_cmd(dev, ep_out, bytes([VND_CMD_SET_OPTIC_HOLD]) + le16(optic_hold_ds))
    except Exception:
        pass

    # Stop any ongoing stream first, then flush IN to avoid stale buffered frames.
    try:
        send_cmd(dev, ep_out, bytes([VND_CMD_HOST_RX_CLEAR]))
    except Exception:
        pass
    try:
        send_cmd(dev, ep_out, bytes([VND_CMD_STOP_STREAM]))
        time.sleep(0.2)
    except Exception:
        pass
    try:
        while True:
            try:
                junk = dev.read(ep_in, args.read_size, timeout=100)
                if not junk:
                    break
            except usb.core.USBError:
                break
    except Exception:
        pass

    # Start
    send_cmd(dev, ep_out, bytes([VND_CMD_START_STREAM]))

    want_frames = int(args.frames)
    use_time_limit = (want_frames <= 0)
    time_limit_sec = float(args.secs)
    got_a = got_b = tests = 0
    expect_b = False
    last_status = 0.0
    last_rx_ack = 0.0
    last_rx_ack_frames = 0
    last_seq = None
    first_seq = None
    first_pair_time = None
    last_pair_time = None
    last_seq_any = None
    synced = False
    pre_sync_b = 0
    pre_sync_stat = 0
    pairs_completed = 0
    frames_checked = 0
    wrong_channel = 0
    # Seq verification (tracked over a window; compute gaps within observed span)
    seq_seen = set()
    seq_first = None
    seq_max_seen = None
    seq_dupes = 0
    seq_reorders = 0
    seq_prev = None
    missing_a = 0
    missing_b = 0
    stat_midpair = 0
    in_timeouts = 0
    in_errors = 0

    try:
        t0 = time.time()
        # Реассемблер входного потока: копим в acc и выдёргиваем STAT (64) и полные кадры (32+2*ns)
        acc = b""
        def pop(n: int):
            nonlocal acc
            out = acc[:n]
            acc = acc[n:]
            return out
        while True:
            if use_time_limit:
                if (time.time() - t0) >= time_limit_sec:
                    break
            else:
                if (got_a + got_b + tests) >= want_frames:
                    break
            # Periodically ask for status (device will send between pairs)
            now = time.time()
            if args.status_interval > 0 and (now - last_status) >= args.status_interval:
                try:
                    if args.ctrl_status:
                        # bmRequestType: 0xC0 (device-to-host, vendor, device)
                        raw = dev.ctrl_transfer(0xC0, VND_CMD_GET_STATUS, 0, 0, STAT_LEN_V5, timeout=300)
                        buf = bytes(raw)
                        st = parse_stat(buf)
                        if st and status_verbose:
                            print(f"STAT[vnd-ctl] v{st['ver']} f2=0x{st['flags2']:04X} cur={st['cur_samples']} seq={st['cur_stream_seq']} sentA/B={st['sent0']}/{st['sent1']} wr={st['wr']} dma0/1={st['dma0']}/{st['dma1']} lastTX={st['last_tx_len']} send={st['sending_ch']} pair fs={st['pair_idx']>>8}/{st['pair_idx']&0xFF} optic_power={st['optic_power']} optic_hold_ds={st.get('optic_hold_ds', st['optic_hold_seconds']*10)} optic_active={1 if (st['optic_active_packed'] or st['optic_active_flag']) else 0} tx_enable={st['tx_enable_packed']} led_pattern={st.get('led_pattern', '-')} sync_count={st.get('sync_node_count', '-')}")
                        elif status_verbose:
                            print("STAT[vnd-ctl]", buf[:16].hex(), "len=", len(buf))
                    else:
                        send_cmd(dev, ep_out, bytes([VND_CMD_GET_STATUS]))
                except usb.core.USBError as e:
                    if status_verbose or error_verbose:
                        print("GET_STATUS err:", e)
                last_status = now

            # Читать крупнее, чтобы снижать overhead и риск переполнения host-side очереди
            try:
                chunk = dev.read(ep_in, args.read_size, timeout=args.timeout)
            except usb.core.USBError as e:
                # даже при таймауте пробуем выделить из буфера, если вдруг уже накопили
                chunk = b""
                if getattr(e, 'errno', None) is None:
                    in_errors += 1
                    if error_verbose:
                        print(f"IN error @ {time.time()-t0:.3f}s: {e}")
                else:
                    in_timeouts += 1
                    if error_verbose:
                        print(f"IN timeout/err @ {time.time()-t0:.3f}s: {e}")
            acc += bytes(chunk)

            # Парсинг acc: возможен leading мусор — сдвигаем до 'STAT' или 0x5A 0xA5
            progressed = True
            while progressed:
                progressed = False
                # Выравнивание
                while len(acc) >= 3 and not (acc.startswith(b'STAT') or (acc[0] == 0x5A and acc[1] == 0xA5 and acc[2] == 0x01)):
                    acc = acc[1:]
                    progressed = True
                if len(acc) < 4:
                    break
                if acc.startswith(b'STAT'):
                    want = stat_expected_len(acc)
                    if len(acc) < want:
                        break  # ждём полный STAT
                    st = pop(want)
                    if not synced:
                        pre_sync_stat += 1
                        progressed = True
                        continue
                    if expect_b:
                        stat_midpair += 1
                        if args.ab_strict and status_verbose:
                            print("[WARN] STAT received mid-pair while expecting B (allowed)")
                    if status_verbose:
                        stp = parse_stat(st)
                        if stp:
                            extra = ""
                            if 'now_ms' in stp:
                                extra = f" now={stp['now_ms']} last_full0/1={stp['last_full0_ms']}/{stp['last_full1_ms']}"
                            print(f"STAT v{stp['ver']} f2=0x{stp['flags2']:04X} cur={stp['cur_samples']} seq={stp['cur_stream_seq']} sentA/B={stp['sent0']}/{stp['sent1']} wr={stp['wr']} dma0/1={stp['dma0']}/{stp['dma1']} lastTX={stp['last_tx_len']} send={stp['sending_ch']} pair fs={stp['pair_idx']>>8}/{stp['pair_idx']&0xFF} optic_power={stp['optic_power']} optic_hold_ds={stp.get('optic_hold_ds', stp['optic_hold_seconds']*10)} optic_active={1 if (stp['optic_active_packed'] or stp['optic_active_flag']) else 0} tx_enable={stp['tx_enable_packed']} led_pattern={stp.get('led_pattern', '-')} sync_count={stp.get('sync_node_count', '-')}{extra}")
                        else:
                            print("STAT", st[:16].hex(), "len=", len(st))
                    progressed = True
                    continue
                # Кадр: имеем минимум 32 байта на заголовок?
                if len(acc) < 32:
                    break
                if not (acc[0] == 0x5A and acc[1] == 0xA5 and acc[2] == 0x01):
                    # не распознали — сдвиг
                    acc = acc[1:]
                    progressed = True
                    continue
                # Достанем ns и длину кадра
                try:
                    total_samples = struct.unpack_from('<H', acc, 12)[0]
                except Exception:
                    break
                # Sanity-check: if we are misaligned, ns will be garbage. Don't consume a bogus length.
                if total_samples <= 0 or total_samples > 4096:
                    acc = acc[1:]
                    progressed = True
                    continue
                total_len = 32 + total_samples * 2
                # Устройство может паддировать кадры до 512 байт, чтобы bulk IN
                # завершался ZLP, а не коротким пакетом. Для full-mode съедаем
                # паддинг только если он нулевой, чтобы не проглотить следующий кадр.
                unit = 512  # HS max packet size; also multiple of FS 64-byte MPS
                padded_len = ((total_len + (unit - 1)) // unit) * unit
                if padded_len > total_len and len(acc) >= padded_len:
                    padding = acc[total_len:padded_len]
                    if (not args.full_mode) or all(b == 0 for b in padding):
                        fbuf_full = pop(padded_len)
                        fbuf = fbuf_full[:total_len]
                    elif len(acc) >= total_len:
                        fbuf = pop(total_len)
                    else:
                        break
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

                is_a = bool(fl & 0x01)
                is_b = bool(fl & 0x02)
                ch = 'A' if is_a else ('B' if is_b else 'UNK')

                if verify_mode == 'mono':
                    # In mono mode, ignore pairing; just validate seq continuity.
                    if args.chmode == 0 and not is_a:
                        wrong_channel += 1
                    elif args.chmode == 1 and not is_b:
                        wrong_channel += 1
                    # Pre-sync is just first valid frame.
                    if not synced and ch != 'UNK':
                        synced = True
                    # Seq continuity check (after warmup): track uniques and compute gaps within observed span.
                    if synced and ch != 'UNK' and args.seq_strict:
                        check_enabled = (frames_checked >= args.warmup_pairs)
                        if check_enabled:
                            s = fr['seq']
                            if seq_first is None:
                                seq_first = s
                                seq_max_seen = s
                            if seq_prev is not None and s < seq_prev:
                                seq_reorders += 1
                                if args.fail_fast:
                                    print(f"[VIOLATION] seq reorder: got {s} after {seq_prev}")
                                    sys.exit(6)
                            if s in seq_seen:
                                seq_dupes += 1
                                if args.fail_fast:
                                    print(f"[VIOLATION] seq duplicate: {s}")
                                    sys.exit(7)
                            else:
                                seq_seen.add(s)
                                seq_max_seen = s if seq_max_seen is None else max(seq_max_seen, s)
                            seq_prev = s
                        frames_checked += 1

                    # Counts / optional prints
                    if ch == 'A':
                        got_a += 1
                    elif ch == 'B':
                        got_b += 1
                    if not args.quiet:
                        print(f"{ch} seq={fr['seq']} ns={fr['ns']} len={fr['len']}")
                    progressed = True
                    continue

                # verify_mode == 'pair'
                if ch == 'A':
                    if not synced:
                        synced = True
                    if expect_b:
                        # new A arrived while expecting B => missing B / desync
                        if pairs_completed >= args.warmup_pairs:
                            missing_b += 1
                            if args.ab_strict and args.fail_fast:
                                print(f"[VIOLATION] A received while expecting B (missing B). A seq={fr['seq']}")
                                sys.exit(4)
                    got_a += 1
                    expect_b = True
                    last_seq = fr['seq']
                    if first_seq is None:
                        first_seq = fr['seq']
                        first_pair_time = time.time()
                    if not args.quiet:
                        print(f"A seq={fr['seq']} ns={fr['ns']} len={fr['len']}")
                else:
                    if not synced:
                        # We may start mid-stream (B first). Ignore until first A syncs.
                        pre_sync_b += 1
                        progressed = True
                        continue
                    if not expect_b:
                        if pairs_completed >= args.warmup_pairs:
                            missing_a += 1
                            if args.ab_strict and args.fail_fast:
                                print(f"[VIOLATION] B received while not expecting B (missing A). B seq={fr['seq']}")
                                sys.exit(5)
                    got_b += 1
                    if last_seq is not None and fr['seq'] != last_seq:
                        msg = f"B seq mismatch: got {fr['seq']} expected {last_seq}"
                        if pairs_completed >= args.warmup_pairs:
                            if args.ab_strict and args.fail_fast:
                                print("[VIOLATION]", msg)
                                sys.exit(4)
                            elif not args.quiet:
                                print("[WARN]", msg)
                    expect_b = False
                    last_pair_time = time.time()
                    # Pair completed: track uniques and compute true gaps at end.
                    if args.seq_strict:
                        check_enabled = (pairs_completed >= args.warmup_pairs)
                        if check_enabled:
                            s = fr['seq']
                            if seq_first is None:
                                seq_first = s
                                seq_max_seen = s
                            if seq_prev is not None and s < seq_prev:
                                seq_reorders += 1
                                if args.fail_fast:
                                    print(f"[VIOLATION] seq reorder: got {s} after {seq_prev}")
                                    sys.exit(6)
                                elif not args.quiet:
                                    print(f"[WARN] seq reorder: got {s} after {seq_prev}")
                            if s in seq_seen:
                                seq_dupes += 1
                                if args.fail_fast:
                                    print(f"[VIOLATION] seq duplicate: {s}")
                                    sys.exit(7)
                                elif not args.quiet:
                                    print(f"[WARN] seq duplicate: {s}")
                            else:
                                seq_seen.add(s)
                                seq_max_seen = s if seq_max_seen is None else max(seq_max_seen, s)
                            seq_prev = s
                        pairs_completed += 1
                    if not args.quiet:
                        print(f"B seq={fr['seq']} ns={fr['ns']} len={fr['len']}")
                progressed = True

            total_host_frames = got_a + got_b
            if (args.rx_ack_interval > 0 and
                    total_host_frames > last_rx_ack_frames and
                    (now - last_rx_ack) >= args.rx_ack_interval):
                try:
                    send_cmd(dev, ep_out, bytes([VND_CMD_HOST_RX_ACK]) + le32(total_host_frames), timeout=20)
                    last_rx_ack = now
                    last_rx_ack_frames = total_host_frames
                except usb.core.USBError as e:
                    if error_verbose:
                        print("HOST_RX_ACK err:", e)

        dt = time.time() - t0
    # FPS by completed pairs (seq increments)
        fps = 0.0
        if first_seq is not None and last_pair_time is not None and last_pair_time > first_pair_time:
            pairs = (fr['seq'] - first_seq + 1) if fr is not None else (got_b)
            if pairs > 0:
                fps = pairs / (last_pair_time - first_pair_time)
        # Compute true seq gaps from observed window.
        seq_range = "n/a"
        seq_unique = 0
        if args.seq_strict and seq_first is not None and seq_max_seen is not None:
            seq_unique = len(seq_seen)
            seq_gaps = (seq_max_seen - seq_first + 1) - seq_unique
            seq_range = f"{seq_first}..{seq_max_seen}"
        else:
            seq_gaps = 0
        print(
            f"Done. mode={verify_mode} A={got_a} B={got_b} TEST={tests} time={dt:.2f}s pairs_fps≈{fps:.1f} "
            f"timeouts={in_timeouts} in_errors={in_errors} missingA={missing_a} missingB={missing_b} "
            f"seq_gaps={seq_gaps} seq_dupes={seq_dupes} seq_reorders={seq_reorders} "
            f"seq_unique={seq_unique} seq_range={seq_range} stat_midpair={stat_midpair} "
            f"wrong_ch={wrong_channel} pre_sync_b={pre_sync_b} pre_sync_stat={pre_sync_stat}"
        )
    finally:
        try:
            send_cmd(dev, ep_out, bytes([VND_CMD_HOST_RX_CLEAR]))
        except Exception:
            pass
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
