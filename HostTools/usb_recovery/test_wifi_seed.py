import configparser,unittest
from unittest.mock import patch
import usb_recovery_wifi_seed as seed
from usb_recovery_common import Refused

def settings(ssid='router',mode='infrastructure',shared=False,password='fixture-password'):
    return ({'802-11-wireless':{'ssid':ssid.encode(),'mode':mode,'mac-address':b'old-binding'},
             '802-11-wireless-security':{'key-mgmt':'wpa-psk'},'ipv4':{'method':'shared' if shared else 'manual'}},
            {'802-11-wireless-security':{'psk':password}})

class WifiSeedTests(unittest.TestCase):
    def test_current_router_and_shared_master_hotspot_are_saved_as_station_profiles(self):
        with patch.object(seed,'active_settings',side_effect=[settings(),settings('BMI30-MASTER','ap',True)]):
            result=seed.capture_source_wifi('BMI30-MASTER')
        self.assertTrue(result['summary']['active_wifi_saved']);self.assertTrue(result['summary']['source_hotspot_fallback_saved'])
        for name,text in result['profiles'].items():
            cfg=configparser.ConfigParser(interpolation=None);cfg.read_string(text)
            self.assertEqual(cfg['connection']['autoconnect'],'true');self.assertEqual(cfg['connection']['interface-name'],'wlan0')
            self.assertEqual(cfg['wifi']['mode'],'infrastructure');self.assertNotIn('mac-address',cfg['wifi'])
            self.assertEqual(cfg['ipv4']['method'],'auto');self.assertEqual(cfg['wifi-security']['psk'],'fixture-password')
        self.assertIn('autoconnect-priority=100',result['profiles']['bmi30-recovery-router.nmconnection'])
        self.assertIn('autoconnect-priority=-100',result['profiles']['bmi30-recovery-via-source.nmconnection'])
    def test_unreadable_password_or_other_security_is_refused(self):
        s,secrets=settings();secrets={}
        with self.assertRaises(Refused):seed.station_profile(s,secrets,'test',100)
        s,secrets=settings();s['802-11-wireless-security']['key-mgmt']='wpa-eap'
        with self.assertRaises(Refused):seed.station_profile(s,secrets,'test',100)
    def test_no_connection_allows_offline_copy_without_claiming_wifi_was_saved(self):
        with patch.object(seed,'active_settings',return_value=None):result=seed.capture_source_wifi('BMI30-MASTER')
        self.assertFalse(result['summary']['active_wifi_saved']);self.assertFalse(result['summary']['source_hotspot_fallback_saved'])
        self.assertEqual(result['profiles'],{})
    def test_manual_ap_is_not_advertised_as_an_internet_fallback(self):
        with patch.object(seed,'active_settings',side_effect=[settings(),settings('BMI30-MASTER','ap',False)]):result=seed.capture_source_wifi('BMI30-MASTER')
        self.assertFalse(result['summary']['source_hotspot_fallback_saved'])
    def test_keyfile_escaping_round_trips_with_the_real_glib_parser(self):
        try:from gi.repository import GLib
        except ImportError:self.skipTest('GLib parser available on the lab Raspberry Pi')
        ssid=' router: back\\slash ';password=' space:back\\slash '
        data=seed.station_profile(*settings(ssid,password=password),'test',100)
        key=GLib.KeyFile();key.load_from_data(data,len(data),GLib.KeyFileFlags.NONE)
        self.assertEqual(key.get_string('wifi','ssid'),ssid);self.assertEqual(key.get_string('wifi-security','psk'),password)

if __name__=='__main__':unittest.main()
