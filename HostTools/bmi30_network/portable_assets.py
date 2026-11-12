"""Allowlisted portable system code; device configuration/credentials stay local."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

NETWORK_FILES = ('network.py', 'service.py', 'policy.py', 'group.py', 'protocol.py',
                 'owner_events.py', 'realtime.py', 'communication_recovery.py',
                 'scope_live.py', 'scope_codec.py', 'scope_rtc.py', 'scope_rtc_proxy.py',
                 'status.html', 'portal_ui_version.js', 'portal_help.js', 'group_ui.js',
                 'portal_relay.js', 'live_ui.js', 'release.json')
UNITS = ('bmi30-network.service', 'bmi30-channel-policy.service',
         'bmi30-communication-recovery.service', 'bmi30-scope-rtc.service',
         'bmi30-refresh-network-identity.service', 'bmi30-core.service')
ALLOWLIST = {**{f'opt/bmi30-network/{name}': 0o644 for name in NETWORK_FILES},
             **{f'etc/systemd/system/{name}': 0o644 for name in UNITS},
             'usr/local/sbin/bmi30-refresh-network-identity.sh': 0o755,
             'usr/local/lib/bmi30/bmi30_core_bootstrap.py': 0o755,
             'home/techaid/Documents/hotspot_info_server.py': 0o755}
LEGACY_ALLOWLIST = dict(ALLOWLIST)
ACCESS_ASSETS = {'opt/bmi30-network/access.py': 420, 'opt/bmi30-network/access_policy.py': 420, 'opt/bmi30-network/access_power.py': 420, 'opt/bmi30-network/Open-BMI30.cmd': 420, 'opt/bmi30-network/vendor/qrcode/base.py': 420, 'opt/bmi30-network/vendor/qrcode/console_scripts.py': 420, 'opt/bmi30-network/vendor/qrcode/constants.py': 420, 'opt/bmi30-network/vendor/qrcode/exceptions.py': 420, 'opt/bmi30-network/vendor/qrcode/LUT.py': 420, 'opt/bmi30-network/vendor/qrcode/main.py': 420, 'opt/bmi30-network/vendor/qrcode/release.py': 420, 'opt/bmi30-network/vendor/qrcode/util.py': 420, 'opt/bmi30-network/vendor/qrcode/__init__.py': 420, 'opt/bmi30-network/vendor/qrcode-8.2.dist-info/entry_points.txt': 420, 'opt/bmi30-network/vendor/qrcode-8.2.dist-info/INSTALLER': 420, 'opt/bmi30-network/vendor/qrcode-8.2.dist-info/LICENSE': 420, 'opt/bmi30-network/vendor/qrcode-8.2.dist-info/METADATA': 420, 'opt/bmi30-network/vendor/qrcode-8.2.dist-info/RECORD': 420, 'opt/bmi30-network/vendor/qrcode-8.2.dist-info/REQUESTED': 420, 'opt/bmi30-network/vendor/qrcode-8.2.dist-info/WHEEL': 420, 'opt/bmi30-network/vendor/qrcode/compat/etree.py': 420, 'opt/bmi30-network/vendor/qrcode/compat/png.py': 420, 'opt/bmi30-network/vendor/qrcode/compat/__init__.py': 420, 'opt/bmi30-network/vendor/qrcode/image/base.py': 420, 'opt/bmi30-network/vendor/qrcode/image/pil.py': 420, 'opt/bmi30-network/vendor/qrcode/image/pure.py': 420, 'opt/bmi30-network/vendor/qrcode/image/styledpil.py': 420, 'opt/bmi30-network/vendor/qrcode/image/svg.py': 420, 'opt/bmi30-network/vendor/qrcode/image/__init__.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/consts.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/test_example.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/test_qrcode.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/test_qrcode_pil.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/test_qrcode_pypng.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/test_qrcode_svg.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/test_release.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/test_script.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/test_util.py': 420, 'opt/bmi30-network/vendor/qrcode/tests/__init__.py': 420, 'opt/bmi30-network/vendor/qrcode/image/styles/colormasks.py': 420, 'opt/bmi30-network/vendor/qrcode/image/styles/__init__.py': 420, 'opt/bmi30-network/vendor/qrcode/image/styles/moduledrawers/base.py': 420, 'opt/bmi30-network/vendor/qrcode/image/styles/moduledrawers/pil.py': 420, 'opt/bmi30-network/vendor/qrcode/image/styles/moduledrawers/svg.py': 420, 'opt/bmi30-network/vendor/qrcode/image/styles/moduledrawers/__init__.py': 420, 'etc/systemd/system/bmi30-access.service': 420, 'etc/systemd/logind.conf.d/80-bmi30-access.conf': 420, 'etc/systemd/system/bmi30-core.service.d/80-bmi30-offline-start.conf': 420, 'etc/NetworkManager/dnsmasq-shared.d/91-bmi30-captive-detection.conf': 420, 'usr/local/bin/bmi30-hotspot.sh': 493, 'usr/local/sbin/bmi30-wifi-watchdog.py': 493}
ALLOWLIST.update(ACCESS_ASSETS)
SCHEMA3_ALLOWLIST = dict(ALLOWLIST)
ALLOWLIST.update({f'opt/bmi30-network/{name}': 0o644 for name in
                 ('freshness.py', 'bluetooth_transport.py', 'bluez_service.py', 'transport_policy.py')})

PROJECT_SOURCES = {'usr/local/lib/bmi30/bmi30_core_bootstrap.py': 'utilities/bmi30_core_bootstrap.py',
                   'home/techaid/Documents/hotspot_info_server.py': 'hotspot_info_server.py'}
REQUIRED = {f'opt/bmi30-network/{name}' for name in NETWORK_FILES[:8]} | {
    f'etc/systemd/system/{name}' for name in UNITS[:3]}
META = 'metadata/portable-assets.json'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def managed_path(root, name):
    if name not in ALLOWLIST:
        raise ValueError('Unexpected portable asset path: ' + name)
    root = Path(root).resolve()
    target = root / name
    node = target
    while node != root:
        if node.is_symlink():
            raise ValueError('Symlink in portable asset path: ' + name)
        node = node.parent
    return target


def atomic_copy(source, target, mode, uid=0, gid=0):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.bmi30-asset-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as output, Path(source).open('rb') as reader:
            shutil.copyfileobj(reader, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        if hasattr(os, 'chown'):
            os.chown(temporary, uid, gid)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def collect(project, system_root, bundle):
    project, system_root, bundle = map(Path, (project, system_root, bundle))
    found = {}
    for name, mode in ALLOWLIST.items():
        leaf = Path(name).name
        if name in ACCESS_ASSETS:
            candidates = [system_root / name]
        elif name.startswith('opt/bmi30-network/'):
            candidates = [system_root / name, project / 'utilities/bmi30_communication' / leaf]
        elif name.startswith('etc/systemd/system/'):
            candidates = [system_root / name, project / 'ops/systemd' / leaf,
                          project / 'utilities/bmi30_communication' / leaf]
        elif name in PROJECT_SOURCES:
            candidates = [project / PROJECT_SOURCES[name], system_root / name]
        else:
            candidates = [project / 'utilities/refresh_network_identity.sh', system_root / name]
        source = next((p for p in candidates if p.is_file()), None)
        if source is None:
            continue
        target = bundle / 'system' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(mode)
        found[name] = digest(target)
    if ALLOWLIST.keys() - found.keys():
        raise ValueError('Missing portable runtime assets: ' + ', '.join(sorted(ALLOWLIST.keys() - found.keys())))
    path = bundle / META
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'schema': 4, 'assets': found}, indent=2) + '\n', encoding='utf8')
    validate(bundle)
    return found


def validate(bundle):
    bundle = Path(bundle)
    path = bundle / META
    if not path.exists():
        return {}  # Historical releases predate portable system assets.
    record = json.loads(path.read_text())
    if record.get('schema') not in (1, 2, 3, 4) or not isinstance(record.get('assets'), dict):
        raise ValueError('Invalid portable asset manifest')
    assets = record['assets']
    required = (ALLOWLIST.keys() if record['schema'] == 4 else SCHEMA3_ALLOWLIST.keys() if record['schema'] == 3
                else LEGACY_ALLOWLIST.keys() if record['schema'] == 2 else REQUIRED)
    if required - assets.keys():
        raise ValueError('Incomplete portable runtime assets')
    for name, expected in assets.items():
        path = managed_path(bundle / 'system', name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('Portable asset checksum mismatch: ' + name)
        if name.endswith('.py'):
            ast.parse(path.read_text(encoding='utf8'), filename=name)
        if name.endswith('.service'):
            text = path.read_text()
            if '--expected-host' in text or re.search(r'BMI30-[A-Fa-f0-9]{9,16}\b', text):
                raise ValueError('Host-specific guard in portable service: ' + name)
    present = {name for name in ALLOWLIST if (bundle / 'system' / name).is_file()}
    if present != assets.keys():
        raise ValueError('Incomplete portable asset checksum coverage')
    if record['schema'] in (2, 3, 4):
        core_unit = (bundle / 'system/etc/systemd/system/bmi30-core.service').read_text()
        if any(text not in core_unit for text in ('RuntimeDirectory=bmi30-lgpio', 'RuntimeDirectoryMode=0700',
                                                 'LG_WD=/run/bmi30-lgpio', '/usr/local/lib/bmi30/bmi30_core_bootstrap.py')):
            raise ValueError('Core service lacks the lgpio bootstrap contract')
        portal = bundle / 'project/hotspot_info_server.py'
        if not portal.is_file() or digest(portal) != assets['home/techaid/Documents/hotspot_info_server.py']:
            raise ValueError('Boot portal source differs from the bundle portal')
    return assets


def snapshot(directory, root='/', names=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'snapshot.json').exists():
        raise ValueError('Portable asset backup already exists')
    records = {}
    for name in sorted(ALLOWLIST if names is None else names):
        source = managed_path(root, name)
        record = {'exists': source.exists()}
        if source.exists():
            if not source.is_file():
                raise ValueError('Portable asset is not a regular file: ' + name)
            info = source.stat()
            target = directory / 'files' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            record.update(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid,
                          sha256=digest(target))
        records[name] = record
    (directory / 'snapshot.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf8')


def restart_active(root, names):
    if Path(root).resolve() != Path('/') or os.getenv('BMI30_OFFLINE_ACTIVATION') == '1':
        return
    subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=20)
    # Identity refresh may restart NetworkManager; next boot runs the updated
    # script. Do not invoke that oneshot while acquisition is running.
    # Core/portal restart only after the switcher has completed runtime staging.
    units = [unit for unit in UNITS[:4] if 'etc/systemd/system/' + unit in names]
    if 'etc/systemd/system/bmi30-access.service' in names:
        subprocess.run(['systemctl', 'enable', 'bmi30-access.service'], check=True, timeout=15)
        units += ['bmi30-access.service', 'bmi30-wifi-watchdog.service']
    for unit in units:
        active = subprocess.run(['systemctl', 'is-active', '--quiet', unit], timeout=10)
        if active.returncode == 0:
            subprocess.run(['systemctl', 'restart', unit], check=True, timeout=30)


def restore(directory, root='/', restart=True):
    directory = Path(directory)
    path = directory / 'snapshot.json'
    if not path.exists():
        return
    records = json.loads(path.read_text())
    for name, record in records.items():
        target = managed_path(root, name)
        if record['exists']:
            source = directory / 'files' / name
            if digest(source) != record['sha256']:
                raise ValueError('Portable asset backup checksum mismatch: ' + name)
            atomic_copy(source, target, record['mode'], record['uid'], record['gid'])
        elif target.exists():
            target.unlink()
    if restart:
        restart_active(root, records)


def apply(bundle, backup, root='/', restart=True):
    assets = validate(bundle)
    if not assets:
        return {}
    snapshot(backup, root, assets)
    try:
        for name in assets:
            target = managed_path(root, name)
            owner = target.stat() if target.exists() else None
            atomic_copy(Path(bundle) / 'system' / name, target, ALLOWLIST[name],
                        owner.st_uid if owner else 0, owner.st_gid if owner else 0)
        if restart:
            restart_active(root, assets)
    except Exception:
        restore(backup, root, restart=restart)
        raise
    return assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['collect', 'validate', 'apply', 'restore'])
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--system-root', type=Path, default=Path('/'))
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.action == 'collect':
        result = collect(args.project, args.system_root, args.bundle)
    elif args.action == 'validate':
        result = validate(args.bundle)
    elif args.action == 'apply':
        result = apply(args.bundle, args.backup, args.system_root)
    else:
        restore(args.backup, args.system_root); result = {}
    print(json.dumps({'ok': True, 'assets': sorted(result)}))


if __name__ == '__main__':
    main()
