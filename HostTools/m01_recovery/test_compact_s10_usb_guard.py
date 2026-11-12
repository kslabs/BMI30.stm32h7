"""Reject unsafe media selection before offline block zeroing can start."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('compact_s10_usb', Path(__file__).with_name('compact_s10_usb_image_remote.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.root = '/dev/mmcblk0p2'
        self.disk = {'name': 'sda', 'serial': module.SERIAL, 'children': [
            {'name': 'sda1', 'uuid': module.BOOT_UUID, 'mountpoints': [None]},
            {'name': 'sda2', 'uuid': module.ROOT_UUID, 'mountpoints': [None]}]}

    def check(self):
        def command(*args):
            if args[0] == 'hostname': return module.HOST
            if args[0] == 'findmnt': return self.root
            if args[0] == 'lsblk': return json.dumps({'blockdevices': [self.disk]})
            raise AssertionError('Unexpected command: ' + str(args))
        with mock.patch.object(module, 'text', side_effect=command), \
             mock.patch.object(module.Path, 'read_text', return_value=module.CID):
            module.guard_unmounted()

    def test_approved_offline_usb(self):
        self.check()

    def test_live_usb_root_is_rejected(self):
        self.root = '/dev/sda2'
        with self.assertRaises(AssertionError): self.check()

    def test_different_usb_is_rejected(self):
        self.disk['serial'] = 'another-drive'
        with self.assertRaises(AssertionError): self.check()

    def test_changed_root_partition_is_rejected(self):
        self.disk['children'][1]['uuid'] = 'different-filesystem'
        with self.assertRaises(AssertionError): self.check()

    def test_mounted_partition_is_rejected(self):
        for partition in (0, 1):
            with self.subTest(partition=partition):
                old = copy.deepcopy(self.disk)
                self.disk['children'][partition]['mountpoints'] = ['/media/in-use']
                with self.assertRaises(AssertionError): self.check()
                self.disk = old

    def test_known_fat_dirty_flag_can_be_repaired(self):
        dirty={'rc':1,'output':'fsck.fat 4.2 (2021-01-31)\nDirty bit is set. Fs was not properly unmounted and some data may be corrupt.\n Automatically removing dirty bit.\nLeaving filesystem unchanged.\n/dev/sda1: 431 files, 20086/130812 clusters','error':''}
        clean={'rc':0,'output':'/dev/sda1: 431 files, 20086/130812 clusters','error':''}
        with mock.patch.object(module,'guard_unmounted'), \
             mock.patch.object(module,'command',side_effect=[dirty,{'rc':1},clean]) as command:
            result,repair=module.verify_fat_clean()
            self.assertEqual(result,clean)
            self.assertEqual(command.call_args_list[1].args,('fsck.fat','-a','/dev/sda1'))

    def test_unexpected_fat_damage_is_not_auto_repaired(self):
        dirty={'rc':1,'output':'Dirty bit is set. Fs was not properly unmounted and some data may be corrupt.\nCluster chains contain a loop','error':''}
        with mock.patch.object(module,'guard_unmounted'), \
             mock.patch.object(module,'command',return_value=dirty) as command:
            with self.assertRaises(AssertionError):module.verify_fat_clean()
            self.assertEqual(command.call_count,1)


if __name__ == '__main__':
    unittest.main()
