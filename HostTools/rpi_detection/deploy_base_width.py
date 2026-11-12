"""Install the reviewed base-width bundle through the existing trusted SSH aliases."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'bmi30_network'))
from deploy import remote
from capture_status import HOSTS

WORK = Path(__file__).resolve().parents[2]/'.tmp/base-width-20260930'

REMOTE = r'''
import ast,base64,hashlib,json,os,pathlib,shutil,subprocess,sys,tarfile,time,urllib.request
p=json.load(sys.stdin);v=p['version'];d=pathlib.Path('/home/techaid/Documents')
assert subprocess.check_output(['hostname'],text=True).strip().upper()==p['host']
task=d/'.tmp/base-width-20260930';task.mkdir(exist_ok=True)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def status():
 with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=4) as r:return json.load(r)
def atomic(path,data):
 st=path.stat();tmp=path.with_name(path.name+'.base-width-new');tmp.write_bytes(data)
 os.chmod(tmp,st.st_mode);os.chown(tmp,st.st_uid,st.st_gid);tmp.replace(path)
def activate(version):
 env=dict(os.environ,BMI30_RUNTIME_ONLY='1',BMI30_AGENT_BOOTSTRAPPED='1',BMI30_ACTIVATE_PRESERVE_CONFIG='1',BMI30_SPLIT_SELECTED_BY_OVERRIDE='base-width-user-authorized')
 r=subprocess.run(['bash',str(d/'switch_bmi30_split_versions.sh'),'--activate',version],env=env,capture_output=True,text=True)
 (task/('activate-'+version+'.log')).write_text(r.stdout+r.stderr)
 if r.returncode:raise RuntimeError(r.stdout+r.stderr)
before=status();assert before.get('ok') and not before.get('stale')
assert before['connection']['connected'] and before['connection']['last_frame_age_s']<2
rec=before.get('player_recorder',{})
assert not rec.get('recording') and not rec.get('playback_active'),'Recording or playback is active'
env_file=d/'host/bmi30_split_active_version.env'
assert 'BMI30_SPLIT_VERSION=2026-09-30-1449' in env_file.read_text(),'Active version changed'
active=d/'host/bmi30_active_runtime/project'
for name,expected in p['expected'].items():assert sha(active/name)==expected,'Source changed: '+name
assert sha(d/'switch_bmi30_split_versions.sh')==p['switcher_sha256']
archive=task/('BMI30-RPi-'+v+'.tar.gz');data=base64.b64decode(p['archive']);assert hashlib.sha256(data).hexdigest()==p['archive_sha256'];archive.write_bytes(data)
bundle=d/'host/bmi30_split_bundles'/v
if not bundle.exists():
 with tarfile.open(archive) as t:
  for member in t.getmembers():
   name=pathlib.PurePosixPath(member.name)
   assert not name.is_absolute() and '..' not in name.parts and name.parts[0]==v and (member.isfile() or member.isdir())
  t.extractall(bundle.parent)
for name,expected in p['hashes'].items():assert sha(bundle/'project'/name)==expected,name
subprocess.run(['bash',str(d/'switch_bmi30_split_versions.sh'),'--validate',v],check=True,stdout=subprocess.DEVNULL)
backup=task/('before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(exist_ok=False)
names=['host/bmi30_config.json','host/bmi30_sel.json','host/bmi30_relay.json','host/bmi30_split_active_version.env','host/bmi30_firmware_release.env','hotspot_info_server.py']
original={}
for name in names:
 path=d/name
 if path.exists():original[name]=path.read_bytes();shutil.copy2(path,backup/path.name)
(backup/'status.json').write_text(json.dumps(before))
config=d/'host/bmi30_config.json'
try:
 # Read settings after stopping their writer; preserve every unrelated value.
 subprocess.run(['systemctl','stop','bmi30-core'],check=True)
 original['host/bmi30_config.json']=config.read_bytes();(backup/'bmi30_config.json').write_bytes(config.read_bytes())
 old_config=json.loads(config.read_text());new_config=dict(old_config)
 new_config.update(base_width_enabled=True,base_width_max=24)
 new_config['tag_detection']={**old_config.get('tag_detection',{}),'base_width_enabled':True,'base_width_max':24}
 atomic(config,(json.dumps(new_config,ensure_ascii=False,indent=2)+'\n').encode())
 activate(v)
 deadline=time.monotonic()+40
 while time.monotonic()<deadline:
  try:
   after=status()
   if after.get('ok') and after['mode']['selected']==before['mode']['selected'] and after['connection']['connected'] and after['connection']['last_frame_age_s']<1 and after['detector']['settings'].get('base_width_enabled') and after['detector']['settings'].get('base_width_max')==24:break
  except Exception:pass
  time.sleep(.5)
 else:raise RuntimeError('New detector failed startup validation')
 assert after['mode']['selected']==before['mode']['selected']
 assert v in after['service']['engine_source']
 for name,expected in p['hashes'].items():assert sha(active/name)==expected,name
 actual=json.loads(config.read_text());assert actual==new_config,'Unrelated config changed'
 for name in ('host/bmi30_sel.json','host/bmi30_relay.json'):
  if name in original:
   if name.endswith('sel.json'):assert json.loads(original[name])['sel']==json.loads((d/name).read_text())['sel']
   else:assert original[name]==(d/name).read_bytes(),name
 assert sha(pathlib.Path('/usr/local/bin/bmi30-hotspot-info-server.py'))==p['hashes']['hotspot_info_server.py']
 # Keep the conventional source path aligned with the active installed portal.
 atomic(d/'hotspot_info_server.py',(active/'hotspot_info_server.py').read_bytes())
 with urllib.request.urlopen('http://127.0.0.1:8765/api/frame?max_points=200',timeout=4) as r:frame=json.load(r)
 assert frame.get('available') and any((frame.get(f'prod_peak{ch}') or {}).get('base_width') for ch in (0,1))
 with urllib.request.urlopen('http://127.0.0.1:8765/',timeout=4) as r:html=r.read().decode()
 assert 'function drawBaseWidthGate(' in html
except Exception:
 try:(task/'failed-status.json').write_text(json.dumps(status(),indent=2))
 except Exception:pass
 subprocess.run(['systemctl','stop','bmi30-core'],check=False)
 for name in ('host/bmi30_config.json','host/bmi30_sel.json','host/bmi30_relay.json'):
  if name in original:atomic(d/name,original[name])
 activate('2026-09-30-1449')
 raise
result={'host':p['host'],'version':v,'hashes':p['hashes'],'settings':{'base_width_enabled':True,'base_width_max':24},'other_settings_preserved':True,'mode':after['mode']['selected'],'connection':after['connection'],'fps':after.get('fps'),'base_width':[after['detector'].get(f'base_width{ch}') for ch in (0,1)],'root':subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip()}
(task/'deployment.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', choices=HOSTS)
    args = parser.parse_args()
    info = json.loads((WORK/'bundle.json').read_text())
    version = info['version']
    archive = WORK.parents[1]/'FirmwareReleases/RaspberryPi'/version/('BMI30-RPi-'+version+'.tar.gz')
    candidate = WORK/'release-candidate'
    hashes = {p.relative_to(candidate).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in candidate.rglob('*') if p.is_file()}
    source = WORK/'source/project'
    expected = {str(k).replace(version,'2026-09-30-1135'): hashlib.sha256((source/str(k).replace(version,'2026-09-30-1135')).read_bytes()).hexdigest() for k in hashes}
    payload = dict(version=version,host=HOSTS[args.board],hashes=hashes,expected=expected,
                   switcher_sha256=hashlib.sha256((WORK/'tools/switch_bmi30_split_versions.sh').read_bytes()).hexdigest(),
                   archive=base64.b64encode(archive.read_bytes()).decode(),archive_sha256=info['sha256'])
    result=json.loads(remote(HOSTS[args.board],REMOTE,payload,timeout=110))
    (WORK/(args.board+'-deployment.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':main()
