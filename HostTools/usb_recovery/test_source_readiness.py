"""A completed application update must not expose a half-installed system to copying."""
import copy,json,os,pathlib,tempfile,unittest
from unittest.mock import patch
import usb_recovery_common as c
from test_usb_recovery import CFG,inventory


class SourceReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name);self.module=self.root/'host/bmi30_usb_recovery'
        self.module.mkdir(parents=True);self.cfg={**CFG,'version':'2026-10-29-125008'}
        self.inv=inventory();self.records={}
        manifest=self.module.parent/'bmi30_full_update/manifest.json';manifest.parent.mkdir()
        c.write_json(manifest,{'bundle_id':self.cfg['version']})
        self.manifest=manifest
        original=c.read_json
        def read(path,fallback=None):
            if str(path).startswith('/var/lib/'):return copy.deepcopy(self.records.get(str(path),fallback))
            return original(path,fallback)
        self.addCleanup(patch.stopall)
        patch.object(c,'MODULE',self.module).start();patch.object(c,'read_json',side_effect=read).start()

    def committed(self,**changes):
        value={'stage':'committed','kind':'full-system','bundle_id':self.cfg['version'],
            'expected_id':c.hostname(self.inv['physical_serial']),**changes}
        self.records['/var/lib/bmi30-legacy-menu/'+self.cfg['version']+'/state.json']=value

    def plan(self):return c.make_plan(self.inv,'/dev/sda',self.cfg,1,10)

    def test_pending_or_failed_full_install_cannot_be_copied(self):
        for stage in (None,'prepared','applying','failed','rolled-back'):
            with self.subTest(stage=stage):
                if stage is None:self.records.clear()
                else:self.committed(stage=stage)
                with self.assertRaises(c.Refused):self.plan()

    def test_committed_transaction_must_belong_to_current_hardware(self):
        self.committed(expected_id='BMI30-OTHER')
        with self.assertRaises(c.Refused):self.plan()
        self.committed();self.assertFalse(self.plan()['authorized'])

    def test_prepared_copy_is_rejected_if_system_state_changes_before_start(self):
        self.committed();plan=self.plan();self.committed(stage='applying')
        with self.assertRaises(c.Refused):c.revalidate(plan,self.inv,self.cfg,20)

    def test_adopted_usb_or_its_native_copy_does_not_need_donor_transaction(self):
        self.records['/var/lib/bmi30-portable-runtime.json']={'stage':'adopted',
            'physical_serial':self.inv['physical_serial'],'bundle_id':self.cfg['version']}
        self.assertFalse(self.plan()['authorized'])
        self.records['/var/lib/bmi30-portable-runtime.json']['physical_serial']='abdd2dbc9775fbae'
        with self.assertRaises(c.Refused):self.plan()

    def test_portable_marker_from_previous_release_cannot_bypass_pending_update(self):
        self.records['/var/lib/bmi30-portable-runtime.json']={'stage':'adopted',
            'physical_serial':self.inv['physical_serial'],'bundle_id':'old'}
        with self.assertRaises(c.Refused):self.plan()

    def test_corrupt_or_mismatched_system_manifest_is_blocked(self):
        self.committed()
        for data in ('{',json.dumps({'bundle_id':'old'})):
            self.manifest.write_text(data)
            with self.assertRaises(c.Refused):self.plan()

    def test_standalone_application_bundle_does_not_require_system_migration(self):
        self.manifest.unlink();self.assertFalse(self.plan()['authorized'])


@unittest.skipIf(os.name=='nt','Portal backend uses Linux process locks')
class CompletedMediaTests(unittest.TestCase):
    def setUp(self):
        import usb_recovery_backend as backend
        self.b=backend;self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=pathlib.Path(self.temp.name);self.cfg={**CFG,'version':'current'};self.inv=inventory()
        self.plan=c.make_plan(self.inv,'/dev/sda',self.cfg,1,10)
        folder=self.state/'jobs'/self.plan['id'];folder.mkdir(parents=True)
        c.write_json(self.state/'latest.json',{'id':self.plan['id'],'unit':'completed-test'})
        c.write_json(folder/'plan.json',self.plan)
        c.write_json(folder/'status.json',{'stage':'complete','progress':100,'safe_to_remove':True})
        p=patch.object(backend,'STATE',self.state);p.start();self.addCleanup(p.stop)

    def test_current_attached_copy_keeps_completed_result(self):
        value=self.b.latest(self.inv,self.cfg)
        self.assertEqual(value['stage'],'complete');self.assertTrue(value['safe_to_remove'])

    def test_replaced_or_disconnected_usb_cannot_inherit_old_ready_message(self):
        for change in ('removed','diskseq','serial'):
            inv=copy.deepcopy(self.inv)
            if change=='removed':inv['blockdevices'].pop()
            else:inv['blockdevices'][1][change]='replacement'
            with self.subTest(change=change):
                value=self.b.latest(inv,self.cfg)
                self.assertEqual(value['stage'],'idle');self.assertFalse(value['safe_to_remove'])
                self.assertEqual(value['previous_copy']['id'],self.plan['id'])

    def test_firmware_change_or_reboot_does_not_claim_new_system_was_copied(self):
        for inv,cfg in ((self.inv,{**self.cfg,'version':'new'}),({**self.inv,'boot_id':'new'},self.cfg)):
            value=self.b.latest(inv,cfg)
            self.assertEqual(value['progress'],0);self.assertEqual(value['stage'],'idle')


if __name__=='__main__':unittest.main()
