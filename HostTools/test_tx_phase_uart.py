#!/usr/bin/env python3
"""Exercise TX phases on running boards via ST-LINK UART; restore settings."""

import argparse
import json
from pathlib import Path
import re
import time

import serial


class Board:
    def __init__(self, port, folder):
        self.port = port
        self.serial = serial.Serial(port, 115200, timeout=0.1, write_timeout=1)
        self.log = (folder / f"{port}.log").open("w", encoding="utf-8")

    def command(self, command, prefix="OPTIC "):
        self.serial.reset_input_buffer()
        self.log.write(f"\n>>> {command}\n")
        self.serial.write(("\r\n" + command + "\r\n").encode("ascii"))
        deadline = time.monotonic() + 3
        pending = b""
        while time.monotonic() < deadline:
            pending += self.serial.read_until(b"\n")
            if not pending.endswith(b"\n"):
                continue
            line = pending.decode("utf-8", errors="replace").strip()
            pending = b""
            self.log.write(line + "\n")
            self.log.flush()
            if line.startswith(prefix):
                return dict(re.findall(r"(\w+)=([^\s]+)", line))
        raise RuntimeError(f"{self.port}: no {prefix!r} response to {command!r}")

    def close(self):
        self.serial.close()
        self.log.close()


def check_phase(state, expected, enabled=None):
    for ch, phase in enumerate(expected, 1):
        assert int(state[f"tx{ch}_phase_req"]) == phase, state
        assert int(state[f"tx{ch}_phase"]) == phase, state
    s = int(state["marker_phase"])
    on = int(state["tx200"])
    if enabled is not None:
        assert on == enabled, state
    assert int(state["pa1"]) == int(not (on and (s ^ expected[0]))), state
    assert int(state["pa2"]) == int(bool(on and (s ^ expected[1]))), state
    assert int(state["pc7"]) == int(bool(on and s)), state
    assert state["adc_pause"] == "0", state


def check_sync(state, baseline):
    for name in ("raw", "node", "id_assigned", "phase_flip", "phase_restart",
                 "id_conflicts", "multiple_master", "sync_err"):
        assert state[name] == baseline[name], (name, baseline[name], state[name])
    assert state["sync_alive"] == "1" and state["sync_ok"] == "1", state
    assert state["sync_locked"] == "1", state
    if state["char"] == "S":
        assert state["phase_rel"] == "1", state
        # phase_locked is an instantaneous deadband diagnostic; the existing
        # PLL may briefly correct outside it while stable sync_locked stays 1.


def exercise(board):
    initial = board.command("TXPH")
    original = [int(initial[f"tx{ch}_phase_req"]) for ch in (1, 2)]
    tx_request = int(initial["tx_req"])
    version = board.command("VER", "VERSION ")
    baseline = board.command("SYNCSTATE", "SYNC_STATE ")
    assert version["fw"] == "1.2.46", version
    assert initial["stream"] == "1", "An existing USB stream is required for GPIO checks"
    expected = original[:]
    samples = []
    try:
        board.command("TX200 1")
        for p1, p2 in ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0)):
            # Verify after each individual channel command, including retries.
            for ch, value in ((1, p1), (2, p2)):
                board.command(f"TXPH{ch} {value}")
                expected[ch - 1] = value
                check_phase(board.command("TXPH"), expected, enabled=1)
                board.command(f"TXPH{ch} {value}")
                check_phase(board.command("TXPH"), expected, enabled=1)
            levels_seen = set()
            for _ in range(12):
                state = board.command("TXPH")
                check_phase(state, expected, enabled=1)
                levels_seen.add(int(state["marker_phase"]))
                samples.append({key: state[key] for key in (
                    "marker_phase", "tx1_phase", "tx2_phase", "pa1", "pa2", "pc7")})
                if levels_seen == {0, 1}:
                    break
            assert levels_seen == {0, 1}, "Both sync half-cycles must be sampled"
            check_sync(board.command("SYNCSTATE", "SYNC_STATE "), baseline)
            print(f"{board.port}: phases {p1}/{p2}, both half-cycles, sync OK", flush=True)

        for invalid in ("TXPH1 2", "TXPH2 -1", "TXPH1", "TXPH2 1 extra", "TXPH3 1"):
            board.command(invalid, "TXPH usage:")
            check_phase(board.command("TXPH"), expected, enabled=1)

        board.command("TX200 0")
        for ch in (1, 2):
            board.command(f"TXPH{ch} 1")
            expected[ch - 1] = 1
            check_phase(board.command("TXPH"), expected, enabled=0)
        board.command("TX200 1")
        check_phase(board.command("TXPH"), expected, enabled=1)
        after = board.command("TXPH")
        assert after["adc_restart"] == initial["adc_restart"], after
        assert after["adc_tc_rearm_fail"] == initial["adc_tc_rearm_fail"], after
        assert int(after["adc_wr"]) > int(initial["adc_wr"]), after
        final_sync = board.command("SYNCSTATE", "SYNC_STATE ")
        check_sync(final_sync, baseline)
        return {"port": board.port, "version": version, "sync_before": baseline,
                "sync_after": final_sync, "samples": samples,
                "adc_restart": after["adc_restart"], "result": "PASS"}
    finally:
        for ch, phase in enumerate(original, 1):
            board.command(f"TXPH{ch} {phase}")
        board.command(f"TX200 {tx_request}")
        check_phase(board.command("TXPH"), original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ports", nargs="+")
    parser.add_argument("--out", type=Path, default=Path(".tmp/tx_phase_uart"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for port in args.ports:
        board = Board(port, args.out)
        try:
            results.append(exercise(board))
        finally:
            board.close()
    (args.out / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"PASS: all boards; original phases/TX request restored; logs in {args.out}")


if __name__ == "__main__":
    main()
