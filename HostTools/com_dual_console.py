#!/usr/bin/env python3
"""Dual COM console/logger for BMI30 ST-LINK UART ports."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import queue
import sys
import threading
import time
from pathlib import Path

try:
    import serial
except ImportError as exc:
    print("pyserial is required: pip install pyserial", file=sys.stderr)
    raise SystemExit(2) from exc


DEFAULT_PORTS = ("COM18", "COM22")
DEFAULT_BAUD = 115200


class PortWorker:
    def __init__(self, name: str, baud: int, log_dir: Path, stop_event: threading.Event):
        self.name = name.upper()
        self.baud = baud
        self.log_dir = log_dir
        self.stop_event = stop_event
        self.tx_queue: queue.Queue[bytes] = queue.Queue()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"com-{self.name}", daemon=True)
        self.log_path = log_dir / f"{self.name}.log"

    def start(self) -> None:
        self.thread.start()

    def send_line(self, text: str) -> None:
        payload = text.encode("ascii", errors="replace") + b"\r\n"
        self.tx_queue.put(payload)

    def _emit(self, message: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{stamp}][{self.name}] {message}"
        print(line, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8", errors="replace") as log_file:
                log_file.write(line + "\n")
        except OSError:
            pass

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                with serial.Serial(self.name, self.baud, timeout=0.05, write_timeout=1.0) as port:
                    port.reset_input_buffer()
                    self.ready.set()
                    self._emit(f"opened @ {self.baud} 8N1")
                    rx = bytearray()
                    while not self.stop_event.is_set():
                        self._drain_tx(port)
                        data = port.read(256)
                        if data:
                            for byte in data:
                                if byte in (10, 13):
                                    if rx:
                                        self._emit(rx.decode("utf-8", errors="replace"))
                                        rx.clear()
                                else:
                                    rx.append(byte)
                                    if len(rx) >= 512:
                                        self._emit(rx.decode("utf-8", errors="replace"))
                                        rx.clear()
                        else:
                            time.sleep(0.01)
                    if rx:
                        self._emit(rx.decode("utf-8", errors="replace"))
            except serial.SerialException as exc:
                self.ready.clear()
                self._emit(f"not ready: {exc}")
                time.sleep(1.0)
            except OSError as exc:
                self.ready.clear()
                self._emit(f"I/O error: {exc}")
                time.sleep(1.0)

    def _drain_tx(self, port: serial.Serial) -> None:
        while True:
            try:
                payload = self.tx_queue.get_nowait()
            except queue.Empty:
                return
            port.write(payload)
            port.flush()
            shown = payload.rstrip(b"\r\n").decode("ascii", errors="replace")
            self._emit(f">>> {shown}")


def parse_targeted_command(raw: str, ports: dict[str, PortWorker]) -> tuple[list[PortWorker], str] | None:
    text = raw.strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    head = parts[0].upper()
    rest = parts[1] if len(parts) > 1 else ""

    aliases = {
        "18": "COM18",
        "22": "COM22",
        "COM18": "COM18",
        "COM22": "COM22",
        "1": "COM18",
        "2": "COM22",
    }
    if head in ("BOTH", "ALL"):
        return list(ports.values()), rest
    if head in aliases:
        port_name = aliases[head]
        if port_name in ports:
            return [ports[port_name]], rest
    return list(ports.values()), text


def main() -> int:
    parser = argparse.ArgumentParser(description="Open COM18/COM22, log output, and send text commands.")
    parser.add_argument("--ports", nargs="+", default=list(DEFAULT_PORTS), help="COM ports to open")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--log-dir", default=None, help="Directory for per-port logs")
    parser.add_argument("--send", action="append", default=[], help="Command to send after opening; repeatable")
    parser.add_argument("--listen-secs", type=float, default=0.0, help="Non-interactive listen duration after --send")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    if args.log_dir:
        log_dir = Path(args.log_dir)
    else:
        stamp = dt.datetime.now().strftime("com_%Y%m%d_%H%M%S")
        log_dir = root / "test_logs" / stamp
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"[COM] Logs: {log_dir}", flush=True)

    stop_event = threading.Event()
    workers = {name.upper(): PortWorker(name, args.baud, log_dir, stop_event) for name in args.ports}
    for worker in workers.values():
        worker.start()

    time.sleep(0.5)
    for command in args.send:
        parsed = parse_targeted_command(command, workers)
        if parsed is None:
            continue
        targets, payload = parsed
        if not payload:
            continue
        for target in targets:
            target.send_line(payload)

    if args.listen_secs > 0:
        deadline = time.time() + args.listen_secs
        try:
            while time.time() < deadline:
                time.sleep(0.1)
        finally:
            stop_event.set()
        return 0

    print("[COM] Input examples: VER | DC | STATUS | 18 VER | 22 DC | both RS485 | quit", flush=True)
    try:
        while True:
            raw = input("com> ")
            if raw.strip().lower() in ("q", "quit", "exit"):
                break
            parsed = parse_targeted_command(raw, workers)
            if parsed is None:
                continue
            targets, payload = parsed
            if not payload:
                continue
            for target in targets:
                target.send_line(payload)
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        stop_event.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
