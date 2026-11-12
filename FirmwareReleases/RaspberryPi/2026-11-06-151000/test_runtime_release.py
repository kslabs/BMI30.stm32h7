"""Offline package contract checks. No network, services, target or installation."""
import ast,hashlib,importlib.util,json,sys,types,unittest
from pathlib import Path
W=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('runtime_delivery_builder',W/'build_runtime_release.py');B=importlib.util.module_from_spec(s);s.loader.exec_module(B)
FILES,MODES,REPORT=B.render();SOURCE,_=B.read_source()
class RuntimeDelivery(unittest.TestCase):
 def test_frozen_reproducible_standard_archive(self):
  raw=B.archive_bytes(FILES,MODES);self.assertEqual(raw,(W/B.NEW/'bmi30_backup_20261106_151000.tar.gz').read_bytes());self.assertEqual(hashlib.sha256(raw).hexdigest(),'129555be6524ee69f036516d2d03de086a5ab71d6c74cfb9a5e5dfccc03f8ba8')
 def test_no_native_payload_or_keys_and_empty_preferences(self):
  for n in FILES:self.assertNotIn('bmi30_full_update',Path(n).parts);self.assertNotEqual(Path(n).name,'bmi30_native_service_variants.py')
  self.assertEqual(json.loads(FILES[B.TARGET+'project/host/bmi30_config.json']),{});self.assertEqual(json.loads(FILES[B.TARGET+'project/host/bmi30_sel.json']),{})
 def test_exact_numeric_sources_and_Portal(self):
  for n,pin in B.PINS.items():self.assertEqual(B.sha(FILES[B.TARGET+n]),pin)
  self.assertEqual(FILES[B.TARGET+'project/hotspot_info_server.py'],SOURCE[B.PREFIX+'project/hotspot_info_server.py'])
 def test_only_allowed_nonmetadata_runtime_changes(self):
  changed=[]
  for n,raw in FILES.items():
   if not n.startswith(B.TARGET):continue
   rel=n[len(B.TARGET):];old=SOURCE.get(B.PREFIX+rel)
   if old!=raw and not(rel.endswith('.env')or rel in('SHA256SUMS','project/host/bmi30_config.json','project/host/bmi30_sel.json','project/host/bmi30_usb_recovery/config.json')):changed.append(rel)
  self.assertEqual(sorted(changed),['project/host/bmi30_bluetooth_remote_test.py','project/host/bmi30_full_bootstrap.py'])
 def test_own_hostname_guard_preserved(self):
  rel='project/host/bmi30_bluetooth_remote_test.py';old=SOURCE[B.PREFIX+rel];new=FILES[B.TARGET+rel]
  self.assertEqual(new,old.replace(b"DEFAULT_HOSTNAME = 'BMI30-C9775FBAE'",b'DEFAULT_HOSTNAME = socket.gethostname()'));self.assertIn(b'if self.expected_hostname and socket.gethostname() != self.expected_hostname:',new)
 def test_startup_adapter_does_no_work(self):
  m=types.ModuleType('runtime_startup_adapter_test');exec(FILES[B.TARGET+'project/host/bmi30_full_bootstrap.py'],m.__dict__);self.assertIsNone(m.start());self.assertEqual([n.name for n in ast.parse(B.ADAPTER).body if isinstance(n,ast.FunctionDef)],['start'])
 def test_existing_distributable_validator_rejects_old_but_accepts_empty(self):
  sf=B.load_module('bmi30_server_files',SOURCE['Documents/utilities/bmi30_server_files.py']);B.load_module('bmi30_portable_assets',SOURCE['Documents/utilities/bmi30_portable_assets.py']);fake=types.ModuleType('fcntl');fake.LOCK_EX=2;fake.LOCK_NB=4;fake.LOCK_UN=8;old=sys.modules.get('fcntl');sys.modules['fcntl']=fake
  try:u=B.load_module('offline_server_updater_test',SOURCE['Documents/utilities/bmi30_server_update.py'])
  finally:
   if old is None:sys.modules.pop('fcntl',None)
   else:sys.modules['fcntl']=old
  class ConfigFile:
   def __init__(self,value):self.value=value
   def exists(self):return True
   def read_text(self):return json.dumps(self.value)
  class Directory:
   def __init__(self,value):self.value=value
   def __truediv__(self,path):return ConfigFile(self.value)
   def rglob(self,pattern):return []
  manifest={'BMI30_BUNDLE_PROJECT_CONFIG_REL':'project/host/bmi30_config.json'}
  with self.assertRaises(sf.FirmwareError):u.Updater.ensure_distributable(Directory({'avg_n':8}),manifest)
  u.Updater.ensure_distributable(Directory({}),manifest)
if __name__=='__main__':unittest.main()
