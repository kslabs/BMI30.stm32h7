#!/usr/bin/env python3
"""Monitor live RAM via SWD HotPlug; never send UART, halt, reset, or write.

Reads the 128-byte g_rs485_stability_diag symbol from the supplied ELF.
Only CubeProgrammer -c ... mode=HotPlug -u is used. The ELF must match
the image already running on every probe. Exit 0=pass, 1=observed failure,
2=incomplete evidence. JSONL preserves every read attempt and its raw bytes.
"""

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SYMBOL = "g_rs485_stability_diag"
SIZE = 128
# Layout supplied by Core/Src/main.c; words 0 and 31 are the same even sequence.
FIELDS = ("seq_begin", "magic", "uptime_ms", "role", "node", "node_count",
          "mask", "phase_relation", "sync_alive", "observed_count",
          "unlocked_count", "ctrl_peak_ticks", "raw_peak_ticks", "phase_raw",
          "phase_ctrl", "phase_flip", "phase_restart", "uart_errors",
          "sync_errors", "filter_drop", "adc_publish_count", "adc_gap_over10ms",
          "adc_fault_count", "usb_tx_cplt", "usb_recovery", "adc_overflow_drop",
          "tick_hz", "lock_bound_ticks", "period", "usb_frame_age_ms",
          "adc_publish_age_ms", "seq_end")
SIGNED_FIELDS = {"phase_raw", "phase_ctrl"}
COUNTERS = ("observed_count", "unlocked_count", "phase_flip", "phase_restart",
            "uart_errors", "sync_errors", "filter_drop", "adc_publish_count",
            "adc_gap_over10ms", "adc_fault_count", "usb_tx_cplt", "usb_recovery",
            "adc_overflow_drop")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def run_process(argv, timeout):
    return subprocess.run(argv, capture_output=True, text=True, errors="replace",
                          timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def executable(requested, name, candidates):
    if requested:
        path = shutil.which(requested) or requested
        if Path(path).is_file():
            return str(Path(path).resolve())
        raise ValueError(f"Executable not found: {requested}")
    found = shutil.which(name)
    if found:
        return found
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ValueError(f"Executable {name} not found; provide its explicit path")


def symbol_address(nm, elf):
    proc = run_process([nm, "-S", "--defined-only", str(elf)], 30)
    if proc.returncode:
        raise ValueError(f"nm failed: {proc.stderr.strip()}")
    for line in proc.stdout.splitlines():
        match = re.fullmatch(r"\s*([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+\S\s+" + SYMBOL + r"\s*", line)
        if match:
            address, size = int(match[1], 16), int(match[2], 16)
            if size != SIZE or address % 4 or not 0x20000000 <= address < 0x20020000:
                raise ValueError(f"Unexpected diagnostic location/size: 0x{address:X}, {size}")
            return address
    raise ValueError(f"{SYMBOL} missing from {elf}; use the matching diagnostic firmware ELF")


def decode_snapshot(data):
    if len(data) != SIZE:
        raise ValueError(f"Expected {SIZE} bytes, received {len(data)}")
    words = struct.unpack("<32I", data)
    if words[0] == 0 or words[0] != words[31] or words[0] & 1:
        raise ValueError(f"Uninitialized/torn snapshot: sequence {words[0]}/{words[31]}")
    fields = dict(zip(FIELDS, words))
    if fields["magic"] != 0x52533438:
        raise ValueError(f"Invalid magic 0x{fields['magic']:08X}; ELF/image mismatch")
    if fields["tick_hz"] == 0:
        raise ValueError("Uninitialized diagnostic timer frequency")
    for key in SIGNED_FIELDS:
        if key in fields and fields[key] & 0x80000000:
            fields[key] -= 0x100000000
    return fields


class Recorder:
    def __init__(self, path):
        self.file = path.open("w", encoding="utf-8")
        self.lock = threading.Lock()

    def write(self, event):
        with self.lock:
            self.file.write(json.dumps({"timestamp": utc_now(), **event}) + "\n")
            self.file.flush()


def read_probe(device, args, address, recorder, poll_index):
    label, serial = device
    binary = args.scratch / f"{serial}-{poll_index}.bin"
    last_error = "no attempt"
    for attempt in range(args.retries + 1):
        started = time.monotonic()
        argv = [args.cli, "-c", "port=SWD", f"sn={serial}", "ap=0",
                f"freq={args.frequency}", "mode=HotPlug", "-u", hex(address),
                str(SIZE), str(binary)]
        event = {"device": label, "serial": serial, "poll": poll_index,
                 "attempt": attempt + 1, "command": argv}
        data = b""
        fields = None
        try:
            # A failed command must never reuse a successful previous upload.
            binary.unlink(missing_ok=True)
            proc = run_process(argv, args.timeout)
            event.update(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
            if proc.returncode:
                raise ValueError(f"CubeProgrammer exit {proc.returncode}")
            data = binary.read_bytes()
            fields = decode_snapshot(data)
            event["fields"] = fields
            event["read_error"] = None
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            event["read_error"] = last_error
        finally:
            event["elapsed_seconds"] = round(time.monotonic() - started, 6)
            event["raw_hex"] = data.hex()
            recorder.write(event)
            binary.unlink(missing_ok=True)
        if fields is not None:
            return {"fields": fields, "missed_attempts": attempt, "error": None}
    return {"fields": None, "missed_attempts": args.retries + 1, "error": last_error}


def positive_counter_delta(before, after):
    if after >= before:
        return after - before
    if before > 0xF0000000 and after < 0x10000000:
        return (after - before) & 0xFFFFFFFF
    raise ValueError("counter reset")


def diagnostic_age_ms(value):
    # Main captures now_ms before IRQ-owned timestamps. A completion in the
    # following few ticks can appear slightly in the future in this snapshot.
    return 0 if value >= 0xFFFFFFFC else value


def summarize_intervals(samples, args, failures, incomplete):
    intervals = []
    metrics = {"adc_publish_per_second": [], "phase_observed_per_second": [],
               "usb_completions_per_second": []}
    totals = dict.fromkeys(metrics, 0)
    duration = 0.0
    require_usb = getattr(args, "require_usb_active", False)
    min_adc_hz = getattr(args, "min_adc_hz", 0.0)
    min_usb_hz = getattr(args, "device_min_usb_hz", 0.0)
    for index, (before, after) in enumerate(zip(samples, samples[1:]), 1):
        try:
            elapsed_ms = positive_counter_delta(before["uptime_ms"], after["uptime_ms"])
            if elapsed_ms == 0:
                incomplete["nonadvancing_uptime"] = incomplete.get("nonadvancing_uptime", 0) + 1
                continue
            seconds = elapsed_ms / 1000
            adc = positive_counter_delta(before["adc_publish_count"], after["adc_publish_count"])
            phase = positive_counter_delta(before["observed_count"], after["observed_count"])
            usb = positive_counter_delta(before["usb_tx_cplt"], after["usb_tx_cplt"])
        except ValueError:
            # Resets are already failures in the cumulative-counter summary;
            # never turn the discontinuity into an apparently high rate.
            incomplete["invalid_rate_intervals"] = incomplete.get("invalid_rate_intervals", 0) + 1
            continue
        errors = []
        if adc == 0:
            errors.append("adc_not_advancing_interval")
        elif adc / seconds < min_adc_hz:
            errors.append("adc_rate_below_floor")
        if before["role"] == 1 and after["role"] == 1 and phase == 0:
            errors.append("phase_not_advancing_interval")
        if require_usb:
            if usb == 0:
                errors.append("usb_not_advancing_interval")
            elif usb / seconds < min_usb_hz:
                errors.append("usb_rate_below_floor")
            # dbg_tx_cplt includes command/status transfers. Also require an
            # actual ADC working-frame completion to have occurred each interval.
            prior_frame = (before["uptime_ms"] - diagnostic_age_ms(before["usb_frame_age_ms"])) & 0xFFFFFFFF
            last_frame = (after["uptime_ms"] - diagnostic_age_ms(after["usb_frame_age_ms"])) & 0xFFFFFFFF
            try:
                if positive_counter_delta(prior_frame, last_frame) == 0:
                    errors.append("usb_working_frame_not_advancing_interval")
            except ValueError:
                errors.append("usb_working_frame_timestamp_reset")
        rates = dict(zip(metrics, (adc / seconds, phase / seconds, usb / seconds)))
        for key, count in zip(metrics, (adc, phase, usb)):
            metrics[key].append(rates[key])
            totals[key] += count
        duration += seconds
        intervals.append({"index": index, "start_uptime_ms": before["uptime_ms"],
                          "end_uptime_ms": after["uptime_ms"], "seconds": seconds,
                          **rates, "failures": errors})
        failures.update(errors)
    rates = {key: {"min": min(values), "max": max(values),
                   "mean": totals[key] / duration, "intervals": len(values)}
             for key, values in metrics.items() if values}
    return intervals, rates


def summarize(samples, args):
    """Use firmware accumulated phase peaks/counters to cover gaps between polls."""
    result = {"readings": len(samples), "failures": {}, "incomplete": {}, "warnings": {},
              "ranges": {}, "counter_deltas": {}, "values_seen": {},
              "rates": {}, "intervals": [], "future_timestamp_artifacts": {}}
    if len(samples) < 2:
        result["incomplete"]["insufficient_readings"] = len(samples)
    if samples:
        result["first"], result["last"] = samples[0], samples[-1]
    if not samples:
        return result
    failures = Counter()
    for key in ("role", "node", "node_count", "mask", "tick_hz", "lock_bound_ticks"):
        values = [row[key] for row in samples]
        result["values_seen"][key] = sorted(set(values))
        if len(result["values_seen"][key]) > 1:
            failures["changed:" + key] += 1
    for key in ("phase_raw", "phase_ctrl", "raw_peak_ticks", "ctrl_peak_ticks",
                "period", "usb_frame_age_ms", "adc_publish_age_ms"):
        values = [row[key] for row in samples]
        result["ranges"][key] = {"min": min(values), "max": max(values)}
    for key in ("phase_raw", "phase_ctrl", "raw_peak_ticks", "ctrl_peak_ticks"):
        values = [row[key] * 1000000 / row["tick_hz"] for row in samples]
        result["ranges"][key.replace("_ticks", "") + "_us"] = {
            "min": min(values), "max": max(values)}
    for key in ("adc_publish_age_ms", "usb_frame_age_ms"):
        values = [diagnostic_age_ms(row[key]) for row in samples]
        result["ranges"][key + "_effective"] = {"min": min(values), "max": max(values)}
    for key in COUNTERS:
        total = 0
        for previous, current in zip(samples, samples[1:]):
            try:
                total += positive_counter_delta(previous[key], current[key])
            except ValueError:
                failures["counter_reset:" + key] += 1
        result["counter_deltas"][key] = total
        if key in ("phase_flip", "phase_restart", "adc_gap_over10ms",
                   "adc_fault_count", "usb_recovery") and total:
            failures["counter_increase:" + key] = total
        # This is RX flags on a byte whose VALUE matches SYNC, including NE
        # majority-voted bytes, reply payloads and local echo. It is not a
        # rejected-reference counter. Phase loss is checked independently via
        # every-edge peaks/unlocked counts, freshness and reference progress.
        if key == "sync_errors" and total:
            result["warnings"]["rx_sync_value_flagged"] = total
            if getattr(args, "require_clean_sync_rx", False):
                failures["counter_increase:" + key] = total
    for row in samples:
        for key in ("adc_publish_age_ms", "usb_frame_age_ms"):
            if row[key] >= 0xFFFFFFFC:
                artifacts = result["future_timestamp_artifacts"]
                artifacts[key] = artifacts.get(key, 0) + 1
        if diagnostic_age_ms(row["adc_publish_age_ms"]) > getattr(args, "max_adc_age_ms", 10):
            failures["stale_adc_publication"] += 1
        if (getattr(args, "require_usb_active", False) and
                diagnostic_age_ms(row["usb_frame_age_ms"]) > getattr(args, "max_usb_age_ms", 1000)):
            failures["stale_usb_working_frame"] += 1
        if row["role"] not in (0, 1):
            failures["invalid_role"] += 1
        if row["sync_alive"] != 1:
            failures["sync_not_alive"] += 1
        if args.expected_nodes is not None and row["node_count"] != args.expected_nodes:
            failures["unexpected_node_count"] += 1
        if args.expected_mask is not None and row["mask"] != args.expected_mask:
            failures["unexpected_node_mask"] += 1
        if row["role"] == 1:
            if row["phase_relation"] != 1:
                failures["slave_not_in_phase"] += 1
            bound = min(row["lock_bound_ticks"], args.limit_us * row["tick_hz"] / 1000000)
            if row["ctrl_peak_ticks"] > bound or abs(row["phase_ctrl"]) > bound:
                failures["control_phase_above_limit"] += 1
            if (getattr(args, "require_raw_phase", False) and
                    (row["raw_peak_ticks"] > bound or abs(row["phase_raw"]) > bound)):
                failures["raw_phase_above_limit"] += 1
    if 1 in result["values_seen"]["role"]:
        if result["counter_deltas"]["unlocked_count"]:
            failures["unlocked_observations"] = result["counter_deltas"]["unlocked_count"]
        if result["counter_deltas"]["observed_count"] == 0:
            result["incomplete"]["no_phase_observations"] = 1
    if len(samples) >= 2:
        try:
            duration_ms = positive_counter_delta(samples[0]["uptime_ms"], samples[-1]["uptime_ms"])
            result["covered_seconds"] = duration_ms / 1000
            if duration_ms < args.duration * 1000:
                result["incomplete"]["short_observation_window"] = duration_ms / 1000
        except ValueError:
            failures["uptime_reset"] += 1
        if result["counter_deltas"]["adc_publish_count"] == 0:
            failures["adc_not_advancing"] += 1
    for previous, current in zip(samples, samples[1:]):
        if previous["seq_begin"] == current["seq_begin"]:
            result["incomplete"]["stale_snapshots"] = result["incomplete"].get("stale_snapshots", 0) + 1
    result["intervals"], result["rates"] = summarize_intervals(samples, args, failures, result["incomplete"])
    result["criteria"] = {"require_clean_sync_rx": getattr(args, "require_clean_sync_rx", False),
                          "require_usb_active": getattr(args, "require_usb_active", False),
                          "require_raw_phase": getattr(args, "require_raw_phase", False),
                          "min_adc_hz": getattr(args, "min_adc_hz", 0),
                          "min_usb_hz": getattr(args, "device_min_usb_hz", 0),
                          "max_adc_age_ms": getattr(args, "max_adc_age_ms", 10),
                          "max_usb_age_ms": getattr(args, "max_usb_age_ms", 1000)}
    result["failures"] = dict(failures)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", action="append", required=True, metavar="LABEL:STLINK_SN")
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--frequency", type=int, default=4000)
    parser.add_argument("--limit-us", "--max-phase-us", dest="limit_us", type=float, default=3.125)
    parser.add_argument("--require-raw-phase", action="store_true",
                        help="also fail on accumulated raw phase peaks or current raw phase above the limit")
    parser.add_argument("--require-clean-sync-rx", action="store_true",
                        help="also fail on UART flags on any SYNC-valued byte, including noise-only bytes and local echo")
    parser.add_argument("--expected-nodes", type=int)
    parser.add_argument("--expected-mask", type=lambda value: int(value, 0))
    parser.add_argument("--require-usb-active", action="store_true",
                        help="require every interval to transfer USB working frames on every device")
    parser.add_argument("--min-adc-hz", type=float, default=0,
                        help="minimum ADC publication rate in each interval (e.g. 390 for the 400-Hz profile)")
    parser.add_argument("--min-usb-hz", action="append", default=[], metavar="LABEL:RATE",
                        help="per-device USB completion rate floor; requires --require-usb-active")
    parser.add_argument("--max-adc-age-ms", type=int, default=10)
    parser.add_argument("--max-usb-age-ms", type=int, default=1000,
                        help="maximum age of an actual working-frame completion when USB is required")
    parser.add_argument("--elf", type=Path, default=Path("Debug/BMI30.stm32h7.elf"))
    parser.add_argument("--cli")
    parser.add_argument("--nm")
    parser.add_argument("--output", type=Path, default=Path("rs485-swd.jsonl"))
    args = parser.parse_args()
    if not all(math.isfinite(value) for value in (args.duration, args.interval, args.timeout,
                                                 args.limit_us, args.min_adc_hz)):
        parser.error("numeric limits must be finite")
    if min(args.duration, args.interval, args.timeout, args.limit_us) <= 0 or args.retries < 0 or args.workers < 1:
        parser.error("durations, phase limit and workers must be positive; retries nonnegative")
    if args.min_adc_hz < 0 or args.max_adc_age_ms < 0 or args.max_usb_age_ms < 0:
        parser.error("rate floors and age limits must be nonnegative")
    devices = []
    for text in args.device:
        match = re.fullmatch(r"([^:]+):([A-Za-z0-9]+)", text)
        if not match:
            parser.error(f"Invalid device {text!r}; expected LABEL:STLINK_SN")
        devices.append((match[1], match[2]))
    if len({item[0] for item in devices}) != len(devices) or len({item[1] for item in devices}) != len(devices):
        parser.error("device labels and ST-LINK serial numbers must be unique")
    usb_floors = {}
    for value in args.min_usb_hz:
        try:
            label, rate = value.rsplit(":", 1)
            rate = float(rate)
        except ValueError:
            parser.error("min-usb-hz must be LABEL:RATE")
        if label not in {item[0] for item in devices} or label in usb_floors or not math.isfinite(rate) or rate < 0:
            parser.error("USB rate floors need a unique configured label and nonnegative rate")
        usb_floors[label] = rate
    if usb_floors and not args.require_usb_active:
        parser.error("min-usb-hz requires require-usb-active")
    try:
        args.cli = executable(args.cli, "STM32_Programmer_CLI.exe", [Path(
            "C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe")])
        args.nm = executable(args.nm, "arm-none-eabi-nm.exe", list(Path("C:/ST").glob(
            "STM32CubeCLT_*/GNU-tools-for-STM32/bin/arm-none-eabi-nm.exe")))
        args.elf = args.elf.resolve(strict=True)
        address = symbol_address(args.nm, args.elf)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.scratch = args.output.parent / (args.output.stem + ".swd-reads")
    args.scratch.mkdir(exist_ok=True)
    summary_path = args.output.with_suffix(".summary.json")
    if summary_path == args.output:
        parser.error("output path conflicts with summary path")
    recorder = Recorder(args.output)
    rows = {label: [] for label, _ in devices}
    misses = Counter()
    missed_attempts = Counter()
    interrupted = False
    start_timestamp = utc_now()
    started = time.monotonic()
    next_poll = started
    poll_index = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(args.workers, len(devices))) as pool:
            while True:
                poll_index += 1
                futures = {pool.submit(read_probe, device, args, address, recorder, poll_index): device[0]
                           for device in devices}
                try:
                    for future in concurrent.futures.as_completed(futures):
                        label = futures[future]
                        result = future.result()
                        missed_attempts[label] += result["missed_attempts"]
                        if result["fields"] is None:
                            misses[label] += 1
                        else:
                            rows[label].append(result["fields"])
                except KeyboardInterrupt:
                    interrupted = True
                    break
                elapsed = time.monotonic() - started
                print(f"SWD {elapsed:.1f}s polls={poll_index} readings=" +
                      ",".join(f"{label}:{len(rows[label])}" for label, _ in devices), flush=True)
                # Firmware uptime must span the complete requested interval on
                # every board, including the time spent obtaining the baseline.
                covered = all(len(rows[label]) >= 2 and
                              ((rows[label][-1]["uptime_ms"] - rows[label][0]["uptime_ms"]) & 0xFFFFFFFF)
                              >= args.duration * 1000 for label, _ in devices)
                if covered or elapsed >= args.duration + args.interval * 2 + args.timeout * (args.retries + 1):
                    break
                next_poll = max(next_poll + args.interval, time.monotonic())
                time.sleep(max(0, next_poll - time.monotonic()))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        recorder.file.close()
    results = {}
    for label, serial in devices:
        device_args = argparse.Namespace(**vars(args), device_min_usb_hz=usb_floors.get(label, 0))
        result = summarize(rows[label], device_args)
        result.update(serial=serial, missed_polls=misses[label], missed_attempts=missed_attempts[label])
        if misses[label]:
            result["incomplete"]["missing_polls"] = misses[label]
        results[label] = result
    failed = any(result["failures"] for result in results.values())
    incomplete = interrupted or any(result["incomplete"] for result in results.values())
    exit_code = 1 if failed else 2 if incomplete else 0
    summary = {"started": start_timestamp, "finished": utc_now(), "interrupted": interrupted,
               "elapsed_seconds": time.monotonic() - started, "requested_duration_seconds": args.duration,
               "interval_seconds": args.interval, "limit_us": args.limit_us,
               "require_raw_phase": args.require_raw_phase,
               "require_clean_sync_rx": args.require_clean_sync_rx,
               "require_usb_active": args.require_usb_active, "min_adc_hz": args.min_adc_hz,
               "min_usb_hz": usb_floors, "max_adc_age_ms": args.max_adc_age_ms,
               "max_usb_age_ms": args.max_usb_age_ms,
               "elf": str(args.elf), "elf_sha256": hashlib.sha256(args.elf.read_bytes()).hexdigest(),
               "symbol": SYMBOL, "address": hex(address), "size": SIZE,
               "status": "FAIL" if failed else "INCOMPLETE" if incomplete else "PASS",
               "exit_code": exit_code, "devices": results,
               "measurement": "Internal firmware timing diagnostics read via SWD HotPlug; "
                              "not an independent oscilloscope measurement of physical output edges. "
                              "Phase pass uses controller peak against firmware/CLI bound and unlocked-count delta; "
                              "raw transport timestamp excursions also fail when require_raw_phase is enabled. "
                              "RX flags on SYNC-valued bytes are reported separately; --require-clean-sync-rx also fails on those flags. "
                              "Firmware peaks must be reset by the operator after settling and before this read-only run. "
                              "Throughput is checked per polling interval using firmware uptime; this is not "
                              "direct DSP profiling and cannot exclude short USB stalls between polls."}
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"{summary['status']} summary={summary_path}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
