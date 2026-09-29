#!/usr/bin/env python3
"""Read-only concurrent UART monitor. Exit: 0 pass, 1 sync failure, 2 incomplete.

Only VER and SYNCSTATE are sent. Each port stays open throughout the run.
Example: python HostTools/monitor_rs485_stability.py COM8 COM11 COM12 COM15
    --duration 180 --interval 2 --expected-nodes 4 --expected-mask 0xF
    --output Logs/rs485-stability.jsonl
"""

import argparse
import concurrent.futures
import json
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import serial


KEY_VALUE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
INTEGER = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|\d+)\Z")
COUNTERS = ("phase_flip", "phase_restart", "uart_err", "uart_pe", "uart_fe",
            "uart_ne", "uart_ore", "sync_err", "phase_filter_drop", "sync_reject")
REQUIRED = {"raw", "node", "sync_alive", "phase_rel", "phase_flip",
            "phase_restart", "sample", "bufs_per_sync"}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_fields(line):
    """Retain every field; convert signed/hex integers, preserve a/b strings."""
    values = {}
    for key, value in KEY_VALUE.findall(line):
        if INTEGER.fullmatch(value):
            value = int(value, 16 if "x" in value.lower() else 10)
        values[key] = value
    return values


def parse_syncstate(text):
    for line in reversed(text.splitlines(keepends=True)):
        if "SYNC_STATE " in line and line.endswith(("\r", "\n")):
            fields = parse_fields(line.split("SYNC_STATE ", 1)[1])
            if REQUIRED.issubset(fields):
                return fields
    return None


class Recorder:
    def __init__(self, path):
        self.stream = path.open("w", encoding="utf-8")
        self.lock = threading.Lock()

    def write(self, **event):
        with self.lock:
            self.stream.write(json.dumps({"timestamp": utc_now(), **event}) + "\n")
            self.stream.flush()


def command(device, port, name, args, recorder):
    """Retry on the same handle and keep raw responses, including partial ones."""
    for attempt in range(args.retries + 1):
        started = time.monotonic()
        data = bytearray()
        fields = None
        error = None
        try:
            device.reset_input_buffer()
            device.write((name + "\r\n").encode("ascii"))
            device.flush()
            deadline = started + args.timeout
            while time.monotonic() < deadline:
                data.extend(device.read(max(1, device.in_waiting)))
                raw = data.decode("utf-8", errors="replace")
                if name == "SYNCSTATE":
                    fields = parse_syncstate(raw)
                else:
                    lines = [line for line in raw.splitlines(keepends=True)
                             if "VERSION " in line and line.endswith(("\r", "\n"))]
                    if lines:
                        fields = parse_fields(lines[-1].split("VERSION ", 1)[1])
                if fields is not None:
                    break
        except (OSError, serial.SerialException) as exc:
            error = str(exc)
        recorder.write(port=port, command=name, attempt=attempt + 1,
                       elapsed_seconds=round(time.monotonic() - started, 6),
                       raw=data.decode("utf-8", errors="replace"), fields=fields,
                       transport_error=error,
                       transport_miss=(fields is None))
        if fields is not None:
            return fields, attempt
        if attempt < args.retries:
            time.sleep(0.05)
    return None, args.retries + 1


def open_session(port, args, recorder):
    device = None
    try:
        device = serial.Serial(port, 115200, timeout=0.025, write_timeout=0.5)
        time.sleep(0.05)
        version, retries = command(device, port, "VER", args, recorder)
        return {"port": port, "device": device, "version": version,
                "startup_missed_attempts": retries, "open_error": None}
    except (OSError, serial.SerialException) as exc:
        if device is not None:
            device.close()
        recorder.write(port=port, command="OPEN", transport_error=str(exc))
        return {"port": port, "device": None, "version": None,
                "startup_missed_attempts": 0, "open_error": str(exc)}


def summarize(rows, misses, expected_nodes=None, expected_mask=None):
    result = {"readings": len(rows), "transport_missed_polls": misses,
              "sync_failures": {}, "changes": {}, "values_seen": {},
              "counter_deltas": {}, "counter_resets": {}, "ranges": {},
              "phase_locked_counts": {}, "slave_phase_locked_counts": {}}
    if not rows:
        result["sync_failures"] = {"no_readings": 1}
        return result
    failures = Counter()
    for key in ("raw", "node", "sync_seen_mask", "active_status_count"):
        vals = [row[key] for row in rows if key in row]
        result["values_seen"][key] = sorted(set(vals))
        result["changes"][key] = sum(a != b for a, b in zip(vals, vals[1:]))
    for key in COUNTERS:
        vals = [row[key] for row in rows if isinstance(row.get(key), int)]
        if not vals:
            continue
        delta, resets = 0, 0
        for before, after in zip(vals, vals[1:]):
            if after >= before:
                delta += after - before
            elif before >= 0xF0000000 and after < 0x10000000:
                delta += (after - before) & 0xFFFFFFFF
            else:
                resets += 1
        result["counter_deltas"][key] = delta
        result["counter_resets"][key] = resets
        if resets:
            failures["counter_reset:" + key] = resets
        if key in ("phase_flip", "phase_restart", "sync_err") and delta:
            failures["counter_increase:" + key] = delta
    for key in ("phase_err", "phase_ctrl", "period", "bufs_per_sync"):
        vals = [row[key] for row in rows if isinstance(row.get(key), int)]
        if vals:
            result["ranges"][key] = {"min": min(vals), "max": max(vals)}
    samples = []
    sample_counts = set()
    for row in rows:
        pair = re.fullmatch(r"(\d+)/(\d+)", str(row.get("sample", "")))
        if pair:
            samples.append(int(pair[1]))
            sample_counts.add(int(pair[2]))
        if row.get("sync_alive") != 1:
            failures["sync_not_alive"] += 1
        if row.get("raw") not in (0, 1):
            failures["invalid_role"] += 1
        if row.get("raw") == 1 and row.get("phase_rel") != 1:
            failures["slave_not_in_phase"] += 1
        if row.get("id_conflicts", 0):
            failures["id_conflicts"] += 1
        if row.get("multiple_master", 0):
            failures["multiple_master"] += 1
        if expected_nodes is not None and row.get("active_status_count") != expected_nodes:
            failures["unexpected_node_count"] += 1
        if expected_mask is not None and row.get("sync_seen_mask") != expected_mask:
            failures["unexpected_node_mask"] += 1
    for key in ("raw", "node"):
        if result["changes"][key]:
            failures["changed:" + key] = result["changes"][key]
    if samples:
        result["ranges"]["sample"] = {"min": min(samples), "max": max(samples)}
    result["sample_counts"] = sorted(sample_counts)
    result["phase_locked_counts"] = dict(Counter(str(row["phase_locked"])
        for row in rows if "phase_locked" in row))
    result["slave_phase_locked_counts"] = dict(Counter(str(row["phase_locked"])
        for row in rows if row.get("raw") == 1 and "phase_locked" in row))
    result["sync_failures"] = dict(failures)
    result["first"] = rows[0]
    result["last"] = rows[-1]
    return result


def monitor_session(session, args, recorder, started, stop):
    rows = []
    misses = 0
    missed_attempts = 0
    device = session.pop("device")
    next_poll = started
    try:
        while device is not None and time.monotonic() < started + args.duration and not stop.is_set():
            row, attempts = command(device, session["port"], "SYNCSTATE", args, recorder)
            missed_attempts += attempts
            if row is None:
                misses += 1
            else:
                rows.append(row)
            next_poll += args.interval
            # Do not issue a burst of catch-up commands after a slow response.
            next_poll = max(next_poll, time.monotonic())
            stop.wait(max(0, min(next_poll, started + args.duration) - time.monotonic()))
    finally:
        if device is not None:
            device.close()
    return {**session, **summarize(rows, misses, args.expected_nodes, args.expected_mask),
            "transport_missed_attempts": missed_attempts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ports", nargs="+")
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--expected-nodes", type=int)
    parser.add_argument("--expected-mask", type=lambda value: int(value, 0))
    parser.add_argument("--output", type=Path, default=Path("rs485-stability.jsonl"))
    args = parser.parse_args()
    if min(args.duration, args.interval, args.timeout) <= 0 or args.retries < 0:
        parser.error("duration, interval and timeout must be positive; retries nonnegative")
    if len({port.upper() for port in args.ports}) != len(args.ports):
        parser.error("ports must be unique")
    if args.expected_nodes is not None and not 1 <= args.expected_nodes <= 32:
        parser.error("expected-nodes must be 1..32")
    if args.expected_mask is not None and not 0 <= args.expected_mask <= 0xFFFFFFFF:
        parser.error("expected-mask must be a 32-bit unsigned integer")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary_path = args.output.with_suffix(".summary.json")
    if summary_path == args.output:
        summary_path = args.output.with_name(args.output.name + ".summary.json")
    recorder = Recorder(args.output)
    stop = threading.Event()
    interrupted = False
    timestamp = utc_now()
    results = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(args.ports)) as pool:
            sessions = list(pool.map(lambda port: open_session(port, args, recorder), args.ports))
            for session in sessions:
                print(f"{session['port']} VERSION {session['version']} open_error={session['open_error']}", flush=True)
            started = time.monotonic()
            futures = [pool.submit(monitor_session, session, args, recorder, started, stop)
                       for session in sessions]
            try:
                while not all(future.done() for future in futures):
                    concurrent.futures.wait(futures, timeout=min(30.0, args.duration))
                    print(f"MONITOR elapsed={time.monotonic() - started:.1f}s", flush=True)
            except KeyboardInterrupt:
                interrupted = True
                stop.set()
            results = [future.result() for future in futures]
            elapsed = time.monotonic() - started
    finally:
        recorder.stream.close()
    failures = any(result["sync_failures"] for result in results)
    incomplete = interrupted or any(result["transport_missed_polls"] or result["version"] is None
                                    for result in results)
    exit_code = 1 if failures else 2 if incomplete else 0
    summary = {"started": timestamp, "finished": utc_now(), "elapsed_seconds": elapsed,
               "requested_duration_seconds": args.duration, "interval_seconds": args.interval,
               "expected_nodes": args.expected_nodes, "expected_mask": args.expected_mask,
               "interrupted": interrupted, "exit_code": exit_code,
               "status": "FAIL" if failures else "INCOMPLETE" if incomplete else "PASS",
               "raw_log": str(args.output.resolve()), "ports": results,
               "interpretation": "phase_locked is instantaneous and counted, not a pass criterion; "
                   "UART transport misses are separate from observed RS485 sync failures; "
                   "polling cannot exclude failures between readings"}
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(f"SUMMARY {result['port']} readings={result['readings']} "
              f"misses={result['transport_missed_polls']} failures={result['sync_failures']} "
              f"counters={result['counter_deltas']} ranges={result['ranges']}", flush=True)
    print(f"{summary['status']} summary={summary_path} raw={args.output}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
