"""Read-only fleet inventory and bounded group/acquisition observation via trusted SSH."""
import argparse
import collections
import concurrent.futures
import json
from pathlib import Path
import time

from deploy import remote

HOSTS = {'M01': 'BMI30-05AB38E6A', 'S10': 'BMI30-D5EB8F26C',
         'S11': 'BMI30-D5507EDFC', 'S12': 'BMI30-27448D72B', 'S13': 'BMI30-7AA5CED27'}
WORK = Path(__file__).resolve().parents[2] / '.tmp/group-stability-20261005'

INVENTORY = r'''
import hashlib,json,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin)
def run(*args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=35)
 return r.stdout.strip() if r.returncode==0 else {'error':r.stderr[-500:]}
def api(port):
 try:
  with urllib.request.urlopen('http://127.0.0.1'+port+'/api/status',timeout=8) as r:return json.load(r)
 except Exception as e:return {'error':type(e).__name__}
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def inspect(root):
 d=root/'home/techaid/Documents';host=d/'host';active=host/'bmi30_active_runtime/project'
 hashes={}
 for base in (root/'opt/bmi30-network',active/'host'):
  if base.exists():
   for f in sorted(base.iterdir()):
    if f.is_file() and ('.py' in f.name or f.suffix in ('.js','.html','.service')):hashes[str(f.relative_to(root))]=sha(f)
 for base in (active/'host/usb_vendor',active/'host/tag_response_filters'):
  if base.exists():
   for f in sorted(base.rglob('*')):
    if f.is_file() and f.suffix=='.py':hashes[str(f.relative_to(root))]=sha(f)
 for f in (root/'usr/local/bin/bmi30-hotspot-info-server.py',active/'hotspot_info_server.py',d/'hotspot_info_server.py'):
  if f.is_file():hashes[str(f.relative_to(root))]=sha(f)
 env={}
 for f in (host/'bmi30_split_active_version.env',host/'bmi30_firmware_release.env',active/'host/bmi30_firmware_release.env'):
  if f.exists():env[str(f.relative_to(root))]=f.read_text()
 configs={}
 for name in ('bmi30_config.json','bmi30_sel.json','bmi30_relay.json'):
  f=host/name
  if f.exists():configs[name]=sha(f)
 config=root/'etc/bmi30-network/config.json'
 if config.exists():
  c=json.loads(config.read_text());configs['network_id']=c.get('id');configs['automatic']=c.get('automatic');configs['network_config_sha256']=sha(config)
 release=root/'opt/bmi30-network/release.json'
 return {'hostname':(root/'etc/hostname').read_text().strip(),'env':env,'hashes':hashes,'settings_hashes':configs,
  'network_release':json.loads(release.read_text()) if release.exists() else None,
  'os_release':(root/'etc/os-release').read_text()}
assert run('hostname')==p['host']
live=run('findmnt','-n','-o','SOURCE','/')
disks=json.loads(run('lsblk','-J','-o','NAME,PATH,TYPE,TRAN,SERIAL,UUID,FSTYPE,MOUNTPOINTS,SIZE'))
roots=[{'device':live,'active':True,**inspect(pathlib.Path('/'))}]
for disk in disks['blockdevices']:
 for part in disk.get('children',[]):
  device=part['path']
  if part.get('fstype')!='ext4' or device==live:continue
  m=pathlib.Path('/mnt/bmi30-group-audit-20261005')
  targets=run('findmnt','-rn','-S',device,'-o','TARGET')
  if isinstance(targets,str) and targets:
   roots.append({'device':device,'active':False,'skipped':'already mounted','targets':targets});continue
  m.mkdir(exist_ok=False)
  try:
   subprocess.run(['mount','-o','ro,noload',device,str(m)],check=True,capture_output=True)
   roots.append({'device':device,'uuid':part.get('uuid'),'active':False,**inspect(m)})
  finally:
   if subprocess.run(['mountpoint','-q',str(m)]).returncode==0:subprocess.run(['umount',str(m)],check=True,capture_output=True)
   m.rmdir()
portal=api('');core=api(':8765')
net=json.loads(pathlib.Path('/run/bmi30-network/status.json').read_text())
group=json.loads(pathlib.Path('/run/bmi30-network/group.json').read_text())
logs={}
for label,args in [('network',('-u','bmi30-network')),('wifi',('-u','NetworkManager')),('kernel',('-k',))]:
 logs[label]=run('journalctl','--since','-2 hours','--no-pager','-o','short-iso',*args)
print(json.dumps({'board':p['board'],'host':p['host'],'time':time.time(),'root':live,'boot_root':run('findmnt','-n','-o','SOURCE','/boot/firmware'),
 'disks':disks,'roots':roots,'services':run('systemctl','show','bmi30-core','bmi30-network','bmi30-hotspot-info','-p','Id','-p','MainPID','-p','NRestarts','-p','ActiveState'),
 'failed_services':run('systemctl','--failed','--no-legend','--no-pager'),'wifi_link':run('iw','dev','wlan0','link'),
 'wifi_powersave':run('iw','dev','wlan0','get','power_save'),'sync':portal.get('sync_mode'),'firmware':portal.get('stm32_firmware_version'),
 'core':{k:core.get(k) for k in ('ok','stale','connection','stream_stats','detector','mode','firmware','sync')},
 'network':net,'group':group,'logs':logs}))
'''

OBSERVE = r'''
import collections,json,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin);assert subprocess.check_output(['hostname'],text=True).strip()==p['host']
samples=[];end=time.monotonic()+p['duration']
while time.monotonic()<end:
 started=time.monotonic();now=time.time();s={'time':now}
 for name in ('group','status'):
  try:
   data=json.loads(pathlib.Path('/run/bmi30-network/'+name+'.json').read_text())
   s[name]={'file_age':round(now-data.get('checked_at',0),3),'nodes':[{'id':n['id'],'age':round(now-n.get('issued',0),3),'available':n.get('available'),'seq':n.get('seq'),'boot':n.get('boot'),'via':n.get('via'),'cache_age':n.get('cache_age_s')} for n in data.get('nodes',[])],
    'local':{k:data.get('local',{}).get(k) for k in ('bssid','signal','ssid','freq','internet')},'status':data.get('status'),'history':data.get('history')}
  except Exception as e:s[name]={'error':type(e).__name__}
 try:
  with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=1.5) as r:c=json.load(r)
  s['core']={k:c.get(k) for k in ('ok','stale','connection','stream_stats','mode')};s['worker_alive']=c.get('detector',{}).get('worker',{}).get('alive')
 except Exception as e:s['core']={'error':type(e).__name__}
 s['sample_ms']=round((time.monotonic()-started)*1000,1);samples.append(s)
 if p.get('progress_path') and len(samples)%120==0:
  window=[x for x in samples if x['time']>=samples[0]['time']+p.get('settle',0)]
  missing=collections.Counter();unavailable=collections.Counter()
  for x in window:
   nodes={n['id']:n for n in x.get('group',{}).get('nodes',[])}
   for ident in p['hosts']:
    if ident not in nodes or nodes[ident]['age']>=4:missing[ident]+=1
    elif not nodes[ident].get('available'):unavailable[ident]+=1
  summary={'board':p['board'],'samples':len(samples),'steady_samples':len(window),'missing':dict(missing),'unavailable':dict(unavailable),
   'current_nodes':len(s.get('group',{}).get('nodes',[])), 'core_issues':sum(not x.get('core',{}).get('ok') or x['core'].get('stale') or not x.get('worker_alive') for x in window)}
  path=pathlib.Path(p['progress_path']);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(summary));temp.replace(path)
 time.sleep(max(.02,.5-(time.monotonic()-started)))
print(json.dumps({'board':p['board'],'host':p['host'],'duration':p['duration'],'samples':samples}))
'''

def one(item, action, duration):
    board, host = item
    try:
        result = json.loads(remote(host, INVENTORY if action == 'inventory' else OBSERVE,
                                   {'host': host, 'board': board, 'duration': duration},
                                   timeout=180 if action == 'inventory' else duration+30))
        (WORK / (board+'-'+action+'.json')).write_text(json.dumps(result, indent=2))
        if action == 'inventory':
            summary = {'board':board,'root':result['root'],'roots':[{k:r.get(k) for k in ('device','active','hostname','skipped')} for r in result['roots']],
                       'group_nodes':len(result['group']['nodes']),'network_nodes':len(result['network']['nodes']),
                       'services':result['services'],'wifi':result['wifi_link'],'power_save':result['wifi_powersave']}
        else:
            missing = collections.Counter()
            unavailable = collections.Counter()
            for s in result['samples']:
                nodes={n['id']:n for n in s.get('group',{}).get('nodes',[])}
                for h in HOSTS.values():
                    if h not in nodes or nodes[h]['age']>=4:missing[h]+=1
                    elif not nodes[h].get('available'):unavailable[h]+=1
            summary={'board':board,'samples':len(result['samples']),'missing':dict(missing),'unavailable':dict(unavailable),
                     'max_group_file_age':max(s.get('group',{}).get('file_age',0) for s in result['samples']),
                     'core_issues':sum(not s.get('core',{}).get('ok') or s['core'].get('stale') or not s.get('worker_alive') for s in result['samples'])}
        print(json.dumps(summary), flush=True)
    except Exception as exc:
        print(json.dumps({'board':board,'error':str(exc)}),flush=True)
        raise

if __name__ == '__main__':
    import collections
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['inventory','observe'])
    parser.add_argument('--duration',type=int,default=180)
    args=parser.parse_args()
    WORK.mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda item:one(item,args.action,args.duration),HOSTS.items()))
