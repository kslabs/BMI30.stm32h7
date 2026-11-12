"""Observe optical bindings, RS485 option lists and group telemetry without writes."""
import argparse
import concurrent.futures
import json
from pathlib import Path

from audit_stability import HOSTS
from deploy import remote

WORK=Path(__file__).resolve().parents[2]/'.tmp/group-display-20261005'
SCRIPT=r'''
import json,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin)
assert subprocess.check_output(['hostname'],text=True).strip()==p['host']
def api(port):
 with urllib.request.urlopen('http://127.0.0.1'+port+'/api/status',timeout=2) as r:return json.load(r)
keys=('available','reaction_enabled','neighbor_reaction_enabled','neighbor_device_id','reaction_source','reaction_local_active','reaction_neighbor_active','led_pattern_actual','led_pattern_actual_age_s','led_pattern_selected','led_output_active','led_indication_enabled')
samples=[];end=time.monotonic()+p['duration']
while time.monotonic()<end:
 start=time.monotonic();now=time.time();s={'time':now}
 try:
  c=api(':8765');s['core_optic']={k:c.get('optic',{}).get(k) for k in keys}
  s['core_keys']=list(c.get('optic',{}))
  s['connection']=c.get('connection');s['core_ok']=c.get('ok');s['stale']=c.get('stale')
  s['stream_stats']=c.get('stream_stats');s['worker_alive']=c.get('detector',{}).get('worker',{}).get('alive')
  cache=json.loads(pathlib.Path('/tmp/bmi30_device_state.json').read_text())
  s['cache_age']=now-cache.get('cache_written_at',0)
  s['event_optic']={k:v for k,v in cache.get('events',{}).get('optic_state',{}).items() if 'neighbor' in k or 'reaction' in k or 'hold' in k}
  s['rs485_sync']=cache.get('events',{}).get('sync_state');s['sensor_map']=cache.get('events',{}).get('sensor_map')
  s['remote_sensors']=[{k:n.get(k) for k in ('node_id','online','master','rpi_id','stm32_id','optic_active','age_s')} for n in cache.get('sensors',{}).get('remote',[])]
  g=json.loads(pathlib.Path('/run/bmi30-network/group.json').read_text())
  s['group_age']=now-g.get('checked_at',0)
  s['nodes']=[{'id':n['id'],'age':round(now-n['issued'],3),'available':n.get('available'),
              'optic':{k:n.get('optic',{}).get(k) for k in keys},'neighbors':n.get('neighbors'),
              'sync':n.get('sync'),'hold_ds':n.get('hold_ds'),'sensors':n.get('sensors')} for n in g.get('nodes',[])]
 except Exception as e:s['error']=type(e).__name__
 samples.append(s);time.sleep(max(.01,.25-(time.monotonic()-start)))
portal=api('')
print(json.dumps({'board':p['board'],'host':p['host'],'samples':samples,
 'portal_keys':list(portal),'device_sensors':portal.get('device_sensors')}))
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--duration',type=int,default=90);parser.add_argument('--summary',action='store_true');parser.add_argument('--phase',default='before',choices=('before','after','final','led-final'));args=parser.parse_args()
    if args.summary:
        import collections
        for board in HOSTS:
            data=json.loads((WORK/(board+'-'+args.phase+'.json')).read_text())
            result={'board':board,'core_values':{},'nodes':{}}
            for key in ('reaction_enabled','neighbor_reaction_enabled','neighbor_device_id'):
                result['core_values'][key]=dict(collections.Counter(json.dumps(s.get('core_optic',{}).get(key)) for s in data['samples']))
            for ident in HOSTS.values():
                nodes=[n for s in data['samples'] for n in s.get('nodes',[]) if n['id']==ident]
                values={key:dict(collections.Counter(json.dumps(n.get('optic',{}).get(key)) for n in nodes)) for key in ('reaction_enabled','neighbor_reaction_enabled','neighbor_device_id')}
                values['neighbors']=dict(collections.Counter(json.dumps(n['neighbors'],sort_keys=True) for n in nodes))
                values['availability']=dict(collections.Counter(str(n['available']) for n in nodes))
                result['nodes'][ident]=values
            (WORK/(board+'-'+args.phase+'-summary.json')).write_text(json.dumps(result,indent=2))
            print(json.dumps(result),flush=True)
        return
    WORK.mkdir(parents=True,exist_ok=True)
    source=remote(HOSTS['S13'],"from pathlib import Path; print(Path('/usr/local/bin/bmi30-hotspot-info-server.py').read_text())")
    (WORK/('portal-'+args.phase+'.py')).write_bytes(source)
    def one(item):
        board,host=item
        result=json.loads(remote(host,SCRIPT,{'board':board,'host':host,'duration':args.duration},timeout=args.duration+25))
        (WORK/(board+'-'+args.phase+'.json')).write_text(json.dumps(result,indent=2))
        changes=[];old=None
        for s in result['samples']:
            state={n['id']:{'available':n['available'],'optic':n['optic'],'neighbors':n['neighbors'],'hold_ds':n['hold_ds']} for n in s.get('nodes',[])}
            if state!=old:
                changes.append({'time':s['time'],'changed':[k for k in set(state)|set(old or {}) if state.get(k)!=(old or {}).get(k)]})
            old=state
        print(json.dumps({'board':board,'samples':len(result['samples']),'telemetry_updates':len(changes)}),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(one,HOSTS.items()))


if __name__=='__main__':main()
