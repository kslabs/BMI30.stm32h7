"""USB/direct UART settings transport for autonomous BMI30 XM125 firmware.

No presence decision or GPIO switching runs on the Raspberry Pi. The module
owns only its explicitly selected serial port and polls diagnostic readback.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import threading
import time

DEVICE = "BMI30_XM125_GATE"
CONFIG_KEYS = ("trigger_delta_mm", "release_delta_mm", "on_delay_ms", "off_delay_ms",
               "clear_delay_ms", "adaptation_ms", "rate_hz", "no_echo_ms", "fault_high",
               "sensitivity_milli", "signal_quality_db")


class RadarError(RuntimeError):
    pass


def ports():
    from serial.tools import list_ports
    return [{"port": p.device, "description": p.description, "hwid": p.hwid}
            for p in list_ports.comports()
            if (p.vid, p.pid) == (0x1A86, 0x7523)]


def validate_port(port):
    if not isinstance(port, str) or not (
            re.fullmatch(r"COM[1-9][0-9]*", port, re.I) or
            re.fullmatch(r"/dev/(?:tty(?:USB|ACM|AMA|S)[0-9]+|serial[0-9]+|serial/by-(?:id|path)/[^/\s]+)", port)):
        raise RadarError("Choose a local USB/UART device path")
    return port


class RadarClient:
    def __init__(self, port, timeout=3.0, serial_factory=None):
        validate_port(port)
        if serial_factory is None:
            import serial
            serial_factory = serial.Serial
        self.port = port
        self.timeout = timeout
        self.serial = serial_factory(port=port, baudrate=115200, timeout=0.15,
                                     write_timeout=1, rtscts=False, dsrdtr=False)
        self.lock = threading.RLock()
        try:
            self.serial.reset_input_buffer()
            self.serial.write(b"STREAM 0\n")
            # Drain any previously enabled diagnostic stream before identity.
            time.sleep(0.2)
            self.serial.reset_input_buffer()
            state = self.request("ID")
            if state.get("device") != DEVICE:
                raise RadarError("The selected port is not running BMI30 XM125 gate firmware")
        except Exception:
            self.close()
            raise

    def close(self):
        self.serial.close()

    def request(self, command):
        if "\n" in command or "\r" in command or len(command) >= 95:
            raise RadarError("Invalid command framing")
        with self.lock:
            self.serial.write((command + "\n").encode("ascii"))
            end = time.monotonic() + self.timeout
            while time.monotonic() < end:
                raw = self.serial.readline(1025)
                if not raw:
                    continue
                try:
                    reply = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(reply, dict) or "ok" not in reply:
                    continue
                if not reply["ok"]:
                    raise RadarError(str(reply.get("error", "Firmware rejected the command")))
                return reply
            raise RadarError("XM125 response timed out")

    def status(self):
        state = self.request("GET")
        if state.get("device") != DEVICE:
            raise RadarError("Unexpected serial device identity")
        return state

    def apply(self, values):
        if not isinstance(values, dict) or set(values) - set(CONFIG_KEYS):
            raise RadarError("Unknown radar setting")
        with self.lock:
            config = self.status()["config"].copy()
            for key, value in values.items():
                if type(value) is not int or value < 0 or value > 4294967295:
                    raise RadarError("Settings must be nonnegative integers")
                config[key] = value
            reply = self.request("CONFIG " + " ".join(str(config[k]) for k in CONFIG_KEYS))
            confirmed = self.status()
            if any(confirmed["config"].get(k) != config[k] for k in CONFIG_KEYS):
                raise RadarError("Radar configuration readback mismatch")
            return confirmed


class RadarGateService:
    def __init__(self, config_path=None):
        self.lock = threading.RLock()
        self.client = None
        self.selected_port = ""
        self.config_path = Path(config_path) if config_path else None
        if self.config_path and self.config_path.is_file():
            try:
                self.selected_port = validate_port(json.loads(self.config_path.read_text())["port"])
            except (OSError, ValueError, KeyError, RadarError):
                pass

    def _remember_port(self, port):
        if not self.config_path:
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config_path.with_name(self.config_path.name + ".new")
        temp.write_text(json.dumps({"port": port, "baud": 115200}) + "\n", encoding="utf-8")
        temp.chmod(0o600)
        os.replace(temp, self.config_path)

    def close(self):
        with self.lock:
            if self.client:
                self.client.close()
                self.client = None

    def snapshot(self):
        with self.lock:
            result = {"ok": True, "connected": self.client is not None,
                      "port": self.selected_port, "ports": ports()}
            if self.client:
                try:
                    result["radar"] = self.client.status()
                except Exception as exc:
                    self.close()
                    result.update(ok=False, connected=False, error=str(exc))
            return result

    def command(self, body):
        if not isinstance(body, dict):
            return {"ok": False, "error": "JSON object required"}
        with self.lock:
            try:
                action = body.get("action", "status")
                if action == "status":
                    return self.snapshot()
                if action == "disconnect":
                    self.close()
                    return self.snapshot()
                if action == "connect":
                    port = validate_port(body.get("port", self.selected_port))
                    self.close()
                    self.client = RadarClient(port)
                    self.selected_port = port
                    self._remember_port(port)
                    return self.snapshot()
                if not self.client:
                    raise RadarError("Connect the XM125 first")
                if action == "apply":
                    self.client.apply(body.get("settings"))
                elif action == "calibrate":
                    self.client.request("CALIBRATE")
                elif action == "gap":
                    value = body.get("gap_mm")
                    if type(value) is not int or not 600 <= value <= 1800:
                        raise RadarError("Gap must be 600..1800 mm")
                    self.client.request("GAP " + str(value))
                elif action == "save":
                    self.client.request("SAVE")
                elif action == "load":
                    self.client.request("LOAD")
                else:
                    raise RadarError("Unknown action")
                return self.snapshot()
            except Exception as exc:
                return {"ok": False, "connected": self.client is not None,
                        "port": self.selected_port, "error": str(exc)}


def main():
    p = argparse.ArgumentParser(description="Configure autonomous XM125 gate firmware")
    p.add_argument("--port")
    p.add_argument("--list", action="store_true")
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--gap-mm", type=int)
    p.add_argument("--save", action="store_true")
    p.add_argument("--set", action="append", default=[], metavar="KEY=INTEGER")
    p.add_argument("--watch", type=int, default=0, metavar="SECONDS")
    args = p.parse_args()
    if args.list:
        print(json.dumps(ports(), indent=2))
        return
    if not args.port:
        p.error("--port is required")
    service = RadarGateService()
    try:
        result = service.command({"action": "connect", "port": args.port})
        if not result["ok"]:
            raise RadarError(result["error"])
        actions = []
        if args.set:
            actions.append({"action": "apply", "settings": {k: int(v) for k, v in (s.split("=", 1) for s in args.set)}})
        if args.gap_mm is not None:
            actions.append({"action": "gap", "gap_mm": args.gap_mm})
        if args.calibrate:
            actions.append({"action": "calibrate"})
        if args.save:
            actions.append({"action": "save"})
        for action in actions:
            result = service.command(action)
            if not result["ok"]:
                raise RadarError(result["error"])
        print(json.dumps(result, indent=2))
        end = time.monotonic() + args.watch
        while time.monotonic() < end:
            print(json.dumps(service.snapshot()))
            time.sleep(0.5)
    finally:
        service.close()


if __name__ == "__main__":
    main()
