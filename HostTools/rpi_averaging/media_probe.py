import hashlib,json,pathlib,subprocess,sys
P=pathlib.Path;p=json.load(sys.stdin)
M=P('/mnt/bmi30-media-sync-audit');B=M/'boot/firmware';D=P('/home/techaid/Documents')
def run(*a,ok=(0,)):
 r=subprocess.run(a,capture_output=True,text=True)
 assert r.returncode in ok,(a,r.returncode,r.stderr[-1200:],r.stdout[-1200:])
 return r.stdout.strip()
def read(path):return path.read_text() if path.is_file() else None
assert run('hostname')==p['hostname']
assert P('/proc/device-tree/serial-number').read_text()==p['serial']
assert run('findmnt','-n','-o','SOURCE','/')==p['root']=='/dev/mmcblk0p2'
usb=next(d for d in p['disks']['blockdevices'] if d['name']=='sda')
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==usb['serial']
assert run('blkid','-s','UUID','-o','value','/dev/sda2')==usb['children'][1]['uuid']
assert P('/sys/block/mmcblk0/device/cid').read_text()==p['emmc_cid']
for dev in ('/dev/sda1','/dev/sda2'):
 assert subprocess.run(['findmnt','-rn','-S',dev],stdout=subprocess.DEVNULL).returncode!=0
out={'fsck':{},'differences':{},'usb_serial':usb['serial']}
for dev,cmd in [('/dev/sda2',['e2fsck','-f','-n']),('/dev/sda1',['fsck.fat','-n'])]:
 r=subprocess.run(cmd+[dev],capture_output=True,text=True)
 out['fsck'][dev]={'rc':r.returncode,'output':(r.stdout+r.stderr)[-1800:]}
 if r.returncode!=0:print(json.dumps(out));sys.exit(0)
M.mkdir(exist_ok=False)
try:
 run('mount','-o','ro,noload','/dev/sda2',str(M));run('mount','-o','ro','/dev/sda1',str(B))
 for rel in ['etc/hostname','etc/fstab','home/techaid/Documents/host/bmi30_split_active_version.env','home/techaid/Documents/host/bmi30_firmware_release.env','boot/firmware/cmdline.txt']:
  out[rel]=read(M/rel)
 out['offline_agents']=read(M/'home/techaid/Documents/AGENTS.md')
 for folder in ['etc/systemd/system','usr/local','opt','usr/lib','var/lib/dpkg','boot/firmware']:
  r=run('ionice','-c','3','nice','-n','15','rsync','-aHAXxnc','--numeric-ids','--delete','--out-format=%i|%n','/'+folder+'/',str(M/folder)+'/',ok=(0,24))
  out['differences'][folder]=r.splitlines()
 for root in [D,M/'home/techaid/Documents']:
  out[str(root)+'-instructions']=run('find',str(root/'host/bmi30_active_runtime'),str(root/'utilities'),'-name','AGENTS.md','-type','f',ok=(0,1))
 out['settings_equal']={}
 for root in [D,D/'host']:
  for f in root.iterdir():
   if f.is_file() and not f.is_symlink() and f.suffix in ('.json','.npy','.npz') and not f.name.startswith('bmi30_split'):
    target=M/str(f).lstrip('/')
    out['settings_equal'][str(f)]=target.is_file() and target.read_bytes()==f.read_bytes()
 out['space']=run('df','-B1',str(M))
finally:
 for path in (B,M):
  if subprocess.run(['mountpoint','-q',str(path)]).returncode==0:run('umount',str(path))
 M.rmdir()
print(json.dumps(out))
