import json,pathlib,time,urllib.error,urllib.request
P=pathlib.Path;cfgpath=P('/home/techaid/Documents/host/bmi30_config.json');cfg_before=cfgpath.read_bytes()
def api(path,payload=None):
 req=urllib.request.Request('http://127.0.0.1:8765/api/'+path,data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
 try:return json.load(urllib.request.urlopen(req,timeout=5))
 except urllib.error.HTTPError as e:return json.load(e)
before=api('status');stm=before['mode']['avg_n'];old=before['mode']['rpi_avg_n'];results=[]
try:
 for depth in range(9,13):
  reply=api('command',{'action':'avg','params':{'avg_n':stm,'rpi_avg_n':depth}})
  assert reply.get('ok') and reply['rpi_avg_n']==depth,reply
  deadline=time.monotonic()+8
  while time.monotonic()<deadline:
   s=api('status');a=s['averaging'];w=s['detector']['worker']
   if s['mode']['rpi_avg_n']==depth and a['counts']==[depth]*4 and w['results']>0:break
   time.sleep(.08)
  else:raise RuntimeError('Window did not fill: '+str(s['averaging']))
  first=w['results'];time.sleep(.4);s=api('status')
  assert s['detector']['worker']['results']>first and s['detector']['worker']['overflows']==0
  results.append({'depth':depth,'counts':s['averaging']['counts'],'memory_bytes':s['averaging']['bytes']})
 reply=api('command',{'action':'avg','params':{'avg_n':stm,'rpi_avg_n':13}})
 assert not reply.get('ok') and api('status')['mode']['rpi_avg_n']==12
finally:
 current=cfgpath.read_bytes()
 if current!=cfg_before:
  saved=json.loads(current);stm=saved['avg_n'];old=saved['rpi_avg_n']
 reply=api('command',{'action':'avg','params':{'avg_n':stm,'rpi_avg_n':old}});assert reply.get('ok')
time.sleep(.4);after=api('status')
delta={k:after['stream_stats'][k]-before['stream_stats'][k] for k in ['seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail']}
assert all(v==0 for v in delta.values()),delta
print(json.dumps({'tested':results,'rejects_13':True,'stream_errors_delta':delta,'restored_mode':after['mode'],
 'config_unchanged':cfgpath.read_bytes()==cfg_before}))
