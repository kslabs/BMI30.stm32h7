#!/usr/bin/env python3
"""Recover a lost station link without restarting NetworkManager or the AP."""

import argparse
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time


LOG = logging.getLogger("bmi30-wifi")
POLL_SECONDS = 20
FAILURE_LIMIT = 3
RETRY_SECONDS = 180
SERVICE_NAME = "bmi30-wifi-watchdog.service"
SERVICE_UNIT = """[Unit]
Description=BMI30 local Wi-Fi link recovery
After=NetworkManager.service bmi30-hotspot.service
Wants=NetworkManager.service

[Service]
Type=simple
EnvironmentFile=/etc/default/bmi30-wifi-watchdog
ExecStart=/usr/bin/python3 /usr/local/sbin/bmi30-wifi-watchdog.py
Restart=always
RestartSec=10
RuntimeDirectory=bmi30-wifi
LogsDirectory=bmi30-wifi
UMask=0027

[Install]
WantedBy=multi-user.target
"""


def install(config_path, interface="wlan0", *, system_root=Path("/"), runner=None):
    """Install this release's known Wi-Fi component; no incoming shell scripts.

    Called by the updated portal, so older server clients can deploy the fix.
    Device config and credentials are never copied from the release.
    """
    runner = runner or command
    config_path = Path(config_path).resolve()
    if not isinstance(json.loads(config_path.read_text()), dict):
        raise ValueError("Portal configuration must be an object")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
        raise ValueError("Invalid station interface")
    config = str(config_path)
    if "\n" in config or "\r" in config:
        raise ValueError("Invalid configuration path")
    config = config.replace("\\", "\\\\").replace('"', '\\"')
    env = f'BMI30_CONFIG_JSON="{config}"\nBMI30_WIFI_STA_IFACE={interface}\n'
    nm_config = f"[connection-bmi30-station]\nmatch-device=interface-name:{interface}\nwifi.powersave=2\n"
    contents = {
        "usr/local/sbin/bmi30-wifi-watchdog.py": (Path(__file__).read_bytes(), 0o755),
        f"etc/systemd/system/{SERVICE_NAME}": (SERVICE_UNIT.encode(), 0o644),
        "etc/default/bmi30-wifi-watchdog": (env.encode(), 0o644),
        "etc/NetworkManager/conf.d/90-bmi30-wifi-reliability.conf": (nm_config.encode(), 0o644),
    }
    identity = hashlib.sha256(b"".join(data for data, _mode in contents.values())).hexdigest()
    receipt = Path(system_root) / "var/lib/bmi30-wifi/installed.sha256"
    installed_identity = receipt.read_text().strip() if receipt.is_file() else ""
    changed = []
    for name, (data, mode) in contents.items():
        path = Path(system_root) / name
        if path.is_file() and path.read_bytes() == data:
            path.chmod(mode)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        changed.append(name)

    def run(*args):
        result = runner(*args, timeout=20)
        if result.returncode:
            raise RuntimeError(f"Wi-Fi setup failed: {' '.join(args)}: {result.stderr.strip()}")

    # Also run these on a retry after partial setup, even if files now match.
    run("nmcli", "general", "reload", "conf")
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", SERVICE_NAME)
    # Do not restart a healthy watchdog on every portal restart.
    run("systemctl", "restart" if changed or installed_identity != identity else "start", SERVICE_NAME)
    run("systemctl", "is-active", "--quiet", SERVICE_NAME)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary_receipt = receipt.with_suffix(".tmp")
    temporary_receipt.write_text(identity + "\n")
    temporary_receipt.replace(receipt)
    return {"changed": changed, "service": SERVICE_NAME}


def command(*args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "LC_ALL": "C"})


def fields(*args):
    result = command("nmcli", "--escape", "no", "-t", *args)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "NetworkManager query failed")
    return dict(line.split(":", 1) for line in result.stdout.splitlines() if ":" in line)


class Network:
    def __init__(self, interface):
        self.interface = interface

    def status(self):
        data = fields("-f", "GENERAL.STATE,GENERAL.CON-UUID,GENERAL.AUTOCONNECT,IP4.GATEWAY",
                      "device", "show", self.interface)
        return {
            "state": int(data["GENERAL.STATE"].split()[0]),
            "uuid": data.get("GENERAL.CON-UUID", "--"),
            "autoconnect": data.get("GENERAL.AUTOCONNECT") == "yes",
            "gateway": data.get("IP4.GATEWAY", "--"),
        }

    def profile(self, uuid):
        return fields("-f", "connection.uuid,connection.type,connection.interface-name,"
                      "connection.autoconnect,connection.autoconnect-priority,"
                      "802-11-wireless.mode,802-11-wireless.ssid",
                      "connection", "show", "uuid", uuid)

    def eligible(self, profile):
        return (profile.get("connection.type") == "802-11-wireless"
                and profile.get("802-11-wireless.mode") == "infrastructure"
                and profile.get("connection.interface-name") in (self.interface, "--", "")
                and profile.get("connection.autoconnect") == "yes")

    def saved_profile(self, ssid):
        if not ssid:
            return ""
        result = command("nmcli", "-g", "UUID", "connection", "show")
        if result.returncode:
            raise RuntimeError("Cannot list saved Wi-Fi profiles")
        candidates = []
        for uuid in result.stdout.splitlines():
            profile = self.profile(uuid)
            if self.eligible(profile) and profile.get("802-11-wireless.ssid") == ssid:
                candidates.append((int(profile["connection.autoconnect-priority"]), uuid))
        return max(candidates)[1] if candidates else ""

    def gateway_healthy(self, gateway):
        # No internet probe: a WAN outage must not restart a working LAN link.
        if gateway in ("", "--"):
            return True  # IPv6-only / intentionally gateway-less LAN.
        result = command("ping", "-n", "-I", self.interface, "-c", "1", "-W", "2",
                         gateway, timeout=4)
        if result.returncode == 0:
            return True
        if result.returncode != 1:
            raise RuntimeError("Gateway probe could not run")
        # An ICMP-blocking router is healthy if ARP still confirms reachability.
        # STALE/DELAY/PROBE are not proof; successive probes let NUD resolve them.
        result = command("ip", "-j", "neigh", "show", "to", gateway, "dev", self.interface)
        if result.returncode:
            raise RuntimeError("Cannot read gateway neighbour state")
        return any(set(row.get("state", [])) & {"REACHABLE", "PERMANENT"}
                   for row in json.loads(result.stdout))

    def reconnect(self, uuid):
        # Reuse NM's saved credentials. Never down the radio or wlan0ap.
        return command("nmcli", "--wait", "40", "connection", "up", "uuid", uuid,
                       "ifname", self.interface, timeout=45)

    def prepare_station(self, uuid):
        profile = self.profile(uuid)
        if not self.eligible(profile):
            return
        settings = fields("-f", "802-11-wireless.powersave,connection.autoconnect-retries",
                          "connection", "show", "uuid", uuid)
        if (settings["802-11-wireless.powersave"] not in ("disable", "2", "2 (disable)")
                or settings["connection.autoconnect-retries"] != "0"):
            result = command("nmcli", "connection", "modify", "uuid", uuid,
                             "802-11-wireless.powersave", "2", "connection.autoconnect-retries", "0")
            if result.returncode:
                raise RuntimeError("Cannot save station reliability settings")
        result = command("iw", "dev", self.interface, "set", "power_save", "off")
        if result.returncode:
            raise RuntimeError("Cannot disable station power saving")

    def diagnostics(self):
        return {
            "station": command("iw", "dev", self.interface, "station", "dump").stdout.strip(),
            "neighbours": command("ip", "-j", "neigh", "show", "dev", self.interface).stdout.strip(),
        }


class Watchdog:
    def __init__(self, network, load_config, clock=time.monotonic):
        self.network = network
        self.load_config = load_config
        self.clock = clock
        self.failures = 0
        self.key = None
        self.last_uuid = ""
        self.next_retry = 0.0
        self.last_report = None
        self.prepared_uuid = ""

    def report(self, label, **details):
        if label != self.last_report:
            LOG.info("%s %s", label, json.dumps(details, ensure_ascii=False))
            self.last_report = label
        return {"status": label, "failures": self.failures, **details}

    def reset(self, label, **details):
        self.failures = 0
        self.key = None
        return self.report(label, **details)

    def check(self):
        import sys
        if '/opt/bmi30-network' not in sys.path: sys.path.insert(0, '/opt/bmi30-network')
        import access_policy
        if access_policy.offline_mode() or access_policy.maintenance() or access_policy.ap_clients():
            return self.reset('local-access-priority')
        config = self.load_config()  # Malformed/unreadable config: fail closed.
        if not (config.get("channel_permissions") or {}).get("wifi", True):
            return self.reset("disabled-by-user")
        status = self.network.status()
        state = status["state"]
        if state != 100:
            self.prepared_uuid = ""
        if not status["autoconnect"] or state < 30:
            return self.reset("disabled-or-unavailable")
        if state not in (30, 100, 120):
            return self.reset("activation-in-progress")
        if state == 100:
            if self.prepared_uuid != status["uuid"]:
                self.network.prepare_station(status["uuid"])
                self.prepared_uuid = status["uuid"]
            if self.network.gateway_healthy(status["gateway"]):
                self.last_uuid = status["uuid"]
                return self.reset("healthy", **status)
        key = (state, status["uuid"], status["gateway"])
        if key != self.key:
            self.failures = 0
            self.key = key
        self.failures += 1
        if self.failures == 1:
            try:
                LOG.warning("Link check failed; state=%s gateway=%s diagnostics=%s",
                            state, status["gateway"], self.network.diagnostics())
            except Exception as exc:
                LOG.warning("Link diagnostics unavailable: %s", exc)
        if self.failures < FAILURE_LIMIT:
            return self.report("link-check-failed", **status)
        if self.clock() < self.next_retry:
            return self.report("recovery-cooldown", **status)

        uuid = status["uuid"] if state == 100 else self.last_uuid
        if not uuid or uuid == "--":
            saved = config.get("wifi_internet") or {}
            uuid = self.network.saved_profile(saved.get("ssid", ""))
        if not uuid or not self.network.eligible(self.network.profile(uuid)):
            return self.reset("no-enabled-saved-profile")
        # Recheck after probes/profile lookup: do not race a portal disconnect
        # or an activation already started by NetworkManager/the user.
        if (not (self.load_config().get("channel_permissions") or {}).get("wifi", True)
                or self.network.status() != status):
            return self.reset("state-changed")
        if state == 100 and self.network.gateway_healthy(status["gateway"]):
            return self.reset("healthy", **status)
        self.next_retry = self.clock() + RETRY_SECONDS
        LOG.warning("Reactivating station profile %s after %d failures; state=%s gateway=%s",
                    uuid, self.failures, state, status["gateway"])
        result = self.network.reconnect(uuid)
        self.failures = 0
        self.key = None
        LOG.warning("Station reactivation result=%s %s", result.returncode,
                    (result.stdout or result.stderr).strip())
        return self.report("reactivated" if result.returncode == 0 else "reactivation-failed",
                           uuid=uuid, returncode=result.returncode)


def main():
    interface = os.environ.get("BMI30_WIFI_STA_IFACE", "wlan0")
    config_path = Path(os.environ["BMI30_CONFIG_JSON"])
    log_dir = Path("/var/log/bmi30-wifi")
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "watchdog.log", maxBytes=1024 * 1024,
                                  backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), handler])
    state_path = Path("/run/bmi30-wifi/status.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    watchdog = Watchdog(Network(interface), lambda: json.loads(config_path.read_text()))
    LOG.info("Started: interface=%s interval=%ss threshold=%s cooldown=%ss",
             interface, POLL_SECONDS, FAILURE_LIMIT, RETRY_SECONDS)
    while True:
        try:
            status = watchdog.check()
        except Exception as exc:
            status = watchdog.reset("check-unavailable", error=str(exc))
        status["checked_at"] = int(time.time())
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, ensure_ascii=False) + "\n")
        temporary.replace(state_path)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path, metavar="PORTAL_CONFIG")
    parser.add_argument("--interface", default="wlan0")
    args = parser.parse_args()
    if args.install:
        print(json.dumps(install(args.install, args.interface)))
    else:
        main()
