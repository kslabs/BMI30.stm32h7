"""Finish copying current settings, then boot the verified USB once."""
import hashlib,json,pathlib,shutil,subprocess,sys,urllib.request
P=pathlib.Path;p=json.load(sys.stdin);D=P('/home/techaid/Documents');M=P('/mnt/bmi30-media-final-settings')
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')==p['hostname']
assert run('findmnt','-n','-o','SOURCE','/')==p['root']=='/dev/mmcblk0p2'
assert P('/sys/block/mmcblk0/device/cid').read_text()==p['emmc_cid']
usb=next(d for d in p['disks']['blockdevices'] if d['name']=='sda')
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==usb['serial']
state=json.loads(P('/run/bmi30-media-sync.json').read_text());assert state['stage']=='complete'
assert subprocess.run(['systemctl','is-active','--quiet','bmi30-media-sync']).returncode!=0
for dev in ('/dev/sda1','/dev/sda2'):
 assert subprocess.run(['findmnt','-rn','-S',dev],stdout=subprocess.DEVNULL).returncode!=0
for line in run('loginctl','list-sessions','--no-legend').splitlines():
 assert run('loginctl','show-session',line.split()[0],'-p','Service','--value')=='sshd'
assert not any(x.strip() in ('code','Xorg','lxsession','rsync','dpkg','apt','apt-get') for x in run('ps','-eo','comm=').splitlines())
order=next(x.split('=',1)[1] for x in run('vcgencmd','bootloader_config').splitlines() if x.startswith('BOOT_ORDER='))
assert order.endswith('4')
s=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=5))
assert s['ok'] and not s['stale'] and s['connection']['connected']
assert not s['player_recorder']['recording'] and not s['player_recorder']['playback_active']
before=P('/proc/sys/kernel/random/boot_id').read_text().strip()
paths=[]
for folder in (D,D/'host',P('/usr/local/bin/host')):
 if folder.is_dir():
  paths += [f for f in folder.iterdir() if f.is_file() and not f.is_symlink() and f.suffix in ('.json','.npy','.npz') and not f.name.startswith('bmi30_split')]
paths += [f for f in P('/etc').glob('bmi30*') if f.is_file() and not f.is_symlink()]
hashes={};changed=[];scheduled=False
M.mkdir(exist_ok=False)
try:
 run('mount','/dev/sda2',str(M))
 assert (M/'etc/hostname').read_text().strip()==p['hostname']
 run('systemctl','stop','bmi30-core','bmi30-hotspot-info')
 for f in paths:
  rel=str(f).lstrip('/');target=M/rel
  assert not target.is_symlink()
  if not target.is_file() or target.read_bytes()!=f.read_bytes():
   run('rsync','-aHAX','--numeric-ids',str(f),str(target));changed.append(rel)
  hashes[rel]=hashlib.sha256(f.read_bytes()).hexdigest()
  assert target.read_bytes()==f.read_bytes()
 config=json.loads((D/'host/bmi30_config.json').read_text())
 (P(state['backup'])/'preboot-config.json').write_text(json.dumps(config,indent=2))
 run('sync','-f',str(M));run('umount',str(M));M.rmdir()
 result={'host':p['hostname'],'boot_id_before':before,'target':'/dev/sda2','serial':usb['serial'],
  'version':state['result']['version'],'rollback':state['backup'],'settings_sha256':hashes,
  'settings_refreshed':changed,'mode_before':s['mode'],'config_before':config}
 (P('/var/lib/bmi30-media-sync')/'usb-boot-plan.json').write_text(json.dumps(result))
 run('systemd-run','--unit=bmi30-media-usb-boot','--on-active=4s','/usr/bin/systemctl','reboot')
 scheduled=True;result['reboot_scheduled']=True;print(json.dumps(result))
finally:
 if subprocess.run(['mountpoint','-q',str(M)]).returncode==0:run('umount',str(M))
 if M.exists():M.rmdir()
 if not scheduled:run('systemctl','start','bmi30-core','bmi30-hotspot-info')
