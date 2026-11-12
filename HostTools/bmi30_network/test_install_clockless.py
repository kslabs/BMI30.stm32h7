"""Installation preflight, config preservation and complete rollback."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import install_clockless as installer
from test_clockless import directory

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / 'FirmwareReleases/RaspberryPi/2026-10-21-1819/review/portal.py'


class InstallationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Patch the real 1.7 MB portal once; fixtures still test complete plans.
        cls.original = SOURCE.read_bytes()
        cls.candidate = installer.portal_candidate(cls.original.decode()).encode()

    @contextlib.contextmanager
    def fixture(self):
        with directory() as root:
            docs=root/'Documents';host=docs/'host';host.mkdir(parents=True)
            selected=host/'portal.py';installed=root/'installed.py'
            for path in (selected,installed,docs/'hotspot_info_server.py'):
                path.write_bytes(self.original)
            (host/'bmi30_split_active_version.env').write_text('BMI30_PORTAL_PATH=host/portal.py\nBMI30_ENGINE_SOURCE=engine.py\n')
            (docs/'engine.py').write_text('# protected acquisition engine\n')
            (host/'bmi30_config.json').write_text('{"unchanged":true}')
            runtime=root/'runtime';runtime.mkdir()
            for name in installer.MODULES:
                if name not in installer.NEW_MODULES:(runtime/name).write_text('# original '+name+'\n')
            config=root/'config.json';config.write_text(json.dumps({'id':'BMI30-STALE','fleet_key':'17'*32}))
            yield root,docs,installed,runtime,config

    def plan(self,docs,installed,runtime,config):
        with patch.object(installer,'portal_candidate',return_value=self.candidate.decode()):
            return installer.build_plan(docs,installed,runtime,config)

    def test_dry_run_preserves_sources_and_plans_new_module(self):
        with self.fixture() as (root,docs,installed,runtime,config):
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            plan,protected=self.plan(docs,installed,runtime,config)
            self.assertEqual(len(plan),3+len(installer.MODULES))
            self.assertIsNone(next(p for p in plan if p['path'].name=='freshness.py')['original'])
            self.assertEqual(before,{p:p.read_bytes() for p in before})
            installer.base.check_unchanged(protected)

    def test_success_preserves_engine_settings_and_core_pid(self):
        with self.fixture() as (root,docs,installed,runtime,config):
            plan,protected=self.plan(docs,installed,runtime,config)
            with patch.object(installer.base,'hardware_identity',return_value='BMI30-27448D72B'),patch.object(
                    installer.base,'core_pid',return_value=1234),patch.object(installer,'restart_services') as restart,patch.object(
                    installer,'verify_runtime') as verify:
                result=installer.apply_plan(plan,protected,'BMI30-27448D72B',root/'backup')
                self.assertTrue(result['applied']);restart.assert_called_once_with()
                verify.assert_called_once_with('BMI30-27448D72B')
            for item in plan:self.assertEqual(item['path'].read_bytes(),item['candidate'])
            installer.base.check_unchanged(protected)

    def test_failure_restores_old_files_and_removes_new_module(self):
        with self.fixture() as (root,docs,installed,runtime,config):
            plan,protected=self.plan(docs,installed,runtime,config)
            with patch.object(installer.base,'hardware_identity',return_value='BMI30-27448D72B'),patch.object(
                    installer.base,'core_pid',return_value=1234),patch.object(installer,'restart_services') as restart,patch.object(
                    installer,'verify_runtime',side_effect=RuntimeError('forced failure')):
                with self.assertRaisesRegex(RuntimeError,'all original files restored'):
                    installer.apply_plan(plan,protected,'BMI30-27448D72B',root/'backup')
                self.assertEqual(restart.call_count,2)
            for item in plan:
                if item['original'] is None:self.assertFalse(item['path'].exists())
                else:self.assertEqual(item['path'].read_bytes(),item['original'])
            installer.base.check_unchanged(protected)

    def test_identity_mismatch_or_changed_source_prevents_any_write(self):
        with self.fixture() as (root,docs,installed,runtime,config):
            plan,protected=self.plan(docs,installed,runtime,config)
            with patch.object(installer.base,'hardware_identity',return_value='BMI30-05AB38E6A'):
                with self.assertRaisesRegex(RuntimeError,'physical CM5 identity'):
                    installer.apply_plan(plan,protected,'BMI30-27448D72B',root/'backup')
            self.assertFalse((root/'backup').exists())
            installed.write_text('# changed after preflight\n')
            with patch.object(installer.base,'hardware_identity',return_value='BMI30-27448D72B'),patch.object(
                    installer.base,'core_pid',return_value=1234):
                with self.assertRaisesRegex(RuntimeError,'Source changed'):
                    installer.apply_plan(plan,protected,'BMI30-27448D72B',root/'backup')
            self.assertFalse((runtime/'freshness.py').exists())

    def test_runtime_verification_requires_clock_mode_network_version_and_physical_identity(self):
        states=[{'hostname':'BMI30-27448D72B','rpi_identity_version':installer.base.VERSION,
                 'service_clock_mode':'monotonic-v2','service_transport_version':'1'}, {'version':'0.2.4','clock_mode':'monotonic-v2',
                                                        'bluetooth':{'enabled':True,'state':'listening'}}]
        opener=type('Opener',(),{'open':lambda obj,*args,**kw:contextlib.closing(io.BytesIO(json.dumps(states.pop(0)).encode()))})()
        with patch.object(installer.urllib.request,'build_opener',return_value=opener):
            installer.verify_runtime('BMI30-27448D72B')

    def test_both_real_portal_families_compile_and_incompatible_input_is_rejected(self):
        old=HERE.parents[1]/'FirmwareReleases/RaspberryPi/2026-10-21-1501/project/hotspot_info_server.py'
        candidate=installer.portal_candidate(old.read_text(encoding='utf-8'))
        compile(candidate,'clockless-portal','exec')
        self.assertIn(installer.clock.PATCH_MARKER,candidate)
        self.assertEqual(installer.portal_candidate(candidate),candidate)
        with self.assertRaises(Exception):installer.portal_candidate('print("unrelated")')


if __name__=='__main__':unittest.main()
