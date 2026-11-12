#!/usr/bin/env python3
"""BMI30 HTTPS files contract. No shell parsing, token arguments or redirects."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import gzip
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import ssl
import tarfile
import tempfile
import time
import urllib.parse
import uuid

BASE_URL = "https://www.teiots.net/bmi30"
CONFIG = Path("/etc/bmi30/firmware_sources.json")
AGENT_CONFIG = Path("/etc/bmi30-agent/config.json")
MARKER_KEYS = {"FIRMWARE_VERSION", "FIRMWARE_BUNDLE_ID", "ARCHIVE_NAME", "ARCHIVE_SIZE", "ARCHIVE_SHA256"}
MAX_ARCHIVE = 2 * 1024**3
MAX_EXPANDED = 8 * 1024**3
MAX_MARKER = 65536
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")
NAME_RE = re.compile(r"bmi30_backup_[0-9]{8}_[0-9]{6}\.tar\.gz\Z")
SHA_RE = re.compile(r"[a-fA-F0-9]{64}\Z")


class FirmwareError(RuntimeError):
    pass


class HTTPError(FirmwareError):
    def __init__(self, status, code="", retry_after=""):
        self.status, self.code, self.retry_after = status, code, retry_after
        super().__init__(f"Server HTTP {status}: {code or 'request failed'}")


def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def atomic_write(path: Path, data: bytes, mode=0o644, *, uid=None, gid=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            if uid is not None or gid is not None:
                os.fchown(f.fileno(), uid if uid is not None else -1,
                          gid if gid is not None else -1)
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data, mode=0o644):
    atomic_write(Path(path), (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(), mode)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def parse_env(data: bytes | str, allowed=None):
    """Accept data-only shell quoting used by existing bundles; never execute it."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="strict")
    if len(data.encode()) > MAX_MARKER:
        raise FirmwareError("Environment/marker file is too large")
    result = {}
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise FirmwareError("Invalid environment assignment")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in result:
            raise FirmwareError("Invalid or duplicate environment field")
        if allowed is not None and key not in allowed:
            raise FirmwareError("Unknown marker field: " + key)
        # Variables/command substitutions have no place in release metadata.
        if "$" in value or "`" in value or "\x00" in value:
            raise FirmwareError("Executable environment syntax is forbidden")
        lexer = shlex.shlex(value, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            words = list(lexer)
        except ValueError as exc:
            raise FirmwareError("Invalid environment quoting") from exc
        if len(words) > 1 or (words and words[0] in {";", "&", "|", "<", ">", "(", ")"}):
            raise FirmwareError("Environment value must be one literal")
        result[key] = words[0] if words else ""
    return result


def env_bytes(values):
    # The server rejects shell-style backslash-escaped spaces in unquoted
    # values. Use one explicit quoted literal, including labels with apostrophes.
    def literal(value):
        value = str(value)
        if "$" in value or "`" in value or any(ord(c) < 32 for c in value):
            raise FirmwareError("Invalid environment value")
        return value if re.fullmatch(r"[A-Za-z0-9_./:+@%-]+", value) else json.dumps(value, ensure_ascii=False)
    return ("".join(f"{key}={literal(value)}\n" for key, value in values.items())).encode()


def read_env(path):
    return parse_env(Path(path).read_bytes())


def version(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}", str(value)):
        raise FirmwareError("Unknown firmware version; installation is blocked")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d-%H%M%S")
    except ValueError as exc:
        raise FirmwareError("Invalid firmware version date") from exc


def marker(data):
    result = parse_env(data, MARKER_KEYS)
    if set(result) != MARKER_KEYS:
        raise FirmwareError("Server marker must contain exactly five fields")
    version(result["FIRMWARE_VERSION"])
    if not ID_RE.fullmatch(result["FIRMWARE_BUNDLE_ID"]) or ".." in result["FIRMWARE_BUNDLE_ID"]:
        raise FirmwareError("Invalid bundle ID")
    if not NAME_RE.fullmatch(result["ARCHIVE_NAME"]):
        raise FirmwareError("Invalid archive basename")
    if not re.fullmatch(r"[0-9]+", result["ARCHIVE_SIZE"]) or not 0 < int(result["ARCHIVE_SIZE"]) <= MAX_ARCHIVE:
        raise FirmwareError("Invalid archive size")
    if not SHA_RE.fullmatch(result["ARCHIVE_SHA256"]):
        raise FirmwareError("Invalid archive SHA-256")
    result["ARCHIVE_SHA256"] = result["ARCHIVE_SHA256"].lower()
    return result


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_file(path, info):
    if Path(path).stat().st_size != int(info["ARCHIVE_SIZE"]) or file_sha(path) != info["ARCHIVE_SHA256"]:
        raise FirmwareError("Archive size/SHA-256 mismatch")


def settings():
    # Keep the query for older local callers; firmware has one source now.
    return {"backend": "server"}


class Client:
    def __init__(self, *, base_url=None, token_file=None, timeout=30):
        cfg = read_json(AGENT_CONFIG, {}) if base_url is None or token_file is None else {}
        self.base_url = base_url or cfg.get("server_url", BASE_URL)
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise FirmwareError("Firmware server requires an HTTPS base URL")
        self.host, self.port, self.base_path = parsed.hostname, parsed.port or 443, parsed.path.rstrip("/")
        self.token_file = Path(token_file or cfg.get("device_api_token_path", "/var/lib/bmi30-agent/device_api_token"))
        self.timeout = timeout

    def token(self):
        try:
            value = self.token_file.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise FirmwareError("Cannot read the existing BMI30 Agent token; run through sudo") from exc
        if not 40 <= len(value) <= 512 or any(c.isspace() or ord(c) < 33 for c in value):
            raise FirmwareError("Existing Agent token has an invalid format")
        return value

    def connection(self):
        return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=ssl.create_default_context())

    @contextlib.contextmanager
    def request(self, method, path, headers=None, body=None):
        if not path.startswith("/") or "?" in path:
            raise FirmwareError("Invalid firmware API path")
        connection = self.connection()
        try:
            connection.request(method, self.base_path + path, body=body, headers={
                "Authorization": "Bearer " + self.token(), "User-Agent": "BMI30-Firmware/1",
                **(headers or {}),
            })
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raw = response.read(MAX_MARKER)
                try:
                    code = json.loads(raw).get("code", "")
                    code = code if re.fullmatch(r"[a-z0-9_]{1,100}", code) else "request_failed"
                except (ValueError, AttributeError):
                    code = "request_failed"
                raise HTTPError(response.status, code, response.getheader("Retry-After", ""))
            yield response
        finally:
            connection.close()

    def latest(self):
        with self.request("GET", "/firmware/bmi30_latest.env", {"Cache-Control": "no-cache"}) as response:
            raw = response.read(MAX_MARKER + 1)
        return marker(raw), raw

    def status(self):
        with self.request("GET", "/api/v1/firmware/status") as response:
            return json.loads(response.read(MAX_MARKER))

    def download(self, info, directory, *, verify_remote=False):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / info["ARCHIVE_NAME"]
        if target.exists() and not verify_remote:
            verify_file(target, info)
            return target
        partial = target.with_name(target.name + ".part")
        metadata = target.with_name(target.name + ".part.json")
        expected = int(info["ARCHIVE_SIZE"])
        if verify_remote or read_json(metadata) != info or not partial.exists() or partial.stat().st_size > expected:
            partial.unlink(missing_ok=True)
            write_json(metadata, info)
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == expected:
            try:
                verify_file(partial, info)
            except FirmwareError:
                partial.unlink()
                offset = 0
        if offset < expected:
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with self.request("GET", "/firmware/" + info["ARCHIVE_NAME"], headers) as response:
                if response.status == 206:
                    if response.getheader("Content-Range") != f"bytes {offset}-{expected - 1}/{expected}":
                        raise FirmwareError("Invalid Content-Range; partial archive was not appended")
                elif response.status == 200:
                    offset = 0
                else:
                    raise FirmwareError("Unexpected download status")
                length = response.getheader("Content-Length")
                if length is None or not length.isdigit() or int(length) != expected - offset:
                    raise FirmwareError("Invalid download Content-Length")
                if shutil.disk_usage(directory).free < expected - offset + 16 * 1024**2:
                    raise FirmwareError("Insufficient disk space for download")
                total = offset
                started = time.monotonic()
                with partial.open("ab" if offset else "wb") as output:
                    while block := response.read(min(1024 * 1024, expected - total + 1)):
                        total += len(block)
                        if total > expected or time.monotonic() - started > 1800:
                            raise FirmwareError("Download exceeded size/time limit")
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
        verify_file(partial, info)
        os.replace(partial, target)
        metadata.unlink(missing_ok=True)
        atomic_write(target.with_name(target.name + ".marker.env"), env_bytes(info))
        return target

    def publish(self, info, raw_marker, archive):
        verify_file(archive, info)
        state = self.status()
        publisher = state.get("publisher_device_id")
        if not publisher:
            raise FirmwareError("Server did not confirm publisher authorization")
        boundary = "BMI30" + uuid.uuid4().hex
        first = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"marker\"; filename=\"bmi30_latest.env\"\r\n"
                 "Content-Type: text/plain\r\n\r\n").encode() + raw_marker
        second = (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"archive\"; filename=\"{info['ARCHIVE_NAME']}\"\r\n"
                  "Content-Type: application/gzip\r\n\r\n").encode()
        end = f"\r\n--{boundary}--\r\n".encode()
        def chunks():
            started = time.monotonic()
            yield first
            yield second
            with Path(archive).open("rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    if time.monotonic() - started > 1800:
                        raise FirmwareError("Publish transfer exceeded time limit")
                    yield block
            yield end
        headers = {"Content-Type": "multipart/form-data; boundary=" + boundary,
                   "Content-Length": str(len(first) + len(second) + int(info["ARCHIVE_SIZE"]) + len(end))}
        with self.request("POST", "/api/v1/firmware/publish", headers, chunks()) as response:
            if response.status not in (200, 201):
                raise FirmwareError("Unexpected publish response")
            result = json.loads(response.read(MAX_MARKER))
        if not result.get("ok") or result.get("publisher") != publisher:
            raise FirmwareError("Publisher response identity mismatch")
        response_fields = {"FIRMWARE_BUNDLE_ID": "bundle_id"}
        for key, expected in info.items():
            if str(result.get(response_fields.get(key, key.lower()))) != str(expected):
                raise FirmwareError("Publisher response mismatch: " + key)
        with self.request("HEAD", "/firmware/" + info["ARCHIVE_NAME"]) as response:
            if response.getheader("Content-Length") != info["ARCHIVE_SIZE"]:
                raise FirmwareError("Published archive HEAD size mismatch")
        # Verify remote bytes, never satisfy publication verification from cache.
        with tempfile.TemporaryDirectory(prefix=".verify-publish-", dir=Path(archive).parent) as directory:
            self.download(info, directory, verify_remote=True)
        latest, _ = self.latest()
        if result.get("latest_updated") and latest != info and version(latest["FIRMWARE_VERSION"]) <= version(info["FIRMWARE_VERSION"]):
            raise FirmwareError("Publication completed but latest changed; check server state")
        return {**result, "verified_download": True, "latest": latest}


def safe_rel(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or any(ord(c) < 32 for c in name):
        raise FirmwareError("Unsafe archive path")
    return p


def verify_bundle(directory, bundle_id):
    directory = Path(directory)
    manifest = read_env(directory / "manifest.env")
    if manifest.get("BMI30_BUNDLE_ID") != bundle_id or manifest.get("BMI30_BUNDLE_FORMAT") != "1":
        raise FirmwareError("Bundle manifest identity mismatch")
    for key, value in manifest.items():
        if not key.startswith("BMI30_BUNDLE_"):
            raise FirmwareError("Unexpected bundle manifest key")
        if key.endswith("_REL"):
            safe_rel(value)
    sums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if not match:
            raise FirmwareError("Invalid bundle checksum record")
        digest, name = match.groups()
        safe_rel(name)
        if name in sums:
            raise FirmwareError("Duplicate bundle checksum")
        sums[name] = digest.lower()
    actual = {}
    for file in directory.rglob("*"):
        if file.is_symlink() or (not file.is_file() and not file.is_dir()):
            raise FirmwareError("Links/special files are not supported in a runtime bundle")
        if file.is_file() and file != directory / "SHA256SUMS":
            actual[file.relative_to(directory).as_posix()] = file_sha(file)
    if actual != sums or "manifest.env" not in sums:
        raise FirmwareError("Incomplete bundle checksum coverage or checksum mismatch")
    for key in ("CORE", "ENGINE", "GUI", "PORTAL", "PROJECT_CONFIG", "RELEASE"):
        name = manifest.get("BMI30_BUNDLE_" + key + "_REL", "")
        if name not in sums:
            raise FirmwareError("Bundle is missing " + key)
    read_env(directory / manifest["BMI30_BUNDLE_RELEASE_REL"])
    if manifest.get("BMI30_BUNDLE_PORTABLE_SYSTEM") == "1":
        from bmi30_portable_assets import validate
        if not (directory / "metadata/portable-assets.json").is_file():
            raise FirmwareError("Portable asset manifest is missing")
        try:
            validate(directory)
        except (ValueError, OSError, SyntaxError) as exc:
            raise FirmwareError("Invalid portable runtime assets: " + str(exc)) from exc
    return manifest


def extract_verified(archive, info, directory):
    """Stream into a fresh directory; no tar.extract or script execution."""
    verify_file(archive, info)
    directory = Path(directory)
    if any(directory.iterdir()):
        raise FirmwareError("Extraction directory must be empty")
    seen, roots, total, count = set(), set(), 0, 0
    with gzip.open(archive, "rb") as compressed, tarfile.open(fileobj=compressed, mode="r|") as stream:
        for member in stream:
            count += 1
            if count > 200000:
                raise FirmwareError("Too many archive members")
            rel = safe_rel(member.name.rstrip("/"))
            if str(rel) in seen:
                raise FirmwareError("Duplicate archive path")
            seen.add(str(rel)); roots.add(rel.parts[0])
            if len(roots) != 1 or not (member.isdir() or member.isfile()):
                raise FirmwareError("Archive must have one project root and regular files only")
            if member.mode & 0o7000:
                raise FirmwareError("Privileged archive modes are forbidden")
            if member.size < 0:
                raise FirmwareError("Invalid archive member size")
            total += member.size
            if total > MAX_EXPANDED:
                raise FirmwareError("Archive expansion limit exceeded")
            target = directory.joinpath(*rel.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if shutil.disk_usage(directory).free < member.size + 64 * 1024**2:
                raise FirmwareError("Insufficient extraction disk space")
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
        # Reading the remaining compressed stream validates gzip footer/truncation.
        while compressed.read(1024 * 1024):
            if compressed.tell() > MAX_EXPANDED + 400 * 1024**2:
                raise FirmwareError("Archive padding/expansion limit exceeded")
    if len(roots) != 1:
        raise FirmwareError("Empty archive")
    project = directory / roots.pop()
    active = read_env(project / "host/bmi30_split_active_version.env")
    bundle_id = info["FIRMWARE_BUNDLE_ID"]
    if active.get("BMI30_SPLIT_BUNDLE_ID") != bundle_id:
        raise FirmwareError("Archive active bundle differs from marker")
    bundle = project / "host/bmi30_split_bundles" / bundle_id
    manifest = verify_bundle(bundle, bundle_id)
    release = read_env(project / "host/bmi30_firmware_release.env")
    if any(not key.startswith("BMI30_FIRMWARE_") for key in release):
        raise FirmwareError("Unexpected release manifest field")
    if release.get("BMI30_FIRMWARE_VERSION") != info["FIRMWARE_VERSION"] or release.get("BMI30_FIRMWARE_BUNDLE_ID") != bundle_id:
        raise FirmwareError("Release manifest differs from marker")
    for component in ("CORE", "ENGINE", "GUI", "PORTAL"):
        expected = release.get("BMI30_FIRMWARE_" + component + "_SHA256")
        if expected != file_sha(bundle / manifest["BMI30_BUNDLE_" + component + "_REL"]):
            raise FirmwareError("Release component checksum mismatch: " + component)
    return project
