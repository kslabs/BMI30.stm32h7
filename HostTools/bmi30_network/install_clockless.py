"""Validate or apply the clock-independent network update to one named CM5."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

import install_rpi_identity as base
import patch_rpi_identity as identity
import patch_service_clock_portal as clock
import patch_service_transport_portal as transports

HERE = Path(__file__).resolve().parent
DOCS = base.DOCS
RUNTIME = Path('/opt/bmi30-network')
CONFIG = Path('/etc/bmi30-network/config.json')
MODULES = ('service.py', 'group.py', 'protocol.py', 'freshness.py', 'network.py',
           'policy.py', 'access_policy.py', 'realtime.py', 'owner_events.py',
           'bluetooth_transport.py', 'bluez_service.py', 'transport_policy.py')
NEW_MODULES = {'freshness.py','bluetooth_transport.py','bluez_service.py','transport_policy.py'}
VERSION = '2026-10-23-service-transports-r2'


def validate_identity(expected):
    if not re.fullmatch(r'BMI30-[0-9A-F]{9}', expected) or base.hardware_identity() != expected:
        raise RuntimeError('Wrong or unavailable physical CM5 identity; nothing changed')


def portal_candidate(source):
    if clock.PATCH_MARKER in source:
        if identity.PATCH_MARKER not in source or source.count(clock.PATCH_MARKER)!=1:
            raise RuntimeError('Incomplete clock-independent portal patch')
        return transports.patch(source)
    if identity.PATCH_MARKER not in source:
        source = identity.patch(source)
    return transports.patch(clock.patch(source))


def build_plan(docs=DOCS, installed=base.INSTALLED, runtime=RUNTIME, config=CONFIG, payload=HERE):
    env = base.active_environment(docs)
    selected = env.get('BMI30_PORTAL_PATH')
    if not selected:
        raise RuntimeError('Active portal source is not declared')
    # All active/mirror copies are prepared before the first write.
    paths = [installed.resolve(strict=True), base.source_path(docs, selected)]
    mirror = docs / 'hotspot_info_server.py'
    if mirror.is_file():
        paths.append(mirror.resolve(strict=True))
    plan = []

    def add(path, candidate, template=None):
        path = path.resolve()
        original = path.read_bytes() if path.exists() else None
        compile(candidate, str(path), 'exec')
        plan.append({'path': path, 'original': original, 'candidate': candidate,
                     'stat': path.stat() if original is not None else template})

    for path in dict.fromkeys(paths):
        add(path, portal_candidate(path.read_text(encoding='utf-8')).encode('utf-8'))
    runtime = runtime.resolve(strict=True)
    template = (runtime / 'protocol.py').stat()
    for name in MODULES:
        path = runtime / name
        if path.is_symlink():
            raise RuntimeError('Unexpected symlink in network runtime: ' + str(path))
        if name not in NEW_MODULES and not path.is_file():
            raise RuntimeError('Network runtime is incomplete: ' + str(path))
        add(path, (payload / name).read_bytes(), template)
    # The running owner remains compatible with the local event receiver. Its
    # source helpers acquire monotonic stamps at the next normal core start.
    helpers = [docs / 'host/usb_vendor/owner_events.py']
    for key in ('BMI30_ENGINE_SOURCE', 'BMI30_CORE_PATH'):
        if env.get(key):
            source = base.source_path(docs, env[key])
            helpers.append(source.parent / 'host/usb_vendor/owner_events.py')
            helpers.append(source.parent / 'owner_events.py')
    for helper in dict.fromkeys(p.resolve() for p in helpers if p.is_file()):
        if helper not in {p['path'] for p in plan}:
            add(helper, (payload / 'owner_events.py').read_bytes())
    protected_paths = [docs / 'host/bmi30_split_active_version.env', docs / 'host/bmi30_config.json',
                       docs / 'host/bmi30_sel.json', config]
    for key in ('BMI30_ENGINE_SOURCE', 'BMI30_CORE_PATH', 'BMI30_PROJECT_CONFIG_PATH',
                'BMI30_CONFIG_JSON', 'BMI30_MODE_STATE_JSON'):
        if env.get(key):
            protected_paths.append(base.source_path(docs, env[key]))
    if not config.is_file():
        raise RuntimeError('Existing network configuration required')
    settings = json.loads(config.read_text(encoding='utf-8'))
    key = settings.get('fleet_key', '')
    if not isinstance(key, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', key):
        raise RuntimeError('Invalid existing fleet key; configuration was not changed')
    protected = {p.resolve(): base.digest(p.read_bytes()) for p in protected_paths if p.is_file()}
    return plan, protected


def restart_services():
    for name in ('bmi30-network', 'bmi30-hotspot-info'):
        subprocess.run(['systemctl', 'restart', name], check=True, capture_output=True)


def verify_runtime(expected):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open('http://127.0.0.1/api/status', timeout=2) as response:
                portal = json.load(response)
            with opener.open('http://127.0.0.1:8791/status', timeout=2) as response:
                network = json.load(response)
            if (portal.get('hostname') == expected and portal.get('rpi_identity_version') == base.VERSION
                    and portal.get('service_clock_mode') == 'monotonic-v2'
                    and portal.get('service_transport_version') == '1'
                    and network.get('version') == '0.2.4' and network.get('clock_mode') == 'monotonic-v2'
                    and ((network.get('bluetooth') or {}).get('state')=='listening'
                         or (network.get('bluetooth') or {}).get('enabled') is False)):
                return
        except (OSError, ValueError):
            pass
        time.sleep(.3)
    raise RuntimeError('Portal/network/Classic Bluetooth profile did not confirm readiness')


def apply_plan(plan, protected, expected, backup):
    validate_identity(expected)
    pid = base.core_pid()
    for item in plan:
        path = item['path']
        current = path.read_bytes() if path.exists() else None
        if current != item['original']:
            raise RuntimeError('Source changed after validation: ' + str(path))
    base.check_unchanged(protected)
    backup.mkdir(parents=True, exist_ok=False, mode=0o700 if os.name == 'posix' else 0o777)
    report = {'version': VERSION, 'hardware_identity': expected, 'backup': str(backup), 'core_pid': pid,
              'files': [{'path': str(item['path']),
                         'before': base.digest(item['original']) if item['original'] is not None else None,
                         'after': base.digest(item['candidate'])} for item in plan]}
    for index, item in enumerate(plan):
        if item['original'] is not None:
            (backup / str(index)).write_bytes(item['original'])
    (backup / 'manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    changed = []
    try:
        for item in plan:
            base.atomic_write(item['path'], item['candidate'], item['stat'])
            changed.append(item)
        restart_services()
        verify_runtime(expected)
        if base.core_pid() != pid:
            raise RuntimeError('Acquisition owner PID changed')
        base.check_unchanged(protected)
        for item in plan:
            if item['path'].read_bytes() != item['candidate']:
                raise RuntimeError('Installed file does not match validated candidate')
    except BaseException as error:
        failures = []
        for item in reversed(changed):
            try:
                if item['original'] is None:
                    item['path'].unlink(missing_ok=True)
                else:
                    base.atomic_write(item['path'], item['original'], item['stat'])
            except Exception:
                failures.append(str(item['path']))
        try:
            restart_services()
        except Exception:
            failures.append('network/portal restart')
        if failures:
            raise RuntimeError('Rollback needs attention: ' + ', '.join(failures) + '; backup: ' + str(backup)) from error
        raise RuntimeError('Update failed; all original files restored; backup: ' + str(backup)) from error
    report['applied'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-id', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    expected = args.expected_id.strip().upper()
    validate_identity(expected)
    # Never leave a working V1 service without its required crypto dependency.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    AESGCM.generate_key(bit_length=256)
    # D-Bus/GLib are standard Raspberry Pi OS packages, never fetched by the
    # installer. Missing support is diagnosed before any runtime file changes.
    import dbus
    from gi.repository import GLib
    if args.apply:
        if os.geteuid() != 0:
            parser.error('--apply requires root')
        import fcntl
        with open('/run/lock/bmi30-clockless.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            plan, protected = build_plan()
            backup = Path('/var/backups/bmi30-clockless') / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
            print(json.dumps(apply_plan(plan, protected, expected, backup), indent=2))
    else:
        plan, _ = build_plan()
        print(json.dumps({'applied': False, 'hardware_identity': expected,
                          'files': [str(item['path']) for item in plan]}, indent=2))


if __name__ == '__main__':
    main()
