"""Observe the running Raspberry detector through its read-only local HTTP API.

No USB access, configuration writes, service restarts, or player recording.
The existing player recorder suspends realtime detection and is unsuitable here.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import subprocess
import threading


HOSTS = {
    "M01": "BMI30-05AB38E6A",
    "S10": "BMI30-D5EB8F26C",
    "S11": "BMI30-D5507EDFC",
    "S12": "BMI30-27448D72B",
    "S13": "BMI30-7AA5CED27",
}

REMOTE = r'''
import json,subprocess,sys,time,urllib.request
expected,duration,interval=sys.argv[1],float(sys.argv[2]),float(sys.argv[3])
hostname=subprocess.check_output(['hostname'],text=True).strip()
if hostname.casefold()!=expected.casefold():
    raise RuntimeError('SSH hostname does not match requested instrument')
def pid():
    return subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()
def emit(x):
    print(json.dumps(x,separators=(',',':')),flush=True)
start=time.monotonic()
emit({'kind':'start','hostname':hostname,'core_pid':pid(),'epoch':time.time(),'interval_s':interval,'duration_s':duration})
deadline=start+duration
while time.monotonic()<deadline:
    before=time.monotonic()
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3) as response:
            status=json.load(response)
        emit({'kind':'sample','elapsed_s':time.monotonic()-start,'epoch':time.time(),
              'request_ms':(time.monotonic()-before)*1000,'status':status})
    except Exception as error:
        emit({'kind':'error','elapsed_s':time.monotonic()-start,'epoch':time.time(),'error':str(error)})
    time.sleep(max(0,min(interval-(time.monotonic()-before),deadline-time.monotonic())))
emit({'kind':'end','hostname':hostname,'core_pid':pid(),'epoch':time.time()})
'''


def summarize(records):
    all_samples = [r for r in records if r.get("kind") == "sample"]
    samples = [r for r in all_samples if r["status"].get("ok")
               and not r["status"].get("stale") and not r["status"].get("busy")]
    result = {"samples": len(all_samples), "usable_samples": len(samples),
              "request_errors": sum(r.get("kind") == "error" for r in records)}
    boundaries = [r for r in records if r.get("kind") in ("start", "end")]
    result["complete"] = len(boundaries) == 2 and boundaries[-1]["kind"] == "end"
    result["same_core_pid"] = (boundaries[0]["core_pid"] == boundaries[-1]["core_pid"]
                                if result["complete"] else None)
    if len(samples) < 2:
        result["error"] = "Fewer than two usable status samples"
        return result
    first, last = samples[0], samples[-1]
    a, b = first["status"], last["status"]
    elapsed = last["elapsed_s"] - first["elapsed_s"]
    result["elapsed_s"] = elapsed
    result["mode"] = b.get("mode")
    result["mode_changed"] = any(r["status"].get("mode") != a.get("mode") for r in samples)
    def delta(section, key):
        values = [r["status"].get(section, {}).get(key) for r in samples]
        if any(not isinstance(v, (int, float)) for v in values):
            return None
        if any(y < x for x, y in zip(values, values[1:])):
            return None  # Counter reset/wrap is not a negative number of events.
        return values[-1] - values[0]
    counters = ("rx_cnt_ch0", "rx_cnt_ch1", "seq_gap_ch0", "seq_gap_ch1", "crc_bad", "magic_bad", "host_rx_ack_fail")
    result["stream_delta"] = {k: delta("stream_stats", k) for k in counters}
    result["received_fps"] = {f"ch{ch}": (result["stream_delta"][f"rx_cnt_ch{ch}"] / elapsed
                              if elapsed > 0 and result["stream_delta"][f"rx_cnt_ch{ch}"] is not None else None)
                              for ch in (0, 1)}
    result["fire_delta"] = {f"ch{ch}": delta("detector", f"fire_seq{ch}") for ch in (0, 1)}
    workers = [r["status"].get("detector", {}).get("worker", {}) for r in samples]
    def worker_delta(key):
        values = [w.get(key) for w in workers]
        return values[-1] - values[0] if all(isinstance(v, (int, float)) for v in values) and all(y >= x for x, y in zip(values, values[1:])) else None
    result["worker_delta"] = {k: worker_delta(k) for k in ("submitted", "results", "overflows")}
    result["worker_alive_all_samples"] = all(w.get("alive") for w in workers)
    result["worker_max_sampled_queue"] = {k: max(w.get(k, 0) or 0 for w in workers) for k in ("request_queue", "result_queue")}
    latencies = [w.get("latency", {}) for w in workers]
    counts = [v.get("over_100ms") for v in latencies]
    result["over_100ms_delta"] = counts[-1] - counts[0] if all(isinstance(v, (int, float)) for v in counts) and all(y >= x for x, y in zip(counts, counts[1:])) else None
    result["worker_lifetime_max_ms"] = latencies[-1].get("max_total_ms")
    result["optic_blocked_samples"] = sum(r["status"].get("optic", {}).get("reaction_gate_allowed") is False for r in samples)
    result["channels_enabled"] = {k: v.get("enabled") for k, v in b.get("detector", {}).get("channels", {}).items()}
    result["settings"] = b.get("detector", {}).get("settings")
    result["confirmed_events"] = []
    seen = {ch: a.get("detector", {}).get(f"fire_seq{ch}") for ch in (0, 1)}
    for record in samples[1:]:
        status = record["status"]
        detector = status.get("detector", {})
        for ch in (0, 1):
            seq = detector.get(f"fire_seq{ch}")
            if seq is not None and seen[ch] is not None and seq > seen[ch]:
                result["confirmed_events"].append({"observed_epoch": record["epoch"], "channel": ch,
                    "counter_delta": seq-seen[ch], "snapshot": detector.get(f"fire_snapshot{ch}"),
                    "optic_gate": status.get("optic", {}).get("reaction_gate_allowed"),
                    "sound_output": status.get("sound", {}).get("output_active")})
            seen[ch] = seq
    result["limitations"] = "API sampling does not record every detector decision or establish physical tag passages. Lifetime latency maximum is not the observation-window maximum. Output state is sampled after the event."
    return result


def capture(board, args, output):
    host = HOSTS[board]
    ssh = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH/ssh.exe"
    command = "python3 - " + " ".join(shlex.quote(str(x)) for x in (host, args.seconds, args.interval))
    process = subprocess.Popen([str(ssh), "-F", str(Path.home()/".ssh/bmi30_config"),
                              "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                              "-o", "ConnectTimeout=8", host, command],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timer = threading.Timer(args.seconds+30, process.kill)
    timer.daemon = True
    timer.start()
    records = []
    try:
        process.stdin.write(REMOTE.encode())
        process.stdin.close()
        with (output/f"{board}.jsonl").open("wb") as log:
            ready = False
            previous_fire = None
            for line in process.stdout:
                log.write(line)
                log.flush()
                record = json.loads(line)
                records.append(record)
                if record.get("kind") == "sample":
                    status = record["status"]
                    detector = status.get("detector", {})
                    if not ready:
                        print(json.dumps({"board": board, "observation_started": True,
                              "epoch": record["epoch"], "detector_active": detector.get("active"),
                              "channels": {k:v.get("enabled") for k,v in detector.get("channels", {}).items()},
                              "request_ms": record["request_ms"]}), flush=True)
                        ready = True
                    fire = [detector.get(f"fire_seq{ch}") for ch in (0, 1)]
                    if previous_fire is not None and fire != previous_fire:
                        print(json.dumps({"board": board, "elapsed_s": round(record["elapsed_s"], 2),
                              "fire_counters": fire, "optic_gate": status.get("optic", {}).get("reaction_gate_allowed"),
                              "sound_output": status.get("sound", {}).get("output_active")}), flush=True)
                    previous_fire = fire
        process.wait(timeout=5)
        stderr = process.stderr.read()
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
            process.wait()
    if process.returncode:
        raise RuntimeError(f"{board}: {stderr.decode(errors='replace')[-1200:]}")
    summary = summarize(records)
    (output/f"{board}-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return board, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boards", nargs="+", choices=list(HOSTS), default=["S13"])
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 600 or not 0.1 <= args.interval <= 5:
        parser.error("seconds must be 5..600 and interval 0.1..5")
    output = args.output or Path(".tmp/tag-detection-20260930")/dt.datetime.now().strftime("capture-%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    print(f"Read-only status observation: {output}", flush=True)
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(args.boards)) as pool:
        futures = {pool.submit(capture, board, args, output): board for board in args.boards}
        for future in concurrent.futures.as_completed(futures):
            board = futures[future]
            try:
                _, summary = future.result()
                print(json.dumps({"board": board, **{k: summary.get(k) for k in (
                    "complete", "same_core_pid", "elapsed_s", "received_fps", "stream_delta", "worker_delta",
                    "over_100ms_delta", "fire_delta", "optic_blocked_samples")}}, ensure_ascii=False), flush=True)
            except Exception as error:
                errors.append({"board": board, "error": str(error)})
                print(json.dumps(errors[-1]), flush=True)
    if errors:
        (output/"errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
