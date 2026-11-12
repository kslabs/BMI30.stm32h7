import base64
import concurrent.futures
import hashlib
import json
from pathlib import Path
from read_runtime import WORK,s07,m02

REMOTE = '''
import hashlib,json,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin);d=pathlib.Path('/home/techaid/Documents');a=d/'host/bmi30_active_runtime/project'
def api(path):return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/'+path,timeout=5))
def diff(old,new,prefix=''):
 result=[]
 if isinstance(old,dict) and isinstance(new,dict):
  for key in old.keys()|new.keys():
   name=prefix+'.'+key if prefix else key
   if key not in old or key not in new:result.append(name)
   else:result.extend(diff(old[key],new[key],name))
 elif old!=new:result.append(prefix)
 return result
before=api('status');samples=[]
for i in range(12):
 s=api('status');assert s['ok'] and not s['stale'] and s['connection']['connected']
 assert s['mode']['avg_n'] in (8,16,24,32,40,48,56,64) and 1<=s['mode']['rpi_avg_n']<=8 and s['mode']['stream_mode']==2
 assert s['detector']['worker']['alive'] and s['detector']['worker']['overflows']==0
 samples.append({'connection':s['connection'],'worker':s['detector']['worker'],'fps':s['fps']});time.sleep(.5)
after=api('status');delta={k:after['stream_stats'][k]-before['stream_stats'][k] for k in ['seq_gap_ch0','seq_gap_ch1','crc_bad','host_rx_ack_fail']}
assert all(v==0 for v in delta.values()),delta
assert after['detector']['worker']['results']>before['detector']['worker']['results']
cfg=json.loads((d/'host/bmi30_config.json').read_text());changes=sorted(diff(p['original_config'],cfg))
assert cfg['rpi_avg_n']==after['mode']['rpi_avg_n'] and cfg['operation']['rpi_avg_n']==cfg['rpi_avg_n']
hashes={}
for name,relative in p['paths'].items():
 f=a/relative;hashes[name]=hashlib.sha256(f.read_bytes()).hexdigest();assert hashes[name]==p['hashes'][name]
portal=pathlib.Path('/usr/local/bin/bmi30-hotspot-info-server.py');assert hashlib.sha256(portal.read_bytes()).hexdigest()==p['hashes']['portal']
f=a/'host/bmi30_averaging.py';assert hashlib.sha256(f.read_bytes()).hexdigest()==p['hashes']['helper']
phase=api('tx-phase');frame=api('frame?max_points=200');assert frame.get('available')
html=urllib.request.urlopen('http://127.0.0.1:8765/',timeout=5).read().decode();assert 'avgReadout' in html and 'avgSel' not in html
root=subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip();assert root==p['root']
validation=subprocess.run(['bash',str(d/'switch_bmi30_split_versions.sh'),'--validate','2026-10-13-1534'],capture_output=True,text=True,timeout=60)
assert validation.returncode==0,validation.stderr[-1500:]
print(json.dumps({'mode':after['mode'],'sync':after['sync'],'averaging':after['averaging'],'phase':phase,
 'stream_errors_delta':delta,'samples':samples,'config_changed_paths':changes,'config':cfg,
 'hashes':hashes,'root':root,'bundle_validated':True,'frame_available':True}))
'''

def audit(board):
    original=json.loads((WORK/board/'inspection.json').read_text(encoding='utf8'))
    hashes={name:hashlib.sha256((WORK/'candidate'/(name+'.py')).read_bytes()).hexdigest() for name in ('core','engine','portal')}
    hashes['helper']=hashlib.sha256((Path(__file__).parent/'bmi30_averaging.py').read_bytes()).hexdigest()
    result=(s07 if board=='S07' else m02)(REMOTE,payload={'original_config':original['config'],'paths':original['paths'],'root':original['root'],'hashes':hashes},timeout=85)
    (WORK/board/'final-audit.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({'board':board,'mode':result['mode'],'config_changed_paths':result['config_changed_paths'],
                      'stream_errors_delta':result['stream_errors_delta'],'bundle_validated':result['bundle_validated']}))

if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(audit,('S07','M02')))
