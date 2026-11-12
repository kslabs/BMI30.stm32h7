from __future__ import annotations

import asyncio
import csv
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import ipaddress
import io
import json
from pathlib import Path
import re
import secrets
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import Settings, load_settings
from .db import Database, utcnow
from .firmware import FirmwareStorage, build_firmware_router
from .central_store import CentralStore, build_central_router, system_scope_resolver
from .key_client import KeyClientError, call_key_manager
from .legacy_auth import (
    LEGACY_PASSWORD_SENTINEL,
    LegacyAuthError,
    LegacyIdentity,
    LegacyMqttAuthenticator,
)
from .monitor import DeviceMonitor, extract_checkin_versions
from .security import (
    SecurityError,
    canonical_public_key,
    constant_time_equal,
    device_id_from_raspberry_serial,
    hash_password,
    legacy_username_to_display,
    legacy_username_to_raw,
    password_needs_rehash,
    public_key_fingerprint,
    random_token,
    token_hash,
    validate_device_id,
    validate_email,
    validate_legacy_username,
    validate_password,
    validate_raspberry_serial,
    validate_username,
    verify_password,
)

SETTINGS: Settings = load_settings()
DB = Database(SETTINGS.db_path)
DB.initialize()
PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))
MONITOR = DeviceMonitor(SETTINGS, DB)
FIRMWARE_STORAGE = FirmwareStorage(SETTINGS, DB)
CENTRAL_STORE = CentralStore(SETTINGS.root)
LEGACY_AUTH = LegacyMqttAuthenticator(
    SETTINGS.legacy_mqtt_host,
    SETTINGS.legacy_mqtt_port,
    SETTINGS.legacy_mqtt_timeout_seconds,
)
LEGACY_ADMIN_USERS = {
    legacy_username_to_raw(value).casefold()
    for value in SETTINGS.legacy_admin_users
}
LOGIN_FAILURES: dict[str, list[float]] = {}
COOKIE_NAME = "bmi30hub_session"
PATH_PREFIX = SETTINGS.path_prefix
COOKIE_PATH = PATH_PREFIX or "/"
MAX_AGENT_BODY = 65536
MAX_PROXY_BODY = 64 * 1024 * 1024
MAX_PROXY_RESPONSE = 128 * 1024 * 1024
MAX_LOGIN_RATE_KEYS = 10000
PUBLIC_IP_LOCATION_ENDPOINT = "https://ipwho.is"
PUBLIC_IP_LOCATION_REFRESH_DAYS = 30
PUBLIC_IP_LOCATION_RETRY_HOURS = 6
PUBLIC_IP_LOCATION_MAX_BATCH = 20


@dataclass
class AuthContext:
    user: dict[str, Any]
    session: dict[str, Any]


def public_path(path: str) -> str:
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Public path must be absolute and host-relative")
    if not PATH_PREFIX:
        return path
    if path == "/":
        return PATH_PREFIX + "/"
    return PATH_PREFIX + path


def request_public_path(request: Request) -> str:
    path = request.url.path or "/"
    if PATH_PREFIX and (path == PATH_PREFIX or path.startswith(PATH_PREFIX + "/")):
        return path
    return public_path(path)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def human_time(value: str | None) -> str:
    timestamp = parse_utc(value)
    if timestamp is None:
        return "—"
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def normalize_history_timestamp(value: str) -> str:
    """Normalize a browser-local ISO value to the canonical UTC representation."""
    raw = value.strip()
    if not raw or len(raw) > 64:
        raise HTTPException(status_code=400, detail="History date and time are required")
    try:
        timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid history date and time") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc).replace(microsecond=0)
    if timestamp.year < 2000 or timestamp.year > 2100:
        raise HTTPException(status_code=400, detail="History date is outside the supported range")
    return timestamp.isoformat().replace("+00:00", "Z")


def normalize_history_text(value: Any) -> str:
    entry = str(value or "").strip()
    if not entry:
        raise HTTPException(status_code=400, detail="History record text is required")
    if len(entry) > 4000:
        raise HTTPException(status_code=400, detail="History record is too long")
    return entry


def load_device_history(device_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in DB.fetchall(
        """
        SELECT * FROM device_history
        WHERE device_id=? ORDER BY occurred_at DESC,id DESC
        """,
        (device_id,),
    )]


def public_ip_location_text(device: dict[str, Any]) -> str:
    """Return a compact, de-duplicated approximate IP location label."""
    parts: list[str] = []
    for key in ("country_name", "region_name", "city_name"):
        value = str(device.get(key) or "").strip()
        if value and value.casefold() not in {part.casefold() for part in parts}:
            parts.append(value[:100])
    return ", ".join(parts)


def parse_public_ip_location(payload: Any, expected_ip: str) -> dict[str, str] | None:
    """Validate the bounded subset accepted from the external GeoIP service."""
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    returned_ip = str(payload.get("ip") or "").strip()
    try:
        if ipaddress.ip_address(returned_ip) != ipaddress.ip_address(expected_ip):
            return None
    except ValueError:
        return None
    result = {}
    for source, destination in (
        ("country", "country_name"),
        ("region", "region_name"),
        ("city", "city_name"),
    ):
        value = str(payload.get(source) or "").strip()
        result[destination] = value[:100] if value else ""
    return result if any(result.values()) else None


async def resolve_public_ip_locations(client: httpx.AsyncClient | None = None) -> None:
    """Refresh a small server-side cache of approximate public-IP locations.

    Failed lookups are cached for six hours and successful results for thirty
    days, so opening the table does not repeatedly disclose or query an IP.
    """
    rows = [dict(row) for row in DB.fetchall(
        """
        SELECT DISTINCT d.public_ip,g.lookup_success,g.updated_at
        FROM devices d
        LEFT JOIN public_ip_locations g ON g.ip_address=d.public_ip
        WHERE d.public_ip IS NOT NULL AND d.public_ip<>''
        ORDER BY d.last_seen_at DESC
        """
    )]
    now = datetime.now(timezone.utc)
    candidates: list[str] = []
    for row in rows:
        address_text = str(row.get("public_ip") or "").strip()
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError:
            continue
        if not address.is_global:
            continue
        updated = parse_utc(row.get("updated_at"))
        if updated is not None:
            lifetime = (
                timedelta(days=PUBLIC_IP_LOCATION_REFRESH_DAYS)
                if row.get("lookup_success")
                else timedelta(hours=PUBLIC_IP_LOCATION_RETRY_HOURS)
            )
            if now - updated < lifetime:
                continue
        candidates.append(str(address))
        if len(candidates) >= PUBLIC_IP_LOCATION_MAX_BATCH:
            break
    if not candidates:
        return

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(4.0),
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "BMI30Hub/0.2.1"},
        )
    try:
        async def lookup(address: str) -> tuple[str, dict[str, str] | None]:
            try:
                response = await client.get(f"{PUBLIC_IP_LOCATION_ENDPOINT}/{address}")
                response.raise_for_status()
                return address, parse_public_ip_location(response.json(), address)
            except (httpx.HTTPError, ValueError):
                return address, None

        results = await asyncio.gather(*(lookup(address) for address in candidates))
    finally:
        if owns_client:
            await client.aclose()
    updated_at = utcnow()
    with DB.transaction(immediate=True) as connection:
        for address, location in results:
            values = location or {"country_name": "", "region_name": "", "city_name": ""}
            connection.execute(
                """
                INSERT INTO public_ip_locations(
                    ip_address,country_name,region_name,city_name,lookup_success,updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(ip_address) DO UPDATE SET
                    country_name=excluded.country_name,
                    region_name=excluded.region_name,
                    city_name=excluded.city_name,
                    lookup_success=excluded.lookup_success,
                    updated_at=excluded.updated_at
                """,
                (
                    address,
                    values["country_name"],
                    values["region_name"],
                    values["city_name"],
                    1 if location else 0,
                    updated_at,
                ),
            )


def is_recent(value: str | None, seconds: int) -> bool:
    timestamp = parse_utc(value)
    if timestamp is None:
        return False
    return datetime.now(timezone.utc) - timestamp <= timedelta(seconds=seconds)


def display_device_id(device: dict[str, Any]) -> str:
    """Return the short service-facing BMI30 identifier used in the table."""
    raw = str(device.get("device_id") or "").strip().upper()
    suffix = raw.removeprefix("BMI30-")
    if suffix:
        return f"BMI30-{suffix[-9:]}"
    serial = re.sub(r"[^A-Z0-9]", "", str(device.get("raspberry_serial") or "").upper())
    if serial:
        return f"BMI30-{serial[-9:]}"
    hostname = str(device.get("hostname") or "").strip().upper()
    hostname_suffix = hostname.removeprefix("BMI30-")
    return f"BMI30-{hostname_suffix[-9:]}" if hostname_suffix else hostname


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _runtime_text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def _runtime_node_code(value: Any, role: Any, limit: int = 100) -> str:
    """Use Mnn for masters and Snn for slaves; never display ambiguous Nnn."""
    code = _runtime_text(value, limit)
    match = re.fullmatch(r"[MNS](\d{1,4})", code, flags=re.IGNORECASE)
    normalized_role = _runtime_text(role, 64).lower()
    if match and normalized_role in {"master", "slave"}:
        prefix = "M" if normalized_role == "master" else "S"
        return f"{prefix}{match.group(1)}"
    return code


def enrich_device_runtime(device: dict[str, Any]) -> dict[str, Any]:
    """Expose bounded, display-only runtime data reported by the Raspberry.

    The administrator-managed ``device_role`` remains authoritative and is not
    overwritten by the live RS485 role reported by the device.
    """
    tunnel_last_seen = parse_utc(str(device.get("tunnel_last_seen_at") or ""))
    agent_last_seen = parse_utc(str(device.get("last_seen_at") or ""))
    tunnel_is_current = bool(device.get("tunnel_online")) and is_recent(
        str(device.get("tunnel_last_seen_at") or ""),
        SETTINGS.device_offline_after_seconds,
    )
    device["tunnel_online"] = tunnel_is_current
    last_contact = max(
        (value for value in (agent_last_seen, tunnel_last_seen) if value is not None),
        default=None,
    )
    device["last_contact_at"] = (
        last_contact.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if last_contact
        else ""
    )

    status = _json_object(device.get("last_status_json"))
    if not status:
        local_api = _json_object(device.get("local_api_json"))
        status = _json_object(local_api.get("status")) or local_api

    interfaces: list[dict[str, str]] = []
    seen_interfaces: set[tuple[str, str]] = set()
    raw_interfaces = status.get("interfaces")
    if isinstance(raw_interfaces, list):
        for item in raw_interfaces[:32]:
            if not isinstance(item, dict):
                continue
            ip = _runtime_text(item.get("ip"), 64)
            if not ip:
                continue
            try:
                if ipaddress.ip_address(ip).is_loopback:
                    continue
            except ValueError:
                pass
            interface = {
                "iface": _runtime_text(item.get("iface"), 64) or "—",
                "ip": ip,
                "cidr": _runtime_text(item.get("cidr"), 80),
                "role": _runtime_text(item.get("role"), 64) or "unknown",
            }
            key = (interface["iface"], interface["ip"])
            if key not in seen_interfaces:
                interfaces.append(interface)
                seen_interfaces.add(key)
    if not interfaces:
        try:
            local_ips = json.loads(str(device.get("local_ips_json") or "[]"))
        except json.JSONDecodeError:
            local_ips = []
        if isinstance(local_ips, list):
            for value in local_ips[:32]:
                ip = _runtime_text(value, 64)
                if ip:
                    try:
                        if ipaddress.ip_address(ip).is_loopback:
                            continue
                    except ValueError:
                        pass
                    interfaces.append(
                        {"iface": "—", "ip": ip, "cidr": "", "role": "agent"}
                    )
    interface_priority = {"wifi": 0, "ethernet": 1, "hotspot": 2, "agent": 3}
    interfaces.sort(
        key=lambda item: (
            interface_priority.get(item["role"].lower(), 9),
            item["iface"].lower(),
            item["ip"],
        )
    )
    device["runtime_interfaces"] = interfaces

    sync_mode = _json_object(status.get("sync_mode"))
    reported_role = _runtime_text(sync_mode.get("value"), 64)
    reported_code = _runtime_node_code(sync_mode.get("code"), reported_role, 64)
    device["reported_role"] = reported_role
    device["reported_role_code"] = reported_code

    runtime_online = tunnel_is_current
    groups: list[dict[str, Any]] = []
    raw_groups = status.get("groups")
    if isinstance(raw_groups, list):
        for raw_group in raw_groups[:32]:
            if not isinstance(raw_group, dict):
                continue
            members: list[dict[str, Any]] = []
            raw_members = raw_group.get("members")
            if isinstance(raw_members, list):
                for raw_member in raw_members[:128]:
                    if not isinstance(raw_member, dict):
                        continue
                    reported_connected = bool(raw_member.get("connected"))
                    members.append(
                        {
                            "device_id": _runtime_node_code(
                                raw_member.get("device_id"),
                                raw_member.get("role"),
                                100,
                            ),
                            "name": _runtime_text(raw_member.get("name"), 200),
                            "role": _runtime_text(raw_member.get("role"), 64)
                            or "unknown",
                            "connected": runtime_online and reported_connected,
                            "reported_connected": reported_connected,
                            "last_seen_at": _runtime_text(
                                raw_member.get("last_seen_at"), 100
                            ),
                            "local": bool(raw_member.get("local")),
                        }
                    )
            reported_connected = bool(raw_group.get("connected"))
            groups.append(
                {
                    "group_id": _runtime_text(raw_group.get("group_id"), 100),
                    "name": _runtime_text(raw_group.get("name"), 200),
                    "role": _runtime_text(raw_group.get("role"), 64) or "unknown",
                    "local_device_id": _runtime_node_code(
                        raw_group.get("local_device_id"),
                        raw_group.get("role"),
                        100,
                    ),
                    "connected": runtime_online and reported_connected,
                    "reported_connected": reported_connected,
                    "last_seen_at": _runtime_text(raw_group.get("last_seen_at"), 100),
                    "members": members,
                }
            )
    device["runtime_groups"] = groups
    device["runtime_groups_stale"] = bool(groups) and not runtime_online
    device["runtime_groups_reported_at"] = _runtime_text(
        device.get("tunnel_last_seen_at"), 100
    )
    device["runtime_groups_error"] = _runtime_text(status.get("groups_error"), 500)
    device["runtime_raspberry_version"] = (
        _runtime_text(status.get("raspberry_firmware_version"), 64)
        or _runtime_text(device.get("agent_version"), 64)
    )
    device["runtime_stm32_version"] = (
        _runtime_text(status.get("stm32_firmware_version"), 64)
        or _runtime_text(device.get("firmware_version"), 64)
    )
    return device


TEMPLATES.env.filters["human_time"] = human_time
TEMPLATES.env.filters["e"] = html.escape


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def audit(
    request: Request,
    action: str,
    *,
    auth: AuthContext | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    DB.audit(
        action,
        actor_user_id=int(auth.user["id"]) if auth else None,
        actor_name=str(auth.user["username"]) if auth else None,
        target_type=target_type,
        target_id=target_id,
        remote_ip=client_ip(request),
        details=details,
    )


def create_session(user_id: int, request: Request) -> tuple[str, str]:
    token = random_token(32)
    csrf = random_token(24)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=SETTINGS.session_hours)
    DB.execute(
        """
        INSERT INTO sessions(token_hash,csrf_token,user_id,created_at,last_seen_at,
                             expires_at,ip_address,user_agent,revoked)
        VALUES(?,?,?,?,?,?,?,?,0)
        """,
        (
            token_hash(token),
            csrf,
            user_id,
            utcnow(),
            utcnow(),
            expires.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            client_ip(request),
            request.headers.get("user-agent", "")[:500],
        ),
    )
    return token, csrf


def auth_context(request: Request) -> AuthContext | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    row = DB.fetchone(
        """
        SELECT s.id AS session_id,s.csrf_token,s.expires_at,s.revoked,
               u.id,u.username,u.role,u.active,u.force_password_change,
               u.auth_provider,u.display_name,u.email,u.legacy_distributor,
               u.legacy_customer,u.legacy_read_only
        FROM sessions s JOIN users u ON u.id=s.user_id
        WHERE s.token_hash=?
        """,
        (token_hash(token),),
    )
    if row is None or int(row["revoked"]) or not int(row["active"]):
        return None
    expires = parse_utc(row["expires_at"])
    if expires is None or expires <= datetime.now(timezone.utc):
        return None
    DB.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (utcnow(), row["session_id"]))
    stored_user = dict(row)
    effective_role = "admin" if is_legacy_tagit_user(stored_user) else row["role"]
    return AuthContext(
        user={
            "id": row["id"],
            "username": row["username"],
            "role": effective_role,
            "force_password_change": row["force_password_change"],
            "auth_provider": row["auth_provider"],
            "display_name": row["display_name"],
            "email": row["email"],
            "legacy_distributor": row["legacy_distributor"],
            "legacy_customer": row["legacy_customer"],
            "legacy_read_only": row["legacy_read_only"],
        },
        session={
            "id": row["session_id"],
            "csrf_token": row["csrf_token"],
            "expires_at": row["expires_at"],
        },
    )


def require_auth(request: Request, *, admin: bool = False) -> AuthContext:
    auth = auth_context(request)
    if auth is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if admin and auth.user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return auth


def verify_csrf(auth: AuthContext, value: str) -> None:
    if not value or not constant_time_equal(str(auth.session["csrf_token"]), value):
        raise HTTPException(status_code=400, detail="Invalid CSRF token")


def template(
    request: Request,
    name: str,
    *,
    auth: AuthContext | None = None,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    payload = {
        "request": request,
        "user": auth.user if auth else None,
        "csrf": auth.session["csrf_token"] if auth else "",
        "portal_version": __version__,
        "temporary_tls": SETTINGS.temporary_tls_warning,
        "path_prefix": PATH_PREFIX,
        "local_auth_enabled": SETTINGS.local_auth_enabled,
        "legacy_auth_enabled": SETTINGS.legacy_auth_enabled,
        "display_username": display_username,
        **context,
    }
    return TEMPLATES.TemplateResponse(request, name, payload, status_code=status_code)


def display_username(user: Any) -> str:
    """Return a readable login without changing the internal BMI20 identity."""
    try:
        username = str(user.get("username") or "")
        provider = str(user.get("auth_provider") or "")
    except AttributeError:
        return ""
    return legacy_username_to_display(username) if provider == "legacy_mqtt" else username


def redirect_login(request: Request) -> RedirectResponse:
    return RedirectResponse(
        url=f"{public_path('/login')}?next={request_public_path(request)}",
        status_code=303,
    )


def _prune_login_failures(now: float) -> None:
    stale = []
    for key, values in LOGIN_FAILURES.items():
        kept = [value for value in values if now - value < 900]
        if kept:
            LOGIN_FAILURES[key] = kept
        else:
            stale.append(key)
    for key in stale:
        LOGIN_FAILURES.pop(key, None)
    if len(LOGIN_FAILURES) > MAX_LOGIN_RATE_KEYS:
        # Bound memory even under a distributed username-spray attack.
        for key in list(LOGIN_FAILURES)[: len(LOGIN_FAILURES) - MAX_LOGIN_RATE_KEYS]:
            LOGIN_FAILURES.pop(key, None)


def login_rate_limited(key: str) -> bool:
    now = asyncio.get_event_loop().time()
    _prune_login_failures(now)
    return len(LOGIN_FAILURES.get(key, [])) >= 10


def add_login_failure(key: str) -> None:
    now = asyncio.get_event_loop().time()
    _prune_login_failures(now)
    LOGIN_FAILURES.setdefault(key, []).append(now)


def persist_legacy_identity(identity: LegacyIdentity) -> dict[str, Any]:
    now = utcnow()
    force_admin = (
        identity.username.casefold() in LEGACY_ADMIN_USERS
        or identity.distributor.strip().casefold()
        == SETTINGS.legacy_tagit_distributor.strip().casefold()
    )
    with DB.transaction(immediate=True) as connection:
        existing = connection.execute(
            "SELECT id,auth_provider,role,active FROM users WHERE username=? COLLATE NOCASE",
            (identity.username,),
        ).fetchone()
        if existing is not None and existing["auth_provider"] != "legacy_mqtt":
            raise LegacyAuthError(
                "account_conflict",
                "A local BMI30 account already uses this username",
            )
        if existing is None:
            role = "admin" if force_admin else "user"
            connection.execute(
                """
                INSERT INTO users(
                    username,password_hash,auth_provider,role,active,
                    force_password_change,display_name,email,legacy_distributor,
                    legacy_customer,legacy_read_only,legacy_profile_at,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    identity.username,
                    LEGACY_PASSWORD_SENTINEL,
                    "legacy_mqtt",
                    role,
                    1,
                    0,
                    identity.display_name,
                    identity.email,
                    identity.distributor,
                    identity.customer,
                    1 if identity.read_only else 0,
                    now,
                    now,
                    now,
                ),
            )
        else:
            # BMI20 remains the credential/profile authority, while Hub access is
            # an independent administrator-controlled switch.  Never reactivate
            # a deliberately disabled Hub account during a later BMI20 sign-in.
            role = "admin" if force_admin else str(existing["role"])
            connection.execute(
                """
                UPDATE users
                SET role=?,display_name=?,email=?,legacy_distributor=?,
                    legacy_customer=?,legacy_read_only=?,legacy_profile_at=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    role,
                    identity.display_name,
                    identity.email,
                    identity.distributor,
                    identity.customer,
                    1 if identity.read_only else 0,
                    now,
                    now,
                    existing["id"],
                ),
            )
        row = connection.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE",
            (identity.username,),
        ).fetchone()
    if row is None:
        raise LegacyAuthError("unavailable", "Legacy user could not be synchronized")
    return dict(row)


def allocate_port() -> int:
    rows = DB.fetchall("SELECT remote_port FROM devices WHERE remote_port IS NOT NULL")
    used = {int(row["remote_port"]) for row in rows}
    used.update(MONITOR.current_listeners)
    span = SETTINGS.port_max - SETTINGS.port_min + 1
    for _ in range(min(span, 50000)):
        candidate = SETTINGS.port_min + secrets.randbelow(span)
        if candidate not in used:
            return candidate
    raise RuntimeError("No free BMI30 tunnel port is available")


def is_legacy_tagit_user(user: dict[str, Any]) -> bool:
    """Return whether a BMI20 identity belongs to the configured Tagit distributor."""
    if user.get("auth_provider") != "legacy_mqtt":
        return False
    configured = SETTINGS.legacy_tagit_distributor.strip().casefold()
    distributor = str(user.get("legacy_distributor") or "").strip().casefold()
    return bool(configured) and distributor == configured


def legacy_scope_allows(user: dict[str, Any], system: dict[str, Any]) -> bool:
    if user.get("auth_provider") != "legacy_mqtt":
        return False
    user_distributor = str(user.get("legacy_distributor") or "").strip()
    system_distributor = str(system.get("legacy_distributor") or "").strip()
    if not user_distributor or not system_distributor:
        return False
    distributor_allowed = is_legacy_tagit_user(user) or (
        user_distributor.casefold() == system_distributor.casefold()
    )
    if not distributor_allowed:
        return False
    if not int(user.get("legacy_read_only") or 0):
        return True
    user_customer = str(user.get("legacy_customer") or "").strip()
    system_customer = str(system.get("legacy_customer") or "").strip()
    return bool(user_customer and system_customer) and (
        user_customer.casefold() == system_customer.casefold()
    )


def system_accessible(
    user: dict[str, Any], system: dict[str, Any], *, open_required: bool = True
) -> bool:
    if user["role"] == "admin":
        return True
    assigned = DB.fetchone(
        "SELECT can_open FROM user_systems WHERE user_id=? AND system_id=?",
        (int(user["id"]), int(system["id"])),
    )
    if assigned is not None and (
        not open_required or int(assigned["can_open"])
    ):
        return True
    return legacy_scope_allows(user, system)


def device_accessible(
    user: dict[str, Any], device_id: str, *, open_required: bool = True
) -> dict[str, Any] | None:
    row = DB.fetchone(
        """
        SELECT d.*,s.legacy_distributor AS system_legacy_distributor,
               s.legacy_customer AS system_legacy_customer
        FROM devices d LEFT JOIN systems s ON s.id=d.system_id
        WHERE d.device_id=?
        """,
        (device_id,),
    )
    if row is None:
        return None
    device = dict(row)
    if user["role"] == "admin":
        return device
    # Tagit is the owner/operator distributor. Its BMI20 users must be able to
    # see every BMI30 device even before an administrator assigns a system.
    if is_legacy_tagit_user(user):
        return device
    direct = DB.fetchone(
        "SELECT can_open FROM device_users WHERE user_id=? AND device_id=?",
        (int(user["id"]), int(device["id"])),
    )
    if direct is not None and (not open_required or int(direct["can_open"])):
        return device
    if device.get("system_id") is None:
        return None
    system = {
        "id": device["system_id"],
        "legacy_distributor": device.get("system_legacy_distributor", ""),
        "legacy_customer": device.get("system_legacy_customer", ""),
    }
    return device if system_accessible(user, system, open_required=open_required) else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    FIRMWARE_STORAGE.ensure_layout()
    CENTRAL_STORE.initialize_optional()
    monitor_task = asyncio.create_task(MONITOR.run(), name="bmi30-device-monitor")
    try:
        yield
    finally:
        await MONITOR.stop()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="BMI30 Hub",
    version=__version__,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    root_path=PATH_PREFIX,
)
app.include_router(build_firmware_router(FIRMWARE_STORAGE))
app.include_router(build_central_router(CENTRAL_STORE, FIRMWARE_STORAGE.authenticate_device, system_scope_resolver(DB), require_https=True, enable_captures=False))
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/v1/agent/"):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_AGENT_BODY:
                    return JSONResponse({"detail": "Request body too large"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    response.headers[
        "Content-Security-Policy"
    ] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self' 'unsafe-inline'"
    return response


@app.get("/static/{asset_path:path}", name="static")
async def static_asset(asset_path: str) -> FileResponse:
    static_root = (PACKAGE_ROOT / "static").resolve()
    candidate = (static_root / asset_path).resolve()
    try:
        candidate.relative_to(static_root)
    except ValueError:
        raise HTTPException(status_code=404) from None
    if not candidate.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(candidate)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "BMI30HubPortal",
        "version": __version__,
        "monitor_listeners": len(MONITOR.current_listeners),
    }


@app.get("/")
async def root(request: Request):
    auth = auth_context(request)
    if auth is None:
        return RedirectResponse(public_path("/login"), status_code=303)
    if auth.user["role"] == "admin":
        return RedirectResponse(public_path("/admin/devices"), status_code=303)
    return RedirectResponse(public_path("/dashboard"), status_code=303)


@app.get("/login")
async def login_page(request: Request):
    if SETTINGS.legacy_auth_enabled and not SETTINGS.local_auth_enabled:
        return RedirectResponse("/", status_code=303)
    if auth_context(request):
        return RedirectResponse(public_path("/"), status_code=303)
    return template(
        request,
        "login.html",
        next_url=request.query_params.get("next", public_path("/")),
    )


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_url = str(form.get("next", public_path("/")))
    allowed_prefix = public_path("/")
    if (
        not next_url.startswith(allowed_prefix)
        or next_url.startswith("//")
    ):
        next_url = allowed_prefix
    key = f"{client_ip(request)}|{username.lower()}"
    if login_rate_limited(key):
        return template(
            request,
            "login.html",
            status_code=429,
            error="Too many failed attempts. Please try again later.",
            next_url=next_url,
        )
    row = DB.fetchone("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,))
    authenticated = False
    local_login = row is not None and row["auth_provider"] == "local"
    if local_login:
        authenticated = bool(
            SETTINGS.local_auth_enabled
            and int(row["active"])
            and verify_password(row["password_hash"], password)
        )
    elif SETTINGS.legacy_auth_enabled:
        try:
            validated_username = validate_legacy_username(
                legacy_username_to_raw(username)
            )
            if not password or len(password) > 512:
                raise LegacyAuthError("invalid_credentials", "Invalid credentials")
            identity = await asyncio.to_thread(
                LEGACY_AUTH.authenticate, validated_username, password
            )
            row = persist_legacy_identity(identity)
            authenticated = bool(int(row["active"]))
        except (SecurityError, LegacyAuthError) as exc:
            error_code = getattr(exc, "code", "invalid_credentials")
            if error_code in {"unavailable", "invalid_profile", "profile_rejected"}:
                audit(
                    request,
                    "login_backend_unavailable",
                    target_type="user",
                    target_id=username,
                    details={"provider": "legacy_mqtt", "code": error_code},
                )
                return template(
                    request,
                    "login.html",
                    status_code=503,
                    error="The sign-in service is temporarily unavailable. Please try again later.",
                    next_url=next_url,
                )
    if not authenticated or row is None:
        add_login_failure(key)
        audit(
            request,
            "login_failed",
            target_type="user",
            target_id=username,
            details={"provider": "local" if local_login else "legacy_mqtt"},
        )
        return template(
            request,
            "login.html",
            status_code=401,
            error="Invalid username or password.",
            next_url=next_url,
        )
    LOGIN_FAILURES.pop(key, None)
    if row["auth_provider"] == "local" and password_needs_rehash(row["password_hash"]):
        DB.execute(
            "UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
            (hash_password(password), utcnow(), row["id"]),
        )
    token, _csrf = create_session(int(row["id"]), request)
    DB.execute("UPDATE users SET last_login_at=?,updated_at=? WHERE id=?", (utcnow(), utcnow(), row["id"]))
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        token,
        secure=SETTINGS.cookie_secure,
        httponly=True,
        samesite="lax",
        max_age=SETTINGS.session_hours * 3600,
        path=COOKIE_PATH,
    )
    audit(request, "login_success", target_type="user", target_id=str(row["id"]))
    return response


@app.post("/logout")
async def logout(request: Request):
    auth = require_auth(request)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    DB.execute("UPDATE sessions SET revoked=1 WHERE id=?", (auth.session["id"],))
    response = RedirectResponse(public_path("/login"), status_code=303)
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
    return response


@app.get("/dashboard")
async def dashboard(request: Request):
    auth = auth_context(request)
    if auth is None:
        return redirect_login(request)
    if auth.user["role"] == "admin":
        return RedirectResponse(public_path("/admin/devices"), status_code=303)
    if is_legacy_tagit_user(auth.user):
        devices = [dict(row) for row in DB.fetchall(
            """
            SELECT * FROM devices
            WHERE state='approved'
            ORDER BY logical_number,display_name,device_id
            """
        )]
        for device in devices:
            device["display_device_id"] = display_device_id(device)
            enrich_device_runtime(device)
            device["agent_online"] = is_recent(
                device.get("last_seen_at"), SETTINGS.device_offline_after_seconds
            )
        return template(
            request,
            "dashboard.html",
            auth=auth,
            systems=[{
                "id": None,
                "name": "All BMI30 devices",
                "comment": "",
                "devices": devices,
            }],
        )
    systems = [
        system
        for system in (
            dict(row)
            for row in DB.fetchall(
                "SELECT * FROM systems WHERE active=1 ORDER BY name"
            )
        )
        if system_accessible(auth.user, system, open_required=True)
    ]
    for system in systems:
        devices = [dict(row) for row in DB.fetchall(
            """
            SELECT * FROM devices
            WHERE system_id=? AND state='approved'
            ORDER BY logical_number,display_name,device_id
            """,
            (system["id"],),
        )]
        for device in devices:
            device["display_device_id"] = display_device_id(device)
            enrich_device_runtime(device)
            device["agent_online"] = is_recent(
                device.get("last_seen_at"), SETTINGS.device_offline_after_seconds
            )
        system["devices"] = devices
    accessible_system_ids = {int(system["id"]) for system in systems}
    direct_devices = [dict(row) for row in DB.fetchall(
        """
        SELECT d.* FROM devices d
        JOIN device_users du ON du.device_id=d.id
        WHERE du.user_id=? AND du.can_open=1 AND d.state='approved'
        ORDER BY d.logical_number,d.display_name,d.device_id
        """,
        (auth.user["id"],),
    )]
    direct_devices = [
        device
        for device in direct_devices
        if device.get("system_id") is None
        or int(device["system_id"]) not in accessible_system_ids
    ]
    if direct_devices:
        for device in direct_devices:
            device["display_device_id"] = display_device_id(device)
            enrich_device_runtime(device)
            device["agent_online"] = is_recent(
                device.get("last_seen_at"), SETTINGS.device_offline_after_seconds
            )
        systems.append({
            "id": None,
            "name": "Individually assigned devices",
            "comment": "Access was granted directly to the device.",
            "devices": direct_devices,
        })
    return template(request, "dashboard.html", auth=auth, systems=systems)


@app.post("/api/v1/agent/checkin")
async def agent_checkin(request: Request):
    raw = await request.body()
    if len(raw) > MAX_AGENT_BODY:
        raise HTTPException(status_code=413, detail="Request body too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    try:
        reported_device_id = validate_device_id(str(payload.get("device_id", "")))
        raspberry_serial = validate_raspberry_serial(
            str(payload.get("raspberry_serial", ""))
        )
        device_id = device_id_from_raspberry_serial(raspberry_serial)
        if reported_device_id != device_id:
            audit(
                request,
                "hardware_identity_mismatch",
                target_type="device",
                target_id=reported_device_id,
                details={
                    "expected_device_id": device_id,
                    "raspberry_serial": raspberry_serial,
                },
            )
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "code": "hardware_identity_mismatch",
                    "detail": "DEVICE_ID does not match this Raspberry hardware serial",
                    "expected_device_id": device_id,
                    "reset_identity_required": True,
                },
            )
        public_key = canonical_public_key(str(payload.get("public_key", "")), device_id)
        fingerprint = public_key_fingerprint(public_key)
    except SecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    auth_header = request.headers.get("authorization", "")
    bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    presented_token = bearer or str(payload.get("api_token", ""))
    if len(presented_token) < 40 or len(presented_token) > 256:
        raise HTTPException(status_code=400, detail="Invalid device API token")
    presented_hash = token_hash(presented_token)
    raspberry_version, stm32_version = extract_checkin_versions(payload)

    local_ips: list[str] = []
    for value in payload.get("local_ips", []) if isinstance(payload.get("local_ips"), list) else []:
        try:
            address = ipaddress.ip_address(str(value).split("%", 1)[0])
        except ValueError:
            continue
        if not address.is_unspecified and not address.is_loopback:
            local_ips.append(str(address))
    local_ips = list(dict.fromkeys(local_ips))[:16]
    now = utcnow()
    remote_ip = client_ip(request)
    row = DB.fetchone("SELECT * FROM devices WHERE device_id=?", (device_id,))
    serial_owner = DB.fetchone(
        "SELECT device_id FROM devices WHERE raspberry_serial=? AND device_id<>?",
        (raspberry_serial, device_id),
    )
    if serial_owner is not None:
        raise HTTPException(
            status_code=409,
            detail="Raspberry hardware serial is already bound to another device record",
        )
    if row is None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        recent_new = DB.fetchone(
            "SELECT COUNT(*) AS count FROM devices WHERE public_ip=? AND first_seen_at>=?",
            (remote_ip, cutoff),
        )
        if recent_new is not None and int(recent_new["count"]) >= 100:
            raise HTTPException(
                status_code=429,
                detail="Too many new device registrations from this address",
            )
        duplicate = DB.fetchone(
            "SELECT device_id FROM devices WHERE public_key_fingerprint=?",
            (fingerprint,),
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="SSH key already belongs to another DEVICE_ID")
        DB.execute(
            """
            INSERT INTO devices(
                device_id,public_key,public_key_fingerprint,api_token_hash,state,
                hostname,raspberry_serial,model,firmware_version,agent_version,
                local_ips_json,public_ip,tunnel_service_json,local_api_json,
                first_seen_at,last_seen_at,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                device_id,
                public_key,
                fingerprint,
                presented_hash,
                "pending",
                str(payload.get("hostname") or "")[:128] or None,
                raspberry_serial,
                str(payload.get("model") or "")[:256] or None,
                stm32_version,
                raspberry_version,
                json.dumps(local_ips, separators=(",", ":")),
                remote_ip,
                json.dumps(payload.get("tunnel_service") or {}, separators=(",", ":")),
                json.dumps(payload.get("local_api") or {}, separators=(",", ":")),
                now,
                now,
                now,
                now,
            ),
        )
        audit(request, "device_registered", target_type="device", target_id=device_id, details={"fingerprint": fingerprint})
        row = DB.fetchone("SELECT * FROM devices WHERE device_id=?", (device_id,))
    else:
        if row["public_key_fingerprint"] != fingerprint:
            pending_fingerprint = str(row["pending_public_key_fingerprint"] or "")
            if not pending_fingerprint or pending_fingerprint != fingerprint:
                DB.execute(
                    """
                    INSERT INTO registration_conflicts(
                        created_at,device_id,stored_fingerprint,presented_fingerprint,
                        remote_ip,payload_json
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        now,
                        device_id,
                        row["public_key_fingerprint"],
                        fingerprint,
                        remote_ip,
                        json.dumps(
                            {
                                key: value
                                for key, value in payload.items()
                                if key != "api_token"
                            },
                            ensure_ascii=False,
                        )[:32000],
                    ),
                )
            if row["state"] == "blocked":
                raise HTTPException(
                    status_code=409,
                    detail="Blocked device credentials cannot be replaced",
                )
            if pending_fingerprint and pending_fingerprint != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="Another credential replacement is already awaiting administrator approval",
                )
            if pending_fingerprint:
                pending_hash = str(row["pending_api_token_hash"] or "")
                if not pending_hash or not constant_time_equal(pending_hash, presented_hash):
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid pending device API token",
                    )
                requested_at = str(row["credential_rotation_requested_at"] or now)
            else:
                requested_at = now
            with DB.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE devices
                    SET pending_public_key=?,pending_public_key_fingerprint=?,
                        pending_api_token_hash=?,credential_rotation_requested_at=?,
                        hostname=?,raspberry_serial=?,model=?,
                        firmware_version=COALESCE(?,firmware_version),
                        agent_version=COALESCE(?,agent_version),
                        local_ips_json=?,public_ip=?,last_seen_at=?,
                        tunnel_service_json=?,local_api_json=?,updated_at=?
                    WHERE id=?
                    """,
                    (
                        public_key,
                        fingerprint,
                        presented_hash,
                        requested_at,
                        str(payload.get("hostname") or "")[:128] or None,
                        raspberry_serial,
                        str(payload.get("model") or "")[:256] or None,
                        stm32_version,
                        raspberry_version,
                        json.dumps(local_ips, separators=(",", ":")),
                        remote_ip,
                        now,
                        json.dumps(payload.get("tunnel_service") or {}, separators=(",", ":")),
                        json.dumps(payload.get("local_api") or {}, separators=(",", ":")),
                        now,
                        row["id"],
                    ),
                )
            if not pending_fingerprint:
                audit(
                    request,
                    "device_credential_rotation_requested",
                    target_type="device",
                    target_id=device_id,
                    details={
                        "current_fingerprint": str(row["public_key_fingerprint"]),
                        "requested_fingerprint": fingerprint,
                        "raspberry_serial": raspberry_serial,
                    },
                )
            return JSONResponse(
                {
                    "ok": True,
                    "state": "pending",
                    "device_id": device_id,
                    "message": "New credentials are waiting for administrator approval",
                    "next_checkin_seconds": SETTINGS.agent_checkin_seconds,
                    "tunnel_enabled": False,
                    "credential_rotation_pending": True,
                }
            )
        if row["api_token_hash"]:
            if not constant_time_equal(str(row["api_token_hash"]), presented_hash):
                raise HTTPException(status_code=401, detail="Invalid device API token")
        else:
            pending_hash = row["pending_api_token_hash"]
            if pending_hash and not constant_time_equal(str(pending_hash), presented_hash):
                raise HTTPException(
                    status_code=401,
                    detail="Device is awaiting administrator token confirmation",
                )
            DB.execute(
                """
                UPDATE devices
                SET pending_api_token_hash=?, state='pending', updated_at=?
                WHERE id=?
                """,
                (presented_hash, now, row["id"]),
            )
        DB.execute(
            """
            UPDATE devices
            SET hostname=?,raspberry_serial=?,model=?,
                firmware_version=COALESCE(?, firmware_version),
                agent_version=COALESCE(?, agent_version),
                local_ips_json=?,public_ip=?,last_seen_at=?,
                tunnel_service_json=?,local_api_json=?,updated_at=?
            WHERE id=?
            """,
            (
                str(payload.get("hostname") or "")[:128] or None,
                raspberry_serial,
                str(payload.get("model") or "")[:256] or None,
                stm32_version,
                raspberry_version,
                json.dumps(local_ips, separators=(",", ":")),
                remote_ip,
                now,
                json.dumps(payload.get("tunnel_service") or {}, separators=(",", ":")),
                json.dumps(payload.get("local_api") or {}, separators=(",", ":")),
                now,
                row["id"],
            ),
        )
        row = DB.fetchone("SELECT * FROM devices WHERE device_id=?", (device_id,))

    assert row is not None
    response: dict[str, Any] = {
        "ok": True,
        "state": row["state"],
        "device_id": device_id,
        "message": {
            "pending": "Waiting for administrator approval",
            "approved": "Device approved",
            "blocked": "Device blocked by an administrator",
            "rejected": "Registration rejected",
        }.get(row["state"], ""),
        "next_checkin_seconds": SETTINGS.agent_checkin_seconds,
        "tunnel_enabled": row["state"] == "approved",
    }
    if row["state"] == "approved":
        response.update(
            {
                "remote_port": row["remote_port"],
                "listen_address": row["listen_address"],
                "ssh_host": SETTINGS.ssh_host,
                "ssh_port": SETTINGS.ssh_port,
                "ssh_user": SETTINGS.ssh_user,
                "ssh_host_public_key": SETTINGS.ssh_host_public_key,
            }
        )
    return JSONResponse(response)


@app.get("/api/v1/ui/devices")
async def ui_device_catalog(request: Request):
    auth = auth_context(request)
    if auth is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    rows = [dict(row) for row in DB.fetchall(
        """
        SELECT device_id,state,hostname,raspberry_serial,model,
               firmware_version,agent_version,tunnel_online,
               pending_public_key_fingerprint,
               distributor_name,customer_name,location_name,device_notes,
               installed_on,technician_name,first_seen_at,last_seen_at,
               tunnel_last_seen_at,
               local_ips_json,local_api_json,last_status_json,device_role
        FROM devices
        ORDER BY CASE WHEN pending_public_key_fingerprint IS NOT NULL THEN 0
                      WHEN state='pending' THEN 1 WHEN state='approved' THEN 2 ELSE 3 END,
                 last_seen_at DESC,device_id
        """
    )]
    devices: list[dict[str, Any]] = []
    for device in rows:
        if auth.user["role"] != "admin":
            if device["state"] != "approved" or device_accessible(
                auth.user, str(device["device_id"]), open_required=False
            ) is None:
                continue
        first_seen = parse_utc(device.get("first_seen_at"))
        installed_epoch = 0
        try:
            installed = datetime.fromisoformat(str(device.get("installed_on") or ""))
            installed_epoch = int(installed.replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
        device_id = str(device["device_id"])
        enrich_device_runtime(device)
        last_contact = parse_utc(device.get("last_contact_at"))
        agent_online = is_recent(
            device.get("last_seen_at"), SETTINGS.device_offline_after_seconds
        )
        devices.append({
            "device_id": device_id,
            "display_device_id": display_device_id(device),
            "hardware": "BMI30 v1.0",
            # The Raspberry agent version and STM32 application firmware are
            # intentionally returned as separate values for the legacy table.
            "raspberry_version": str(device.get("runtime_raspberry_version") or ""),
            "stm32_version": str(device.get("runtime_stm32_version") or ""),
            "hostname": str(device.get("hostname") or ""),
            "model": str(device.get("model") or ""),
            "state": (
                "pending"
                if auth.user["role"] == "admin"
                and device.get("pending_public_key_fingerprint")
                else str(device["state"])
            ),
            "credential_rotation_pending": bool(
                device.get("pending_public_key_fingerprint")
            ),
            "connected": bool(device.get("tunnel_online")),
            "tunnel_online": bool(device.get("tunnel_online")),
            "agent_online": agent_online,
            "distributor": str(device.get("distributor_name") or ""),
            "customer": str(device.get("customer_name") or ""),
            "location": str(device.get("location_name") or ""),
            "notes": str(device.get("device_notes") or ""),
            "install_epoch": installed_epoch,
            "technician": str(device.get("technician_name") or ""),
            "first_seen_epoch": int(first_seen.timestamp()) if first_seen else 0,
            "last_seen_epoch": int(last_contact.timestamp()) if last_contact else 0,
            "last_seen_at": str(device.get("last_contact_at") or ""),
            "last_seen_display": human_time(device.get("last_contact_at")),
            "agent_last_seen_at": str(device.get("last_seen_at") or ""),
            "agent_last_seen_display": human_time(device.get("last_seen_at")),
            "tunnel_last_seen_at": str(device.get("tunnel_last_seen_at") or ""),
            "tunnel_last_seen_display": human_time(
                device.get("tunnel_last_seen_at")
            ),
            "reported_role": str(device.get("reported_role") or ""),
            "local_interfaces": device.get("runtime_interfaces") or [],
            "open_url": public_path(
                f"/admin/devices/{device_id}"
                if auth.user["role"] == "admin"
                else f"/device/{device_id}/"
            ),
        })
    return JSONResponse(
        {"devices": devices},
        headers={"Cache-Control": "no-store"},
    )


async def load_admin_device_rows() -> list[dict[str, Any]]:
    """Load one canonical snapshot for the full administrator device table."""
    await resolve_public_ip_locations()
    devices = [dict(row) for row in DB.fetchall(
        """
        SELECT d.*,s.name AS system_name,
               g.country_name,g.region_name,g.city_name,g.lookup_success
        FROM devices d LEFT JOIN systems s ON s.id=d.system_id
        LEFT JOIN public_ip_locations g ON g.ip_address=d.public_ip
        ORDER BY CASE WHEN d.pending_public_key_fingerprint IS NOT NULL THEN 0
                      WHEN d.state='pending' THEN 1 WHEN d.state='approved' THEN 2 ELSE 3 END,
                 d.last_seen_at DESC
        """
    )]
    for device in devices:
        device["display_device_id"] = display_device_id(device)
        enrich_device_runtime(device)
        device["agent_online"] = is_recent(device.get("last_seen_at"), SETTINGS.device_offline_after_seconds)
        device["public_ip_location"] = public_ip_location_text(device)
    return devices


@app.get("/admin/devices")
async def admin_devices(request: Request):
    auth = auth_context(request)
    if auth is None:
        return redirect_login(request)
    if auth.user["role"] != "admin":
        raise HTTPException(status_code=403)
    devices = await load_admin_device_rows()
    return template(request, "admin_devices.html", auth=auth, devices=devices)


@app.get("/admin/devices-live")
async def admin_devices_live(request: Request):
    """Return fresh rows without replacing the search and sorting controls."""
    auth = auth_context(request)
    if auth is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if auth.user["role"] != "admin":
        raise HTTPException(status_code=403)
    devices = await load_admin_device_rows()
    response = template(
        request,
        "_admin_device_rows.html",
        auth=auth,
        devices=devices,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/admin/devices/{device_id}")
async def admin_device_edit(request: Request, device_id: str):
    auth = auth_context(request)
    if auth is None:
        return redirect_login(request)
    if auth.user["role"] != "admin":
        raise HTTPException(status_code=403)
    device_id = validate_device_id(device_id)
    row = DB.fetchone("SELECT * FROM devices WHERE device_id=?", (device_id,))
    if row is None:
        raise HTTPException(status_code=404)
    systems = [dict(item) for item in DB.fetchall("SELECT * FROM systems ORDER BY name")]
    masters = [dict(item) for item in DB.fetchall(
        "SELECT id,device_id,display_name FROM devices WHERE device_role='master' AND device_id<>? ORDER BY display_name,device_id",
        (device_id,),
    )]
    assigned_users = [dict(item) for item in DB.fetchall(
        """
        SELECT u.id,u.username,
               CASE WHEN du.user_id IS NULL THEN 0 ELSE 1 END AS assigned
        FROM users u LEFT JOIN device_users du
          ON du.user_id=u.id AND du.device_id=?
        WHERE u.role='user' ORDER BY u.username
        """,
        (row["id"],),
    )]
    device = dict(row)
    device["display_device_id"] = display_device_id(device)
    enrich_device_runtime(device)
    history_entries = load_device_history(device_id)
    return template(
        request,
        "admin_device_edit.html",
        auth=auth,
        device=device,
        systems=systems,
        masters=masters,
        assigned_users=assigned_users,
        history_entries=history_entries,
    )


@app.post("/admin/devices/{device_id}/save")
async def admin_device_save(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    role = str(form.get("device_role", "unassigned"))
    if role not in {"unassigned", "master", "slave"}:
        raise HTTPException(status_code=400, detail="Invalid role")
    system_id = int(form.get("system_id")) if str(form.get("system_id", "")).isdigit() else None
    master_id = int(form.get("master_device_id")) if str(form.get("master_device_id", "")).isdigit() else None
    installed_on = str(form.get("installed_on", "")).strip()
    if installed_on and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", installed_on):
        raise HTTPException(status_code=400, detail="Invalid install date")
    with DB.transaction(immediate=True) as connection:
        device_row = connection.execute(
            "SELECT id FROM devices WHERE device_id=?", (device_id,)
        ).fetchone()
        if device_row is None:
            raise HTTPException(status_code=404)
        connection.execute(
            """
            UPDATE devices SET display_name=?,inventory_number=?,logical_number=?,
                distributor_name=?,customer_name=?,location_name=?,device_notes=?,
                installed_on=?,technician_name=?,
                device_role=?,master_device_id=?,system_id=?,admin_comment=?,
                user_comment=?,updated_at=? WHERE device_id=?
            """,
            (
                str(form.get("display_name", ""))[:200],
                str(form.get("inventory_number", ""))[:100],
                str(form.get("logical_number", ""))[:100],
                str(form.get("distributor_name", ""))[:200],
                str(form.get("customer_name", ""))[:200],
                str(form.get("location_name", ""))[:200],
                str(form.get("device_notes", ""))[:1000],
                installed_on,
                str(form.get("technician_name", ""))[:200],
                role,
                master_id if role == "slave" else None,
                system_id,
                str(form.get("admin_comment", ""))[:4000],
                str(form.get("user_comment", ""))[:4000],
                utcnow(),
                device_id,
            ),
        )
        connection.execute("DELETE FROM device_users WHERE device_id=?", (device_row["id"],))
        for key, value in form.multi_items():
            if key == "user_id" and str(value).isdigit():
                connection.execute(
                    "INSERT OR IGNORE INTO device_users(user_id,device_id,can_open) VALUES(?,?,1)",
                    (int(value), device_row["id"]),
                )
    audit(request, "device_updated", auth=auth, target_type="device", target_id=device_id)
    return RedirectResponse(public_path(f"/admin/devices/{device_id}"), status_code=303)


@app.post("/admin/devices/{device_id}/history/add")
async def admin_device_history_add(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    if DB.fetchone("SELECT id FROM devices WHERE device_id=?", (device_id,)) is None:
        raise HTTPException(status_code=404)
    occurred_at = normalize_history_timestamp(str(form.get("occurred_at", "")))
    entry_text = normalize_history_text(form.get("entry_text"))
    now = utcnow()
    history_id = DB.execute(
        """
        INSERT INTO device_history(
            device_id,occurred_at,entry_text,author_user_id,author_name,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            device_id,
            occurred_at,
            entry_text,
            int(auth.user["id"]),
            display_username(auth.user),
            now,
            now,
        ),
    )
    audit(
        request,
        "device_history_added",
        auth=auth,
        target_type="device",
        target_id=device_id,
        details={"history_id": history_id, "occurred_at": occurred_at},
    )
    return RedirectResponse(
        public_path(f"/admin/devices/{device_id}#device-history"), status_code=303
    )


@app.post("/admin/devices/{device_id}/history/{history_id}/save")
async def admin_device_history_save(
    request: Request, device_id: str, history_id: int
):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    occurred_at = normalize_history_timestamp(str(form.get("occurred_at", "")))
    entry_text = normalize_history_text(form.get("entry_text"))
    with DB.transaction(immediate=True) as connection:
        updated = connection.execute(
            """
            UPDATE device_history
            SET occurred_at=?,entry_text=?,author_user_id=?,author_name=?,updated_at=?
            WHERE id=? AND device_id=?
            """,
            (
                occurred_at,
                entry_text,
                int(auth.user["id"]),
                display_username(auth.user),
                utcnow(),
                history_id,
                device_id,
            ),
        ).rowcount
        if updated != 1:
            raise HTTPException(status_code=404)
    audit(
        request,
        "device_history_updated",
        auth=auth,
        target_type="device",
        target_id=device_id,
        details={"history_id": history_id, "occurred_at": occurred_at},
    )
    return RedirectResponse(
        public_path(f"/admin/devices/{device_id}#device-history"), status_code=303
    )


@app.post("/admin/devices/{device_id}/history/{history_id}/delete")
async def admin_device_history_delete(
    request: Request, device_id: str, history_id: int
):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    with DB.transaction(immediate=True) as connection:
        deleted = connection.execute(
            "DELETE FROM device_history WHERE id=? AND device_id=?",
            (history_id, device_id),
        ).rowcount
        if deleted != 1:
            raise HTTPException(status_code=404)
    audit(
        request,
        "device_history_deleted",
        auth=auth,
        target_type="device",
        target_id=device_id,
        details={"history_id": history_id},
    )
    return RedirectResponse(
        public_path(f"/admin/devices/{device_id}#device-history"), status_code=303
    )


@app.get("/admin/devices/{device_id}/history.csv")
async def admin_device_history_csv(request: Request, device_id: str):
    require_auth(request, admin=True)
    device_id = validate_device_id(device_id)
    if DB.fetchone("SELECT id FROM devices WHERE device_id=?", (device_id,)) is None:
        raise HTTPException(status_code=404)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("Device ID", "Occurred at (UTC)", "Record", "Author"))
    for entry in load_device_history(device_id):
        writer.writerow(
            (
                device_id,
                entry["occurred_at"],
                entry["entry_text"],
                entry["author_name"],
            )
        )
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{device_id}-service-history.csv"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/admin/devices/{device_id}/history/print")
async def admin_device_history_print(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    device_id = validate_device_id(device_id)
    row = DB.fetchone("SELECT * FROM devices WHERE device_id=?", (device_id,))
    if row is None:
        raise HTTPException(status_code=404)
    device = dict(row)
    device["display_device_id"] = display_device_id(device)
    return template(
        request,
        "device_history_print.html",
        auth=auth,
        device=device,
        history_entries=load_device_history(device_id),
    )


@app.post("/admin/devices/{device_id}/approve")
async def admin_device_approve(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    row = DB.fetchone("SELECT * FROM devices WHERE device_id=?", (device_id,))
    if row is None:
        raise HTTPException(status_code=404)
    remote_port = int(row["remote_port"]) if row["remote_port"] else allocate_port()
    listen_address = row["listen_address"] or SETTINGS.new_tunnel_listen_address
    try:
        result = call_key_manager(
            SETTINGS,
            {
                "op": "upsert",
                "device_id": device_id,
                "public_key": row["public_key"],
                "listen_address": listen_address,
                "remote_port": remote_port,
            },
        )
    except KeyClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    DB.execute(
        """
        UPDATE devices SET state='approved',remote_port=?,listen_address=?,
            ssh_key_installed=1,
            api_token_hash=COALESCE(api_token_hash,pending_api_token_hash),
            pending_api_token_hash=NULL,
            updated_at=? WHERE device_id=?
        """,
        (remote_port, listen_address, utcnow(), device_id),
    )
    audit(request, "device_approved", auth=auth, target_type="device", target_id=device_id, details=result)
    return RedirectResponse(public_path(f"/admin/devices/{device_id}"), status_code=303)


@app.post("/admin/devices/{device_id}/approve-credential-rotation")
async def admin_device_approve_credential_rotation(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    row = DB.fetchone("SELECT * FROM devices WHERE device_id=?", (device_id,))
    if row is None:
        raise HTTPException(status_code=404)
    pending_key = str(row["pending_public_key"] or "")
    pending_fingerprint = str(row["pending_public_key_fingerprint"] or "")
    pending_token_hash = str(row["pending_api_token_hash"] or "")
    if not pending_key or not pending_fingerprint or not pending_token_hash:
        raise HTTPException(status_code=409, detail="No credential replacement is pending")
    remote_port = int(row["remote_port"]) if row["remote_port"] else allocate_port()
    listen_address = row["listen_address"] or SETTINGS.new_tunnel_listen_address
    try:
        result = call_key_manager(
            SETTINGS,
            {
                "op": "upsert",
                "device_id": device_id,
                "public_key": pending_key,
                "listen_address": listen_address,
                "remote_port": remote_port,
            },
        )
    except KeyClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    DB.execute(
        """
        UPDATE devices
        SET public_key=pending_public_key,
            public_key_fingerprint=pending_public_key_fingerprint,
            api_token_hash=pending_api_token_hash,
            pending_public_key=NULL,pending_public_key_fingerprint=NULL,
            pending_api_token_hash=NULL,credential_rotation_requested_at=NULL,
            state='approved',remote_port=?,listen_address=?,ssh_key_installed=1,
            listener_active=0,tunnel_online=0,tunnel_last_seen_at=NULL,
            status_error='Credentials replaced; waiting for the new tunnel',updated_at=?
        WHERE device_id=?
        """,
        (remote_port, listen_address, utcnow(), device_id),
    )
    audit(
        request,
        "device_credential_rotation_approved",
        auth=auth,
        target_type="device",
        target_id=device_id,
        details={
            "previous_fingerprint": str(row["public_key_fingerprint"]),
            "approved_fingerprint": pending_fingerprint,
            "remote_port": remote_port,
            "key_manager": result,
        },
    )
    return RedirectResponse(public_path(f"/admin/devices/{device_id}"), status_code=303)


@app.post("/admin/devices/{device_id}/reject-credential-rotation")
async def admin_device_reject_credential_rotation(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    row = DB.fetchone("SELECT * FROM devices WHERE device_id=?", (device_id,))
    if row is None:
        raise HTTPException(status_code=404)
    pending_fingerprint = str(row["pending_public_key_fingerprint"] or "")
    if not pending_fingerprint:
        raise HTTPException(status_code=409, detail="No credential replacement is pending")
    DB.execute(
        """
        UPDATE devices
        SET pending_public_key=NULL,pending_public_key_fingerprint=NULL,
            pending_api_token_hash=NULL,credential_rotation_requested_at=NULL,updated_at=?
        WHERE device_id=?
        """,
        (utcnow(), device_id),
    )
    audit(
        request,
        "device_credential_rotation_rejected",
        auth=auth,
        target_type="device",
        target_id=device_id,
        details={"rejected_fingerprint": pending_fingerprint},
    )
    return RedirectResponse(public_path(f"/admin/devices/{device_id}"), status_code=303)


@app.post("/admin/devices/{device_id}/reject")
async def admin_device_reject(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    try:
        call_key_manager(SETTINGS, {"op": "revoke", "device_id": device_id})
    except KeyClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    DB.execute(
        """UPDATE devices SET state='rejected',ssh_key_installed=0,
            listener_active=0,tunnel_online=0,pending_public_key=NULL,
            pending_public_key_fingerprint=NULL,pending_api_token_hash=NULL,
            credential_rotation_requested_at=NULL,updated_at=? WHERE device_id=?""",
        (utcnow(), device_id),
    )
    audit(request, "device_rejected", auth=auth, target_type="device", target_id=device_id)
    return RedirectResponse(public_path(f"/admin/devices/{device_id}"), status_code=303)


@app.post("/admin/devices/{device_id}/block")
async def admin_device_block(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    try:
        call_key_manager(SETTINGS, {"op": "revoke", "device_id": device_id})
    except KeyClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    DB.execute(
        """UPDATE devices SET state='blocked',ssh_key_installed=0,
            listener_active=0,tunnel_online=0,pending_public_key=NULL,
            pending_public_key_fingerprint=NULL,pending_api_token_hash=NULL,
            credential_rotation_requested_at=NULL,updated_at=? WHERE device_id=?""",
        (utcnow(), device_id),
    )
    audit(request, "device_blocked", auth=auth, target_type="device", target_id=device_id)
    return RedirectResponse(public_path(f"/admin/devices/{device_id}"), status_code=303)


@app.post("/admin/devices/{device_id}/reset-agent-token")
async def admin_device_reset_agent_token(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    DB.execute(
        """
        UPDATE devices
        SET api_token_hash=NULL,pending_api_token_hash=NULL,
            pending_public_key=NULL,pending_public_key_fingerprint=NULL,
            credential_rotation_requested_at=NULL,state='pending',updated_at=?
        WHERE device_id=?
        """,
        (utcnow(), device_id),
    )
    audit(request, "device_agent_token_reset", auth=auth, target_type="device", target_id=device_id)
    return RedirectResponse(public_path(f"/admin/devices/{device_id}"), status_code=303)


@app.post("/admin/devices/{device_id}/delete")
async def admin_device_delete(request: Request, device_id: str):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    device_id = validate_device_id(device_id)
    row = DB.fetchone("SELECT * FROM devices WHERE device_id=?", (device_id,))
    if row is None:
        raise HTTPException(status_code=404)
    try:
        call_key_manager(SETTINGS, {"op": "revoke", "device_id": device_id})
    except KeyClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    details = {
        "raspberry_serial": str(row["raspberry_serial"] or ""),
        "public_key_fingerprint": str(row["public_key_fingerprint"] or ""),
        "state": str(row["state"]),
    }
    with DB.transaction(immediate=True) as connection:
        deleted = connection.execute(
            "DELETE FROM devices WHERE device_id=?", (device_id,)
        ).rowcount
        if deleted != 1:
            raise HTTPException(status_code=404)
    audit(
        request,
        "device_deleted",
        auth=auth,
        target_type="device",
        target_id=device_id,
        details=details,
    )
    return RedirectResponse(public_path("/admin/devices"), status_code=303)


@app.get("/admin/users")
async def admin_users(request: Request):
    auth = auth_context(request)
    if auth is None:
        return redirect_login(request)
    if auth.user["role"] != "admin":
        raise HTTPException(status_code=403)
    users = [dict(row) for row in DB.fetchall("SELECT * FROM users ORDER BY username")]
    systems = [dict(row) for row in DB.fetchall("SELECT id,name,code,active FROM systems ORDER BY name")]
    assigned: dict[int, list[int]] = {}
    for row in DB.fetchall("SELECT user_id,system_id FROM user_systems WHERE can_open=1"):
        assigned.setdefault(int(row["user_id"]), []).append(int(row["system_id"]))
    modal_users: list[dict[str, Any]] = []
    for user in users:
        tagit_user = is_legacy_tagit_user(user)
        if tagit_user:
            user["role"] = "admin"
        user_id = int(user["id"])
        user["is_self"] = user_id == int(auth.user["id"])
        modal_users.append({
            "id": user_id,
            "username": display_username(user),
            "email": str(user.get("email") or ""),
            "display_name": str(user.get("display_name") or ""),
            "source": "BMI20" if user.get("auth_provider") == "legacy_mqtt" else "BMI30 local",
            "auth_provider": str(user.get("auth_provider") or "local"),
            "distributor": str(user.get("legacy_distributor") or ""),
            "customer": str(user.get("legacy_customer") or ""),
            "role": str(user["role"]),
            "active": bool(int(user["active"])),
            "last_login": human_time(user.get("last_login_at")),
            "tagit": tagit_user,
            "is_self": bool(user["is_self"]),
            "can_delete": user.get("auth_provider") == "local" and not user["is_self"],
            "system_ids": assigned.get(user_id, []),
        })
    return template(
        request,
        "admin_users.html",
        auth=auth,
        users=users,
        systems=systems,
        modal_users=modal_users,
    )


@app.post("/admin/users/create")
async def admin_user_create(request: Request):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    try:
        username = validate_username(str(form.get("username", "")))
        password = validate_password(str(form.get("password", "")))
        email = validate_email(str(form.get("email", "")))
    except SecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    role = "admin" if str(form.get("role", "user")) == "admin" else "user"
    active = 1 if str(form.get("active", "")) == "1" else 0
    display_name = str(form.get("display_name", "")).strip()[:200]
    now = utcnow()
    try:
        with DB.transaction(immediate=True) as connection:
            duplicate_email = connection.execute(
                "SELECT id FROM users WHERE email=? COLLATE NOCASE", (email,)
            ).fetchone()
            if duplicate_email is not None:
                raise HTTPException(status_code=409, detail="Email already exists")
            cursor = connection.execute(
                """
                INSERT INTO users(
                    username,password_hash,auth_provider,role,active,
                    force_password_change,display_name,email,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    username, hash_password(password), "local", role, active, 0,
                    display_name, email, now, now,
                ),
            )
            user_id = int(cursor.lastrowid)
            for key, value in form.multi_items():
                if key == "system_id" and str(value).isdigit():
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO user_systems(user_id,system_id,can_open)
                        SELECT ?,id,1 FROM systems WHERE id=?
                        """,
                        (user_id, int(value)),
                    )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    audit(
        request,
        "user_created",
        auth=auth,
        target_type="user",
        target_id=str(user_id),
        details={"username": username, "email": email, "role": role, "active": bool(active)},
    )
    return RedirectResponse(public_path("/admin/users"), status_code=303)


@app.get("/admin/users/{user_id}")
async def admin_user_edit(request: Request, user_id: int):
    auth = auth_context(request)
    if auth is None:
        return redirect_login(request)
    if auth.user["role"] != "admin":
        raise HTTPException(status_code=403)
    if DB.fetchone("SELECT id FROM users WHERE id=?", (user_id,)) is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(public_path("/admin/users"), status_code=303)


@app.post("/admin/users/{user_id}/access")
async def admin_user_access(request: Request, user_id: int):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    active = 1 if str(form.get("active", "")) == "1" else 0
    if int(auth.user["id"]) == user_id and not active:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    with DB.transaction(immediate=True) as connection:
        user = connection.execute(
            "SELECT id,username,role,active FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if user is None:
            raise HTTPException(status_code=404)
        if user["role"] == "admin" and int(user["active"]) and not active:
            other_admins = connection.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role='admin' AND active=1 AND id<>?",
                (user_id,),
            ).fetchone()
            if other_admins is None or int(other_admins["count"]) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="The last active administrator cannot be disabled",
                )
        connection.execute(
            "UPDATE users SET active=?,updated_at=? WHERE id=?",
            (active, utcnow(), user_id),
        )
        if not active:
            connection.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,))
    audit(
        request,
        "user_access_changed",
        auth=auth,
        target_type="user",
        target_id=str(user_id),
        details={"active": bool(active)},
    )
    return JSONResponse({"ok": True, "active": bool(active)})


@app.post("/admin/users/{user_id}/save")
async def admin_user_save(request: Request, user_id: int):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    role = "admin" if str(form.get("role", "user")) == "admin" else "user"
    active = 1 if str(form.get("active", "")) == "1" else 0
    password = str(form.get("password", ""))
    username = str(form.get("username", "")).strip()
    email = str(form.get("email", "")).strip()
    display_name = str(form.get("display_name", "")).strip()[:200]
    if password:
        try:
            validate_password(password)
        except SecurityError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        if username:
            username = validate_username(username)
        if email:
            email = validate_email(email)
    except SecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with DB.transaction(immediate=True) as connection:
        existing_user = connection.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if existing_user is None:
            raise HTTPException(status_code=404)
        if existing_user["auth_provider"] == "legacy_mqtt":
            if password:
                raise HTTPException(
                    status_code=400,
                    detail="A BMI20 user's password can only be changed in BMI20",
                )
            if is_legacy_tagit_user(dict(existing_user)):
                role = "admin"
            username = str(existing_user["username"])
            email = str(existing_user["email"])
            display_name = str(existing_user["display_name"])
        else:
            username = username or str(existing_user["username"])
            if not email:
                raise HTTPException(status_code=400, detail="Email address is required")
            username_owner = connection.execute(
                "SELECT id FROM users WHERE username=? COLLATE NOCASE AND id<>?",
                (username, user_id),
            ).fetchone()
            email_owner = connection.execute(
                "SELECT id FROM users WHERE email=? COLLATE NOCASE AND id<>?",
                (email, user_id),
            ).fetchone()
            if username_owner is not None:
                raise HTTPException(status_code=409, detail="Username already exists")
            if email_owner is not None:
                raise HTTPException(status_code=409, detail="Email already exists")
        if int(auth.user["id"]) == user_id:
            active = 1
        if (
            existing_user["role"] == "admin"
            and int(existing_user["active"])
            and (role != "admin" or not active)
        ):
            other_admins = connection.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role='admin' AND active=1 AND id<>?",
                (user_id,),
            ).fetchone()
            if other_admins is None or int(other_admins["count"]) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="The last active administrator cannot be disabled or demoted",
                )
        if password:
            connection.execute(
                "UPDATE users SET username=?,email=?,display_name=?,role=?,active=?,password_hash=?,force_password_change=0,updated_at=? WHERE id=?",
                (
                    username, email, display_name, role, active,
                    hash_password(password), utcnow(), user_id,
                ),
            )
            connection.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,))
        else:
            connection.execute(
                "UPDATE users SET username=?,email=?,display_name=?,role=?,active=?,updated_at=? WHERE id=?",
                (username, email, display_name, role, active, utcnow(), user_id),
            )
            if not active:
                connection.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,))
        connection.execute("DELETE FROM user_systems WHERE user_id=?", (user_id,))
        for key, value in form.multi_items():
            if key == "system_id" and str(value).isdigit():
                connection.execute(
                    "INSERT OR IGNORE INTO user_systems(user_id,system_id,can_open) VALUES(?,?,1)",
                    (user_id, int(value)),
                )
    audit(request, "user_updated", auth=auth, target_type="user", target_id=str(user_id))
    return RedirectResponse(public_path("/admin/users"), status_code=303)


@app.post("/admin/users/{user_id}/delete")
async def admin_user_delete(request: Request, user_id: int):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    if int(auth.user["id"]) == user_id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    with DB.transaction(immediate=True) as connection:
        user = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if user is None:
            raise HTTPException(status_code=404)
        if user["auth_provider"] != "local":
            raise HTTPException(
                status_code=400,
                detail="BMI20 users must be removed in the BMI20 user directory",
            )
        if user["role"] == "admin" and int(user["active"]):
            other_admins = connection.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role='admin' AND active=1 AND id<>?",
                (user_id,),
            ).fetchone()
            if other_admins is None or int(other_admins["count"]) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete the last active administrator",
                )
        username = str(user["username"])
        email = str(user["email"])
        connection.execute("DELETE FROM users WHERE id=?", (user_id,))
    audit(
        request,
        "user_deleted",
        auth=auth,
        target_type="user",
        target_id=str(user_id),
        details={"username": username, "email": email},
    )
    return RedirectResponse(public_path("/admin/users"), status_code=303)


@app.get("/admin/systems")
async def admin_systems(request: Request):
    auth = auth_context(request)
    if auth is None:
        return redirect_login(request)
    if auth.user["role"] != "admin":
        raise HTTPException(status_code=403)
    systems = [dict(row) for row in DB.fetchall(
        """
        SELECT s.*, COUNT(d.id) AS device_count
        FROM systems s LEFT JOIN devices d ON d.system_id=s.id
        GROUP BY s.id ORDER BY s.name
        """
    )]
    return template(request, "admin_systems.html", auth=auth, systems=systems)


@app.post("/admin/systems/create")
async def admin_system_create(request: Request):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    name = str(form.get("name", "")).strip()[:200]
    if not name:
        raise HTTPException(status_code=400, detail="System name is required")
    now = utcnow()
    try:
        system_id = DB.execute(
            """
            INSERT INTO systems(
                name,code,comment,legacy_distributor,legacy_customer,
                active,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                name,
                str(form.get("code", ""))[:100],
                "",
                str(form.get("legacy_distributor", "")).strip()[:200],
                str(form.get("legacy_customer", "")).strip()[:200],
                1,
                now,
                now,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail="System name already exists") from exc
    audit(request, "system_created", auth=auth, target_type="system", target_id=str(system_id), details={"name": name})
    return RedirectResponse(public_path("/admin/systems"), status_code=303)


@app.post("/admin/systems/{system_id}/save")
async def admin_system_save(request: Request, system_id: int):
    auth = require_auth(request, admin=True)
    form = await request.form()
    verify_csrf(auth, str(form.get("csrf", "")))
    name = str(form.get("name", "")).strip()[:200]
    if not name:
        raise HTTPException(status_code=400, detail="System name is required")
    DB.execute(
        """
        UPDATE systems
        SET name=?,code=?,comment=?,legacy_distributor=?,legacy_customer=?,
            active=?,updated_at=?
        WHERE id=?
        """,
        (
            name,
            str(form.get("code", ""))[:100],
            str(form.get("comment", ""))[:4000],
            str(form.get("legacy_distributor", "")).strip()[:200],
            str(form.get("legacy_customer", "")).strip()[:200],
            1 if str(form.get("active", "")) == "1" else 0,
            utcnow(),
            system_id,
        ),
    )
    audit(request, "system_updated", auth=auth, target_type="system", target_id=str(system_id))
    return RedirectResponse(public_path("/admin/systems"), status_code=303)


def rewrite_html(content: bytes, device_id: str) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    prefix = public_path(f"/device/{device_id}/")
    for attribute in ("href", "src", "action"):
        text = re.sub(
            rf'({attribute}\s*=\s*["\'])/(?!/)',
            rf'\1{prefix}',
            text,
            flags=re.IGNORECASE,
        )
    if "<head" in text.lower():
        proxy_prefix = prefix.rstrip("/")
        bootstrap = f"""<script data-bmi30-proxy-prefix>(function(){{
var prefix={json.dumps(proxy_prefix)};
function proxyPath(value){{
  if(typeof value!=="string"||value.charAt(0)!=="/"||value.indexOf("//")===0) return value;
  if(value===prefix||value.indexOf(prefix+"/")===0) return value;
  return prefix+value;
}}
window.bmi30HubProxyPath=proxyPath;
if(typeof window.fetch==="function"&&!window.__bmi30ProxyFetchWrapped){{
  var nativeFetch=window.fetch.bind(window);
  window.fetch=function(input,init){{
    if(typeof input==="string") input=proxyPath(input);
    return nativeFetch(input,init);
  }};
  window.__bmi30ProxyFetchWrapped=true;
}}
if(typeof window.XMLHttpRequest==="function"&&!window.__bmi30ProxyXhrWrapped){{
  var nativeOpen=window.XMLHttpRequest.prototype.open;
  window.XMLHttpRequest.prototype.open=function(){{
    var args=Array.prototype.slice.call(arguments);
    if(args.length>1) args[1]=proxyPath(args[1]);
    return nativeOpen.apply(this,args);
  }};
  window.__bmi30ProxyXhrWrapped=true;
}}
}}());</script>"""
        base = "" if "<base " in text.lower() else f'<base href="{prefix}">'
        text = re.sub(
            r"(<head[^>]*>)",
            r"\1" + base + bootstrap,
            text,
            count=1,
            flags=re.IGNORECASE,
        )
    return text.encode("utf-8")


async def proxy_device_request(request: Request, device_id: str, path: str):
    auth = auth_context(request)
    if auth is None:
        return redirect_login(request)
    device_id = validate_device_id(device_id)
    device = device_accessible(auth.user, device_id, open_required=True)
    if device is None:
        raise HTTPException(status_code=404)
    if (
        auth.user["role"] != "admin"
        and int(auth.user.get("legacy_read_only") or 0)
        and request.method.upper() not in {
        "GET",
        "HEAD",
        "OPTIONS",
        }
    ):
        raise HTTPException(status_code=403, detail="Read-only technician")
    if device["state"] != "approved" or not device["remote_port"]:
        raise HTTPException(status_code=503, detail="Device is not approved")
    port = int(device["remote_port"])
    if not SETTINGS.port_min <= port <= SETTINGS.port_max or not MONITOR.is_listening(port):
        raise HTTPException(status_code=503, detail="Device tunnel is offline")
    target = f"http://{SETTINGS.tunnel_target_host}:{port}/" + path
    if request.url.query:
        target += "?" + request.url.query
    excluded = {
        "host",
        "connection",
        "content-length",
        "transfer-encoding",
        "upgrade",
        "proxy-connection",
    }
    headers = {key: value for key, value in request.headers.items() if key.lower() not in excluded}
    headers.pop("authorization", None)
    headers.pop("x-forwarded-for", None)
    headers.pop("x-forwarded-host", None)
    headers.pop("x-forwarded-proto", None)
    cookie_header = headers.get("cookie", "")
    if cookie_header:
        filtered_cookies = []
        for item in cookie_header.split(";"):
            name = item.strip().split("=", 1)[0].strip()
            if name and name != COOKIE_NAME:
                filtered_cookies.append(item.strip())
        if filtered_cookies:
            headers["cookie"] = "; ".join(filtered_cookies)
        else:
            headers.pop("cookie", None)
    headers["x-bmi30-device-id"] = device_id
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_PROXY_BODY:
                raise HTTPException(status_code=413, detail="Proxy request body too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from None
    body = await request.body()
    if len(body) > MAX_PROXY_BODY:
        raise HTTPException(status_code=413, detail="Proxy request body too large")
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=15.0,
            trust_env=False,
        ) as client:
            async with client.stream(
                request.method,
                target,
                headers=headers,
                content=body,
            ) as upstream:
                chunks: list[bytes] = []
                total = 0
                async for chunk in upstream.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_PROXY_RESPONSE:
                        raise HTTPException(
                            status_code=502,
                            detail="Device response is too large for proxy v0.1",
                        )
                    chunks.append(chunk)
                upstream_content = b"".join(chunks)
                upstream_status = upstream.status_code
                upstream_headers = upstream.headers
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Device proxy error: {exc}") from exc
    response_headers: dict[str, str] = {}
    for key, value in upstream_headers.items():
        lower = key.lower()
        if lower in excluded or lower in {"content-security-policy", "set-cookie"}:
            continue
        if lower == "location" and value.startswith("/"):
            value = public_path(f"/device/{device_id}") + value
        response_headers[key] = value
    device_cookies: list[str] = []
    for value in upstream_headers.get_list("set-cookie"):
        cookie_name = value.split("=", 1)[0].strip()
        if cookie_name == COOKIE_NAME:
            continue
        # Upstream devices run on an isolated tunnel network.  Never allow them to set a cookie
        # Domain for the central portal host; keep cookies host-only and path-scoped.
        value = re.sub(r"(?i);\s*Domain=[^;]*", "", value)
        if re.search(r"(?i)(?:^|;)\s*Path=", value):
            value = re.sub(
                r"(?i)(;\s*Path=)/[^;]*",
                rf"\1{public_path(f'/device/{device_id}/')}",
                value,
                count=1,
            )
        else:
            value += f"; Path={public_path(f'/device/{device_id}/')}"
        device_cookies.append(value)
    content = upstream_content
    content_type = upstream_headers.get("content-type", "")
    if "text/html" in content_type.lower():
        content = rewrite_html(content, device_id)
        response_headers.pop("content-length", None)
    proxied_response = Response(
        content=content,
        status_code=upstream_status,
        headers=response_headers,
        media_type=None,
    )
    for value in device_cookies:
        proxied_response.headers.append("set-cookie", value)
    return proxied_response


@app.api_route(
    "/device/{device_id}/",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_device_root(request: Request, device_id: str):
    return await proxy_device_request(request, device_id, "")


@app.api_route(
    "/device/{device_id}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_device_path(request: Request, device_id: str, path: str):
    return await proxy_device_request(request, device_id, path)
