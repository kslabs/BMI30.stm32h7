"""Read-only CM5 antenna-selection check; does not detect an attached RF cable."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time


def antenna_state(pins: str) -> dict:
    states = {}
    for line in pins.splitlines():
        match = re.match(r"\s*\d+:[^\n]*\|\s+(hi|lo)\s*//\s*(ANT[12])/", line)
        if match:
            level, name = match.groups()
            if name in states:
                raise ValueError("Duplicate antenna control: " + name)
            states[name] = level == "hi"
    if set(states) != {"ANT1", "ANT2"}:
        raise ValueError("Both ANT1 and ANT2 must be readable")
    return {"ant1": states["ANT1"], "ant2": states["ANT2"],
            "external_selected": not states["ANT1"] and states["ANT2"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"hostname": socket.gethostname(), "epoch": time.time(),
              "scope": "antenna_selection_only", "physical_connection_checked": False}
    try:
        model = Path("/proc/device-tree/model").read_bytes().rstrip(b"\x00").decode()
        if not model.startswith("Raspberry Pi Compute Module 5"):
            raise ValueError("Unsupported model: " + model)
        pins = subprocess.check_output(["/usr/bin/pinctrl", "get"], text=True,
                                       stderr=subprocess.STDOUT, timeout=8)
        result.update(model=model, **antenna_state(pins))
        result["boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        result["config_sha256"] = hashlib.sha256(Path("/boot/firmware/config.txt").read_bytes()).hexdigest()
        result["ok"] = result["external_selected"]
        if not result["ok"]:
            result["error"] = "External antenna output is not selected; check dtparam=ant2 and reboot"
    except Exception as error:
        result.update(ok=False, error=str(error))
    payload = json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name + ".new")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, args.output)
    print(payload, end="", flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
