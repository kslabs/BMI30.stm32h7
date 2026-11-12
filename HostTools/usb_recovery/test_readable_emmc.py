import configparser,json,os,pathlib,shutil,subprocess,tempfile,time,unittest
from unittest.mock import patch
import usb_recovery_emmc_settings as e
import usb_recovery_adopt as a
import usb_recovery_worker as w
from test_emergency_boot import fixture,SOURCE,OTHER
from test_emmc_restore import usb_inventory

class ReadableSettingsTests(unittest.TestCase):
    def test_boot_dns_uses_the_personalized_ap_subnet_and_own_identity(self):
        with fixture() as (donor,usb,image,commands):
            stale=usb/'etc/NetworkManager/dnsmasq-shared.d/90-bmi30-captive-portal.conf'
            stale.parent.mkdir(parents=True);stale.write_text('address=/bmi30.local/10.42.0.1\n')
            result=a.personalize(image,OTHER,donor)
            self.assertIn('address=/bmi30.local/'+result['hotspot_ip'],stale.read_text())
            self.assertIn('address=/'+a.hostname(OTHER).lower()+'.local/'+result['hotspot_ip'],stale.read_text())
            self.assertNotIn('10.42.0.1',stale.read_text())
            self.assertNotIn(a.hostname(SOURCE).lower()+'.local',stale.read_text())

    def test_target_keeps_master_fallback_but_does_not_connect_to_its_own_hotspot(self):
        from test_usb_recovery import PROFILE
        for actual in (SOURCE,OTHER):
            with self.subTest(actual=actual),fixture() as (donor,usb,image,commands):
                folder=usb/'etc/NetworkManager/system-connections';folder.mkdir(parents=True)
                fallback=folder/'bmi30-recovery-via-source.nmconnection'
                fallback.write_text(PROFILE.replace('ssid=router','ssid='+a.hostname(SOURCE)))
                a.personalize(image,actual,donor)
                self.assertEqual(fallback.exists(),actual!=SOURCE)
    @unittest.skipIf(os.name=='nt','Complete removable-root fixture uses Linux ownership and symlinks')
    def test_master_router_credentials_survive_usb_preparation_and_bad_target_profiles(self):
        from test_usb_recovery import PROFILE
        with fixture() as (donor,usb,image,commands):
            profiles=usb/'etc/NetworkManager/system-connections';profiles.mkdir(parents=True,exist_ok=True)
            router=profiles/'master-router.nmconnection'
            router.write_text(PROFILE.replace('permissions=user:donor:;','permissions=user:donor:;\nautoconnect=false'))
            boot=usb/'boot/firmware';boot.mkdir(parents=True)
            (boot/'cmdline.txt').write_text('root=PARTUUID=old-02 rootwait\n');(boot/'config.txt').write_text('[all]\n')
            (usb/'etc/fstab').write_text('old / ext4 defaults 0 1\nboot /boot/firmware vfat defaults 0 2\n')
            module=usb/'source-module';module.mkdir()
            for name in ('usb_recovery_adopt.py','usb_recovery_common.py','usb_recovery_emmc_settings.py','config.json'):(module/name).write_text('{}')
            with patch.object(w,'physical_serial',return_value=SOURCE),patch.object(w,'save_source_snapshot'),patch.object(w,'install_selected_portal'),patch.object(w,'MODULE',module):
                w.prepare_image(usb,boot,{'version':'new-release'},'usb-02','usb-01','root-uuid','boot-uuid',{})
            own=usb/'target-native';shutil.copytree(donor,own)
            (own/'etc/hostname').write_text(a.hostname(OTHER)+'\n');(own/'var/lib/bmi30-agent/bound_raspberry_serial').write_text(OTHER)
            bad=own/'etc/NetworkManager/system-connections/bad-router.nmconnection'
            bad.write_text(PROFILE.replace('psk=local-test-only','psk=wrong-target-password'))
            guest=own/'etc/NetworkManager/system-connections/guest.nmconnection'
            guest.write_text(PROFILE.replace('ssid=router','ssid=invite').replace('psk=local-test-only','psk=guest-test-only'))
            cfg=own/'home/techaid/Documents/host/bmi30_config.json';saved=json.loads(cfg.read_text());saved['detector_calibration']=42;cfg.write_text(json.dumps(saved))
            cache=usb/'own-cache';e.capture(own,cache,OTHER)
            result=a.personalize({**image,'source_serial':OTHER},OTHER,cache,network_fallback=(donor,SOURCE))
            parser=configparser.ConfigParser(interpolation=None);parser.read(router)
            self.assertEqual(parser['wifi-security']['psk'],'local-test-only')
            self.assertEqual(parser['wifi']['ssid'],'router');self.assertEqual(parser['connection']['autoconnect'],'true')
            self.assertEqual(parser['connection']['interface-name'],'wlan0');self.assertEqual(parser['ipv4']['method'],'auto')
            for field in ('mac-address','bssid','band','channel'):self.assertNotIn(field,parser['wifi'])
            self.assertFalse((profiles/'bad-router.nmconnection').exists())
            recovered=[]
            for path in profiles.glob('bmi30-own-wifi-*.nmconnection'):
                item=configparser.ConfigParser(interpolation=None);item.read(path);recovered.append(item)
                self.assertEqual(item['connection']['autoconnect-priority'],'50')
                self.assertEqual(path.stat().st_mode&0o777,0o600)
                for field in ('mac-address','bssid','band','channel'):self.assertNotIn(field,item['wifi'])
            guests=[item for item in recovered if item['wifi']['ssid']=='invite']
            self.assertEqual(len(guests),1);self.assertEqual(guests[0]['wifi-security']['psk'],'guest-test-only')
            self.assertEqual(result['own_wifi_profiles_restored'],len(recovered))
            again=a.restore_own_wifi(cache,profiles);self.assertEqual(again['own_wifi_profiles_restored'],0)
            self.assertEqual(json.loads((usb/'home/techaid/Documents/host/bmi30_config.json').read_text())['detector_calibration'],42)
            self.assertEqual(result['hotspot_ssid'],a.hostname(OTHER));self.assertTrue(result['network_settings_fallback'])
    def missing_native_network_case(self,broken_config):
        with fixture() as (donor,usb,image,commands):
            own=usb/'old-native';shutil.copytree(donor,own)
            (own/'etc/hostname').write_text(a.hostname(OTHER)+'\n')
            (own/'var/lib/bmi30-agent/bound_raspberry_serial').write_text(OTHER)
            cfg=own/'home/techaid/Documents/host/bmi30_config.json';value=json.loads(cfg.read_text());value['detector_calibration']=42;cfg.write_text(json.dumps(value))
            shutil.rmtree(own/'etc/NetworkManager/system-connections')
            if broken_config:(own/'etc/bmi30-network/config.json').write_text('{broken')
            cache=usb/'native-settings';e.capture(own,cache,OTHER)
            result=a.personalize({**image,'source_serial':OTHER},OTHER,cache,network_fallback=(donor,SOURCE))
            self.assertEqual(result['mode'],'full');self.assertTrue(result['network_settings_fallback'])
            self.assertEqual(json.loads((usb/'home/techaid/Documents/host/bmi30_config.json').read_text())['detector_calibration'],42)
            self.assertEqual((usb/'var/lib/bmi30-agent/bound_raspberry_serial').read_text(),OTHER)
            self.assertNotEqual(result['hotspot_ip'],'10.42.0.1','A readable target must not keep the same AP subnet as its master')
            ap=(usb/'etc/NetworkManager/system-connections/bmi30-emergency-hotspot.nmconnection').read_text()
            self.assertIn('ssid='+a.hostname(OTHER),ap)
            self.assertFalse((usb/'etc/systemd/system/bmi30-core.service').is_symlink())
    def test_own_settings_with_missing_ap_use_verified_usb_network_seed(self):
        self.missing_native_network_case(False)
    def test_broken_native_network_does_not_discard_readable_own_calibration(self):
        self.missing_native_network_case(True)
    @unittest.skipIf(os.name=='nt','Requires Linux service-user ownership')
    def test_root_owned_old_settings_are_repaired_without_changing_values(self):
        with fixture() as (native,usb,image,commands):
            names=['home/techaid/Documents/host/bmi30_config.json','home/techaid/Documents/host/dc_offset_samples.npz']
            original={name:(native/name).read_bytes() for name in names}
            for name in names:
                os.chown(native/name,0,0);(native/name).chmod(0o600)
            cache=usb/'native-settings';e.capture(native,cache,SOURCE)
            result=a.personalize(image,SOURCE,cache)
            self.assertEqual(result['mode'],'full')
            for name in names:
                target=usb/name
                self.assertEqual(target.read_bytes(),original[name])
                self.assertEqual((target.stat().st_uid,target.stat().st_gid),(1000,1000))
                self.assertEqual(target.stat().st_mode&0o777,0o600)
    def test_own_emmc_settings_are_verified_copied_and_readable_by_service_user(self):
        with fixture() as (native,usb,image,commands):
            ssh=native/'home/techaid/.ssh/authorized_keys';ssh.parent.mkdir(parents=True);ssh.write_text('test-public-key\n');ssh.chmod(0o600);os.chown(ssh,1000,1000)
            cfg=native/'home/techaid/Documents/host/bmi30_config.json';os.chown(cfg,1000,1000)
            cache=usb/'native-settings';e.capture(native,cache,SOURCE)
            result=a.personalize(image,SOURCE,cache)
            self.assertEqual(result['mode'],'full')
            self.assertEqual((usb/'home/techaid/.ssh/authorized_keys').stat().st_uid,1000)
            self.assertEqual((usb/'home/techaid/Documents/host/bmi30_config.json').stat().st_uid,1000)
            self.assertFalse((usb/'etc/systemd/system/bmi30-core.service').is_symlink())
    def test_other_boards_native_settings_are_refused(self):
        with fixture() as (native,usb,image,commands):
            with self.assertRaises(a.Refused):e.capture(native,usb/'native-settings',OTHER)
    def test_failed_emmc_io_does_not_wait_for_child_to_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);device=root/'emmc';device.touch();children=[];real_popen=subprocess.Popen
            def popen(args,**kwargs):
                child=real_popen(['/bin/true'] if args[0]=='umount' else ['/bin/sleep','30'],**kwargs);children.append(child);return child
            start=time.monotonic()
            with patch.object(e,'INTERNAL_ROOT',device),patch.object(e,'CACHE_ROOT',root/'cache'),patch.object(e.subprocess,'Popen',side_effect=popen):
                self.assertIsNone(e.try_settings(SOURCE,usb_inventory(),timeout=.15))
            self.assertLess(time.monotonic()-start,1.5)
            for child in children:child.wait(timeout=2)
    def test_no_emmc_starts_core_with_new_settings_and_accessible_authorized_key(self):
        with fixture() as (native,usb,image,commands):
            ssh=native/'home/techaid/.ssh/authorized_keys';ssh.parent.mkdir(parents=True);ssh.write_text('test-public-key\n');ssh.chmod(0o600)
            result=a.personalize(image,OTHER,native)
            self.assertEqual(result['mode'],'factory-settings')
            self.assertFalse((usb/'etc/systemd/system/bmi30-core.service').is_symlink())
            self.assertEqual((usb/'home/techaid/.ssh/authorized_keys').stat().st_uid,1000)
            self.assertEqual((usb/'home/techaid/Documents/host/bmi30_config.json').stat().st_uid,1000)
    def test_boot_uses_selected_portal_even_if_old_setup_replaces_system_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);relative='home/techaid/Documents/host/bmi30_active_runtime/project/hotspot_info_server.py'
            selected=root/relative;selected.parent.mkdir(parents=True);selected.write_text('selected release\n')
            w.install_selected_portal(root)
            legacy=root/'usr/local/bin/bmi30-hotspot-info-server.py';legacy.parent.mkdir(parents=True);legacy.write_text('old setup copy\n')
            unit=(root/'etc/systemd/system/bmi30-hotspot-info.service.d/06-bmi30-runtime.conf').read_text()
            chosen=unit.split('ExecStart=/usr/bin/python3 ',1)[1].strip()
            self.assertEqual((root/chosen.lstrip('/')).read_text(),'selected release\n')
            self.assertEqual((root/'home/techaid/Documents/hotspot_info_server.py').read_text(),'selected release\n')

if __name__=='__main__':unittest.main()
