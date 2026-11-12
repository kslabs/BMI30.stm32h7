"""Exercise real USB personalization in an isolated root with dead/absent eMMC."""
import contextlib,json,os,pathlib,struct,tempfile,unittest
from unittest.mock import patch
import usb_recovery_common as c
import usb_recovery_adopt as a
from usb_recovery_stm32 import read_device
from test_usb_recovery import PROFILE,CFG

SOURCE=CFG['source_serial'];OTHER='a043b0627448d72b'
@contextlib.contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix='bmi30-emergency-test-') as directory:
        root=pathlib.Path(directory);snapshot=root/'snapshot';usb=root/'usb';usb.mkdir()
        files={'etc/hostname':(c.hostname(SOURCE)+'\n').encode(),'etc/machine-id':b'1'*32,
          'etc/bmi30-network/config.json':b'{"id":"old","ap_address":"10.42.0.1/24"}',
          'home/techaid/Documents/host/bmi30_config.json':b'{"detector_calibration":17,"portal_auth":{"username":"admin"}}',
          'home/techaid/Documents/host/dc_offset_samples.npz':b'own-calibration',
          'var/lib/bmi30-agent/bound_raspberry_serial':SOURCE.encode(),
          'var/lib/bmi30-agent/device_api_token':b'own-agent-test-token',
          'etc/bmi30-agent/config.json':b'{}',
          'etc/ssl/bmi30/portal.key':b'own-test-key','etc/ssl/bmi30/portal.crt':b'own-test-cert',
          'etc/NetworkManager/system-connections/own.nmconnection':PROFILE.replace('mode=infrastructure','mode=ap').replace('ssid=router','ssid='+c.hostname(SOURCE)).encode()}
        for name,data in files.items():
            path=snapshot/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        c.write_json(snapshot/'snapshot.json',{'format':1,'physical_serial':SOURCE,'full_settings':True,
                     'files':{name:c.digest(snapshot/name) for name in files}})
        for name in ('bmi30-core','bmi30-agent'):
            path=usb/'etc/systemd/system'/(name+'.service');path.parent.mkdir(parents=True,exist_ok=True);path.write_text('[Service]\nExecStart=/usr/bin/true\n')
        commands=[]
        def command(*args,**kwargs):
            commands.append(args)
            if args[0] in ('mount','umount','blkid') or any('/dev/mmc' in str(arg) for arg in args):raise AssertionError('eMMC must never be accessed')
            if args[0]=='openssl':
                for flag in ('-keyout','-out'):pathlib.Path(args[args.index(flag)+1]).write_bytes(b'new-test-identity')
            return ''
        image={'format':2,'mode':'portable-emergency','source_serial':SOURCE,'root_partuuid':'usb-02','boot_partuuid':'usb-01'}
        with patch.object(a,'LIVE',usb),patch.object(a,'command',side_effect=command):yield snapshot,usb,image,commands

@unittest.skipIf(os.name=='nt','Uses Linux ownership and symlink semantics')
class BootTests(unittest.TestCase):
    def test_original_device_restores_full_settings_without_any_emmc(self):
        with fixture() as (snapshot,usb,image,commands):
            result=a.personalize(image,SOURCE,snapshot)
            self.assertEqual(result['mode'],'full');self.assertFalse(result['emmc_read'])
            self.assertEqual(json.loads((usb/'home/techaid/Documents/host/bmi30_config.json').read_text())['detector_calibration'],17)
            self.assertEqual((usb/'var/lib/bmi30-agent/device_api_token').read_bytes(),b'own-agent-test-token')
            self.assertFalse((usb/'etc/systemd/system/bmi30-core.service').is_symlink())
    def test_other_device_gets_network_access_without_donor_calibration_or_agent(self):
        with fixture() as (snapshot,usb,image,commands):
            result=a.personalize(image,OTHER,snapshot)
            self.assertEqual(result['mode'],'factory-settings');self.assertFalse(result['emmc_read'])
            self.assertNotIn('detector_calibration',json.loads((usb/'home/techaid/Documents/host/bmi30_config.json').read_text()))
            self.assertFalse((usb/'var/lib/bmi30-agent/device_api_token').exists())
            self.assertFalse((usb/'etc/systemd/system/bmi30-core.service').is_symlink())
            self.assertIn('ssid='+c.hostname(OTHER),(usb/'etc/NetworkManager/system-connections/bmi30-emergency-hotspot.nmconnection').read_text())
    def test_actual_boot_entry_point_does_not_probe_or_mount_broken_emmc(self):
        with fixture() as (snapshot,usb,image,commands):
            manifest=usb/'etc/bmi30-usb-recovery-image.json';c.write_json(manifest,image)
            boot_cfg=usb/'boot/firmware/config.txt';boot_cfg.parent.mkdir(parents=True);boot_cfg.write_text('[all]\n')
            inv={'root_source':'/dev/sda2','boot_source':'/dev/sda1','blockdevices':[
              {'path':'/dev/mmcblk0','type':'disk','tran':None,'children':[{'path':'/dev/mmcblk0p2','type':'part'}]},
              {'path':'/dev/sda','type':'disk','tran':'usb','children':[
                {'path':'/dev/sda1','type':'part','partuuid':'usb-01'},{'path':'/dev/sda2','type':'part','partuuid':'usb-02'}]}]}
            with patch.object(a,'MANIFEST',manifest),patch.object(a,'SNAPSHOT',snapshot),patch.object(a,'physical_serial',return_value=SOURCE),patch.object(a,'inventory',return_value=inv):
                a.main()
            self.assertFalse(c.read_json(usb/'var/lib/bmi30-usb-adoption.json')['emmc_read'])
            self.assertNotIn('dtoverlay=bmi30-usb-no-emmc',boot_cfg.read_text())
            self.assertTrue(commands);self.assertFalse(any(cmd[0] in ('mount','umount','blkid') for cmd in commands))
    def test_damaged_usb_snapshot_is_refused_before_activation(self):
        with fixture() as (snapshot,usb,image,commands):
            (snapshot/'home/techaid/Documents/host/bmi30_config.json').write_text('{}')
            with self.assertRaises(c.Refused):a.personalize(image,SOURCE,snapshot)
            self.assertFalse(commands)
    def test_returning_to_creator_restores_its_service_after_network_only_mode(self):
        with fixture() as (snapshot,usb,image,commands):
            a.personalize(image,OTHER,snapshot);result=a.personalize(image,SOURCE,snapshot)
            self.assertEqual(result['mode'],'full');self.assertFalse((usb/'etc/systemd/system/bmi30-core.service').is_symlink())
    def test_recopy_of_limited_recovery_does_not_invent_full_calibration(self):
        with fixture() as (snapshot,usb,image,commands):
            record=c.read_json(snapshot/'snapshot.json');record['full_settings']=False;c.write_json(snapshot/'snapshot.json',record)
            self.assertEqual(a.personalize(image,SOURCE,snapshot)['mode'],'factory-settings')

class PureTests(unittest.TestCase):
    def test_optional_emmc_swap_and_network_mounts_do_not_follow_usb_boot(self):
        command,fstab=c.rewrite_boot('root=old\n','old / ext4 defaults 0 1\nboot /boot/firmware vfat defaults 0 2\n/dev/mmcblk0p3 /data ext4 defaults 0 2\n/dev/mmcblk0p4 none swap sw 0 0\nserver:/share /share nfs defaults 0 0\n','usb-02','usb-01')
        self.assertFalse(any(line.startswith(('/dev/mmc','server:')) for line in fstab.splitlines()))
    def test_usb_boot_refuses_copying_to_another_usb(self):
        from test_usb_recovery import inventory
        inv=inventory();usb=inv['blockdevices'][1]
        usb['children']=[{'path':'/dev/sda1','type':'part','mountpoints':['/boot/firmware']},{'path':'/dev/sda2','type':'part','mountpoints':['/']}]
        inv['root_source']='/dev/sda2';inv['boot_source']='/dev/sda1'
        target={'path':'/dev/sdb','type':'disk','tran':'usb','size':64_000_000_000,'model':'USB2','serial':'two','diskseq':'13','maj:min':'8:16'}
        inv['blockdevices'].append(target)
        with self.assertRaises(c.Refused):c.make_plan(inv,'/dev/sda',CFG,1,10)
        with self.assertRaisesRegex(c.Refused,'only USB → eMMC'):c.make_plan(inv,'/dev/sdb',CFG,1,10)
    def test_stm32_reader_uses_only_in_requests_and_no_device_configuration(self):
        class Device:
            def __init__(self):self.calls=[]
            def ctrl_transfer(self,direction,request,value,index,length,timeout):
                self.calls.append((direction,request,value));assert direction==0xC0 and request in (0x40,0x3A)
                if request==0x40:
                    data=bytearray(40);data[:4]=b'RID2';data[4]=2;data[5]=7;data[6:8]=(0x81).to_bytes(2,'little');data[8:11]=b'ABC';return data
                return struct.pack('<4sBBHIII5I',b'DCCF',1,1,0,3500,1,2,0,0,0,0,0)
        device=Device();result=read_device(device)
        self.assertEqual(result['device_code'],'M07');self.assertEqual(result['dc']['settle_ms'],3500)
        self.assertFalse(result['applied']);self.assertEqual(len(device.calls),2)
    def test_inactive_emmc_identifiers_are_never_probed_by_inventory(self):
        calls=[]
        def command(*args,**kwargs):
            calls.append(args)
            if args[0]=='lsblk':return json.dumps({'blockdevices':[{'path':'/dev/sda','type':'disk','children':[{'path':'/dev/sda1','type':'part'},{'path':'/dev/sda2','type':'part'}]},{'path':'/dev/mmcblk0','type':'disk'}]})
            if args[0]=='findmnt':
                if args[-2]=='SOURCE':return '/dev/sda2' if args[-1]=='/' else '/dev/sda1'
                return 'active-usb-id'
            raise AssertionError(args)
        with patch.object(c,'command',side_effect=command),patch.object(c,'physical_serial',return_value=SOURCE),patch.object(pathlib.Path,'read_text',return_value='boot'):
            c.inventory()
        listing=next(args for args in calls if args[0]=='lsblk')
        self.assertNotIn('UUID',listing[-1]);self.assertNotIn('PARTUUID',listing[-1])
        self.assertFalse(any('/dev/mmc' in str(args) for args in calls))

if __name__=='__main__':unittest.main()
