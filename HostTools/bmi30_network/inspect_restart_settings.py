import json
from inspect_realtime import WORK
from audit_stability import HOSTS
from deploy import remote
SCRIPT=r'''
import hashlib,json,pathlib,subprocess,sys,urllib.request
p=json.load(sys.stdin);d=pathlib.Path('/home/techaid/Documents');result=[]
for name in ('bmi30_config.json','bmi30_sel.json','bmi30_relay.json'):
 f=d/'host'/name;live=f.read_bytes();prior=p['hashes'][name]
 candidates=[]
 for base in (d/'host/bmi30_split_bundles',d/'.tmp',pathlib.Path('/var/backups')):
  for other in base.rglob(name):
   if other.is_file() and hashlib.sha256(other.read_bytes()).hexdigest()==prior:
    candidates.append(str(other))
    if len(candidates)>=3:break
  if candidates:break
 diffs=[]
 if candidates:
  old=json.loads(pathlib.Path(candidates[0]).read_bytes());new=json.loads(live)
  def compare(a,b,path=''):
   if isinstance(a,dict) and isinstance(b,dict):
    for k in a.keys()|b.keys():compare(a.get(k),b.get(k),path+'/'+k)
   elif a!=b:diffs.append({'path':path,'old_type':type(a).__name__,'new_type':type(b).__name__})
  compare(old,new)
 result.append({'name':name,'changed':hashlib.sha256(live).hexdigest()!=prior,'prior_copies':candidates,'differences':diffs})
with urllib.request.urlopen('http://127.0.0.1:8765/api/status') as r:core=json.load(r)
print(json.dumps({'settings':result,'core_ok':core['ok'],'connected':core['connection']['connected'],
 'version':json.loads(pathlib.Path('/opt/bmi30-network/release.json').read_text())['version']}))
'''
if __name__=='__main__':
 board='S13';before=json.loads((WORK/(board+'-inventory-before.json')).read_text())
 active=next(r for r in before['roots'] if r['active'])
 result=json.loads(remote(HOSTS[board],SCRIPT,{'hashes':active['settings_hashes']},timeout=100))
 (WORK/'restart-settings-inspection.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
