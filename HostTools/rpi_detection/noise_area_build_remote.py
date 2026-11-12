"""Build a complete dated M02 candidate with the existing bundle utility."""
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

p = json.load(sys.stdin)
i = p['before']
v = p['version']
D = Path('/home/techaid/Documents')
A = D / 'host/bmi30_active_runtime/project'
W = D / '.tmp/noise-area-20261014'
W.mkdir(exist_ok=True)


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert run('hostname') == i['hostname'] == 'BMI30-E29805C7C'
assert Path('/proc/device-tree/serial-number').read_text().strip('\0\n') == i['serial']
assert run('findmnt', '-n', '-o', 'SOURCE', '/') == i['root']
assert (D / 'host/bmi30_split_active_version.env').read_text() == i['env']
assert all(sha(A / rel) == i['hashes'][name] for name, rel in i['paths'].items())
stage = W / ('stage-' + v)
assert not stage.exists()
shutil.copytree(A, stage, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'player_recordings', '.lgd-*'))
paths = dict(i['paths'], core='host/BMI30.001.py.' + v, engine='host/BMI30.200.py.' + v)
for name, raw in p['files'].items():
    data = base64.b64decode(raw)
    ast.parse(data)
    (stage / paths[name]).write_bytes(data)
stage_env = '\n'.join([
    'BMI30_CORE_PATH=' + paths['core'], 'BMI30_ENGINE_SOURCE=' + paths['engine'],
    'BMI30_GUI_PATH=' + paths['gui'], 'BMI30_PORTAL_PATH=' + paths['portal'], ''])
(stage / 'host/bmi30_split_active_version.env').write_text(stage_env)
bundle = D / 'host/bmi30_split_bundles' / v
assert not bundle.exists()
proc = subprocess.run(['bash', str(D / 'utilities/create_bmi30_split_bundle.sh'), '--id', v,
                       '--label', 'Adjustable noise measurement area',
                       '--origin', 'Peak-relative noise boundaries, 5-95 percent; preserve signal/noise coefficients',
                       '--source-project', str(stage), '--portal-source', 'hotspot_info_server.py',
                       '--without-player', '--for-server'], env=dict(os.environ, TZ='Europe/Prague'),
                      capture_output=True, text=True, timeout=140)
(W / ('build-' + v + '.log')).write_text(proc.stdout + '\n' + proc.stderr)
assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
for file in (stage / 'host').glob('bmi30_*.py'):
    shutil.copy2(file, bundle / 'project/host' / file.name)
(bundle / 'project/host/bmi30_split_active_version.env').write_text(stage_env)
for rel in ['system/usr/local/bin/bmi30-hotspot-info-server.py', 'system/home/techaid/Documents/hotspot_info_server.py']:
    file = bundle / rel
    if file.exists():
        file.write_bytes((stage / 'hotspot_info_server.py').read_bytes())
files = sorted(file for file in bundle.rglob('*') if file.is_file() and file.name != 'SHA256SUMS')
(bundle / 'SHA256SUMS').write_text(''.join(sha(file) + '  ' + file.relative_to(bundle).as_posix() + '\n' for file in files))
validation = run('bash', str(D / 'switch_bmi30_split_versions.sh'), '--validate', v)
archive = W / ('BMI30-RPi-' + v + '.tar.gz')
with tarfile.open(archive, 'w:gz') as t:
    t.add(bundle, arcname=v)
manifest = (bundle / 'project/host/bmi30_firmware_release.env').read_text()
firmware_version = next(line.split('=', 1)[1] for line in manifest.splitlines() if line.startswith('BMI30_FIRMWARE_VERSION='))
print(json.dumps({'version': v, 'firmware_version': firmware_version, 'paths': paths,
                  'archive': base64.b64encode(archive.read_bytes()).decode(), 'archive_sha256': sha(archive),
                  'hashes': {name: sha(bundle / 'project' / rel) for name, rel in paths.items()},
                  'bundle_checksums_sha256': sha(bundle / 'SHA256SUMS'), 'validation': validation}))
