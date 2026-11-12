"""Read relay status in the core, group and portal; never command the relay."""
import argparse,collections,concurrent.futures,json
from pathlib import Path
from audit_stability import HOSTS
from deploy import remote
WORK=Path(__file__).resolve().parents[2]/'.tmp/relay-display-20261005'
SCRIPT=r'''
import json,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin);assert subprocess.check_output(['hostname'],text=True).strip()==p['host']
samples=[];until=time.monotonic()+p['duration'];opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8765/api/status',timeout=2) as r:core_before=json.load(r)
while time.monotonic()<until:
 start=time.monotonic();s={'time':time.time()}
 try:
  with opener.open('http://127.0.0.1:8765/api/relay',timeout=1) as r:s['relay']=json.load(r)['relay']
  group=json.loads(pathlib.Path('/run/bmi30-network/group.json').read_text())
  s['group']=[{'id':n['id'],'available':n['available'],'age':time.time()-n['issued'],'relay':n.get('relay')} for n in group['nodes']]
  if len(samples)%10==0:
   with opener.open('http://127.0.0.1/api/status',timeout=2) as r:portal=json.load(r)
   s['portal_relay']=(portal.get('device_sensors',{}).get('optic_settings') or {}).get('relay')
 except Exception as e:s['error']=type(e).__name__
 samples.append(s);time.sleep(max(.01,.1-(time.monotonic()-start)))
with opener.open('http://127.0.0.1:8765/api/status',timeout=2) as r:core=json.load(r)
print(json.dumps({'board':p['board'],'samples':samples,'core_before':{k:core_before.get(k) for k in ('ok','stale','connection','stream_stats')},'core':{k:core.get(k) for k in ('ok','stale','connection','stream_stats')},
 'services':subprocess.check_output(['systemctl','show','bmi30-core','bmi30-network','bmi30-hotspot-info','-p','Id','-p','MainPID','-p','NRestarts'],text=True)}))
'''
def one(board,duration=45,phase='before'):
 data=json.loads(remote(HOSTS[board],SCRIPT,{'host':HOSTS[board],'board':board,'duration':duration},timeout=duration+25))
 WORK.mkdir(exist_ok=True);(WORK/(board+'-'+phase+'.json')).write_text(json.dumps(data,indent=2))
 samples=data['samples'];relays=[s['relay'] for s in samples if 'relay' in s]
 print(json.dumps({'board':board,'samples':len(samples),'relay_states':dict(collections.Counter(str((r.get('available'),r.get('enabled'),r.get('active'),r.get('test_enabled'),r.get('error'))) for r in relays)),
 'sequence_delta':relays[-1]['sequence']-relays[0]['sequence'],'max_age':max(r.get('age_s') or 0 for r in relays),'errors':sum('error' in s for s in samples)}),flush=True)
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--duration',type=int,default=45);parser.add_argument('--phase',default='before');parser.add_argument('--board',choices=HOSTS);args=parser.parse_args()
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda b:one(b,args.duration,args.phase),(args.board,) if args.board else ('S10','S12','S13')))
