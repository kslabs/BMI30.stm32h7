"""Verify the portal-saved window survives an actual core service restart."""
import concurrent.futures
import json
from read_runtime import WORK,s07,m02

REMOTE='''
import json,pathlib,subprocess,time,urllib.request
d=pathlib.Path('/home/techaid/Documents')
def api(path):return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/'+path,timeout=5))
config=d/'host/bmi30_config.json';before=config.read_bytes();c=json.loads(before)
assert c['rpi_avg_n']==8 and c['operation']['rpi_avg_n']==8
assert api('status')['mode']['rpi_avg_n']==8
subprocess.run(['systemctl','restart','bmi30-core'],check=True)
for i in range(60):
 try:
  s=api('status')
  if s.get('ok') and not s.get('stale') and s['mode']['stream_mode']==2 and s['mode']['rpi_avg_n']==8 and s['averaging']['counts']==[8]*4 and s['detector']['worker']['results']>0:
   break
 except Exception:pass
 time.sleep(.2)
else:raise RuntimeError('Saved window did not become ready after restart')
assert config.read_bytes()==before,'Configuration changed during restart'
phase=api('tx-phase')['phase_adaptation']
print(json.dumps({'saved_depth':8,'after_restart':s['mode'],'averaging':s['averaging'],'connection':s['connection'],
 'worker':s['detector']['worker'],'phase_adaptation':phase,'config_unchanged':True}))
'''

def verify(board):
    result=(s07 if board=='S07' else m02)(REMOTE,timeout=35)
    (WORK/board/'restart-verification.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({'board':board,**result}))

if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(verify,('S07','M02')))
