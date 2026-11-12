import json,os,pathlib,tempfile,unittest
from unittest.mock import patch
import usb_recovery_common as c
import usb_recovery_emmc as e
from test_usb_recovery import inventory,CFG

def usb_inventory():
    inv=inventory();inv['root_source']='/dev/sda2';inv['boot_source']='/dev/sda1'
    emmc=inv['blockdevices'][0];emmc.update(diskseq='5',serial='own-emmc',model='eMMC',**{'maj:min':'179:0'})
    for node in emmc['children']:node['mountpoints']=[None]
    inv['blockdevices'][1]['children']=[{'path':'/dev/sda1','type':'part','mountpoints':['/boot/firmware']},
                                      {'path':'/dev/sda2','type':'part','mountpoints':['/']}]
    return inv

class PolicyTests(unittest.TestCase):
    def test_direction_is_determined_by_actual_root_and_boot(self):
        self.assertEqual(c.copy_direction(inventory()),'usb')
        self.assertEqual(c.copy_direction(usb_inventory()),'emmc-restore')
    def test_mixed_root_and_boot_refuse_both_directions(self):
        inv=usb_inventory();inv['boot_source']='/dev/mmcblk0p1'
        for kind,target in (('usb','/dev/sda'),('emmc-restore','/dev/mmcblk0')):
            with self.assertRaisesRegex(c.Refused,'same system drive'):c.make_plan(inv,target,CFG,1,10,kind=kind)
    def test_unknown_root_does_not_fall_back_to_usb_copy(self):
        inv=inventory();inv['root_source']='/dev/unknown'
        with self.assertRaises(c.Refused):c.make_plan(inv,'/dev/sda',CFG,1,10)
    def test_plan_cannot_change_direction_at_start(self):
        plan=c.make_plan(usb_inventory(),'/dev/mmcblk0',CFG,1,10,kind='emmc-restore')
        plan['kind']='usb'
        with self.assertRaises(c.Refused):c.revalidate(plan,usb_inventory(),CFG,20)
    def test_native_emmc_cannot_overwrite_itself(self):
        with self.assertRaises(c.Refused):c.make_plan(inventory(),'/dev/mmcblk0',CFG,1,10,kind='emmc-restore')
    def test_restore_plan_is_bound_to_internal_emmc_after_usb_boot(self):
        plan=c.make_plan(usb_inventory(),'/dev/mmcblk0',CFG,1,10,kind='emmc-restore')
        self.assertEqual(plan['kind'],'emmc-restore');self.assertFalse(plan['authorized'])
        c.revalidate(plan,usb_inventory(),CFG,20)
    def test_active_usb_is_never_a_restore_target(self):
        with self.assertRaises(c.Refused):c.make_plan(usb_inventory(),'/dev/sda',CFG,1,10,kind='emmc-restore')
    def test_mounted_readonly_or_too_small_emmc_is_refused(self):
        for field,value in [('ro',True),('size',0)]:
            inv=usb_inventory();inv['blockdevices'][0][field]=value
            with self.assertRaises(c.Refused):c.make_plan(inv,'/dev/mmcblk0',CFG,1,10,kind='emmc-restore')
        inv=usb_inventory();inv['blockdevices'][0]['children'][1]['mountpoints']=['/home']
        with self.assertRaises(c.Refused):c.make_plan(inv,'/dev/mmcblk0',CFG,1,10,kind='emmc-restore')
    def test_enabling_emmc_requires_usb_boot_and_explicit_reboot_confirmation(self):
        with patch.object(e,'inventory',return_value=inventory()):
            with self.assertRaises(c.Refused):e.enable_controller({'confirm_reboot':True})
        with patch.object(e,'inventory',return_value=usb_inventory()):
            with self.assertRaises(c.Refused):e.enable_controller({})

@unittest.skipIf(os.name=='nt','Portal backend uses Linux process locks')
class BackendDirectionTests(unittest.TestCase):
    def status(self,inv,stale_image):
        import usb_recovery_backend as b
        with patch.object(b,'inventory',return_value=inv),patch.object(b,'config',return_value=CFG),\
             patch.object(b,'latest',return_value={'stage':'idle'}),patch.object(b,'read_json',return_value=stale_image),\
             patch.object(e,'P') as paths,patch.object(e,'config',return_value=CFG):
            paths.return_value.read_text.side_effect=OSError()
            return b.state()
    def test_image_marker_cannot_change_native_copy_direction(self):
        value=self.status(inventory(),{'format':2})
        self.assertEqual(value['copy']['kind'],'usb');self.assertFalse(value['emmc']['visible'])
    def test_usb_boot_exposes_internal_target_even_without_image_marker(self):
        value=self.status(usb_inventory(),{})
        self.assertEqual(value['copy'],{'kind':'emmc-restore','target':'/dev/mmcblk0'})
        self.assertTrue(value['emmc']['available']);self.assertTrue(all(not d['eligible'] for d in value['disks']))
    def test_mixed_boot_is_blocked_in_status_as_well_as_plan(self):
        inv=usb_inventory();inv['boot_source']='/dev/mmcblk0p1'
        value=self.status(inv,{})
        self.assertFalse(value['can_write']);self.assertIsNone(value['copy']['kind'])
        self.assertFalse(value['emmc']['visible'])

@unittest.skipIf(os.name=='nt','Linux target filesystem fixtures')
class FilesystemTests(unittest.TestCase):
    def test_native_copy_preserves_full_runtime_binding_only_for_its_own_hardware(self):
        for source in ('own-serial','another-serial'):
            with self.subTest(source=source),tempfile.TemporaryDirectory(prefix='bmi30-native-binding-') as directory:
                root=pathlib.Path(directory);boot=root/'boot/firmware';boot.mkdir(parents=True)
                (root/'etc').mkdir();(root/'etc/fstab').write_text('x / ext4 defaults 0 1\ny /boot/firmware vfat defaults 0 2\n')
                (boot/'cmdline.txt').write_text('root=PARTUUID=old-02 rootwait\n');(boot/'config.txt').write_text('[all]\n')
                adoption={'stage':'adopted','physical_serial':source}
                with patch.object(e,'read_json',return_value=adoption),patch.object(e,'physical_serial',return_value='own-serial'),patch.object(e,'config',return_value={'version':'new-release'}):
                    e.prepare_native_image(root,boot,'native-02','native-01')
                marker=root/'var/lib/bmi30-portable-runtime.json'
                self.assertEqual(marker.is_file(),source=='own-serial')
                if marker.is_file():self.assertEqual(json.loads(marker.read_text())['bundle_id'],'new-release')
    def test_enable_only_changes_usb_boot_configuration_and_schedules_reboot(self):
        with tempfile.TemporaryDirectory(prefix='bmi30-emmc-mode-') as directory:
            root=pathlib.Path(directory);boot=root/'usb-boot';boot.mkdir()
            text='[all]\n# BMI30_EMERGENCY_EMMC_BEGIN\n[all]\ndtoverlay=bmi30-usb-no-emmc\n# BMI30_EMERGENCY_EMMC_END\n'
            (boot/'config.txt').write_text(text);calls=[]
            with patch.object(e,'BOOT',boot),patch.object(e,'STATE',root/'state'),patch.object(e,'inventory',return_value=usb_inventory()),patch.object(e,'command',side_effect=lambda *args,**kwargs:calls.append(args)):
                result=e.enable_controller({'confirm_reboot':True})
            self.assertTrue(result['rebooting']);self.assertNotIn('dtoverlay=bmi30-usb-no-emmc',(boot/'config.txt').read_text())
            self.assertFalse(any('/dev/mmc' in str(args) for args in calls))
            self.assertTrue(any(args[0]=='systemd-run' for args in calls))
    def test_native_copy_removes_usb_only_startup_and_rewrites_root_to_emmc(self):
        with tempfile.TemporaryDirectory(prefix='bmi30-emmc-image-') as directory:
            root=pathlib.Path(directory);boot=root/'boot/firmware';boot.mkdir(parents=True)
            (boot/'cmdline.txt').write_text('root=PARTUUID=usb-02 rootwait\n')
            (boot/'config.txt').write_text('[all]\ndtoverlay=bmi30-usb-no-emmc\n')
            (root/'etc').mkdir();(root/'etc/fstab').write_text('x / ext4 defaults 0 1\ny /boot/firmware vfat defaults 0 2\n')
            (root/'etc/bmi30-usb-recovery-image.json').write_text('{}')
            drop=root/'etc/systemd/system/bmi30-core.service.d/05-bmi30-usb-adopt.conf';drop.parent.mkdir(parents=True);drop.write_text('[Unit]\nRequires=bmi30-usb-adopt.service\n')
            e.prepare_native_image(root,boot,'emmc-02','emmc-01')
            self.assertNotIn('bmi30-usb-no-emmc',(boot/'config.txt').read_text());self.assertFalse(drop.exists())
            self.assertFalse((root/'etc/bmi30-usb-recovery-image.json').exists())
            self.assertIn('root=PARTUUID=emmc-02',(boot/'cmdline.txt').read_text())

if __name__=='__main__':unittest.main()
