"""Independent full-system component transaction following the stock core update."""
import argparse,configparser,contextlib,hashlib,importlib.util,ipaddress,json,os,pathlib,shlex,subprocess,time,uuid
P=pathlib.Path
SYSTEM_ROOT=P('/')
def system(name):return SYSTEM_ROOT/str(name).lstrip('/')
DOCS=P('/home/techaid/Documents')
STATE_ROOT=P('/var/lib/bmi30-legacy-menu')
BACKUPS=P('/var/backups/bmi30-full-network')
UNITS=('bmi30-network.service','bmi30-channel-policy.service','bmi30-communication-recovery.service',
       'bmi30-scope-rtc.service','bmi30-access.service','bmi30-wifi-watchdog.service')
CHANNEL_POLICY='bmi30-channel-policy.service'
CONNECTIVITY_URL='https://www.teiots.net/'
CONNECTIVITY_CHECK_INTERVAL=10
CONNECTIVITY_OUTAGE_GRACE=120
CONNECTIVITY_UPDATE_WINDOW=900
CONNECTIVITY_BOOT_WINDOW=300

def stop_runtime_units():
 # NetworkManager Requires this one-shot policy on newer installations.
 # Stopping it also stops NetworkManager. Its inactive code can be replaced
 # without interrupting the active policy; preserve the network itself.
 for unit in UNITS:
  if unit!=CHANNEL_POLICY:run('systemctl','stop',unit,check=False)

def sha(path):return hashlib.sha256(P(path).read_bytes()).hexdigest()
def atomic(path,data,mode=0o600):
 path=P(path);path.parent.mkdir(parents=True,exist_ok=True)
 temp=path.with_name(path.name+'.'+uuid.uuid4().hex)
 with temp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 temp.chmod(mode);temp.replace(path)
def write(path,value):atomic(path,(json.dumps(value,indent=2)+'\n').encode())
def env(path):
 return {k:shlex.split(v)[0] if shlex.split(v) else '' for line in path.read_text().splitlines()
         if '=' in line and not line.startswith('#') for k,v in [line.split('=',1)]}
def active():return env(DOCS/'host/bmi30_split_active_version.env').get('BMI30_SPLIT_BUNDLE_ID')
def identity():return 'BMI30-'+system('/proc/device-tree/serial-number').read_text().strip('\0\n')[-9:].upper()

def require_compatible_hardware(manifest):
 spec=manifest.get('hardware_compatibility')
 if spec is None:
  if identity() not in manifest.get('allowed_ids',[]):raise RuntimeError('Wrong CM5 cohort')
  return
 if (spec.get('schema')!=1 or spec.get('software_contract')!='bmi30-split-v1'
     or spec.get('models')!=['raspberrypi,5-compute-module']):
  raise RuntimeError('Unsupported firmware compatibility contract')
 compatible=system('/proc/device-tree/compatible').read_bytes().split(b'\0')
 if b'raspberrypi,5-compute-module' not in compatible:raise RuntimeError('Firmware requires a CM5')
 value=system('/proc/device-tree/serial-number').read_text().strip('\0\n').lower()
 if len(value)!=16 or any(c not in '0123456789abcdef' for c in value) or not int(value,16):
  raise RuntimeError('Cannot identify the physical CM5')
 if not (DOCS/'host/bmi30_split_active_version.env').is_file():
  raise RuntimeError('BMI30 split firmware runtime is not installed')
def boot_id():
 try:return system('/proc/sys/kernel/random/boot_id').read_text().strip()
 except OSError:return 'unknown'
def run(*args,check=True,timeout=35):
 r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
 if check and r.returncode:raise RuntimeError('System operation failed: '+args[0])
 return r.stdout.strip()
def load_module(path,name):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
@contextlib.contextmanager
def firmware_idle():
 import fcntl
 with (DOCS/'.bmi30_cloud_sync/server_operation.lock').open('rb') as f:
  fcntl.flock(f,fcntl.LOCK_SH|fcntl.LOCK_NB);yield
@contextlib.contextmanager
def previous_component_locks(expected):
 import fcntl
 with contextlib.ExitStack() as stack:
  for path in STATE_ROOT.glob('*/state.json'):
   record=json.loads(path.read_text())
   backup=record.get('backup')
   if record.get('expected_id')!=expected or not backup:continue
   target=P(backup).resolve()
   if target.parent!=P('/var/backups/bmi30-network-recovery') or not (target/'lock').is_file():continue
   stream=stack.enter_context((target/'lock').open('rb'))
   fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
  yield

def protected():
 paths=[system('/etc/bmi30-network/config.json'),system('/etc/bmi30/portal_config.json'),DOCS/'host/bmi30_config.json',
        DOCS/'host/bmi30_sel.json',system('/var/lib/bmi30-access/settings.json')]
 profiles=system('/etc/NetworkManager/system-connections')
 if profiles.is_dir():paths+=list(profiles.iterdir())
 return {str(p):sha(p) for p in paths if p.is_file()}
def protected_unchanged(before):
 for name,digest in before.items():
  p=P(name)
  if p.name=='bmi30_sel.json':continue  # The core refreshes only its timestamp.
  if not p.is_file() or sha(p)!=digest:raise RuntimeError('A preserved setting changed')

def ap_profiles():
 result={}
 folder=system('/etc/NetworkManager/system-connections')
 if not folder.is_dir():return result
 for path in folder.iterdir():
  if path.is_symlink() or not path.is_file() or path.stat().st_size>1048576:continue
  cfg=configparser.ConfigParser(interpolation=None,strict=False)
  try:cfg.read_string(path.read_text())
  except (OSError,ValueError,configparser.Error):continue
  mode=cfg.get('wifi','mode',fallback=cfg.get('802-11-wireless','mode',fallback=''))
  if mode=='ap':result[path.name]=path
 return result

def save_ap_profiles(backup):
 records={}
 folder=system('/etc/NetworkManager/system-connections')
 before_names=[p.name for p in folder.iterdir()] if folder.is_dir() else []
 for name,path in ap_profiles().items():
  st=path.stat();atomic(backup/'ap-profiles'/name,path.read_bytes())
  records[name]={'sha256':sha(path),'uid':st.st_uid,'gid':st.st_gid,'mode':st.st_mode&0o777}
 return {'before_names':before_names,'profiles':records,
         'interface_existed':system('/sys/class/net/wlan0ap').exists()}

def restore_ap_profiles(backup,record):
 folder=system('/etc/NetworkManager/system-connections')
 # Free the radio before reloading profiles or restoring the station. A new
 # AP on another channel can prevent NM from reconnecting the old uplink.
 if system('/sys/class/net/wlan0ap').exists():
  run('nmcli','device','disconnect','wlan0ap',check=False)
  if not record['interface_existed']:run('iw','dev','wlan0ap','del',check=False)
 for name,path in ap_profiles().items():
  if name not in record['before_names']:path.unlink()
 for name,item in record['profiles'].items():
  if P(name).name!=name:raise RuntimeError('Invalid saved AP profile path')
  saved=backup/'ap-profiles'/name
  if sha(saved)!=item['sha256']:raise RuntimeError('Saved AP profile checksum mismatch')
  path=folder/name;atomic(path,saved.read_bytes(),item['mode']);os.chown(path,item['uid'],item['gid'])
 run('nmcli','connection','reload')

def station_identity():
 return {'uuid':run('nmcli','-g','GENERAL.CON-UUID','device','show','wlan0',check=False),
         'address':run('nmcli','-g','IP4.ADDRESS','device','show','wlan0',check=False)}

def restore_station(expected):
 ident=str(expected.get('uuid') or '')
 if ident in ('','--'):return
 if str(uuid.UUID(ident))!=ident.lower():raise RuntimeError('Invalid saved station identity')
 current=station_identity()
 if current.get('uuid')==ident and current.get('address'):return
 run('nmcli','--wait','30','connection','up','uuid',ident,timeout=35)
 for _ in range(20):
  current=station_identity()
  if current.get('uuid')==ident and current.get('address'):return
  time.sleep(.5)
 raise RuntimeError('Saved station did not reconnect')

def create_missing_hotspot(expected_station):
 # The private NM checkpoint keeps the current station recoverable even if
 # this worker is killed while adding a separate AP interface.
 import sys
 sys.path.insert(0,str(system('/opt/bmi30-network')))
 from network import Checkpoint
 checkpoint=Checkpoint(('wlan0',),timeout=150)
 try:
  run('bash',str(system('/usr/local/bin/bmi30-hotspot.sh')),timeout=60)
  if station_identity()!=expected_station:raise RuntimeError('Hotspot setup changed the active station')
 except BaseException as error:
  # The caller must first remove the failed AP, then restore NM and explicitly
  # reactivate the saved router profile even if NM checkpoint rollback fails.
  error.checkpoint=checkpoint
  raise
 return checkpoint
def committed(bundle):
 try:
  r=json.loads((DOCS/'.bmi30_cloud_sync/server/last_transaction.json').read_text())
  return active()==bundle and r.get('target',{}).get('FIRMWARE_BUNDLE_ID')==bundle
 except (OSError,ValueError):return False
def state_write(path,state):write(path,state)

def install_persistent_guard(path,state):
 name='bmi30-legacy-guard-'+state['bundle_id']+'.service'
 unit=system('/etc/systemd/system')/name
 text=('[Unit]\nDescription=BMI30 independent update connection recovery\n'
       'After=local-fs.target NetworkManager.service\nStartLimitIntervalSec=0\n\n'
       '[Service]\nType=simple\nExecStart=/usr/bin/python3 '+str(path.parent/'guard.py')+' --state '+str(path)+'\n'
       'Restart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=multi-user.target\n')
 data=text.encode();expected=hashlib.sha256(data).hexdigest()
 if unit.exists() and (unit.is_symlink() or sha(unit)!=expected):raise RuntimeError('Unexpected existing recovery service')
 atomic(unit,data,0o644);state['persistent_guard']={'unit':name,'sha256':expected};state_write(path,state)
 run('systemctl','daemon-reload');run('systemctl','enable',name);run('systemctl','start',name)

def retire_persistent_guard(state):
 saved=state.get('persistent_guard')
 if not saved:return
 name='bmi30-legacy-guard-'+state['bundle_id']+'.service'
 if saved.get('unit')!=name:raise RuntimeError('Unexpected recovery service identity')
 unit=system('/etc/systemd/system')/name
 if unit.exists():
  if unit.is_symlink() or sha(unit)!=saved['sha256']:raise RuntimeError('Recovery service was changed')
  run('systemctl','disable',name);unit.unlink();run('systemctl','daemon-reload')
def api(port,path='/api/status'):
 import urllib.request
 o=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with o.open('http://127.0.0.1:'+str(port)+path,timeout=3) as r:return json.load(r)
def server_probe(address=None):
 # Keep TLS hostname/certificate checks on both paths. A cached address is
 # accepted only after a successful ordinary HTTPS request in this transaction.
 args=['curl','--noproxy','*','--interface','wlan0','--proto','=https',
       '--connect-timeout','3','--max-time','5','-s','-o','/dev/null',
       '-w','%{http_code} %{remote_ip} %{exitcode}']
 if address:
  try:
   parsed=ipaddress.ip_address(address)
   if parsed.is_unspecified or parsed.is_loopback or parsed.is_multicast or parsed.is_link_local or '%' in address:
    raise ValueError('Invalid server address')
  except ValueError:return {'ok':False,'reason':'invalid-cached-address'}
  ip='['+str(parsed)+']' if parsed.version==6 else str(parsed)
  args+=['--resolve','www.teiots.net:443:'+ip]
 try:
  fields=run(*args,CONNECTIVITY_URL,timeout=7,check=False).split()
  code=int(fields[0]) if fields and fields[0].isdigit() else 0
  exit_code=int(fields[-1]) if len(fields)>=2 and fields[-1].isdigit() else None
  result={'ok':200<=code<500 and exit_code in (None,0),'http_code':code,'curl_exit':exit_code}
  if result['ok']:
   result['reason']='reachable'
   if len(fields)>=2:
    try:result['address']=str(ipaddress.ip_address(fields[1]))
    except ValueError:pass
  else:
   result['reason']={5:'proxy-dns-failed',6:'dns-failed',7:'connect-failed',28:'timeout',
                     35:'tls-failed',51:'tls-verification-failed',60:'tls-verification-failed'}.get(exit_code,
                     'http-error' if code else 'request-failed')
  return result
 except subprocess.TimeoutExpired:return {'ok':False,'reason':'process-timeout'}
 except (OSError,RuntimeError):return {'ok':False,'reason':'probe-unavailable'}

def server_reachable(watch=None):
 primary=server_probe()
 if watch is None:return primary['ok']
 report={k:v for k,v in primary.items() if k!='address'}
 report['method']='dns-https'
 if primary['ok']:
  if primary.get('address'):watch['verified_server_address']=primary['address']
 else:
  saved=watch.get('verified_server_address')
  if saved:
   fallback=server_probe(saved)
   report.update(ok=fallback['ok'],method='cached-ip-tls',primary_reason=primary['reason'],
                 reason=fallback['reason'],fallback_http_code=fallback.get('http_code'),
                 fallback_curl_exit=fallback.get('curl_exit'))
 watch['server_probe']=report
 return report['ok']

def arm_connectivity_watch(station):
 armed=bool(station.get('uuid') not in (None,'','--') and station.get('address'))
 watch={'schema':2,'armed':armed,'status':'monitoring' if armed else 'no-wifi-baseline',
         'boot_id':boot_id(),'phase':'after-update','until_mono':time.monotonic()+CONNECTIVITY_UPDATE_WINDOW,
         'next_check_mono':0,'outage_since_mono':None,'first_boot_pending':True,
         'agent_required':run('systemctl','is-active','bmi30-agent.service',check=False)=='active'}
 if armed and not server_reachable(watch):raise RuntimeError('Cannot verify the original cloud route before changing the network')
 return watch

def connection_result(state,checks,ok,reason):
 observation={'ok':ok,'reason':reason,'checks':checks}
 state['connectivity_watch']['last_observation']=observation
 if not ok:state['connectivity_watch']['last_failed_observation']=observation
 return ok

def connection_healthy(state):
 checks={'station':None,'portal':None,'agent':None,'server':None};step='station'
 try:
  station=station_identity()
  checks['station']=bool(station.get('uuid') not in (None,'','--') and station.get('address'))
  if not checks['station']:return connection_result(state,checks,False,'station-unavailable')
  step='portal'
  portal=api(80)
  checks['portal']=(portal.get('hostname')==state['expected_id'] and portal.get('raspberry_firmware_version')==state['bundle_id']
                   and portal.get('firmware_release',{}).get('portal_matches_release') is True)
  if not checks['portal']:return connection_result(state,checks,False,'portal-identity-or-release-mismatch')
  step='agent'
  if state['connectivity_watch'].get('agent_required'):
   checks['agent']=run('systemctl','is-active','bmi30-agent.service',check=False)=='active'
   if not checks['agent']:return connection_result(state,checks,False,'agent-inactive')
  # STM32 may be unplugged to insert USB; it is not a remote-path failure.
  step='server';checks['server']=server_reachable(state['connectivity_watch'])
  reason='healthy' if checks['server'] else 'server-'+state['connectivity_watch'].get('server_probe',{}).get('reason','unreachable')
  return connection_result(state,checks,checks['server'],reason)
 except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired):
  checks[step]=False
  return connection_result(state,checks,False,step+'-probe-error')

def record_connection_sample(watch,now):
 sample={'at_mono':now,'phase':watch.get('phase'),'ok':watch.get('last_check_ok'),
         'reason':watch.get('last_observation',{}).get('reason','unspecified')}
 history=watch.setdefault('history',[])
 if not history or any(sample.get(k)!=history[-1].get(k) for k in ('phase','ok','reason')):
  history.append(sample);watch['history']=history[-32:]

def record_rollback_diagnostics(state,trigger):
 watch=state.get('connectivity_watch') or {}
 value={'schema':1,'bundle_id':state['bundle_id'],'expected_id':state['expected_id'],
        'trigger':trigger,'phase':watch.get('phase'),'boot_id':boot_id(),
        'last_observation':watch.get('last_observation'),
        'last_failed_observation':watch.get('last_failed_observation'),
        'server_probe':watch.get('server_probe'),'history':watch.get('history',[])[-8:]}
 state['rollback_diagnostics']=value
 try:write(system('/var/lib/bmi30-update-diagnostics/last-rollback.json'),value)
 except OSError:pass  # Recording a failure must never prevent recovery.
 print('BMI30 rollback diagnosis: '+json.dumps(value,sort_keys=True),flush=True)

def annotate_completed_rollback(state):
 # The previous portal only exposes message/error from the stock operation
 # file. Retain a short diagnosis there after the stock tool finishes, under
 # its own firmware lock; never overwrite a subsequent update or its progress.
 try:
  with firmware_idle():
   if active()==state['bundle_id']:return False
   path=DOCS/'.bmi30_cloud_sync/portal_operation.json'
   value=json.loads(path.read_text())
   if value.get('action')!='rollback' or value.get('status')!='succeeded' or value.get('progress')!=100:return False
   diagnosis=state.get('rollback_diagnostics') or {}
   observation=diagnosis.get('last_observation') or {}
   reason=str(observation.get('reason') or diagnosis.get('trigger') or 'unknown')[:80]
   value['message']=('Automatic rollback completed. Failed release: '+state['bundle_id']+'. Check: '+reason)[:240]
   write(path,value)
   return True
 except (OSError,ValueError,RuntimeError,BlockingIOError):return False

def connectivity_step(state,now=None):
 watch=state.get('connectivity_watch') or {}
 if not watch.get('armed'):return False
 now=time.monotonic() if now is None else now;current_boot=boot_id()
 if watch['boot_id']!=current_boot:
  if not watch.get('first_boot_pending'):return False
  watch.update(boot_id=current_boot,phase='after-reboot',until_mono=now+CONNECTIVITY_BOOT_WINDOW,
               next_check_mono=0,outage_since_mono=None,status='monitoring')
 if now<float(watch.get('next_check_mono',0)):return False
 if watch.get('status')=='passed':return False
 watch['next_check_mono']=now+CONNECTIVITY_CHECK_INTERVAL
 healthy=connection_healthy(state);watch['last_check_ok']=healthy
 record_connection_sample(watch,now)
 if healthy:
  watch['outage_since_mono']=None
  watch['status']='monitoring'
  if now>=watch['until_mono']:
   watch['status']='passed'
   if watch['phase']=='after-reboot':watch['first_boot_pending']=False
  return False
 if watch.get('outage_since_mono') is None:watch['outage_since_mono']=now
 if now-watch['outage_since_mono']<CONNECTIVITY_OUTAGE_GRACE:return False
 watch['status']='rollback-required';return True

def verify(version,core_pid):
 for _ in range(35):
  try:
   n=api(8791,'/status');c=api(8765);p=api(80)
   if (n.get('version')==version and (n.get('local',{}).get('internet') or {}).get('ok')
       and c.get('ok') and not c.get('stale') and c.get('connection',{}).get('connected')
       and (c.get('detector',{}).get('worker') or {}).get('alive')
       and run('systemctl','show','bmi30-core','-p','MainPID','--value')==core_pid):return
  except Exception:pass
  time.sleep(.5)
 raise RuntimeError('Full component did not pass runtime/Internet checks')

def restore_system(path,automatic=False):
 state=json.loads(P(path).read_text());expected=state['expected_id']
 if identity()!=expected:raise RuntimeError('Wrong CM5 for full rollback')
 if automatic and state.get('stage')=='committed':return False
 backup=P(state['backup']);portable=load_module(backup/'portable.py','saved_full_portable')
 # Another complete system update wins; never replace its files.
 for name,item in state['files'].items():
  target=system(name)
  actual=sha(target) if target.is_file() else None
  if actual not in (item['before'],item['after']):
   state.update(stage='superseded-by-another-update');state_write(path,state);return False
 stop_runtime_units()
 portable.restore(backup/'portable',root=SYSTEM_ROOT,restart=False)
 for name,record in state.get('extra',{}).items():
  target=system(name)
  if record['exists']:atomic(target,(backup/'extra'/name).read_bytes(),record.get('mode',0o600))
  else:target.unlink(missing_ok=True)
 run('systemctl','start','NetworkManager.service',check=False)
 if state.get('ap_bootstrap'):restore_ap_profiles(backup,state['ap_bootstrap'])
 if state.get('station_before'):restore_station(state['station_before'])
 # A previous recovery guardian may have restored plain 0.1.13 while the
 # core changed. Returning to recovery3 restores its exact known two files.
 if active()=='2026-10-27-133000':
  for name,digest in state.get('recovery3',{}).items():
   source=backup/'recovery3'/name
   if sha(source)!=digest:raise RuntimeError('Saved recovery3 checksum mismatch')
   atomic(system('/opt/bmi30-network')/name,source.read_bytes(),0o644)
 run('systemctl','daemon-reload')
 for unit,record in state['units'].items():
  if record['enabled']:run('systemctl','enable',unit,check=False)
  else:run('systemctl','disable',unit,check=False)
  if record['active']:run('systemctl','start',unit,check=False)
  elif unit==CHANNEL_POLICY:
   required=run('systemctl','show','NetworkManager.service','-p','Requires','--value',check=False).split()
   if CHANNEL_POLICY not in required:run('systemctl','stop',unit,check=False)
   else:state['channel_policy_kept_active_for_network']=True
 busy=system('/run/bmi30-access/busy')
 if busy.is_file() and busy.read_text()==state['busy_token']:busy.unlink()
 state.update(stage='rolled-back');state_write(path,state);retire_persistent_guard(state);return True

def apply(package):
 package=P(package);manifest=json.loads((package/'manifest.json').read_text());bundle=manifest['bundle_id']
 expected=identity()
 require_compatible_hardware(manifest)
 if not committed(bundle):raise RuntimeError('Stock firmware transaction not committed')
 for name,digest in manifest['payload_sha256'].items():
  if sha(package/name)!=digest:raise RuntimeError('Full component checksum mismatch')
 portable=load_module(package/'portable.py','full_portable')
 assets=portable.validate(package/'payload')
 names=[name for name in assets if name!='home/techaid/Documents/hotspot_info_server.py']
 directory=STATE_ROOT/bundle;directory.mkdir(parents=True,exist_ok=True,mode=0o700)
 state_path=directory/'state.json'
 if state_path.exists():
  state=json.loads(state_path.read_text())
  if state.get('stage')=='committed':verify(manifest['network_version'],state['core_pid']);return
  if state.get('stage') in ('prepared','applying'):raise RuntimeError('Previous full transaction pending')
 with firmware_idle(),previous_component_locks(expected):
  if sha(system('/opt/bmi30-network/service.py')) not in manifest['allowed_service_sha256']:
   raise RuntimeError('Unsupported existing network service')
  before=protected();core=run('systemctl','show','bmi30-core','-p','MainPID','--value')
  if not core.isdigit() or int(core)==0:raise RuntimeError('Detector is not running')
  station_before=station_identity();connectivity_watch=arm_connectivity_watch(station_before)
  backup=BACKUPS/(bundle+'-'+uuid.uuid4().hex);backup.mkdir(parents=True,mode=0o700)
  atomic(backup/'portable.py',(package/'portable.py').read_bytes(),0o700)
  atomic(directory/'guard.py',(package/'component.py').read_bytes(),0o700)
  portable.snapshot(backup/'portable',root=SYSTEM_ROOT,names=names)
  saved=json.loads((backup/'portable/snapshot.json').read_text())
  units={unit:{'active':run('systemctl','is-active',unit,check=False)=='active',
               'enabled':run('systemctl','is-enabled',unit,check=False)=='enabled'} for unit in UNITS}
  state={'format':2,'kind':'full-system','expected_id':expected,'bundle_id':bundle,'backup':str(backup),
         'network_version':manifest['network_version'],'stage':'prepared','deadline_mono':time.monotonic()+300,'boot_id':boot_id(),
         'core_pid':core,'protected':before,'units':units,'busy_token':'bmi30-full-update:'+bundle,
         'station_before':station_before,'connectivity_watch':connectivity_watch,
         'files':{name:{'before':saved[name].get('sha256'),'after':assets[name]} for name in names},
         'recovery3':manifest.get('recovery3',{})}
  import ipaddress
  ap_text=run('nmcli','-g','IP4.ADDRESS','dev','show','wlan0ap',check=False)
  missing_ap=not ap_text.strip()
  if missing_ap:
   config=json.loads(system('/etc/bmi30-network/config.json').read_text())
   # Use the existing network configuration, without inventing another subnet
   # or changing the station's DHCP address or saved router credentials.
   ap_text=config['ap_address']
   state['ap_bootstrap']=save_ap_profiles(backup)
   for name in state['ap_bootstrap']['profiles']:
    before.pop(str(system('/etc/NetworkManager/system-connections')/name),None)
  ap=ipaddress.IPv4Interface(ap_text.splitlines()[0])
  if not ap.ip.is_private:raise RuntimeError('Unexpected hotspot address')
  retained='var/lib/bmi30-access/retained-ap.json'
  retained_data=(json.dumps({'cidr':str(ap)})+'\n').encode()
  local_dns='etc/NetworkManager/dnsmasq-shared.d/90-bmi30-captive-portal.conf'
  dns_data=('# BMI30 service network: ordinary DNS is forwarded upstream.\n'
            'address=/bmi30.local/'+str(ap.ip)+'\naddress=/'+expected.lower()+'.local/'+str(ap.ip)+'\n').encode()
  generated={retained:(retained_data,0o600),local_dns:(dns_data,0o644)}
  state['extra']={}
  for name,(data,mode) in generated.items():
   target=system(name);state['extra'][name]={'exists':target.is_file(),'mode':target.stat().st_mode&0o777 if target.is_file() else mode}
   if target.is_file():atomic(backup/'extra'/name,target.read_bytes())
   state['files'][name]={'before':sha(target) if target.is_file() else None,'after':hashlib.sha256(data).hexdigest()}
  rendered={}
  dns='etc/NetworkManager/dnsmasq-shared.d/91-bmi30-captive-detection.conf'
  if dns in assets:
   rendered[dns]=(package/'payload/system'/dns).read_bytes().replace(b'10.42.0.1',str(ap.ip).encode())
   state['files'][dns]['after']=hashlib.sha256(rendered[dns]).hexdigest()
  for name,digest in state['recovery3'].items():atomic(backup/'recovery3'/name,(package/'recovery3'/name).read_bytes())
  state_write(state_path,state)
  install_persistent_guard(state_path,state)
  busy=system('/run/bmi30-access/busy');busy.parent.mkdir(parents=True,exist_ok=True)
  if busy.exists() and busy.read_text()!=state['busy_token']:raise RuntimeError('Another radio operation is running')
  busy.write_text(state['busy_token'])
  checkpoint=None
  try:
   state['stage']='applying';state_write(state_path,state)
   stop_runtime_units()
   busy.parent.mkdir(parents=True,exist_ok=True);busy.write_text(state['busy_token'])
   for name in sorted(names,key=lambda name:name=='opt/bmi30-network/service.py'):
    target=system(name);owner=target.stat() if target.exists() else None
    if name in rendered:atomic(target,rendered[name],portable.ALLOWLIST[name])
    else:portable.atomic_copy(package/'payload/system'/name,target,portable.ALLOWLIST[name],
                              owner.st_uid if owner else 0,owner.st_gid if owner else 0)
   for name,(data,mode) in generated.items():atomic(system(name),data,mode)
   run('systemctl','daemon-reload')
   if missing_ap:checkpoint=create_missing_hotspot(state['station_before'])
   for unit_name,record in units.items():
    if record['active'] or unit_name in ('bmi30-network.service','bmi30-access.service'):
     run('systemctl','start',unit_name)
   # Do not enable previously disabled services until their health is known.
   verify(manifest['network_version'],core);protected_unchanged(before)
   if missing_ap and station_identity()!=state['station_before']:raise RuntimeError('Station identity changed during system update')
   if checkpoint:checkpoint.commit();checkpoint=None
   for unit_name in ('bmi30-network.service','bmi30-access.service'):run('systemctl','enable',unit_name)
   state['stage']='committed'
   state['connectivity_watch']['until_mono']=time.monotonic()+CONNECTIVITY_UPDATE_WINDOW
   state_write(state_path,state)
  except BaseException as error:
   checkpoint=checkpoint or getattr(error,'checkpoint',None)
   try:
    restore_system(state_path)
   finally:
    if checkpoint:
     try:
      if active()==bundle:checkpoint.rollback()
      else:checkpoint.commit()
     except Exception:pass
     checkpoint=None
    if active()==bundle:restore_station(state['station_before'])
   raise
  finally:
   if busy.is_file() and busy.read_text()==state['busy_token']:busy.unlink()

def forward_full_update(state):
 current=active()
 if not current or current<=state['bundle_id']:return False
 try:
  manifest=json.loads((DOCS/'host/bmi30_active_runtime/project/host/bmi30_full_update/manifest.json').read_text())
  require_compatible_hardware(manifest)
  return (manifest.get('bundle_id')==current and bool(manifest.get('network_version'))
          and 'component.py' in manifest.get('payload_sha256',{}))
 except (OSError,ValueError,RuntimeError,AttributeError):return False

def guard(path):
 path=P(path)
 while True:
  state=json.loads(path.read_text())
  if identity()!=state['expected_id']:raise RuntimeError('Wrong CM5')
  timed_out=state.get('stage') in ('prepared','applying') and (boot_id()!=state['boot_id'] or time.monotonic()>state['deadline_mono'])
  if state.get('stage') in ('rolled-back','superseded-by-another-update'):return
  if (not timed_out and state.get('stage') in ('prepared','applying')
      and active()==state['bundle_id']):
   # The durable transaction state owns maintenance. Keep the shared hint
   # alive even after an access-service restart or a slow eMMC operation.
   busy=system('/run/bmi30-access/busy')
   if not busy.exists():
    busy.parent.mkdir(parents=True,exist_ok=True);busy.write_text(state['busy_token']);busy.chmod(0o600)
   elif busy.read_text()==state['busy_token']:os.utime(busy,None)
  # A later full transaction owns activation and rollback. Remain alive so
  # returning here retains this release's original system rollback path.
  if not timed_out and forward_full_update(state):time.sleep(3);continue
  lost_connection=False
  if not timed_out and state.get('stage')=='committed' and active()==state['bundle_id']:
   previous=json.dumps(state,sort_keys=True);lost_connection=connectivity_step(state)
   if json.dumps(state,sort_keys=True)!=previous:state_write(path,state)
  if active()!=state['bundle_id'] or timed_out or lost_connection:
   if timed_out or lost_connection:
    record_rollback_diagnostics(state,'activation-timeout' if timed_out else 'connection-loss')
    state_write(path,state)
   try:
    with firmware_idle():restore_system(path)
   except BlockingIOError:time.sleep(2);continue
   if (timed_out or lost_connection) and active()==state['bundle_id']:
    run('bash',str(DOCS/'utilities/server_firmware.sh'),'rollback',check=False,timeout=240)
    annotate_completed_rollback(state)
   return
  time.sleep(3)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--apply',type=P);parser.add_argument('--state',type=P);args=parser.parse_args()
 if args.apply:
  try:apply(args.apply)
  except Exception:
   # The new core remains reachable while the system component is restored.
   # Invoke the normal saved-core rollback after releasing the firmware lock.
   manifest=json.loads((args.apply/'manifest.json').read_text())
   if active()==manifest['bundle_id']:
    run('bash',str(DOCS/'utilities/server_firmware.sh'),'rollback',check=False,timeout=240)
   raise
 else:guard(args.state)

if __name__=='__main__':main()
