import ast,hashlib,json,os,pathlib,re,tempfile,types,unittest
from unittest.mock import Mock,patch
from patch_portal_launcher import HELPER,patch as patch_source
ROOT=pathlib.Path(__file__).resolve().parents[2]

class LauncherTests(unittest.TestCase):
    def context(self,root,valid=True):
        workspace=root/'project';workspace.mkdir();portal=workspace/'selected/portal.py';portal.parent.mkdir();portal.write_text('print("new portal")\n')
        unit=root/'system/bmi30-hotspot-info.service';unit.parent.mkdir();unit.write_text('[Service]\nExecStart=old-portal\n')
        real_path=pathlib.Path
        def path(value):return unit if str(value)=='/etc/systemd/system/bmi30-hotspot-info.service' else real_path(value)
        proc=Mock()
        context={'os':types.SimpleNamespace(geteuid=lambda:0,fsync=os.fsync),'pathlib':types.SimpleNamespace(Path=path),
                 'hashlib':hashlib,'json':json,'re':re,'subprocess':proc,'FIRMWARE_WORKSPACE':workspace,
                 'detect_bmi30_firmware_release':lambda:{'portal_path':'selected/portal.py','portal_sha256':hashlib.sha256(portal.read_bytes()).hexdigest() if valid else '0'*64},
                 '_resolve_project_path':lambda name:workspace/name}
        exec(HELPER,context);return context,unit,portal,proc
    def test_launcher_uses_verified_release_after_old_service_is_reinstalled(self):
        with tempfile.TemporaryDirectory() as directory:
            context,unit,portal,proc=self.context(pathlib.Path(directory))
            self.assertTrue(context['ensure_versioned_portal_launcher']())
            override=unit.with_name('bmi30-hotspot-info.service.d')/'06-bmi30-versioned-portal.conf'
            self.assertIn(str(portal).replace('\\','\\\\'),override.read_text())
            unit.write_text('[Service]\nExecStart=another-old-portal\n')
            self.assertTrue(override.is_file());self.assertTrue(context['ensure_versioned_portal_launcher']())
            proc.run.assert_called_once()
    def test_invalid_selected_portal_hash_never_changes_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            context,unit,portal,proc=self.context(pathlib.Path(directory),False)
            self.assertFalse(context['ensure_versioned_portal_launcher']());proc.run.assert_not_called()
            self.assertFalse(unit.with_name('bmi30-hotspot-info.service.d').exists())
    def test_no_core_or_stm32_query_is_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            context,unit,portal,proc=self.context(pathlib.Path(directory))
            self.assertTrue(context['ensure_versioned_portal_launcher']())
            self.assertEqual(proc.run.call_args.args[0],['systemctl','daemon-reload'])
    def test_patch_keeps_hash_verification_and_is_idempotent(self):
        source=(ROOT/'.tmp/usb-recovery-20261028/M02-portal-before.py').read_text(encoding='utf-8')
        candidate=patch_source(source);self.assertEqual(candidate,patch_source(candidate));ast.parse(candidate)
        self.assertIn('ensure_versioned_portal_launcher()',candidate)

if __name__=='__main__':unittest.main()
