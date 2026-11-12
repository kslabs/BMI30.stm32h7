import json,os,pathlib,subprocess,tempfile,unittest
from integrate_bundle import add_to_bundle,validate_included,MODULE_PREFIX
from patch_bundle_builder import patch,MARKER
from bundle_feature_cli import include
ROOT=pathlib.Path(__file__).resolve().parents[2]
PORTAL=ROOT/'.tmp/usb-recovery-20261028/M02-portal-before.py'

class PersistenceTests(unittest.TestCase):
    def test_new_bundles_contain_unrestricted_feature_without_changing_detector_code(self):
        files={'project/portal.py':PORTAL.read_bytes(),'project/host/core.py':b'core unchanged'}
        manifest={'BMI30_BUNDLE_PORTAL_REL':'project/portal.py'}
        add_to_bundle(files,manifest,'2026-10-28-120000');validate_included(files,manifest)
        self.assertEqual(files['project/host/core.py'],b'core unchanged')
        cfg=json.loads(files[MODULE_PREFIX+'config.json'])
        self.assertNotIn('source_serial',cfg);self.assertNotIn('target_serial',cfg)
        self.assertEqual(cfg['internal_storage_on_usb_boot'],'read-own-settings-if-available')
    def test_missing_feature_or_reintroduced_board_binding_is_rejected(self):
        files={'project/portal.py':PORTAL.read_bytes()};manifest={'BMI30_BUNDLE_PORTAL_REL':'project/portal.py'}
        add_to_bundle(files,manifest,'version')
        cfg=json.loads(files[MODULE_PREFIX+'config.json']);cfg['target_serial']='bad'
        files[MODULE_PREFIX+'config.json']=json.dumps(cfg).encode()
        with self.assertRaises(ValueError):validate_included(files,manifest)
        del files[MODULE_PREFIX+'usb_recovery_adopt.py']
        with self.assertRaises(ValueError):validate_included(files,manifest)
    @unittest.skipIf(os.name=='nt','Standard builder is Linux bash')
    def test_standard_builder_hook_runs_before_hashes_and_is_idempotent(self):
        source=(ROOT/'.tmp/usb-recovery-20261028/bundle-builder-before.sh').read_text()
        candidate=patch(source)
        self.assertEqual(candidate,patch(candidate));self.assertIn(MARKER,candidate)
        self.assertLess(candidate.index('python3 "$emergency_source/bundle_feature_cli.py"'),candidate.index('    write_release_manifest "$tmp_dir"'))
        result=subprocess.run(['bash','-n'],input=candidate,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
    @unittest.skipIf(os.name=='nt','Linux bundle staging')
    def test_stock_builder_cli_repackages_feature_even_from_older_portal_source(self):
        with tempfile.TemporaryDirectory(prefix='bmi30-feature-stage-') as directory:
            stage=pathlib.Path(directory);portal=stage/'project/hotspot_info_server.py'
            portal.parent.mkdir(parents=True);portal.write_bytes(PORTAL.read_bytes())
            include(stage,'2026-10-28-120000')
            cfg=json.loads((stage/MODULE_PREFIX/'config.json').read_text())
            self.assertEqual(cfg['version'],'2026-10-28-120000')
            self.assertIn('_BMI30_USB_MODULE',portal.read_text())

if __name__=='__main__':unittest.main()
