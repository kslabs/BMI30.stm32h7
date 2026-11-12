"""Install/restore a positively identified S07 legacy reference from BMI30 server.

The historical runtime is unchanged. Local rollback metadata is saved before an
explicit test selection and restored afterward. Device calibration stays S07's.
"""
import argparse,hashlib,importlib,json,os,pathlib,shutil,subprocess,sys,tarfile,time,uuid
P=pathlib.Path;D=P('/home/techaid/Documents')
SERIAL='abdd2dbc9775fbae';UID='333635343032511300090040'
UNITS=('bmi30-network','bmi30-access','bmi30-channel-policy','bmi30-communication-recovery',
       'bmi30-scope-rtc','bmi30-wifi-watchdog')
def run(*args,check=True,timeout=40):
 r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
 if check and r.returncode:raise RuntimeError('System operation failed: '+args[0])
 return r.stdout.strip()
def guard():
 assert P('/proc/device-tree/serial-number').read_text().strip('\0\n')==SERIAL
 assert run('findmnt','-n','-o','SOURCE','/')=='/dev/mmcblk0p2'
def sha(path):return hashlib.sha256(P(path).read_bytes()).hexdigest()
def write(path,value):
 path=P(path);path.parent.mkdir(parents=True,exist_ok=True)
 temporary=path.with_name(path.name+'.'+uuid.uuid4().hex)
 temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.chmod(0o600);temporary.replace(path)
def updater():
 sys.path.insert(0,str(D/'utilities'))
 from bmi30_server_update import Updater
 return Updater(D)
def api(port,path='/api/status'):
 import urllib.request
 o=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with o.open('http://127.0.0.1:'+str(port)+path,timeout=5) as r:return json.load(r)
def status(folder,stage,**extra):write(folder/'result.json',{'stage':stage,**extra})
def save_file(folder,path):
 path=P(path);target=folder/'files'/str(path).lstrip('/')
 if path.is_file():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
 return {'existed':path.is_file(),'sha256':sha(path) if path.is_file() else None}
def restore_file(folder,path,record):
 path=P(path)
 if record['existed']:
  path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(folder/'files'/str(path).lstrip('/'),path)
 else:path.unlink(missing_ok=True)
def wait_health(version,network_version):
 for _ in range(45):
  try:
   p=api(80);c=api(8765);n=api(8791,'/status')
   if (p.get('raspberry_firmware_version')==version and p.get('stm32_firmware_version')=='1.2.53'
       and c.get('ok') and c.get('connection',{}).get('connected')
       and (c.get('detector',{}).get('worker') or {}).get('alive')
       and n.get('version')==network_version):return p,c,n
  except Exception:pass
  time.sleep(2)
 raise RuntimeError('Reference runtime did not become healthy')
def prepare(folder,payload):
 guard();assert not (folder/'state.json').exists()
 p=api(80);assert p['group_state']['stm32_uid96']==UID and p['stm32_firmware_version']=='1.2.53'
 assert p['raspberry_firmware_version']=='2026-10-23-151427'
 u=updater()
 from bmi30_server_files import Client,extract_verified,verify_bundle,read_env
 client=Client();archive=client.download(payload['marker'],folder/'download',verify_remote=True)
 extracted=folder/'extracted';extracted.mkdir()
 project=extract_verified(archive,payload['marker'],extracted)
 bundle=project/'host/bmi30_split_bundles/2026-10-05-1930'
 manifest=verify_bundle(bundle,'2026-10-05-1930')
 assert read_env(bundle/manifest['BMI30_BUNDLE_RELEASE_REL'])['BMI30_FIRMWARE_VERSION']=='2026-10-05-192357'
 net=project/'legacy-test/network-0.1.13.tar.gz'
 assert sha(net)==payload['network_archive_sha256']
 with u.lock():
  before=u.snapshot(folder/'working-runtime')
  u.install_bundle(project,payload['marker'])
 files={}
 for f in (D/'.bmi30_cloud_sync/server/rollback.json',D/'.bmi30_cloud_sync/server/installed.json',
           D/'.bmi30_cloud_sync/server/last_transaction.json',D/'.bmi30_cloud_sync/rollback_state.env',
           P('/etc/bmi30-network/config.json'),P('/etc/systemd/system/bmi30-core.service'),
           P('/etc/systemd/system/bmi30-network.service'),
           P('/etc/systemd/system/NetworkManager.service.d/30-bmi30-channel-policy.conf'),
           P('/etc/systemd/system/bmi30-hotspot.service.d/30-bmi30-channel-policy.conf')):
  files[str(f)]=save_file(folder,f)
 units={name:{'active':run('systemctl','is-active',name,check=False)=='active',
              'enabled':run('systemctl','is-enabled',name,check=False)=='enabled'} for name in UNITS}
 profiles={str(f):save_file(folder,f) for f in P('/etc/NetworkManager/system-connections').iterdir() if f.is_file()}
 state={'serial':SERIAL,'uid96':UID,'before':before,'files':files,'profiles':profiles,'units':units,
        'original_station_uuid':payload.get('original_station_uuid'),
        'original_ap_uuid':payload.get('original_ap_uuid'),
        'marker':payload['marker'],'legacy_project':str(project),'legacy_network':str(net),'phase':'prepared'}
 shutil.copytree('/opt/bmi30-network',folder/'network-original')
 for file in P('/opt/bmi30-network').glob('*.py'):
  assert sha(file)==sha(folder/'network-original'/file.name)
 write(folder/'state.json',state);status(folder,'prepared',working_version=before['version'])
def restore(folder):
 guard();state=json.loads((folder/'state.json').read_text())
 assert state['serial']==SERIAL and state['uid96']==UID
 if state['phase']=='restored':return
 u=updater()
 # Acquire the stock firmware lock before stopping anything. A busy updater
 # must leave the running network and Core alone.
 with u.lock():restore_locked(folder,state,u)

def restore_locked(folder,state,u):
 state['phase']='restoring';write(folder/'state.json',state)
 status(folder,'restoring-working-runtime')
 for unit in UNITS:
  # NetworkManager Requires this one-shot unit on the current OS.
  if unit!='bmi30-channel-policy':run('systemctl','stop',unit,check=False)
 run('systemctl','stop','bmi30-core',check=False)
 original=folder/'network-original'
 if original.is_dir():
  target=P('/opt/bmi30-network');assert target==P('/opt/bmi30-network')
  # OS-level hooks can still import these modules while the daemon is off.
  # Keep the directory present throughout the restoration.
  shutil.copytree(original,target,dirs_exist_ok=True)
 for path,record in state['files'].items():
  if path.startswith('/etc/'):restore_file(folder,path,record)
 for unit in ('bmi30-core.service','bmi30-network.service'):
  saved=folder/(unit+'-dropins')
  if saved.is_dir():
   target=P('/etc/systemd/system')/(unit+'.d')
   if target.exists():shutil.rmtree(target)
   shutil.copytree(saved,target)
 for path,record in state['profiles'].items():restore_file(folder,path,record)
 run('systemctl','daemon-reload');run('systemctl','start','NetworkManager')
 run('nmcli','connection','reload')
 run('nmcli','device','set','wlan0','managed','yes')
 run('nmcli','device','set','wlan0','autoconnect','yes')
 if state.get('original_station_uuid'):
  current=run('nmcli','-g','GENERAL.CON-UUID','device','show','wlan0',check=False)
  if current!=state['original_station_uuid']:
   run('nmcli','--wait','30','connection','up','uuid',state['original_station_uuid'],timeout=40)
 u.restore(state['before'],failed=True)
 for unit,record in state['units'].items():
  run('systemctl','enable' if record['enabled'] else 'disable',unit,check=False)
  if record['active']:run('systemctl','start',unit)
 for path,record in state['files'].items():
  if path.startswith(str(D)+ '/.bmi30_cloud_sync/'):restore_file(folder,path,record)
 state['phase']='restored';write(folder/'state.json',state)
 status(folder,'restored',working_version=state['before']['version'],stm32_remains='1.2.53')
def install(folder,payload):
 guard();state=json.loads((folder/'state.json').read_text());assert state['phase']=='prepared'
 try:
  status(folder,'installing-original-legacy-runtime')
  # Detach the newer dependency before stopping its unit. Otherwise an
  # explicit stop of channel-policy propagates to NetworkManager itself.
  for path in ('/etc/systemd/system/NetworkManager.service.d/30-bmi30-channel-policy.conf',
               '/etc/systemd/system/bmi30-hotspot.service.d/30-bmi30-channel-policy.conf'):
   P(path).unlink(missing_ok=True)
  run('systemctl','daemon-reload')
  for unit in UNITS:
   run('systemctl','stop',unit,check=False)
   if unit!='bmi30-network':run('systemctl','disable',unit,check=False)
  run('systemctl','stop','bmi30-core')
  for unit in ('bmi30-core.service','bmi30-network.service'):
   dropins=P('/etc/systemd/system')/(unit+'.d')
   if dropins.is_dir():shutil.move(str(dropins),str(folder/(unit+'-dropins')))
  # The original factory unit runs the historical Core directly.
  P('/etc/systemd/system/bmi30-core.service').write_text(payload['original_core_unit'])
  P('/etc/systemd/system/bmi30-core.service').chmod(0o644)
  target=P('/opt/bmi30-network')
  # Keep support modules referenced by OS-level NetworkManager hooks. The
  # old daemon imports only the historical files replaced below.
  with tarfile.open(state['legacy_network']) as tar:
   for member in tar.getmembers():
    rel=P(member.name)
    assert not rel.is_absolute() and '..' not in rel.parts
    if not member.isfile():continue
    assert len(rel.parts)==2 and rel.parts[0]=='2026-10-05-network-13'
    destination=target/rel.name;destination.write_bytes(tar.extractfile(member).read());destination.chmod(0o644)
  shutil.copy2(target/'bmi30-network.service','/etc/systemd/system/bmi30-network.service')
  cfg_path=P('/etc/bmi30-network/config.json');cfg=json.loads(cfg_path.read_text())
  cfg['id']='BMI30-C9775FBAE';cfg['automatic']=False;write(cfg_path,cfg)
  run('systemctl','daemon-reload')
  # Explicit historical test selection through the normal server rollback.
  legacy_snapshot=folder/'legacy-runtime';shutil.copytree(folder/'working-runtime',legacy_snapshot)
  old_release=P(state['legacy_project'])/'host/bmi30_firmware_release.env'
  shutil.copy2(old_release,legacy_snapshot/'bmi30_firmware_release.env')
  legacy={**state['before'],'version':'2026-10-05-192357','bundle_id':'2026-10-05-1930',
          'label':'S07 clean legacy reference; original 2026-10-05 release',
          'snapshot':str(legacy_snapshot),'installed':state['marker']}
  u=updater()
  with u.lock():u.write_rollback(legacy,{'FIRMWARE_VERSION':state['before']['version'],'FIRMWARE_BUNDLE_ID':state['before']['bundle_id']})
  run('bash',str(D/'utilities/server_firmware.sh'),'rollback',timeout=260)
  # Some historical activations reconcile shared installed helpers. Reapply
  # the verified 0.1.13 runtime after the application has finished activating.
  with tarfile.open(state['legacy_network']) as tar:
   for member in tar.getmembers():
    if member.isfile():
     rel=P(member.name);assert len(rel.parts)==2 and rel.parts[0]=='2026-10-05-network-13'
     (target/rel.name).write_bytes(tar.extractfile(member).read())
  shutil.copy2(target/'bmi30-network.service','/etc/systemd/system/bmi30-network.service')
  run('systemctl','daemon-reload')
  run('systemctl','restart','bmi30-network')
  p,c,n=wait_health('2026-10-05-192357','0.1.13')
  state['phase']='legacy-installed';write(folder/'state.json',state)
  status(folder,'legacy-installed',raspberry=p['raspberry_firmware_version'],stm32=p['stm32_firmware_version'],
         network=n['version'],role=p['sync_mode']['code'],internet=(n.get('local',{}).get('internet') or {}).get('ok'),
         core_alive=c['detector']['worker']['alive'],kernel=run('uname','-r'),
         limitation='OS/kernel and Wi-Fi-driver identity with customer S12 has not been established')
 except BaseException:
  restore(folder);raise
def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','install','restore']);parser.add_argument('folder',type=P)
 args=parser.parse_args();guard();folder=args.folder.resolve()
 assert folder.parent==P('/var/backups') and folder.name.startswith('bmi30-clean-s07-')
 import fcntl
 lock=(folder/'operation.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 payload=json.loads((folder/'input.json').read_text())
 if args.action=='prepare':prepare(folder,payload)
 elif args.action=='install':install(folder,payload)
 else:restore(folder)
if __name__=='__main__':main()
