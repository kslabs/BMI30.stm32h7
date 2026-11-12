"""Bounded real Wi-Fi/NAT trial, restoring both radios and profile bytes."""
import base64
import json
from pathlib import Path
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from live_menu import remote, WORK

COMMON = r'''
import pathlib,json,sys,time,hashlib,subprocess,os,configparser
P=pathlib.Path;root=P(sys.argv[1]);p=json.loads((root/'input.json').read_text())
sys.path.insert(0,'/opt/bmi30-network');import network
profile_dir=P('/etc/NetworkManager/system-connections')
saved={f:f.read_bytes() for f in profile_dir.iterdir() if f.is_file()}
uuids=set(network.run('nmcli','-g','UUID','connection','show').splitlines())
meta={f:(f.stat().st_mode&0o777,f.stat().st_uid,f.stat().st_gid) for f in saved}
busy=P('/run/bmi30-access/busy');busy.parent.mkdir(parents=True,exist_ok=True)
old_busy=(busy.read_bytes(),busy.stat().st_mtime_ns) if busy.exists() else None
core_before=network.run('systemctl','show','bmi30-core','-p','MainPID','--value')
cfg=json.loads(P('/etc/bmi30-network/config.json').read_text());adapter=network.Network(cfg)
old=adapter.snapshot();assert not old['clients'],'Hotspot clients present; trial refused'
busy.write_text('bounded BMI30 relay verification')
result={'stage':'starting','core_pid_before':core_before};checkpoint=None;transit=None
def write(): (root/'result.json').write_text(json.dumps(result))
def restore_profiles():
 if transit and transit not in uuids:network.run('nmcli','con','delete','uuid',transit,check=False)
 for path,data in saved.items():
  if not path.is_file() or path.read_bytes()!=data:
   temporary=path.with_name(path.name+'.relay-restore');temporary.write_bytes(data)
   temporary.chmod(meta[path][0]);os.chown(temporary,meta[path][1],meta[path][2]);temporary.replace(path)
 network.run('nmcli','con','reload')
 if old_busy:
  busy.write_bytes(old_busy[0]);os.utime(busy,ns=(old_busy[1],old_busy[1]))
 else:busy.unlink(missing_ok=True)
 result['profile_hashes_restored']=all(path.is_file() and path.read_bytes()==data for path,data in saved.items())
 result['core_pid_unchanged']=network.run('systemctl','show','bmi30-core','-p','MainPID','--value')==core_before
'''

PARENT = COMMON + r'''
try:
 checkpoint=network.Checkpoint((adapter.sta,adapter.ap),timeout=180)
 network.run('nmcli','con','modify','uuid',old['ap_uuid'],'ipv4.addresses','10.77.250.1/24')
 network.run('nmcli','--wait','20','con','up','uuid',old['ap_uuid'],timeout=25)
 if adapter.active(adapter.sta)!=old['station_uuid']:
  network.run('nmcli','--wait','30','con','up','uuid',old['station_uuid'],timeout=35)
 for i in range(35):
  try:
   import urllib.request
   o=urllib.request.build_opener(urllib.request.ProxyHandler({}))
   with o.open('http://127.0.0.1:8791/status',timeout=2) as r:state=json.load(r)
   local=state.get('local') or {}
   if local.get('ap',{}).get('address')=='10.77.250.1/24' and local.get('internet',{}).get('ok'):break
  except Exception:pass
  time.sleep(1)
 else:raise RuntimeError('Parent advertisement did not become ready')
 import re
 info=network.run('iw','dev',adapter.ap,'info');match=re.search(r'channel (\d+) \((\d+) MHz\)',info)
 assert match,'AP channel missing'
 result.update(stage='ready',choice={**local['ap'],'freq':int(match[2]),'channel':int(match[1])})
 write();deadline=time.monotonic()+100
 while time.monotonic()<deadline and not (root/'restore').exists():time.sleep(1)
except Exception as e:result.update(stage='failed',error_type=type(e).__name__,error=str(e)[:240])
finally:
 try:
  if checkpoint:
   try:checkpoint.rollback()
   except Exception as e:result['checkpoint_error']=type(e).__name__
  restore_profiles()
  if adapter.active(adapter.sta)!=old['station_uuid']:
   network.run('nmcli','--wait','35','con','up','uuid',old['station_uuid'],timeout=40)
  if adapter.active(adapter.ap)!=old['ap_uuid']:
   network.run('nmcli','--wait','20','con','up','uuid',old['ap_uuid'],timeout=25)
  if adapter.active(adapter.sta)!=old['station_uuid']:
   network.run('nmcli','--wait','35','con','up','uuid',old['station_uuid'],timeout=40)
  result['parent_ap_address_restored']=adapter.snapshot()['ap']['address']==old['ap']['address']
  result['stage']='restored' if 'error_type' not in result else 'failed-restored'
 except Exception as e:result.update(stage='restore-failed',restore_error=type(e).__name__)
 write()
'''

CHILD = COMMON + r'''
try:
 import base64,types,threading,urllib.request
 from unittest.mock import patch
 protocol=types.ModuleType('original_relay_protocol');exec(base64.b64decode(p['protocol']),protocol.__dict__)
 protocol.time=types.SimpleNamespace(time=lambda:0)
 scope={'__name__':'original_relay_client','__file__':'original_relay_client.py'}
 with patch.dict(sys.modules,{'protocol':protocol}):exec(base64.b64decode(p['service']),scope)
 scope['time']=types.SimpleNamespace(time=lambda:0,monotonic=time.monotonic)
 receiver=scope['Agent'].__new__(scope['Agent']);receiver.config={'id':'BMI30-27448D72B'}
 receiver.protocol=protocol.Protocol(cfg['fleet_key']);receiver.records=protocol.Records(receiver.protocol)
 receiver.lock=threading.RLock();receiver.boot='real-relay-trial';receiver.seq=1
 receiver.local={'id':receiver.config['id'],'boot':receiver.boot,'seq':1,'issued':0,'cost':1000000,'path':[],'internet':{'ok':False}}
 receiver.records.records[receiver.config['id']]=receiver.protocol.sign(receiver.local)
 receiver.secrets={};receiver.secrets_path=root/'received-private.json';receiver.peers={}
 receiver.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 reply=receiver.contact('192.168.0.114','BMI30-D5EB8F26C',reserve=True)
 assert reply['reserved'] and reply['self']['data']['internet']['ok'],'Parent reservation refused'
 assert reply['self']['data']['ap']['address']=='10.77.250.1/24'
 secret=receiver.secrets['ap:BMI30-D5EB8F26C'];choice=p['choice']
 def verify(gateway):
  answer=receiver.contact(gateway,'BMI30-D5EB8F26C',reserve=True)
  assert answer['reserved'],'Gateway reservation failed'
  result['gateway_authenticated']=True
 before=network.run('nmcli','-g','UUID','con','show')
 # This is the actual unchanged Network.activate transaction used by routing.
 original_profile=adapter.profile
 def capture(choice,secret):
  global transit
  transit=original_profile(choice,secret);return transit
 adapter.profile=capture
 health=adapter.activate(choice,secret,verify_peer=verify,commit=False,require_internet=True)
 result.update(stage='passed',internet_through_neighbour=health['ok'],probe=health,
               ap_credentials_received_with_wrong_calendar=True,receiver_calendar=0)
except Exception as e:result.update(stage='failed',error_type=type(e).__name__)
finally:
 try:
  restore_profiles()
  if adapter.active(adapter.sta)!=old['station_uuid']:
   network.run('nmcli','--wait','35','con','up','uuid',old['station_uuid'],timeout=40)
  if adapter.active(adapter.ap)!=old['ap_uuid']:
   network.run('nmcli','--wait','20','con','up','uuid',old['ap_uuid'],timeout=25)
  result['station_restored']=adapter.active(adapter.sta)==old['station_uuid']
  result['hotspot_restored']=adapter.active(adapter.ap)==old['ap_uuid']
 except Exception as e:result.update(stage='restore-failed',restore_error=type(e).__name__)
 (root/'received-private.json').unlink(missing_ok=True)
 write()
'''

START = r'''
import pathlib,json,sys,base64,subprocess,uuid
P=pathlib.Path;p=json.load(sys.stdin);root=P('/var/tmp/bmi30-relay-trial')/uuid.uuid4().hex;root.mkdir(parents=True,mode=0o700)
(root/'worker.py').write_bytes(base64.b64decode(p.pop('code')));(root/'input.json').write_text(json.dumps(p));(root/'input.json').chmod(0o600)
unit='bmi30-relay-trial-'+uuid.uuid4().hex[:12]
subprocess.run(['systemd-run','--quiet','--collect','--unit='+unit,'--property=Nice=10','/usr/bin/python3',str(root/'worker.py'),str(root)],check=True,capture_output=True)
print(json.dumps({'root':str(root),'unit':unit}))
'''

READ = "import pathlib,json,sys;p=json.load(sys.stdin);f=pathlib.Path(p['root'])/'result.json';print(f.read_text() if f.is_file() else json.dumps({'stage':'running'}))"


def main():
    parent=remote('M10',START,{'code':base64.b64encode(PARENT.encode()).decode()},timeout=20)
    (WORK/'relay-parent-job.json').write_text(json.dumps(parent))
    state={'stage':'running'}
    for _ in range(35):
        try:state=remote('M10',READ,parent,timeout=8)
        except Exception:
            time.sleep(1)
            continue
        if state['stage'] in ('ready','failed-restored','restore-failed'):break
        time.sleep(1)
    if state['stage']!='ready':raise RuntimeError('Parent trial did not become ready')
    choice=state['choice'];choice['bssid']=choice['bssid'].lower()
    path=ROOT/'FirmwareReleases/RaspberryPi/2026-10-05-network-13/BMI30-Network-2026-10-05-network-13.tar.gz'
    with tarfile.open(path) as tar:code={n:base64.b64encode(tar.extractfile('2026-10-05-network-13/'+n+'.py').read()).decode() for n in ('protocol','service')}
    child=remote('S09',START,{'code':base64.b64encode(CHILD.encode()).decode(),'choice':choice,**code},timeout=20)
    (WORK/'relay-child-job.json').write_text(json.dumps(child))
    result=None
    for _ in range(55):
        try:
            result=remote('S09',READ,child,timeout=8)
            if result['stage']!='running':break
        except Exception:pass
        time.sleep(1)
    remote('M10',"import pathlib,json,sys;p=json.load(sys.stdin);(pathlib.Path(p['root'])/'restore').write_text('restore');print(json.dumps({'restoration_requested':True}))",parent,timeout=15)
    for _ in range(25):
        final=remote('M10',READ,parent,timeout=15)
        if final['stage'] in ('restored','failed-restored','restore-failed'):break
        time.sleep(1)
    report={'parent':final,'child':result,'scope':'S09 and M10 only; temporary AP subnet and checkpoint rollback'}
    (WORK/'real-neighbour-internet-trial.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


if __name__=='__main__':main()
