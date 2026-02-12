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
    p = argparse.ArgumentParser(description="Vendor USB reader: START then high-rate capture of STAT/TEST/A/B (supports profile selection)")
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
    p.add_argument('--rate-hz', type=int, default=int(os.getenv('VND_RATE_HZ','200')), help='Requested block rate (Hz)')
    p.add_argument('--profile', type=int, choices=[0,1,2,3], default=int(os.getenv('VND_PROFILE','0')), help='ADC profile id: 0=200Hz(1360),1=300Hz(912),2=300Hz(944),3=300Hz(976). Default 0 for full 200Hz buffer')
    p.add_argument('--full-mode', type=int, choices=[0,1], default=int(os.getenv('VND_FULL_MODE','1')), help='1=ADC, 0=DIAG(A-only)')
    p.add_argument('--stream-mode', type=int, choices=[0,1], default=int(os.getenv('VND_STREAM_MODE','0')), help='0=LATEST (lossy), 1=LOSSLESS_ROI (paired/async=0 recommended)')
    # Status reporting mode during read: none (default), ctrl (EP0), or bulk (0x30 over OUT)
    p.add_argument('--status-mode', choices=['none','ctrl','bulk'], default=os.getenv('VND_STATUS_MODE','none'), help='How to request STAT during read. Default: none')
    p.add_argument('--frame-samples', type=int, default=int(os.getenv('VND_FRAME_SAMPLES','0')), help='Samples per frame per channel (CMD 0x17). E.g., 10 for 200Hz, 15 for 300Hz (~20 FPS). 0=disabled')
    p.add_argument('--async-mode', type=int, choices=[0,1], default=int(os.getenv('VND_ASYNC_MODE','1')), help='1=async A/B independent (default), 0=strict A->B pairs')
    p.add_argument('--ch-mode', type=int, choices=[0,1,2], default=int(os.getenv('VND_CH_MODE','2')), help='0=A-only, 1=B-only, 2=both (default)')
    # Logging controls
    p.add_argument('--verbose', action='store_true', help='Print each received frame (default: aggregate 1 Hz)')
    p.add_argument('--log-interval', type=float, default=float(os.getenv('VND_LOG_INTERVAL','1.0')), help='Aggregate log interval in seconds (default 1.0)')
    # Start robustness
    p.add_argument('--start-check-sec', type=float, default=float(os.getenv('VND_START_CHECK_SEC','3.5')), help='Initial time budget to observe first activity before first kick (default 3.5s)')
    p.add_argument('--start-retries', type=int, default=int(os.getenv('VND_START_RETRIES','5')), help='How many START re-sends on failed start (default 5)')
    # Early abort if no RX
    p.add_argument('--abort-no-rx-sec', type=float, default=float(os.getenv('VND_ABORT_NO_RX_SEC','5.0')), help='Abort test if no frames received within this time (sec). Default 5.0')
    p.add_argument('--abort-count-mode', choices=['data','any'], default=os.getenv('VND_ABORT_COUNT_MODE','data'), help='data = only A/B frames count; any = any frame (incl. STAT/TEST). Default data')
    # Strict start policy
    p.add_argument('--strict-start', action='store_true', help='If set, abort immediately when START probes fail to detect activity')
    # Fail-fast thresholds (per aggregate interval unless specified otherwise)
    p.add_argument('--fail-max-timeouts', type=int, default=int(os.getenv('VND_FAIL_MAX_TIMEOUTS','0')), help='Abort if timeouts per aggregate interval exceed this value (0=disabled)')
    p.add_argument('--fail-max-pipes', type=int, default=int(os.getenv('VND_FAIL_MAX_PIPES','0')), help='Abort if pipe errors per aggregate interval exceed this value (0=disabled)')
    p.add_argument('--fail-lag-frames', type=int, default=int(os.getenv('VND_FAIL_LAG_FRAMES','0')), help='Abort if cumulative (devTX - hostRX) A+B frame lag exceeds this value (requires --status-mode ctrl; 0=disabled)')
    # Start detection policy
    p.add_argument('--start-allow-stat', action='store_true', help='Treat seeing a STAT packet as a successful start (default: disabled; require A/B header or advancing device A/B counters)')
    # Optional JSON summary output
    p.add_argument('--summary-json', type=str, default=os.getenv('VND_SUMMARY_JSON',''), help='If set, write final summary as JSON to this path')
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
STREAM_MODE = args.stream_mode
FRAME_SAMPLES = args.frame_samples
ASYNC_MODE = args.async_mode
CH_MODE = args.ch_mode
VERBOSE = bool(args.verbose)
LOG_INTERVAL = args.log_interval if args.log_interval > 0 else 1.0

CMD_SET_STREAM_MODE = 0x1A
START_CHECK_SEC = args.start_check_sec if args.start_check_sec > 0 else 3.5
START_RETRIES = max(0, args.start_retries)
ABORT_NO_RX_SEC = args.abort_no_rx_sec if args.abort_no_rx_sec > 0 else 0.0
ABORT_COUNT_MODE = args.abort_count_mode
# Start policy
START_ALLOW_STAT = bool(args.start_allow_stat)
# Optional JSON path
SUMMARY_JSON_PATH = args.summary_json if getattr(args, 'summary_json', '') else ''
# Fail-fast thresholds
FAIL_MAX_TIMEOUTS = max(0, args.fail_max_timeouts)
FAIL_MAX_PIPES = max(0, args.fail_max_pipes)
FAIL_LAG_FRAMES = max(0, args.fail_lag_frames)
# Control GET_STATUS params
IFACE_INDEX = args.intf  # Vendor interface index in composite config
VND_CMD_GET_STATUS = 0x30
VND_CMD_SET_FULL_MODE = 0x13
VND_CMD_SET_PROFILE   = 0x14
VND_CMD_SET_ASYNC_MODE = 0x18
VND_CMD_SET_CHMODE     = 0x19
STATUS_MODE = args.status_mode  # 'none'|'ctrl'|'bulk'

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


def _is_timeout(e: Exception) -> bool:
    try:
        # PyUSB on Windows uses errno 10060; on Unix-like: 110 or 60; or message contains 'timed out'
        en = getattr(e, 'errno', None)
        if en in (10060, 110, 60):
            return True
        if 'timed out' in str(e).lower():
            return True
    except Exception:
        pass
    return False


def write_vendor(dev, payload: bytes, timeout_ms: int = 1000, label: str = "CMD", max_retries: int = 1) -> int:
    """Robust write to Vendor OUT with simple recovery on timeout.
    - On timeout: clear_halt(OUT), try SetInterface alt=1 again, and retry once.
    Returns number of bytes written or raises last exception.
    """
    try:
        return dev.write(OUT_EP, payload, timeout=timeout_ms)
    except Exception as e1:
        if not _is_timeout(e1) or max_retries <= 0:
            raise
        log_line(f"[HOST][WRITE][WARN] {label} timeout: {e1} -> try recover")
        # Try to recover endpoint and alt setting
        try:
            try:
                dev.clear_halt(OUT_EP)
                log_line(f"[HOST][RECOVER] clear_halt OUT 0x{OUT_EP:02X} OK")
            except Exception as ce:
                log_line(f"[HOST][RECOVER][WARN] clear_halt OUT failed: {ce}")
            # Try toggling altsetting 0 -> 1 to force EP reopen on device
            try:
                dev.set_interface_altsetting(interface=IFACE_INDEX, alternate_setting=0)
                log_line("[HOST][RECOVER] SetInterface alt=0 OK")
                time.sleep(0.02)
            except Exception as se0:
                log_line(f"[HOST][RECOVER][WARN] SetInterface alt=0 failed: {se0}")
            try:
                dev.set_interface_altsetting(interface=IFACE_INDEX, alternate_setting=1)
                log_line("[HOST][RECOVER] SetInterface alt=1 OK")
            except Exception as se1:
                log_line(f"[HOST][RECOVER][WARN] SetInterface alt=1 failed: {se1}")
            time.sleep(0.02)
            w = dev.write(OUT_EP, payload, timeout=timeout_ms)
            log_line(f"[HOST][WRITE][OK] {label} after recover: {w} bytes")
            return w
        except Exception as e2:
            raise e2


def find_iface_with_eps(dev, out_ep=OUT_EP, in_ep=IN_EP):
    """Try to locate the Vendor interface.
    Priority:
      1) interface that currently exposes both OUT/IN endpoints (works if alt=1 already active)
      2) fallback by interface number (IFACE_INDEX), even if alt=0 has 0 endpoints
    Returns (cfg, intf) or (None, None).
    """
    # First pass: endpoints present (alt=1 already)
    for cfg in dev:
        for intf in cfg:
            eps = [ep.bEndpointAddress for ep in intf]
            if out_ep in eps and in_ep in eps:
                return cfg, intf
    # Second pass: fallback by interface index (will require SetInterface to alt=1 later)
    try:
        for cfg in dev:
            for intf in cfg:
                if getattr(intf, 'bInterfaceNumber', None) == IFACE_INDEX:
                    return cfg, intf
    except Exception:
        pass
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


def get_status_ctrl(dev, timeout_ms: int = 200, quiet: bool = False):
    """Query device status via control transfer (EP0). Returns dict or None.
    timeout_ms kept small to minimize impact on streaming.
    """
    try:
        bm = usb.util.build_request_type(usb.util.CTRL_IN, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_INTERFACE)
        data = dev.ctrl_transfer(bm, VND_CMD_GET_STATUS, 0, IFACE_INDEX, 64, timeout=timeout_ms)
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
        if not quiet:
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
            # Prefer device method per PyUSB API
            dev.clear_halt(in_ep)
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


def ensure_stream_started(dev, claim_idx) -> bool:
    """Robust start detection with graceful patience:
      - Initial grace window START_CHECK_SEC (default 3.5s) for natural first frame.
      - During grace: fast probes (100ms) + optional STAT poll every 0.5s.
      - After grace: up to START_RETRIES structured kicks (alt toggle + START re-send), each granting another grace window.
      - Bulk/ctrl status requests (if enabled) are opportunistically queued to stimulate early STAT.
    Returns True on first observed STAT or frame header or advancing device counters.
    """
    t0 = time.time()
    deadline = t0 + START_CHECK_SEC
    rx_local = bytearray()
    tries = 0
    last_stat_req = 0.0
    STAT_POLL_MIN = 0.5
    while True:
        now = time.time()
        # Exit condition
        if not (now < deadline or tries < START_RETRIES):
            return False
        # 1) Quick read probe
        try:
            chunk = bytes(dev.read(IN_EP, 512, timeout=100))
            rx_local += chunk
            if len(rx_local) >= 4:
                if rx_local[0:4] == b'STAT':
                    # Не считаем наличие STAT достаточным признаком старта,
                    # если явно не разрешено через флаг. Ждём A/B заголовок или рост счётчиков устройства.
                    if START_ALLOW_STAT:
                        log_line(f"[HOST][START][TIME] {now-t0:.3f}s (STAT)")
                        return True
                    # иначе просто резинхронизируем буфер
                if rx_local[0:3] == b'\x5A\xA5\x01':
                    log_line(f"[HOST][START][TIME] {now-t0:.3f}s (HDR)")
                    return True
                # Resync minimal
                idx_stat = rx_local.find(b'STAT')
                idx_hdr = rx_local.find(b"\x5A\xA5\x01")
                best = -1
                if idx_stat != -1 and (idx_hdr == -1 or idx_stat < idx_hdr):
                    best = idx_stat
                elif idx_hdr != -1:
                    best = idx_hdr
                if best > 0:
                    rx_local = rx_local[best:]
        except usb.core.USBError:
            pass
        # 2) Counter-based detection (cheap, EP0)
        try:
            st = get_status_ctrl(dev, timeout_ms=120, quiet=True)
            # Считаем старт успешным только при наличии отправленных A/B или хотя бы одного завершённого TX (hdr/STAT)
            if st and (st.get('sent0',0) or st.get('sent1',0) or st.get('dbg_tx_cplt',0)):
                log_line(f"[HOST][START][TIME] {time.time()-t0:.3f}s (CTRL)")
                return True
        except Exception:
            pass
        # 3) Opportunistic STAT request over bulk to wake device early
        if STATUS_MODE == 'bulk' and (now - last_stat_req) >= STAT_POLL_MIN:
            try:
                dev.write(OUT_EP, bytes([VND_CMD_GET_STATUS]), timeout=200)
                last_stat_req = now
            except Exception:
                pass
        # 4) Kicks after grace expires
        if now >= deadline and tries < START_RETRIES:
            tries += 1
            log_line(f"[HOST][START][KICK] retry {tries}/{START_RETRIES}")
            try:
                try: dev.clear_halt(IN_EP)
                except Exception: pass
                try: dev.clear_halt(OUT_EP)
                except Exception: pass
                try:
                    dev.set_interface_altsetting(interface=claim_idx, alternate_setting=0)
                    time.sleep(0.04)
                    dev.set_interface_altsetting(interface=claim_idx, alternate_setting=1)
                    time.sleep(0.06)
                except Exception: pass
                try:
                    dev.write(OUT_EP, bytes([0x20]), timeout=600)
                except Exception as e:
                    log_line(f"[HOST][START][KICK][WARN] START failed: {e}")
            except Exception as e:
                log_line(f"[HOST][START][KICK][ERR] {e}")
            deadline = time.time() + START_CHECK_SEC
        # Small breather to avoid busy spin
        time.sleep(0.02)


def main():
    log_line(f"[HOST][CFG] VID=0x{VID:04X} PID=0x{PID:04X} intf={IFACE_INDEX} IN=0x{IN_EP:02X} OUT=0x{OUT_EP:02X} pairs={READ_COUNT} tmo={READ_TIMEOUT_MS}ms window={READ_WINDOW_SEC}s full={FULL_MODE} async={ASYNC_MODE} chmode={CH_MODE} rate={RATE_HZ}Hz statusMode={STATUS_MODE} verbose={int(VERBOSE)} interval={LOG_INTERVAL}s")
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
        # Give the device stack a brief moment to open EPs after alt-switch (Windows/WinUSB is sensitive)
        time.sleep(0.1)
        try:
            dev.clear_halt(OUT_EP)
        except Exception:
            pass
    except Exception as e:
        log_line(f"[HOST][WARN] SetInterface alt=1 failed: {e}")

    # Stop any ongoing stream first (in case device was already streaming)
    try:
        dev.write(OUT_EP, bytes([0x21]), timeout=500)  # CMD_STOP
        log_line("[HOST] Sent STOP command (cleanup before config)")
        time.sleep(0.2)  # дадим устройству время остановиться
    except Exception:
        pass  # игнорируем ошибки, если устройство уже остановлено

    # Configure windows and block rate before START
    try:
        payload = struct.pack('<BHHHH', 0x10, WIN0_START, WIN0_LEN, WIN1_START, WIN1_LEN)
        w1 = write_vendor(dev, payload, timeout_ms=1000, label="SET_WINDOWS", max_retries=1)
        log_line(f"[HOST] SET_WINDOWS written: {w1} bytes ({WIN0_START},{WIN0_LEN}) ({WIN1_START},{WIN1_LEN})")
        time.sleep(0.02)
    except Exception as e:
        log_line(f"[HOST][WARN] SET_WINDOWS failed: {e}")

    try:
        payload = struct.pack('<BH', 0x11, RATE_HZ)
        w2 = write_vendor(dev, payload, timeout_ms=1000, label="SET_BLOCK_RATE", max_retries=1)
        log_line(f"[HOST] SET_BLOCK_RATE written: {w2} bytes ({RATE_HZ} Hz)")
        time.sleep(0.02)
    except Exception as e:
        log_line(f"[HOST][WARN] SET_BLOCK_RATE failed: {e}")

    # Optional: set frame samples for ~20 FPS
    if FRAME_SAMPLES and FRAME_SAMPLES > 0:
        try:
            payload = struct.pack('<BH', 0x17, FRAME_SAMPLES)
            wfs = write_vendor(dev, payload, timeout_ms=1000, label="SET_FRAME_SAMPLES", max_retries=1)
            log_line(f"[HOST] SET_FRAME_SAMPLES written: {wfs} bytes (Ns={FRAME_SAMPLES})")
            time.sleep(0.02)
        except Exception as e:
            log_line(f"[HOST][WARN] SET_FRAME_SAMPLES failed: {e}")

    # Ensure full mode and selected profile
    try:
        fm = 0x01 if FULL_MODE else 0x00
        w3 = write_vendor(dev, bytes([VND_CMD_SET_FULL_MODE, fm]), timeout_ms=1000, label="SET_FULL_MODE", max_retries=1)
        log_line(f"[HOST] SET_FULL_MODE({FULL_MODE}) written: {w3} bytes")
        time.sleep(0.02)
    except Exception as e:
        log_line(f"[HOST][WARN] SET_FULL_MODE failed: {e}")
    try:
        prof = args.profile & 0xFF
        w4 = write_vendor(dev, bytes([VND_CMD_SET_PROFILE, prof]), timeout_ms=1000, label="SET_PROFILE", max_retries=1)
        log_line(f"[HOST] SET_PROFILE({prof}) written: {w4} bytes")
        time.sleep(0.02)
    except Exception as e:
        log_line(f"[HOST][WARN] SET_PROFILE failed: {e}")

    # Toggle async mode before START (default: enabled)
    try:
        am = 0x01 if ASYNC_MODE else 0x00
        w5 = write_vendor(dev, bytes([VND_CMD_SET_ASYNC_MODE, am]), timeout_ms=1000, label="SET_ASYNC_MODE", max_retries=1)
        log_line(f"[HOST] SET_ASYNC_MODE({ASYNC_MODE}) written: {w5} bytes")
        time.sleep(0.02)
    except Exception as e:
        log_line(f"[HOST][WARN] SET_ASYNC_MODE failed: {e}")

    # Optional: select streaming mode (0=latest lossy, 1=lossless ROI)
    try:
        sm = STREAM_MODE & 0xFF
        wsm = write_vendor(dev, bytes([CMD_SET_STREAM_MODE, sm]), timeout_ms=1000, label="SET_STREAM_MODE", max_retries=1)
        log_line(f"[HOST] SET_STREAM_MODE({sm}) written: {wsm} bytes")
        time.sleep(0.02)
    except Exception as e:
        log_line(f"[HOST][WARN] SET_STREAM_MODE failed: {e}")

    # Set channel mode (0=A-only by default per current stabilization goal)
    try:
        cm = CH_MODE & 0xFF
        w6 = write_vendor(dev, bytes([VND_CMD_SET_CHMODE, cm]), timeout_ms=1000, label="SET_CHMODE", max_retries=1)
        log_line(f"[HOST] SET_CHMODE({CH_MODE}) written: {w6} bytes")
        time.sleep(0.02)
    except Exception as e:
        log_line(f"[HOST][WARN] SET_CHMODE failed: {e}")

    # Небольшая пауза после всех SET_* перед START, чтобы устройство стабилизировало состояние EP/конфигурацию
    time.sleep(0.10)

    # Send START (0x20) to OUT EP
    data = bytes([0x20])
    wlen = write_vendor(dev, data, timeout_ms=1000, label="START", max_retries=1)
    log_line(f"[HOST] START written: {wlen} bytes to EP 0x{OUT_EP:02X}")
    time.sleep(0.02)
    # Ensure stream started (short probe; optional recovery kicks)
    started = ensure_stream_started(dev, claim_idx)
    if not started:
        log_line("[HOST][START][FAIL] did not observe frames/counters after START probes")
    else:
        log_line("[HOST][START][OK] stream activity detected")
    # Strict-start: abort immediately if start probes failed
    if (not started) and args.strict_start:
        log_line("[HOST][ABORT] failed to start stream during probes (strict-start)")
        # Best-effort STOP before exit
        try:
            dev.write(OUT_EP, bytes([0x21]), timeout=500)
        except Exception:
            pass
        try:
            usb.util.release_interface(dev, claim_idx)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass
        sys.exit(2)
    # Optionally request initial STAT snapshot (throttled)
    if STATUS_MODE == 'ctrl':
        st0 = get_status_ctrl(dev)
        if st0:
            log_line(f"[HOST_STAT0] ver={st0['version']} flags=0x{st0['flags_runtime']:04X} test={st0['test_frames']} seq={st0['produced_seq']} sentA/B={st0['sent0']}/{st0['sent1']} dma={st0['dma_done0']}/{st0['dma_done1']} cur_samples={st0['cur_samples']}")
    elif STATUS_MODE == 'bulk':
        queue_status_bulk(dev)

    # Logger is defined at module level

    # Read several complete frames (STAT/TEST/A/B), reassembling from 512B packets
    got = 0
    cnt_a = 0
    cnt_b = 0
    cnt_stat = 0
    # Zero-frame analysis counters (frames whose payload samples are all 0x0000)
    zero_a = 0
    zero_b = 0
    data_a = 0  # non-zero
    data_b = 0
    timeouts = 0
    pipe_err_total = 0
    start_time = time.time()
    last_stat_print = 0.0
    # Aggregate logging state
    last_agg_print = start_time
    agg_a = 0
    agg_b = 0
    agg_stat = 0
    agg_timeouts = 0
    agg_pipes = 0
    # Суммарные байты за интервал (инициализируем заранее, чтобы избежать UnboundLocalError при ветке без первых успешных чтений)
    agg_bytes = 0
    # Per-interval zero/data counters
    agg_zero_a = 0
    agg_zero_b = 0
    agg_data_a = 0
    agg_data_b = 0
    # Device-side counters for per-second delta (only when STATUS_MODE == 'ctrl')
    last_dev_sent0 = None
    last_dev_sent1 = None
    last_dev_txcplt = None
    # Accumulated lag between device TX (A+B) and host RX (A+B) across intervals
    lag_accum_ab = 0
    rx = bytearray()
    pipe_errs = 0
    aborted_no_rx = False
    abort_reason = ""
    # Повторные попытки при отсутствии приёма — мягкая перезапуск/"bus kick" (alt 0->1, clear_halt, START)
    no_rx_retries = int(os.getenv('VND_NO_RX_RETRIES','2'))
    used_norx_retries = 0
    # Track last STAT for bulk/fallback loss metrics
    last_stat_status = None
    # Throttle for STAT polls to reduce contention (min 0.5s)
    MIN_STAT_INTERVAL = max(0.5, float(os.getenv('VND_MIN_STAT_SEC','0.5')))

    def _rx_count():
        return (cnt_a + cnt_b) if ABORT_COUNT_MODE == 'data' else got
    while ((READ_COUNT <= 0) or (got < READ_COUNT)) and (time.time() - start_time) < READ_WINDOW_SEC:
        try:
            chunk = bytes(dev.read(IN_EP, 512, timeout=READ_TIMEOUT_MS))
            rx += chunk
            pipe_errs = 0  # reset on success
            total_bytes = len(chunk)
            # Accumulate per-interval raw bytes (host-side throughput)
            # Счётчик байт за интервал: уже инициализирован выше, не проверяем locals()
            agg_bytes += total_bytes
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
                    if VERBOSE:
                        head = ' '.join(f"{b:02X}" for b in frame[:4])
                        log_line(f"[HOST_RX] ep=0x{IN_EP:02X} len={len(frame)} type=STAT head={head}")
                    st = parse_stat_frame(frame)
                    if st is not None:
                        last_stat_status = st
                    if st and VERBOSE:
                        base = f"ver={st['version']} flags=0x{st['flags_runtime']:04X} test={st['test_frames']} seq={st['produced_seq']} sentA/B={st['sent0']}/{st['sent1']} TxCplt={st['dbg_tx_cplt']} dma={st['dma_done0']}/{st['dma_done1']} cur_samples={st['cur_samples']} wr_seq={st['frame_wr_seq']}"
                        ext = ""
                        if 'flags2' in st:
                            ext = f" flags2=0x{st['flags2']:04X} send_ch={st['sending_ch']} pair {st['pair_fill']}/{st['pair_send']} lastTX={st['last_tx_len']} cur_seq={st['cur_stream_seq']} prep {st['prep_calls']}/{st['prep_ok']}"
                        log_line(f"[HOST_STAT] {base}{ext}")
                    got += 1
                    cnt_stat += 1
                    agg_stat += 1
                    continue
                # Frame header?
                if rx[0] == 0x5A and rx[1] == 0xA5 and rx[2] == 0x01 and len(rx) >= 16:
                    total_samples = rx[12] | (rx[13] << 8)
                    flen = 32 + total_samples * 2
                    if len(rx) < flen:
                        break
                    flags = rx[3]
                    ch_bits = flags & 0x03  # 0x01=A, 0x02=B; CRC и прочие биты маскируем
                    ftype = 'TEST' if (flags & 0x80) else ('A' if ch_bits == 0x01 else ('B' if ch_bits == 0x02 else 'UNK'))
                    frame = bytes(rx[:flen]); rx = rx[flen:]
                    if VERBOSE:
                        head = ' '.join(f"{b:02X}" for b in frame[:4])
                        log_line(f"[HOST_RX] ep=0x{IN_EP:02X} len={len(frame)} type={ftype} head={head}")
                    got += 1
                    if ftype == 'A':
                        cnt_a += 1
                        agg_a += 1
                        # Zero-frame classification (only sample payload, skip header 32 bytes)
                        if total_samples > 0:
                            samples_bytes = frame[32:32 + total_samples * 2]
                            if samples_bytes and all(b == 0 for b in samples_bytes):
                                zero_a += 1
                                agg_zero_a += 1
                            else:
                                data_a += 1
                                agg_data_a += 1
                    elif ftype == 'B':
                        cnt_b += 1
                        agg_b += 1
                        if total_samples > 0:
                            samples_bytes = frame[32:32 + total_samples * 2]
                            if samples_bytes and all(b == 0 for b in samples_bytes):
                                zero_b += 1
                                agg_zero_b += 1
                            else:
                                data_b += 1
                                agg_data_b += 1
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
            # Periodic aggregate print
            now_agg = time.time()
            if not VERBOSE and (now_agg - last_agg_print) >= LOG_INTERVAL:
                dt = now_agg - last_agg_print
                fps_a = agg_a / dt if dt > 0 else 0.0
                fps_b = agg_b / dt if dt > 0 else 0.0
                mbps = (agg_bytes * 8.0 / dt / 1_000_000.0) if dt>0 else 0.0
                # Optionally poll device counters once per interval to compare TX vs RX
                dev_info = ""
                dev_dA = dev_dB = dev_dT = None
                if STATUS_MODE == 'ctrl' and (now_agg - last_stat_print) >= MIN_STAT_INTERVAL:
                    st = get_status_ctrl(dev, timeout_ms=150)
                    if st is not None:
                        s0 = st.get('sent0', 0)
                        s1 = st.get('sent1', 0)
                        tx = st.get('dbg_tx_cplt', 0)
                        dA = s0 - (last_dev_sent0 if last_dev_sent0 is not None else s0)
                        dB = s1 - (last_dev_sent1 if last_dev_sent1 is not None else s1)
                        dT = tx - (last_dev_txcplt if last_dev_txcplt is not None else tx)
                        dev_dA, dev_dB, dev_dT = dA, dB, dT
                        dev_info = f" devTX A={dA} B={dB} TxCplt={dT} (tot A={s0} B={s1} TxCplt={tx})"
                        last_dev_sent0, last_dev_sent1, last_dev_txcplt = s0, s1, tx
                # Fail-fast checks (per-aggregate)
                if FAIL_MAX_TIMEOUTS and agg_timeouts > FAIL_MAX_TIMEOUTS:
                    aborted_no_rx = True
                    abort_reason = f"timeouts per {LOG_INTERVAL:.1f}s interval exceeded ({agg_timeouts}>{FAIL_MAX_TIMEOUTS})"
                if not abort_reason and FAIL_MAX_PIPES and agg_pipes > FAIL_MAX_PIPES:
                    aborted_no_rx = True
                    abort_reason = f"pipe errors per {LOG_INTERVAL:.1f}s interval exceeded ({agg_pipes}>{FAIL_MAX_PIPES})"
                if not abort_reason and FAIL_LAG_FRAMES and STATUS_MODE == 'ctrl' and dev_dA is not None and dev_dB is not None:
                    host_ab = (agg_a + agg_b)
                    dev_ab = (dev_dA + dev_dB)
                    delta_lag = dev_ab - host_ab
                    if delta_lag > 0:
                        lag_accum_ab += delta_lag
                    # If host caught up, allow partial forgiveness but do not go below 0
                    elif delta_lag < 0:
                        lag_accum_ab = max(0, lag_accum_ab + delta_lag)
                    if lag_accum_ab > FAIL_LAG_FRAMES:
                        aborted_no_rx = True
                        abort_reason = f"host RX lags device TX by >{FAIL_LAG_FRAMES} frames (accum={lag_accum_ab})"
                # Idle heuristic: fraction of interval consumed by timeout events (each timeout ~READ_TIMEOUT_MS) — coarse
                idle_est = 0.0
                if READ_TIMEOUT_MS>0 and agg_timeouts>0:
                    approx_timeout_time = min(dt, (agg_timeouts * READ_TIMEOUT_MS)/1000.0)
                    idle_est = approx_timeout_time / dt * 100.0
                # Zero-frame percentages within interval
                z_pct_a = (agg_zero_a / agg_a * 100.0) if agg_a > 0 and agg_zero_a > 0 else 0.0
                z_pct_b = (agg_zero_b / agg_b * 100.0) if agg_b > 0 and agg_zero_b > 0 else 0.0
                log_line(f"[STAT] +{dt:.1f}s A={agg_a} ({fps_a:.1f}/s,z0={agg_zero_a},z%={z_pct_a:.2f}) B={agg_b} ({fps_b:.1f}/s,z0={agg_zero_b},z%={z_pct_b:.2f}) STAT={agg_stat} bytes={agg_bytes} mbps={mbps:.3f} timeouts={agg_timeouts} pipes={agg_pipes} idle~{idle_est:.1f}%{dev_info}")
                agg_a = agg_b = agg_stat = agg_timeouts = agg_pipes = 0
                agg_bytes = 0
                agg_zero_a = agg_zero_b = agg_data_a = agg_data_b = 0
                last_agg_print = now_agg
                if abort_reason:
                    log_line(f"[HOST][ABORT] {abort_reason}")
                    break
            # Early abort if no RX for too long
            if ABORT_NO_RX_SEC > 0 and _rx_count() == 0 and (time.time() - start_time) >= ABORT_NO_RX_SEC:
                # Одноразово попробуем получить STAT через EP0 для диагностики перед перезапуском/абортом
                if STATUS_MODE == 'bulk':
                    stn = get_status_ctrl(dev, timeout_ms=200, quiet=False)
                    if stn is not None:
                        log_line(f"[HOST][NRX][STAT] sentA/B={stn.get('sent0',0)}/{stn.get('sent1',0)} txcplt={stn.get('dbg_tx_cplt',0)} flags=0x{stn.get('flags_runtime',0):04X}")
                if used_norx_retries < no_rx_retries:
                    used_norx_retries += 1
                    log_line(f"[HOST][NRX][RETRY] kick #{used_norx_retries}/{no_rx_retries}")
                    try:
                        try:
                            dev.clear_halt(IN_EP)
                        except Exception:
                            pass
                        try:
                            dev.clear_halt(OUT_EP)
                        except Exception:
                            pass
                        try:
                            dev.set_interface_altsetting(interface=claim_idx, alternate_setting=0)
                            time.sleep(0.03)
                            dev.set_interface_altsetting(interface=claim_idx, alternate_setting=1)
                            time.sleep(0.05)
                        except Exception:
                            pass
                        # re-START
                        try:
                            dev.write(OUT_EP, bytes([0x20]), timeout=500)
                            log_line("[HOST][NRX][RETRY] START re-sent")
                        except Exception as e:
                            log_line(f"[HOST][NRX][RETRY][WARN] START failed: {e}")
                    except Exception as e:
                        log_line(f"[HOST][NRX][RETRY][ERR] {e}")
                    # Сброс таймера без обнуления общих счётчиков
                    start_time = time.time()
                    continue
                else:
                    aborted_no_rx = True
                    abort_reason = f"no RX for {ABORT_NO_RX_SEC:.1f}s (mode={ABORT_COUNT_MODE})"
                    log_line(f"[HOST][ABORT] {abort_reason}")
                    break
        except usb.core.USBError as e:
            msg = str(e).lower()
            # Treat common timeout errnos/messages as non-fatal (Windows 10060, POSIX 110/ETIMEDOUT)
            if (getattr(e, 'errno', None) in (10060, 110, 60)) or ('timed out' in msg) or ('timeout' in msg):
                if VERBOSE:
                    log_line("[HOST_RX] timeout")
                timeouts += 1
                agg_timeouts += 1
                # Emit periodic aggregate even if we are timing out
                now_agg = time.time()
                if not VERBOSE and (now_agg - last_agg_print) >= LOG_INTERVAL:
                    dt = now_agg - last_agg_print
                    fps_a = agg_a / dt if dt > 0 else 0.0
                    fps_b = agg_b / dt if dt > 0 else 0.0
                    dev_info = ""
                    dev_dA = dev_dB = dev_dT = None
                    if STATUS_MODE == 'ctrl' and (now_agg - last_stat_print) >= MIN_STAT_INTERVAL:
                        st = get_status_ctrl(dev, timeout_ms=150)
                        if st is not None:
                            s0 = st.get('sent0', 0)
                            s1 = st.get('sent1', 0)
                            tx = st.get('dbg_tx_cplt', 0)
                            dA = s0 - (last_dev_sent0 if last_dev_sent0 is not None else s0)
                            dB = s1 - (last_dev_sent1 if last_dev_sent1 is not None else s1)
                            dT = tx - (last_dev_txcplt if last_dev_txcplt is not None else tx)
                            dev_dA, dev_dB, dev_dT = dA, dB, dT
                            dev_info = f" devTX A={dA} B={dB} TxCplt={dT} (tot A={s0} B={s1} TxCplt={tx})"
                            last_dev_sent0, last_dev_sent1, last_dev_txcplt = s0, s1, tx
                    # Fail-fast checks (per-aggregate)
                    if FAIL_MAX_TIMEOUTS and agg_timeouts > FAIL_MAX_TIMEOUTS:
                        aborted_no_rx = True
                        abort_reason = f"timeouts per {LOG_INTERVAL:.1f}s interval exceeded ({agg_timeouts}>{FAIL_MAX_TIMEOUTS})"
                    if not abort_reason and FAIL_MAX_PIPES and agg_pipes > FAIL_MAX_PIPES:
                        aborted_no_rx = True
                        abort_reason = f"pipe errors per {LOG_INTERVAL:.1f}s interval exceeded ({agg_pipes}>{FAIL_MAX_PIPES})"
                    if not abort_reason and FAIL_LAG_FRAMES and STATUS_MODE == 'ctrl' and dev_dA is not None and dev_dB is not None:
                        host_ab = (agg_a + agg_b)
                        dev_ab = (dev_dA + dev_dB)
                        delta_lag = dev_ab - host_ab
                        if delta_lag > 0:
                            lag_accum_ab += delta_lag
                        elif delta_lag < 0:
                            lag_accum_ab = max(0, lag_accum_ab + delta_lag)
                        if lag_accum_ab > FAIL_LAG_FRAMES:
                            aborted_no_rx = True
                            abort_reason = f"host RX lags device TX by >{FAIL_LAG_FRAMES} frames (accum={lag_accum_ab})"
                    mbps = (agg_bytes * 8.0 / dt / 1_000_000.0) if dt>0 else 0.0
                    idle_est = 0.0
                    if READ_TIMEOUT_MS>0 and agg_timeouts>0:
                        approx_timeout_time = min(dt, (agg_timeouts * READ_TIMEOUT_MS)/1000.0)
                        idle_est = approx_timeout_time / dt * 100.0
                    z_pct_a = (agg_zero_a / agg_a * 100.0) if agg_a > 0 and agg_zero_a > 0 else 0.0
                    z_pct_b = (agg_zero_b / agg_b * 100.0) if agg_b > 0 and agg_zero_b > 0 else 0.0
                    log_line(f"[STAT] +{dt:.1f}s A={agg_a} ({fps_a:.1f}/s,z0={agg_zero_a},z%={z_pct_a:.2f}) B={agg_b} ({fps_b:.1f}/s,z0={agg_zero_b},z%={z_pct_b:.2f}) STAT={agg_stat} bytes={agg_bytes} mbps={mbps:.3f} timeouts={agg_timeouts} pipes={agg_pipes} idle~{idle_est:.1f}%{dev_info}")
                    agg_a = agg_b = agg_stat = agg_timeouts = agg_pipes = 0
                    agg_bytes = 0
                    agg_zero_a = agg_zero_b = agg_data_a = agg_data_b = 0
                    last_agg_print = now_agg
                    if abort_reason:
                        log_line(f"[HOST][ABORT] {abort_reason}")
                        break
                # Periodically request STAT to aid diagnosis (bulk preferred)
                now = time.time()
                if STATUS_MODE != 'none' and (now - last_stat_print) >= MIN_STAT_INTERVAL:
                    last_stat_print = now
                    if STATUS_MODE == 'ctrl':
                        st = get_status_ctrl(dev, quiet=False)
                        if st and VERBOSE:
                            log_line(f"[HOST_STAT] ver={st['version']} flags=0x{st['flags_runtime']:04X} test={st['test_frames']} seq={st['produced_seq']} sentA/B={st['sent0']}/{st['sent1']} TxCplt={st['dbg_tx_cplt']} dma={st['dma_done0']}/{st['dma_done1']} cur_samples={st['cur_samples']} wr_seq={st['frame_wr_seq']}")
                    elif STATUS_MODE == 'bulk':
                        queue_status_bulk(dev)
                # Early abort if no RX for too long
                if ABORT_NO_RX_SEC > 0 and _rx_count() == 0 and (time.time() - start_time) >= ABORT_NO_RX_SEC:
                    if STATUS_MODE == 'bulk':
                        stn = get_status_ctrl(dev, timeout_ms=200, quiet=False)
                        if stn is not None:
                            log_line(f"[HOST][NRX][STAT] sentA/B={stn.get('sent0',0)}/{stn.get('sent1',0)} txcplt={stn.get('dbg_tx_cplt',0)} flags=0x{stn.get('flags_runtime',0):04X}")
                    if used_norx_retries < no_rx_retries:
                        used_norx_retries += 1
                        log_line(f"[HOST][NRX][RETRY] kick #{used_norx_retries}/{no_rx_retries}")
                        try:
                            try:
                                dev.clear_halt(IN_EP)
                            except Exception:
                                pass
                            try:
                                dev.clear_halt(OUT_EP)
                            except Exception:
                                pass
                            try:
                                dev.set_interface_altsetting(interface=claim_idx, alternate_setting=0)
                                time.sleep(0.03)
                                dev.set_interface_altsetting(interface=claim_idx, alternate_setting=1)
                                time.sleep(0.05)
                            except Exception:
                                pass
                            try:
                                dev.write(OUT_EP, bytes([0x20]), timeout=500)
                                log_line("[HOST][NRX][RETRY] START re-sent")
                            except Exception as e:
                                log_line(f"[HOST][NRX][RETRY][WARN] START failed: {e}")
                        except Exception as e:
                            log_line(f"[HOST][NRX][RETRY][ERR] {e}")
                        start_time = time.time()
                        continue
                    else:
                        aborted_no_rx = True
                        abort_reason = f"no RX for {ABORT_NO_RX_SEC:.1f}s (mode={ABORT_COUNT_MODE})"
                        log_line(f"[HOST][ABORT] {abort_reason}")
                        break
                continue
            # Handle pipe errors gracefully: clear halt, reselect alt=1, re-send START
            if (getattr(e, 'errno', None) in (32, 5)) or ('pipe' in msg):
                pipe_errs += 1
                pipe_err_total += 1
                if VERBOSE:
                    log_line(f"[HOST_RX][PIPE] {e} (#{pipe_errs})")
                agg_pipes += 1
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

    # Final summary (attempt to obtain final STAT BEFORE STOP to improve chances)
    elapsed = time.time() - start_time
    dev_tot = ""
    final_status = None  # capture final STAT dict if available for loss metrics
    if STATUS_MODE == 'ctrl':
        # If we have last device totals captured, print them for final comparison
        try:
            st = get_status_ctrl(dev, timeout_ms=250, quiet=False)
            if st is not None:
                final_status = st
                dev_tot = f" dev_tot A={st.get('sent0',0)} B={st.get('sent1',0)} TxCplt={st.get('dbg_tx_cplt',0)} seq={st.get('produced_seq',0)}"
        except Exception:
            pass
        # Fallback attempts if first control STAT failed (heavy bus load or EP0 contention)
        if final_status is None:
            try:
                st2 = get_status_ctrl(dev, timeout_ms=600, quiet=False)
                if st2 is not None:
                    final_status = st2
                    dev_tot = f" dev_tot A={st2.get('sent0',0)} B={st2.get('sent1',0)} TxCplt={st2.get('dbg_tx_cplt',0)} seq={st2.get('produced_seq',0)}"
            except Exception:
                pass
        # Bulk fallback: request STAT frame via OUT (0x30) then sniff a few IN packets
        if final_status is None:
            try:
                dev.write(OUT_EP, bytes([VND_CMD_GET_STATUS]), timeout=500)
                # Try to read up to ~1s total in small chunks while stream is still active
                tmp = bytearray()
                t_dead = time.time() + 1.0
                while time.time() < t_dead:
                    try:
                        chunk = bytes(dev.read(IN_EP, 512, timeout=150))
                        tmp += chunk
                        # Attempt incremental extraction of STAT frame
                        if len(tmp) >= 4:
                            # resync to either STAT signature or frame header; keep buffer small
                            idx = tmp.find(b'STAT')
                            if idx != -1 and len(tmp) - idx >= 52:
                                flen = 64 if len(tmp) - idx >= 64 else 52
                                frame = bytes(tmp[idx: idx + flen])
                                pf = parse_stat_frame(frame)
                                if pf is not None:
                                    final_status = pf
                                    dev_tot = f" dev_tot A={pf.get('sent0',0)} B={pf.get('sent1',0)} TxCplt={pf.get('dbg_tx_cplt',0)} seq={pf.get('produced_seq',0)}"
                                    break
                            # prevent unbounded growth
                            if len(tmp) > 4096:
                                tmp = tmp[-1024:]
                    except usb.core.USBError as e:
                        # ignore timeouts in this short window
                        if not _is_timeout(e):
                            break
            except Exception:
                pass
    elif STATUS_MODE == 'bulk':
        # Use last STAT frame seen during streaming, if any
        if last_stat_status is not None:
            final_status = last_stat_status
            dev_tot = f" dev_tot A={final_status.get('sent0',0)} B={final_status.get('sent1',0)} TxCplt={final_status.get('dbg_tx_cplt',0)} seq={final_status.get('produced_seq',0)}"
    # Include lag accumulator into summary if enabled
    lag_info = f" lag_accum={lag_accum_ab}" if FAIL_LAG_FRAMES else ""
    # Final zero-frame stats
    z_pct_a_total = (zero_a / cnt_a * 100.0) if cnt_a > 0 and zero_a > 0 else 0.0
    z_pct_b_total = (zero_b / cnt_b * 100.0) if cnt_b > 0 and zero_b > 0 else 0.0
    log_line(f"[HOST][SUMMARY] elapsed={elapsed:.1f}s A={cnt_a} (z0={zero_a},z%={z_pct_a_total:.2f}) B={cnt_b} (z0={zero_b},z%={z_pct_b_total:.2f}) STAT={cnt_stat} timeouts={timeouts} pipe_errors={pipe_err_total}{lag_info}{dev_tot}")

    # Loss metrics: compare device sent vs host received (only when STATUS_MODE=ctrl and final_status present)
    if final_status is not None:
        sentA = int(final_status.get('sent0', 0))
        sentB = int(final_status.get('sent1', 0))
        lostA = max(0, sentA - cnt_a)
        lostB = max(0, sentB - cnt_b)
        lossPctA = (lostA / sentA * 100.0) if sentA > 0 else 0.0
        lossPctB = (lostB / sentB * 100.0) if sentB > 0 else 0.0
        # Combined A+B perspective
        sentAB = sentA + sentB
        gotAB = cnt_a + cnt_b
        lostAB = max(0, sentAB - gotAB)
        lossPctAB = (lostAB / sentAB * 100.0) if sentAB > 0 else 0.0
        log_line(f"[HOST][LOSS] dev_sent A={sentA} B={sentB} host_rx A={cnt_a} B={cnt_b} lost A={lostA} ({lossPctA:.4f}%) B={lostB} ({lossPctB:.4f}%) A+B lost={lostAB} ({lossPctAB:.4f}%)")

        # Optional threshold warnings (informational, non-fatal)
        warn_thresh = 0.1  # percent
        crit_thresh = 1.0  # percent
        if lossPctAB > crit_thresh:
            log_line(f"[HOST][LOSS][CRIT] Combined loss {lossPctAB:.3f}% exceeds {crit_thresh:.3f}%")
        elif lossPctAB > warn_thresh:
            log_line(f"[HOST][LOSS][WARN] Combined loss {lossPctAB:.3f}% exceeds {warn_thresh:.3f}%")

    # Optional STOP (after we've tried to collect final STAT)
    try:
        slen = dev.write(OUT_EP, bytes([0x21]), timeout=1000)
        log_line(f"[HOST] STOP written: {slen} bytes")
    except Exception as e:
        log_line(f"[HOST] STOP write failed: {e}")

    # Мягкий сброс altsetting для очистки состояний EP перед следующими запусками в той же сессии
    try:
        try:
            dev.set_interface_altsetting(interface=claim_idx, alternate_setting=0)
            time.sleep(0.02)
            dev.set_interface_altsetting(interface=claim_idx, alternate_setting=1)
            time.sleep(0.02)
            log_line("[HOST] Alt reset 0->1 done")
        except Exception:
            pass
    except Exception:
        pass

    try:
        usb.util.release_interface(dev, claim_idx)
    except Exception:
        pass

    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass

    # Запись JSON-резюме при необходимости
    if SUMMARY_JSON_PATH:
        try:
            import json
            out = {
                'elapsed_sec': round(elapsed, 3),
                'A': cnt_a,
                'B': cnt_b,
                'STAT': cnt_stat,
                'timeouts': timeouts,
                'pipe_errors': pipe_err_total,
                'zero_a': zero_a,
                'zero_b': zero_b,
                'data_a': data_a,
                'data_b': data_b,
            }
            if final_status is not None:
                out['dev_tot'] = {
                    'sentA': int(final_status.get('sent0',0)),
                    'sentB': int(final_status.get('sent1',0)),
                    'TxCplt': int(final_status.get('dbg_tx_cplt',0)),
                    'seq': int(final_status.get('produced_seq',0)),
                }
            # ensure directory exists
            d = os.path.dirname(SUMMARY_JSON_PATH)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(SUMMARY_JSON_PATH, 'w', encoding='utf-8') as jf:
                json.dump(out, jf, ensure_ascii=False, indent=2)
            log_line(f"[HOST][SUMMARY][JSON] written to {SUMMARY_JSON_PATH}")
        except Exception as je:
            log_line(f"[HOST][SUMMARY][JSON][ERR] {je}")

    # Exit code policy: abort if no frames received
    if aborted_no_rx or (cnt_a + cnt_b + cnt_stat) == 0:
        # Non-zero exit to indicate failure for automation
        msg = "no frames received" if not abort_reason else abort_reason
        log_line(f"[HOST][FAIL] {msg}")
        sys.exit(2)


if __name__ == '__main__':
    main()
