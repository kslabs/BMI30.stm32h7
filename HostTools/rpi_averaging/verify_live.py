"""Exercise all depths through the running core, restore the previous depth."""
import concurrent.futures
import json
from read_runtime import WORK, s07, m02

REMOTE = '''
import json,time,urllib.request
def api(path,payload=None):
 req=urllib.request.Request('http://127.0.0.1:8765/api/'+path,data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
 return json.load(urllib.request.urlopen(req,timeout=5))
before=api('status');old=before['mode']['rpi_avg_n'];stm=before['mode']['avg_n'];results=[]
try:
 for depth in range(1,9):
  reply=api('command',{'action':'avg','params':{'avg_n':stm,'rpi_avg_n':depth}})
  assert reply.get('ok') and reply['rpi_avg_n']==depth,reply
  deadline=time.monotonic()+6
  while time.monotonic()<deadline:
   s=api('status');a=s['averaging'];w=s['detector']['worker']
   if s['mode']['rpi_avg_n']==depth and (depth==1 or a['counts']==[depth]*4) and w['results']>0 and w['request_queue']==0 and w['result_queue']==0:break
   time.sleep(.08)
  else:raise RuntimeError('Window did not fill: '+str(s['averaging']))
  first=s;time.sleep(.45);s=api('status')
  assert s['detector']['worker']['results']>first['detector']['worker']['results']
  assert s['mode']['stream_mode']==2 and s['mode']['base_buf_len']==200
  assert s['connection']['connected'] and s['connection']['last_frame_age_s']<1
  results.append({'depth':depth,'averaging':s['averaging'],'worker':s['detector']['worker'],'fps':s['fps']})
finally:
 api('command',{'action':'avg','params':{'avg_n':stm,'rpi_avg_n':old}})
time.sleep(.5);after=api('status')
keys=['seq_gap_ch0','seq_gap_ch1','crc_bad','host_rx_ack_fail']
delta={k:after['stream_stats'][k]-before['stream_stats'][k] for k in keys}
assert all(v==0 for v in delta.values()),delta
print(json.dumps({'depths':results,'stream_errors_delta':delta,'restored_mode':after['mode'],'sync':after['sync']}))
'''

def verify(board):
    result=(s07 if board=='S07' else m02)(REMOTE,timeout=60)
    (WORK/board/'live-verification.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({'board':board,'depths':[r['depth'] for r in result['depths']],
                      'stream_errors_delta':result['stream_errors_delta'],'restored_mode':result['restored_mode']}))

if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(verify,('S07','M02')))
