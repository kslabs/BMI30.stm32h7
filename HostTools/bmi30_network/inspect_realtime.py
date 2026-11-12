"""Inspect live event providers without opening USB or changing settings."""
import concurrent.futures
import json
from pathlib import Path
from audit_stability import HOSTS
from deploy import remote
WORK=Path(__file__).resolve().parents[2]/'.tmp/group-realtime-20261005'
SCRIPT='''
import importlib.util,json,pathlib,subprocess,time,urllib.request
assert subprocess.check_output(['hostname'],text=True).strip()==HOST
with urllib.request.urlopen('http://127.0.0.1:8765/api/status') as r:c=json.load(r)
cache=json.loads(pathlib.Path('/tmp/bmi30_device_state.json').read_text())
core=pathlib.Path('/home/techaid/Documents/host/bmi30_active_runtime/project/host/BMI30.001.py.2026-09-30-1650').read_text()
print(json.dumps({'modules':{n:bool(importlib.util.find_spec(n)) for n in ('websockets','aiohttp','wsproto','cryptography')},
 'cache_age':time.time()-cache.get('cache_written_at',0), 'cache_local':cache.get('sensors',{}).get('local'),
 'core_sync':c.get('sync'), 'detector':c.get('detector'), 'core_source':core,
 'root':subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip()}))
'''
if __name__=='__main__':
    WORK.mkdir(exist_ok=True)
    def one(item):
        board,host=item
        data=json.loads(remote(host,SCRIPT.replace('HOST',repr(host))))
        core=data.pop('core_source');(WORK/(board+'-core.py')).write_text(core,encoding='utf-8')
        (WORK/(board+'-inspect.json')).write_text(json.dumps(data,indent=2))
        print(json.dumps({'board':board,'modules':data['modules'],'cache_age':data['cache_age'],'local':data['cache_local']}),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(one,HOSTS.items()))
