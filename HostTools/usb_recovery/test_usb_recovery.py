import copy,io,json,os,pathlib,tempfile,unittest
import usb_recovery_common as c
from usb_recovery_adopt import own_identity,own_hotspot

CFG={'mode':'portable-emergency','source_serial':'490df72e29805c7c','target_serial':'abdd2dbc9775fbae'}
def inventory():
    return {'physical_serial':CFG['source_serial'],'boot_id':'boot','root_source':'/dev/mmcblk0p2',
      'boot_source':'/dev/mmcblk0p1','blockdevices':[
       {'path':'/dev/mmcblk0','type':'disk','tran':None,'size':32_000_000_000,'children':[
        {'path':'/dev/mmcblk0p1','type':'part','mountpoints':['/boot/firmware']},
        {'path':'/dev/mmcblk0p2','type':'part','mountpoints':['/']}]},
       {'path':'/dev/sda','type':'disk','tran':'usb','size':64_000_000_000,'model':'USB',
        'serial':'one','diskseq':'12','maj:min':'8:0','children':[
         {'path':'/dev/sda1','type':'part','mountpoints':[None]}]}]}

PROFILE='''[connection]
id=router
uuid=old-uuid
type=wifi
interface-name=wlan99
permissions=user:donor:;
[wifi]
ssid=router
mode=infrastructure
mac-address=01:02:03:04:05:06
bssid=11:22:33:44:55:66
band=a
channel=36
[wifi-security]
key-mgmt=wpa-psk
psk=local-test-only
[ipv4]
method=manual
address1=192.168.1.107/24,192.168.1.1
[ipv6]
method=manual
'''

class Tests(unittest.TestCase):
    def test_firmware_update_invalidates_prepared_copy_before_any_erasure(self):
        cfg={**CFG,'version':'old'};plan=c.make_plan(inventory(),'/dev/sda',cfg,1,10)
        with self.assertRaisesRegex(c.Refused,'Источник системы изменился'):c.revalidate(plan,inventory(),{**cfg,'version':'new'},20)
    def test_root_and_partition_and_nonusb_targets_are_refused(self):
        for target in ('/dev/mmcblk0','/dev/mmcblk0p2','/dev/sda1','/dev/sdb'):
            with self.assertRaises(c.Refused):c.make_plan(inventory(),target,CFG,1,10)
        inv=inventory();inv['blockdevices'][1]['tran']='sata'
        with self.assertRaises(c.Refused):c.make_plan(inv,'/dev/sda',CFG,1,10)
    def test_plan_never_authorizes_erasure(self):
        plan=c.make_plan(inventory(),'/dev/sda',CFG,1,10)
        self.assertFalse(plan['authorized']);self.assertNotIn('destination_hostname',plan)
        self.assertNotIn('destination_serial',plan)
    def test_other_compatible_physical_boards_can_create_emergency_usb(self):
        inv=inventory();inv['physical_serial']=CFG['target_serial']
        plan=c.make_plan(inv,'/dev/sda',CFG,1,10)
        self.assertEqual(plan['source_serial'],CFG['target_serial'])
    def test_unidentified_source_root_is_refused(self):
        inv=inventory();inv['root_source']='/dev/sda2'
        with self.assertRaises(c.Refused):c.make_plan(inv,'/dev/sda',CFG,1,10)
    def test_same_path_usb_replacement_is_refused(self):
        for key,value in [('serial','two'),('diskseq','13'),('size',63_000_000_000),('maj:min','8:16')]:
            inv=inventory();plan=c.make_plan(inv,'/dev/sda',CFG,1,10);inv['blockdevices'][1][key]=value
            with self.assertRaises(c.Refused):c.revalidate(plan,inv,CFG,20)
    def test_reboot_and_expired_confirmation_are_refused(self):
        inv=inventory();plan=c.make_plan(inv,'/dev/sda',CFG,1,10)
        for now in (9,311):
            with self.assertRaises(c.Refused):c.revalidate(plan,inv,CFG,now)
        inv['boot_id']='new'
        with self.assertRaises(c.Refused):c.revalidate(plan,inv,CFG,20)
    def test_mounted_system_or_readonly_or_small_usb_refused(self):
        inv=inventory();inv['blockdevices'][1]['children'][0]['mountpoints']=['/srv/database']
        with self.assertRaises(c.Refused):c.make_plan(inv,'/dev/sda',CFG,1,10)
        inv=inventory();inv['blockdevices'][1]['ro']=True
        with self.assertRaises(c.Refused):c.make_plan(inv,'/dev/sda',CFG,1,10)
        with self.assertRaises(c.Refused):c.make_plan(inventory(),'/dev/sda',CFG,65_000_000_000,10)
    def test_only_selected_desktop_usb_mount_can_be_unmounted_before_write(self):
        inv=inventory();inv['blockdevices'][1]['children'][0]['mountpoints']=['/media/techaid/USB']
        plan=c.make_plan(inv,'/dev/sda',CFG,1,10)
        with self.assertRaises(c.Refused):c.revalidate(plan,inv,CFG,20)
        c.revalidate(plan,inv,CFG,20,mounted_ok=True)
    def test_usb_without_disk_sequence_is_refused(self):
        inv=inventory();inv['blockdevices'][1]['diskseq']=None
        with self.assertRaises(c.Refused):c.make_plan(inv,'/dev/sda',CFG,1,10)
    def test_new_boot_references_use_only_usb_partuuids(self):
        command,fstab=c.rewrite_boot('console=tty1 root=PARTUUID=source-02 rootwait\n',
          'PARTUUID=source-02 / ext4 defaults 0 1\nPARTUUID=source-01 /boot/firmware vfat defaults 0 2\n','usb-02','usb-01')
        self.assertNotIn('source-',command+fstab);self.assertIn('root=PARTUUID=usb-02',command)
        self.assertIn('PARTUUID=usb-01',fstab)
    def test_ambiguous_boot_configuration_is_refused(self):
        with self.assertRaises(c.Refused):c.rewrite_boot('root=a root=b','x / ext4 defaults 0 1\n','a','b')
        with self.assertRaises(c.Refused):c.rewrite_boot('root=a','x / ext4 defaults 0 1\n','a','b')
    def test_router_profile_uses_own_mac_dhcp_and_new_uuid(self):
        text=c.new_keyfile(PROFILE,'BMI30-C9775FBAE')
        self.assertIn('psk=local-test-only',text)
        for forbidden in ('old-uuid','wlan99','permissions=','mac-address=','bssid=','band=','channel=','192.168.1.107'):
            self.assertNotIn(forbidden,text)
        self.assertIn('method=auto',text);self.assertIn('interface-name=wlan0',text)
    def test_neighbor_profiles_are_not_router_credentials(self):
        self.assertIsNone(c.new_keyfile(PROFILE.replace('ssid=router','ssid=BMI30-OTHER'),'BMI30-C9775FBAE'))
    def test_hotspot_uses_own_ssid_and_subnet_preserving_own_key(self):
        text=c.new_keyfile(PROFILE.replace('mode=infrastructure','mode=ap'),'BMI30-C9775FBAE',access_point=True,cidr='10.80.2.1/24')
        self.assertIn('ssid=BMI30-C9775FBAE',text);self.assertIn('address1=10.80.2.1/24',text)
        self.assertIn('psk=local-test-only',text);self.assertIn('method=shared',text)
    @unittest.skipIf(os.name=='nt','Linux identity fixture; also executed on the lab CM5')
    def test_donor_agent_identity_cannot_be_imported_on_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);bound=root/'var/lib/bmi30-agent/bound_raspberry_serial'
            bound.parent.mkdir(parents=True);bound.write_text(CFG['source_serial'])
            with self.assertRaises(c.Refused):own_identity(root,CFG['target_serial'])
            bound.write_text(CFG['target_serial']);self.assertTrue(own_identity(root,CFG['target_serial']))
    @unittest.skipIf(os.name=='nt','Linux identity fixture; also executed on the lab CM5')
    def test_legacy_without_agent_requires_own_hostname(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);(root/'etc').mkdir();(root/'etc/hostname').write_text('BMI30-E29805C7C')
            with self.assertRaises(c.Refused):own_identity(root,CFG['target_serial'])
            (root/'etc/hostname').write_text('BMI30-C9775FBAE');self.assertFalse(own_identity(root,CFG['target_serial']))
    @unittest.skipIf(os.name=='nt','Linux identity fixture; also executed on the lab CM5')
    def test_hotspot_key_comes_only_from_own_live_profile_not_donor_or_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);profiles=root/'etc/NetworkManager/system-connections';profiles.mkdir(parents=True)
            ap=PROFILE.replace('mode=infrastructure','mode=ap')
            (profiles/'donor.nmconnection').write_text(ap.replace('ssid=router','ssid=BMI30-E29805C7C').replace('local-test-only','donor-key'))
            (profiles/'own.nmconnection.backup').write_text(ap.replace('ssid=router','ssid=BMI30-C9775FBAE').replace('local-test-only','obsolete-key'))
            with self.assertRaises(c.Refused):own_hotspot(root,CFG['target_serial'],'10.80.2.1/24')
            (profiles/'own.nmconnection').write_text(ap.replace('ssid=router','ssid=BMI30-C9775FBAE'))
            text=own_hotspot(root,CFG['target_serial'],'10.80.2.1/24')
            self.assertIn('psk=local-test-only',text);self.assertNotIn('donor-key',text);self.assertNotIn('obsolete-key',text)
            self.assertIn('band=bg',text);self.assertIn('channel=6',text)

if __name__=='__main__':unittest.main()
