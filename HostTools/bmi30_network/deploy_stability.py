"""Guarded network-only release on audited live and offline fleet roots."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import tarfile

from audit_stability import HOSTS, WORK
from deploy import FILES, remote

VERSION = '0.1.2'
RELEASE = '2026-10-05-network-02'
HERE = Path(__file__).resolve().parent
DEST = HERE.parents[1] / 'FirmwareReleases/RaspberryPi' / RELEASE
CHANGED = ('policy.py', 'service.py', 'group.py', 'test_network.py', 'test_group.py', 'test_stability.py')

INSTALL = r'''
import ast,base64,hashlib,json,os,pathlib,shutil,stat,subprocess,sys,time,urllib.request
p=json.load(sys.stdin)
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE,timeout=40).strip()
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def core():
 with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3) as r:return json.load(r)
assert run('hostname')==p['host']
assert run('findmnt','-n','-o','SOURCE','/')==p['live']
pid=run('systemctl','show','bmi30-core','-p','MainPID','--value')
assert pid!='0'
before_core=core();assert before_core['connection']['connected'] and not before_core.get('stale')
root=pathlib.Path('/');mounted=False
if p['device']!=p['live']:
 assert run('blkid','-s','UUID','-o','value',p['device'])==p['uuid']
 assert subprocess.run(['findmnt','-rn','-S',p['device']],stdout=subprocess.DEVNULL).returncode!=0
 root=pathlib.Path('/mnt/bmi30-network-stability-20261005');root.mkdir(exist_ok=False)
 try:run('mount',p['device'],str(root));mounted=True
 except Exception:root.rmdir();raise
try:
 assert (root/'etc/hostname').read_text().strip()==p['host']
 docs=root/'home/techaid/Documents';target=root/'opt/bmi30-network'
 assert 'BMI30_SPLIT_VERSION=2026-10-02-0948' in (docs/'host/bmi30_split_active_version.env').read_text()
 assert json.loads((root/'etc/bmi30-network/config.json').read_text())['id']==p['host']
 for name,digest in p['expected'].items():assert sha(root/name)==digest,'Unexpected file: '+name
 protected=[root/name for name in p['expected'] if not name.startswith('opt/bmi30-network/')]
 protected += [docs/'host'/n for n in ('bmi30_config.json','bmi30_sel.json','bmi30_relay.json') if (docs/'host'/n).exists()]
 protected += [root/'etc/bmi30-network/config.json']
 before={str(f):sha(f) for f in protected}
 changes={target/name:base64.b64decode(data) for name,data in p['files'].items()}
 changes[target/'release.json']=json.dumps(p['manifest'],indent=2).encode()+b'\n'
 for f,data in changes.items():
  if f.suffix=='.py':ast.parse(data)
  assert f.resolve().is_relative_to(root.resolve())
 backup=root/'var/backups/bmi30-network-stability'/time.strftime('%Y%m%d-%H%M%S');backup.mkdir(parents=True,mode=0o700)
 saved={}
 for f in changes:
  saved[f]=(f.read_bytes(),f.stat()) if f.exists() else None
  if f.exists():shutil.copy2(f,backup/f.name)
 (backup/'paths.json').write_text(json.dumps({str(f.relative_to(root)):bool(old) for f,old in saved.items()},indent=2))
 def atomic(f,data,st=None):
  st=st or (f.stat() if f.exists() else target.stat());tmp=f.with_name(f.name+'.stability-new')
  tmp.write_bytes(data);os.chmod(tmp,stat.S_IMODE(st.st_mode) if stat.S_ISREG(st.st_mode) else 0o644)
  os.chown(tmp,st.st_uid,st.st_gid);tmp.replace(f)
 try:
  for f,data in changes.items():atomic(f,data)
  test=subprocess.run(['python3','-m','unittest','-v','test_network','test_group','test_stability'],cwd=target,capture_output=True,text=True,timeout=40)
  assert test.returncode==0,test.stderr[-4000:]
  if not mounted:
   run('systemctl','restart','bmi30-network')
   for attempt in range(40):
    try:
     s=json.loads((root/'run/bmi30-network/status.json').read_text())
     g=json.loads((root/'run/bmi30-network/group.json').read_text())
     c=core()
     assert s['version']==p['manifest']['version'] and s['local']['id']==p['host']
     assert any(n['id']==p['host'] and n['available'] for n in g['nodes'])
     assert c['connection']['connected'] and not c.get('stale') and c['detector']['worker']['alive']
     break
    except Exception:
     if attempt==39:raise
     time.sleep(.5)
  assert pid==run('systemctl','show','bmi30-core','-p','MainPID','--value'),'Acquisition restarted'
  assert before=={str(f):sha(f) for f in protected},'Protected software or settings changed'
  for name,digest in p['manifest']['hashes'].items():assert sha(target/name)==digest,name
 except Exception:
  for f,old in saved.items():
   if old:atomic(f,old[0],old[1])
   elif f.exists():f.unlink()
  if not mounted:run('systemctl','restart','bmi30-network')
  raise
 run('sync','-f',str(root))
 print(json.dumps({'host':p['host'],'device':p['device'],'active':not mounted,'network_version':p['manifest']['version'],
  'app_bundle':'2026-10-02-0948','backup':str(backup.relative_to(root)),'core_pid':pid,'protected_files':len(before),
  'settings_preserved':True,'hashes':{n:sha(target/n) for n in p['manifest']['hashes']},'tests':test.stderr[-180:]}))
finally:
 if mounted:run('umount',str(root));root.rmdir()
'''


def build():
    DEST.mkdir(parents=True,exist_ok=False)
    hashes={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in [*FILES,'test_stability.py']}
    manifest={'version':VERSION,'release_id':RELEASE,'app_bundle':'2026-10-02-0948',
              'scope':'Network policy and version only; preserves per-root settings and acquisition',
              'hashes':hashes}
    (DEST/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive=DEST/('BMI30-Network-'+RELEASE+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tf:
        for name in hashes:tf.add(HERE/name,arcname=RELEASE+'/'+name)
        tf.add(DEST/'release.json',arcname=RELEASE+'/release.json')
    (DEST/'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(json.dumps({'release':RELEASE,'version':VERSION,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':len(hashes)}))


def install(board):
    inventory=json.loads((WORK/(board+'-inventory.json')).read_text())
    manifest=json.loads((DEST/'release.json').read_text())
    for name,digest in manifest['hashes'].items():
        assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==digest,'Release source changed: '+name
    results=[]
    # First update the inactive root, then restart only the live network daemon.
    for root in sorted(inventory['roots'],key=lambda r:r['active']):
        assert 'hashes' in root and root['hostname']==HOSTS[board]
        for name,digest in manifest['hashes'].items():
            if name not in CHANGED:
                assert root['hashes']['opt/bmi30-network/'+name]==digest,'Non-policy component differs: '+name
        payload={'host':HOSTS[board],'live':inventory['root'],'device':root['device'],'uuid':root.get('uuid'),
                 'expected':root['hashes'],'manifest':manifest,
                 'files':{name:base64.b64encode((HERE/name).read_bytes()).decode() for name in CHANGED}}
        result=json.loads(remote(HOSTS[board],INSTALL,payload,timeout=110))
        results.append(result)
        (WORK/(board+'-install.json')).write_text(json.dumps(results,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='hashes'}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['build','install'])
    parser.add_argument('--board',choices=HOSTS)
    args=parser.parse_args()
    if args.action=='build':build()
    else:
        for board in ([args.board] if args.board else HOSTS):install(board)
