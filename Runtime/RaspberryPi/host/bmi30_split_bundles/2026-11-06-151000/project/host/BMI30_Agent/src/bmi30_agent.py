#!/usr/bin/env python3
"""BMI30 production enrollment, heartbeat, and reverse-tunnel agent."""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import http.client
import ipaddress
import json
import logging
import os
from pathlib import Path
import random
import re
import secrets
import shlex
import shutil
import ssl
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

AGENT_VERSION = "0.2.7"
PRODUCTION_SERVER_URL = "https://www.teiots.net/bmi30"
CHECKIN_PATH = "/api/v1/agent/checkin"
PRODUCTION_SSH_HOST = "www.teiots.net"
PRODUCTION_SSH_PORT = 2222
PRODUCTION_SSH_USER = "bmi30-tunnel"
PRODUCTION_LISTEN_ADDRESS = "0.0.0.0"

DEFAULT_CONFIG = Path("/etc/bmi30-agent/config.json")
DEFAULT_STATE = Path("/var/lib/bmi30-agent/state.json")
DEFAULT_TOKEN = Path("/var/lib/bmi30-agent/device_api_token")
DEFAULT_BOUND_SERIAL = Path("/var/lib/bmi30-agent/bound_raspberry_serial")
DEFAULT_IDENTITY_LOCK = Path("/var/lib/bmi30-agent/identity.lock")
DEFAULT_PRIVATE_KEY = Path("/etc/bmi30-agent/id_ed25519")
DEFAULT_PUBLIC_KEY = Path("/etc/bmi30-agent/id_ed25519.pub")
DEFAULT_KNOWN_HOSTS = Path("/etc/bmi30-agent/known_hosts")
DEFAULT_TUNNEL_ENV = Path("/etc/bmi30-agent/tunnel.env")

AGENT_SERVICE = "bmi30-agent.service"
TUNNEL_SERVICE = "bmi30-tunnel.service"
DEVICE_ID_RE = re.compile(r"^BMI30-[A-F0-9]{16}$")
KEY_COMMENT_RE = re.compile(r"^(BMI30-[A-F0-9]{16})@bmi30-tunnel$")
MAX_CHECKIN_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_AUTH_ERROR_RETRY_SECONDS = 60
MIN_AUTH_ERROR_RETRY_SECONDS = 60

LOG = logging.getLogger("bmi30-agent")


class AgentError(RuntimeError):
    """A local validation or configuration error."""


class CheckinHTTPError(AgentError):
    """An HTTP error whose status controls retry policy."""

    def __init__(
        self,
        status: int,
        detail: str = "",
        response: dict[str, Any] | None = None,
    ) -> None:
        self.status = int(status)
        self.detail = sanitize_text(detail, 240)
        self.response = dict(response or {})
        suffix = f": {self.detail}" if self.detail else ""
        super().__init__(f"Hub check-in returned HTTP {self.status}{suffix}")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def sanitize_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())[:limit]


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, mode)
    os.replace(temp, path)


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return {} if default is None else dict(default)
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AgentError(f"Expected a JSON object in {path}")
    return value


def save_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        mode,
    )


def run_command(
    args: list[str],
    *,
    timeout: float = 15,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=check,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        if check:
            raise AgentError(f"Command failed: {shlex.join(args)}: {exc}") from exc
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def systemctl(*args: str, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return run_command(["/bin/systemctl", *args], timeout=timeout)


def systemd_state(unit: str) -> dict[str, str]:
    active = systemctl("is-active", unit)
    enabled = systemctl("is-enabled", unit)
    return {
        "active": active.stdout.strip() or "unknown",
        "enabled": enabled.stdout.strip() or "unknown",
    }


def public_key_fingerprint(public_key: str) -> str:
    parts = public_key.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise AgentError("Only ssh-ed25519 public keys are accepted")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except Exception as exc:  # noqa: BLE001
        raise AgentError("Invalid OpenSSH public-key base64") from exc
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def canonical_public_key(value: str, expected_comment: str | None = None) -> str:
    parts = value.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise AgentError("Only ssh-ed25519 public keys are accepted")
    try:
        base64.b64decode(parts[1], validate=True)
    except Exception as exc:  # noqa: BLE001
        raise AgentError("Invalid OpenSSH public-key base64") from exc
    comment = expected_comment or (parts[2] if len(parts) >= 3 else "")
    return " ".join(part for part in ("ssh-ed25519", parts[1], comment) if part)


def read_public_key(path: Path, device_id: str | None = None) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AgentError(f"Cannot read SSH public key {path}: {exc}") from exc
    expected_comment = f"{device_id}@bmi30-tunnel" if device_id else None
    result = canonical_public_key(value, expected_comment)
    if device_id:
        parts = value.split()
        if len(parts) < 3 or parts[2] != expected_comment:
            raise AgentError(f"SSH public-key comment does not match {device_id}")
    return result


def get_local_ips(local_api: dict[str, Any] | None = None) -> list[str]:
    values: list[str] = []

    def add(value: Any) -> None:
        try:
            address = ipaddress.ip_address(str(value or "").split("%", 1)[0])
        except ValueError:
            return
        if address.is_loopback or address.is_unspecified:
            return
        rendered = str(address)
        if rendered not in values:
            values.append(rendered)

    status = local_api.get("status") if isinstance(local_api, dict) else None
    interfaces = status.get("interfaces") if isinstance(status, dict) else None
    if isinstance(interfaces, list):
        for interface in interfaces:
            if not isinstance(interface, dict):
                continue
            if interface.get("active") is False:
                continue
            if str(interface.get("state", "")).lower() in {"down", "disabled", "inactive"}:
                continue
            add(interface.get("ip"))
        if values:
            return values[:16]

    # Compatibility fallback for older local Portal versions that do not yet
    # expose status.interfaces. The value is still collected live and never
    # loaded from copied configuration.
    result = run_command(["/usr/bin/hostname", "-I"], timeout=5)
    if result.returncode == 0:
        for token in result.stdout.split():
            add(token)
    return values[:16]


def read_first_existing(paths: list[Path], limit: int = 256) -> str | None:
    for path in paths:
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8", errors="replace").strip("\x00\r\n\t ")
                if value:
                    return value[:limit]
        except OSError:
            continue
    return None


def raspberry_serial() -> str | None:
    try:
        lines = Path("/proc/cpuinfo").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return None
    for line in lines:
        if line.lower().startswith("serial") and ":" in line:
            value = line.split(":", 1)[1].strip().upper()
            if re.fullmatch(r"[0-9A-F]{16}", value):
                return value
    return None


def validate_hardware_identity(config: dict[str, Any]) -> str:
    serial = raspberry_serial()
    if not serial:
        raise AgentError(
            "Raspberry CPU serial is unavailable; refusing to use a saved or copied DEVICE_ID"
        )
    return f"BMI30-{serial}"


def raspberry_firmware_version() -> str | None:
    """Return the installed Raspberry/websplit software release."""
    manifest = Path("/home/techaid/Documents/host/bmi30_firmware_release.env")
    try:
        for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("BMI30_FIRMWARE_VERSION="):
                return line.split("=", 1)[1].strip().strip("'\"")[:256] or None
    except OSError:
        pass
    return read_first_existing(
        [
            Path("/etc/bmi30-version"),
            Path("/home/techaid/Documents/VERSION"),
            Path("/home/techaid/Documents/version.txt"),
        ]
    )


def checkin_versions(local_api: dict[str, Any]) -> tuple[str, str]:
    """Resolve Raspberry and STM32 versions without substituting one for the other."""
    status = local_api.get("status") if isinstance(local_api.get("status"), dict) else {}
    raspberry_version = sanitize_text(status.get("raspberry_firmware_version"), 256)
    if not raspberry_version:
        release = status.get("firmware_release") if isinstance(status.get("firmware_release"), dict) else {}
        split = status.get("split_system") if isinstance(status.get("split_system"), dict) else {}
        raspberry_version = sanitize_text(
            release.get("version") or split.get("version") or raspberry_firmware_version(),
            256,
        )
    stm32_version = sanitize_text(status.get("stm32_firmware_version"), 64)
    return raspberry_version, stm32_version


def local_api_probe(local_port: int) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False}
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection("127.0.0.1", local_port, timeout=1.0)
        connection.request("GET", "/api/status", headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_CHECKIN_BYTES)
        result["http_status"] = response.status
        content_type = response.getheader("Content-Type", "")
        if body and "json" in content_type.lower():
            try:
                parsed = json.loads(body.decode("utf-8", errors="strict"))
                if isinstance(parsed, dict):
                    result["status"] = parsed
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        result["ok"] = 200 <= response.status < 300
    except Exception as exc:  # noqa: BLE001
        result["error"] = sanitize_text(exc, 200)
    finally:
        if connection is not None:
            connection.close()
    return result


def collect_metadata(config: dict[str, Any]) -> dict[str, Any]:
    device_id = validate_hardware_identity(config)
    hardware_serial = device_id.removeprefix("BMI30-")
    key_path = Path(str(config.get("ssh_public_key_path", DEFAULT_PUBLIC_KEY)))
    public_key = read_public_key(key_path, device_id)
    local_api = local_api_probe(int(config.get("local_port", 80)))
    raspberry_version, stm32_version = checkin_versions(local_api)
    return {
        "device_id": device_id,
        "public_key": public_key,
        # A copied /etc/hostname may contain the previous Raspberry number.
        # Hub identity labels therefore come from the same live CPU serial as
        # DEVICE_ID and never from persistent hostname configuration.
        "hostname": device_id,
        "raspberry_serial": hardware_serial,
        "model": read_first_existing([Path("/proc/device-tree/model")]),
        "firmware_version": stm32_version,
        "agent_version": raspberry_version,
        "connector_version": AGENT_VERSION,
        "local_ips": get_local_ips(local_api),
        "tunnel_service": systemd_state(TUNNEL_SERVICE),
        "local_api": local_api,
    }


def validate_server_url(base_url: str) -> tuple[str, int, str]:
    parsed = urlsplit(base_url.rstrip("/"))
    if parsed.scheme.lower() != "https":
        raise AgentError("Server URL must use https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AgentError("Server URL must not contain credentials, query, or fragment")
    if parsed.hostname != PRODUCTION_SSH_HOST or parsed.port not in (None, 443):
        raise AgentError(f"Server URL must use {PRODUCTION_SERVER_URL}")
    if parsed.path.rstrip("/") != "/bmi30":
        raise AgentError(f"Server URL must use {PRODUCTION_SERVER_URL}")
    return parsed.hostname, parsed.port or 443, parsed.path.rstrip("/")


def system_ca_https_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    token: str,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    host, port, base_path = validate_server_url(base_url)
    if not path.startswith("/"):
        raise AgentError("Check-in path must be absolute")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_CHECKIN_BYTES:
        raise AgentError(f"Check-in JSON exceeds {MAX_CHECKIN_BYTES} bytes")
    if len(token) < 40 or len(token) > 512 or any(character.isspace() for character in token):
        raise AgentError("Saved device API token is invalid")

    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    try:
        connection.request(
            "POST",
            base_path + path,
            body=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
                "User-Agent": f"BMI30-Agent/{AGENT_VERSION}",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        response_body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(response_body) > MAX_RESPONSE_BYTES:
            raise AgentError("Hub response exceeds the permitted size")
        decoded: dict[str, Any] = {}
        if response_body:
            try:
                candidate = json.loads(response_body.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if response.status == 200:
                    raise AgentError(f"Hub returned non-JSON response, HTTP {response.status}") from exc
                candidate = {"detail": "non-JSON error response"}
            if not isinstance(candidate, dict):
                raise AgentError("Hub JSON response must be an object")
            decoded = candidate
        return response.status, decoded
    finally:
        connection.close()


def get_or_create_api_token(path: Path) -> str:
    if path.exists():
        token = path.read_text(encoding="ascii").strip()
        if 40 <= len(token) <= 512 and not any(character.isspace() for character in token):
            return token
        raise AgentError(f"Invalid saved device API token in {path}; it was not replaced")
    token = secrets.token_urlsafe(48)
    atomic_write_text(path, token + "\n", 0o600)
    return token


def _saved_public_key_device_id(path: Path) -> str:
    try:
        parts = path.read_text(encoding="utf-8", errors="strict").strip().split()
    except OSError:
        return ""
    if len(parts) < 3:
        return ""
    match = KEY_COMMENT_RE.fullmatch(parts[2])
    return match.group(1) if match else ""


def _backup_identity_files(
    paths: list[Path],
    hardware_device_id: str,
    reason: str,
) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    backup_dir = Path("/var/backups/bmi30-agent") / f"auto-reenroll-{stamp}-{os.getpid()}"
    backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(backup_dir, 0o700)
    for source in paths:
        if not source.exists():
            continue
        target = backup_dir / source.as_posix().lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    atomic_write_text(
        backup_dir / "AUDIT.txt",
        "\n".join(
            (
                "BMI30 automatic hardware re-enrollment backup",
                f"UTC={stamp}",
                f"HARDWARE_DEVICE_ID={hardware_device_id}",
                f"REASON={sanitize_text(reason, 240)}",
                "",
            )
        ),
        0o600,
    )
    return backup_dir


def _generate_ssh_identity(private_key: Path, public_key: Path, device_id: str) -> None:
    private_key.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(private_key.parent, 0o700)
    temp_private = private_key.with_name(private_key.name + f".new.{os.getpid()}")
    temp_public = Path(str(temp_private) + ".pub")
    for temp in (temp_private, temp_public):
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    result = run_command(
        [
            "/usr/bin/ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            f"{device_id}@bmi30-tunnel",
            "-f",
            str(temp_private),
        ],
        timeout=20,
    )
    if result.returncode != 0:
        raise AgentError("Unable to generate a hardware-specific Ed25519 identity")
    os.chmod(temp_private, 0o600)
    os.chmod(temp_public, 0o644)
    os.replace(temp_private, private_key)
    os.replace(temp_public, public_key)


def _read_bound_serial(path: Path) -> str:
    try:
        value = path.read_text(encoding="ascii", errors="strict").strip().upper()
    except (OSError, UnicodeError):
        return ""
    return value if re.fullmatch(r"[0-9A-F]{16}", value) else ""


def _reset_runtime_assignment(state_path: Path) -> None:
    """Remove only the previous Hub assignment/approval from saved state."""
    try:
        state = load_json(state_path, {})
    except (OSError, ValueError, json.JSONDecodeError):
        state = {}
    state.pop("remote_port", None)
    state.pop("http_status", None)
    state["server_state"] = "identity_reset"
    state["server_message"] = "Hardware identity reset; enrollment required"
    save_json(state_path, state, 0o600)


def hardware_identity_reset_required(
    status: int,
    response: dict[str, Any],
    hardware_device_id: str,
) -> bool:
    """Accept only the documented, hardware-bound Hub reset instruction."""
    return (
        status == 409
        and response.get("code") == "hardware_identity_mismatch"
        and response.get("reset_identity_required") is True
        and response.get("expected_device_id") == hardware_device_id
    )


def reconcile_hardware_identity(
    config_path: Path,
    config: dict[str, Any],
    *,
    force_reset: bool = False,
    reset_reason: str = "",
) -> tuple[dict[str, Any], str, bool]:
    """Bind runtime credentials to the real CPU serial, never to copied config."""
    device_id = validate_hardware_identity(config)
    hardware_serial = device_id.removeprefix("BMI30-")
    private_key = Path(str(config.get("ssh_private_key_path", DEFAULT_PRIVATE_KEY)))
    public_key = Path(str(config.get("ssh_public_key_path", DEFAULT_PUBLIC_KEY)))
    token_path = Path(str(config.get("device_api_token_path", DEFAULT_TOKEN)))
    known_hosts = Path(str(config.get("ssh_known_hosts_path", DEFAULT_KNOWN_HOSTS)))
    DEFAULT_IDENTITY_LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(DEFAULT_IDENTITY_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    rotated = False
    try:
        os.chmod(DEFAULT_IDENTITY_LOCK, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        saved_key_device_id = _saved_public_key_device_id(public_key)
        bound_serial = _read_bound_serial(DEFAULT_BOUND_SERIAL)
        identity_complete = private_key.is_file() and public_key.is_file()
        identity_matches = identity_complete and saved_key_device_id == device_id
        binding_matches = bound_serial == hardware_serial

        if force_reset or not identity_matches or not binding_matches:
            if reset_reason:
                reason = reset_reason
            elif not binding_matches:
                reason = "Saved Raspberry binding is absent or belongs to other hardware."
            else:
                reason = "Saved SSH identity is absent, incomplete, or belongs to other hardware."
            stop_new_tunnel()
            backup_dir = _backup_identity_files(
                [
                    config_path,
                    private_key,
                    public_key,
                    token_path,
                    known_hosts,
                    DEFAULT_TUNNEL_ENV,
                    DEFAULT_STATE,
                    DEFAULT_BOUND_SERIAL,
                ],
                device_id,
                reason,
            )
            _generate_ssh_identity(private_key, public_key, device_id)
            atomic_write_text(token_path, secrets.token_urlsafe(48) + "\n", 0o600)
            try:
                DEFAULT_TUNNEL_ENV.unlink()
            except FileNotFoundError:
                pass
            _reset_runtime_assignment(DEFAULT_STATE)
            config.pop("last_remote_port", None)
            atomic_write_text(DEFAULT_BOUND_SERIAL, hardware_serial + "\n", 0o600)
            rotated = True
            LOG.warning(
                "Hardware identity initialized for %s; previous BMI30 identity was backed up in %s",
                device_id,
                backup_dir,
            )
        else:
            os.chmod(DEFAULT_BOUND_SERIAL, 0o600)

        # DEVICE_ID is intentionally not persisted.  Every process invocation
        # derives it again from /proc/cpuinfo, so a copied SD card cannot impersonate
        # the Raspberry from which the image was made.
        changed = config.pop("device_id", None) is not None
        if rotated or changed:
            save_json(config_path, config, 0o600)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
    return config, device_id, rotated


def shell_assignment(name: str, value: str | int) -> str:
    return f"{name}={shlex.quote(str(value))}"


def canonical_host_key_line(ssh_host: str, ssh_port: int, host_key: str) -> str:
    canonical = canonical_public_key(host_key)
    parts = canonical.split()
    host_field = ssh_host if ssh_port == 22 else f"[{ssh_host}]:{ssh_port}"
    return f"{host_field} {parts[0]} {parts[1]}"


def pin_ssh_host_key(path: Path, ssh_host: str, ssh_port: int, host_key: str) -> None:
    new_line = canonical_host_key_line(ssh_host, ssh_port, host_key)
    if path.exists():
        existing_lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8", errors="strict").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if existing_lines != [new_line]:
            raise AgentError(
                "Pinned SSH host key changed; refusing automatic replacement"
            )
        return
    atomic_write_text(path, new_line + "\n", 0o600)


def validate_approved_response(response: dict[str, Any]) -> dict[str, Any]:
    try:
        remote_port = int(response["remote_port"])
        ssh_port = int(response["ssh_port"])
        listen_address = str(response["listen_address"])
        ssh_host = str(response["ssh_host"])
        ssh_user = str(response["ssh_user"])
        host_key = str(response["ssh_host_public_key"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentError("Approved response is missing required tunnel fields") from exc
    if not 20000 <= remote_port <= 39999:
        raise AgentError(f"Hub returned invalid tunnel port {remote_port}")
    if listen_address != PRODUCTION_LISTEN_ADDRESS:
        raise AgentError("Hub returned an unsupported listen address")
    if ssh_host != PRODUCTION_SSH_HOST:
        raise AgentError("Hub returned an unexpected SSH host")
    if ssh_port != PRODUCTION_SSH_PORT:
        raise AgentError("Hub returned an unexpected SSH port")
    if ssh_user != PRODUCTION_SSH_USER:
        raise AgentError("Hub returned an unexpected SSH user")
    canonical_public_key(host_key)
    return {
        "remote_port": remote_port,
        "listen_address": listen_address,
        "ssh_host": ssh_host,
        "ssh_port": ssh_port,
        "ssh_user": ssh_user,
        "ssh_host_public_key": host_key,
    }


def sync_tunnel(
    config: dict[str, Any],
    response: dict[str, Any],
    config_path: Path = DEFAULT_CONFIG,
) -> None:
    approved = validate_approved_response(response)
    known_hosts_path = Path(str(config.get("ssh_known_hosts_path", DEFAULT_KNOWN_HOSTS)))
    pin_ssh_host_key(
        known_hosts_path,
        approved["ssh_host"],
        approved["ssh_port"],
        approved["ssh_host_public_key"],
    )
    env_lines = [
        "# Managed by BMI30 Agent 0.2+. Do not edit.",
        shell_assignment("SSH_HOST", approved["ssh_host"]),
        shell_assignment("SSH_PORT", approved["ssh_port"]),
        shell_assignment("SSH_USER", approved["ssh_user"]),
        shell_assignment("LISTEN_ADDRESS", approved["listen_address"]),
        shell_assignment("REMOTE_PORT", approved["remote_port"]),
        shell_assignment("LOCAL_PORT", int(config.get("local_port", 80))),
        "",
    ]
    new_text = "\n".join(env_lines)
    old_text = ""
    if DEFAULT_TUNNEL_ENV.exists():
        old_text = DEFAULT_TUNNEL_ENV.read_text(encoding="utf-8", errors="replace")
    changed = old_text != new_text
    if changed:
        atomic_write_text(DEFAULT_TUNNEL_ENV, new_text, 0o600)

    current = systemd_state(TUNNEL_SERVICE)
    action = "restart" if current["active"] == "active" and changed else "start"
    if current["active"] != "active" or changed:
        result = systemctl(action, TUNNEL_SERVICE, timeout=30)
        if result.returncode != 0:
            raise AgentError(
                f"Cannot {action} {TUNNEL_SERVICE}: "
                + sanitize_text(result.stderr or result.stdout, 400)
            )

    config["last_remote_port"] = approved["remote_port"]
    save_json(config_path, config, 0o600)


def stop_new_tunnel() -> None:
    systemctl("stop", TUNNEL_SERVICE, timeout=30)


def save_agent_state(
    state: str,
    response: dict[str, Any] | None = None,
    *,
    http_status: int | None = None,
) -> None:
    response = response or {}
    value: dict[str, Any] = {
        "last_checkin_unix": int(time.time()),
        "server_state": state,
        "server_message": sanitize_text(response.get("message"), 240),
        "remote_port": response.get("remote_port"),
        "tunnel_service": systemd_state(TUNNEL_SERVICE),
    }
    if http_status is not None:
        value["http_status"] = int(http_status)
    save_json(DEFAULT_STATE, value, 0o600)


def perform_checkin(config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    config = load_json(config_path)
    required = ("server_url", "ssh_private_key_path", "ssh_public_key_path")
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise AgentError(f"Missing agent configuration fields: {', '.join(missing)}")
    config, device_id, _rotated = reconcile_hardware_identity(config_path, config)

    for attempt in range(2):
        token_path = Path(str(config.get("device_api_token_path", DEFAULT_TOKEN)))
        token = get_or_create_api_token(token_path)
        payload = collect_metadata(config)
        status, response = system_ca_https_json(
            str(config["server_url"]),
            CHECKIN_PATH,
            payload,
            token=token,
            timeout=float(config.get("request_timeout_seconds", 10)),
        )
        if attempt == 0 and hardware_identity_reset_required(status, response, device_id):
            LOG.warning(
                "Hub confirmed hardware identity mismatch for %s; creating new BMI30 credentials",
                device_id,
            )
            config, device_id, _rotated = reconcile_hardware_identity(
                config_path,
                config,
                force_reset=True,
                reset_reason="Hub returned HTTP 409 hardware_identity_mismatch with matching expected DEVICE_ID.",
            )
            continue
        break

    if status != 200:
        save_agent_state("http_error", response, http_status=status)
        detail = response.get("detail") or response.get("message") or response.get("code") or ""
        raise CheckinHTTPError(status, str(detail), response)

    state = str(response.get("state", "")).lower()
    if state == "approved":
        sync_tunnel(config, response, config_path)
    elif state in {"pending", "blocked", "rejected"}:
        stop_new_tunnel()
    else:
        raise AgentError(f"Hub returned unsupported state: {sanitize_text(state)}")

    save_agent_state(state, response, http_status=status)
    return response


def response_interval(response: dict[str, Any], default: int) -> int:
    try:
        value = int(response.get("next_checkin_seconds", default))
    except (TypeError, ValueError):
        value = default
    return max(10, min(value, 24 * 60 * 60))


def auth_error_retry_interval(config: dict[str, Any]) -> int:
    """Return the retry delay after 401/409 without creating a tight loop."""
    try:
        value = int(
            config.get(
                "auth_error_retry_seconds",
                DEFAULT_AUTH_ERROR_RETRY_SECONDS,
            )
        )
    except (TypeError, ValueError):
        value = DEFAULT_AUTH_ERROR_RETRY_SECONDS
    return max(MIN_AUTH_ERROR_RETRY_SECONDS, value)


def run_forever(config_path: Path, verbose: bool = False) -> int:
    setup_logging(verbose)
    LOG.info("BMI30 Agent %s starting with system CA verification", AGENT_VERSION)
    consecutive_failures = 0
    while True:
        try:
            config = load_json(config_path)
            default_interval = max(10, int(config.get("heartbeat_seconds", 30)))
            auth_error_interval = auth_error_retry_interval(config)
        except Exception:  # noqa: BLE001
            default_interval = 30
            auth_error_interval = DEFAULT_AUTH_ERROR_RETRY_SECONDS

        try:
            response = perform_checkin(config_path)
            state = str(response.get("state", "unknown"))
            LOG.info("Hub state: %s", state)
            consecutive_failures = 0
            delay = response_interval(response, default_interval)
        except CheckinHTTPError as exc:
            consecutive_failures += 1
            if exc.status in {401, 409}:
                LOG.error(
                    "Hub authentication/conflict error HTTP %s; key and token were preserved",
                    exc.status,
                )
                delay = auth_error_interval
            elif 500 <= exc.status <= 599:
                LOG.error("Hub temporary error HTTP %s", exc.status)
                delay = min(900, default_interval * (2 ** min(consecutive_failures - 1, 5)))
            else:
                LOG.error("Hub rejected check-in with HTTP %s", exc.status)
                delay = max(300, default_interval)
        except Exception as exc:  # noqa: BLE001
            consecutive_failures += 1
            LOG.error("Check-in failed: %s", sanitize_text(exc, 400))
            delay = min(900, default_interval * (2 ** min(consecutive_failures - 1, 5)))

        delay += random.uniform(0, min(10, delay * 0.1))
        time.sleep(delay)


def configure_agent(config_path: Path, server: str | None, local_port: int | None) -> None:
    config = load_json(config_path)
    config.pop("device_id", None)
    if server:
        validate_server_url(server)
        config["server_url"] = server.rstrip("/")
    if local_port is not None:
        if not 1 <= int(local_port) <= 65535:
            raise AgentError("Local HTTP port must be in 1..65535")
        config["local_port"] = int(local_port)
    save_json(config_path, config, 0o600)
    print("Configuration updated:")
    print(f"  server_url={config.get('server_url')}")
    print(f"  local_port={config.get('local_port')}")
    print("  tls_verification=SYSTEM_CA")


def print_status(config_path: Path) -> None:
    config = load_json(config_path)
    state = load_json(DEFAULT_STATE, {})
    device_id = validate_hardware_identity(config)
    public_key_path = Path(str(config.get("ssh_public_key_path", DEFAULT_PUBLIC_KEY)))
    token_path = Path(str(config.get("device_api_token_path", DEFAULT_TOKEN)))
    print(f"BMI30 Agent version: {AGENT_VERSION}")
    print(f"DEVICE_ID: {device_id}")
    print(f"Server: {config.get('server_url')}")
    print("TLS verification: SYSTEM_CA")
    print(f"API token: {'PRESENT' if token_path.is_file() else 'MISSING'}")
    print(f"Last remote port: {config.get('last_remote_port')}")
    if public_key_path.is_file():
        public_key = read_public_key(public_key_path, device_id)
        print(f"SSH public-key fingerprint: {public_key_fingerprint(public_key)}")
    print("Agent service:", systemd_state(AGENT_SERVICE))
    print("BMI30 tunnel service:", systemd_state(TUNNEL_SERVICE))
    if state:
        print("Last state:", json.dumps(state, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="BMI30 production enrollment and tunnel agent")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("once")
    sub.add_parser("once-approved")
    sub.add_parser("status")
    sub.add_parser("validate-identity")
    configure = sub.add_parser("configure")
    configure.add_argument("--server")
    configure.add_argument("--local-port", type=int)
    args = parser.parse_args()

    try:
        if args.command == "run":
            return run_forever(args.config, args.verbose)
        setup_logging(args.verbose)
        if args.command == "once":
            result = perform_checkin(args.config)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "once-approved":
            result = perform_checkin(args.config)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if str(result.get("state", "")).lower() != "approved":
                LOG.error("Hub state is not approved; autostart was not enabled")
                return 3
            return 0
        if args.command == "configure":
            configure_agent(args.config, args.server, args.local_port)
            return 0
        if args.command == "status":
            print_status(args.config)
            return 0
        if args.command == "validate-identity":
            validate_hardware_identity(load_json(args.config))
            return 0
    except AgentError as exc:
        LOG.error("%s", sanitize_text(exc, 400))
        return 2
    except KeyboardInterrupt:
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
