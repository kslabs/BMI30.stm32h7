"""Guarded installation of a complete bundle, preserving persistent settings."""
import ast
import base64
import hashlib
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

p = json.load(sys.stdin)
d = pathlib.Path('/home/techaid/Documents')
active = d/'host/bmi30_active_runtime/project'
work = d/'.tmp/rpi-averaging-20261013'
work.mkdir(exist_ok=True)
version = p['version']
assert subprocess.check_output(['hostname'], text=True).strip() == p['hostname']
assert subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip() == p['root']


def api():
    return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=5))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path, raw):
    previous = path.stat() if path.exists() else None
    temp = path.with_name(path.name+'.averaging-new')
    temp.write_bytes(raw)
    temp.chmod(previous.st_mode & 0o777 if previous else 0o644)
    if previous:os.chown(temp, previous.st_uid, previous.st_gid)
    else:os.chown(temp, active.stat().st_uid, active.stat().st_gid)
    temp.replace(path)


before = api()
assert before['ok'] and not before.get('stale') and before['connection']['connected']
assert before['sync']['code'] == p['board']
assert not before.get('player_recorder',{}).get('recording')
assert not before.get('player_recorder',{}).get('playback_active')
env_path = d/'host/bmi30_split_active_version.env'
env_raw = env_path.read_text()
assert 'BMI30_SPLIT_VERSION='+p.get('base_version','2026-10-12-1730') in env_raw
paths = {key:pathlib.Path(value) for key,value in p['paths'].items()}
for name in ('core','engine','portal'):
    path = active/paths[name] if name in paths else active/'hotspot_info_server.py'
    assert sha(path) == p['expected'][name], 'Source changed: '+name
assert sha(pathlib.Path('/usr/local/bin/bmi30-hotspot-info-server.py')) == p['expected']['portal']
stage = work/('stage-'+version)
if not stage.exists():
    shutil.copytree(active, stage, symlinks=True, ignore=shutil.ignore_patterns('__pycache__','*.pyc','player_recordings','.lgd-*'))
for name in ('core','engine','portal','helper'):
    raw = base64.b64decode(p['files'][name])
    ast.parse(raw)
    relative = paths[name] if name in paths else pathlib.Path('host/bmi30_averaging.py' if name=='helper' else 'hotspot_info_server.py')
    (stage/relative).write_bytes(raw)
(stage/'host/bmi30_split_active_version.env').write_text('\n'.join([
    'BMI30_CORE_PATH='+str(paths['core']), 'BMI30_ENGINE_SOURCE='+str(paths['engine']),
    'BMI30_GUI_PATH=host/BMI30.GUI.001.py.2026-09-30-1650', 'BMI30_PORTAL_PATH=hotspot_info_server.py','']))
bundle = d/'host/bmi30_split_bundles'/version
label = 'STM32 and Raspberry packet averaging'
if not bundle.exists():
    result = subprocess.run(['bash',str(d/'utilities/create_bmi30_split_bundle.sh'),'--id',version,
        '--label',label,'--origin','Per-channel per-phase Raspberry averaging 1-8; centered oscilloscope readout',
        '--source-project',str(stage),'--portal-source','hotspot_info_server.py','--without-player','--for-server'],
        capture_output=True,text=True,timeout=150)
    (work/(version+'-create.log')).write_text(result.stdout+'\n'+result.stderr)
    assert result.returncode == 0, result.stdout[-2000:]+result.stderr[-2000:]
for relative in (paths['core'],paths['engine'],pathlib.Path('hotspot_info_server.py')):
    assert sha(stage/relative)==sha(bundle/'project'/relative),'Existing candidate differs: '+str(relative)
# The stock packager has a fixed helper list. Include all current helper modules,
# including the existing manual RX policy, so later bundle activation is complete.
for file in (stage/'host').glob('bmi30_*.py'):
    shutil.copy2(file,bundle/'project/host'/file.name)
for relative in ('system/usr/local/bin/bmi30-hotspot-info-server.py','system/home/techaid/Documents/hotspot_info_server.py'):
    target = bundle/relative
    if target.exists():target.write_bytes((stage/'hotspot_info_server.py').read_bytes())
files = sorted(f for f in bundle.rglob('*') if f.is_file() and f.name != 'SHA256SUMS')
(bundle/'SHA256SUMS').write_text(''.join(sha(f)+'  '+f.relative_to(bundle).as_posix()+'\n' for f in files))
result = subprocess.run(['bash',str(d/'switch_bmi30_split_versions.sh'),'--validate',version],capture_output=True,text=True,timeout=90)
assert result.returncode == 0,result.stdout[-2000:]+result.stderr[-2000:]
for name in ('core','engine'):
    assert sha(active/paths[name]) == p['expected'][name], 'Source changed during preparation'
targets = {active/paths['core']:bundle/'project'/paths['core'],
           active/paths['engine']:bundle/'project'/paths['engine'],
           active/'host/bmi30_averaging.py':bundle/'project/host/bmi30_averaging.py',
           active/'hotspot_info_server.py':bundle/'project/hotspot_info_server.py',
           d/'hotspot_info_server.py':bundle/'project/hotspot_info_server.py',
           pathlib.Path('/usr/local/bin/bmi30-hotspot-info-server.py'):bundle/'project/hotspot_info_server.py',
           active/'host/bmi30_firmware_release.env':bundle/'project/host/bmi30_firmware_release.env',
           d/'host/bmi30_firmware_release.env':bundle/'project/host/bmi30_firmware_release.env'}
backup = work/('backup-'+version+('-retry-'+time.strftime('%H%M%S') if (work/('backup-'+version)).exists() else ''))
backup.mkdir(exist_ok=False)
snapshot = {f:f.read_bytes() if f.exists() else None for f in [*targets,env_path]}
for index,(file,raw) in enumerate(snapshot.items()):
    if raw is not None:(backup/(str(index)+'-'+file.name)).write_bytes(raw)
(backup/'paths.json').write_text(json.dumps([str(f) for f in snapshot],indent=2))
subprocess.run(['systemctl','stop','bmi30-core','bmi30-hotspot-info'],check=True)
persistent = {d/'host'/name:(d/'host'/name).read_bytes() for name in ('bmi30_config.json','bmi30_sel.json','bmi30_relay.json') if (d/'host'/name).exists()}
for file,raw in persistent.items():(backup/file.name).write_bytes(raw)
try:
    for target,source in targets.items():atomic(target,source.read_bytes())
    updates = {'BMI30_SPLIT_VERSION':version,'BMI30_SPLIT_BUNDLE_ID':version,'BMI30_SPLIT_LABEL':label,
               'BMI30_SPLIT_SELECTED_BY':'validated-runtime-averaging-update',
               'BMI30_SPLIT_SELECTED_AT':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
               'BMI30_BUNDLE_ORIGIN':'Raspberry averaging 1-8; centered oscilloscope readout'}
    atomic(env_path, ('\n'.join(line.split('=',1)[0]+'='+shlex.quote(updates[line.split('=',1)[0]])
             if line.split('=',1)[0] in updates else line for line in env_raw.splitlines())+'\n').encode())
    subprocess.run(['systemctl','start','bmi30-core','bmi30-hotspot-info'],check=True)
    for i in range(35):
        try:
            after = api()
            if after.get('ok') and not after.get('stale') and after['connection']['connected'] and after['connection']['last_frame_age_s'] < 1 and after['mode'].get('rpi_avg_n') == 1 and after['mode']['stream_mode'] == before['mode']['stream_mode'] and after['mode']['base_buf_len'] == before['mode']['base_buf_len'] and after['detector']['worker']['alive'] and after['detector']['worker']['results'] > 0:
                break
        except Exception:pass
        time.sleep(.5)
    else:raise RuntimeError('New core did not become healthy')
    assert after['mode']['selected'] == before['mode']['selected']
    assert after['mode']['avg_n'] == before['mode']['avg_n']
    for file,raw in persistent.items():
        if file.name=='bmi30_sel.json':
            assert json.loads(file.read_bytes())['sel']==json.loads(raw)['sel'], 'Selected mode changed'
        else:assert file.read_bytes() == raw, 'Persistent settings changed: '+str(file)
    for target,source in targets.items():assert sha(target) == sha(source)
    html = urllib.request.urlopen('http://127.0.0.1:8765/',timeout=5).read().decode()
    assert 'id="avgReadout"' in html and 'id="avgSel"' not in html and 'sendAvg' not in html
    portal=urllib.request.urlopen('http://127.0.0.1/api/status',timeout=8)
except Exception:
    subprocess.run(['systemctl','stop','bmi30-core','bmi30-hotspot-info'],check=True)
    for file,raw in snapshot.items():
        if raw is None:file.unlink(missing_ok=True)
        else:atomic(file,raw)
    for file,raw in persistent.items():atomic(file,raw)
    subprocess.run(['systemctl','start','bmi30-core','bmi30-hotspot-info'],check=True)
    raise
archive=work/('BMI30-RPi-'+version+'.tar.gz')
with tarfile.open(archive,'w:gz') as tar:tar.add(bundle,arcname=version)
print(json.dumps({'board':p['board'],'version':version,'backup':str(backup),'root':p['root'],
                  'persistent_settings_unchanged':True,'mode':after['mode'],'connection':after['connection'],
                  'worker':after['detector']['worker'],'archive_sha256':sha(archive),
                  'archive':base64.b64encode(archive.read_bytes()).decode()}))
