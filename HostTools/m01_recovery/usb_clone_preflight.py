"""Inventory an inactive USB target and estimate space needed for rollback."""
import concurrent.futures
import json
from pathlib import Path
from deploy_fleet_release import remote, HOSTS

WORK = Path(__file__).resolve().parents[2] / '.tmp/system-copy-20261002'
EXCLUDES = ['/boot/firmware/***','/dev/***','/proc/***','/sys/***','/tmp/***',
 '/run/***','/mnt/***','/media/***','/lost+found/***','/swapfile','/var/swap',
 '/var/backups/bmi30-usb-sync/***','/home/*/thinclient_drives/***',
 '/home/*/.cache/gvfs/***','/home/*/.cache/doc/***','/home/*/.gvfs/***']
SCRIPT = r'''
import json,pathlib,subprocess,sys,os
p=json.load(sys.stdin)
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')==p['host']
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/mmcblk0p2'
assert run('findmnt','-n','-o','SOURCE','/boot/firmware')=='/dev/mmcblk0p1'
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==p['serial']
assert run('blkid','-s','UUID','-o','value','/dev/sda2')==p['uuid']
assert subprocess.run(['findmnt','-rn','-S','/dev/sda2'],stdout=subprocess.DEVNULL).returncode!=0
m=pathlib.Path('/mnt/bmi30-usb-preflight');m.mkdir(exist_ok=False)
try:
 run('mount','-o','ro,noload','/dev/sda2',str(m))
 base=['ionice','-c','3','nice','-n','15','rsync','-aHAXxn','--numeric-ids','--delete',
       '--itemize-changes','--out-format=%i|%l|%n','--stats']
 result=subprocess.run(base+['--exclude='+x for x in p['excludes']]+['/',str(m)+'/'],capture_output=True,text=True,timeout=180)
 assert result.returncode in (0,24),result.stderr
 rollback_bytes=0;rollback_files=[];changed=[]
 for line in result.stdout.splitlines():
  if '|' not in line:continue
  kind,size,name=line.split('|',2)
  name=name.rstrip('/')
  if name in ('','.'):continue
  target=m/name
  if kind.startswith('*deleting') or kind.startswith('>f') or kind.startswith('cL'):
   if target.is_file() and not target.is_symlink():
    rollback_bytes+=target.stat().st_size;rollback_files.append(name)
  changed.append(line)
 st=os.statvfs('/')
 usb=os.statvfs(m)
 print(json.dumps({'host':p['host'],'usb_hostname':(m/'etc/hostname').read_text().strip(),
  'serial':p['serial'],'uuid':p['uuid'],'rollback_file_bytes_upper_bound':rollback_bytes,
  'rollback_files':rollback_files,'source_available':st.f_bavail*st.f_frsize,
  'target_size':usb.f_blocks*usb.f_frsize,'target_available':usb.f_bavail*usb.f_frsize,
  'source_used':(st.f_blocks-st.f_bfree)*st.f_frsize,'changes':changed,
  'statistics':result.stdout.split('\n\n')[-1],
  'bootloader':run('vcgencmd','bootloader_config')}))
finally:
 if subprocess.run(['mountpoint','-q',str(m)]).returncode==0:run('umount',str(m))
 m.rmdir()
'''

def preflight(board):
    fleet=json.loads((WORK/'fleet-disks.json').read_text())
    disk=next(d for d in fleet[board]['disks']['blockdevices'] if d['path']=='/dev/sda')
    uuid=next(d['uuid'] for d in disk['children'] if d['path']=='/dev/sda2')
    p={'host':HOSTS[board],'serial':disk['serial'],'uuid':uuid,'excludes':EXCLUDES}
    result=json.loads(remote(HOSTS[board],SCRIPT,p,timeout=220))
    (WORK/(board+'-clone-preflight.json')).write_text(json.dumps(result,indent=2))
    return {'board':board,**{k:v for k,v in result.items() if k not in ('changes','rollback_files')}}

if __name__=='__main__':
    import sys
    boards=sys.argv[1:] or ['S10','S11','S13']
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(preflight,boards):print(json.dumps(result),flush=True)
