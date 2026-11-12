"""Build stock-server archives without changing detector code or system assets."""
import argparse
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

import make_server_sources as sources

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
LIBRARY = ROOT / '.tmp/relay-web-20260929/192.168.0.163/audit/home/techaid/Documents/utilities/bmi30_server_files.py'
spec = importlib.util.spec_from_file_location('firmware_contract', LIBRARY)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
TEST_IDS = ('BMI30-6922A5A0D', 'BMI30-D5EB8F26C')
CLIENT_IDS = ('BMI30-05AB38E6A', 'BMI30-D5507EDFC', 'BMI30-7AA5CED27')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_bundle(path, modes=None):
    files = {}
    with tarfile.open(path) as archive:
        for item in archive.getmembers():
            if not item.isfile():
                continue
            name = PurePosixPath(item.name)
            if '..' in name.parts or name.is_absolute():
                raise ValueError('Unsafe base archive')
            relative = '/'.join(name.parts[1:])
            files[relative] = archive.extractfile(item).read()
            if modes is not None:
                modes[relative] = item.mode & 0o777
    return files


def build(kind, bundle_id, firmware_version, created):
    contract.version(firmware_version)
    base_id = '2026-10-23-1514' if kind == 'test' else '2026-10-05-1930'
    base = ROOT / 'FirmwareReleases/RaspberryPi' / base_id / ('BMI30-RPi-' + base_id + '.tar.gz')
    donors = TEST_IDS if kind == 'test' else CLIENT_IDS
    modes = {}
    files = read_bundle(base, modes)
    original_files = dict(files)
    manifest = contract.parse_env(files['manifest.env'])
    manifest['BMI30_BUNDLE_PORTABLE_SYSTEM'] = '0'
    for name in list(files):
        if name.startswith('metadata/') or (name.startswith('system/') and name not in
                (manifest.get('BMI30_BUNDLE_SYSTEM_CONFIG_REL'), manifest.get('BMI30_BUNDLE_INSTALLED_CONFIG_REL'))):
            del files[name]
    # No portable manifest: the stock updater leaves all existing system assets
    # alone. Only the committed portal launches the two-file network installer.
    portal_rel = manifest['BMI30_BUNDLE_PORTAL_REL']
    portal_before = files[portal_rel]
    files[portal_rel] = sources.portal(portal_before.decode()).encode()
    host = 'project/host/'
    files[host + 'bmi30_legacy_menu_bootstrap.py'] = (HERE / 'bundle_bootstrap.py').read_bytes()
    guard = (HERE / 'bundle_guard.py').read_bytes()
    helper = sources.helper(donors, bundle_id, sha(guard)).encode()
    network_source = (ROOT / '.tmp/legacy-menu-20261027/M10-network-original.py').read_text() if kind == 'test' else (
        ROOT / '.tmp/wifi-offline-20261027/baseline/network/2026-10-05-network-13/service.py').read_text()
    base_network_version = '0.2.4' if kind == 'test' else '0.1.13'
    network_version = base_network_version + '-recovery4'
    candidate = sources.service(network_source, network_version).encode()
    directory = 'network-' + base_network_version
    payload_rel = host + 'bmi30_legacy_recovery/'
    installer = sources.installer(donors, kind == 'test').encode()
    payload = {'service.py': candidate, 'legacy_recovery.py': helper, 'install_recovery.py': installer}
    component = {'format': 1, 'target_id': 'BMI30-27448D72B', 'donor_ids': list(donors),
                 'network_version': network_version, 'changed_runtime_files': ['service.py', 'legacy_recovery.py'],
                 'base_service_sha256': sha(network_source.encode()),
                 'payload_sha256': {name: sha(data) for name, data in payload.items()}}
    previous_id = '2026-10-27-131304' if kind == 'test' else '2026-10-27-133000'
    previous = ROOT / 'FirmwareReleases/RaspberryPi' / previous_id / 'review/bundle/project/host/bmi30_legacy_recovery' / directory
    component['accepted_previous_payloads'] = [{name: sha((previous / name).read_bytes())
                                                for name in ('service.py', 'legacy_recovery.py')}]
    payload['manifest.json'] = (json.dumps(component, indent=2) + '\n').encode()
    for name, data in payload.items():
        files[payload_rel + directory + '/' + name] = data
    files[payload_rel + 'guard.py'] = guard
    menu = {'bundle_id': bundle_id, 'donor_ids': list(donors), 'target_id': component['target_id'],
            'guard_sha256': sha(guard), 'variants': [{'directory': directory, 'before': component['base_service_sha256'],
            'after': sha(candidate), 'network_version': network_version,
            'previous': [p['service.py'] for p in component['accepted_previous_payloads']],
            'sha256': {name: sha(data) for name, data in payload.items()}}]}
    files[payload_rel + 'manifest.json'] = (json.dumps(menu, indent=2) + '\n').encode()
    label = ('TEST S09 M10: ' if kind == 'test' else 'M01 S11 S13: ') + 'active Wi-Fi recovery for S12; detector unchanged'
    sys.path.insert(0,str(ROOT/'HostTools/usb_recovery'))
    from integrate_bundle import add_to_bundle,validate_included
    add_to_bundle(files,manifest,bundle_id);validate_included(files,manifest)
    manifest.update(BMI30_BUNDLE_ID=bundle_id, BMI30_BUNDLE_CREATED_AT=created,
                    BMI30_BUNDLE_LABEL=label, BMI30_BUNDLE_ORIGIN='Based on ' + base_id + '; credential exchange recovery only')
    files['manifest.env'] = contract.env_bytes(manifest)
    release_rel = manifest['BMI30_BUNDLE_RELEASE_REL']
    release = contract.parse_env(files[release_rel])
    release.update(BMI30_FIRMWARE_VERSION=firmware_version, BMI30_FIRMWARE_BUNDLE_ID=bundle_id,
                   BMI30_FIRMWARE_CREATED_AT=created, BMI30_FIRMWARE_LABEL=label)
    for part in ('CORE', 'ENGINE', 'GUI', 'PORTAL'):
        rel = manifest['BMI30_BUNDLE_' + part + '_REL']
        release['BMI30_FIRMWARE_' + part + '_SHA256'] = sha(files[rel])
        if part != 'PORTAL':
            assert files[rel] == original_files[rel], part + ' code changed'
    files[release_rel] = contract.env_bytes(release)
    files['SHA256SUMS'] = ''.join(sha(data) + '  ' + name + '\n' for name, data in sorted(files.items())
                                 if name != 'SHA256SUMS').encode()
    output = ROOT / 'FirmwareReleases/RaspberryPi' / bundle_id
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / 'review/bundle'
    for name, data in files.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if name.endswith('.py'):
            ast.parse(data, filename=name)
    contract.verify_bundle(bundle, bundle_id)
    outer = {'host/bmi30_firmware_release.env': files[release_rel],
             'host/bmi30_split_active_version.env': contract.env_bytes({'BMI30_SPLIT_BUNDLE_ID': bundle_id})}
    outer.update({'host/bmi30_split_bundles/' + bundle_id + '/' + name: data for name, data in files.items()})
    archive_name = 'bmi30_backup_' + firmware_version[:10].replace('-', '') + '_' + firmware_version[-6:] + '.tar.gz'
    archive_path = output / archive_name
    with tarfile.open(archive_path, 'w:gz') as archive:
        for name, data in sorted(outer.items()):
            item = tarfile.TarInfo('Documents/' + name)
            prefix = 'host/bmi30_split_bundles/' + bundle_id + '/'
            mode = modes.get(name[len(prefix):], 0o644) if name.startswith(prefix) else 0o644
            item.size, item.mode, item.mtime = len(data), mode, 0
            archive.addfile(item, io.BytesIO(data))
    info = contract.marker(contract.env_bytes({'FIRMWARE_VERSION': firmware_version, 'FIRMWARE_BUNDLE_ID': bundle_id,
        'ARCHIVE_NAME': archive_name, 'ARCHIVE_SIZE': str(archive_path.stat().st_size), 'ARCHIVE_SHA256': sha(archive_path.read_bytes())}))
    (output / 'bmi30_latest.env').write_bytes(contract.env_bytes(info))
    report = {'kind': kind, 'base_bundle': base_id, 'bundle_id': bundle_id, 'firmware_version': firmware_version,
              'created_at': created, 'donor_ids': list(donors), 'target_id': component['target_id'],
              'archive': str(archive_path), 'marker': info, 'network_version': network_version,
              'component_hashes': {part: release['BMI30_FIRMWARE_' + part + '_SHA256'] for part in ('CORE', 'ENGINE', 'GUI', 'PORTAL')},
              'detector_code_unchanged': True, 'portable_system_assets_applied': False, 'published': False, 'tested_on_hardware': False}
    (output / 'release.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=['test', 'client'], required=True)
    parser.add_argument('--bundle-id', required=True)
    parser.add_argument('--firmware-version', required=True)
    parser.add_argument('--created-at', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.kind, args.bundle_id, args.firmware_version, args.created_at), indent=2))
