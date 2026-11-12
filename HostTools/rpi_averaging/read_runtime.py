"""Read current runtime and settings using the existing pinned SSH connections."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.tmp/rpi-averaging-20261013'
sys.path.insert(0, str(ROOT / '.tmp/tx-phase-repair-20261012'))
from s07_remote import remote as s07
sys.path.insert(0, str(ROOT / '.tmp/new-device-20261009'))
from device_remote import remote as m02

REMOTE = '''
import hashlib,json,pathlib,shlex,subprocess,urllib.request
d=pathlib.Path('/home/techaid/Documents');a=d/'host/bmi30_active_runtime/project'
def api(port,path):
 try:return json.load(urllib.request.urlopen('http://127.0.0.1:'+str(port)+path,timeout=6))
 except Exception as e:return {'error':str(e)}
env_path=d/'host/bmi30_split_active_version.env';raw=env_path.read_text()
env={k:shlex.split(v)[0] for line in raw.splitlines() if line and not line.startswith('#') and '=' in line for k,v in [line.split('=',1)]}
sources={};paths={};source_sha256={}
for name,key in [('core','BMI30_CORE_PATH'),('engine','BMI30_ENGINE_SOURCE')]:
 f=pathlib.Path(env[key]);f=f if f.is_absolute() else d/f
 paths[name]=str(f.relative_to(a));sources[name]=f.read_text();source_sha256[name]=hashlib.sha256(f.read_bytes()).hexdigest()
for name,f in [('portal',a/'hotspot_info_server.py'),('installed_portal',pathlib.Path('/usr/local/bin/bmi30-hotspot-info-server.py'))]:sources[name]=f.read_text();source_sha256[name]=hashlib.sha256(f.read_bytes()).hexdigest()
for f in (a/'host').glob('bmi30_*.py'):sources['host/'+f.name]=f.read_text()
config=json.loads((d/'host/bmi30_config.json').read_text())
print(json.dumps({'hostname':subprocess.check_output(['hostname'],text=True).strip(),'env':raw,'paths':paths,
 'root':subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip(),
 'status':api(8765,'/api/status'),'portal':api(80,'/api/status'),'config':config,'sources':sources,'source_sha256':source_sha256}))
'''

def inspect(board):
    result = (s07 if board == 'S07' else m02)(REMOTE, timeout=35)
    folder = WORK / board
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'inspection.json').write_text(json.dumps({k:v for k,v in result.items() if k!='sources'}, indent=2), encoding='utf8')
    for name, raw in result['sources'].items():
        path = folder / 'source' / (name+'.py' if '/' not in name else name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding='utf8', newline='\n')
    print(json.dumps({'board':board,'hostname':result['hostname'],'root':result['root'],'env':result['env'],
                     'mode':result['status'].get('mode'),'worker':result['status'].get('detector',{}).get('worker'),
                     'hashes':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in result['sources'].items() if '/' not in k}}))

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(inspect, ('S07','M02')))
