"""Guarded, portal-only update on the named physical Raspberry Pi.

Without --apply this validates and reports a plan. All source files are patched
before the first write; original files are backed up and restored on failure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import time
import urllib.request

from patch_rpi_identity import HARDWARE, PATCH_MARKER, patch

VERSION = '2026-10-23'
DOCS = Path('/home/techaid/Documents')
INSTALLED = Path('/usr/local/bin/bmi30-hotspot-info-server.py')


def hardware_identity() -> str:
    # Exactly the same hardware reader which is embedded in the portal.
    namespace = {'re': re}
    exec(compile(HARDWARE, 'hardware-identity', 'exec'), namespace)
    return namespace['_bmi30_hardware_hostname']()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def active_environment(docs: Path) -> dict:
    path = docs / 'host/bmi30_split_active_version.env'
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        parts = shlex.split(value, comments=True)
        if len(parts) == 1:
            result[key.strip()] = parts[0]
    return result


def source_path(docs: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else docs / path).resolve(strict=True)


def build_plan(docs: Path = DOCS, installed: Path = INSTALLED) -> tuple[list, dict]:
    env = active_environment(docs)
    selected = env.get('BMI30_PORTAL_PATH')
    if not selected:
        raise RuntimeError('Active portal source is not declared')
    paths = [installed.resolve(strict=True), source_path(docs, selected)]
    mirror = docs / 'hotspot_info_server.py'
    if mirror.is_file():
        paths.append(mirror.resolve(strict=True))
    plan = []
    for path in dict.fromkeys(paths):
        original = path.read_bytes()
        source = original.decode('utf-8')
        if PATCH_MARKER in source:
            raise RuntimeError('Identity patch already present: ' + str(path))
        candidate = patch(source).encode('utf-8')
        compile(candidate, str(path), 'exec')
        plan.append({'path': path, 'original': original, 'candidate': candidate, 'stat': path.stat()})
    protected_paths = [docs / 'host/bmi30_split_active_version.env',
                       docs / 'host/bmi30_config.json', docs / 'host/bmi30_sel.json']
    for key in ('BMI30_ENGINE_SOURCE', 'BMI30_CORE_PATH', 'BMI30_PROJECT_CONFIG_PATH',
                'BMI30_CONFIG_JSON', 'BMI30_MODE_STATE_JSON'):
        if env.get(key):
            protected_paths.append(source_path(docs, env[key]))
    protected = {path.resolve(): digest(path.read_bytes()) for path in protected_paths if path.is_file()}
    return plan, protected


def core_pid() -> int:
    result = subprocess.run(['systemctl', 'show', 'bmi30-core', '-p', 'MainPID', '--value'],
                            capture_output=True, text=True, check=True)
    value = int(result.stdout.strip())
    if value <= 0:
        raise RuntimeError('Acquisition owner is not running')
    return value


def restart_portal() -> None:
    subprocess.run(['systemctl', 'restart', 'bmi30-hotspot-info'], check=True, capture_output=True)


def verify_portal(expected: str) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(20):
        try:
            with opener.open('http://127.0.0.1/api/status', timeout=2) as response:
                state = json.load(response)
            if state.get('hostname') == expected and state.get('rpi_identity_version') == VERSION:
                return
        except (OSError, ValueError):
            pass
        time.sleep(.3)
    raise RuntimeError('Portal did not confirm its hardware identity and patch version')


def atomic_write(path: Path, data: bytes, original_stat) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.bmi30-rpi-id-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(stat.S_IMODE(original_stat.st_mode))
        if hasattr(os, 'chown'):
            os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_unchanged(protected: dict) -> None:
    for path, expected in protected.items():
        if digest(path.read_bytes()) != expected:
            raise RuntimeError('Protected acquisition/configuration file changed: ' + str(path))


def apply_plan(plan: list, protected: dict, expected: str, backup: Path) -> dict:
    pid = core_pid()
    for item in plan:
        if item['path'].read_bytes() != item['original']:
            raise RuntimeError('Portal changed since validation: ' + str(item['path']))
    check_unchanged(protected)
    backup.mkdir(parents=True, exist_ok=False, mode=0o700 if os.name == 'posix' else 0o777)
    report = {'version': VERSION, 'hardware_identity': expected, 'backup': str(backup),
              'core_pid': pid, 'files': [{'path': str(item['path']),
              'before': digest(item['original']), 'after': digest(item['candidate'])} for item in plan]}
    for index, item in enumerate(plan):
        (backup / str(index)).write_bytes(item['original'])
    (backup / 'manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    changed = []
    try:
        for item in plan:
            atomic_write(item['path'], item['candidate'], item['stat'])
            changed.append(item)
        restart_portal()
        verify_portal(expected)
        if core_pid() != pid:
            raise RuntimeError('Acquisition owner PID changed')
        check_unchanged(protected)
        for item in plan:
            if item['path'].read_bytes() != item['candidate']:
                raise RuntimeError('Installed portal did not match candidate')
    except Exception as error:
        rollback_errors = []
        for item in reversed(changed):
            try:
                atomic_write(item['path'], item['original'], item['stat'])
            except Exception:
                rollback_errors.append(str(item['path']))
        try:
            restart_portal()
        except Exception:
            rollback_errors.append('portal restart')
        if rollback_errors:
            raise RuntimeError('Rollback needs attention: ' + ', '.join(rollback_errors) + '; backup: ' + str(backup)) from error
        raise RuntimeError('Update failed; original portal files restored; backup: ' + str(backup)) from error
    report['applied'] = True
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-id', required=True, help='Physical device name, e.g. BMI30-27448D72B')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    expected = args.expected_id.strip().upper()
    if not re.fullmatch(r'BMI30-[0-9A-F]{9}', expected) or hardware_identity() != expected:
        raise RuntimeError('Wrong or unavailable Raspberry Pi hardware identity; nothing changed')
    if args.apply:
        if os.geteuid() != 0:
            parser.error('--apply requires root')
        import fcntl
        with open('/run/lock/bmi30-rpi-identity.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            plan, protected = build_plan()
            backup = Path('/var/backups/bmi30-rpi-identity') / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
            print(json.dumps(apply_plan(plan, protected, expected, backup), indent=2))
    else:
        plan, _ = build_plan()
        print(json.dumps({'applied': False, 'hardware_identity': expected,
                          'files': [str(item['path']) for item in plan]}, indent=2))


if __name__ == '__main__':
    main()
