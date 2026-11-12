"""Bounded M02/S07 radio trial; original v1 receiver and working firmware intact.

No installation, reboot, MCU command, boot setting, or persistent test service.
The temporary receiver stores only a dummy test credential. Device AP keys stay
on the donor. Positive serial/root/Core/service guards precede every mutation.
"""
import ast,base64,hashlib,json,sys,tarfile,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
WORK=ROOT/'.tmp/current-client-migration-20261027'
sys.path.insert(0,str(HERE))
import live_active_trial as template
sys.path.insert(0,str(ROOT/'.tmp/hotspot-media-20261021'))
from control import remote

GUARD=r'''
import pathlib,json,sys,subprocess,hashlib
P=pathlib.Path
root=P(sys.argv[1]);p=json.loads((root/'input.json').read_text());expected=p['expected']
assert P('/proc/device-tree/serial-number').read_text().strip('\0\n')==expected['serial']
assert subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip()==expected['root']
assert hashlib.sha256(P('/opt/bmi30-network/service.py').read_bytes()).hexdigest()==expected['network_sha256']
assert subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()==expected['core_pid']
'''

PARENT=GUARD+template.PARENT.replace("a.config={'id':'BMI30-27448D72B'}", "a.config={'id':'BMI30-C9775FBAE'}").replace("scope['PORT']=8793", "scope['PORT']=18891")

CHILD=GUARD+template.CHILD
CHILD=CHILD.replace("cfg['id']='BMI30-6922A5A0D'", "cfg['id']='BMI30-E29805C7C'")
CHILD=CHILD.replace("a.secrets=json.loads(path.read_text())", "enrolled=json.loads(path.read_text());a.secrets={'wifi:bmi30-recovery-radio-test':{'ssid':'bmi30-recovery-radio-test','psk':'dummy-radio-test-only','security':'wpa-psk','origin':cfg['id'],'updated':2000000000}}")
CHILD=CHILD.replace("active=active_module.ActiveRecovery(recovery);active.port=8793", "active_module.ACTIVE_DONOR=cfg['id'];active_module.ACTIVE_TARGET='BMI30-C9775FBAE';active_module.TARGET_SSIDS=(p['ap']['ssid'],'BMI30-ABDD2DBC9775FBAE');active=active_module.ActiveRecovery(recovery);active.port=18891;active.state['attempts']=1")
CHILD=CHILD.replace("'BMI30-D5EB8F26C'", "'BMI30-C9775FBAE'")
CHILD=CHILD.replace("a.secrets.get('ap:BMI30-D5EB8F26C')", "enrolled.get('ap:BMI30-C9775FBAE')")
CHILD=CHILD.replace("visible=adapter.visible(rescan=True);choice=next(v for v in visible if v['bssid'].lower()==p['ap']['bssid'].lower())\n active.visit(choice,secret)", "active.refresh_target_scan=lambda adapter:None\n active.visit(None,secret)")
assert 'active.visit(None,secret)' in CHILD
assert "enrolled.get('ap:BMI30-C9775FBAE')" in CHILD
for name,source in [('parent',PARENT),('child',CHILD)]:ast.parse(source,filename='lab-radio-'+name)

START=r'''
import pathlib,json,sys,base64,subprocess,uuid,hashlib
P=pathlib.Path;p=json.load(sys.stdin);expected=p['expected']
assert P('/proc/device-tree/serial-number').read_text().strip('\0\n')==expected['serial']
assert subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip()==expected['root']
assert hashlib.sha256(P('/opt/bmi30-network/service.py').read_bytes()).hexdigest()==expected['network_sha256']
assert subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()==expected['core_pid']
folder=P('/run/bmi30-recovery-radio-test')/uuid.uuid4().hex;folder.mkdir(parents=True,mode=0o700)
(folder/'worker.py').write_bytes(base64.b64decode(p.pop('code')))
(folder/'input.json').write_text(json.dumps(p));(folder/'input.json').chmod(0o600)
unit='bmi30-recovery-radio-test-'+uuid.uuid4().hex[:12]
subprocess.run(['systemd-run','--quiet','--collect','--unit='+unit,'--property=RuntimeMaxSec=180',
 '--property=TimeoutStopSec=10','/usr/bin/python3',str(folder/'worker.py'),str(folder)],check=True,capture_output=True)
print(json.dumps({'root':str(folder),'unit':unit}))
'''
READ="import pathlib,json,sys;p=json.load(sys.stdin);f=pathlib.Path(p['root'])/'result.json';print(f.read_text() if f.is_file() else json.dumps({'stage':'running'}))"
STOP="import pathlib,json,sys;p=json.load(sys.stdin);r=pathlib.Path(p['root']);assert r.parent==pathlib.Path('/run/bmi30-recovery-radio-test');(r/'stop').write_text('stop');print(json.dumps({'stop':True}))"

def prepare():
 archive=ROOT/'FirmwareReleases/RaspberryPi/2026-10-05-network-13/BMI30-Network-2026-10-05-network-13.tar.gz'
 with tarfile.open(archive) as tar:
  old={n:base64.b64encode(tar.extractfile('2026-10-05-network-13/'+n+'.py').read()).decode() for n in ('service','protocol')}
 baseline={b:json.loads((WORK/(b+'-lab-baseline.json')).read_text()) for b in ('M02','S07')}
 for board,record in baseline.items():
  assert record['internet'] and not record['hotspot_clients'] and record['core_connected'] and record['worker_alive'],board+' is not idle and healthy'
  assert record['firmware']=='2026-10-23-151427'
 parent={'code':base64.b64encode(PARENT.encode()).decode(),'expected':baseline['S07'],**old}
 child={'code':base64.b64encode(CHILD.encode()).decode(),'expected':baseline['M02'],
        **{n:base64.b64encode((HERE.parent/(n+'_recovery.py')).read_bytes()).decode() for n in ('legacy','active')}}
 plan={'scope':['M02','S07'],'receiver':'S07: original 0.1.13 server, private process clock=0, TCP 18891',
       'donor':'M02: temporary name-only Wi-Fi probe to S07, own AP down during association',
       'maximum_guard_time_s':90,'process_maximum_time_s':180,'firmware_installation':False,
       'root_media':{b:r['root'] for b,r in baseline.items()},'dummy_credentials_only':True,
       'parent_source_sha256':hashlib.sha256(PARENT.encode()).hexdigest(),'child_source_sha256':hashlib.sha256(CHILD.encode()).hexdigest()}
 (WORK/'lab-radio-trial-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
 return parent,child,plan

def main():
 parent_payload,child_payload,plan=prepare()
 if '--run' not in sys.argv:print(json.dumps(plan));return
 parent=remote('S07',START,parent_payload,timeout=20)
 (WORK/'lab-radio-parent-job.json').write_text(json.dumps(parent))
 try:
  for _ in range(12):
   state=remote('S07',READ,parent,timeout=8)
   if state.get('stage')=='ready':break
   time.sleep(1)
  else:raise RuntimeError('Temporary receiver did not become ready')
  child_payload['ap']=state['ap']
  child=remote('M02',START,child_payload,timeout=20)
  (WORK/'lab-radio-child-job.json').write_text(json.dumps(child))
  deadline=time.monotonic()+150;result={'stage':'running'}
  while time.monotonic()<deadline:
   try:
    result=remote('M02',READ,child,timeout=9)
    if result.get('stage')!='running':break
   except Exception:pass
   time.sleep(2)
 finally:remote('S07',STOP,parent,timeout=12)
 time.sleep(2)
 final=remote('S07',READ,parent,timeout=12)
 report={'plan':plan,'receiver':final,'donor':result}
 (WORK/'lab-radio-trial-result.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report))

if __name__=='__main__':main()
