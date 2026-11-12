"""Read-only progress and health summary for USB system copies."""
import concurrent.futures
import json
from pathlib import Path
import sys
from deploy_fleet_release import remote, HOSTS
from usb_clone_preflight import WORK

SCRIPT = r'''
import json,pathlib,subprocess
def run(*a):return subprocess.run(a,capture_output=True,text=True).stdout.strip()
f=pathlib.Path('/run/bmi30-usb-copy.json')
v=json.loads(f.read_text()) if f.exists() else {}
b=pathlib.Path(v.get('backup','/no-such-path'))
mem={p[0].rstrip(':'):int(p[1]) for l in pathlib.Path('/proc/meminfo').read_text().splitlines() for p in [l.split()] if len(p)>=2}
copy_bytes=0
for process in pathlib.Path('/proc').iterdir():
 if not process.name.isdigit():continue
 try:
  if (process/'comm').read_text().strip()=='rsync':
   counters=dict(line.split(':',1) for line in (process/'io').read_text().splitlines())
   copy_bytes=max(copy_bytes,int(counters['rchar']))
 except (OSError,ValueError):pass
print(json.dumps({'root':run('findmnt','-n','-o','SOURCE','/'),'status':v,
 'rsync_read_gib':round(copy_bytes/1024**3,2),
 'archives':{f.name:f.stat().st_size for f in b.glob('*.tar.gz')},
 'log_sizes':{f.name:f.stat().st_size for f in b.glob('*.log')},
 'active':run('systemctl','is-active','bmi30-usb-copy'),
 'core':run('systemctl','is-active','bmi30-core'),
 'network':run('systemctl','is-active','bmi30-network'),
 'available_ram_mib':mem['MemAvailable']//1024,
 'tail':run('journalctl','-u','bmi30-usb-copy','-n','10','-o','cat','--no-pager') if v.get('stage')=='failed' else ''}))
'''

def one(board):
    try:
        return board,json.loads(remote(HOSTS[board],SCRIPT,timeout=30))
    except Exception as exc:
        return board,{'error':str(exc)}

if __name__=='__main__':
    boards=sys.argv[1:] or ['M01','S10','S11','S13']
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        result=dict(pool.map(one,boards))
    (WORK/'progress.json').write_text(json.dumps(result,indent=2))
    for board,status in result.items():print(board,json.dumps(status),flush=True)
