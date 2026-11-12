"""Apply the tested portal fix on M01 only, retaining exact backup copies."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
from patch_portal import patch

def run(*args):
    return subprocess.check_output(args, text=True).strip()

assert run('hostname') == 'BMI30-05AB38E6A'
expected = '62964cea7cb398d909080092b43c2a67a92bf2b852f44a1888ba6bc86d8aa2b2'
paths = [Path('/usr/local/bin/bmi30-hotspot-info-server.py'),
         Path('/home/techaid/Documents/host/bmi30_active_runtime/project/hotspot_info_server.py'),
         Path('/home/techaid/Documents/hotspot_info_server.py')]
originals = {p: p.read_bytes() for p in paths}
assert all(hashlib.sha256(b).hexdigest() == expected for b in originals.values()), 'Portal changed since inspection'
candidate = patch(originals[paths[0]].decode()).encode()
ast.parse(candidate)
before = run('systemctl', 'show', 'bmi30-core', '-p', 'MainPID', '--value')
backup = Path('/var/backups/bmi30-m01-recovery') / time.strftime('%Y%m%d-%H%M%S')
backup.mkdir(parents=True, mode=0o700)
for n, path in enumerate(paths):
    shutil.copy2(path, backup / str(n))
(backup / 'paths.json').write_text(json.dumps([str(p) for p in paths]))
def replace(path, data):
    tmp = path.with_name(path.name + '.m01-recovery-new')
    shutil.copy2(path, tmp)
    tmp.write_bytes(data)
    st = path.stat()
    import os
    os.chown(tmp, st.st_uid, st.st_gid)
    tmp.replace(path)
try:
    for path in paths:
        replace(path, candidate)
    subprocess.run(['systemctl', 'restart', 'bmi30-hotspot-info'], check=True)
    for attempt in range(20):
        try:
            with urllib.request.urlopen('http://127.0.0.1/', timeout=2) as response:
                assert response.status == 200
            break
        except Exception:
            if attempt == 19:
                raise
            time.sleep(0.5)
    assert run('systemctl', 'show', 'bmi30-core', '-p', 'MainPID', '--value') == before
except Exception:
    for path, data in originals.items():
        replace(path, data)
    subprocess.run(['systemctl', 'restart', 'bmi30-hotspot-info'], check=True)
    raise
print(json.dumps({'backup': str(backup), 'portal_sha256': hashlib.sha256(candidate).hexdigest(),
                  'core_pid_unchanged': before, 'paths': [str(p) for p in paths]}))
