"""Full-system migration and rollback in an isolated filesystem, never live /."""
import ast,contextlib,hashlib,json,multiprocessing,os,pathlib,shutil,signal,subprocess,sys,tempfile,time,unittest,types,queue,threading
from unittest.mock import patch,Mock
HERE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import build_full_server as build
import full_component as component
import full_bootstrap as bootstrap

BUNDLE='2026-10-27-235959'
FILES,MODES,MANIFEST,RELEASE,COMPONENT=build.assemble(BUNDLE,'2026-10-27T23:59:59Z')

@contextlib.contextmanager
def fixture():
 directory=pathlib.Path(tempfile.mkdtemp(prefix='bmi30-full-test-'))
 root=directory/'root';root.mkdir();package=directory/'package';package.mkdir()
 prefix='project/host/bmi30_full_update/'
 for name,data in FILES.items():
  if name.startswith(prefix):
   target=package/name[len(prefix):];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
 assets=json.loads((package/'payload/metadata/portable-assets.json').read_text())['assets']
 before={}
 for name in assets:
  if name=='home/techaid/Documents/hotspot_info_server.py':continue
  target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
  data=b'# previous '+name.encode()+b'\n'
  if name=='opt/bmi30-network/service.py':data=(package/'recovery3/service.py').read_bytes()
  target.write_bytes(data);before[name]=data
 legacy=root/'opt/bmi30-network/legacy_recovery.py';legacy.write_bytes((package/'recovery3/legacy_recovery.py').read_bytes())
 for name,data in {'proc/device-tree/serial-number':b'000000005AB38E6A\0',
   'proc/device-tree/compatible':b'raspberrypi,5-compute-module\0brcm,bcm2712\0','proc/sys/kernel/random/boot_id':b'test-boot',
   'etc/bmi30-network/config.json':b'{"id":"BMI30-05AB38E6A","fleet_key":"local-only-test"}',
   'etc/bmi30/portal_config.json':b'{}','home/techaid/Documents/host/bmi30_config.json':b'{"threshold":7}',
   'home/techaid/Documents/host/bmi30_sel.json':b'{"sel":6}',
   'home/techaid/Documents/host/bmi30_split_active_version.env':('BMI30_SPLIT_BUNDLE_ID='+BUNDLE+'\n').encode(),
   'home/techaid/Documents/.bmi30_cloud_sync/server/last_transaction.json':json.dumps({'target':{'FIRMWARE_BUNDLE_ID':BUNDLE}}).encode(),
   'home/techaid/Documents/.bmi30_cloud_sync/server_operation.lock':b''}.items():
  target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
 calls=[]
 def run(*args,**kwargs):
  calls.append(args)
  if args[:3]==('systemctl','show','bmi30-core'):return '4242'
  if args[:2]==('systemctl','is-active'):return 'active'
  if args[:2]==('systemctl','is-enabled'):return 'enabled'
  if args[0]=='curl':return '200'
  if args[:3]==('nmcli','-g','IP4.ADDRESS'):return '10.65.23.1/24'
  return ''
 def api(port,path='/api/status'):
  if port==8791:return {'version':build.NETWORK_VERSION,'local':{'internet':{'ok':True}}}
  if port==8765:return {'ok':True,'stale':False,'connection':{'connected':True},'detector':{'worker':{'alive':True}}}
  return {'hostname':'BMI30-05AB38E6A'}
 try:
  with patch.multiple(component,SYSTEM_ROOT=root,DOCS=root/'home/techaid/Documents',STATE_ROOT=root/'var/lib/bmi30-legacy-menu',BACKUPS=root/'var/backups/bmi30-full-network'),patch.object(component,'run',side_effect=run),patch.object(component,'api',side_effect=api):
   assert component.SYSTEM_ROOT.resolve().is_relative_to(directory.resolve())
   yield root,package,before,calls
 finally:
  assert directory.name.startswith('bmi30-full-test-') and directory.parent==pathlib.Path(tempfile.gettempdir())
  shutil.rmtree(directory)

class FullTests(unittest.TestCase):
 def test_nondefault_ap_dns_is_rendered_and_rollback_restores_old_contents_and_mode(self):
  with fixture() as (root,package,before,calls):
   dns=root/'etc/NetworkManager/dnsmasq-shared.d/90-bmi30-captive-portal.conf'
   dns.parent.mkdir(parents=True,exist_ok=True);original=b'address=/bmi30.local/10.42.0.1\n'
   dns.write_bytes(original);dns.chmod(0o644)
   component.apply(package)
   self.assertIn('address=/bmi30.local/10.65.23.1',dns.read_text())
   self.assertNotIn('10.42.0.1',dns.read_text())
   state=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json'
   component.restore_system(state)
   self.assertEqual(dns.read_bytes(),original);self.assertEqual(dns.stat().st_mode&0o777,0o644)

 def test_apply_accepts_its_own_maintenance_marker_created_by_the_guard(self):
  with fixture() as (root,package,before,calls):
   original=component.run
   def run(*args,**kwargs):
    if args[:2]==('systemctl','start') and args[-1].startswith('bmi30-legacy-guard-'):
     path=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json';state=json.loads(path.read_text())
     busy=root/'run/bmi30-access/busy';busy.parent.mkdir(parents=True,exist_ok=True);busy.write_text(state['busy_token'])
    return original(*args,**kwargs)
   with patch.object(component,'run',side_effect=run):component.apply(package)
   self.assertEqual(json.loads((root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json').read_text())['stage'],'committed')
   self.assertFalse((root/'run/bmi30-access/busy').exists())
 def test_cloud_route_is_checked_before_any_network_replacement(self):
  with fixture() as (root,package,before,calls),patch.object(component,'station_identity',return_value={'uuid':'00000000-1111-2222-3333-444444444444','address':'192.168.1.107/24'}),patch.object(component,'server_reachable',return_value=False):
   with self.assertRaisesRegex(RuntimeError,'original cloud route'):component.apply(package)
   for name,data in before.items():self.assertEqual((root/name).read_bytes(),data,name)
   self.assertFalse(any(c[:2]==('systemctl','stop') for c in calls))
 def test_persistent_connection_failure_after_commit_restores_system_and_menu(self):
  with fixture() as (root,package,before,calls):
   component.apply(package);path=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json'
   state=json.loads(path.read_text());state['connectivity_watch']=component.arm_connectivity_watch({'uuid':'00000000-1111-2222-3333-444444444444','address':'192.168.1.107/24'})
   component.state_write(path,state);calls.clear()
   with patch.object(component,'CONNECTIVITY_OUTAGE_GRACE',0),patch.object(component,'connection_healthy',return_value=False):component.guard(path)
   self.assertEqual(json.loads(path.read_text())['stage'],'rolled-back')
   self.assertTrue(any(c[0]=='bash' and c[-1]=='rollback' for c in calls))
   for name,data in before.items():self.assertEqual((root/name).read_bytes(),data,name)
 def test_connection_watch_does_not_require_stm32_for_usb_creation(self):
  state={'expected_id':'BMI30-TEST','bundle_id':BUNDLE,'connectivity_watch':{'agent_required':True}}
  portal={'hostname':'BMI30-TEST','raspberry_firmware_version':BUNDLE,'firmware_release':{'portal_matches_release':True}}
  with patch.object(component,'api',return_value=portal) as api,patch.object(component,'station_identity',return_value={'uuid':'connected','address':'192.168.1.107/24'}),patch.object(component,'server_reachable',return_value=True),patch.object(component,'run',return_value='active'):
   self.assertTrue(component.connection_healthy(state));api.assert_called_once_with(80)
 def test_guard_restart_policy_is_present_before_and_after_boot(self):
  with fixture() as (root,package,before,calls):
   component.apply(package)
   unit=root/'etc/systemd/system'/('bmi30-legacy-guard-'+BUNDLE+'.service')
   self.assertIn('Restart=on-failure',unit.read_text())
   self.assertIn(('systemctl','enable',unit.name),calls)
   self.assertNotIn('After=bmi30-network',unit.read_text())
   self.assertIn('WantedBy=multi-user.target',unit.read_text())
  service=FILES['project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py']
  self.assertIn(b'--property=Restart=on-failure',service)
 def test_unlisted_cm5_is_accepted_without_changing_rollback_identity_guard(self):
  with fixture() as (root,package,before,calls):
   serial=root/'proc/device-tree/serial-number';serial.write_text('abcdef0123456789\0')
   self.assertNotIn(component.identity(),COMPONENT['allowed_ids'])
   component.apply(package);state=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json'
   self.assertEqual(json.loads(state.read_text())['expected_id'],component.identity())
   serial.write_text('abcdef0123456780\0')
   with self.assertRaisesRegex(RuntimeError,'Wrong CM5'):component.restore_system(state)
 def test_other_raspberry_model_is_refused_before_system_mutation(self):
  with fixture() as (root,package,before,calls):
   (root/'proc/device-tree/compatible').write_bytes(b'raspberrypi,5-model-b\0brcm,bcm2712\0')
   with self.assertRaisesRegex(RuntimeError,'requires a CM5'):component.apply(package)
   self.assertFalse(calls)
   for name,data in before.items():self.assertEqual((root/name).read_bytes(),data,name)
 def test_missing_hardware_identity_is_refused_before_system_mutation(self):
  with fixture() as (root,package,before,calls):
   (root/'proc/device-tree/serial-number').write_text('not-a-serial')
   with self.assertRaisesRegex(RuntimeError,'physical CM5'):component.apply(package)
   self.assertFalse(calls)
 def test_known_installed_local_candidate_is_an_allowed_upgrade_source(self):
  previous=build.ROOT/'FirmwareReleases/RaspberryPi/2026-10-28-181307/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
  self.assertIn(hashlib.sha256(previous.read_bytes()).hexdigest(),COMPONENT['allowed_service_sha256'])
 def test_published_084854_is_an_allowed_upgrade_source(self):
  previous=build.ROOT/'FirmwareReleases/RaspberryPi/2026-10-29-084854/review/bundle/project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'
  self.assertIn(hashlib.sha256(previous.read_bytes()).hexdigest(),COMPONENT['allowed_service_sha256'])
 def test_independent_guard_restores_maintenance_marker_removed_by_another_service(self):
  with fixture() as (root,package,before,calls):
   component.apply(package);path=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json'
   state=json.loads(path.read_text());state.update(stage='applying',deadline_mono=time.monotonic()+100);component.state_write(path,state)
   busy=root/'run/bmi30-access/busy';busy.unlink(missing_ok=True)
   with patch.object(component.time,'sleep',side_effect=RuntimeError('stop after one guard pass')):
    with self.assertRaisesRegex(RuntimeError,'one guard pass'):component.guard(path)
   self.assertEqual(busy.read_text(),state['busy_token'])

 @unittest.skipUnless(os.name=='posix','Requires isolated Linux fork fixtures')
 def test_killed_ap_worker_is_recovered_by_independent_guard_and_requests_stock_rollback(self):
  with fixture() as (root,package,before,calls):
   station,ap=self.missing_ap_fixture(root);saved_station=station.read_bytes();saved_ap=ap.read_bytes()
   state_path=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json'
   live_path=root/'station.json';original_station={'uuid':'00000000-1111-2222-3333-444444444444','address':'172.18.10.84/24'}
   live_path.write_text(json.dumps(original_station));original_run=component.run;ctx=multiprocessing.get_context('fork');events=ctx.Queue()
   def run(*args,**kwargs):
    events.put(('command',args))
    if args[:3]==('nmcli','-g','IP4.ADDRESS') and args[-1]=='wlan0ap':return ''
    if args[:6]==('nmcli','--wait','30','connection','up','uuid'):live_path.write_text(json.dumps(original_station))
    if args[0]=='bash':events.put(('stock-rollback',));return ''
    return original_run(*args,**kwargs)
   def interrupted_setup(_):
    ap.write_text('[connection]\nid=unfinished-ap\ninterface-name=wlan0ap\n[wifi]\nmode=ap\n')
    live_path.write_text(json.dumps({'uuid':'','address':''}))
    state=json.loads(state_path.read_text());state['deadline_mono']=time.monotonic()+.3;component.state_write(state_path,state)
    events.put(('ready-to-kill',));time.sleep(30)
   with patch.object(component,'run',side_effect=run),patch.object(component,'station_identity',side_effect=lambda:json.loads(live_path.read_text())),patch.object(component,'create_missing_hotspot',side_effect=interrupted_setup):
    worker=ctx.Process(target=component.apply,args=(package,));worker.start();received=[]
    try:
     end=time.monotonic()+10
     while time.monotonic()<end:
      event=events.get(timeout=2);received.append(event)
      if event[0]=='ready-to-kill':break
     else:self.fail('Worker never reached AP setup')
     os.kill(worker.pid,signal.SIGKILL);worker.join(timeout=3);self.assertEqual(worker.exitcode,-signal.SIGKILL)
     guard=ctx.Process(target=component.guard,args=(state_path,));guard.start();guard.join(timeout=10)
     if guard.is_alive():guard.kill();guard.join();self.fail('Independent guard did not finish')
     self.assertEqual(guard.exitcode,0)
     while True:
      try:received.append(events.get(timeout=.1))
      except queue.Empty:break
     self.assertIn(('stock-rollback',),received)
    finally:
     if worker.is_alive():worker.kill();worker.join()
     events.close();events.join_thread()
   self.assertEqual(json.loads(state_path.read_text())['stage'],'rolled-back')
   self.assertEqual(json.loads(live_path.read_text()),original_station)
   self.assertEqual(station.read_bytes(),saved_station);self.assertEqual(ap.read_bytes(),saved_ap)
   self.assertFalse((root/'run/bmi30-access/busy').exists())
   for name,data in before.items():self.assertEqual((root/name).read_bytes(),data,name)
   self.assertFalse(any(e[0]=='command' and e[1][:3]==('systemctl','stop','bmi30-core') for e in received))

 @unittest.skipUnless(os.name=='posix' and shutil.which('bash'),'Requires a private bash fixture')
 def test_failed_ap_never_falls_back_to_another_channel_when_station_was_connected(self):
  script=FILES['project/host/bmi30_full_update/payload/system/usr/local/bin/bmi30-hotspot.sh'].decode()
  detect=script[script.index('detect_ap_band_and_channel() {'):script.index('\napply_connection_settings() {')]
  bring=script[script.index('bring_connection_up() {'):script.index('\nserial="$(detect_serial)"')]
  fixture_script='''set -euo pipefail
WLAN_STA=wlan0
WLAN_AP=wlan0ap
CON=TEST
CONNECTED=1
FALLBACKS=0
nmcli() {
 case "$*" in
  '-t -f DEVICE,STATE dev status') echo 'wlan0:connected';;
  '-t -f ACTIVE,CHAN dev wifi') echo 'yes:13';;
  '--wait 10 con up uuid TEST ifname wlan0ap') CONNECTED=0; return 1;;
  *) return 1;;
 esac
}
apply_connection_settings() { FALLBACKS=$((FALLBACKS+1)); }
sleep() { :; }
'''+detect+'\n'+bring+'''
detect_ap_band_and_channel
result=0
bring_connection_up TEST || result=$?
[[ "$result" == 1 && "$STA_WAS_CONNECTED" == 1 && "$AP_CHAN" == 13 && "$FALLBACKS" == 0 ]]
'''
  result=subprocess.run(['bash','-c',fixture_script],capture_output=True,text=True,timeout=5)
  self.assertEqual(result.returncode,0,result.stderr)

 def test_failed_ap_setup_reactivates_saved_station_even_when_checkpoint_rollback_fails(self):
  with fixture() as (root,package,before,calls):
   self.missing_ap_fixture(root)
   original_station={'uuid':'00000000-1111-2222-3333-444444444444','address':'172.18.10.84/24'}
   live_station=dict(original_station);original_run=component.run;checkpoint=Mock()
   checkpoint.rollback.side_effect=RuntimeError('AP prevents NM checkpoint restoration')
   failure=RuntimeError('forced lost uplink');failure.checkpoint=checkpoint
   def run(*args,**kwargs):
    if args[:3]==('nmcli','-g','IP4.ADDRESS') and args[-1]=='wlan0ap':return ''
    if args[:6]==('nmcli','--wait','30','connection','up','uuid'):
     live_station.update(original_station)
    return original_run(*args,**kwargs)
   def create(_):
    live_station.update(uuid='',address='');raise failure
   with patch.object(component,'run',side_effect=run),patch.object(component,'station_identity',side_effect=lambda:dict(live_station)),patch.object(component,'create_missing_hotspot',side_effect=create):
    with self.assertRaises(RuntimeError):component.apply(package)
   self.assertEqual(live_station,original_station,'Saved router profile was never reactivated')
   self.assertEqual(checkpoint.rollback.call_count,1)

 def test_ap_failure_keeps_checkpoint_for_cleanup_before_radio_rollback(self):
  with fixture() as (root,package,before,calls):
   checkpoint=Mock();fake_network=types.SimpleNamespace(Checkpoint=Mock(return_value=checkpoint))
   original_run=component.run
   def run(*args,**kwargs):
    if args[0]=='bash':raise RuntimeError('forced AP startup failure')
    return original_run(*args,**kwargs)
   with patch.object(sys,'path',sys.path.copy()),patch.dict(sys.modules,{'network':fake_network}),patch.object(component,'run',side_effect=run):
    with self.assertRaises(RuntimeError) as caught:component.create_missing_hotspot({'uuid':'original','address':'172.18.10.84/24'})
   self.assertIs(getattr(caught.exception,'checkpoint',None),checkpoint)
   checkpoint.rollback.assert_not_called()

 def test_personalized_usb_and_native_copy_skip_donor_transaction_only_for_same_board_and_release(self):
  with fixture() as (root,package,before,calls),patch.object(bootstrap,'PORTABLE_STATE',root/'portable.json'):
   record={'stage':'adopted','physical_serial':'abdd2dbc9775fbae','bundle_id':BUNDLE}
   bootstrap.PORTABLE_STATE.write_text(json.dumps(record))
   self.assertTrue(bootstrap.portable_runtime(BUNDLE,'abdd2dbc9775fbae'))
   self.assertFalse(bootstrap.portable_runtime('later-release','abdd2dbc9775fbae'))
   self.assertFalse(bootstrap.portable_runtime(BUNDLE,'490df72e29805c7c'))
 def missing_ap_fixture(self,root):
  cfg=root/'etc/bmi30-network/config.json';value=json.loads(cfg.read_text());value['ap_address']='10.65.23.1/24';cfg.write_text(json.dumps(value))
  profiles=root/'etc/NetworkManager/system-connections';profiles.mkdir(parents=True,exist_ok=True)
  station=profiles/'router.nmconnection';station.write_text('[connection]\nid=invite\ninterface-name=wlan0\n[wifi]\nmode=infrastructure\nssid=invite\n')
  ap=profiles/'old-ap.nmconnection';ap.write_text('[connection]\nid=old-ap\ninterface-name=wlan0\n[wifi]\nmode=ap\nssid=BMI30-05AB38E6A\n')
  return station,ap

 def test_missing_ap_is_prepared_without_replacing_station_and_rollback_restores_ap_profiles(self):
  with fixture() as (root,package,before,calls):
   station,ap=self.missing_ap_fixture(root);saved_station=station.read_bytes();saved_ap=ap.read_bytes();checkpoint=Mock()
   original=component.run
   def run(*args,**kwargs):
    if args[:3]==('nmcli','-g','IP4.ADDRESS') and args[-1]=='wlan0ap':return ''
    return original(*args,**kwargs)
   def create(old_station):
    ap.write_text('[connection]\nid=old-ap\ninterface-name=wlan0ap\n[wifi]\nmode=ap\n')
    (ap.parent/'new-ap.nmconnection').write_text('[connection]\nid=new-ap\ninterface-name=wlan0ap\n[wifi]\nmode=ap\n')
    return checkpoint
   with patch.object(component,'run',side_effect=run),patch.object(component,'create_missing_hotspot',side_effect=create):component.apply(package)
   self.assertEqual(station.read_bytes(),saved_station);checkpoint.commit.assert_called_once()
   state=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json';self.assertIn('ap_bootstrap',json.loads(state.read_text()))
   component.restore_system(state)
   self.assertEqual(ap.read_bytes(),saved_ap);self.assertEqual(station.read_bytes(),saved_station)
   self.assertFalse((ap.parent/'new-ap.nmconnection').exists())

 def test_missing_ap_bootstrap_failure_restores_profiles_and_clears_busy_state(self):
  with fixture() as (root,package,before,calls):
   station,ap=self.missing_ap_fixture(root);saved=ap.read_bytes();original=component.run
   def run(*args,**kwargs):
    if args[:3]==('nmcli','-g','IP4.ADDRESS') and args[-1]=='wlan0ap':return ''
    return original(*args,**kwargs)
   def create(_):
    ap.write_text('[connection]\nid=failed-setup\n[wifi]\nmode=ap\n');raise RuntimeError('forced AP failure')
   with patch.object(component,'run',side_effect=run),patch.object(component,'create_missing_hotspot',side_effect=create):
    with self.assertRaisesRegex(RuntimeError,'forced AP failure'):component.apply(package)
   self.assertEqual(ap.read_bytes(),saved);self.assertFalse((root/'run/bmi30-access/busy').exists())

 def test_direct_upgrade_from_initial_full_release_is_rejected_before_worker_start(self):
  with fixture() as (root,package,before,calls),patch.object(bootstrap,'DOCS',component.DOCS):
   pending=component.DOCS/'.bmi30_cloud_sync/server/pending.json'
   pending.write_text(json.dumps({'target':{'FIRMWARE_BUNDLE_ID':BUNDLE},'before':{'bundle_id':'2026-10-27-152521'}}))
   with self.assertRaisesRegex(RuntimeError,'saved rollback'):bootstrap.check_upgrade_source(COMPONENT)
   pending.write_text(json.dumps({'target':{'FIRMWARE_BUNDLE_ID':BUNDLE},'before':{'bundle_id':'2026-10-27-133000'}}))
   bootstrap.check_upgrade_source(COMPONENT)

 def test_guard_defers_to_newer_full_release_and_still_covers_returned_release(self):
  with fixture() as (root,package,before,calls):
   component.apply(package)
   state=json.loads((root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json').read_text())
   future='2026-10-28-010000';active=component.DOCS/'host/bmi30_split_active_version.env'
   active.write_text('BMI30_SPLIT_BUNDLE_ID='+future+'\n')
   manifest=component.DOCS/'host/bmi30_active_runtime/project/host/bmi30_full_update/manifest.json'
   manifest.parent.mkdir(parents=True,exist_ok=True)
   manifest.write_text(json.dumps({'bundle_id':future,'allowed_ids':[state['expected_id']],
       'network_version':'future','payload_sha256':{'component.py':'test'}}))
   self.assertTrue(component.forward_full_update(state))
   active.write_text('BMI30_SPLIT_BUNDLE_ID='+BUNDLE+'\n')
   self.assertFalse(component.forward_full_update(state))
   active.write_text('BMI30_SPLIT_BUNDLE_ID=2026-10-27-133000\n')
   self.assertFalse(component.forward_full_update(state))

 def test_full_payload_uses_current_core_and_preserves_ap_selection(self):
  self.assertIn('2026-10-23-1514',MANIFEST['BMI30_BUNDLE_ORIGIN'])
  self.assertIn(b'active-and-passive-v1',FILES['project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'])
  self.assertIn(b'retained-ap.json',FILES['project/host/bmi30_full_update/payload/system/opt/bmi30-network/access.py'])
  self.assertIn(b'BMI30_RETAINED_AP_CIDR',FILES['project/host/bmi30_full_update/payload/system/usr/local/bin/bmi30-hotspot.sh'])
  ast.parse(FILES['project/host/bmi30_full_update/payload/system/opt/bmi30-network/service.py'])

 def test_actual_inlined_v2_runtime_accepts_unchanged_old_receiver(self):
  with fixture() as (root,package,before,calls):
   modules=package/'payload/system/opt/bmi30-network';sys.path.insert(0,str(modules));sys.path.insert(0,str(HERE.parent))
   import network
   from test_recovery import Clock,module as old_module,agent as old_agent
   scope={'__name__':'isolated_full_service','__file__':str(modules/'service.py')}
   exec((modules/'service.py').read_text(),scope)
   state=root/'fake-agent-state';state.mkdir()
   with patch('network.hardware_identity',return_value='BMI30-05AB38E6A'),patch('network.Network'):
    donor=scope['Agent']({'id':'BMI30-05AB38E6A','fleet_key':'17'*32,'state_dir':str(state)})
   self.addCleanup(donor.group.socket.close)
   donor.secrets={'wifi:test-router':{'ssid':'test-router','psk':'test-password','security':'wpa-psk','origin':'BMI30-05AB38E6A','updated':2000000000}}
   donor.publish({'id':'BMI30-05AB38E6A','broadcasts':[],'path':[],'internet':{'ok':False}})
   old=old_module(Clock(0));receiver=old_agent(old,'BMI30-27448D72B',root,Clock(0))
   ready=queue.Queue();thread=threading.Thread(target=scope['serve'],args=(donor,'127.0.0.1',0,ready.put),daemon=True);thread.start();http=ready.get(timeout=3)
   try:
    old['PORT']=http.server_port
    receiver.contact('127.0.0.1','BMI30-05AB38E6A')
    self.assertEqual(receiver.secrets['wifi:test-router']['psk'],'test-password')
    self.assertEqual(donor.legacy_recovery.snapshot()['requests'],1)
   finally:http.shutdown();http.server_close();thread.join(timeout=3)

 def test_newly_added_system_file_is_deleted_by_rollback(self):
  with fixture() as (root,package,before,calls):
   path=root/'opt/bmi30-network/bluetooth_transport.py';path.unlink()
   component.apply(package)
   self.assertTrue(path.is_file())
   component.restore_system(root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json')
   self.assertFalse(path.exists())

 def test_apply_and_menu_rollback_restore_all_system_files_and_profiles(self):
  with fixture() as (root,package,before,calls):
   config=(root/'etc/bmi30-network/config.json').read_bytes()
   component.apply(package)
   state=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json'
   record=json.loads(state.read_text());self.assertEqual(record['stage'],'committed')
   self.assertEqual((root/'etc/bmi30-network/config.json').read_bytes(),config)
   self.assertEqual(json.loads((root/'var/lib/bmi30-access/retained-ap.json').read_text())['cidr'],'10.65.23.1/24')
   dns=(root/'etc/NetworkManager/dnsmasq-shared.d/91-bmi30-captive-detection.conf').read_text()
   self.assertIn('10.65.23.1',dns);self.assertNotIn('10.42.0.1',dns)
   self.assertFalse(any(c[:3]==('systemctl','stop','bmi30-core') for c in calls))
   self.assertTrue(component.restore_system(state))
   self.assertFalse(any(c[:3]==('systemctl','stop',component.CHANNEL_POLICY) for c in calls))
   for name,data in before.items():self.assertEqual((root/name).read_bytes(),data,name)
   self.assertFalse((root/'var/lib/bmi30-access/retained-ap.json').exists())

 def test_failed_health_restores_system_and_preserved_settings(self):
  with fixture() as (root,package,before,calls):
   with patch.object(component,'verify',side_effect=RuntimeError('forced health failure')):
    with self.assertRaises(RuntimeError):component.apply(package)
   for name,data in before.items():self.assertEqual((root/name).read_bytes(),data,name)
   self.assertFalse((root/'run/bmi30-access/busy').exists())
   self.assertFalse(any(c[:3]==('systemctl','stop',component.CHANNEL_POLICY) for c in calls))

 def policy_restore_case(self,required):
  with fixture() as (root,package,before,calls):
   original=component.run
   def run(*args,**kwargs):
    if args==('systemctl','is-active',component.CHANNEL_POLICY):
     calls.append(args);return 'inactive'
    if args[:3]==('systemctl','show','NetworkManager.service'):
     calls.append(args);return component.CHANNEL_POLICY if required else ''
    return original(*args,**kwargs)
   with patch.object(component,'run',side_effect=run):
    component.apply(package)
    state=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json'
    calls.clear();component.restore_system(state)
    stopped=('systemctl','stop',component.CHANNEL_POLICY) in calls
    self.assertEqual(stopped,not required)
    record=json.loads(state.read_text())
    self.assertEqual(bool(record.get('channel_policy_kept_active_for_network')),required)

 def test_rollback_keeps_previously_inactive_policy_required_by_network_manager(self):
  self.policy_restore_case(required=True)

 def test_rollback_stops_previously_inactive_policy_without_network_dependents(self):
  self.policy_restore_case(required=False)

 def test_newer_system_files_are_not_overwritten_by_old_guard(self):
  with fixture() as (root,package,before,calls):
   component.apply(package);calls.clear()
   (root/'opt/bmi30-network/network.py').write_bytes(b'# newer firmware\n')
   state=root/'var/lib/bmi30-legacy-menu'/BUNDLE/'state.json'
   self.assertFalse(component.restore_system(state))
   self.assertFalse(calls)
   self.assertEqual((root/'opt/bmi30-network/network.py').read_bytes(),b'# newer firmware\n')

class ConnectivityClockTests(unittest.TestCase):
 def state(self):
  with patch.object(component,'server_reachable',return_value=True),patch.object(component,'run',return_value='active'),patch.object(component,'boot_id',return_value='boot-a'),patch.object(component.time,'monotonic',return_value=100):
   watch=component.arm_connectivity_watch({'uuid':'connected','address':'192.168.1.107/24'})
  return {'connectivity_watch':watch}
 def test_temporary_failure_recovers_without_rollback_or_accumulated_outage(self):
  state=self.state()
  with patch.object(component,'boot_id',return_value='boot-a'),patch.object(component,'connection_healthy',return_value=False):
   self.assertFalse(component.connectivity_step(state,110));self.assertFalse(component.connectivity_step(state,170))
  with patch.object(component,'boot_id',return_value='boot-a'),patch.object(component,'connection_healthy',return_value=True):
   self.assertFalse(component.connectivity_step(state,180));self.assertIsNone(state['connectivity_watch']['outage_since_mono'])
  with patch.object(component,'boot_id',return_value='boot-a'),patch.object(component,'connection_healthy',return_value=False):
   self.assertFalse(component.connectivity_step(state,190));self.assertFalse(component.connectivity_step(state,300))
   self.assertTrue(component.connectivity_step(state,310))
 def test_first_boot_gets_its_own_monotonic_window_even_after_update_watch_passed(self):
  state=self.state()
  with patch.object(component,'boot_id',return_value='boot-a'),patch.object(component,'connection_healthy',return_value=True):
   self.assertFalse(component.connectivity_step(state,1100));self.assertEqual(state['connectivity_watch']['status'],'passed')
  with patch.object(component,'boot_id',return_value='boot-b'),patch.object(component,'connection_healthy',return_value=False):
   self.assertFalse(component.connectivity_step(state,1));self.assertEqual(state['connectivity_watch']['phase'],'after-reboot')
   self.assertFalse(component.connectivity_step(state,110));self.assertTrue(component.connectivity_step(state,121))
 def test_successful_first_boot_does_not_rollback_an_unrelated_later_outage(self):
  state=self.state()
  with patch.object(component,'boot_id',return_value='boot-b'),patch.object(component,'connection_healthy',return_value=True):
   self.assertFalse(component.connectivity_step(state,1));self.assertFalse(component.connectivity_step(state,302))
  self.assertFalse(state['connectivity_watch']['first_boot_pending'])
  with patch.object(component,'boot_id',return_value='boot-c'),patch.object(component,'connection_healthy') as healthy:
   self.assertFalse(component.connectivity_step(state,1));healthy.assert_not_called()
 def test_polls_are_bounded_and_do_not_use_wall_clock(self):
  state=self.state()
  with patch.object(component.time,'time',side_effect=AssertionError('Wall clock must not govern safety')),patch.object(component,'boot_id',return_value='boot-a'),patch.object(component,'connection_healthy',return_value=True) as healthy:
   component.connectivity_step(state,110);component.connectivity_step(state,111);component.connectivity_step(state,112)
   self.assertEqual(healthy.call_count,1)

if __name__=='__main__':unittest.main()
