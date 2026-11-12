"""Pin native portal startup to the selected, hash-verified application release."""
import ast

HELPER='''
_BMI30_VERSIONED_PORTAL_LAUNCHER = True
def ensure_versioned_portal_launcher():
    # Core/STM32 availability must never choose which web interface is started.
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    unit = pathlib.Path("/etc/systemd/system/bmi30-hotspot-info.service")
    if not unit.is_file():
        return False
    release = detect_bmi30_firmware_release()
    expected = str(release.get("portal_sha256") or "").lower()
    relative = str(release.get("portal_path") or "")
    if not relative or not re.fullmatch(r"[0-9a-f]{64}", expected):
        return False
    portal = pathlib.Path(_resolve_project_path(relative))
    workspace = pathlib.Path(FIRMWARE_WORKSPACE).resolve()
    try:
        portal.resolve().relative_to(workspace)
    except ValueError:
        return False
    if not portal.is_file() or hashlib.sha256(portal.read_bytes()).hexdigest() != expected:
        return False
    path = unit.with_name("bmi30-hotspot-info.service.d") / "06-bmi30-versioned-portal.conf"
    quoted = json.dumps(str(portal), ensure_ascii=False)
    content = "[Service]\\nExecStart=\\nExecStart=/usr/bin/python3 " + quoted + "\\n"
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o644)
    temporary.replace(path)
    subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True, timeout=8)
    return True

'''

def patch(source):
    if '_BMI30_VERSIONED_PORTAL_LAUNCHER' in source:return source
    anchor='class HotspotInfoHandler(BaseHTTPRequestHandler):'
    assert source.count(anchor)==1;source=source.replace(anchor,HELPER+anchor,1)
    anchor='def main() -> None:\n'
    assert source.count(anchor)==1
    source=source.replace(anchor,anchor+'''    try:
        ensure_versioned_portal_launcher()
    except Exception:
        # Keep serving a recovery page if service files are temporarily readonly.
        pass
''',1)
    ast.parse(source);return source
