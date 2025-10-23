#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Focused checks for:
 1) STOP (0x21): device must send STAT (ACK-STOP) promptly, then halt streaming only after that STAT's TxCplt.
 2) Mid-stream GET_STATUS (0x30): exactly one STAT strictly between A and B frames; streaming continues afterwards.

Environment variables (optional):
  VND_VID, VND_PID           - default 0xCAFE, 0x4001
  VND_READ_TIMEOUT           - per-packet read timeout ms (default 1500)
  VND_WAIT_AFTER_STOP_MS     - how long to probe for any extra frames after STOP ACK (default 700)
  VND_STATUS_CTRL            - if '1', use control GET_STATUS (EP0) once upfront (default 0)
  VND_HOST_LOG               - path to append logs (default HostTools/host_rx.log)
"""
import os
import sys
import time
import struct
import usb.core
import usb.util

VID = int(os.getenv('VND_VID', '0xCAFE'), 16)
PID = int(os.getenv('VND_PID', '0x4001'), 16)
OUT_EP = 0x03
IN_EP  = 0x83
READ_TIMEOUT_MS = int(os.getenv('VND_READ_TIMEOUT', '1500'))
WAIT_AFTER_STOP_MS = int(os.getenv('VND_WAIT_AFTER_STOP_MS', '700'))
LOG_PATH = os.getenv('VND_HOST_LOG', 'HostTools/host_rx.log')
USE_CTRL_STATUS = bool(int(os.getenv('VND_STATUS_CTRL', '0')))
IFACE_INDEX = 2
VND_CMD_GET_STATUS = 0x30


def log_line(s: str):
    print(s)
    try:
        d = os.path.dirname(LOG_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
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
    try:
        if len(ba) < 52 or ba[0:4] != b'STAT':
            return None
        base = struct.unpack('<4sBBHHHIIIIIIIIIHH', ba[:52])
        res = {
            'version': base[1],
            'flags_runtime': base[15],
        }
        if len(ba) >= 64:
            flags2 = int.from_bytes(ba[50:52], 'little')
            last_tx_len = int.from_bytes(ba[56:58], 'little')
            cur_stream_seq = int.from_bytes(ba[58:62], 'little')
            res.update({'flags2': flags2, 'last_tx_len': last_tx_len, 'cur_stream_seq': cur_stream_seq})
        return res
    except Exception:
        return None


def get_status_ctrl(dev):
    try:
        bm = usb.util.build_request_type(usb.util.CTRL_IN, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_INTERFACE)
        data = dev.ctrl_transfer(bm, VND_CMD_GET_STATUS, 0, IFACE_INDEX, 64, timeout=500)
        ba = bytes(data)
        if len(ba) >= 52 and ba[:4] == b'STAT':
            return parse_stat_frame(ba)
    except Exception as e:
        log_line(f"[HOST][STAT-CTRL][ERR] {e}")
    return None


def queue_status_bulk(dev):
    try:
        dev.write(OUT_EP, bytes([VND_CMD_GET_STATUS]), timeout=500)
        log_line("[HOST] GET_STATUS queued via BULK")
    except Exception as e:
        log_line(f"[HOST][GET_STATUS][BULK][ERR] {e}")


def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        log_line(f"[ERR] Device not found VID=0x{VID:04X} PID=0x{PID:04X}")
        sys.exit(1)

    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass

    cfg, intf = find_iface_with_eps(dev)
    if cfg is None:
        log_line("[ERR] Vendor interface (0x03/0x83) not found; check driver binding (WinUSB/Zadig)")
        sys.exit(2)

    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except Exception:
        pass

    usb.util.claim_interface(dev, intf.bInterfaceNumber)

    # Optional upfront STAT snapshot (debug aid)
    if USE_CTRL_STATUS:
        st = get_status_ctrl(dev)
        if st:
            log_line(f"[HOST_STAT0] ver={st['version']} flags=0x{st['flags_runtime']:04X} lastTX={st.get('last_tx_len',0)} cur_seq={st.get('cur_stream_seq',0)}")

    # START
    dev.write(OUT_EP, bytes([0x20]), timeout=800)
    log_line("[HOST] START written")

    # Read until we see a first A and B; in parallel, test mid-stream GET_STATUS
    seen_A = False
    seen_B = False
    queued_status = False
    rx = bytearray()
    status_after_get = 0
    t_start = time.time()

    while True:
        # Issue exactly one GET_STATUS once A was seen but before B to try landing strictly between
        if seen_A and not seen_B and not queued_status:
            queue_status_bulk(dev)
            queued_status = True

        try:
            chunk = bytes(dev.read(IN_EP, 512, timeout=READ_TIMEOUT_MS))
            rx += chunk
        except usb.core.USBError as e:
            msg = str(e).lower()
            if (getattr(e, 'errno', None) in (10060, 110, 60)) or ('timeout' in msg):
                log_line("[HOST_RX] timeout (waiting initial A/B)")
                if time.time() - t_start > 8.0:
                    log_line("[ERR] Did not receive initial frames in time")
                    break
                continue
            log_line(f"[HOST_RX][ERR] {e}")
            break

        # Reassemble frames
        while True:
            if len(rx) < 4:
                break
            if rx[:4] == b'STAT':
                flen = 64 if len(rx) >= 64 else (52 if len(rx) >= 52 else 0)
                if flen == 0 or len(rx) < flen:
                    break
                frame = bytes(rx[:flen]); rx = rx[flen:]
                log_line(f"[HOST_RX] STAT len={len(frame)}")
                if queued_status and not seen_B:
                    status_after_get += 1
                continue
            if rx[0:3] == b"\x5A\xA5\x01" and len(rx) >= 16:
                total_samples = rx[12] | (rx[13] << 8)
                flen = 32 + total_samples * 2
                if len(rx) < flen:
                    break
                flags = rx[3]
                ftype = 'TEST' if (flags & 0x80) else ('A' if flags == 0x01 else ('B' if flags == 0x02 else 'UNK'))
                frame = bytes(rx[:flen]); rx = rx[flen:]
                log_line(f"[HOST_RX] {ftype} len={len(frame)}")
                if ftype == 'A':
                    seen_A = True
                elif ftype == 'B':
                    seen_B = True
                # Exit to STOP once we have A and B and validated GET_STATUS yielded exactly one STAT
                if seen_A and seen_B:
                    break
                continue
            # resync
            idx_stat = rx.find(b'STAT')
            idx_hdr = rx.find(b"\x5A\xA5\x01")
            idx = min([i for i in (idx_stat, idx_hdr) if i != -1], default=-1)
            if idx > 0:
                rx = rx[idx:]
                continue
            break
        if seen_A and seen_B:
            break

    # Validate GET_STATUS gating
    if queued_status:
        if status_after_get == 1:
            log_line("[CHECK][GET_STATUS] PASS: exactly one STAT between A and B")
        else:
            log_line(f"[CHECK][GET_STATUS] FAIL: expected 1 STAT, got {status_after_get}")
    else:
        log_line("[CHECK][GET_STATUS] SKIP: did not queue mid-stream GET_STATUS")

    # Issue STOP and expect STAT (ACK-STOP), then quiet
    t_stop_write = time.time()
    dev.write(OUT_EP, bytes([0x21]), timeout=800)
    log_line("[HOST] STOP written")

    ack_seen = False
    t_ack = None
    rx2 = bytearray()
    t_ack_deadline = time.time() + 1.5  # allow up to 1.5s for the ACK-STAT
    while time.time() < t_ack_deadline and not ack_seen:
        try:
            chunk = bytes(dev.read(IN_EP, 512, timeout=READ_TIMEOUT_MS))
            rx2 += chunk
        except usb.core.USBError as e:
            msg = str(e).lower()
            if (getattr(e, 'errno', None) in (10060, 110, 60)) or ('timeout' in msg):
                continue
            log_line(f"[HOST_RX][ERR] {e}")
            break
        while True:
            if len(rx2) < 4:
                break
            # STAT?
            if rx2[:4] == b'STAT':
                flen = 64 if len(rx2) >= 64 else (52 if len(rx2) >= 52 else 0)
                if flen == 0 or len(rx2) < flen:
                    break
                rx2 = rx2[flen:]
                ack_seen = True
                t_ack = time.time()
                log_line("[HOST_RX] STOP-ACK: STAT received")
                break
            # A/B/TEST frame?
            if rx2[0:3] == b"\x5A\xA5\x01" and len(rx2) >= 16:
                total_samples = rx2[12] | (rx2[13] << 8)
                flen = 32 + total_samples * 2
                if len(rx2) < flen:
                    break
                flags = rx2[3]
                ftype = 'TEST' if (flags & 0x80) else ('A' if flags == 0x01 else ('B' if flags == 0x02 else 'UNK'))
                rx2 = rx2[flen:]
                log_line(f"[HOST_RX] (post-STOP) skip {ftype}")
                continue
            # resync to next plausible header
            idx_stat = rx2.find(b'STAT')
            idx_hdr = rx2.find(b"\x5A\xA5\x01")
            idx = min([i for i in (idx_stat, idx_hdr) if i != -1], default=-1)
            if idx > 0:
                rx2 = rx2[idx:]
                continue
            break

    if not ack_seen:
        log_line("[CHECK][STOP] FAIL: no STAT after STOP within 1.5s")
    else:
        dt_ms = int((t_ack - t_stop_write) * 1000)
        log_line(f"[CHECK][STOP] ACK-STAT latency: ~{dt_ms} ms")

    # After ACK-STAT, probe for any extra frames for a short window; expect silence/timeouts
    t_end = time.time() + (WAIT_AFTER_STOP_MS / 1000.0)
    extra_frames = 0
    while time.time() < t_end:
        try:
            _ = bytes(dev.read(IN_EP, 512, timeout=READ_TIMEOUT_MS))
            extra_frames += 1
        except usb.core.USBError as e:
            msg = str(e).lower()
            if (getattr(e, 'errno', None) in (10060, 110, 60)) or ('timeout' in msg):
                continue
            break
    if ack_seen and extra_frames == 0:
        log_line("[CHECK][STOP] PASS: no frames after ACK-STAT (stream halted)")
    elif ack_seen:
        log_line(f"[CHECK][STOP] WARN: {extra_frames} frame(s) seen after ACK-STAT window")

    try:
        usb.util.release_interface(dev, intf.bInterfaceNumber)
    except Exception:
        pass
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass


if __name__ == '__main__':
    main()
