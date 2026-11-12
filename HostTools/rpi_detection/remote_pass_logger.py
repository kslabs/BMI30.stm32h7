"""Run on the Raspberry: capture one identified Bluetooth remote and detector status.

Stdout is JSONL. Redirect it to a new file over SSH. No configuration writes,
USB access, recorder/player commands or service changes. The selected remote is
grabbed only for the lifetime of this process so media keys cannot change volume.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import struct
import subprocess
import threading
import time
import urllib.request


CHANNEL_SETTING_KEYS = ('enabled', 'confirm_count', 'threshold', 'threshold_high', 'auto_threshold',
                        'noise_up', 'noise_down', 'noise_unit', 'burst_gate', 'burst_blank_s', 'burst_max_ratio')
MODE_SETTING_KEYS = ('selected', 'stream_mode', 'base_buf_len', 'freq_hz', 'desired_freq',
                     'avg_n', 'rpi_avg_n', 'det_ratio')


def detector_profile(status):
    """Comparable input/settings only; exclude evolving noise, counters and HOLD."""
    detector = status.get('detector', {})
    channels = detector.get('channels', {})
    mode = status.get('mode', {})
    return {'mode': {key: mode.get(key) for key in MODE_SETTING_KEYS},
            'settings': detector.get('settings', {}),
            'channels': {name: {key: row.get(key) for key in CHANNEL_SETTING_KEYS}
                         for name, row in channels.items()}}


class PassMarks:
    """Human labels only; never infer passage count from detector events."""

    def __init__(self):
        self.current = None
        self.next_id = 1
        self.completed = []

    def mark(self, action, mono):
        records = []
        if action == "start":
            if self.current is not None:
                records.append(self.finish("unlabelled", mono))
            self.current = {"pass_id": self.next_id, "start_mono": mono}
            self.next_id += 1
            records.append({"kind": "pass_start", **self.current})
        elif self.current is None:
            records.append({"kind": "ignored_mark", "action": action,
                            "reason": "no_open_pass"})
        else:
            records.append(self.finish(action, mono))
        return records

    def finish(self, result, mono):
        record = {"kind": "pass_end", **self.current,
                  "human_result": result, "end_mono": mono}
        self.completed.append(record)
        self.current = None
        return record


class KeyPresses:
    def __init__(self, mapping):
        self.mapping = mapping
        self.down = set()

    def update(self, event_type, code, value):
        if event_type != 1:  # EV_KEY; ignore mouse movement etc.
            return None
        if value == 0:
            self.down.discard(code)
            return None
        if value != 1 or code in self.down:  # Ignore hold repeat and duplicate down.
            return None
        self.down.add(code)
        return self.mapping.get(code)


def device_identity(path, expected_mac):
    resolved = Path(path).resolve(strict=True)
    if resolved.parent != Path("/dev/input") or not re.fullmatch(r"event\d+", resolved.name):
        raise ValueError("Select a specific /dev/input/eventN device")
    base = Path("/sys/class/input") / resolved.name / "device"
    identity = {"device": str(resolved), "name": (base / "name").read_text().strip(),
                "uniq": (base / "uniq").read_text().strip(),
                "bustype": (base / "id/bustype").read_text().strip()}
    if int(identity["bustype"], 16) != 5:
        raise ValueError("The selected input device is not Bluetooth")
    if identity["uniq"].casefold() != expected_mac.casefold():
        raise ValueError("Input device identity does not match the selected remote")
    return identity


def get_status():
    with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=2) as response:
        return json.load(response)


def core_pid():
    return subprocess.check_output(
        ["systemctl", "show", "bmi30-core", "-p", "MainPID", "--value"], text=True).strip()


def validate_status(status, channel):
    if (not status.get("ok") or status.get("stale") or status.get("busy")
            or not status.get("connection", {}).get("connected")):
        raise ValueError("Detector status is unavailable or stale")
    detector = status.get("detector", {})
    if not detector.get("active") or not detector.get("channels", {}).get(channel, {}).get("enabled"):
        raise ValueError("The selected detector channel is not active")
    player = status.get("player_recorder", {})
    if player.get("recording") or player.get("playback_active"):
        raise ValueError("Player/recorder is active; live test is unavailable")


def run(args):
    import fcntl  # Linux only; pure mark state machine remains testable on Windows.

    if socket.gethostname().casefold() != args.hostname.casefold():
        raise ValueError("Unexpected Raspberry hostname")
    identity = device_identity(args.device, args.mac)
    mapping = {args.start_code: "start", args.hit_code: "hit", args.miss_code: "miss"}
    touch_mapping = {args.touch_start: "start", args.touch_hit: "hit", args.touch_miss: "miss"}
    touch = None
    if args.input_mode == "touch":
        from ring_gestures import TouchGestures
        touch = TouchGestures()
    marks, presses = PassMarks(), KeyPresses(mapping)
    stop = threading.Event()
    output_lock = threading.Lock()

    def emit(record):
        with output_lock:
            record = {"epoch": time.time(), "observed_mono": time.monotonic(), **record}
            print(json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=True)

    def stop_signal(*_):
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop_signal)

    baseline = None
    if not args.inspect:
        baseline = get_status()
        validate_status(baseline, args.channel)

    initial_pid = core_pid()
    # Open exactly the verified device, never /dev/input/* or another keyboard.
    fd = os.open(identity["device"], os.O_RDONLY | os.O_NONBLOCK)
    companion_fds = []
    poller = None
    try:
        fcntl.ioctl(fd, 0x40044590, 1)  # EVIOCGRAB; released by close even on error.
        # Suppress desktop actions from companion interfaces of this same ring.
        for path in Path("/sys/class/input").glob("event*"):
            try:
                if (path / "device/uniq").read_text().strip().casefold() != args.mac.casefold():
                    continue
                companion = device_identity("/dev/input/" + path.name, args.mac)
                if companion["device"] == identity["device"]:
                    continue
                extra = os.open(companion["device"], os.O_RDONLY | os.O_NONBLOCK)
                companion_fds.append(extra)
                fcntl.ioctl(extra, 0x40044590, 1)
            except FileNotFoundError:
                continue
        clock_name = "unspecified"
        try:
            fcntl.ioctl(fd, 0x400445A0, struct.pack("i", time.CLOCK_MONOTONIC))
            clock_name = "monotonic"
        except OSError:
            pass
        # Drop input queued before this test became ready.
        try:
            while os.read(fd, 4096):
                pass
        except BlockingIOError:
            pass
        if touch is not None:
            for axis, attribute in ((53, "x"), (54, "y")):
                state = bytearray(struct.pack("2i", axis, 0))
                fcntl.ioctl(fd, 0x8008450a, state, True)  # EVIOCGMTSLOTS, slot zero.
                setattr(touch, attribute, struct.unpack("2i", state)[1])
        started = time.monotonic()
        deadline = started + args.seconds
        emit({"kind": "start", "hostname": args.hostname, "core_pid": initial_pid,
              "remote": identity, "input_clock": clock_name, "inspect": args.inspect,
              "duration_s": args.seconds, "interval_s": args.interval,
              "tag": args.tag, "distance_cm": args.distance_cm, "channel": args.channel,
              "input_mode": args.input_mode,
              "key_mapping": mapping if not args.inspect and touch is None else {},
              "gesture_mapping": touch_mapping if not args.inspect and touch is not None else {},
              "ready": True})
        if baseline is not None:
            emit({"kind": "sample", "elapsed_s": 0, "status": baseline, "baseline": True})

        def poll_status():
            while not stop.is_set() and time.monotonic() < deadline:
                before = time.monotonic()
                try:
                    status = get_status()
                    emit({"kind": "sample", "elapsed_s": time.monotonic() - started,
                          "request_start_mono": before,
                          "request_ms": (time.monotonic() - before) * 1000, "status": status})
                except Exception as error:
                    emit({"kind": "status_error", "error": str(error)})
                stop.wait(max(0, args.interval - (time.monotonic() - before)))

        if not args.inspect:
            poller = threading.Thread(target=poll_status, daemon=True)
            poller.start()
        event = struct.Struct("llHHi")
        pending = b""
        while not stop.is_set() and time.monotonic() < deadline:
            readable, _, _ = select.select([fd], [], [], min(0.2, max(0, deadline - time.monotonic())))
            if not readable:
                continue
            chunk = os.read(fd, event.size * 64)
            if not chunk:
                raise RuntimeError("Bluetooth remote disconnected")
            pending += chunk
            while len(pending) >= event.size:
                sec, usec, event_type, code, value = event.unpack(pending[:event.size])
                pending = pending[event.size:]
                # SYN_DROPPED means button/mark loss: do not present this as a complete test.
                if event_type == 0 and code == 3:
                    raise RuntimeError("Input events were lost (SYN_DROPPED); repeat this test")
                received = time.monotonic()
                event_time = sec + usec / 1_000_000
                mark_time = event_time if clock_name == "monotonic" else received
                if touch is None:
                    if event_type != 1:
                        continue
                    emit({"kind": "key", "code": code, "value": value,
                          "input_time": event_time, "received_mono": received})
                    action = presses.update(event_type, code, value)
                else:
                    emit({"kind": "input", "type": event_type, "code": code, "value": value,
                          "input_time": event_time, "received_mono": received})
                    gesture = touch.update(event_type, code, value, mark_time)
                    if gesture is None:
                        continue
                    emit(gesture)
                    action = touch_mapping.get(gesture["gesture"])
                    mark_time = gesture["start_mono"]
                if action and not args.inspect:
                    for record in marks.mark(action, mark_time):
                        emit(record)
    finally:
        stop.set()
        os.close(fd)
        for extra in companion_fds:
            os.close(extra)
        if poller is not None:
            poller.join(timeout=3)
        if marks.current is not None:
            emit(marks.finish("unlabelled", time.monotonic()))
    counts = {key: sum(p["human_result"] == key for p in marks.completed)
              for key in ("hit", "miss", "unlabelled")}
    emit({"kind": "end", "core_pid": core_pid(), "human_counts": counts,
          "stop_requested": stop.is_set() and time.monotonic() < deadline,
          "limitations": "Human labels establish passage outcomes. API snapshots are sampled; "
                         "detector event counts are not passage counts or exact alarm timing."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--mac", required=True)
    parser.add_argument("--inspect", action="store_true", help="Inspect input without pass marks")
    parser.add_argument("--input-mode", choices=("keys", "touch"), default="keys")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--tag")
    parser.add_argument("--distance-cm", type=float)
    parser.add_argument("--channel", choices=("upper", "lower"), default="upper")
    parser.add_argument("--start-code", type=int)
    parser.add_argument("--hit-code", type=int)
    parser.add_argument("--miss-code", type=int)
    for action in ("start", "hit", "miss"):
        parser.add_argument("--touch-" + action, choices=("tap", "x+", "x-", "y+", "y-"))
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600 or not 0.1 <= args.interval <= 5:
        parser.error("seconds must be 1..3600 and interval 0.1..5")
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", args.mac):
        parser.error("Use the paired remote's Bluetooth MAC address")
    codes = (args.start_code, args.hit_code, args.miss_code)
    gestures = (args.touch_start, args.touch_hit, args.touch_miss)
    if not args.inspect and (not args.tag or args.distance_cm is None or args.distance_cm <= 0
            or (args.input_mode == "keys" and
                (any(code is None or not 1 <= code <= 767 for code in codes) or len(set(codes)) != 3))
            or (args.input_mode == "touch" and (None in gestures or len(set(gestures)) != 3))):
        parser.error("Recording requires tag, positive distance, and three distinct keys or calibrated gestures")
    try:
        run(args)
    except Exception as error:
        print(json.dumps({"kind": "fatal", "epoch": time.time(), "error": str(error)}), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
