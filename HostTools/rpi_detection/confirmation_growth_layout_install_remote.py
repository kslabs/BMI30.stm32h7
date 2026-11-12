"""Install the inline portal controls without restarting Core or changing settings."""
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
i, r = p['before'],p['release']
D = Path('/home/techaid/Documents')
A = D/'host/bmi30_active_runtime/project'
B = D/'host/bmi30_split_bundles'/r['version']
BACKUP = D/'.tmp/confirmation-growth-20261021'/('backup-'+r['version'])


def run(*args):
    return subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()


def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def api():
    return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=5))


def atomic(file,data):
    stat = file.stat()
    temp = file.with_name(file.name+'.growth-layout-tmp')
    temp.write_bytes(data)
    os.chown(temp,stat.st_uid,stat.st_gid)
    os.chmod(temp,stat.st_mode & 0o777)
    temp.replace(file)


assert run('hostname') == i['hostname'] == 'BMI30-E29805C7C'
assert Path('/proc/device-tree/serial-number').read_text().strip('\0\n') == i['serial'] == '490df72e29805c7c'
assert run('findmnt','-n','-o','SOURCE','/') == i['root']
assert (D/'host/bmi30_split_active_version.env').read_text() == i['env']
assert all(sha(A/path) == i['hashes'][name] for name,path in i['paths'].items())
assert sha(Path('/usr/local/bin/bmi30-hotspot-info-server.py')) == i['installed_portal_sha256']
assert sha(B/'SHA256SUMS') == r['bundle_checksums_sha256']
assert all(r['hashes'][key] == i['hashes'][key] for key in ('core','engine','gui','helper','dual'))
run('bash',str(D/'switch_bmi30_split_versions.sh'),'--validate',r['version'])
core_pid = run('systemctl','show','-p','MainPID','--value','bmi30-core')
assert int(core_pid) > 0
before = api()
assert before['ok'] and not before['stale'] and before['connection']['connected']
config_hash = sha(D/'host/bmi30_config.json')
updates = {'BMI30_SPLIT_VERSION':r['version'],'BMI30_SPLIT_BUNDLE_ID':r['version'],
    'BMI30_SPLIT_LABEL':'Optional confirmation peak growth with inline controls',
    'BMI30_SPLIT_SELECTED_BY':'M02-portal-layout-only-core-preserved',
    'BMI30_SPLIT_SELECTED_AT':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
    'BMI30_BUNDLE_ORIGIN':'Confirmation peak growth enable checkbox and coefficient on the same row; Core and settings unchanged'}
active_env = ('\n'.join(line.split('=',1)[0]+'='+shlex.quote(updates[line.split('=',1)[0]])
    if line.split('=',1)[0] in updates else line for line in i['env'].splitlines())+'\n').encode()
portal = (B/'project/hotspot_info_server.py').read_bytes()
manifest = (B/'project/host/bmi30_firmware_release.env').read_bytes()
targets = {A/'hotspot_info_server.py':portal,D/'hotspot_info_server.py':portal,
    Path('/usr/local/bin/bmi30-hotspot-info-server.py'):portal,
    A/'host/bmi30_firmware_release.env':manifest,D/'host/bmi30_firmware_release.env':manifest,
    D/'host/bmi30_split_active_version.env':active_env,
    A/'host/bmi30_split_active_version.env':(B/'project/host/bmi30_split_active_version.env').read_bytes()}
BACKUP.mkdir(mode=0o700,exist_ok=False)
snapshots = {}
committed = False
try:
    for file in targets:
        assert file.is_file() and not file.is_symlink()
        snapshots[file] = file.read_bytes()
        destination = BACKUP/file.relative_to('/')
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(file,destination)
    run('systemctl','stop','bmi30-hotspot-info')
    for file,data in targets.items():
        atomic(file,data)
    run('systemctl','start','bmi30-hotspot-info')
    for attempt in range(50):
        if run('systemctl','is-active','bmi30-hotspot-info') == 'active':
            break
        time.sleep(.1)
    after = api()
    assert run('systemctl','show','-p','MainPID','--value','bmi30-core') == core_pid
    assert after['ok'] and not after['stale'] and after['connection']['connected']
    assert after['detector']['settings'] == before['detector']['settings']
    assert all(after['mode'][key] == before['mode'][key] for key in ('selected','avg_n','rpi_avg_n','stream_mode','base_buf_len','freq_hz'))
    assert sha(D/'host/bmi30_config.json') == config_hash
    assert all(file.read_bytes() == data for file,data in targets.items())
    assert all(sha(A/path) == r['hashes'][name] for name,path in r['paths'].items())
    (BACKUP/'startup-status.json').write_text(json.dumps({'before':before,'after':after},indent=2))
    result = {'version':r['version'],'firmware_version':r['firmware_version'], 'active_root':i['root'],
        'backup':str(BACKUP),'settings_preserved':True,'core_pid_preserved':True,'core_pid':int(core_pid),
        'updated_scope':'M02 active root; portal layout only','mode':after['mode']}
    (BACKUP/'deployment.json').write_text(json.dumps(result,indent=2))
    committed = True
    print(json.dumps(result))
finally:
    if not committed:
        run('systemctl','stop','bmi30-hotspot-info')
        for file,data in snapshots.items():
            atomic(file,data)
        run('systemctl','start','bmi30-hotspot-info')
