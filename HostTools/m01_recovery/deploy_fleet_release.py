"""Install a portal-only bundle on audited active/offline roots, preserving acquisition."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from build_fleet_release import VERSION, WORK
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bmi30_network'))
from deploy import remote, SSH, CONFIG

HOSTS = {'M01':'BMI30-05AB38E6A','S10':'BMI30-D5EB8F26C','S11':'BMI30-D5507EDFC',
         'S12':'BMI30-27448D72B','S13':'BMI30-7AA5CED27'}

SCRIPT = r'''
import ast,datetime,hashlib,json,os,pathlib,shlex,shutil,ssl,stat,subprocess,sys,tarfile,time,urllib.request
p=json.load(sys.stdin);v=p['version'];host=p['host'];offline=p.get('device')
run=lambda *a:subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
assert run('hostname')==host
assert run('findmnt','-n','-o','SOURCE','/')==p['live_root']
before_pid=run('systemctl','show','bmi30-core','-p','MainPID','--value')
archive=pathlib.Path('/tmp')/('BMI30-RPi-'+v+'.tar.gz')
assert sha(archive)==p['archive_sha256']
root=pathlib.Path('/')
mounted=False
if offline:
 assert offline!=p['live_root']
 assert run('blkid','-s','UUID','-o','value',offline)==p['uuid']
 assert subprocess.run(['findmnt','-rn','-S',offline],stdout=subprocess.DEVNULL).returncode!=0
 root=pathlib.Path('/mnt/bmi30-portal-release-20261002');root.mkdir(exist_ok=False)
 run('mount',offline,str(root));mounted=True
try:
 d=root/'home/techaid/Documents';active=d/'host/bmi30_active_runtime/project'
 assert (root/'etc/hostname').read_text().strip()==host
 bundle=d/'host/bmi30_split_bundles'/v
 if not bundle.exists():
  with tarfile.open(archive) as tf:
   for entry in tf.getmembers():
    name=pathlib.PurePosixPath(entry.name)
    assert name.parts and name.parts[0]==v and not name.is_absolute() and '..' not in name.parts
    assert entry.isfile() or entry.isdir()
   tf.extractall(bundle.parent)
  st=d.stat()
  for f in [bundle,*bundle.rglob('*')]:os.chown(f,st.st_uid,st.st_gid,follow_symlinks=False)
 r=subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=bundle,capture_output=True,text=True)
 assert r.returncode==0,r.stderr
 project=bundle/'project'
 for name,digest in p['hashes'].items():assert sha(project/name)==digest,name
 # Every packaged acquisition Python module must match the active installation.
 components=[name for name in p['hashes'] if name.startswith('host/') and '.py' in pathlib.Path(name).name]
 for name in components:assert sha(active/name)==p['hashes'][name],'Acquisition differs: '+name
 installed=root/'usr/local/bin/bmi30-hotspot-info-server.py'
 portal_paths=[installed,active/'hotspot_info_server.py',d/'hotspot_info_server.py']
 for f in portal_paths:assert sha(f) in p['expected_portal'],'Unreviewed portal: '+str(f)
 env_path=d/'host/bmi30_split_active_version.env'
 env_before=env_path.read_bytes()
 assert 'BMI30_SPLIT_VERSION=2026-09-30-1650' in env_before.decode()
 settings=[d/'host'/name for name in ('bmi30_config.json','bmi30_sel.json','bmi30_relay.json')]
 before_settings={str(f):sha(f) for f in settings if f.exists()}
 updates={'BMI30_SPLIT_VERSION':v,'BMI30_SPLIT_BUNDLE_ID':v,
  'BMI30_SPLIT_LABEL':'Portal TLS client isolation and timeouts',
  'BMI30_SPLIT_SELECTED_BY':'portal-only-fleet-verified',
  'BMI30_SPLIT_SELECTED_AT':datetime.datetime.now().astimezone().isoformat(),
  'BMI30_BUNDLE_ORIGIN':'Based on 2026-09-30-1650; portal-only fix; acquisition bytes unchanged'}
 env=('\n'.join(key+'='+shlex.quote(updates[key]) if (key:=line.split('=',1)[0]) in updates else line
       for line in env_before.decode().splitlines())+'\n').encode()
 portal=(project/'hotspot_info_server.py').read_bytes();ast.parse(portal)
 release=(project/'host/bmi30_firmware_release.env').read_bytes()
 changes={f:portal for f in portal_paths}
 changes.update({active/'host/bmi30_firmware_release.env':release,d/'host/bmi30_firmware_release.env':release,env_path:env})
 backup=root/'var/backups/bmi30-portal-fleet'/time.strftime('%Y%m%d-%H%M%S');backup.mkdir(parents=True,mode=0o700)
 saved={}
 for i,f in enumerate(changes):
  assert f.resolve().is_relative_to(root.resolve()),str(f)
  saved[f]=(f.read_bytes(),f.stat());shutil.copy2(f,backup/str(i))
 (backup/'paths.json').write_text(json.dumps([str(f.relative_to(root)) for f in changes]))
 def atomic(f,data,st=None):
  st=st or f.stat();tmp=f.with_name(f.name+'.portal-release-new')
  tmp.write_bytes(data);os.chmod(tmp,stat.S_IMODE(st.st_mode));os.chown(tmp,st.st_uid,st.st_gid);tmp.replace(f)
 try:
  for f,data in changes.items():atomic(f,data)
  if not offline:
   subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)
   for attempt in range(20):
    try:
     with urllib.request.urlopen('https://127.0.0.1/',timeout=3,context=ssl._create_unverified_context()) as r:assert r.status==200
     with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3) as r:core=json.load(r)
     assert core['ok'] and not core['stale'] and core['connection']['connected'] and core['connection']['last_frame_age_s']<2
     break
    except Exception:
     if attempt==19:raise
     time.sleep(.3)
  assert before_pid==run('systemctl','show','bmi30-core','-p','MainPID','--value')
  assert before_settings=={str(f):sha(f) for f in settings if f.exists()},'Settings changed during deployment'
  for name in components:assert sha(active/name)==p['hashes'][name]
  for f in portal_paths:assert sha(f)==p['hashes']['hotspot_info_server.py']
 except Exception:
  for f,(data,st) in saved.items():atomic(f,data,st)
  if not offline:subprocess.run(['systemctl','restart','bmi30-hotspot-info'],check=True)
  raise
 run('sync','-f',str(root))
 print(json.dumps(dict(host=host,root=offline or p['live_root'],version=v,offline=bool(offline),
  boot_tested=False,core_pid=before_pid,settings_preserved=True,acquisition_files_verified=len(components),
  portal_sha256=p['hashes']['hotspot_info_server.py'],backup=str(backup.relative_to(root)))))
finally:
 if mounted:
  run('umount',str(root));root.rmdir()
'''

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('board',choices=HOSTS)
    parser.add_argument('--device')
    parser.add_argument('--uuid')
    args=parser.parse_args()
    if bool(args.device)!=bool(args.uuid):parser.error('Supply both --device and --uuid')
    host=HOSTS[args.board]
    info=json.loads((WORK/'release.json').read_text())
    audit=json.loads((WORK/(host+'-audit.json')).read_text())
    name='BMI30-RPi-'+VERSION+'.tar.gz'
    archive=WORK.parents[1]/'FirmwareReleases/RaspberryPi'/VERSION/name
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',str(archive),host+':/tmp/'+name],check=True)
    payload={'host':host,'version':VERSION,'live_root':audit['root'],'device':args.device,'uuid':args.uuid,
        'archive_sha256':info['sha256'],'hashes':info['hashes'],'expected_portal':[
        '62964cea7cb398d909080092b43c2a67a92bf2b852f44a1888ba6bc86d8aa2b2',
        '7eac1c256a4508f223f4f3947cee46f2817febb9b0feb6b1336449416171a501']}
    result=json.loads(remote(host,SCRIPT,payload,timeout=100))
    suffix='offline-'+Path(args.device).name if args.device else 'active'
    (WORK/(args.board+'-'+suffix+'-result.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
