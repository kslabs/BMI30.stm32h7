"""Preserve live M01 settings and server identity before a user-controlled eMMC boot."""
import json
from pathlib import Path

from deploy_fleet_release import remote, HOSTS

SCRIPT = r'''
import hashlib,json,os,pathlib,subprocess,time
def run(*a):
 return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')=='BMI30-05AB38E6A'
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2'
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')=='25072913750154'
assert run('blkid','-s','UUID','-o','value','/dev/mmcblk0p2')=='fb90e995-3446-4991-8468-02d2544a3521'
for dev in ('/dev/mmcblk0p1','/dev/mmcblk0p2'):
 assert subprocess.run(['findmnt','-rn','-S',dev],stdout=subprocess.DEVNULL).returncode!=0
stamp=time.strftime('%Y%m%d-%H%M%S')
m=pathlib.Path('/mnt/bmi30-preboot-settings');m.mkdir(exist_ok=False)
b=pathlib.Path('/mnt/bmi30-preboot-boot');b.mkdir(exist_ok=False)
backup=pathlib.Path('/var/backups/bmi30-preboot')/stamp
backup.mkdir(parents=True,mode=0o700)
paths=['etc/bmi30-agent','etc/bmi30/portal_config.json',
 'var/lib/bmi30-agent/device_api_token','var/lib/bmi30-agent/bound_raspberry_serial',
 'var/lib/bmi30-agent/state.json','usr/local/bin/host/bmi30_config.json',
 'home/techaid/Documents/.bmi30_cloud_sync/server']
host=pathlib.Path('/home/techaid/Documents/host')
paths += [str(p).lstrip('/') for p in host.iterdir() if p.is_file() and
          (p.suffix in ('.json','.npz','.npy') or p.name in
           ('bmi30_split_active_version.env','bmi30_firmware_release.env'))]
try:
 run('mount','/dev/mmcblk0p2',str(m))
 run('mount','-o','ro','/dev/mmcblk0p1',str(b))
 assert (m/'etc/hostname').read_text().strip()=='BMI30-05AB38E6A'
 assert 'root=PARTUUID=706df40b-02' in (b/'cmdline.txt').read_text()
 assert 'PARTUUID=706df40b-02' in (m/'etc/fstab').read_text()
 assert 'dtparam=ant2' in (b/'config.txt').read_text()
 assert (m/'etc/systemd/system/multi-user.target.wants/bmi30-network.service').is_symlink()
 assert (m/'opt/bmi30-network/service.py').is_file()
 for tag,root in [('usb-current',pathlib.Path('/')),('emmc-before',m)]:
  selected=[p for p in paths if (root/p).exists()]
  archive=backup/(tag+'.tar.gz')
  run('tar','--acls','--xattrs','-czf',str(archive),'-C',str(root),'--',*selected)
  archive.chmod(0o600)
 # Keep a separate rollback on eMMC, which remains available after USB overwrite.
 emmc_backup=m/'var/backups/bmi30-preboot'/stamp
 emmc_backup.mkdir(parents=True,mode=0o700)
 run('cp','-a',str(backup/'usb-current.tar.gz'),str(backup/'emmc-before.tar.gz'),str(emmc_backup))
 stable=[p for p in paths if p!='var/lib/bmi30-agent/state.json' and pathlib.Path('/'+p).is_file()]
 before={p:hashlib.sha256(pathlib.Path('/'+p).read_bytes()).hexdigest() for p in stable}
 for p in paths:
  src=pathlib.Path('/'+p)
  dst=m/p
  if not src.exists(): continue
  dst.parent.mkdir(parents=True,exist_ok=True)
  if src.is_dir():
   run('rsync','-aHAX',str(src)+'/',str(dst)+'/')
  else:
   run('cp','-a',str(src),str(dst))
 for p,digest in before.items():
  assert hashlib.sha256(pathlib.Path('/'+p).read_bytes()).hexdigest()==digest, 'Live setting changed during copy: '+p
  assert hashlib.sha256((m/p).read_bytes()).hexdigest()==digest, 'Readback failed: '+p
 for p in pathlib.Path('/etc/bmi30-agent').iterdir():
  if p.is_file(): assert p.read_bytes()==(m/'etc/bmi30-agent'/p.name).read_bytes()
 run('sync','-f',str(m))
 result={'host':run('hostname'),'live_root':'/dev/sda2','prepared_root':'/dev/mmcblk0p2',
  'copied_paths':paths,'settings_verified':True,'agent_identity_verified':True,
  'emmc_boot_references_verified':True,'network_service_present':True,
  'backup':str(backup),'emmc_backup':'/'+str(emmc_backup.relative_to(m)),
  'usb_full_copy_done':False,'emmc_boot_tested':False}
 (backup/'result.json').write_text(json.dumps(result,indent=2))
 (emmc_backup/'result.json').write_text(json.dumps(result,indent=2))
 print(json.dumps(result))
finally:
 for p in (b,m):
  if subprocess.run(['mountpoint','-q',str(p)]).returncode==0: run('umount',str(p))
  p.rmdir()
'''

if __name__ == '__main__':
    result = json.loads(remote(HOSTS['M01'], SCRIPT, timeout=120))
    path = Path(__file__).resolve().parents[2] / '.tmp/system-copy-20261002/M01-preboot-result.json'
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
