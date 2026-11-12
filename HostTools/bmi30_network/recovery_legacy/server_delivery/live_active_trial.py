"""One bounded radio trial; installed working firmware is never changed."""
import base64,json,sys,tarfile,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(HERE))
from live_menu import remote,WORK
from live_relay_trial import START,READ

PARENT=r'''
import pathlib,json,sys,base64,types,threading,time,urllib.request
from unittest.mock import patch
from email.utils import formatdate
P=pathlib.Path;root=P(sys.argv[1]);p=json.loads((root/'input.json').read_text())
sys.path.insert(0,'/opt/bmi30-network');import network
cfg=json.loads(P('/etc/bmi30-network/config.json').read_text());adapter=network.Network(cfg)
old=adapter.snapshot();assert not old['clients'];result={'stage':'starting'}
core=network.run('systemctl','show','bmi30-core','-p','MainPID','--value')
profiles={str(f):f.read_bytes() for f in P('/etc/NetworkManager/system-connections').iterdir() if f.is_file()}
proto=types.ModuleType('actual_old_protocol');exec(base64.b64decode(p['protocol']),proto.__dict__)
proto.time=types.SimpleNamespace(time=lambda:0)
scope={'__name__':'actual_old_receiver','__file__':'actual_old_receiver.py'}
with patch.dict(sys.modules,{'protocol':proto}):exec(base64.b64decode(p['service']),scope)
scope['time']=types.SimpleNamespace(time=lambda:0,monotonic=time.monotonic)
a=scope['Agent'].__new__(scope['Agent']);a.config={'id':'BMI30-27448D72B'}
a.protocol=proto.Protocol(cfg['fleet_key']);a.records=proto.Records(a.protocol);a.lock=threading.RLock()
a.boot='actual-old-radio-receiver';a.seq=1;a.local={'id':a.config['id'],'boot':a.boot,'seq':1,'issued':0,'internet':{'ok':False},'path':[],'cost':1000000}
a.records.records[a.config['id']]=a.protocol.sign(a.local);a.secrets={};a.secrets_path=root/'private-received.json';a.peers={};a.hold_until=0;a.switching=False
a.state={'version':'0.1.13','local':a.local,'checked_at':0};a.group=types.SimpleNamespace()
scope['BaseHTTPRequestHandler'].date_time_string=lambda *_:formatdate(0,usegmt=True)
base=scope['ThreadingHTTPServer']
class Bounded(base):
 def __init__(self,*args,**kwargs):
  super().__init__(*args,**kwargs);self.timeout=1
 def serve_forever(self):
  result.update(stage='ready',ap=old['ap']);(root/'result.json').write_text(json.dumps(result));deadline=time.monotonic()+240
  while time.monotonic()<deadline and not (root/'stop').exists():self.handle_request()
  self.server_close()
scope['ThreadingHTTPServer']=Bounded;scope['PORT']=8793
try:scope['serve'](a)
finally:
 result.update(stage='finished',received_wifi_count=sum(k.startswith('wifi:') for k in a.secrets),
   credentials_persisted=a.secrets_path.is_file(),core_pid_unchanged=network.run('systemctl','show','bmi30-core','-p','MainPID','--value')==core,
   profile_bytes_unchanged=all(P(f).is_file() and P(f).read_bytes()==v for f,v in profiles.items()))
 a.secrets_path.unlink(missing_ok=True);(root/'result.json').write_text(json.dumps(result))
'''

CHILD=r'''
import pathlib,json,sys,base64,types,threading,urllib.request,hashlib
P=pathlib.Path;root=P(sys.argv[1]);p=json.loads((root/'input.json').read_text())
sys.path.insert(0,'/opt/bmi30-network');import network,protocol
cfg=json.loads(P('/etc/bmi30-network/config.json').read_text());cfg['id']='BMI30-6922A5A0D'
adapter=network.Network(cfg);old=adapter.snapshot();assert not old['clients']
core=network.run('systemctl','show','bmi30-core','-p','MainPID','--value')
profiles={str(f):f.read_bytes() for f in P('/etc/NetworkManager/system-connections').iterdir() if f.is_file()}
legacy=types.ModuleType('trial_legacy_recovery');exec(base64.b64decode(p['legacy']),legacy.__dict__)
sys.modules['legacy_recovery']=legacy
active_module=types.ModuleType('trial_active_recovery');exec(base64.b64decode(p['active']),active_module.__dict__)
a=types.SimpleNamespace(config=cfg,network=adapter,protocol=protocol.Protocol(cfg['fleet_key']),lock=threading.RLock(),
 boot='bounded-outbound-test',seq=1,local=old,switching=False,opener=urllib.request.build_opener(urllib.request.ProxyHandler({})))
path=P(cfg.get('state_dir','/var/lib/bmi30-network'))/'credentials.json';a.secrets=json.loads(path.read_text())
recovery=legacy.LegacyRecovery(a);active=active_module.ActiveRecovery(recovery);active.port=8793
result={'stage':'starting','original_station_uuid':old['station_uuid']}
try:
 peers=cfg.get('peers',[]);peer=next((v for v in peers if v.get('id')=='BMI30-D5EB8F26C'),None)
 secret=(peer or {}).get('psk') or (a.secrets.get('ap:BMI30-D5EB8F26C') or {}).get('psk');assert secret
 visible=adapter.visible(rescan=True);choice=next(v for v in visible if v['bssid'].lower()==p['ap']['bssid'].lower())
 active.visit(choice,secret)
 result.update(stage='passed',recovery=active.snapshot())
except Exception as error:result.update(stage='failed',error_type=type(error).__name__,recovery=active.snapshot())
finally:
 result.update(core_pid_unchanged=network.run('systemctl','show','bmi30-core','-p','MainPID','--value')==core,
   profile_bytes_unchanged=all(P(f).is_file() and P(f).read_bytes()==v for f,v in profiles.items()),
   profile_file_set_unchanged=set(profiles)=={str(f) for f in P('/etc/NetworkManager/system-connections').iterdir() if f.is_file()},
   station_restored=adapter.active(adapter.sta)==old['station_uuid'],hotspot_restored=adapter.active(adapter.ap)==old['ap_uuid'])
 (root/'result.json').write_text(json.dumps(result))
'''

def main():
 archive=ROOT/'FirmwareReleases/RaspberryPi/2026-10-05-network-13/BMI30-Network-2026-10-05-network-13.tar.gz'
 with tarfile.open(archive) as tar:
  code={n:base64.b64encode(tar.extractfile('2026-10-05-network-13/'+n+'.py').read()).decode() for n in ('service','protocol')}
 parent=remote('M10',START,{'code':base64.b64encode(PARENT.encode()).decode(),**code},timeout=20)
 (WORK/'active-trial-parent-job.json').write_text(json.dumps(parent))
 state=None
 for _ in range(12):
  state=remote('M10',READ,parent,timeout=10)
  if state.get('stage')=='ready':break
  time.sleep(1)
 assert state and state.get('stage')=='ready'
 payload={'code':base64.b64encode(CHILD.encode()).decode(),'ap':state['ap']}
 for name in ('legacy','active'):
  payload[name]=base64.b64encode((HERE.parent/(name+'_recovery.py')).read_bytes()).decode()
 child=remote('S09',START,payload,timeout=20)
 (WORK/'active-trial-child-job.json').write_text(json.dumps(child))
 result=None
 for _ in range(22):
  try:
   result=remote('S09',READ,child,timeout=8)
   if result.get('stage')!='running':break
  except Exception:pass
  time.sleep(2)
 remote('M10',"import pathlib,json,sys;p=json.load(sys.stdin);(pathlib.Path(p['root'])/'stop').write_text('stop');print(json.dumps({'stop':True}))",parent,timeout=15)
 time.sleep(2)
 final=remote('M10',READ,parent,timeout=12)
 report={'parent':final,'child':result,'scope':'Original v1 receiver in a temporary M10 process; real S09 radio; installed firmware unchanged.'}
 (WORK/'active-real-radio-trial.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
