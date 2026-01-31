from __future__ import annotations

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
CMD_START_STREAM = 0x20
CMD_STOP_STREAM = 0x21
CMD_GET_STATUS = 0x30
CMD_SET_ALT = 0x31
CMD_SOFT_RESET = 0x7E
CMD_DEEP_RESET = 0x7F

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


class USBStream:
    """Minimal streaming helper used by HostTools/BMI30.200.py.

    Implements:
    - bulk OUT commands (send_cmd)
    - bulk IN frame reads (get_stereo)
    - EP0 vendor GET_STATUS (last_stat, _get_status_ep0)
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
        self.port_info = self.get_port_path_info() or {}

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

    def send_cmd(self, cmd: int, payload: bytes = b""):
        pkt = bytes([int(cmd) & 0xFF]) + (payload or b"")
        self.dev.write(self.ep_out, pkt, timeout=500)  # type: ignore[attr-defined]

    def set_alt(self, alt: int):
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

    def soft_reset(self):
        # vendor control OUT without data
        self.dev.ctrl_transfer(0x40, CMD_SOFT_RESET, 0, 0, None, timeout=400)  # type: ignore[attr-defined]

    def deep_reset(self):
        self.dev.ctrl_transfer(0x40, CMD_DEEP_RESET, 0, 0, None, timeout=500)  # type: ignore[attr-defined]

    def _get_status_ep0(self, length: int = 96, timeout_ms: int = 500) -> bytes:
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
            return bytes(data)
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
                continue

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
