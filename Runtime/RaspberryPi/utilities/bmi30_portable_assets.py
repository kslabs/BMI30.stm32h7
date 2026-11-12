"""Allowlisted portable system code; device configuration/credentials stay local."""
import argparse
import ast
import hashlib
import importlib.util
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
                 ('freshness.py', 'bluetooth_transport.py', 'bluez_service.py', 'transport_policy.py', 'cohesion.py')})

SCHEMA4_ALLOWLIST = dict(ALLOWLIST)
CENTRAL_ASSETS = {'opt/bmi30-central-wifi/central_wifi_client.py': 420, 'opt/bmi30-central-wifi/adapter.py': 420, 'opt/bmi30-central-wifi/central_wifi_runtime.py': 420, 'opt/bmi30-central-wifi/lifecycle.py': 420, 'opt/bmi30-central-wifi/daemon.py': 420, 'opt/bmi30-central-wifi/rollback_guard.py': 420, 'opt/bmi30-network/central_wifi_runtime.py': 420, 'etc/bmi30-network/central-wifi-ram-only': 420, 'etc/systemd/system/bmi30-central-wifi.service': 420}
ALLOWLIST.update(CENTRAL_ASSETS)

SCHEMA5_ALLOWLIST = dict(ALLOWLIST)
DNS_ASSETS = {'opt/bmi30-network/dns_policy.py': 420, 'etc/NetworkManager/dnsmasq-shared.d/90-bmi30-captive-portal.conf': 420}
ALLOWLIST.update(DNS_ASSETS)

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
    record = {'schema': 6, 'assets': found}
    candidates = [project / 'host/bmi30_full_update/payload',
                  project / 'host/bmi30_active_runtime/project/host/bmi30_full_update/payload']
    source_payload = next((candidate for candidate in candidates if (candidate / META).is_file()), None)
    if source_payload is None:
        raise ValueError('Known-native service variant source is missing')
    source_record = json.loads((source_payload / META).read_text())
    descriptor = source_record.get('native_service_variants')
    helper = _native_variant_helper()
    verified = helper.validate_variants(source_payload, descriptor, source_record['network_version'])
    for serial, source in verified.items():
        target = bundle / descriptor['variants'][serial]['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)
    record.update(network_version=source_record['network_version'], native_service_variants=descriptor)
    path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf8')
    validate(bundle)
    return found


def _native_variant_helper():
    path = Path(__file__).with_name('bmi30_native_service_variants.py')
    spec = importlib.util.spec_from_file_location('reviewed_native_network_variants', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native_service_source(bundle, root='/'):
    bundle = Path(bundle)
    record = json.loads((bundle / META).read_text())
    descriptor = record.get('native_service_variants')
    if descriptor is None:
        return None  # Historical releases retain their original portable contract.
    helper = _native_variant_helper()
    return helper.service_source(bundle, descriptor, helper.physical_serial(root), record['network_version'])


def validate(bundle):
    bundle = Path(bundle)
    path = bundle / META
    if not path.exists():
        return {}  # Historical releases predate portable system assets.
    record = json.loads(path.read_text())
    if record.get('schema') not in (1, 2, 3, 4, 5, 6) or not isinstance(record.get('assets'), dict):
        raise ValueError('Invalid portable asset manifest')
    assets = record['assets']
    required = (ALLOWLIST.keys() if record['schema'] == 6 else SCHEMA5_ALLOWLIST.keys() if record['schema'] == 5 else SCHEMA4_ALLOWLIST.keys() if record['schema'] == 4 else SCHEMA3_ALLOWLIST.keys() if record['schema'] == 3
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
    if record['schema'] in (2, 3, 4, 5, 6):
        core_unit = (bundle / 'system/etc/systemd/system/bmi30-core.service').read_text()
        if any(text not in core_unit for text in ('RuntimeDirectory=bmi30-lgpio', 'RuntimeDirectoryMode=0700',
                                                 'LG_WD=/run/bmi30-lgpio', '/usr/local/lib/bmi30/bmi30_core_bootstrap.py')):
            raise ValueError('Core service lacks the lgpio bootstrap contract')
        portal = bundle / 'project/hotspot_info_server.py'
        if not portal.is_file() or digest(portal) != assets['home/techaid/Documents/hotspot_info_server.py']:
            raise ValueError('Boot portal source differs from the bundle portal')
    if record.get('native_service_variants') is not None:
        _native_variant_helper().validate_variants(bundle, record['native_service_variants'], record['network_version'])
    return assets


DNS_RESTORE_NAMES = ('etc/NetworkManager/dnsmasq-shared.d/90-bmi30-captive-portal.conf',
                     'etc/NetworkManager/dnsmasq-shared.d/91-bmi30-captive-detection.conf')

def dns_restore_module(directory, root='/'):
    import importlib.util
    parent = Path(__file__).parent
    choices = (Path(directory)/'dns_policy.py',
               parent/'payload/system/opt/bmi30-network/dns_policy.py',
               parent.parent/'host/bmi30_full_update/payload/system/opt/bmi30-network/dns_policy.py',
               Path(root)/'opt/bmi30-network/dns_policy.py')
    for source in choices:
        if source.is_symlink():
            raise ValueError('Unsafe DNS restore helper')
        if not source.exists():
            continue
        if not source.is_file() or digest(source) != '846d8d7ac00ece0178e17c336c060d70ae30a88bf6daae64afb604b087b859c0':
            raise ValueError('DNS restore helper checksum mismatch')
        spec = importlib.util.spec_from_file_location('portable_canonical_dns', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, source
    raise ValueError('Reviewed DNS restore helper is unavailable')

def dns_restore_plan(directory, root, records, expected_current=None):
    result, module = {}, None
    for name in DNS_RESTORE_NAMES:
        if name not in records:
            continue
        record = records[name]
        target = managed_path(root, name)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('Unsafe DNS restore target')
        current = target.read_bytes() if target.exists() else None
        actual = hashlib.sha256(current).hexdigest() if current is not None else None
        before = record.get('sha256') if record['exists'] else None
        if record['exists'] and digest(Path(directory)/'files'/name) != before:
            raise ValueError('DNS backup checksum mismatch')
        if actual == before:
            result[name] = None
            continue
        if module is None:
            module, unused = dns_restore_module(directory, root)
        if expected_current is not None:
            allowed = expected_current.get(name, ())
        else:
            # Older schema5 snapshots have no after-hash map. Only exact
            # reviewed canonical outputs may be undone, never arbitrary edits.
            outputs = [module.render()]
            try:
                config = json.loads((Path(root)/'etc/bmi30-network/config.json').read_text())
                outputs.append(module.render(config['id']))
            except (OSError, ValueError, KeyError, TypeError):
                pass
            allowed = {hashlib.sha256(policy[Path(name).name].encode('utf-8')).hexdigest()
                       for policy in outputs}
            allowed.add(before)
        if actual not in allowed:
            raise ValueError('Later DNS owner prevents restore')
        result[name] = current.decode('utf-8') if current is not None else None
    return result, module


def snapshot(directory, root='/', names=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'snapshot.json').exists():
        raise ValueError('Portable asset backup already exists')
    unused, dns_source = dns_restore_module(directory, root)
    atomic_copy(dns_source, directory / 'dns_policy.py', 0o644)
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


def assert_legacy_network_restore_safe(directory, root='/'):
    """Never expose retained scoped RAM credentials to a legacy service."""
    if Path(root).resolve() != Path('/') or os.getenv('BMI30_OFFLINE_ACTIVATION') == '1':
        return
    saved = Path(directory) / 'files/opt/bmi30-network/service.py'
    safe = {'4322a4ddb28815f6470e140b47e460a61b1fe8888ae9ddaf758f2bb348e34945',
            'b4abc246559863f1144db841007c63d692d93de56b8eb2db6a81484c2e38fbbe', '6b9a0ef7ea855cd5cbee4764a823a1830cf31b1bfaeb1d1548afa520b931edc8'}
    snapshot = Path(directory) / 'files'
    marker = snapshot / 'etc/bmi30-network/central-wifi-ram-only'
    helper = snapshot / 'opt/bmi30-network/central_wifi_runtime.py'
    network = snapshot / 'opt/bmi30-network/network.py'
    if (saved.is_file() and digest(saved) in safe
            and marker.is_file() and not marker.is_symlink() and marker.read_bytes() == b'1\n'
            and helper.is_file() and digest(helper) == '79d19b454185cd879929941f2d0e27421044ff5b058ec594bb7b700edd9b88e9'
            and network.is_file() and digest(network) in ('29c7cba7ccd22c15b885983d0d5b91e9cc724da1fe48d18a5afd523d34400bd3', 'a6611b5a155107a940e3300af8643bbe61c70f9698641822d33489981e03d449')):
        return
    # Do not infer safety from the marker: NM profiles outlive that file.
    program = "import sys,json;sys.path.insert(0,'/opt/bmi30-central-wifi');from rollback_guard import assert_legacy_safe;print(json.dumps(assert_legacy_safe()))"
    result = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', program],
                            capture_output=True, text=True, timeout=120)
    try:
        value = json.loads(result.stdout) if result.returncode == 0 else {}
    except Exception:
        value = {}
    if value.get('ok') is not True or value.get('central_RAM_profile_count') != 0:
        raise ValueError('Legacy restore blocked: scoped RAM profiles remain or their absence cannot be proven; no connection was deleted or disconnected')


def restore(directory, root='/', restart=True, expected_current=None):
    directory = Path(directory)
    path = directory / 'snapshot.json'
    if not path.exists():
        return
    assert_legacy_network_restore_safe(directory, root)
    records = json.loads(path.read_text())
    dns_plan, dns_module = dns_restore_plan(directory, root, records, expected_current)
    for name, record in records.items():
        target = managed_path(root, name)
        if name in dns_plan:
            if dns_plan[name] is not None:
                if record['exists']:
                    source = directory / 'files' / name
                    if digest(source) != record['sha256']:
                        raise ValueError('DNS backup checksum mismatch')
                    dns_module.atomic_owned(target, source.read_bytes().decode('utf-8'),
                                            dns_plan[name], record['mode'])
                    if hasattr(os, 'chown'):
                        os.chown(target, record['uid'], record['gid'])
                else:
                    dns_module.remove_owned(target, dns_plan[name])
            continue
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
    if any(name in assets for name in DNS_RESTORE_NAMES):
        raise ValueError('Direct portable DNS apply is disabled; use the reviewed full component owner')
    if not assets:
        return {}
    selected = native_service_source(bundle, root)  # Fresh physical CM5, before snapshot/write.
    effective = dict(assets)
    if selected is not None:
        effective['opt/bmi30-network/service.py'] = digest(selected)
    snapshot(backup, root, assets)
    try:
        for name in assets:
            target = managed_path(root, name)
            owner = target.stat() if target.exists() else None
            source = selected if selected is not None and name == 'opt/bmi30-network/service.py' else Path(bundle) / 'system' / name
            atomic_copy(source, target, ALLOWLIST[name],
                        owner.st_uid if owner else 0, owner.st_gid if owner else 0)
        if restart:
            restart_active(root, assets)
    except Exception:
        restore(backup, root, restart=restart)
        raise
    return effective


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
