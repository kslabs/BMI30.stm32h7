import copy,unittest
from usb_recovery_policy import create_plan,revalidate,can_import_device_identity,SOURCE_SERIAL,TARGET_SERIAL
def inventory():
    return {'physical_serial':SOURCE_SERIAL,'boot_id':'source-boot','root_source':'/dev/mmcblk0p2','boot_source':'/dev/mmcblk0p1',
      'blockdevices':[{'path':'/dev/mmcblk0','type':'disk','tran':None,'size':32000000000,'children':[
        {'path':'/dev/mmcblk0p1','type':'part','mountpoints':['/boot/firmware']},
        {'path':'/dev/mmcblk0p2','type':'part','mountpoints':['/']}]},
       {'path':'/dev/sda','type':'disk','tran':'usb','serial':'usb-one','diskseq':'12','maj:min':'8:0',
        'model':'test USB','size':16000000000,'ro':False,'children':[{'path':'/dev/sda1','type':'part','mountpoints':[None]}]}]}
class PolicyTests(unittest.TestCase):
    def test_plan_is_not_authorization_or_boot_verification(self):
        plan=create_plan(inventory(),'/dev/sda',4000000000,10)
        self.assertFalse(plan['destructive_action_authorized']);self.assertFalse(plan['boot_tested'])
        self.assertEqual(plan['destination_hostname'],'BMI30-27448D72B')
    def test_root_disk_cannot_be_selected(self):
        with self.assertRaises(ValueError):create_plan(inventory(),'/dev/mmcblk0',1,10)
    def test_partition_cannot_be_selected_as_a_whole_disk(self):
        with self.assertRaises(ValueError):create_plan(inventory(),'/dev/sda1',1,10)
    def test_wrong_source_hardware_is_refused(self):
        inv=inventory();inv['physical_serial']=TARGET_SERIAL
        with self.assertRaises(ValueError):create_plan(inv,'/dev/sda',1,10)
    def test_mounted_usb_and_readonly_media_are_refused(self):
        for field,value in [('mountpoints',['/media/usb']),('ro',True)]:
            inv=inventory();inv['blockdevices'][1]['children'][0][field]=value
            with self.assertRaises(ValueError):create_plan(inv,'/dev/sda',1,10)
    def test_usb_replacement_same_path_and_capacity_is_refused(self):
        inv=inventory();plan=create_plan(inv,'/dev/sda',1,10)
        inv['blockdevices'][1]['serial']='usb-two'
        with self.assertRaises(ValueError):revalidate(plan,inv,20)
    def test_replugged_unserialized_usb_is_refused_by_disk_sequence(self):
        inv=inventory();inv['blockdevices'][1]['serial']=None;plan=create_plan(inv,'/dev/sda',1,10)
        inv['blockdevices'][1]['diskseq']='13'
        with self.assertRaises(ValueError):revalidate(plan,inv,20)
    def test_reboot_and_old_plan_are_refused_without_wall_clock_dependency(self):
        inv=inventory();plan=create_plan(inv,'/dev/sda',1,10)
        with self.assertRaises(ValueError):revalidate(plan,inv,311)
        inv['boot_id']='new-boot'
        with self.assertRaises(ValueError):revalidate(plan,inv,20)
    def test_unknown_identity_and_small_media_are_refused(self):
        inv=inventory();disk=inv['blockdevices'][1];disk['serial']=None;disk['diskseq']=None
        with self.assertRaises(ValueError):create_plan(inv,'/dev/sda',1,10)
        with self.assertRaises(ValueError):create_plan(inventory(),'/dev/sda',17000000000,10)
    def test_donor_cloud_identity_is_never_accepted_for_s12(self):
        self.assertTrue(can_import_device_identity(TARGET_SERIAL,TARGET_SERIAL))
        self.assertFalse(can_import_device_identity(TARGET_SERIAL,SOURCE_SERIAL))
        self.assertFalse(can_import_device_identity(SOURCE_SERIAL,TARGET_SERIAL))
if __name__=='__main__':unittest.main()
