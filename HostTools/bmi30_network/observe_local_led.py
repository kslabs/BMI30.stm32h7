"""Read-only comparison of each owner's detector and physical LED commands."""
import argparse, collections, concurrent.futures, json
from pathlib import Path
from audit_stability import HOSTS
from deploy import remote
WORK = Path(__file__).resolve().parents[2] / '.tmp/led-pattern-20261005'
SCRIPT = r'''
import json,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin)
assert subprocess.check_output(['hostname'],text=True).strip()==p['host']
samples=[];end=time.monotonic()+p['duration']
while time.monotonic()<end:
 start=time.monotonic();s={'time':time.time()}
 try:
  with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=2) as r:c=json.load(r)
  cache=json.loads(pathlib.Path('/tmp/bmi30_device_state.json').read_text())
  detector=c.get('detector') or {}
  s.update(optic=c.get('optic'),detector={k:detector.get(k) for k in ('hold0','hold1','fire_seq0','fire_seq1','last_fire_age0_s','last_fire_age1_s')},host_detection=cache.get('host_detection'),
    stream_stats=c.get('stream_stats'),connection=c.get('connection'),ok=c.get('ok'),stale=c.get('stale'))
  g=json.loads(pathlib.Path('/run/bmi30-network/group.json').read_text())
  s['nodes']=[{k:n.get(k) for k in ('id','available','issued','optic','sensors','relay')} for n in g['nodes']]
 except Exception as e:s['error']=str(e)
 samples.append(s);time.sleep(max(.01,.2-(time.monotonic()-start)))
print(json.dumps({'board':p['board'],'samples':samples}))
'''
def one(board,phase,duration):
    data=json.loads(remote(HOSTS[board],SCRIPT,{'board':board,'host':HOSTS[board],'duration':duration},timeout=duration+25))
    WORK.mkdir(exist_ok=True)
    (WORK/(board+'-led-observation-'+phase+'.json')).write_text(json.dumps(data,indent=2))
    counts=collections.Counter()
    for s in data['samples']:
        if 'error' in s: counts['error']+=1;continue
        o=s['optic']; state=o.get('led_state') or {}; h=s.get('host_detection') or {}
        own=bool(state.get('local_upper') or state.get('local_lower'))
        led=bool(o.get('led_pattern_commanded'))
        simple=bool((o.get('non_addressable_led') or {}).get('level'))
        counts['local_on' if own else 'local_off']+=1
        counts['strip_on' if led else 'strip_off']+=1
        counts['gpio_on' if simple else 'gpio_off']+=1
        if (led or simple) and not own:
            counts['outputs_without_local']+=1
            counts['event:'+str(o.get('led_event'))]+=1
        if h.get('detadc1') or h.get('detadc2'):counts['host_detection_on']+=1
    print(json.dumps({'board':board,'samples':len(data['samples']),'counts':counts,
      'fires':[data['samples'][0].get('detector'),data['samples'][-1].get('detector')]}),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',default='before');p.add_argument('--duration',type=int,default=45);a=p.parse_args()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(lambda b:one(b,a.phase,a.duration),HOSTS))
