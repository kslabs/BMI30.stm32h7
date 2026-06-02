from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass
from typing import Optional

try:
    import usb.core  # type: ignore
    import usb.util  # type: ignore
except Exception as e:  # pragma: no cover
    raise ImportError(
        "PyUSB is required for usb_vendor.usb_stream. Install: pip install pyusb"
    ) from e

# Public flag used by HostTools/BMI30.200.py to revive module after close()
running = True

# Device defaults (BMI30)
VID = 0xCAFE
PID = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83

# Frame format
MAGIC = 0xA55A
HDR_SIZE = 32

# Commands (must match firmware)
CMD_SET_WINDOWS = 0x10
CMD_BLOCK_HZ = 0x11
CMD_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14
CMD_SET_TRUNC_SAMPLES = 0x16
CMD_SET_FRAME_SAMPLES = 0x17
CMD_ASYNC = 0x18
CMD_CHMODE = 0x19
CMD_SET_STREAM_MODE = 0x1A
CMD_SET_DC_ADAPT = 0x1B
CMD_SET_BUF_RATE_FINE = 0x1C
CMD_SET_SYNC_MODE = 0x1D
CMD_SET_DC_CONFIG = 0x1F
CMD_START_STREAM = 0x20
CMD_STOP_STREAM = 0x21
CMD_GET_STATUS = 0x30
CMD_SET_OPTIC_POWER = 0x34
CMD_SET_OPTIC_HOLD = 0x39
CMD_GET_LCD_STATUS = 0x38
CMD_GET_DC_CONFIG = 0x3A
CMD_SET_LED_PATTERN = 0x3B
CMD_SET_ALT = 0x31
CMD_HOST_RX_ACK = 0x36
CMD_HOST_RX_CLEAR = 0x37
CMD_SOFT_RESET = 0x7E
CMD_DEEP_RESET = 0x7F

_CMD_NAMES = {
    CMD_SET_WINDOWS: "SET_WINDOWS",
    CMD_BLOCK_HZ: "BLOCK_HZ",
    CMD_FULL_MODE: "SET_FULL_MODE",
    CMD_SET_PROFILE: "SET_PROFILE",
    CMD_SET_TRUNC_SAMPLES: "SET_TRUNC_SAMPLES",
    CMD_SET_FRAME_SAMPLES: "SET_FRAME_SAMPLES",
    CMD_ASYNC: "SET_ASYNC",
    CMD_CHMODE: "SET_CHMODE",
    CMD_SET_STREAM_MODE: "SET_STREAM_MODE",
    CMD_SET_DC_ADAPT: "SET_DC_ADAPT",
    CMD_SET_BUF_RATE_FINE: "SET_BUF_RATE_FINE",
    CMD_SET_SYNC_MODE: "SET_SYNC_MODE",
    CMD_SET_DC_CONFIG: "SET_DC_CONFIG",
    CMD_START_STREAM: "START_STREAM",
    CMD_STOP_STREAM: "STOP_STREAM",
    CMD_GET_STATUS: "GET_STATUS",
    CMD_SET_ALT: "SET_ALT",
    CMD_SET_OPTIC_POWER: "SET_OPTIC_POWER",
    CMD_GET_LCD_STATUS: "GET_LCD_STATUS",
    CMD_SET_OPTIC_HOLD: "SET_OPTIC_HOLD",
    CMD_GET_DC_CONFIG: "GET_DC_CONFIG",
    CMD_SET_LED_PATTERN: "SET_LED_PATTERN",
    CMD_HOST_RX_ACK: "HOST_RX_ACK",
    CMD_HOST_RX_CLEAR: "HOST_RX_CLEAR",
    CMD_SOFT_RESET: "SOFT_RESET",
    CMD_DEEP_RESET: "DEEP_RESET",
}
_CMD_TRACE_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "HostTools", "usb_cmd_trace.log")
)


def _trace_cmd(cmd: int, payload: bytes) -> None:
    try:
        os.makedirs(os.path.dirname(_CMD_TRACE_FILE), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        frac_ms = int((time.time() % 1.0) * 1000.0)
        name = _CMD_NAMES.get(int(cmd) & 0xFF, "UNKNOWN")
        line = (
            f"{ts}.{frac_ms:03d} cmd=0x{int(cmd) & 0xFF:02X} name={name} "
            f"len={len(payload)} payload={payload.hex()}\n"
        )
        with open(_CMD_TRACE_FILE, "a", encoding="ascii") as f:
            f.write(line)
    except Exception:
        pass

DC_MODE_FREEZE = 0
DC_MODE_WORK = 1
DC_MODE_DETECT = 2
DC_MODE_BOOT_FAST = 3

# EP0 status request (vendor IN, recipient interface)
_BM_STATUS_IN = usb.util.build_request_type(
    usb.util.CTRL_IN,
    usb.util.CTRL_TYPE_VENDOR,
    usb.util.CTRL_RECIPIENT_INTERFACE,
)


@dataclass(frozen=True)
class Frame:
    payload: bytes
    seq: int
    timestamp: int
    flags: int
    reserved: int
    reserved2: int
    adc_id: int


@dataclass(frozen=True)
class DCConfig:
    mode: int
    flags: int
    work_settle_ms: int
    detect_settle_ms: int
    fast_settle_ms: int
    fast_duration_ms: int
    active_settle_ms: int
    mode_enter_ms: int
    fast_until_ms: int
    adapt_updates: int


def _parse_frame(buf: bytes) -> Optional[Frame]:
    if len(buf) < HDR_SIZE:
        return None
    try:
        (
            magic,
            ver,
            flags,
            seq,
            ts,
            total_samples,
            zone_cnt,
            zone_off,
            zone_len,
            reserved,
            reserved2,
            crc16,
        ) = struct.unpack_from("<HBBIIHHIIIHH", buf, 0)
    except Exception:
        return None

    if magic != MAGIC:
        return None

    payload_len = int(total_samples) * 2
    total_len = HDR_SIZE + payload_len
    if len(buf) < total_len:
        return None
    payload = bytes(buf[HDR_SIZE:total_len])

    # Map flags to channel id (best-effort)
    adc_id = -1
    if flags & 0x01:
        adc_id = 0
    elif flags & 0x02:
        adc_id = 1

    return Frame(
        payload=payload,
        seq=int(seq) & 0xFFFFFFFF,
        timestamp=int(ts) & 0xFFFFFFFF,
        flags=int(flags) & 0xFF,
        reserved=int(reserved) & 0xFFFFFFFF,
        reserved2=int(reserved2) & 0xFFFF,
        adc_id=adc_id,
    )


def _parse_dc_config(buf: bytes) -> Optional[DCConfig]:
    if len(buf) < 40 or not buf.startswith(b"DCCF"):
        return None
    try:
        (
            _sig,
            version,
            mode,
            flags,
            work_settle_ms,
            detect_settle_ms,
            fast_settle_ms,
            fast_duration_ms,
            active_settle_ms,
            mode_enter_ms,
            fast_until_ms,
            adapt_updates,
        ) = struct.unpack_from("<4sBBHIIIIIIII", buf, 0)
    except Exception:
        return None
    if int(version) != 1:
        return None
    return DCConfig(
        mode=int(mode),
        flags=int(flags),
        work_settle_ms=int(work_settle_ms),
        detect_settle_ms=int(detect_settle_ms),
        fast_settle_ms=int(fast_settle_ms),
        fast_duration_ms=int(fast_duration_ms),
        active_settle_ms=int(active_settle_ms),
        mode_enter_ms=int(mode_enter_ms),
        fast_until_ms=int(fast_until_ms),
        adapt_updates=int(adapt_updates),
    )


class USBStream:
    """Minimal streaming helper used by HostTools/BMI30.200.py.

    Implements:
    - bulk OUT commands (send_cmd)
    - bulk IN frame reads (get_stereo)
    - EP0 vendor GET_STATUS/GET_LCD_STATUS
    - EP0 vendor GET_DC_CONFIG and bulk SET_DC_CONFIG
    - soft/deep reset via control OUT
    - altsetting switching
    """

    def __init__(
        self,
        vid: int = VID,
        pid: int = PID,
        interface: int = INTERFACE,
        ep_in: int = EP_IN,
        ep_out: int = EP_OUT,
        read_size: int = 16384,

        # Compatibility kwargs expected by HostTools/BMI30.200.py
        profile: int = 2,
        full: bool = True,
        test_as_data: bool = False,
        frame_samples: Optional[int] = None,
        fast_mode: bool = True,
        assembler_independent: bool = False,
        rx_ack_interval: float = 0.0,
    ):
        global running
        running = True

        self.vid = int(vid)
        self.pid = int(pid)
        self.interface = int(interface)
        self.ep_in = int(ep_in)
        self.ep_out = int(ep_out)
        self.read_size = int(read_size)

        self.dev = usb.core.find(idVendor=self.vid, idProduct=self.pid, find_all=False)
        if self.dev is None:
            raise RuntimeError(f"Device {self.vid:04X}:{self.pid:04X} not found")

        # Best-effort setup: Windows stacks often already have cfg/alt selected.
        try:
            self.dev.set_configuration()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            usb.util.claim_interface(self.dev, self.interface)
        except Exception:
            pass
        try:
            self.dev.set_interface_altsetting(interface=self.interface, alternate_setting=1)  # type: ignore[attr-defined]
        except Exception:
            pass

        self.last_stat: Optional[bytes] = None
        self.last_lcd_status: Optional[bytes] = None
        self.last_rx_t = time.time()
        self.bytes = 0
        self.magic_bad = 0
        self.crc_bad = 0
        self.test_seen = 0
        self.disconnected = False
        self.port_info = self.get_port_path_info() or {}
        self._host_rx_frames_total = 0
        self._host_rx_ack_frames = 0
        self._host_rx_ack_last = 0.0
        self._host_rx_ack_interval = max(0.0, float(rx_ack_interval))

        class _AsmStub:
            def __init__(self, independent: bool):
                self.independent = bool(independent)

        # Optional advanced assembler (stubbed). GUI checks attributes defensively.
        self.asm = _AsmStub(bool(assembler_independent))

        # Apply basic configuration expected by host GUI.
        # Keep best-effort and avoid raising if firmware rejects something.
        try:
            self.send_cmd(CMD_FULL_MODE, b"\x01" if full else b"\x00")
        except Exception:
            pass
        try:
            self.send_cmd(CMD_SET_PROFILE, bytes([int(profile) & 0xFF]))
        except Exception:
            pass
        try:
            if frame_samples is not None and int(frame_samples) > 0:
                self.send_cmd(CMD_SET_FRAME_SAMPLES, int(frame_samples).to_bytes(2, "little", signed=False))
        except Exception:
            pass
        try:
            self.send_cmd(CMD_HOST_RX_CLEAR)
        except Exception:
            pass

    def close(self):
        global running
        running = False
        try:
            usb.util.release_interface(self.dev, self.interface)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass

    def send_cmd(self, cmd: int, payload: bytes = b"", timeout_ms: int = 500):
        payload = payload or b""
        _trace_cmd(cmd, payload)
        pkt = bytes([int(cmd) & 0xFF]) + payload
        self.dev.write(self.ep_out, pkt, timeout=int(timeout_ms))  # type: ignore[attr-defined]

    def _note_host_frame_rx(self) -> None:
        self._host_rx_frames_total = (self._host_rx_frames_total + 1) & 0xFFFFFFFF
        if self._host_rx_ack_interval <= 0.0:
            return
        now = time.time()
        if (
            self._host_rx_frames_total != self._host_rx_ack_frames
            and (now - self._host_rx_ack_last) >= self._host_rx_ack_interval
        ):
            try:
                self.send_cmd(CMD_HOST_RX_ACK, struct.pack("<I", self._host_rx_frames_total), timeout_ms=20)
                self._host_rx_ack_frames = self._host_rx_frames_total
                self._host_rx_ack_last = now
            except Exception:
                pass

    def set_alt(self, alt: int):
        _trace_cmd(CMD_SET_ALT, bytes([int(alt) & 0xFF]))
        self.dev.set_interface_altsetting(interface=self.interface, alternate_setting=int(alt))  # type: ignore[attr-defined]

    def set_block_rate(self, hz: int):
        hz_i = int(hz)
        if hz_i < 0:
            hz_i = 0
        if hz_i > 2000:
            hz_i = 2000
        self.send_cmd(CMD_BLOCK_HZ, int(hz_i).to_bytes(2, "little", signed=False))

    def set_buf_rate_fine(self, hz: int):
        """Тонкая настройка частоты буферов: 200-210 Гц с шагом 1 Гц.
        Для проверки влияния переходных процессов."""
        hz_i = int(hz)
        if hz_i < 200 or hz_i > 210:
            raise ValueError(f"buf_rate_fine must be 200..210 Hz, got {hz_i}")
        self.send_cmd(CMD_SET_BUF_RATE_FINE, int(hz_i).to_bytes(2, "little", signed=False))

    def set_optic_power(self, level: int):
        level_i = int(level)
        if level_i < 0:
            level_i = 0
        if level_i > 255:
            level_i = 255
        self.send_cmd(CMD_SET_OPTIC_POWER, bytes([level_i]))

    def set_optic_hold_seconds(self, seconds: float):
        hold_ds = int(round(float(seconds) * 10.0))
        if hold_ds < 0:
            hold_ds = 0
        if hold_ds > 600:
            hold_ds = 600
        self.send_cmd(CMD_SET_OPTIC_HOLD, hold_ds.to_bytes(2, "little", signed=False))

    def set_led_pattern(self, pattern_id: int):
        self.send_cmd(CMD_SET_LED_PATTERN, bytes([int(pattern_id) & 0xFF]))

    def set_dc_adapt(self, enabled: bool):
        """Quick DC learning toggle via CMD_SET_DC_ADAPT (0x1B).

        True  -> resume learning (ACTIVE)
        False -> freeze learning (FREEZE), DC subtraction still applies.
        """
        self.send_cmd(CMD_SET_DC_ADAPT, bytes([1 if bool(enabled) else 0]))

    def set_dc_mode(self, mode: int):
        """Set only the DC adaptation mode. Use DC_MODE_* constants."""
        self.send_cmd(CMD_SET_DC_CONFIG, struct.pack("<BB", 1, int(mode) & 0xFF))

    def set_dc_config_ms(
        self,
        mode: int,
        work_settle_ms: int = 900_000,
        detect_settle_ms: int = 60_000,
        fast_settle_ms: int = 5_000,
        fast_duration_ms: int = 30_000,
    ):
        """Configure DC adaptation time constants in milliseconds."""
        payload = struct.pack(
            "<BBHIIII",
            1,
            int(mode) & 0xFF,
            0,
            max(0, int(work_settle_ms)) & 0xFFFFFFFF,
            max(0, int(detect_settle_ms)) & 0xFFFFFFFF,
            max(0, int(fast_settle_ms)) & 0xFFFFFFFF,
            max(0, int(fast_duration_ms)) & 0xFFFFFFFF,
        )
        self.send_cmd(CMD_SET_DC_CONFIG, payload)

    def set_dc_config_seconds(
        self,
        mode: int,
        work_settle_s: float = 900.0,
        detect_settle_s: float = 60.0,
        fast_settle_s: float = 5.0,
        fast_duration_s: float = 30.0,
    ):
        """Configure DC adaptation time constants in seconds."""
        self.set_dc_config_ms(
            mode=mode,
            work_settle_ms=int(float(work_settle_s) * 1000.0),
            detect_settle_ms=int(float(detect_settle_s) * 1000.0),
            fast_settle_ms=int(float(fast_settle_s) * 1000.0),
            fast_duration_ms=int(float(fast_duration_s) * 1000.0),
        )

    def soft_reset(self):
        # vendor control OUT without data
        _trace_cmd(CMD_SOFT_RESET, b"")
        self.dev.ctrl_transfer(0x40, CMD_SOFT_RESET, 0, 0, None, timeout=400)  # type: ignore[attr-defined]

    def deep_reset(self):
        _trace_cmd(CMD_DEEP_RESET, b"")
        self.dev.ctrl_transfer(0x40, CMD_DEEP_RESET, 0, 0, None, timeout=500)  # type: ignore[attr-defined]

    def _get_status_ep0(self, length: int = 136, timeout_ms: int = 500) -> bytes:
        # Try interface index 2 then 0 (some firmware exposes status on IF#0 too)
        last_err = None
        for idx in (self.interface, 0):
            try:
                data = self.dev.ctrl_transfer(_BM_STATUS_IN, CMD_GET_STATUS, 0, int(idx), int(length), timeout=int(timeout_ms))  # type: ignore[attr-defined]
                ba = bytes(data)
                if ba:
                    self.last_stat = ba
                return ba
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"GET_STATUS EP0 failed: {last_err}")

    def _get_lcd_status_ep0(self, length: int = 24, timeout_ms: int = 500) -> bytes:
        last_err = None
        for idx in (self.interface, 0):
            try:
                data = self.dev.ctrl_transfer(_BM_STATUS_IN, CMD_GET_LCD_STATUS, 0, int(idx), int(length), timeout=int(timeout_ms))  # type: ignore[attr-defined]
                ba = bytes(data)
                if ba:
                    self.last_lcd_status = ba
                return ba
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"GET_LCD_STATUS EP0 failed: {last_err}")

    def get_dc_config(self, timeout_ms: int = 500) -> DCConfig:
        last_err = None
        for idx in (self.interface, 0):
            try:
                data = self.dev.ctrl_transfer(_BM_STATUS_IN, CMD_GET_DC_CONFIG, 0, int(idx), 40, timeout=int(timeout_ms))  # type: ignore[attr-defined]
                cfg = _parse_dc_config(bytes(data))
                if cfg is not None:
                    return cfg
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"GET_DC_CONFIG EP0 failed: {last_err}")

    def get_port_path_info(self) -> dict:
        # Best-effort. On Windows this may be empty/unsupported.
        info: dict = {}
        try:
            bus = getattr(self.dev, "bus", None)
            addr = getattr(self.dev, "address", None)
            if bus is not None:
                info["bus"] = int(bus)
            if addr is not None:
                info["address"] = int(addr)
        except Exception:
            pass
        try:
            # libusb backend exposes port numbers sometimes
            pnums = getattr(self.dev, "port_numbers", None)
            if pnums:
                info["port_numbers"] = list(pnums)
        except Exception:
            pass
        return info

    def _read_once(self, timeout_s: float) -> Optional[bytes]:
        to_ms = max(1, int(float(timeout_s) * 1000.0))
        try:
            data = self.dev.read(self.ep_in, self.read_size, timeout=to_ms)  # type: ignore[attr-defined]
            raw = bytes(data)
            if raw:
                self.last_rx_t = time.time()
                self.bytes = (int(self.bytes) + len(raw)) & 0xFFFFFFFF
            return raw
        except Exception:
            return None

    def get_stereo(self, timeout: float = 0.1):
        """Read until we have (A,B) frames or until timeout.

        Returns:
        - (frameA, frameB) where frameA is ADC0 (flags&1) and frameB is ADC1 (flags&2)
        - (frameA, None) or (None, frameB) if only one channel arrives within timeout
        - None if nothing was read
        """
        deadline = time.time() + float(timeout)
        a: Optional[Frame] = None
        b: Optional[Frame] = None
        got_any = False

        while time.time() < deadline:
            remaining = max(0.001, deadline - time.time())
            raw = self._read_once(remaining)
            if not raw:
                continue
            got_any = True

            # STAT frames: store and keep reading
            if raw.startswith(b"STAT"):
                self.last_stat = raw
                continue

            fr = _parse_frame(raw)
            if fr is None:
                self.magic_bad = (int(self.magic_bad) + 1) & 0xFFFFFFFF
                continue
            if fr.flags & 0x80:
                self.test_seen = (int(self.test_seen) + 1) & 0xFFFFFFFF
                continue
            self._note_host_frame_rx()

            if fr.adc_id == 0:
                a = fr
            elif fr.adc_id == 1:
                b = fr
            else:
                # Unknown mapping; fill first empty slot
                if a is None:
                    a = fr
                elif b is None:
                    b = fr

            if a is not None and b is not None:
                return (a, b)

        if not got_any:
            return None
        if a is not None or b is not None:
            return (a, b)
        return None
