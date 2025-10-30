#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Send START (0x20) to Vendor OUT (0x03) then read few packets from Vendor IN (0x83)
and print brief info (ep, len, first 4 bytes).

Requires WinUSB/libusb driver bound to the Vendor interface (Interface #2 on Windows).
Use Zadig: Options -> List All Devices -> pick your device "... (Interface 2)" -> WinUSB -> Install Driver.

VID/PID default: 0xCAFE / 0x4001. Adjust via env vars VND_VID / VND_PID if needed.
"""
import os
import sys
import time
import argparse
import usb.core
import usb.util
import struct

def _parse_args():
    p = argparse.ArgumentParser(description="Vendor USB quick reader: START then read STAT/TEST/A/B")
    p.add_argument('--vid', type=lambda x: int(x,16), default=int(os.getenv('VND_VID','0xCAFE'),16), help='USB VID (hex, e.g. 0x0483)')
    p.add_argument('--pid', type=lambda x: int(x,16), default=int(os.getenv('VND_PID','0x4001'),16), help='USB PID (hex, e.g. 0x5740)')
    p.add_argument('--intf', type=int, default=int(os.getenv('VND_INTF','2')), help='Vendor interface index (default 2)')
    p.add_argument('--ep-in', dest='ep_in', type=lambda x: int(x,16), default=int(os.getenv('VND_EP_IN','0x83'),16), help='Bulk IN endpoint (hex)')
    p.add_argument('--ep-out', dest='ep_out', type=lambda x: int(x,16), default=int(os.getenv('VND_EP_OUT','0x03'),16), help='Bulk OUT endpoint (hex)')
    p.add_argument('--pairs', type=int, default=int(os.getenv('VND_READ_COUNT','8')), help='How many frames to read (STAT/TEST/A/B count)')
    p.add_argument('--read-timeout-ms', type=int, default=int(os.getenv('VND_READ_TIMEOUT','3000')), help='Read timeout per transfer (ms)')
    p.add_argument('--window-sec', type=float, default=float(os.getenv('VND_READ_WINDOW_SEC','30')), help='Max read window (sec)')
    p.add_argument('--log-path', default=os.getenv('VND_HOST_LOG','HostTools/host_rx.log'), help='Host log file path')
    p.add_argument('--win0', nargs=2, type=int, metavar=('START','LEN'), default=(int(os.getenv('VND_WIN0_START','100')), int(os.getenv('VND_WIN0_LEN','300'))), help='Window0 start,len')
    p.add_argument('--win1', nargs=2, type=int, metavar=('START','LEN'), default=(int(os.getenv('VND_WIN1_START','700')), int(os.getenv('VND_WIN1_LEN','300'))), help='Window1 start,len')
    p.add_argument('--rate-hz', type=int, default=int(os.getenv('VND_RATE_HZ','200')), help='Block rate (Hz)')
    p.add_argument('--full-mode', type=int, choices=[0,1], default=int(os.getenv('VND_FULL_MODE','1')), help='1=ADC, 0=DIAG(A-only)')
    p.add_argument('--use-ctrl-status', action='store_true', help='Use control GET_STATUS instead of bulk 0x30')
    p.add_argument('--frame-samples', type=int, default=int(os.getenv('VND_FRAME_SAMPLES','0')), help='Samples per frame per channel (CMD 0x17). E.g., 10 for 200Hz, 15 for 300Hz (~20 FPS). 0=disabled')
    return p.parse_args()

args = _parse_args()

VID = args.vid
PID = args.pid
OUT_EP = args.ep_out
IN_EP  = args.ep_in
READ_COUNT = args.pairs  # count frames (STAT/TEST/A/B all count)
READ_TIMEOUT_MS = args.read_timeout_ms
READ_WINDOW_SEC = args.window_sec
LOG_PATH = args.log_path
WIN0_START, WIN0_LEN = args.win0
WIN1_START, WIN1_LEN = args.win1
RATE_HZ = args.rate_hz
FULL_MODE = args.full_mode
FRAME_SAMPLES = args.frame_samples
# Control GET_STATUS params
IFACE_INDEX = args.intf  # Vendor interface index in composite config
VND_CMD_GET_STATUS = 0x30
VND_CMD_SET_FULL_MODE = 0x13
VND_CMD_SET_PROFILE   = 0x14
USE_CTRL_STATUS = args.use_ctrl_status  # 1=use ctrl_transfer, 0=use bulk 0x30 (default)

# Ensure log file exists early, even if device not found
def _ensure_log_file():
    try:
        d = os.path.dirname(LOG_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write("=== host reader start ===\n")
    except Exception:
        pass

_ensure_log_file()


def log_line(s: str):
    """Print to console and append to host log file."""
    print(s)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(s + "\n")
    except Exception:
        pass


def find_iface_with_eps(dev, out_ep=OUT_EP, in_ep=IN_EP):
    for cfg in dev:
        for intf in cfg:
            eps = [ep.bEndpointAddress for ep in intf]
            if out_ep in eps and in_ep in eps:
                return cfg, intf
    return None, None

def parse_stat_frame(ba: bytes):
    """Parse STAT frame payload into dict; return None if invalid.
    Supports legacy 52-byte and current 64-byte layouts.
    """
    try:
        if len(ba) < 52 or ba[0:4] != b'STAT':
            return None
        # Base v1 subset (52 bytes)
        base = struct.unpack('<4sBBHHHIIIIIIIIIHH', ba[:52])
        res = {
            'version': base[1],
            'cur_samples': base[3],
            'frame_bytes': base[4],
            'test_frames': base[5],
            'produced_seq': base[6],
            'sent0': base[7],
            'sent1': base[8],
            'dbg_tx_cplt': base[9],
            'dbg_partial_abort': base[10],
            'dbg_size_mismatch': base[11],
            'dma_done0': base[12],
            'dma_done1': base[13],
            'frame_wr_seq': base[14],
            'flags_runtime': base[15],
        }
        # Extended tail if present (64 bytes total)
        if len(ba) >= 64:
            # Offsets based on packed vnd_status_v1_t
            # 0..3 sig, 4 version, 5 reserved0
            flags2 = int.from_bytes(ba[50:52], 'little')
            sending_ch = ba[52]
            prep_calls = ba[5]   # reserved0 at offset 5
            prep_ok    = ba[53]  # reserved2 low byte at offset 53
            pair_idx = int.from_bytes(ba[54:56], 'little')
            last_tx_len = int.from_bytes(ba[56:58], 'little')
            cur_stream_seq = int.from_bytes(ba[58:62], 'little')
            res.update({
                'flags2': flags2,
                'sending_ch': sending_ch,
                'prep_calls': prep_calls,
                'prep_ok': prep_ok,
                'pair_fill': (pair_idx >> 8) & 0xFF,
                'pair_send': pair_idx & 0xFF,
                'last_tx_len': last_tx_len,
                'cur_stream_seq': cur_stream_seq,
            })
        return res
    except Exception:
        return None


def get_status_ctrl(dev):
    """Query device status via control transfer (EP0). Returns dict or None."""
    try:
        bm = usb.util.build_request_type(usb.util.CTRL_IN, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_INTERFACE)
        data = dev.ctrl_transfer(bm, VND_CMD_GET_STATUS, 0, IFACE_INDEX, 64, timeout=500)
        ba = bytes(data)
        if len(ba) < 52:
            return None
        tup = struct.unpack('<4sBBHHHIIIIIIIIIHH', ba[:52])
        if tup[0] != b'STAT':
            return None
        return {
            'version': tup[1],
            'cur_samples': tup[3],
            'frame_bytes': tup[4],
            'test_frames': tup[5],
            'produced_seq': tup[6],
            'sent0': tup[7],
            'sent1': tup[8],
            'dbg_tx_cplt': tup[9],
            'dbg_partial_abort': tup[10],
            'dbg_size_mismatch': tup[11],
            'dma_done0': tup[12],
            'dma_done1': tup[13],
            'frame_wr_seq': tup[14],
            'flags_runtime': tup[15],
        }
    except Exception as e:
        log_line(f"[HOST][STAT-CTRL][ERR] {e}")
        return None

def queue_status_bulk(dev):
    """Request STAT by sending Vendor command 0x30 over bulk OUT (preferred)."""
    try:
        dev.write(OUT_EP, bytes([VND_CMD_GET_STATUS]), timeout=500)
        log_line("[HOST][GET_STATUS] queued via BULK")
    except Exception as e:
        log_line(f"[HOST][GET_STATUS][BULK][ERR] {e}")


def recover_pipe_error(dev, claim_idx, in_ep=IN_EP, out_ep=OUT_EP):
    """Try to recover from a stalled/broken pipe on Windows (WinUSB) or libusb.
    Steps:
      - clear halt on IN endpoint
      - re-select altsetting=1 to ensure endpoints are active
      - re-send START command to resume streaming
    Returns True if recovery attempted, False otherwise.
    """
    try:
        try:
            usb.util.clear_halt(dev, in_ep)
            log_line(f"[HOST][RECOVER] clear_halt IN 0x{in_ep:02X} OK")
        except Exception as e:
            log_line(f"[HOST][RECOVER][WARN] clear_halt IN failed: {e}")
        try:
            dev.set_interface_altsetting(interface=claim_idx, alternate_setting=1)
            log_line(f"[HOST][RECOVER] SetInterface(IF#{claim_idx}, alt=1) OK")
        except Exception as e:
            log_line(f"[HOST][RECOVER][WARN] SetInterface alt=1 failed: {e}")
        try:
            # Re-queue START to OUT EP to resume stream
            w = dev.write(out_ep, bytes([0x20]), timeout=1000)
            log_line(f"[HOST][RECOVER] START re-sent: {w} bytes")
        except Exception as e:
            log_line(f"[HOST][RECOVER][WARN] START re-send failed: {e}")
        return True
    except Exception as e:
        log_line(f"[HOST][RECOVER][ERR] {e}")
        return False


def main():
    log_line(f"[HOST][CFG] VID=0x{VID:04X} PID=0x{PID:04X} intf={IFACE_INDEX} IN=0x{IN_EP:02X} OUT=0x{OUT_EP:02X} pairs={READ_COUNT} tmo={READ_TIMEOUT_MS}ms window={READ_WINDOW_SEC}s full={FULL_MODE} rate={RATE_HZ}Hz ctrlSTAT={int(USE_CTRL_STATUS)}")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        log_line(f"[ERR] Device not found VID=0x{VID:04X} PID=0x{PID:04X}")
        sys.exit(1)

    try:
        dev.set_configuration()
    except Exception:
        # On Windows with interface-specific WinUSB binding, set_configuration may be unsupported; proceed.
        pass

    cfg, intf = find_iface_with_eps(dev, out_ep=OUT_EP, in_ep=IN_EP)
    if cfg is None:
        log_line("[ERR] Vendor interface (with 0x03/0x83) not found; check driver binding")
        sys.exit(2)

    # Detach kernel driver if needed (libusb on POSIX); on Windows this is usually not needed
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except Exception:
        pass

    # Claim interface may be unsupported with WinUSB; proceed best-effort
    claim_idx = IFACE_INDEX
    try:
        usb.util.claim_interface(dev, IFACE_INDEX)
        log_line(f"[HOST] Claimed interface #{IFACE_INDEX}")
    except Exception as e:
        try:
            usb.util.claim_interface(dev, intf.bInterfaceNumber)
            claim_idx = intf.bInterfaceNumber
            log_line(f"[HOST] Claimed interface #{claim_idx}")
        except Exception as e2:
            log_line(f"[HOST][WARN] claim_interface failed: {e2}; continue without explicit claim")

    # Switch Vendor IF to altsetting=1 to activate endpoints (firmware opens EPs only on alt=1)
    try:
        dev.set_interface_altsetting(interface=claim_idx, alternate_setting=1)
        log_line(f"[HOST] SetInterface(IF#{claim_idx}, alt=1) OK")
    except Exception as e:
        log_line(f"[HOST][WARN] SetInterface alt=1 failed: {e}")

    # Configure windows and block rate before START
    try:
        payload = struct.pack('<BHHHH', 0x10, WIN0_START, WIN0_LEN, WIN1_START, WIN1_LEN)
        w1 = dev.write(OUT_EP, payload, timeout=1000)
        log_line(f"[HOST] SET_WINDOWS written: {w1} bytes ({WIN0_START},{WIN0_LEN}) ({WIN1_START},{WIN1_LEN})")
    except Exception as e:
        log_line(f"[HOST][WARN] SET_WINDOWS failed: {e}")

    try:
        payload = struct.pack('<BH', 0x11, RATE_HZ)
        w2 = dev.write(OUT_EP, payload, timeout=1000)
        log_line(f"[HOST] SET_BLOCK_RATE written: {w2} bytes ({RATE_HZ} Hz)")
    except Exception as e:
        log_line(f"[HOST][WARN] SET_BLOCK_RATE failed: {e}")

    # Optional: set frame samples for ~20 FPS
    if FRAME_SAMPLES and FRAME_SAMPLES > 0:
        try:
            payload = struct.pack('<BH', 0x17, FRAME_SAMPLES)
            wfs = dev.write(OUT_EP, payload, timeout=1000)
            log_line(f"[HOST] SET_FRAME_SAMPLES written: {wfs} bytes (Ns={FRAME_SAMPLES})")
        except Exception as e:
            log_line(f"[HOST][WARN] SET_FRAME_SAMPLES failed: {e}")

    # Ensure full mode and default profile
    try:
        fm = 0x01 if FULL_MODE else 0x00
        w3 = dev.write(OUT_EP, bytes([VND_CMD_SET_FULL_MODE, fm]), timeout=1000)
        log_line(f"[HOST] SET_FULL_MODE({FULL_MODE}) written: {w3} bytes")
    except Exception as e:
        log_line(f"[HOST][WARN] SET_FULL_MODE failed: {e}")
    try:
        # Profile 2 => default B profile per firmware
        w4 = dev.write(OUT_EP, bytes([VND_CMD_SET_PROFILE, 0x02]), timeout=1000)
        log_line(f"[HOST] SET_PROFILE(2) written: {w4} bytes")
    except Exception as e:
        log_line(f"[HOST][WARN] SET_PROFILE failed: {e}")

    # Send START (0x20) to OUT EP
    data = bytes([0x20])
    wlen = dev.write(OUT_EP, data, timeout=1000)
    log_line(f"[HOST] START written: {wlen} bytes to EP 0x{OUT_EP:02X}")
    # Optionally request initial STAT snapshot
    if USE_CTRL_STATUS:
        st0 = get_status_ctrl(dev)
        if st0:
            log_line(f"[HOST_STAT0] ver={st0['version']} flags=0x{st0['flags_runtime']:04X} test={st0['test_frames']} seq={st0['produced_seq']} sentA/B={st0['sent0']}/{st0['sent1']} dma={st0['dma_done0']}/{st0['dma_done1']} cur_samples={st0['cur_samples']}")
    else:
        queue_status_bulk(dev)

    # Logger is defined at module level

    # Read several complete frames (STAT/TEST/A/B), reassembling from 512B packets
    got = 0
    start_time = time.time()
    last_stat_print = 0.0
    rx = bytearray()
    pipe_errs = 0
    while got < READ_COUNT and (time.time() - start_time) < READ_WINDOW_SEC:
        try:
            chunk = bytes(dev.read(IN_EP, 512, timeout=READ_TIMEOUT_MS))
            rx += chunk
            pipe_errs = 0  # reset on success
            # Try to extract complete frames
            while True:
                if len(rx) < 4:
                    break
                # STAT?
                if rx[0:4] == b'STAT':
                    flen = 64 if len(rx) >= 64 else (52 if len(rx) >= 52 else 0)
                    if flen == 0 or len(rx) < flen:
                        break
                    frame = bytes(rx[:flen]); rx = rx[flen:]
                    head = ' '.join(f"{b:02X}" for b in frame[:4])
                    log_line(f"[HOST_RX] ep=0x{IN_EP:02X} len={len(frame)} type=STAT head={head}")
                    st = parse_stat_frame(frame)
                    if st:
                        base = f"ver={st['version']} flags=0x{st['flags_runtime']:04X} test={st['test_frames']} seq={st['produced_seq']} sentA/B={st['sent0']}/{st['sent1']} TxCplt={st['dbg_tx_cplt']} dma={st['dma_done0']}/{st['dma_done1']} cur_samples={st['cur_samples']} wr_seq={st['frame_wr_seq']}"
                        ext = ""
                        if 'flags2' in st:
                            ext = f" flags2=0x{st['flags2']:04X} send_ch={st['sending_ch']} pair {st['pair_fill']}/{st['pair_send']} lastTX={st['last_tx_len']} cur_seq={st['cur_stream_seq']} prep {st['prep_calls']}/{st['prep_ok']}"
                        log_line(f"[HOST_STAT] {base}{ext}")
                    got += 1
                    continue
                # Frame header?
                if rx[0] == 0x5A and rx[1] == 0xA5 and rx[2] == 0x01 and len(rx) >= 16:
                    total_samples = rx[12] | (rx[13] << 8)
                    flen = 32 + total_samples * 2
                    if len(rx) < flen:
                        break
                    flags = rx[3]
                    ftype = 'TEST' if (flags & 0x80) else ('A' if flags == 0x01 else ('B' if flags == 0x02 else 'UNK'))
                    frame = bytes(rx[:flen]); rx = rx[flen:]
                    head = ' '.join(f"{b:02X}" for b in frame[:4])
                    log_line(f"[HOST_RX] ep=0x{IN_EP:02X} len={len(frame)} type={ftype} head={head}")
                    got += 1
                    continue
                # Resync: drop until next plausible header
                idx_stat = rx.find(b'STAT')
                idx_hdr = rx.find(b"\x5A\xA5\x01")
                idx = -1
                if idx_stat != -1 and (idx_hdr == -1 or idx_stat < idx_hdr):
                    idx = idx_stat
                elif idx_hdr != -1:
                    idx = idx_hdr
                if idx > 0:
                    rx = rx[idx:]
                    continue
                break
        except usb.core.USBError as e:
            msg = str(e).lower()
            # Treat common timeout errnos/messages as non-fatal (Windows 10060, POSIX 110/ETIMEDOUT)
            if (getattr(e, 'errno', None) in (10060, 110, 60)) or ('timed out' in msg) or ('timeout' in msg):
                log_line("[HOST_RX] timeout")
                # Periodically request STAT to aid diagnosis (bulk preferred)
                now = time.time()
                if now - last_stat_print > 1.0:
                    last_stat_print = now
                    if USE_CTRL_STATUS:
                        st = get_status_ctrl(dev)
                        if st:
                            log_line(f"[HOST_STAT] ver={st['version']} flags=0x{st['flags_runtime']:04X} test={st['test_frames']} seq={st['produced_seq']} sentA/B={st['sent0']}/{st['sent1']} TxCplt={st['dbg_tx_cplt']} dma={st['dma_done0']}/{st['dma_done1']} cur_samples={st['cur_samples']} wr_seq={st['frame_wr_seq']}")
                    else:
                        queue_status_bulk(dev)
                continue
            # Handle pipe errors gracefully: clear halt, reselect alt=1, re-send START
            if (getattr(e, 'errno', None) in (32, 5)) or ('pipe' in msg):
                pipe_errs += 1
                log_line(f"[HOST_RX][PIPE] {e} (#{pipe_errs})")
                recovered = recover_pipe_error(dev, claim_idx)
                if recovered and pipe_errs < 5:
                    # Give device a brief moment to settle
                    time.sleep(0.05)
                    continue
                else:
                    log_line("[HOST_RX][PIPE] unrecoverable, stopping")
                    break
            # other errors
            log_line(f"[HOST_RX][ERR] {e}")
            break

    # Optional STOP
    try:
        slen = dev.write(OUT_EP, bytes([0x21]), timeout=1000)
        log_line(f"[HOST] STOP written: {slen} bytes")
    except Exception as e:
        log_line(f"[HOST] STOP write failed: {e}")

    try:
        usb.util.release_interface(dev, claim_idx)
    except Exception:
        pass

    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass


if __name__ == '__main__':
    main()
