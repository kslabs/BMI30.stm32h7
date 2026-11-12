"""Independent rollback guard; lives outside portal/network service cgroups."""
import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import time
import uuid

DOCS = Path('/home/techaid/Documents')
LOCK = DOCS / '.bmi30_cloud_sync/server_operation.lock'


def active_bundle():
    for line in (DOCS / 'host/bmi30_split_active_version.env').read_text().splitlines():
        if line.startswith('BMI30_SPLIT_BUNDLE_ID='):
            return shlex.split(line.split('=', 1)[1])[0]
    return ''


@contextlib.contextmanager
def firmware_idle():
    with LOCK.open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    with temporary.open('w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


def pending_backup(state):
    backup = state.get('backup')
    if backup:
        return Path(backup)
    matches = []
    for path in Path('/var/backups/bmi30-network-recovery').glob('*/state.json'):
        record = json.loads(path.read_text())
        if (record.get('expected_id') == state['expected_id']
                and record.get('network_version') in state['network_versions']
                and record.get('stage') in ('committed', 'awaiting-external-commit', 'prepared')):
            matches.append(path)
    return max(matches, key=lambda p: p.stat().st_mtime_ns).parent if matches else None


def step(path):
    state = json.loads(path.read_text())
    actual = 'BMI30-' + Path('/proc/device-tree/serial-number').read_text().strip('\0\n')[-9:].upper()
    if actual != state['expected_id']:
        raise RuntimeError('Wrong CM5 for rollback guard')
    with firmware_idle():
        if active_bundle() == state['bundle_id']:
            return False
        backup = pending_backup(state)
        if backup is None:
            state.update(stage='inactive-without-network-write')
            atomic_json(path, state)
            return True
        tool = backup / 'rollback_tool.py'
        spec = importlib.util.spec_from_file_location('saved_recovery_rollback', tool)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.rollback(backup, actual)
        if result.get('stage') == 'deferred-by-firmware-operation':
            return False
        state.update(stage=result['stage'], rollback=result, backup=str(backup))
        atomic_json(path, state)
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    while True:
        try:
            if step(args.state):
                return
        except BlockingIOError:
            pass
        except Exception as error:
            print('Recovery guard deferred: ' + type(error).__name__, flush=True)
        time.sleep(3)


if __name__ == '__main__':
    main()
