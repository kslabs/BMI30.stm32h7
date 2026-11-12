"""Launch the independent full migration after the standard menu commit."""
import importlib.util,json,pathlib,subprocess,threading,time
P=pathlib.Path
PACKAGE=P(__file__).resolve().parent/'bmi30_full_update'
DOCS=P('/home/techaid/Documents')
PORTABLE_STATE=P('/var/lib/bmi30-portable-runtime.json')

def portable_runtime(bundle,serial):
 try:
  value=json.loads(PORTABLE_STATE.read_text())
  return (value.get('stage')=='adopted' and value.get('physical_serial')==serial
          and value.get('bundle_id')==bundle)
 except (OSError,ValueError):return False

def check_upgrade_source(manifest):
 # The first full-system release has an older independent rollback guardian.
 # Its saved rollback must precede this release. Reject an accidental direct
 # forward update while the stock updater still holds its own lock.
 path=DOCS/'.bmi30_cloud_sync/server/pending.json'
 if not path.is_file():return
 pending=json.loads(path.read_text())
 if pending.get('target',{}).get('FIRMWARE_BUNDLE_ID')!=manifest['bundle_id']:return
 before=pending.get('before',{}).get('bundle_id')
 if before in manifest.get('requires_saved_rollback_from',[]):
  raise RuntimeError('Use the saved rollback from '+before+' before installing this release')

def ready(bundle):
 import fcntl,shlex
 try:
  with (DOCS/'.bmi30_cloud_sync/server_operation.lock').open('rb') as f:
   fcntl.flock(f,fcntl.LOCK_SH|fcntl.LOCK_NB)
   transaction=json.loads((DOCS/'.bmi30_cloud_sync/server/last_transaction.json').read_text())
   active={k:shlex.split(v)[0] if shlex.split(v) else '' for line in (DOCS/'host/bmi30_split_active_version.env').read_text().splitlines()
           if '=' in line and not line.startswith('#') for k,v in [line.split('=',1)]}
   return active.get('BMI30_SPLIT_BUNDLE_ID')==bundle and transaction.get('target',{}).get('FIRMWARE_BUNDLE_ID')==bundle
 except (OSError,ValueError):return False

def worker(manifest):
 unit='bmi30-full-apply-'+manifest['bundle_id']
 while True:
  if ready(manifest['bundle_id']):
   state=P('/var/lib/bmi30-legacy-menu')/manifest['bundle_id']/'state.json'
   if state.is_file() and json.loads(state.read_text()).get('stage')=='committed':return
   active=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=5)
   if active.stdout.strip()!='active':
    result=subprocess.run(['systemd-run','--quiet','--collect','--unit='+unit,'/usr/bin/python3',str(PACKAGE/'component.py'),'--apply',str(PACKAGE)],capture_output=True,timeout=10)
    if result.returncode==0:return
  time.sleep(3)

def start():
 manifest=json.loads((PACKAGE/'manifest.json').read_text())
 serial=P('/proc/device-tree/serial-number').read_text().strip('\0\n')
 actual='BMI30-'+serial[-9:].upper()
 # USB copies already contain the system files. They are personalized before
 # portal startup, so do not run a donor's menu transaction on another board.
 if portable_runtime(manifest['bundle_id'],serial):return
 spec=importlib.util.spec_from_file_location('bmi30_full_hardware_check',PACKAGE/'component.py')
 component=importlib.util.module_from_spec(spec);spec.loader.exec_module(component)
 component.require_compatible_hardware(manifest)
 check_upgrade_source(manifest)
 threading.Thread(target=worker,args=(manifest,),daemon=True,name='full-menu-migration').start()
