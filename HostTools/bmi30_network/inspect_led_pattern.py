"""Read-only LED source and portal diagnostics from all acquisition owners."""
import concurrent.futures,json
from pathlib import Path
from audit_stability import HOSTS
from deploy import remote
WORK=Path(__file__).resolve().parents[2]/'.tmp/led-pattern-20261005'
SCRIPT=r'''
import hashlib,json,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin);assert subprocess.check_output(['hostname'],text=True).strip()==p['host']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:8765/api/status',timeout=3) as r:core=json.load(r)
with opener.open('http://127.0.0.1/api/status',timeout=8) as r:portal=json.load(r);headers=dict(r.headers)
g=json.loads(pathlib.Path('/run/bmi30-network/group.json').read_text())
source=pathlib.Path('/usr/local/bin/bmi30-hotspot-info-server.py').read_bytes()
print(json.dumps({'board':p['board'],'time':time.time(),'core_optic':core.get('optic'),
 'group':g,'device':portal.get('device_sensors'),'headers':headers,
 'portal_versions':{k:v for k,v in portal.items() if 'firmware' in k},
 'portal_sha256':hashlib.sha256(source).hexdigest(),
 'browser_processes':subprocess.run(['pgrep','-a','-f','^/usr/lib/chromium/chromium|^/usr/bin/firefox'],capture_output=True,text=True).stdout}))
'''
def one(board):
    data=json.loads(remote(HOSTS[board],SCRIPT,{'board':board,'host':HOSTS[board]},timeout=30))
    WORK.mkdir(exist_ok=True);(WORK/(board+'-before.json')).write_text(json.dumps(data,indent=2))
    print(json.dumps({'board':board,'sha':data['portal_sha256'],'versions':data['portal_versions'],
      'nodes':[{'id':n['id'],'available':n['available'],'optic':n.get('optic')} for n in data['group']['nodes']]}),flush=True)
if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(one,HOSTS))
