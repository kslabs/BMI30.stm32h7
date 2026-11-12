"""Read-only comparison of eMMC and USB persistent systems; never applies rsync."""
import concurrent.futures
import json
from pathlib import Path
from deploy_fleet_release import remote, HOSTS

WORK=Path(__file__).resolve().parents[2]/'.tmp/system-copy-20261002'
SCRIPT=r'''
import collections,hashlib,json,pathlib,subprocess,sys,time
p=json.load(sys.stdin)
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')==p['host']
assert run('findmnt','-n','-o','SOURCE','/')==p['live']
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==p['serial']
assert run('blkid','-s','UUID','-o','value',p['offline'])==p['uuid']
assert subprocess.run(['findmnt','-rn','-S',p['offline']],stdout=subprocess.DEVNULL).returncode!=0
m=pathlib.Path('/mnt/bmi30-system-audit-20261002');m.mkdir(exist_ok=False)
try:
 run('mount','-o','ro,noload',p['offline'],str(m))
 assert (m/'etc/hostname').read_text().strip()==p['host']
 source=pathlib.Path('/') if p['live']=='/dev/mmcblk0p2' else m
 target=m if source==pathlib.Path('/') else pathlib.Path('/')
 base=['ionice','-c','3','nice','-n','15','rsync','--dry-run','--archive','--numeric-ids','--itemize-changes','--one-file-system','--delete','--out-format=%i|%n%L']
 excludes=['/dev/***','/proc/***','/sys/***','/run/***','/tmp/***','/mnt/***','/media/***','/lost+found/***','/var/swap','/swapfile','/boot/firmware/***']
 r=subprocess.run(base+['--exclude='+x for x in excludes]+[str(source).rstrip('/')+'/',str(target).rstrip('/')+'/'],capture_output=True,text=True,timeout=150)
 assert r.returncode in (0,24),r.stderr
 lines=r.stdout.splitlines();groups=collections.Counter()
 for line in lines:
  name=line.split('|',1)[-1];parts=name.split('/');groups['/'.join(parts[:2])]+=1
 content={}
 for name in ['etc','opt','usr/local']:
  r2=subprocess.run(base+['--checksum',str(source/name)+'/',str(target/name)+'/'],capture_output=True,text=True,timeout=90)
  assert r2.returncode in (0,24),r2.stderr;content[name]=r2.stdout.splitlines()
 def packages(root):
  out={}
  for block in (root/'var/lib/dpkg/status').read_text().split('\n\n'):
   fields={k:v for l in block.splitlines() if not l.startswith(' ') and ': ' in l for k,v in [l.split(': ',1)]}
   if fields.get('Status')=='install ok installed':out[fields['Package']]=fields.get('Version')
  return out
 a,b=packages(source),packages(target)
 package_diff={k:{'emmc':a.get(k),'usb':b.get(k)} for k in set(a)|set(b) if a.get(k)!=b.get(k)}
 identities=['etc/machine-id','etc/hostname','etc/fstab','etc/NetworkManager/system-connections',
  'home/techaid/Documents/host/bmi30_config.json','home/techaid/Documents/host/bmi30_sel.json']
 config={}
 for name in identities:
  left,right=source/name,target/name
  if left.is_file() and right.is_file():config[name]={'same':left.read_bytes()==right.read_bytes(),'emmc_mtime':left.stat().st_mtime,'usb_mtime':right.stat().st_mtime}
 print(json.dumps(dict(host=p['host'],live=p['live'],direction='emmc_to_usb',
  metadata_differences=lines,metadata_groups=dict(groups),content_differences=content,package_differences=package_diff,config_comparison=config)))
finally:
 if subprocess.run(['mountpoint','-q',str(m)]).returncode==0:run('umount',str(m))
 m.rmdir()
'''

def audit(item):
    board,params=item
    result=json.loads(remote(HOSTS[board],SCRIPT,{'host':HOSTS[board],**params},timeout=350))
    (WORK/(board+'-audit.json')).write_text(json.dumps(result,indent=2))
    return {'board':board,'metadata_groups':result['metadata_groups'],
        'content_differences':result['content_differences'],'package_differences':result['package_differences'],
        'config_comparison':result['config_comparison']}

if __name__=='__main__':
    WORK.mkdir(exist_ok=True)
    items=[('M01',dict(live='/dev/sda2',offline='/dev/mmcblk0p2',uuid='fb90e995-3446-4991-8468-02d2544a3521',serial='25072913750154')),
           ('S10',dict(live='/dev/mmcblk0p2',offline='/dev/sda2',uuid='d24efc0b-47e6-444f-bc0a-aff5691c7266',serial='25072933770324'))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(audit,items):print(json.dumps(result),flush=True)
