"""Isolated tests and a disposable loop image; no real USB or firmware changes."""
import base64,json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.tmp/hotspot-media-20261021'))
from control import remote
SCRIPT=r'''
import base64,json,os,pathlib,shutil,subprocess,sys,uuid
P=pathlib.Path;p=json.load(sys.stdin)
assert P('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
root=P('/var/tmp')/('bmi30-usb-check-'+uuid.uuid4().hex);root.mkdir(mode=0o700)
def run(*args,**kwargs):return subprocess.run(args,capture_output=True,text=True,timeout=120,**kwargs)
loop=None;report={}
try:
 for name,data in p['files'].items():
  relative=P(name);assert not relative.is_absolute() and '..' not in relative.parts
  target=root/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(base64.b64decode(data))
 r=run('python3','-W','ignore','-m','unittest','discover','-s','HostTools/usb_recovery','-p','test_*.py',cwd=root)
 report['tests']={'returncode':r.returncode,'stderr':r.stderr[-1800:]}
 if r.returncode:raise RuntimeError('isolated tests failed')
 image=root/'disposable.img'
 with image.open('wb') as stream:stream.truncate(1024**3)
 r=run('losetup','--find','--show','--partscan',str(image));assert r.returncode==0
 loop=r.stdout.strip();assert loop.startswith('/dev/loop')
 associated=run('losetup','-j',str(image)).stdout
 assert associated.startswith(loop+':') and str(image) in associated
 fd=os.open(loop,os.O_RDWR)
 try:
  assert os.major(os.fstat(fd).st_rdev)==7
  r=run('sfdisk','--lock=yes','--wipe','always','/proc/self/fd/'+str(fd),pass_fds=(fd,),
    input='label: dos\nlabel-id: 0xb1301028\n\nstart=1MiB,size=512MiB,type=c,bootable\nstart=513MiB,type=83\n')
  report['loop_partition']={'returncode':r.returncode,'error':r.stderr[-700:] if r.returncode else ''}
  assert r.returncode==0
 finally:os.close(fd)
 run('udevadm','settle')
 for index,args in [(1,['mkfs.vfat','-F','32','-n','BMI30BOOT']), (2,['mkfs.ext4','-F','-m','1','-L','BMI30ROOT'])]:
  path=loop+'p'+str(index)
  fd=os.open(path,os.O_RDWR)
  try:r=run(*args,'/proc/self/fd/'+str(fd),pass_fds=(fd,))
  finally:os.close(fd)
  report['loop_mkfs_'+str(index)]={'returncode':r.returncode,'error':r.stderr[-700:] if r.returncode else ''}
  assert r.returncode==0
 report['scope']='Tests and formatting of a private sparse loop image only; no real removable media, firmware, services or settings changed'
finally:
 if loop:run('losetup','-d',loop)
 assert root.parent==P('/var/tmp') and root.name.startswith('bmi30-usb-check-')
 shutil.rmtree(root)
print(json.dumps(report))
'''
from integrate_bundle import FEATURE_FILES,feature_source
files=list(pathlib.Path(__file__).parent.glob('*.py'))+[feature_source(name) for name in FEATURE_FILES]+[ROOT/'.tmp/usb-recovery-20261028/M02-portal-before.py',ROOT/'.tmp/usb-recovery-20261028/bundle-builder-before.sh']
result=remote('M02',SCRIPT,{'files':{p.relative_to(ROOT).as_posix():base64.b64encode(p.read_bytes()).decode() for p in files}},timeout=160)
(ROOT/'.tmp/usb-recovery-20261028/linux-checks.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result))
