"""Linux/NetworkManager adapter. Credentials never enter logs or argv."""
from __future__ import annotations

import concurrent.futures
import configparser
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import uuid


def hardware_identity():
    """Derive the network identity from the current CM5, even after a USB move."""
    paths = (Path('/proc/device-tree/serial-number'),
             Path('/sys/firmware/devicetree/base/serial-number'))
    for path in paths:
        try:
            serial = path.read_text().strip('\x00 \t\r\n')
        except (OSError, UnicodeError):
            continue
        if re.fullmatch(r'[0-9a-fA-F]{16}', serial) and int(serial, 16):
            return 'BMI30-' + serial[-9:].upper()
    try:
        cpuinfo = Path('/proc/cpuinfo').read_text()
    except (OSError, UnicodeError):
        cpuinfo = ''
    match = re.search(r'^Serial\s*:\s*([0-9a-fA-F]{16})\s*$', cpuinfo, re.MULTILINE)
    if match and int(match.group(1), 16):
        return 'BMI30-' + match.group(1)[-9:].upper()
    # Cloned hostnames, machine-id and removable-media serials are not identities.
    raise RuntimeError('Raspberry Pi hardware serial is unavailable or invalid')


def run(*args, timeout=12, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    if check and result.returncode:
        # nmcli errors can contain SSIDs or secrets. Keep journal errors generic.
        raise RuntimeError(f"{args[0]} failed ({result.returncode})")
    return result.stdout.strip()


def split_terse(line):
    fields, value, escape = [], "", False
    for char in line:
        if escape:
            value += char
            escape = False
        elif char == "\\":
            escape = True
        elif char == ":":
            fields.append(value)
            value = ""
        else:
            value += char
    fields.append(value)
    return fields


def atomic(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(temp, mode)
    os.replace(temp, path)


def nm_fields(kind, identity, names, secrets=False):
    args = ["nmcli", "-t"] + (["--show-secrets"] if secrets else [])
    output = run(*args, "-f", ",".join(names), kind, "show", identity)
    return {parts[0]: ":".join(parts[1:]) for line in output.splitlines()
            if len(parts := split_terse(line)) >= 2}


def nm_value(profile, name, secrets=False):
    return nm_fields("connection", profile, [name], secrets).get(name, "")


def probe(interface):
    # TLS and an exact 204 reject captive portals. Two independent providers;
    # no user data is sent. The interface binding prevents an alternate route
    # from falsely making a failed Wi-Fi uplink appear healthy.
    def one(url):
        try:
            start = time.monotonic()
            value = run("curl", "--noproxy", "*", "--interface", interface,
                        "--proto", "=https", "--connect-timeout", "3", "--max-time", "5",
                        "-s", "-o", "/dev/null", "-w", "%{http_code}", url, timeout=7)
            return value == "204", (time.monotonic() - start) * 1000
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            return False, 5000
    urls = ("https://www.gstatic.com/generate_204", "https://cp.cloudflare.com/generate_204")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, urls))
    good = [ms for ok, ms in results if ok]
    return {"ok": bool(good), "rtt_ms": round(min(good), 1) if good else 5000,
            "loss": 1 - len(good) / len(results), "checks": len(results)}


class Checkpoint:
    """NM owns the timeout: a killed process cannot strand the old connection."""
    def __init__(self, interfaces=("wlan0", "wlan0ap"), timeout=150):
        import dbus
        self.dbus = dbus
        self.nm = dbus.Interface(dbus.SystemBus().get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"),
            "org.freedesktop.NetworkManager")
        paths = [self.nm.GetDeviceByIpIface(x) for x in interfaces]
        self.path = self.nm.CheckpointCreate(dbus.Array(paths, signature="o"),
                                             dbus.UInt32(timeout), dbus.UInt32(0))

    def commit(self):
        self.nm.CheckpointDestroy(self.path)

    def rollback(self):
        result = self.nm.CheckpointRollback(self.path)
        try:
            self.nm.CheckpointDestroy(self.path)
        except Exception:
            pass
        if any(int(x) != 0 for x in result.values()):
            raise RuntimeError("NetworkManager rollback incomplete")


def key_string(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


class Network:
    def __init__(self, config):
        self.config = config
        self.sta = config.get("sta", "wlan0")
        self.ap = config.get("ap", "wlan0ap")

    def permissions(self):
        path = Path(self.config["portal_config"])
        return json.loads(path.read_text()).get("channel_permissions", {})

    def active(self, interface):
        return nm_fields("device", interface, ["GENERAL.CON-UUID"]).get("GENERAL.CON-UUID", "--")

    def profiles(self):
        result = {}
        ranks = {}
        active = self.active(self.sta)
        names = ["connection.id", "802-11-wireless.mode", "802-11-wireless.ssid",
                 "802-11-wireless-security.key-mgmt", "802-11-wireless-security.psk", "connection.autoconnect-priority"]
        for line in run("nmcli", "-t", "-f", "UUID,TYPE", "con", "show").splitlines():
            ident, kind = split_terse(line)
            if kind != "802-11-wireless":
                continue
            p = nm_fields("connection", ident, names, secrets=True)
            ssid = p.get("802-11-wireless.ssid", "")
            security = p.get("802-11-wireless-security.key-mgmt", "")
            if (p.get("802-11-wireless.mode") != "infrastructure" or ssid.upper().startswith("BMI30")
                    or (p.get("connection.id", "").startswith("bmi30-net-") and ident != active)
                    or security not in ("wpa-psk", "sae")):
                continue
            psk = p.get("802-11-wireless-security.psk", "")
            if 8 <= len(psk) <= 64:
                try:priority=int(p.get('connection.autoconnect-priority','0'))
                except ValueError:priority=0
                rank=(ident==active,priority)
                if ssid not in ranks or rank>ranks[ssid]:
                    ranks[ssid]=rank
                    result[ssid] = {"ssid": ssid, "psk": psk, "security": security, "uuid": ident}
        return result

    def visible(self, rescan=False):
        if rescan:
            run("nmcli", "--wait", "5", "device", "wifi", "rescan", "ifname", self.sta, check=False)
        result = []
        output = run("nmcli", "-t", "-f", "SSID,BSSID,FREQ,CHAN,SIGNAL,SECURITY", "device", "wifi",
                     "list", "ifname", self.sta, "--rescan", "no")
        for line in output.splitlines():
            values = split_terse(line)
            if len(values) == 6 and values[2].split()[0].isdigit():
                result.append(dict(zip(("ssid", "bssid", "freq", "channel", "signal", "security"),
                                       (values[0], values[1].lower(), int(values[2].split()[0]),
                                        int(values[3]), int(values[4]), values[5]))))
        return result[:256]

    def snapshot(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1/api/status", timeout=4) as response:
            portal = json.load(response)
        code = portal.get("sync_mode", {}).get("code", "")
        mode = portal.get("sync_mode", {})
        role = mode.get("value", "unknown") if mode.get("device_responded") else "unknown"
        link = run("iw", "dev", self.sta, "link", check=False)
        apinfo = run("iw", "dev", self.ap, "info", check=False)
        get = lambda text, pattern, default="": (m.group(1) if (m := re.search(pattern, text)) else default)
        signal_dbm = int(get(link, r"signal: (-?\d+)", "-100"))
        apssid = get(apinfo, r"\bssid (.+)")
        routes = json.loads(run("ip", "-j", "route"))
        defaults = sorted([r for r in routes if r.get("dst") == "default" and "linkdown" not in r.get("flags", [])],
                          key=lambda r: r.get("metric", 0))
        iface = defaults[0]["dev"] if defaults else self.sta
        addresses, broadcasts = [], []
        for row in json.loads(run("ip", "-j", "-4", "address")):
            for addr in row.get("addr_info", []):
                if addr.get("scope") == "global":
                    addresses.append(addr["local"])
                    if addr.get("broadcast"):
                        broadcasts.append(addr["broadcast"])
        ap_addresses = [r["prefsrc"] + "/" + r["dst"].split("/")[-1]
                        for r in routes if r.get("dev") == self.ap and r.get("prefsrc") and "/" in r["dst"]]
        return {"id": self.config["id"], "role": role,
                "stm32_uid96": portal.get("group_state", {}).get("stm32_uid96", ""),
                "rs485_id": int(code[1:]) if re.fullmatch(r"[MS]\d+", code) else 255,
                "code": code, "ssid": get(link, r"SSID: (.+)"),
                "bssid": get(link, r"Connected to ([0-9a-f:]+)").lower(),
                "freq": int(get(link, r"freq: (\d+)", "0")), "rssi_dbm": signal_dbm,
                "signal": max(0, min(100, 2 * (signal_dbm + 100))),
                "iface": iface, "uplink": "wifi" if iface == self.sta else "ethernet",
                "gateway": defaults[0].get("gateway") if defaults else None,
                "addresses": addresses, "broadcasts": broadcasts,
                "ap": {"ssid": apssid, "bssid": get(apinfo, r"addr ([0-9a-f:]+)").lower(),
                       "address": ap_addresses[0] if ap_addresses else self.config["ap_address"]},
                "clients": run("iw", "dev", self.ap, "station", "dump", check=False).count("Station "),
                "station_uuid": self.active(self.sta), "ap_uuid": self.active(self.ap)}

    def profile(self, choice, secret):
        name = "bmi30-net-" + choice["bssid"].replace(":", "")
        ident = str(uuid.uuid5(uuid.NAMESPACE_DNS, self.config["id"] + ":" + name))
        data = {
            "connection": {"id": name, "uuid": ident, "type": "wifi", "interface-name": self.sta,
                           "autoconnect": "false"},
            "wifi": {"mode": "infrastructure", "ssid": key_string(choice["ssid"]),
                     "bssid": choice["bssid"], "band": "a" if choice["freq"] >= 4900 else "bg", "powersave": "2"},
            "wifi-security": {"key-mgmt": secret.get("security", "wpa-psk"), "psk": key_string(secret["psk"])},
            "ipv4": {"method": "auto", "route-metric": "600"}, "ipv6": {"method": "disabled"}}
        text = "\n".join("[" + section + "]\n" + "\n".join(f"{k}={v}" for k, v in fields.items())
                         + "\n" for section, fields in data.items())
        path = "/etc/NetworkManager/system-connections/" + name + ".nmconnection"
        atomic(path, text)
        run("nmcli", "con", "load", path)
        return ident

    def activate(self, choice, secret, verify_peer=None, commit=True, require_internet=True):
        old = self.snapshot()
        ident = self.profile(choice, secret)
        checkpoint = Checkpoint((self.sta, self.ap))
        finalized = False
        try:
            # One PHY/one channel: release AP before changing the STA channel.
            run("nmcli", "--wait", "10", "con", "down", "uuid", old["ap_uuid"], check=False)
            run("nmcli", "--wait", "35", "con", "up", "uuid", ident, "ifname", self.sta, timeout=40)
            run("nmcli", "con", "modify", "uuid", old["ap_uuid"], "wifi.band",
                "a" if choice["freq"] >= 4900 else "bg", "wifi.channel", str(choice["channel"]))
            run("nmcli", "--wait", "20", "con", "up", "uuid", old["ap_uuid"], timeout=25)
            current = self.snapshot()
            if current["bssid"] != choice["bssid"].lower():
                raise RuntimeError("Associated BSSID differs from selected route")
            if verify_peer:
                verify_peer(current["gateway"])
            health = probe(self.sta)
            if require_internet and not health["ok"]:
                raise RuntimeError("Candidate has no verified internet access")
            if commit:
                checkpoint.commit()
                finalized = True
            else:
                checkpoint.rollback()
                finalized = True
                self.wait_restored(old)
            # Managed relay profiles intentionally stay autoconnect=false:
            # after reboot the daemon authenticates the parent path first.
            return health
        except Exception:
            if not finalized:
                checkpoint.rollback()
                self.wait_restored(old)
            raise

    def wait_restored(self, old):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            link = run('iw', 'dev', self.sta, 'link', check=False).lower()
            if (self.active(self.sta) == old['station_uuid']
                    and (not old.get('bssid') or old['bssid'].lower() in link)):
                return
            time.sleep(1)
        raise RuntimeError('Previous station is still reconnecting after rollback')

    def remote_desktop_active(self):
        # A network change can exceed the native XRDP reconnect timeout.
        # Keep healthy links in place while an interactive session uses them.
        return bool(run("ss", "-Htn", "state", "established", "sport", "=", ":3389", check=False))

    def prepare_ap(self):
        active = self.active(self.ap)
        if active == "--":
            raise RuntimeError("Local AP unavailable")
        address = nm_value(active, "ipv4.addresses")
        dns_path = Path('/etc/NetworkManager/dnsmasq-shared.d/90-bmi30-captive-portal.conf')
        old_dns = dns_path.read_text() if dns_path.exists() else None
        dns = ('# BMI30 service network: ordinary DNS is forwarded upstream.\n'
               'address=/bmi30.local/' + self.config['ap_address'].split('/')[0] + '\n'
               'address=/' + self.config['id'].lower() + '.local/' + self.config['ap_address'].split('/')[0] + '\n')
        if address == self.config["ap_address"] and old_dns == dns:
            return False
        if run("iw", "dev", self.ap, "station", "dump", check=False).count("Station "):
            raise RuntimeError("AP subnet migration deferred: connected hotspot clients")
        checkpoint = Checkpoint((self.sta, self.ap))
        try:
            backup = Path('/var/lib/bmi30-network/original-captive-dns.conf')
            if old_dns is not None and not backup.exists():
                atomic(backup, old_dns)
            atomic(dns_path, dns, 0o644)
            run("nmcli", "con", "modify", "uuid", active, "ipv4.method", "shared",
                "ipv4.addresses", self.config["ap_address"])
            run("nmcli", "--wait", "20", "con", "up", "uuid", active, timeout=25)
            snapshot = self.snapshot()
            if snapshot["ap"]["address"] != self.config["ap_address"]:
                raise RuntimeError("AP address verification failed")
            checkpoint.commit()
            return True
        except Exception:
            if old_dns is not None:
                atomic(dns_path, old_dns, 0o644)
            else:
                dns_path.unlink(missing_ok=True)
            checkpoint.rollback()
            raise
