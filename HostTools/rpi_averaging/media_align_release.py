"""Copy the original S07 release identity to M02 without regenerating a release."""
import base64,hashlib,json,tarfile
from media_sync import WORK,ROOT,call
DIR=ROOT/'FirmwareReleases/RaspberryPi/2026-10-13-1534';V='2026-10-13-1534'
names=['manifest.env','SHA256SUMS','project/host/bmi30_firmware_release.env']
with tarfile.open(DIR/('BMI30-RPi-'+V+'.tar.gz')) as t:
    files={n:base64.b64encode(t.extractfile(V+'/'+n).read()).decode() for n in names}
with tarfile.open(DIR/('BMI30-RPi-'+V+'-M02.tar.gz')) as t:
    previous={n:hashlib.sha256(t.extractfile(V+'/'+n).read()).hexdigest() for n in names}
REMOTE=r'''
import base64,hashlib,json,os,pathlib,shutil,subprocess,sys,time
p=json.load(sys.stdin);i=p['inventory'];P=pathlib.Path;D=P('/home/techaid/Documents');M=P('/mnt/bmi30-release-identity-usb')
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
assert run('hostname')==i['hostname']=='BMI30-E29805C7C'
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/mmcblk0p2'
assert P('/sys/block/mmcblk0/device/cid').read_text()==i['emmc_cid']
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==next(d['serial'] for d in i['disks']['blockdevices'] if d['name']=='sda')
assert not subprocess.run(['findmnt','-rn','-S','/dev/sda2'],capture_output=True,text=True).stdout
state=json.loads(P('/run/bmi30-media-sync.json').read_text());assert state['stage']=='complete'
backup=P(state['backup'])/'release-identity';backup.mkdir(exist_ok=False)
M.mkdir(exist_ok=False);out={}
try:
 run('mount','/dev/sda2',str(M))
 for label,root in [('emmc',P('/')),('usb',M)]:
  d=root/'home/techaid/Documents';b=d/'host/bmi30_split_bundles'/p['version']
  configs={f:sha(f) for f in (d/'host').glob('*.json') if f.is_file() and not f.is_symlink()}
  targets={b/n:base64.b64decode(p['files'][n]) for n in p['files']}
  release=base64.b64decode(p['files']['project/host/bmi30_firmware_release.env'])
  targets[d/'host/bmi30_firmware_release.env']=release
  targets[d/'host/bmi30_active_runtime/project/host/bmi30_firmware_release.env']=release
  for n,h in p['previous'].items():assert sha(b/n)==h,(label,n)
  for f,raw in targets.items():
   assert f.is_file() and not f.is_symlink()
   dest=backup/label/str(f.relative_to(root));dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dest)
   s=f.stat();temp=f.with_name(f.name+'.canonical-tmp');temp.write_bytes(raw);os.chmod(temp,s.st_mode&0o777);os.chown(temp,s.st_uid,s.st_gid);temp.replace(f)
  if label=='emmc':run('bash',str(d/'switch_bmi30_split_versions.sh'),'--validate',p['version'])
  else:run('chroot',str(root),'/bin/bash','/home/techaid/Documents/switch_bmi30_split_versions.sh','--validate',p['version'])
  assert all(sha(f)==h for f,h in configs.items())
  out[label]={'settings_preserved':True,'bundle_validated':True,'manifest_sha256':sha(d/'host/bmi30_firmware_release.env')}
 run('sync','-f',str(M));run('sync','-f',str(D))
finally:
 if subprocess.run(['mountpoint','-q',str(M)]).returncode==0:run('umount',str(M))
 M.rmdir()
print(json.dumps({'version':p['version'],'firmware_version':'2026-10-13-153432','roots':out,'backup':str(backup)}))
'''
i=json.loads((WORK/'M02-inventory.json').read_text())
r=call('M02',REMOTE,'release-alignment',{'inventory':i,'version':V,'files':files,'previous':previous},timeout=70)
print(json.dumps(r))
