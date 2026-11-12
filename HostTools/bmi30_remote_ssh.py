#!/usr/bin/env python3
"""Discover local BMI30 portals and open authenticated boards in VS Code.

Windows/Python standard library only. No firmware, acquisition or service changes.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

NAME = re.compile(r"BMI30[A-Za-z0-9-]{0,57}\Z", re.IGNORECASE)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BMI30-RemoteSSH"
SSH_ROOT = Path.home() / ".ssh"
SSH = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH/ssh.exe"
POWERSHELL = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


def log(message):
    print(time.strftime("%H:%M:%S"), message, flush=True)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == value:
        return
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=path.name + ".",
                                     suffix=".tmp", dir=path.parent, delete=False) as stream:
        stream.write(value)
        temp = Path(stream.name)
    os.replace(temp, path)


def powershell_json(script):
    result = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    value = json.loads(result.stdout or "[]")
    return value if isinstance(value, list) else [value]


def local_candidates():
    """At most 1022 addresses/interface; never scan public or wide networks."""
    interfaces = powershell_json(
        "@(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {"
        "$_.AddressState -eq 'Preferred' -and $_.IPAddress -notlike '127.*' -and "
        "$_.IPAddress -notlike '169.254.*'} | Select-Object IPAddress,PrefixLength) | ConvertTo-Json -Compress")
    addresses = set()
    for interface in interfaces:
        address = ipaddress.IPv4Address(interface["IPAddress"])
        network = ipaddress.IPv4Network(f"{address}/{interface['PrefixLength']}", strict=False)
        if not address.is_private or network.prefixlen < 22:
            log(f"Skipping non-private or large subnet: {network}")
            continue
        addresses.update(str(item) for item in network.hosts() if item != address)
    return sorted(addresses, key=ipaddress.IPv4Address)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def portal(ip):
    try:
        with socket.create_connection((ip, 80), timeout=0.3):
            pass
        # Direct LAN requests only; do not forward through system HTTP proxies.
        request = urllib.request.Request(f"http://{ip}/api/status", headers={"User-Agent": "BMI30-RemoteSSH/1"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=3) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            return None
        status = json.loads(raw)
        name = str(status.get("hostname", ""))
        if not NAME.fullmatch(name):
            return None
        services = status.get("services", {})
        # User and SSH port are local policy, never commands supplied by a portal.
        if services.get("ssh", 22) != 22:
            return None
        return {"name": name.upper(), "ip": str(ipaddress.IPv4Address(ip)), "seen": int(time.time())}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def unique_devices(results):
    by_name = {}
    for item in results:
        if item:
            by_name.setdefault(item["name"], []).append(item)
    found = {}
    for name, rows in by_name.items():
        ips = {item["ip"] for item in rows}
        if len(ips) != 1:
            log(f"Duplicate hostname {name}: {sorted(ips)}; connection skipped")
            continue
        found[name] = rows[0]
    return found


def discover():
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
        return unique_devices(pool.map(portal, local_candidates()))


def render_config(devices, key, known_hosts):
    lines = ["# Managed by BMI30 Remote SSH. Discovery does not establish SSH trust."]
    for name, device in sorted(devices.items()):
        if not NAME.fullmatch(name):
            raise ValueError("Invalid hostname")
        ip = str(ipaddress.IPv4Address(device["ip"]))
        lines += [f"Host {name}", f"    HostName {ip}", "    User techaid", "    Port 22",
                  f'    IdentityFile "{key.as_posix()}"', "    IdentitiesOnly yes",
                  f"    HostKeyAlias {name.lower()}", f'    UserKnownHostsFile "{known_hosts.as_posix()}"',
                  "    StrictHostKeyChecking ask", "    CheckHostIP no",
                  "    ServerAliveInterval 15", "    ServerAliveCountMax 3", "    ForwardAgent no", ""]
    return "\n".join(lines) + "\n"


def refresh():
    devices = discover()
    write_text(ROOT / "devices.json", json.dumps(devices, indent=2) + "\n")
    # Retain offline aliases for Remote Explorer; only CURRENT devices get opened.
    saved = read_json(ROOT / "inventory.json", {})
    saved.update(devices)
    write_text(ROOT / "inventory.json", json.dumps(saved, indent=2) + "\n")
    write_text(SSH_ROOT / "bmi30_config", render_config(saved, SSH_ROOT / "id_ed25519_bmi30",
                                                     SSH_ROOT / "bmi30_known_hosts"))
    return devices


def ssh_args(name, batch=True):
    if not NAME.fullmatch(name):
        raise ValueError("Invalid hostname")
    args = [str(SSH), "-F", str(SSH_ROOT / "bmi30_config")]
    if batch:
        args += ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5"]
    return args + [name]


def authenticated(name):
    try:
        result = subprocess.run(ssh_args(name) + ["hostname"], capture_output=True, text=True,
                                timeout=9, creationflags=NO_WINDOW)
        if result.returncode:
            return False, result.stderr.strip().splitlines()[-1:]
        actual = result.stdout.strip().upper()
        return actual == name.upper(), [f"SSH hostname: {actual}"]
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, [str(error)]


def install_key(name):
    public = (SSH_ROOT / "id_ed25519_bmi30.pub").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+ [A-Za-z0-9_.@-]+", public):
        raise ValueError("Unexpected public key format")
    # Double-check hostname over authenticated SSH before adding the public key.
    command = (f"test \"$(hostname | tr '[:lower:]' '[:upper:]')\" = {shlex.quote(name.upper())} || exit 31; "
               "umask 077; mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
               "chmod 600 ~/.ssh/authorized_keys && "
               f"(grep -qF -- {shlex.quote(public.split()[1])} ~/.ssh/authorized_keys || "
               f"printf '\\n%s\\n' {shlex.quote(public)} >> ~/.ssh/authorized_keys)")
    # Inherit the user's console. Passwords are read by OpenSSH, never by Python.
    return subprocess.call(ssh_args(name, batch=False) + [command]) == 0


def open_board(name, settings):
    uri = f"vscode-remote://ssh-remote+{name}{settings['remote_folder']}"
    # Use the actual CLI JS instead of shell-quoting a .cmd path containing spaces.
    env = os.environ.copy()
    env["ELECTRON_RUN_AS_NODE"] = "1"
    code_exe, code_cli = code_paths(settings["code_cmd"])
    result = subprocess.run([str(code_exe), str(code_cli), "--new-window", "--folder-uri", uri],
                            env=env, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())


def code_paths(command):
    # Modern VS Code stores resources in a versioned directory; reread after updates.
    command = Path(command)
    match = re.search(r'"%~dp0([^"\r\n]*cli\.js)"', command.read_text(encoding="utf-8"), re.IGNORECASE)
    cli = (command.parent / match.group(1)).resolve() if match else command.parent.parent / "resources/app/out/cli.js"
    exe = command.parent.parent / "Code.exe"
    if not exe.is_file() or not cli.is_file():
        raise RuntimeError("Cannot locate VS Code CLI")
    return exe, cli


def opened_windows(settings):
    """Best-effort deduplication using the supported CLI's status report."""
    try:
        exe, cli = code_paths(settings['code_cmd'])
        env = dict(os.environ, ELECTRON_RUN_AS_NODE='1')
        result = subprocess.run([str(exe), str(cli), '--status'], env=env, capture_output=True,
                                text=True, timeout=20, creationflags=NO_WINDOW)
        names = re.findall(r"^Connection to 'ssh-remote\+(BMI30[A-Za-z0-9-]*)'", result.stdout, re.MULTILINE)
        names += re.findall(r'\[SSH: (BMI30[A-Za-z0-9-]*)\]', result.stdout)
        names += re.findall(r'^Remote:\s+SSH:\s+(BMI30[A-Za-z0-9-]*)', result.stdout, re.MULTILINE)
        return {name.upper() for name in names if NAME.fullmatch(name)}
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return set()


def setup():
    """Install client configuration; no remote changes and no passwords."""
    ROOT.mkdir(parents=True, exist_ok=True)
    SSH_ROOT.mkdir(parents=True, exist_ok=True)
    key = SSH_ROOT / "id_ed25519_bmi30"
    if not key.exists():
        keygen = SSH.with_name("ssh-keygen.exe")
        subprocess.run([str(keygen), "-q", "-t", "ed25519", "-N", "", "-f", str(key),
                        "-C", "bmi30-vscode-" + os.environ.get("COMPUTERNAME", "windows")], check=True)
    # Protect the passwordless automation key using Windows ACLs.
    who = subprocess.check_output(["whoami.exe", "/user", "/fo", "csv", "/nh"], text=True)
    sid = next(csv.reader([who.strip()]))[1]
    subprocess.run(["icacls.exe", str(key), "/inheritance:r", "/grant:r", f"*{sid}:(F)"], check=True)
    if not key.with_suffix(".pub").exists():
        raise RuntimeError("Existing key has no public part; recover it before continuing")
    code_cmd = shutil.which("code.cmd")
    if not code_cmd:
        raise RuntimeError("VS Code CLI is not on PATH")
    code_paths(code_cmd)
    write_text(ROOT / "settings.json", json.dumps({"code_cmd": code_cmd,
                                                  "remote_folder": "/home/techaid/Documents"}, indent=2) + "\n")
    ssh_config = SSH_ROOT / "config"
    include = 'Include "' + (SSH_ROOT / "bmi30_config").as_posix() + '"'
    old = ssh_config.read_text(encoding="utf-8-sig") if ssh_config.exists() else ""
    if include not in old.splitlines():
        if ssh_config.exists():
            shutil.copy2(ssh_config, SSH_ROOT / ("config.before-bmi30-" + time.strftime("%Y%m%d-%H%M%S")))
        write_text(ssh_config, include + "\n\n" + old)
    devices = refresh()
    user_settings = Path(os.environ["APPDATA"]) / "Code/User/settings.json"
    values = read_json(user_settings, {})
    if user_settings.exists():
        shutil.copy2(user_settings, ROOT / ("vscode-settings-" + time.strftime("%Y%m%d-%H%M%S") + ".json"))
    values.update({"remote.SSH.path": str(SSH), "remote.SSH.configFile": str(ssh_config),
                   "remote.SSH.showLoginTerminal": False, "remote.SSH.connectTimeout": 60})
    platforms = values.setdefault("remote.SSH.remotePlatform", {})
    platforms.update({name: "linux" for name in devices})
    write_text(user_settings, json.dumps(values, ensure_ascii=False, indent=2) + "\n")
    log(f"Client configured; found {len(devices)} boards. Public key: {key.with_suffix('.pub')}")


def singleton():
    import msvcrt
    ROOT.mkdir(parents=True, exist_ok=True)
    handle = (ROOT / "watch.lock").open("a+b")
    handle.seek(0)
    if not handle.read(1):
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle  # process lifetime; OS releases the lock even after a crash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["setup", "discover", "check", "enroll", "open", "watch"])
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--while-code", action="store_true", help="Stop watching after all VS Code processes exit")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This client helper runs on Windows")
    if args.mode == "setup":
        setup()
        return 0
    settings = read_json(ROOT / "settings.json", {})
    lock = singleton() if args.mode in ("watch", "open") else True
    if not lock:
        log("Automatic connection is already running.")
        return 0
    opened = opened_windows(settings) if args.mode in ('watch', 'open') else set()
    if opened:
        log('Already open in VS Code: ' + ', '.join(sorted(opened)))
    previous = {}
    while True:
        try:
            if args.mode == 'watch' and args.while_code and not powershell_json(
                "@(Get-Process Code -ErrorAction SilentlyContinue | Select-Object Id) | ConvertTo-Json -Compress"):
                log('VS Code is closed; stopping discovery.')
                return 0
            devices = refresh()
            current = {name: item['ip'] for name, item in devices.items()}
            if current != previous:
                log("Found: " + ", ".join(f"{n} ({d['ip']})" for n, d in devices.items()))
                previous = current
            failures = 0
            for name in sorted(devices):
                if args.mode == "discover" or (args.mode in ('watch', 'open') and name in opened):
                    continue
                ok, detail = authenticated(name)
                if not ok and args.mode == "enroll":
                    print(f"\n=== {name} / {devices[name]['ip']} / user techaid ===", flush=True)
                    if install_key(name):
                        ok, detail = authenticated(name)
                if not ok:
                    failures += 1
                    log(f"{name}: enrollment required or SSH unavailable: {' '.join(detail)}")
                    continue
                log(f"{name}: SSH key and hostname verified")
                if args.mode in ("watch", "open"):
                    open_board(name, settings)
                    opened.add(name)
                    log(f"{name}: opened in VS Code")
            if args.mode != "watch":
                return 1 if failures else 0
        except Exception as error:
            if args.mode != "watch":
                raise
            log(f"Discovery error: {error}")
        time.sleep(max(15, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
