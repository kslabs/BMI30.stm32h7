"""Task driver for guarded S07/M02 media inspection and synchronization."""
import concurrent.futures
import json
from pathlib import Path
import sys
from read_runtime import ROOT, s07, m02

WORK = ROOT / '.tmp/rpi-media-sync-20261013'
WORK.mkdir(exist_ok=True)
INVENTORY = r'''
import json,pathlib,subprocess,urllib.request
P=pathlib.Path
def run(*a):
 p=subprocess.run(a,capture_output=True,text=True)
 return {'rc':p.returncode,'out':p.stdout.strip(),'err':p.stderr.strip()}
def read(p):return P(p).read_text() if P(p).is_file() else None
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
s=json.load(op.open('http://127.0.0.1:8765/api/status',timeout=5))
print(json.dumps({'hostname':run('hostname')['out'],'serial':read('/proc/device-tree/serial-number'),
 'root':run('findmnt','-n','-o','SOURCE','/')['out'],'boot':run('findmnt','-n','-o','SOURCE','/boot/firmware')['out'],
 'disks':json.loads(run('lsblk','-J','-b','-o','NAME,PATH,SIZE,TYPE,TRAN,MODEL,SERIAL,FSTYPE,UUID,PARTUUID,MOUNTPOINTS')['out']),
 'emmc_cid':read('/sys/block/mmcblk0/device/cid'),'version':read('/home/techaid/Documents/host/bmi30_split_active_version.env'),
 'bootloader':run('vcgencmd','bootloader_config'),'cmdline':read('/boot/firmware/cmdline.txt'),
 'fstab':read('/etc/fstab'),'services':run('systemctl','is-active','bmi30-core','bmi30-hotspot-info','bmi30-network','bmi30-agent'),
 'agents':{p:read(p) for p in ['/AGENTS.md','/home/AGENTS.md','/home/techaid/AGENTS.md','/home/techaid/Documents/AGENTS.md']},
 'status':{k:s.get(k) for k in ['ok','stale','connection','mode','sync','detector','stream_stats','player_recorder']},
 'sessions':run('loginctl','list-sessions','--no-legend'),'space':run('df','-B1','/'),
 'processes':run('ps','-eo','comm=')}))
'''

def call(board, script, label, payload=None, timeout=90):
    result=(s07 if board=='S07' else m02)(script,payload=payload,timeout=timeout)
    (WORK/(board+'-'+label+'.json')).write_text(json.dumps(result,indent=2),encoding='utf8')
    return result

if __name__=='__main__':
    label=sys.argv[1] if len(sys.argv)>1 else 'inventory'
    script=INVENTORY if label=='inventory' else (Path(__file__).parent/(label+'.py')).read_text(encoding='utf8')
    boards=sys.argv[2:] or ['S07','M02']
    for board in boards:
        payload=None if label=='inventory' else json.loads((WORK/(board+'-inventory.json')).read_text(encoding='utf8'))
        r=call(board,script,label,payload=payload,timeout=300)
        print(json.dumps({'board':board,**{k:v for k,v in r.items() if k not in ('status','agents','processes','config_before','differences','offline_agents')}},ensure_ascii=False))
