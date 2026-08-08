#!/usr/bin/env python3
"""Poll SYNCSTATE on several STM32 VCP ports and summarize stability."""

import argparse
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import serial


FIELDS = {
    "raw": r"\braw=(\d+)",
    "node": r"\bnode=(\d+)",
    "nodes": r"\bactive_status_count=(\d+)",
    "mask": r"\bsync_seen_mask=0x([0-9A-Fa-f]+)",
    "alive": r"\bsync_alive=(\d+)",
    "ok": r"\bsync_ok=(\d+)",
    "rel": r"\bphase_rel=(\d+)",
    "flip": r"\bphase_flip=(\d+)",
    "slew": r"\bphase_slew=(\d+)",
    "slew_active": r"\bphase_slew_active=(\d+)",
    "restart": r"\bphase_restart=(\d+)",
    "sample": r"\bsample=(\d+)/(\d+)",
    "bufs": r"\bbufs_per_sync=(\d+)",
}


@dataclass
class PortState:
    readings: int = 0
    misses: int = 0
    anomalies: int = 0
    snapshot_mismatches: int = 0
    events: int = 0
    first: dict | None = None
    last: dict | None = None
    sample_min: int | None = None
    sample_max: int | None = None
    nodes_seen: set[int] = field(default_factory=set)
    masks_seen: set[int] = field(default_factory=set)


def now_text(with_ms: bool = False) -> str:
    fmt = "%H:%M:%S.%f" if with_ms else "%H:%M:%S"
    text = datetime.now().strftime(fmt)
    return text[:-3] if with_ms else text


def parse_syncstate(text: str) -> dict | None:
    lines = [line.strip() for line in text.splitlines()
             if "SYNC_STATE raw=" in line]
    if not lines:
        return None
    line = lines[-1]
    result: dict[str, int] = {}
    for name, pattern in FIELDS.items():
        match = re.search(pattern, line)
        if match is None:
            return None
        if name == "sample":
            result["sample"] = int(match.group(1))
            result["samples"] = int(match.group(2))
        elif name == "mask":
            result[name] = int(match.group(1), 16)
        else:
            result[name] = int(match.group(1))
    return result


def query_port(port: str, timeout: float, retries: int) -> dict | None:
    try:
        with serial.Serial(port, 115200, timeout=0.05,
                           write_timeout=0.5) as device:
            # ST-LINK VCP can drop bytes written immediately after open. Give
            # the bridge a short settle time and retry on the same handle so a
            # diagnostic transport miss is not reported as a sync failure.
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
                        time.sleep(0.01)
                row = parse_syncstate(data.decode("utf-8", errors="ignore"))
                if row is not None:
                    return row
                if attempt < retries:
                    time.sleep(0.05)
        return None
    except (OSError, serial.SerialException):
        return None


def compact(port: str, row: dict | None) -> str:
    if row is None:
        return f"{port}:MISS"
    return (f"{port}:N{row['node']}/r{row['rel']}/s"
            f"{row['sample']}/{row['samples']}/f{row['flip']}/"
            f"w{row['slew']}/x{row['restart']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ports", nargs="+")
    parser.add_argument("--duration", type=float, default=1200.0)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=0.8)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--expected-nodes", type=int, default=6)
    parser.add_argument("--expected-mask", type=lambda value: int(value, 0),
                        default=0x00000C0F)
    parser.add_argument("--progress", type=float, default=60.0)
    args = parser.parse_args()

    states = {port: PortState() for port in args.ports}
    started = time.monotonic()
    deadline = started + args.duration
    next_poll = started
    next_progress = started
    cycle = 0
    hard_failure = False

    print(f"SYNCMON START {datetime.now():%Y-%m-%d %H:%M:%S} "
          f"ports={','.join(args.ports)}", flush=True)
    while time.monotonic() < deadline:
        cycle += 1
        rows: dict[str, dict | None] = {}
        for port in args.ports:
            row = query_port(port, args.timeout, max(0, args.retries))
            rows[port] = row
            state = states[port]
            if row is None:
                state.misses += 1
                print(f"[{now_text(True)}] ANOM {port} no_response",
                      flush=True)
                continue

            state.readings += 1
            state.nodes_seen.add(row["nodes"])
            state.masks_seen.add(row["mask"])
            state.sample_min = (row["sample"] if state.sample_min is None
                                else min(state.sample_min, row["sample"]))
            state.sample_max = (row["sample"] if state.sample_max is None
                                else max(state.sample_max, row["sample"]))

            if state.first is None:
                state.first = dict(row)
            previous = state.last
            state.last = dict(row)

            reasons = []
            if row["nodes"] != args.expected_nodes:
                reasons.append(f"nodes={row['nodes']}")
            if row["mask"] != args.expected_mask:
                reasons.append(f"mask=0x{row['mask']:08X}")
            if row["alive"] != 1:
                reasons.append(f"sync={row['alive']}/{row['ok']}")
            elif row["ok"] != 1:
                # SYNCSTATE prints the cached LCD snapshot before the later
                # live phase fields. A slave can therefore show ok=0 and
                # rel=1 in one slowly transmitted line without losing sync.
                if row["raw"] == 1 and row["rel"] == 1:
                    state.snapshot_mismatches += 1
                else:
                    reasons.append(f"sync={row['alive']}/{row['ok']}")
            if row["raw"] == 1 and row["rel"] != 1:
                reasons.append(f"rel={row['rel']}")
            if row["raw"] == 1 and row["bufs"] != 1:
                reasons.append(f"bufs={row['bufs']}")
            if (previous is not None and
                    row["flip"] != previous["flip"]):
                reasons.append(
                    f"flip={previous['flip']}->{row['flip']}")
                hard_failure = True
            if (previous is not None and
                    row["restart"] != previous["restart"]):
                reasons.append(
                    f"restart={previous['restart']}->{row['restart']}")
                hard_failure = True
            if reasons:
                state.anomalies += 1
                print(f"[{now_text(True)}] ANOM {port} N{row['node']} "
                      + " ".join(reasons), flush=True)

            if previous is not None and row["slew"] > previous["slew"]:
                state.events += 1
                print(f"[{now_text(True)}] EVENT {port} N{row['node']} "
                      f"slew+{row['slew'] - previous['slew']} "
                      f"sample={row['sample']}/{row['samples']} "
                      f"rel={row['rel']} flip={row['flip']} "
                      f"slew={row['slew']} active={row['slew_active']} "
                      f"restart={row['restart']}", flush=True)
        elapsed = time.monotonic() - started
        if time.monotonic() >= next_progress:
            print(f"[{now_text()}] PROGRESS {elapsed:.0f}s "
                  + " ".join(compact(port, rows[port])
                             for port in args.ports), flush=True)
            next_progress += args.progress

        next_poll += args.interval
        delay = min(deadline, next_poll) - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    print(f"SYNCMON END {datetime.now():%Y-%m-%d %H:%M:%S} "
          f"cycles={cycle}", flush=True)
    for port, state in states.items():
        if state.first is None or state.last is None:
            print(f"SUMMARY {port} readings=0 misses={state.misses}",
                  flush=True)
            hard_failure = True
            continue
        first = state.first
        last = state.last
        print(
            f"SUMMARY {port} node={last['node']} readings={state.readings} "
            f"misses={state.misses} anomalies={state.anomalies} "
            f"snapshot_mismatches={state.snapshot_mismatches} "
            f"events={state.events} flip={first['flip']}->{last['flip']} "
            f"slew={first['slew']}->{last['slew']} "
            f"active={last['slew_active']} "
            f"restart={first['restart']}->{last['restart']} "
            f"sample={state.sample_min}..{state.sample_max} "
            f"rel={last['rel']} bufs={last['bufs']} "
            f"nodes={sorted(state.nodes_seen)} "
            f"masks={[f'0x{mask:08X}' for mask in sorted(state.masks_seen)]}",
            flush=True,
        )
    return 1 if hard_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
