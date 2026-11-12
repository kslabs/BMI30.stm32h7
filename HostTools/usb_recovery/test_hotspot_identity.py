import ast,json,os,pathlib,subprocess,tarfile,tempfile,unittest
from patch_hotspot_identity import portal,BODY,identity_script
from unittest.mock import Mock
ROOT=pathlib.Path(__file__).resolve().parents[2]

class HotspotIdentityTests(unittest.TestCase):
    def classify(self,mode,iface,ssid_name,device='wlan0'):
        def run(*args):
            return {'802-11-wireless.mode':mode,'connection.interface-name':iface}[args[2]]
        scope={'run_command':Mock(side_effect=run)};exec(BODY,scope)
        return scope['_is_hotspot_connection'](ssid_name,device)
    def test_neighbour_bmi30_client_is_not_our_ap(self):
        self.assertFalse(self.classify('infrastructure','wlan0','BMI30-05AB38E6A'))
    def test_station_named_hotspot_is_not_our_ap(self):
        self.assertFalse(self.classify('infrastructure','wlan0','Hotspot-router'))
    def test_ap_with_custom_name_is_our_ap(self):
        self.assertTrue(self.classify('ap','wlan0ap','Custom name','wlan0ap'))
        self.assertTrue(self.classify('ap','wlan0','Custom legacy AP'))
    def test_patch_is_idempotent_and_only_replaces_detector(self):
        source=(ROOT/'.tmp/usb-recovery-20261028/M02-portal-before.py').read_text(encoding='utf-8')
        changed=portal(source);self.assertEqual(changed,portal(changed))
        def other(code):return [ast.dump(n,include_attributes=False) for n in ast.parse(code).body if not isinstance(n,ast.FunctionDef) or n.name!='_is_hotspot_connection']
        self.assertEqual(other(source),other(changed))

class BootIdentityProfileTests(unittest.TestCase):
    @staticmethod
    def source():
        archive=ROOT/'FirmwareReleases/RaspberryPi/2026-10-23-1514/BMI30-RPi-2026-10-23-1514.tar.gz'
        with tarfile.open(archive) as stream:
            member=next(m for m in stream if m.name.endswith('system/usr/local/sbin/bmi30-refresh-network-identity.sh'))
            return stream.extractfile(member).read().decode()

    def test_boot_identity_patch_is_idempotent_and_activates_ap_by_uuid(self):
        changed=identity_script(self.source());self.assertEqual(changed,identity_script(changed))
        self.assertNotIn('"$lowered_ssid" == bmi30*',changed)
        function=changed.split('update_nm_hotspots() {',1)[1].split('\n}\n',1)[0]
        self.assertNotIn('nmcli connection up "$ssid"',function)

    @unittest.skipIf(os.name=='nt','Executes the actual Bash function with an isolated nmcli stub')
    def test_boot_refresh_changes_only_ap_never_neighbour_or_hotspot_named_station(self):
        source=identity_script(self.source())
        body='update_nm_hotspots() {'+source.split('update_nm_hotspots() {',1)[1].split('\n}\n',1)[0]+'\n}\n'
        profiles={
          'ap-uuid':{'name':'custom','mode':'ap','method':'shared','ssid':'old-ap','iface':'wlan0ap'},
          'peer-uuid':{'name':'bmi30-recovery-via-source','mode':'infrastructure','method':'auto','ssid':'BMI30-E29805C7C','iface':'wlan0'},
          'router-uuid':{'name':'hotspot-provider','mode':'infrastructure','method':'auto','ssid':'other','iface':'wlan0'},
          'shared-sta':{'name':'shared-client','mode':'infrastructure','method':'shared','ssid':'other','iface':'wlan0'}}
        stub='''#!/usr/bin/env python3
import json,os,pathlib,sys
a=sys.argv[1:];p=json.loads(os.environ['TEST_NM_PROFILES'])
if a[0]=='-g':
 keys={'802-11-wireless.mode':'mode','ipv4.method':'method','802-11-wireless.ssid':'ssid','connection.id':'name','connection.interface-name':'iface'}
 print(p[a[-1]][keys[a[1]]])
elif a[0]=='-t':
 for ident,row in p.items():
  if '--active' in a:
   if row['mode']=='ap':print(ident+':802-11-wireless:'+row['name'])
  else:print(row['name']+':'+ident+':802-11-wireless')
else:
 with pathlib.Path(os.environ['TEST_NM_LOG']).open('a') as f:f.write(json.dumps(a)+'\\n')
'''
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);program=root/'nmcli';program.write_text(stub);program.chmod(0o755)
            log=root/'calls.jsonl'
            env={**os.environ,'PATH':str(root)+os.pathsep+os.environ['PATH'],
                 'TEST_NM_PROFILES':json.dumps(profiles),'TEST_NM_LOG':str(log)}
            subprocess.run(['bash','-c','set -euo pipefail\nlog(){ :; }\n'+body+'\nupdate_nm_hotspots BMI30-C9775FBAE\n'],env=env,check=True,capture_output=True,text=True,timeout=15)
            calls=[json.loads(line) for line in log.read_text().splitlines()]
            changes=[call for call in calls if call[:2]==['connection','modify']]
            self.assertEqual([call[2] for call in changes],['ap-uuid'])
            self.assertIn(['connection','up','ap-uuid'],calls)
            self.assertFalse(any('peer-uuid' in call or 'router-uuid' in call or 'shared-sta' in call for call in calls))


if __name__=='__main__':unittest.main()
