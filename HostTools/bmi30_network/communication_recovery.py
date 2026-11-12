"""Persist channel permissions across boot and consume only local STM32 K1 requests.

Run --prepare before NetworkManager reads its profiles; --daemon waits for the
existing acquisition owner, never opens USB, and ACKs only after durable apply.
"""
from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

CHANNELS = ('hotspot', 'wifi', 'ethernet', 'remote')
DEFAULT_CONFIG = Path('/home/techaid/Documents/host/bmi30_config.json')
LOG = logging.getLogger('bmi30-communication-recovery')


def read_config(path):
    path = Path(path)
    if not path.exists():
        return {}
    if path.stat().st_size > 1048576:
        raise ValueError('Configuration exceeds limit')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Invalid configuration')
    return value


def permissions(config):
    value = config.get('channel_permissions', {})
    if not isinstance(value, dict):
        raise ValueError('Invalid channel permissions')
    result = {}
    for channel in CHANNELS:
        permitted = value.get(channel, True)
        if type(permitted) is not bool:
            raise ValueError('Invalid channel permission')
        result[channel] = permitted
    import access_policy
    if access_policy.offline_mode():
        result['wifi'] = False
    return result


def atomic(path, data):
    path = Path(path)
    st = path.stat() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.comm-new')
    with tmp.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(tmp, st.st_mode & 0o777 if st else 0o600)
    if st and hasattr(os, 'chown'):
        os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, path)
    if hasattr(os, 'O_DIRECTORY'):
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def profile_channel(profile):
    kind = profile.get('connection', 'type', fallback='')
    if kind in ('wifi', '802-11-wireless'):
        mode = profile.get('wifi', 'mode', fallback=profile.get('802-11-wireless', 'mode', fallback='infrastructure'))
        return 'hotspot' if mode == 'ap' else 'wifi'
    if kind in ('ethernet', '802-3-ethernet'):
        return 'ethernet'
    return None


def profile_filename(name):
    # Match NetworkManager's /etc keyfile filename filter (extension optional).
    # The six ASCII-character suffix also excludes .nmmeta storage metadata.
    return (bool(name) and not name.startswith('.') and not name.endswith('~')
            and re.search(r'\.[A-Za-z0-9]{6}$', name) is None
            and not name.lower().endswith(('.pem', '.der'))
            and '.bmi30bak.' not in name)


def prepare_profiles(config_path=DEFAULT_CONFIG,
                     profiles_dir=Path('/etc/NetworkManager/system-connections'),
                     state_path=Path('/var/lib/bmi30-communication-recovery/profile-permissions.json')):
    try:
        allowed = permissions(read_config(config_path))
    except (OSError, ValueError):
        # An unreadable permission record must not silently enable every channel.
        LOG.error('Cannot read channel permissions; automatic network activation withheld')
        allowed = dict.fromkeys(CHANNELS, False)
    saved = read_config(state_path)
    changed = 0
    for path in sorted(Path(profiles_dir).iterdir()):
        if not profile_filename(path.name) or not path.is_file() or path.is_symlink():
            continue
        profile = configparser.ConfigParser(interpolation=None, strict=True)
        profile.optionxform = str
        profile.read_string(path.read_text(encoding='utf-8'))
        channel = profile_channel(profile)
        if channel is None:
            continue
        key = profile.get('connection', 'uuid', fallback=path.name)
        old = profile.get('connection', 'autoconnect', fallback=None)
        new = old
        if not allowed[channel]:
            if key not in saved:
                saved[key] = {'autoconnect': old, 'channel': channel}
                # Save the original before mutating the profile, including on power loss.
                atomic(state_path, json.dumps(saved))
            new = 'false'
        elif key in saved:
            new = saved[key]['autoconnect']
        if old != new:
            if new is None:
                profile.remove_option('connection', 'autoconnect')
            else:
                profile.set('connection', 'autoconnect', new)
            import io
            buffer = io.StringIO()
            profile.write(buffer, space_around_delimiters=False)
            atomic(path, buffer.getvalue())
            changed += 1
        if allowed[channel] and key in saved:
            del saved[key]
            atomic(state_path, json.dumps(saved))
    return changed


def run(*args, check=True, timeout=20):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if check and result.returncode:
        # Avoid returning arbitrary tool output, which can contain credentials.
        raise RuntimeError(f'{args[0]} failed ({result.returncode})')
    return result.returncode


def enable_channels():
    run('nmcli', 'connection', 'reload')
    for interface in ('wlan0', 'eth0'):
        run('nmcli', 'device', 'set', interface, 'managed', 'yes', 'autoconnect', 'yes')
    for unit in ('ssh', 'bmi30-hotspot-info', 'bmi30-hotspot', 'xrdp-sesman', 'xrdp'):
        loaded = subprocess.run(['systemctl', 'show', unit, '-p', 'LoadState', '--value'],
                                capture_output=True, text=True, timeout=5)
        if loaded.stdout.strip() == 'loaded':
            run('systemctl', 'enable', '--now', unit, timeout=40)
    # No cable or no visible router is acceptable: HotSpot remains a recovery path.
    for interface in ('wlan0', 'eth0'):
        run('nmcli', '--wait', '10', 'device', 'connect', interface, check=False, timeout=15)


def request_key(request):
    if not isinstance(request, dict) or request.get('version') != 1:
        return None
    if request.get('pending') is not True or request.get('durable') is not True:
        return None
    generation = request.get('generation')
    uid = request.get('stm32_uid96')
    if type(generation) is not int or not 1 <= generation <= 65535:
        return None
    if not isinstance(uid, str) or not re.fullmatch('[0-9A-Fa-f]{24}', uid):
        return None
    return uid.upper() + ':' + str(generation)


def recover(request, config_path=DEFAULT_CONFIG, apply=enable_channels,
            prepare=prepare_profiles, acknowledge=lambda generation: None):
    key = request_key(request)
    if key is None:
        return False
    config = read_config(config_path)
    recovery = config.get('communication_recovery', {})
    if recovery.get('last_applied') == key:
        # An MCU reset before its ACK was committed must not reopen channels
        # which the user has disabled after a previously completed recovery.
        acknowledge(request['generation'])
        return True
    config['channel_permissions'] = {**permissions(config), **dict.fromkeys(CHANNELS, True),
                                     'updated_at': int(time.time())}
    config['communication_recovery'] = {**recovery, 'in_progress': key}
    atomic(config_path, json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    prepare(config_path)
    apply()
    # Re-read so unrelated settings saved during channel startup are preserved.
    config = read_config(config_path)
    if not all(permissions(config).values()):
        raise RuntimeError('Permissions changed while recovery was being applied')
    recovery = config.setdefault('communication_recovery', {})
    recovery.update(last_applied=key, applied_at=int(time.time()))
    recovery.pop('in_progress', None)
    atomic(config_path, json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    acknowledge(request['generation'])
    LOG.info('Local STM32 K1 recovery applied and acknowledged')
    return True


def api(path, payload=None):
    request = urllib.request.Request('http://127.0.0.1:8765' + path,
        None if payload is None else json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:
        value = json.load(response)
    if not isinstance(value, dict) or not value.get('ok'):
        raise RuntimeError('Acquisition owner did not confirm request')
    return value


def daemon(config_path):
    while True:
        try:
            status = api('/api/status')
            if status.get('connection', {}).get('connected') and not status.get('stale'):
                recover(status.get('communication_recovery'), config_path,
                        acknowledge=lambda generation: api('/api/command', {
                            'action': 'communication_recovery_ack', 'params': {'generation': generation}}))
        except Exception as error:
            LOG.warning('Recovery not completed: %s', type(error).__name__)
        time.sleep(1)


def verify_local_identity(expected_host=None):
    """Use the CM5 hardware identity even before a cloned hostname is refreshed.

    Runtime units are portable between devices. An explicit expected identity
    remains available to deployment commands, without trusting a media-stored
    hostname or network configuration.
    """
    from network import hardware_identity
    identity = hardware_identity()
    if expected_host is not None and identity != expected_host:
        raise RuntimeError('Wrong device identity')
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--expected-host')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--allowed', choices=CHANNELS)
    mode.add_argument('--daemon', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    verify_local_identity(args.expected_host)
    if args.prepare:
        prepare_profiles(args.config)
    elif args.allowed:
        try:
            return 0 if permissions(read_config(args.config))[args.allowed] else 1
        except (OSError, ValueError):
            return 1
    else:
        import fcntl
        with open('/run/bmi30-communication-recovery.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            daemon(args.config)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
