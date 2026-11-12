"""Read active M02 files and settings before preparing a guarded deployment."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import urllib.request

D = Path('/home/techaid/Documents')
A = D / 'host/bmi30_active_runtime/project'


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()


assert run('hostname') == 'BMI30-E29805C7C'
serial = Path('/proc/device-tree/serial-number').read_text().strip('\0\n')
assert serial == '490df72e29805c7c'
raw = (D / 'host/bmi30_split_active_version.env').read_text()
env = {k: shlex.split(v)[0] if shlex.split(v) else '' for line in raw.splitlines()
       if line and not line.startswith('#') and '=' in line for k, v in [line.split('=', 1)]}
paths = {n: str((D / env[k]).relative_to(A)) for n, k in
         [('core', 'BMI30_CORE_PATH'), ('engine', 'BMI30_ENGINE_SOURCE'), ('gui', 'BMI30_GUI_PATH')]}
paths.update(portal='hotspot_info_server.py', helper='host/bmi30_averaging.py')
status = json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=5))
assert status['ok'] and not status['stale'] and status['connection']['connected']
assert not status['player_recorder']['recording'] and not status['player_recorder']['playback_active']
print(json.dumps({
    'hostname': run('hostname'), 'serial': serial, 'root': run('findmnt', '-n', '-o', 'SOURCE', '/'),
    'env': raw, 'paths': paths, 'status': status,
    'config': json.loads((D / 'host/bmi30_config.json').read_text()),
    'sources': {n: base64.b64encode((A / path).read_bytes()).decode() for n, path in paths.items()},
    'hashes': {n: hashlib.sha256((A / path).read_bytes()).hexdigest() for n, path in paths.items()},
    'installed_portal_sha256': hashlib.sha256(Path('/usr/local/bin/bmi30-hotspot-info-server.py').read_bytes()).hexdigest(),
    'agents': (D / 'AGENTS.md').read_text(),
    'packager': (D / 'utilities/create_bmi30_split_bundle.sh').read_text(),
    'switcher': (D / 'switch_bmi30_split_versions.sh').read_text(),
    'registry_head': (D / 'docs/BMI30_version_registry_google_sheet.csv').read_text().splitlines()[:3],
}))
