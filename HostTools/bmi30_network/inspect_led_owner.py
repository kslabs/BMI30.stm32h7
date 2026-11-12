"""Identify the live engine and detector state without accessing private settings."""
import concurrent.futures,json
from pathlib import Path
from audit_stability import HOSTS
from deploy import remote
WORK=Path(__file__).resolve().parents[2]/'.tmp/led-pattern-20261005'
SCRIPT=r'''
import hashlib,json,pathlib,subprocess,sys,urllib.request
p=json.load(sys.stdin);assert subprocess.check_output(['hostname'],text=True).strip()==p['host']
with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3) as r:c=json.load(r)
path=pathlib.Path(c['service']['engine_source'])
print(json.dumps({'board':p['board'],'service':c['service'],'engine_realpath':str(path.resolve()),
 'engine_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
 'engine_mtime':path.stat().st_mtime,
 'systemd_start':subprocess.check_output(['systemctl','show','bmi30-core','-p','ExecMainStartTimestamp','-p','ExecMainStartTimestampMonotonic'],text=True),
 'monotonic_s':__import__('time').monotonic(),'detector':c.get('detector'),
 'optic':c.get('optic')}))
'''
def one(board):
    data=json.loads(remote(HOSTS[board],SCRIPT,{'board':board,'host':HOSTS[board]},timeout=20))
    (WORK/(board+'-engine-owner.json')).write_text(json.dumps(data,indent=2))
    print(json.dumps({k:v for k,v in data.items() if k not in ('optic','detector')}),flush=True)
if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(one,HOSTS))
