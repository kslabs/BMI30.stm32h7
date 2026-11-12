#!/usr/bin/env python3
"""Install two network files on one donor, with an independent rollback timer."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
RUNTIME = Path('/opt/bmi30-network')
BACKUPS = Path('/var/backups/bmi30-network-recovery')
TARGET_ID = 'BMI30-27448D72B'
DONOR_IDS = ('BMI30-05AB38E6A', 'BMI30-D5507EDFC', 'BMI30-7AA5CED27')
FILES = ('service.py', 'legacy_recovery.py')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def hardware_identity():
    for path in (Path('/proc/device-tree/serial-number'), Path('/sys/firmware/devicetree/base/serial-number')):
        try:
            serial = path.read_text().strip('\0 \r\n\t')
        except OSError:
            continue
        if re.fullmatch(r'[0-9a-fA-F]{16}', serial) and int(serial, 16):
            return 'BMI30-' + serial[-9:].upper()
    raise RuntimeError('CM5 hardware identity unavailable')


def identify(expected):
    if expected not in DONOR_IDS or hardware_identity() != expected:
        raise RuntimeError('Physical CM5 identity mismatch; nothing changed')


def command(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30,
                            env={**os.environ, 'LC_ALL': 'C'})
    if check and result.returncode:
        raise RuntimeError('System operation failed: ' + args[0])
    return result.stdout.strip()


def atomic(path, data, mode=0o600, owner=None):
    path = Path(path)
    temporary = path.with_name(path.name + '.recovery-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if owner and hasattr(os, 'chown'):
            os.chown(temporary, *owner)
        os.replace(temporary, path)
        if os.name == 'posix' and hasattr(os, 'O_DIRECTORY'):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def state_write(backup, state):
    atomic(backup / 'state.json', (json.dumps(state, indent=2) + '\n').encode())


@contextlib.contextmanager
def transaction_lock(backup):
    with (backup / 'lock').open('a+b') as stream:
        if os.name == 'posix':
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def protected_files(runtime):
    paths = [runtime / name for name in ('protocol.py', 'network.py', 'group.py', 'policy.py')]
    docs = Path('/home/techaid/Documents')
    paths += [Path('/etc/bmi30-network/config.json'), Path('/etc/bmi30/portal_config.json'),
              docs / 'host/bmi30_config.json', docs / 'host/bmi30_split_active_version.env',
              Path('/usr/local/bin/bmi30-hotspot-info-server.py')]
    env = docs / 'host/bmi30_split_active_version.env'
    if env.is_file():
        for line in env.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key in ('BMI30_CORE_PATH', 'BMI30_ENGINE_SOURCE', 'BMI30_PORTAL_PATH',
                               'BMI30_CONFIG_JSON', 'BMI30_PROJECT_CONFIG_PATH'):
                path = Path(value.strip().strip('"\''))
                paths.append(path if path.is_absolute() else docs / path)
    nm = Path('/etc/NetworkManager/system-connections')
    if nm.is_dir():
        paths += [p for p in nm.iterdir() if p.is_file()]
    return {str(p.resolve()): digest(p.read_bytes()) for p in paths if p.is_file()}


def unchanged(protected):
    for name, expected in protected.items():
        path = Path(name)
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise RuntimeError('A protected file changed; update stopped')


def prepare(expected, runtime=RUNTIME, payload=HERE, config_path=Path('/etc/bmi30-network/config.json')):
    identify(expected)
    if expected == TARGET_ID:
        raise RuntimeError('This recovery package is for the three donors, not the target')
    runtime = runtime.resolve(strict=True)
    manifest = json.loads((payload / 'manifest.json').read_text())
    if (manifest['target_id'] != TARGET_ID or manifest['changed_runtime_files'] != list(FILES)
            or manifest['donor_ids'] != list(DONOR_IDS)):
        raise RuntimeError('Wrong recovery manifest')
    if json.loads(config_path.read_text()).get('id') != expected:
        raise RuntimeError('Network identity differs from the physical CM5; nothing changed')
    actual_pair = {name: digest((runtime / name).read_bytes()) if (runtime / name).is_file() else None for name in FILES}
    approved_previous = actual_pair in manifest.get('accepted_previous_payloads', [])
    plan = []
    for name in FILES:
        target = runtime / name
        if target.is_symlink():
            raise RuntimeError('Unexpected runtime symlink')
        original = target.read_bytes() if target.is_file() else None
        candidate = (payload / name).read_bytes()
        if digest(candidate) != manifest['payload_sha256'][name]:
            raise RuntimeError('Recovery payload checksum mismatch')
        compile(candidate, name, 'exec')
        if name == 'service.py' and not approved_previous and (original is None or digest(original) not in
                (manifest['base_service_sha256'], manifest['payload_sha256'][name])):
            raise RuntimeError('Installed service differs from the shipped 0.1.13; nothing changed')
        if name == 'legacy_recovery.py' and original is not None and original != candidate and not approved_previous:
            raise RuntimeError('An unrelated recovery module already exists; nothing changed')
        stat = target.stat() if target.is_file() else (runtime / 'service.py').stat()
        plan.append({'name': name, 'path': target, 'original': original, 'candidate': candidate,
                     'mode': stat.st_mode & 0o777, 'owner': (stat.st_uid, stat.st_gid)})
    protected = protected_files(runtime)
    protected[str(config_path.resolve())] = digest(config_path.read_bytes())
    return plan, manifest, protected


def verify(version):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(20):
        try:
            with opener.open('http://127.0.0.1:8791/status', timeout=2) as response:
                status = json.load(response)
            with opener.open('http://127.0.0.1/api/status', timeout=2) as response:
                json.load(response)
            if (status.get('version') == version and status.get('legacy_recovery', {}).get('target_id') == TARGET_ID
                    and status['legacy_recovery'].get('enabled') is True):
                return
        except (OSError, ValueError):
            pass
        time.sleep(.5)
    raise RuntimeError('Network or existing portal failed readiness checks')


def stop_timer(state):
    command('systemctl', 'stop', state['timer_unit'] + '.timer', check=False)


def load_state(backup, expected):
    identify(expected)
    state = json.loads((backup / 'state.json').read_text())
    if state.get('expected_id') != expected or state.get('format') != 1:
        raise RuntimeError('Wrong rollback transaction')
    if [f['name'] for f in state['files']] != list(FILES):
        raise RuntimeError('Invalid rollback file list')
    return state


def rollback(backup, expected, automatic=False):
    backup = Path(backup).resolve(strict=True)
    with transaction_lock(backup):
        state = load_state(backup, expected)
        if (automatic and state['stage'] == 'committed') or state['stage'] == 'rolled-back':
            return {'stage': state['stage'], 'restored': state['stage'] == 'rolled-back'}
        runtime = Path(state['runtime']).resolve(strict=True)
        dependencies_changed = any(not Path(name).is_file() or digest(Path(name).read_bytes()) != expected_hash
                                   for name, expected_hash in state.get('runtime_guard', {}).items())
        superseded = dependencies_changed
        for item in state['files']:
            path = runtime / item['name']
            if path.is_symlink():
                raise RuntimeError('Rollback runtime symlink changed')
            actual = digest(path.read_bytes()) if path.is_file() else None
            if actual not in (item['before'], item['after']):
                superseded = True
        if superseded:
            state['stage'] = 'superseded-by-another-update'
            state_write(backup, state)
            stop_timer(state)
            return {'stage': state['stage'], 'restored': False}
        for index, item in enumerate(state['files']):
            if item['before'] is not None:
                if digest((backup / ('original-' + str(index))).read_bytes()) != item['before']:
                    raise RuntimeError('Rollback backup checksum mismatch')
        for index, item in reversed(list(enumerate(state['files']))):
            path = runtime / item['name']
            if item['before'] is None:
                path.unlink(missing_ok=True)
            else:
                data = (backup / ('original-' + str(index))).read_bytes()
                if digest(data) != item['before']:
                    raise RuntimeError('Rollback backup checksum mismatch')
                atomic(path, data, item['mode'], tuple(item['owner']))
        command('systemctl', 'restart', 'bmi30-network')
        state['stage'] = 'rolled-back'
        state_write(backup, state)
        stop_timer(state)
        return {'stage': state['stage'], 'restored': True}


def apply(expected, runtime=RUNTIME, payload=HERE, backups=BACKUPS, config_path=Path('/etc/bmi30-network/config.json')):
    plan, manifest, protected = prepare(expected, runtime, payload, config_path)
    if all(item['original'] == item['candidate'] for item in plan):
        verify(manifest['network_version'])
        pending = []
        if backups.is_dir():
            for record in backups.glob('*/state.json'):
                state = json.loads(record.read_text())
                if (state.get('stage') == 'awaiting-external-commit' and state.get('expected_id') == expected
                        and state.get('runtime') == str(runtime.resolve())):
                    pending.append(record.parent)
        if len(pending) > 1:
            raise RuntimeError('Multiple pending recovery transactions; no files changed')
        if pending:
            return {'stage': 'awaiting-external-commit', 'expected_id': expected, 'backup': str(pending[0]),
                    'network_version': manifest['network_version']}
        return {'stage': 'already-installed', 'expected_id': expected}
    if command('systemctl', 'is-active', 'bmi30-network') != 'active':
        raise RuntimeError('Existing network service is not active')
    core = command('systemctl', 'show', 'bmi30-core', '-p', 'MainPID', '--value')
    if not core.isdigit() or int(core) == 0:
        raise RuntimeError('Acquisition owner is not running')
    backup = backups / uuid.uuid4().hex
    backup.mkdir(parents=True, mode=0o700 if os.name == 'posix' else 0o777)
    state = {'format': 1, 'expected_id': expected, 'runtime': str(runtime.resolve()), 'stage': 'prepared',
             'network_version': manifest['network_version'], 'core_pid': core, 'protected': protected,
             'runtime_guard': {name: value for name, value in protected.items()
                               if Path(name).parent == runtime.resolve()},
             'timer_unit': 'bmi30-legacy-rollback-' + uuid.uuid4().hex[:12], 'files': []}
    for index, item in enumerate(plan):
        if item['original'] is not None:
            atomic(backup / ('original-' + str(index)), item['original'])
        state['files'].append({'name': item['name'], 'before': None if item['original'] is None else digest(item['original']),
                               'after': digest(item['candidate']), 'mode': item['mode'], 'owner': item['owner']})
    atomic(backup / 'rollback_tool.py', Path(__file__).read_bytes(), 0o700)
    state_write(backup, state)
    # The timer is owned by systemd and its rollback tool lives in the backup,
    # so an SSH disconnect or killed installer does not cancel recovery.
    command('systemd-run', '--quiet', '--unit=' + state['timer_unit'], '--on-active=300s',
            '--timer-property=AccuracySec=1s', '/usr/bin/python3', str(backup / 'rollback_tool.py'),
            '--expected-id', expected, '--automatic', '--rollback', str(backup))
    try:
        with transaction_lock(backup):
            identify(expected)
            unchanged(protected)
            for item in plan:
                current = item['path'].read_bytes() if item['path'].is_file() else None
                if current != item['original']:
                    raise RuntimeError('Runtime changed after preflight')
            for item in reversed(plan):  # New module first; service entry last.
                atomic(item['path'], item['candidate'], item['mode'], item['owner'])
            command('systemctl', 'restart', 'bmi30-network')
            verify(manifest['network_version'])
            unchanged(protected)
            if command('systemctl', 'show', 'bmi30-core', '-p', 'MainPID', '--value') != core:
                raise RuntimeError('Acquisition owner PID changed')
            state['stage'] = 'awaiting-external-commit'
            state_write(backup, state)
    except BaseException:
        rollback(backup, expected)
        raise
    return {'stage': state['stage'], 'expected_id': expected, 'backup': str(backup),
            'rollback_after_seconds': 300, 'network_version': manifest['network_version']}


def commit(backup, expected):
    backup = Path(backup).resolve(strict=True)
    with transaction_lock(backup):
        state = load_state(backup, expected)
        if state['stage'] not in ('awaiting-external-commit', 'committed'):
            raise RuntimeError('Transaction is not ready to commit')
        for item in state['files']:
            path = Path(state['runtime']) / item['name']
            if path.is_symlink() or digest(path.read_bytes()) != item['after']:
                raise RuntimeError('Installed files changed before commit')
        unchanged(state['protected'])
        verify(state['network_version'])
        stop_timer(state)
        state['stage'] = 'committed'
        state_write(backup, state)
        return {'stage': 'committed', 'expected_id': expected, 'backup': str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-id', required=True)
    parser.add_argument('--automatic', action='store_true', help=argparse.SUPPRESS)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--apply', action='store_true')
    actions.add_argument('--commit', type=Path)
    actions.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    if args.automatic and not args.rollback:
        raise RuntimeError('Automatic mode is only valid for the rollback timer')
    if (args.apply or args.commit or args.rollback) and (sys.platform != 'linux' or os.geteuid() != 0):
        raise RuntimeError('Apply/commit/rollback require root on the selected Raspberry Pi')
    if args.apply:
        result = apply(args.expected_id)
    elif args.commit:
        result = commit(args.commit, args.expected_id)
    elif args.rollback:
        result = rollback(args.rollback, args.expected_id, args.automatic)
    else:
        plan, manifest, _protected = prepare(args.expected_id)
        result = {'stage': 'dry-run', 'expected_id': args.expected_id, 'target_id': TARGET_ID,
                  'network_version': manifest['network_version'], 'changed_files': list(FILES),
                  'already_installed': all(p['original'] == p['candidate'] for p in plan)}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
