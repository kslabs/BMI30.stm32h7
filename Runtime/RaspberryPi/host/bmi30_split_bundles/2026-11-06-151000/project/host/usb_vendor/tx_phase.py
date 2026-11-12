"""Firmware 1.2.46 TX1/TX2 phase control using the owner's existing USB handle.

Wire contract: host/TX_PHASE_CONTROL_RU.md. Phase values describe transmitter
activity, not GPIO voltage or ADC channel polarity.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from .capabilities import for_stream, FeatureUnavailable


SET_TX1_PHASE = 0x47
SET_TX2_PHASE = 0x48
GET_TX_PHASE = 0x49
_CONFIG_LOCK = threading.RLock()


def _bit(value):
    if type(value) is not int or value not in (0, 1):
        raise ValueError("TX phase must be 0 (0°) or 1 (180°)")
    return value


def parse_tx_phase(raw):
    raw = bytes(raw)
    if len(raw) != 16 or raw[:5] != b"TXP1\x01":
        raise RuntimeError("TX phase control requires STM32 firmware 1.2.46+ (TXP1 v1)")
    if any(value > 1 for value in raw[5:9] + raw[10:14]):
        raise RuntimeError("Invalid TXP1 phase or GPIO value")
    return {
        "requested": list(raw[5:7]), "applied": list(raw[7:9]),
        "pending": bool(raw[9] & 8), "tx_requested": bool(raw[9] & 1),
        "tx_enabled": bool(raw[9] & 2), "streaming": bool(raw[9] & 4),
        "sync_phase": raw[10], "pa1": raw[11], "pa2": raw[12], "pc7": raw[13],
    }


class TxPhaseControl:
    def __init__(self, stream, config_path=None):
        self.stream = stream
        self.config_path = Path(config_path) if config_path else None
        self.serial = None

    def _identity(self):
        identity = (id(self.stream.dev), getattr(self.stream, 'connected_t', None))
        if getattr(self, '_identity_generation', None) != identity:
            self.serial = None
            self._identity_generation = identity
        if self.serial is None:
            # Read the descriptor only under the same EP0 lock as other control
            # transfers. Never key persistent phases by a changing USB address.
            self.serial = str(self.stream.dev.serial_number or "").strip()
        return self.serial

    def _config(self):
        if self.config_path is None:
            return {}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Invalid BMI30 configuration: JSON object expected")
        return data

    def _saved(self):
        with _CONFIG_LOCK:
            records = self._config().get("tx_phase_by_usb_serial", {})
            if not isinstance(records, dict):
                raise ValueError("Invalid saved TX phase configuration")
            saved = records.get(self._identity())
            if saved is None:
                return None
            if not isinstance(saved, list) or len(saved) != 2:
                raise ValueError("Invalid saved TX phase pair")
            return [_bit(value) for value in saved]

    def _read(self):
        if not getattr(self.stream, "_running", True) or getattr(self.stream, "disconnected", False):
            raise RuntimeError("USB is not connected")
        try:
            state = for_stream(self.stream).read("tx_phase", parse_tx_phase, 0xC0, GET_TX_PHASE, 0, 0, 16, timeout=300)
        except FeatureUnavailable:
            raise
        except Exception as exc:
            if getattr(exc, "errno", None) == 32:
                raise RuntimeError("TX phase control is unavailable; STM32 firmware 1.2.46+ is required") from exc
            raise RuntimeError(f"Unable to read TX phases from STM32: {exc}") from exc
        state.update(available=True, supported=True)
        state["usb_serial"] = self._identity()
        state["saved"] = self._saved()
        return state

    def read(self):
        with self.stream._ep0_lock:
            return self._read()

    def _apply(self, expected, state):
        for index, command in enumerate((SET_TX1_PHASE, SET_TX2_PHASE)):
            if state["requested"][index] != expected[index]:
                for_stream(self.stream).transfer("tx_phase", 0x40, command, expected[index], 0, None, timeout=300)
        deadline = time.monotonic() + 1.0
        while True:
            state = self._read()
            if state["requested"] == expected and state["applied"] == expected and not state["pending"]:
                return state
            if time.monotonic() >= deadline:
                raise RuntimeError("STM32 has not confirmed the requested TX phases")
            time.sleep(0.01)

    def _save(self, phases):
        with _CONFIG_LOCK:
            data = self._config()
            records = data.setdefault("tx_phase_by_usb_serial", {})
            records[self._identity()] = list(phases)
            fd, name = tempfile.mkstemp(prefix=".tx-phase-", dir=self.config_path.parent)
            try:
                if self.config_path.exists():
                    os.fchmod(fd, self.config_path.stat().st_mode & 0o777)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, self.config_path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)

    def set_phase(self, channel, phase, persist=True):
        if type(channel) is not int or channel not in (1, 2):
            raise ValueError("TX channel must be 1 or 2")
        phase = _bit(phase)
        with self.stream._ep0_lock:
            state = self._read()  # Capability and config validation before SET.
            if persist and (self.config_path is None or not state["usb_serial"]):
                raise RuntimeError("A USB serial number and config path are required to save TX phases")
            expected = state["requested"][:]
            expected[channel - 1] = phase
            state = self._apply(expected, state)
            if persist:
                self._save(expected)
                state["saved"] = expected[:]
            return state

    def restore(self, before_start=False):
        with self.stream._ep0_lock:
            expected = self._saved()
            if before_start and expected is None:
                return None  # Existing firmware/start behavior without saved phases.
            try:
                state = self._read()
                return self._apply(expected, state) if expected is not None else state
            except FeatureUnavailable:
                # Saved preferences survive. An absent optional feature must
                # never prevent START_STREAM on an older STM32.
                return None
