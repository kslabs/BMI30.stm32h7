"""Activate the validated M02 code, preserving settings and a rollback copy."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request

p = json.load(sys.stdin)
i, r = p['before'], p['release']
D = Path('/home/techaid/Documents')
A = D / 'host/bmi30_active_runtime/project'
B = D / 'host/bmi30_split_bundles' / r['version']
BACKUP = D / '.tmp/noise-area-20261014' / ('backup-' + r['version'])
if BACKUP.exists():
    BACKUP = BACKUP.with_name(BACKUP.name + '-retry-' + str(time.time_ns()))


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()


def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def api():
    return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=5))


def atomic(file, data, reference=None):
    stat = (file if file.exists() else reference).stat()
    temp = file.with_name(file.name + '.noise-area-tmp')
    temp.write_bytes(data)
    os.chown(temp, stat.st_uid, stat.st_gid)
    os.chmod(temp, stat.st_mode & 0o777)
    temp.replace(file)


assert run('hostname') == i['hostname'] == 'BMI30-E29805C7C'
assert Path('/proc/device-tree/serial-number').read_text().strip('\0\n') == i['serial']
assert run('findmnt', '-n', '-o', 'SOURCE', '/') == i['root'] == '/dev/sda2'
assert (D / 'host/bmi30_split_active_version.env').read_text() == i['env']
assert all(sha(A / path) == i['hashes'][name] for name, path in i['paths'].items())
assert sha(Path('/usr/local/bin/bmi30-hotspot-info-server.py')) == i['installed_portal_sha256']
assert sha(B / 'SHA256SUMS') == r['bundle_checksums_sha256']
validation = run('bash', str(D / 'switch_bmi30_split_versions.sh'), '--validate', r['version'])
before = api()
assert before['ok'] and not before['stale'] and before['connection']['connected']
assert not before['player_recorder']['recording'] and not before['player_recorder']['playback_active']

updates = {
    'BMI30_SPLIT_VERSION': r['version'], 'BMI30_SPLIT_BUNDLE_ID': r['version'],
    'BMI30_SPLIT_LABEL': 'Adjustable noise measurement area',
    'BMI30_SPLIT_SELECTED_BY': 'validated-M02-noise-area-update',
    'BMI30_SPLIT_SELECTED_AT': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'BMI30_BUNDLE_ORIGIN': 'Peak-relative noise boundaries; preserve signal/noise coefficients',
    'BMI30_CORE_PATH': str((A / r['paths']['core']).relative_to(D)),
    'BMI30_ENGINE_SOURCE': str(A / r['paths']['engine']),
}
active_env = ('\n'.join(line.split('=', 1)[0] + '=' + shlex.quote(updates[line.split('=', 1)[0]])
                         if line.split('=', 1)[0] in updates else line for line in i['env'].splitlines()) + '\n').encode()
portal = (B / 'project/hotspot_info_server.py').read_bytes()
manifest = (B / 'project/host/bmi30_firmware_release.env').read_bytes()
targets = {
    A / r['paths']['core']: (B / 'project' / r['paths']['core']).read_bytes(),
    A / r['paths']['engine']: (B / 'project' / r['paths']['engine']).read_bytes(),
    A / 'hotspot_info_server.py': portal, D / 'hotspot_info_server.py': portal,
    Path('/usr/local/bin/bmi30-hotspot-info-server.py'): portal,
    A / 'host/bmi30_firmware_release.env': manifest, D / 'host/bmi30_firmware_release.env': manifest,
    D / 'host/bmi30_split_active_version.env': active_env,
    A / 'host/bmi30_split_active_version.env': (B / 'project/host/bmi30_split_active_version.env').read_bytes(),
}
BACKUP.mkdir(mode=0o700, exist_ok=False)
snapshots, settings, paused, committed = {}, {}, False, False
try:
    run('systemctl', 'stop', 'bmi30-core', 'bmi30-hotspot-info')
    paused = True
    for folder in [D / 'host', Path('/usr/local/bin/host'), Path('/etc/bmi30')]:
        if folder.exists():
            for file in folder.iterdir():
                if file.is_file() and file.suffix in ('.json', '.npz', '.npy'):
                    resolved = file.resolve()
                    settings[resolved] = resolved.read_bytes()
    for file, data in {**{f: f.read_bytes() for f in targets if f.exists()}, **settings}.items():
        dest = BACKUP / file.relative_to('/')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, dest)
    for file, data in targets.items():
        assert not file.is_symlink()
        snapshots[file] = file.read_bytes() if file.exists() else None
        atomic(file, data, reference=A / i['paths']['core'])
    run('sync', '-f', str(D))
    run('systemctl', 'start', 'bmi30-core', 'bmi30-hotspot-info')
    paused = False
    mode_keys = ['selected', 'avg_n', 'rpi_avg_n', 'stream_mode', 'base_buf_len', 'freq_hz']
    for attempt in range(140):
        try:
            after = api()
            worker = after['detector']['worker']
            if (after['ok'] and not after['stale'] and after['connection']['connected']
                    and after['connection']['last_frame_age_s'] < 2 and worker['alive'] and worker['results'] > 0
                    and all(after['mode'][key] == before['mode'][key] for key in mode_keys)):
                break
        except Exception:
            pass
        time.sleep(.4)
    else:
        raise RuntimeError('Updated core failed its startup health check')
    (BACKUP / 'startup-status.json').write_text(json.dumps({'before': before, 'after': after}, indent=2))
    for key in mode_keys:
        assert after['mode'][key] == before['mode'][key], key
    for channel in ['upper', 'lower']:
        for key in ['enabled', 'confirm_count', 'threshold', 'threshold_high', 'auto_threshold', 'noise_up', 'noise_down', 'noise_unit']:
            assert after['detector']['channels'][channel][key] == before['detector']['channels'][channel][key], (channel, key)
    assert after['detector']['settings']['noise_area_percent'] == 50
    assert all(file.read_bytes() == data for file, data in targets.items())
    for file, data in settings.items():
        if file.name == 'bmi30_sel.json':
            assert json.loads(file.read_bytes())['sel'] == json.loads(data)['sel']
        else:
            assert file.read_bytes() == data, str(file)
    assert all(sha(A / path) == r['hashes'][name] for name, path in r['paths'].items())
    committed = True
    record = {'version': r['version'], 'firmware_version': r['firmware_version'], 'active_root': i['root'],
              'backup': str(BACKUP), 'settings_preserved': True, 'mode': after['mode'],
              'worker': after['detector']['worker'], 'noise_area_percent': 50, 'validation': validation,
              'updated_scope': 'M02 active USB only'}
    (BACKUP / 'deployment.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record))
finally:
    if snapshots and not committed:
        run('systemctl', 'stop', 'bmi30-core', 'bmi30-hotspot-info')
        for file, data in snapshots.items():
            if data is not None:
                atomic(file, data)
        for file, data in settings.items():
            atomic(file, data)
        run('systemctl', 'start', 'bmi30-core', 'bmi30-hotspot-info')
    elif paused:
        run('systemctl', 'start', 'bmi30-core', 'bmi30-hotspot-info')
