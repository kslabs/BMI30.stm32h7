"""Offline function/fixture checks; never import a route or run device commands."""
import ast
import copy
import hashlib
import importlib.util
import json
import math
import pathlib
import re
import unittest
from unittest import mock

W=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('boot_controller_offline',W/'boot_control.py')
controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)
tree=ast.parse(controller.REMOTE)
definitions=ast.Module(body=[node for node in tree.body if isinstance(node,(ast.ClassDef,ast.FunctionDef))],type_ignores=[])

class BootGuards(unittest.TestCase):
    def setUp(self):
        self.plan=json.loads((W/'s07-plan.json').read_bytes())
        self.result=json.loads((W/'s07-copy-result.json').read_bytes())
        self.ns={'math':math,'re':re,'json':json,'hashlib':hashlib,'plan':self.plan,'p':{'expected_role':'slave','expected_id':7,'expected_code':'S07'},'P':pathlib.Path}
        exec(compile(definitions,'<remote function definitions>','exec'),self.ns)
        frozen=self.result['final_runtime']
        self.status={'ok':True,'stale':False,'connection':{'connected':True,'last_frame_age_s':.01},
            'service':{'engine_source':self.plan['sources']['BMI30_ENGINE_SOURCE']['path'],'version':'001','uptime_s':31},
            'detector':{'active':True,'worker':{'alive':True,'fresh_pairs':{'window_counts':[12]*4}},
             'settings':copy.deepcopy(frozen['settings']),'channels':copy.deepcopy(frozen['channel_policy']),
             'noise_valid0':True,'noise_valid1':True,'noise_avg0':20.,'noise_avg1':30.},
            'mode':copy.deepcopy(frozen['mode']),'sync':copy.deepcopy(frozen['sync']),
            'optimize':{'fast_adapt_active':False,'dc_autotune_active':False},
            'player_recorder':{'recording':False,'playback_active':False}}
    def healthy(self):return self.ns['healthy'](self.status,self.result,True)
    def refused(self):
        with self.assertRaises(self.ns['NotReady']):self.healthy()
    def test_real_final_snapshot_and_public_001_version_are_supported(self):
        self.assertEqual(self.healthy()['device_id'],7)
    def test_learning_or_invalid_noise_refuses(self):
        self.status['service']['uptime_s']=29;self.refused()
        self.status['service']['uptime_s']=31
        for value in (0.,float('nan'),float('inf')):
            self.status['detector']['noise_avg0']=value;self.refused()
    def test_phase_window_not_full_refuses(self):
        self.status['detector']['worker']['fresh_pairs']['window_counts']=[12,12,11,12];self.refused()
    def test_lightning_and_autotune_refuse(self):
        for key in ('fast_adapt_active','dc_autotune_active'):
            self.status['optimize'][key]=True;self.refused();self.status['optimize'][key]=False
        self.status['dc_guard']={'lightning':{'active':True}};self.refused()
    def test_policy_and_mode_drift_refuse(self):
        self.status['mode']['rpi_avg_n']=1;self.refused()
        self.status['mode']=copy.deepcopy(self.result['final_runtime']['mode'])
        self.status['detector']['channels']['upper']['threshold']+=.1;self.refused()
    def test_wrong_role_and_stale_connection_refuse(self):
        self.status['sync']['device_id']=2;self.refused()
        self.status['sync']=copy.deepcopy(self.result['final_runtime']['sync'])
        self.status['connection']['last_frame_age_s']=-1;self.refused()
    def test_copy_result_rejects_unsafe_or_foreign_evidence(self):
        self.ns['p']['copy_result']=copy.deepcopy(self.result)
        self.assertEqual(self.ns['get_result']()['job_id'],self.plan['job_id'])
        for key,value in (('safe_to_remove',False),('host','other'),('source_root_partuuid','foreign')):
            changed=copy.deepcopy(self.result);changed[key]=value;self.ns['p']['copy_result']=changed
            with self.assertRaises(self.ns['NotReady']):self.ns['get_result']()
    def test_no_eeprom_flash_flags_and_preboot_local_fsync(self):
        remote=controller.REMOTE
        self.assertIn("['rpi-eeprom-update']",remote)
        self.assertNotIn("'rpi-eeprom-update','-a'",remote)
        self.assertIn("'vcmailbox','0x0003808b','4','4','0xf41'",remote)
        path=W/'offline-test-prior.json'
        self.assertFalse(path.exists())
        try:
            controller.save(path,{'prior_boot_id':'before'})
            self.assertEqual(json.loads(path.read_bytes())['prior_boot_id'],'before')
            self.assertFalse(path.with_name(path.name+'.tmp').exists())
        finally:
            if path.is_file():path.unlink()
    def media_fixture(self,current='emmc',mounted=False,wrong_boot=False):
        result=self.result;plan=self.plan
        er={'path':'/dev/mmcblk0p2','type':'part','partuuid':plan['root_partuuid'],'uuid':'emmcroot','mountpoints':['/']if current=='emmc'else[]}
        eb={'path':'/dev/mmcblk0p1','type':'part','partuuid':plan['boot_partuuid'],'uuid':'emmcboot','mountpoints':['/boot/firmware']if current=='emmc'else[]}
        ur={'path':'/dev/sda2','type':'part','partuuid':result['root_partuuid'],'uuid':result['root_uuid'],'mountpoints':['/']if current=='usb'else(['/mnt/unsafe']if mounted else[])}
        ub={'path':'/dev/sda1','type':'part','partuuid':result['boot_partuuid'],'uuid':result['boot_uuid'],'mountpoints':['/boot/firmware']if current=='usb'else[]}
        rows={'blockdevices':[{'path':'/dev/mmcblk0','type':'disk','children':[eb,er]},
              {'path':'/dev/sda','type':'disk','serial':plan['target']['serial'],'tran':'usb','size':plan['target']['size'],'ro':False,'children':[ub,ur]}]}
        def run(*args):
            if args[0]=='lsblk':
                self.assertIn('--tree',args)
                self.assertIn('NAME',args[-1].split(','))
                return json.dumps(rows)
            node=(er if current=='emmc'else ur)if args[-1]=='/'else(eb if current=='emmc'else ub)
            if wrong_boot and args[-1]=='/boot/firmware':node=ub if current=='emmc'else eb
            return node['path']if args[3]=='SOURCE'else node['partuuid']
        self.ns['run']=run
        prop=mock.Mock();prop.is_file.return_value=True;prop.read_bytes.return_value=(1 if current=='emmc' else 4).to_bytes(4,'big')
        self.ns['P']=lambda *args:prop
        return rows
    def test_actual_emmc_or_usb_parent_and_partition_selection(self):
        for medium in ('emmc','usb'):
            self.media_fixture(medium)
            found=self.ns['media'](self.result)
            self.assertEqual(found['current'],medium)
            self.assertEqual(found['boot_mode'],1 if medium=='emmc' else 4)
    def test_mixed_root_boot_or_mounted_target_refuses(self):
        for options in ({'mounted':True},{'wrong_boot':True}):
            self.media_fixture(**options)
            with self.assertRaises(self.ns['NotReady']):self.ns['media'](self.result)
    def test_foreign_usb_and_duplicate_serial_refuse(self):
        for duplicate in (False,True):
            rows=self.media_fixture()
            if duplicate:rows['blockdevices'].append(copy.deepcopy(rows['blockdevices'][1]))
            else:rows['blockdevices'][1]['serial']='foreign'
            with self.assertRaises(self.ns['NotReady']):self.ns['media'](self.result)
    def config_hash(self,content):
        path=W/'offline-test-canonical.json'
        self.assertFalse(path.exists())
        try:
            path.write_bytes(content.encode('utf8'))
            return self.ns['semantic_config_sha'](path)
        finally:
            if path.is_file():path.unlink()
    def test_canonical_format_order_and_unicode_are_lossless(self):
        text='{"z":[true,1,1.0],"a":{"note":"Привет"}}'
        a=self.config_hash(text)
        b=self.config_hash(json.dumps(json.loads(text),indent=4,sort_keys=True))
        expected=hashlib.sha256(controller.canon(json.loads(text))).hexdigest()
        self.assertEqual(a,b);self.assertEqual(a,expected)
    def test_config_value_type_and_key_changes_are_not_formatting(self):
        baseline=self.config_hash('{"flag":true,"n":1}')
        for changed in ('{"flag":1,"n":1}','{"flag":true,"n":1.0}','{"flag":true,"n":2}','{"flag":true}'):
            self.assertNotEqual(baseline,self.config_hash(changed))
    def test_duplicate_and_nonfinite_json_fail_closed(self):
        for changed in ('{"a":1,"a":1}','{"outer":{"x":1,"x":2}}','{"a":NaN}','{"a":Infinity}'):
            with self.assertRaises(self.ns['NotReady']):self.config_hash(changed)
    def test_comparison_allows_only_byte_serialization_drift(self):
        before={'config_sha256':'old-bytes','config_semantic_sha256':'same-values','selection_env_sha256':'same-env','source_sha256':'same-code'}
        after={**before,'config_sha256':'new-bytes'}
        self.assertTrue(self.ns['same_sources'](before,after))
        for key in ('config_semantic_sha256','selection_env_sha256','source_sha256'):
            self.assertFalse(self.ns['same_sources'](before,{**after,key:'changed'}))

if __name__=='__main__':unittest.main()
