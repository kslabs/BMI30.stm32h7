"""Apply a network-only hotfix after the stock firmware transaction commits."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
PACKAGE = HERE / 'bmi30_legacy_recovery'
DOCS = Path('/home/techaid/Documents')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def active_bundle():
    for line in (DOCS / 'host/bmi30_split_active_version.env').read_text().splitlines():
        if line.startswith('BMI30_SPLIT_BUNDLE_ID='):
            return shlex.split(line.split('=', 1)[1])[0]
    return ''


def committed(bundle):
    import fcntl
    try:
        with (DOCS / '.bmi30_cloud_sync/server_operation.lock').open('rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
            record = json.loads((DOCS / '.bmi30_cloud_sync/server/last_transaction.json').read_text())
            return record.get('target', {}).get('FIRMWARE_BUNDLE_ID') == bundle and active_bundle() == bundle
    except (OSError, ValueError):
        return False


def atomic(path, data, mode=0o600):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(mode)
    temporary.replace(path)


def update_state(path, state):
    atomic(path, (json.dumps(state, indent=2) + '\n').encode())


def start_guard(directory, manifest):
    unit = 'bmi30-legacy-guard-' + manifest['bundle_id']
    status = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=5)
    if status.stdout.strip() == 'active':
        return
    subprocess.run(['systemd-run', '--quiet', '--collect', '--unit=' + unit,
                    '/usr/bin/python3', str(directory / 'guard.py'), '--state', str(directory / 'state.json')],
                   check=True, capture_output=True, timeout=10)


def apply(manifest, expected):
    current = digest('/opt/bmi30-network/service.py')
    selected = None
    for item in manifest['variants']:
        if current in (item['before'], item['after'], *item.get('previous', [])):
            selected = item
            break
    if selected is None:
        raise RuntimeError('Unsupported existing network source')
    payload = PACKAGE / selected['directory']
    for name, checksum in selected['sha256'].items():
        if digest(payload / name) != checksum:
            raise RuntimeError('Network recovery package checksum mismatch')
    spec = importlib.util.spec_from_file_location('selected_recovery_installer', payload / 'install_recovery.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    installer.identify(expected)
    directory = Path('/var/lib/bmi30-legacy-menu') / manifest['bundle_id']
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = directory / 'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state.update(bundle_id=manifest['bundle_id'], expected_id=expected, stage='installing',
                 network_versions=[v['network_version'] for v in manifest['variants']])
    update_state(state_path, state)
    guard = PACKAGE / 'guard.py'
    if digest(guard) != manifest['guard_sha256']:
        raise RuntimeError('Recovery guard checksum mismatch')
    atomic(directory / 'guard.py', guard.read_bytes(), 0o700)
    start_guard(directory, manifest)
    result = installer.apply(expected, payload=payload)
    backup = result.get('backup') or state.get('backup')
    if backup is None:
        raise RuntimeError('Recovery backup was not confirmed')
    state.update(stage=result['stage'], backup=backup)
    update_state(state_path, state)
    # A normal menu update has already committed; health verification includes
    # the running portal and network. No extra user/SSH action is required.
    if not committed(manifest['bundle_id']):
        raise RuntimeError('Firmware transaction changed before network commit')
    installer.commit(Path(backup), expected)
    state.update(stage='committed', network_version=selected['network_version'])
    update_state(state_path, state)


def run(manifest, expected):
    while active_bundle() == manifest['bundle_id']:
        if committed(manifest['bundle_id']):
            try:
                apply(manifest, expected)
                print('[LEGACY-RECOVERY] Network component ready', flush=True)
                return
            except Exception as error:
                print('[LEGACY-RECOVERY] Setup deferred: ' + type(error).__name__, flush=True)
        time.sleep(5)


def start():
    manifest = json.loads((PACKAGE / 'manifest.json').read_text())
    actual = 'BMI30-' + Path('/proc/device-tree/serial-number').read_text().strip('\0\n')[-9:].upper()
    if actual not in manifest['donor_ids']:
        raise RuntimeError('This recovery firmware is for another CM5; keep the previous release')
    threading.Thread(target=run, args=(manifest, actual), daemon=True, name='bmi30-legacy-recovery-setup').start()
