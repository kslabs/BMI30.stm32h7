"""Apply only to physical M02 using its stock updater and a verified local bundle."""
import base64,json,pathlib,sys,time
ROOT=pathlib.Path(__file__).resolve().parents[2];WORK=ROOT/'.tmp/usb-recovery-20261028'
sys.path.insert(0,str(ROOT/'.tmp/hotspot-media-20261021'))
from control import remote

WORKER=r'''
import hashlib,importlib.util,json,os,pathlib,shutil,sys,time,urllib.request
P=pathlib.Path;folder=P(sys.argv[1]);payload=json.loads((folder/'input.json').read_text())
assert P('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
import subprocess
assert subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip()=='/dev/mmcblk0p2'
D=P('/home/techaid/Documents');sys.path.insert(0,str(D/'utilities'))
from bmi30_server_update import Updater
from bmi30_server_files import verify_file,env_bytes
info=payload['marker'];archive=folder/'firmware.tar.gz';verify_file(archive,info)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(port,path='/api/status',headers=None):
 request=urllib.request.Request('http://127.0.0.1:'+str(port)+path,headers=headers or {})
 with opener.open(request,timeout=8) as response:return response.status,json.load(response)
def hashfile(path):return hashlib.sha256(path.read_bytes()).hexdigest()
profiles=P('/etc/NetworkManager/system-connections')
protected={str(path):hashfile(path) for path in profiles.iterdir() if path.is_file()}
before_cfg=json.loads((D/'host/bmi30_config.json').read_text())
_,before_api=api(80);assert before_api['raspberry_firmware_version']==payload['expected_version']
assert before_api['stm32_firmware_version']=='1.2.56'
(folder/'before.json').write_text(json.dumps({'firmware':before_api['raspberry_firmware_version'],
 'stm32':before_api['stm32_firmware_version'],'profile_hashes':protected,'detector_config_sha256':hashfile(D/'host/bmi30_config.json')}))
class LocalClient:
 def latest(self):return info,env_bytes(info)
 def download(self,requested,destination,**kwargs):
  assert requested==info;verify_file(archive,info);return archive
u=Updater(D,client=LocalClient())
builder=D/'utilities/create_bmi30_split_bundle.sh';builder_before=builder.read_bytes();builder_stat=builder.stat();builder_changed=False
assert hashlib.sha256(builder_before).hexdigest()==payload['builder_sha256']
(folder/'stock-builder-before.sh').write_bytes(builder_before)
try:
 with u.lock():result=u.update()
 for _ in range(120 if payload.get('full_network') else 45):
  try:
   _,p=api(80);_,c=api(8765)
   full_ready=True
   if payload.get('full_network'):
    full_state=P('/var/lib/bmi30-legacy-menu')/info['FIRMWARE_VERSION']/'state.json'
    full_ready=full_state.is_file() and json.loads(full_state.read_text()).get('stage')=='committed'
   if (full_ready and p.get('raspberry_firmware_version')==info['FIRMWARE_VERSION'] and p.get('stm32_firmware_version')=='1.2.56'
       and c.get('ok') and not c.get('stale') and c.get('connection',{}).get('connected')
       and (c.get('detector',{}).get('worker') or {}).get('alive')):break
  except Exception:pass
  time.sleep(1)
 else:raise RuntimeError('Updated M02 did not become healthy')
 assert p['firmware_release']['portal_matches_release'],'Running portal differs from selected release'
 pin=P('/etc/systemd/system/bmi30-hotspot-info.service.d/06-bmi30-versioned-portal.conf')
 selected=D/'host/bmi30_active_runtime/project/hotspot_info_server.py'
 assert pin.is_file() and str(selected) in pin.read_text(),'Selected portal launcher not installed'
 subprocess.run(['systemctl','restart','bmi30-hotspot-info.service'],check=True,capture_output=True,timeout=20)
 for _ in range(20):
  try:
   _,p=api(80)
   if p.get('raspberry_firmware_version')==info['FIRMWARE_VERSION'] and p.get('firmware_release',{}).get('portal_matches_release'):break
  except Exception:pass
  time.sleep(.5)
 else:raise RuntimeError('Portal restart did not retain selected release')
 command=subprocess.check_output(['systemctl','show','bmi30-hotspot-info.service','-p','ExecStart','--value'],text=True)
 assert str(selected) in command,'Systemd did not launch selected portal'
 assert json.loads((D/'host/bmi30_config.json').read_text())==before_cfg,'Detector settings changed'
 assert all(P(name).is_file() and hashfile(P(name))==value for name,value in protected.items()),'Network profiles changed'
 u.progress('update',100,'Local lab firmware activated and health checked.',status='succeeded')
 active=D/'host/bmi30_active_runtime/project/host/bmi30_usb_recovery'
 sys.path.insert(0,str(active));import usb_recovery_backend as backend
 status=backend.state();assert status['source']=='BMI30-E29805C7C' and status['purpose']=='portable-emergency'
 assert 'destination' not in status and 'target_serial' not in backend.config()
 # Mint a normal short-lived portal session locally; token and password never leave M02.
 spec=importlib.util.spec_from_file_location('usb_live_portal_check',P('/usr/local/bin/bmi30-hotspot-info-server.py'))
 portal=importlib.util.module_from_spec(spec);spec.loader.exec_module(portal)
 username=portal.load_portal_auth_config()['username']
 token=portal.create_portal_session_token(username,int(time.time())+180)
 headers={'Cookie':portal.PORTAL_SESSION_COOKIE+'='+token}
 _,public=api(80,'/api/usb-recovery/status',headers)
 assert public['ok'] and public['purpose']=='portable-emergency' and 'destination' not in public
 request=urllib.request.Request('http://127.0.0.1/portal-recovery',headers=headers)
 with opener.open(request,timeout=8) as response:
  page=response.read();assert response.status==200 and 'Аварийная USB-система'.encode() in page
  assert b'S07' not in page and b'S12' not in page
 try:api(80,'/api/usb-recovery/status')
 except urllib.error.HTTPError as error:assert error.code==403
 else:raise AssertionError('Anonymous recovery access unexpectedly permitted')
 _,firmware=api(80,'/api/firmware-control',headers)
 assert firmware['rollback']['available'] and firmware['rollback']['version']==before_api['raspberry_firmware_version'],'Saved rollback not exposed by portal'
 read_request=urllib.request.Request('http://127.0.0.1/api/usb-recovery/read-stm32',data=b'{}',method='POST',
    headers={**headers,'Content-Type':'application/json','X-BMI30-USB':'1'})
 with opener.open(read_request,timeout=10) as response:stm=json.load(response)['stm32']
 assert stm['read_only'] and not stm['applied'] and stm['device_code']=='M02'
 from patch_bundle_builder import patch as patch_builder
 from bmi30_server_files import atomic_write
 candidate=patch_builder(builder_before.decode()).encode();test_file=folder/'stock-builder-candidate.sh';test_file.write_bytes(candidate)
 subprocess.run(['bash','-n',str(test_file)],check=True,capture_output=True)
 with u.lock():
  assert builder.read_bytes()==builder_before
  atomic_write(builder,candidate,builder_stat.st_mode&0o777);os.chown(builder,builder_stat.st_uid,builder_stat.st_gid);builder_changed=True
 report={'stage':'complete','version':info['FIRMWARE_VERSION'],'stm32':p['stm32_firmware_version'],
  'source':'BMI30-E29805C7C','purpose':'portable-emergency','network_profiles_unchanged':True,
  'detector_config_unchanged':True,'core_connected':True,'worker_alive':True,
  'authorized_page_http':200,'anonymous_status_http':403,'usb_detected':bool(status['disks']),
  'standard_builder_preserves_feature':True,'stm32_read_only_api_checked':True,'usb_boot_emmc_policy':'read-own-settings-if-available',
  'rollback_available':True,'rollback_version':firmware['rollback']['version'],
  'portal_matches_release':True,'versioned_launcher_verified_after_restart':True,
  'full_network_committed':bool(payload.get('full_network')),
  'archive_sha256':info['ARCHIVE_SHA256'],'usb_write_tested':False,'usb_boot_tested':False}
 (folder/'result.json').write_text(json.dumps(report))
except Exception as error:
 # The stock updater rolls back activation failures; post-check failures also
 # return its saved source version before reporting this lab attempt failed.
 try:
  if builder_changed:
   from bmi30_server_files import atomic_write
   atomic_write(builder,builder_before,builder_stat.st_mode&0o777);os.chown(builder,builder_stat.st_uid,builder_stat.st_gid)
  if u.current().get('version')!=before_api['raspberry_firmware_version']:
   with u.lock():u.rollback()
  restored=True
 except Exception:restored=False
 u.progress('update',100,'Lab checks failed; saved source restored.' if restored else 'Lab checks failed; restoration needs attention.',status='failed')
 (folder/'result.json').write_text(json.dumps({'stage':'failed','error_type':type(error).__name__,
   'error':str(error)[:160] if isinstance(error,AssertionError) else 'Lab update/check failed',
   'saved_rollback_completed':restored}))
 raise
'''

START=r'''
import base64,hashlib,json,pathlib,subprocess,sys,uuid
P=pathlib.Path;p=json.load(sys.stdin)
assert P('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
assert subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip()=='/dev/mmcblk0p2'
from urllib.request import build_opener,ProxyHandler
with build_opener(ProxyHandler({})).open('http://127.0.0.1/api/status',timeout=5) as response:current=json.load(response)
assert current['raspberry_firmware_version']==p['expected_version']
latest=P('/var/lib/bmi30-usb-recovery/latest.json')
if latest.is_file():
 job=json.loads(latest.read_text());status=P('/var/lib/bmi30-usb-recovery/jobs')/job['id']/'status.json'
 if status.is_file():assert json.loads(status.read_text()).get('stage') not in ('queued','preparing','formatting','copying','verifying','finishing')
folder=P('/var/backups/bmi30-usb-update')/(p['marker']['FIRMWARE_VERSION']+'-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True,mode=0o700,exist_ok=False)
archive=base64.b64decode(p.pop('archive'));assert hashlib.sha256(archive).hexdigest()==p['marker']['ARCHIVE_SHA256']
(folder/'firmware.tar.gz').write_bytes(archive);(folder/'worker.py').write_bytes(base64.b64decode(p.pop('worker')))
(folder/'input.json').write_text(json.dumps(p));(folder/'input.json').chmod(0o600)
unit='bmi30-usb-feature-update-'+uuid.uuid4().hex[:10]
subprocess.run(['systemd-run','--quiet','--collect','--unit='+unit,'--property=RuntimeMaxSec=600',
 '/usr/bin/python3',str(folder/'worker.py'),str(folder)],check=True,capture_output=True)
print(json.dumps({'folder':str(folder),'unit':unit,'started':True}))
'''

READ=r'''
import json,pathlib,sys,subprocess
P=pathlib.Path;p=json.load(sys.stdin)
assert P('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
folder=P(p['folder']);assert folder.parent==P('/var/backups/bmi30-usb-update')
file=folder/'result.json'
result=json.loads(file.read_text()) if file.is_file() else {'stage':'running'}
result['unit_state']=subprocess.run(['systemctl','is-active',p['unit']],capture_output=True,text=True).stdout.strip()
print(json.dumps(result))
'''

def main():
    import ast
    ast.parse(WORKER);ast.parse(START);ast.parse(READ)
    release=json.loads((WORK/'build.json').read_text(encoding='utf-8'))
    import hashlib
    builder=WORK/'bundle-builder-before.sh'
    job=remote('M02',START,{'marker':release['marker'],'expected_version':release.get('expected_source_version','2026-10-28-142027'),
      'full_network':release.get('kind')=='current-client-full',
      'builder_sha256':hashlib.sha256(builder.read_bytes()).hexdigest(),'archive':base64.b64encode(pathlib.Path(release['archive']).read_bytes()).decode(),
      'worker':base64.b64encode(WORKER.encode()).decode()},timeout=45)
    (WORK/'install-job.json').write_text(json.dumps(job,indent=2)+'\n')
    print(json.dumps(job),flush=True)
    deadline=time.monotonic()+220;last=None;result={'stage':'running'}
    while time.monotonic()<deadline:
        try:
            result=remote('M02',READ,job,timeout=12)
            if result!=last:print(json.dumps(result),flush=True);last=result
            if result.get('stage')!='running':break
        except Exception:pass
        time.sleep(3)
    (WORK/'install-result.json').write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':main()
