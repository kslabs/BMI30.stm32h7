#!/usr/bin/env python3
"""Measure phase-loop activity through the existing UART SYNCSTATE command."""

import argparse
import concurrent.futures
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import serial


PATTERNS = {
    "node": r"\bnode=(\d+)",
    "rel": r"\bphase_rel=(\d+)",
    "error": r"\bphase_err=(-?\d+)",
    "control": r"\bphase_ctrl=(-?\d+)",
    "pulse": r"\bphase_pulse=(-?\d+)",
    "updates": r"\bphase_updates=(\d+)/(\d+)",
    "skip": r"\bphase_skip=(\d+)/(\d+)",
    "slew": r"\bphase_slew=(\d+)",
    "restart": r"\bphase_restart=(\d+)",
    "bufs": r"\bbufs_per_sync=(\d+)",
    "sync_error": r"\bsync_err=(\d+)",
    "period": r"\bperiod=(\d+)",
}


@dataclass
class State:
    readings: int = 0
    misses: int = 0
    first: dict | None = None
    last: dict | None = None
    errors: list[int] = field(default_factory=list)
    controls: list[int] = field(default_factory=list)
    sampled_steps: list[int] = field(default_factory=list)
    pulse_values: Counter = field(default_factory=Counter)
    relation_bad: int = 0
    buffer_bad: int = 0


def parse_line(text: str) -> dict | None:
    lines = [line for line in text.splitlines() if "SYNC_STATE raw=" in line]
    if not lines:
        return None
    line = lines[-1]
    row = {}
    for name, pattern in PATTERNS.items():
        match = re.search(pattern, line)
        if match is None:
            return None
        if name == "updates":
            row["edges"] = int(match.group(1))
            row["applied"] = int(match.group(2))
        elif name == "skip":
            row["skip_busy"] = int(match.group(1))
            row["skip_spacing"] = int(match.group(2))
        else:
            row[name] = int(match.group(1))
    return row


def query(port: str, timeout: float, retries: int) -> dict | None:
    try:
        with serial.Serial(port, 115200, timeout=0.05,
                           write_timeout=0.5) as device:
            time.sleep(0.05)
            for attempt in range(retries + 1):
                device.reset_input_buffer()
                device.write(b"SYNCSTATE\r\n")
                device.flush()
                deadline = time.monotonic() + timeout
                data = bytearray()
                while time.monotonic() < deadline:
                    waiting = device.in_waiting
                    if waiting:
                        data.extend(device.read(waiting))
                        if b"bufs_per_sync=" in data:
                            time.sleep(0.02)
                            waiting = device.in_waiting
                            if waiting:
                                data.extend(device.read(waiting))
                            break
                    else:
                        time.sleep(0.005)
                row = parse_line(data.decode("ascii", errors="ignore"))
                if row is not None:
                    return row
                if attempt < retries:
                    time.sleep(0.05)
        return None
    except (OSError, serial.SerialException):
        return None


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def ticks_to_us(ticks: int, tick_hz: int) -> float:
    return (float(ticks) * 1_000_000.0) / float(tick_hz)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ports", nargs="+")
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=0.8)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--progress", type=float, default=30.0)
    parser.add_argument("--tick-hz", type=int, default=275_000_000)
    parser.add_argument(
        "--reference-zero", action="store_true",
        help="include the master target (zero error) in group phase span",
    )
    args = parser.parse_args()

    if args.tick_hz <= 0:
        parser.error("--tick-hz must be positive")

    states = {port: State() for port in args.ports}
    group_spans: list[int] = []
    group_invalid = 0
    started = time.monotonic()
    deadline = started + args.duration
    next_poll = started
    next_progress = started + args.progress

    print(f"PHASEMEAS START {datetime.now():%Y-%m-%d %H:%M:%S} "
          f"ports={','.join(args.ports)}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(args.ports)) as pool:
        while time.monotonic() < deadline:
            futures = {port: pool.submit(
                           query, port, args.timeout, max(0, args.retries))
                       for port in args.ports}
            cycle_rows: dict[str, dict | None] = {}
            for port, future in futures.items():
                row = future.result()
                cycle_rows[port] = row
                state = states[port]
                if row is None:
                    state.misses += 1
                    continue

                previous = state.last
                state.readings += 1
                state.last = row
                if state.first is None:
                    state.first = dict(row)
                state.errors.append(row["error"])
                state.controls.append(row["control"])
                state.pulse_values[row["pulse"]] += 1
                if previous is not None:
                    state.sampled_steps.append(
                        abs(row["error"] - previous["error"]))
                if row["rel"] != 1:
                    state.relation_bad += 1
                if row["bufs"] != 1:
                    state.buffer_bad += 1

            valid_rows = [row for row in cycle_rows.values()
                          if row is not None]
            if (len(valid_rows) == len(args.ports) and
                    all(row["rel"] == 1 and row["bufs"] == 1
                        for row in valid_rows)):
                errors = [row["error"] for row in valid_rows]
                if args.reference_zero:
                    errors.append(0)
                group_spans.append(max(errors) - min(errors))
            else:
                group_invalid += 1

            now = time.monotonic()
            if now >= next_progress:
                elapsed = now - started
                status = []
                for port, state in states.items():
                    if state.last is None:
                        status.append(f"{port}:MISS")
                    else:
                        status.append(
                            f"{port}:N{state.last['node']}"
                            f"/e{state.last['error']}"
                            f"/p{state.last['pulse']}"
                            f"/a{state.last['applied']}")
                print(f"[{elapsed:.0f}s] " + " ".join(status), flush=True)
                next_progress += args.progress

            next_poll += args.interval
            delay = min(deadline, next_poll) - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    elapsed = time.monotonic() - started
    print(f"PHASEMEAS END {datetime.now():%Y-%m-%d %H:%M:%S} "
          f"elapsed={elapsed:.1f}s", flush=True)
    for port, state in states.items():
        if state.first is None or state.last is None:
            print(f"SUMMARY {port} readings=0 misses={state.misses}",
                  flush=True)
            continue
        first = state.first
        last = state.last
        applied_delta = last["applied"] - first["applied"]
        edge_delta = last["edges"] - first["edges"]
        busy_delta = last["skip_busy"] - first["skip_busy"]
        spacing_delta = last["skip_spacing"] - first["skip_spacing"]
        pulse_hist = ",".join(
            f"{value}:{count}"
            for value, count in sorted(state.pulse_values.items()))
        abs_errors = [abs(v) for v in state.errors]
        abs_p50 = percentile(abs_errors, 0.50)
        abs_p95 = percentile(abs_errors, 0.95)
        abs_p99 = percentile(abs_errors, 0.99)
        abs_max = max(abs_errors)
        print(
            f"SUMMARY {port} node={last['node']} readings={state.readings} "
            f"misses={state.misses} rel_bad={state.relation_bad} "
            f"buf_bad={state.buffer_bad} "
            f"edges={edge_delta} applied={applied_delta} "
            f"applied_hz={applied_delta / elapsed:.2f} "
            f"busy={busy_delta} spacing={spacing_delta} "
            f"err_min={min(state.errors)} err_max={max(state.errors)} "
            f"err_abs_p50={abs_p50} "
            f"err_abs_p95={abs_p95} "
            f"err_abs_p99={abs_p99} err_abs_max={abs_max} "
            f"err_us_p50={ticks_to_us(abs_p50, args.tick_hz):.2f} "
            f"err_us_p95={ticks_to_us(abs_p95, args.tick_hz):.2f} "
            f"err_us_p99={ticks_to_us(abs_p99, args.tick_hz):.2f} "
            f"err_us_max={ticks_to_us(abs_max, args.tick_hz):.2f} "
            f"sample_step_p95={percentile(state.sampled_steps, 0.95)} "
            f"sample_step_max={max(state.sampled_steps, default=0)} "
            f"pulse_hist={pulse_hist} "
            f"slew={first['slew']}->{last['slew']} "
            f"restart={first['restart']}->{last['restart']} "
            f"sync_err={first['sync_error']}->{last['sync_error']}",
            flush=True,
        )
    if group_spans:
        span_p50 = percentile(group_spans, 0.50)
        span_p95 = percentile(group_spans, 0.95)
        span_p99 = percentile(group_spans, 0.99)
        span_max = max(group_spans)
        print(
            f"GROUP readings={len(group_spans)} invalid={group_invalid} "
            f"reference_zero={int(args.reference_zero)} "
            f"span_ticks_p50={span_p50} span_ticks_p95={span_p95} "
            f"span_ticks_p99={span_p99} span_ticks_max={span_max} "
            f"span_us_p50={ticks_to_us(span_p50, args.tick_hz):.2f} "
            f"span_us_p95={ticks_to_us(span_p95, args.tick_hz):.2f} "
            f"span_us_p99={ticks_to_us(span_p99, args.tick_hz):.2f} "
            f"span_us_max={ticks_to_us(span_max, args.tick_hz):.2f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
