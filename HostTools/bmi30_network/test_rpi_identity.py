"""Regression tests for hardware identity with cloned/stale/missing metadata."""
import ast
import io
import re
from pathlib import Path
import unittest
import shutil
import uuid
from unittest.mock import patch as mock_patch

from patch_rpi_identity import patch
import install_rpi_identity as installer

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'FirmwareReleases/RaspberryPi/2026-10-21-1819/review/portal.py'


class HardwareIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = patch(SOURCE.read_text(encoding='utf-8'))
        tree = ast.parse(cls.source)
        names = {'_bmi30_hardware_serial', '_bmi30_hardware_hostname', 'group_local_rpi_id',
                 '_group_short_host_id', 'detect_rpi_identity', 'group_local_host_identity', '_ip_last_octet'}
        cls.code = compile(ast.Module(body=[n for n in tree.body
                           if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]),
                           'patched-portal', 'exec')

    def setUp(self):
        import ipaddress
        self.ns = {'re': re, 'Any': object, 'ipaddress': ipaddress, '_BMI30_HARDWARE_SERIAL': '',
                   '_read_first_text': lambda paths: '', 'detect_rpi_software_version': lambda: 'test',
                   '_group_publication_ip': lambda preferred: preferred or '10.43.0.1'}
        exec(self.code, self.ns)

    def files(self, values):
        def read(name, *args, **kwargs):
            if name not in values:
                raise FileNotFoundError(name)
            return io.StringIO(values[name])
        return mock_patch('builtins.open', side_effect=read)

    def test_hardware_overrides_other_boards_hostname(self):
        with self.files({'/proc/device-tree/serial-number': '100000027448d72b\x00\n'}):
            self.assertEqual(self.ns['group_local_rpi_id']('BMI30-05AB38E6A'), '27448D72B')
            self.assertEqual(self.ns['_group_short_host_id']('BMI30-556E5FA3B'), '27448D72B')
            self.assertEqual(self.ns['_bmi30_hardware_hostname'](), 'BMI30-27448D72B')
            host = self.ns['group_local_host_identity']()
            self.assertEqual(host['host_id'], '27448D72B')
            self.assertEqual(host['rpi_id'], '27448D72B')
            self.assertEqual(host['label'], '27448D72B/1')

    def test_device_tree_precedes_stale_cpuinfo(self):
        with self.files({'/proc/device-tree/serial-number': '100000027448d72b\x00',
                         '/proc/cpuinfo': 'Serial: 100000005ab38e6a\n'}):
            self.assertEqual(self.ns['detect_rpi_identity']()['serial'], '100000027448D72B')

    def test_alternate_hardware_sources(self):
        for values in ({'/sys/firmware/devicetree/base/serial-number': '100000027448d72b\x00'},
                       {'/proc/cpuinfo': 'Hardware: BCM2712\nSerial\t: 100000027448d72b\n'}):
            with self.subTest(values=values), self.files(values):
                self.ns['_BMI30_HARDWARE_SERIAL'] = ''
                self.assertEqual(self.ns['group_local_rpi_id'](), '27448D72B')

    def test_missing_or_invalid_hardware_never_borrows_an_identity(self):
        for value in ('', '0000000000000000', '27448D72B', '100000027448d72b-junk'):
            with self.subTest(value=value), self.files({'/proc/device-tree/serial-number': value}):
                self.assertEqual(self.ns['group_local_rpi_id']('BMI30-05AB38E6A'), '')
                self.assertEqual(self.ns['_group_short_host_id']('556E5FA3B'), '')
                self.assertEqual(self.ns['_bmi30_hardware_hostname'](), '')

    def test_temporary_read_error_preserves_verified_local_number(self):
        with self.files({'/proc/device-tree/serial-number': '100000027448d72b\x00'}):
            self.assertEqual(self.ns['group_local_rpi_id'](), '27448D72B')
        with self.files({}):
            self.assertEqual(self.ns['group_local_rpi_id'](), '27448D72B')

    def test_new_process_on_another_cm5_reads_its_own_hardware(self):
        for serial, expected in (('100000027448d72b', '27448D72B'), ('10000007aa5ced27', '7AA5CED27')):
            self.ns['_BMI30_HARDWARE_SERIAL'] = ''
            with self.files({'/proc/device-tree/serial-number': serial}):
                self.assertEqual(self.ns['group_local_rpi_id']('BMI30-05AB38E6A'), expected)

    def test_patch_handles_both_current_portal_families(self):
        for source in (SOURCE, ROOT / 'FirmwareReleases/RaspberryPi/2026-10-21-1501/project/hotspot_info_server.py'):
            with self.subTest(source=source):
                patched = patch(source.read_text(encoding='utf-8'))
                compile(patched, str(source), 'exec')
                self.assertIn('"hostname": _bmi30_hardware_hostname()', patched)
                self.assertNotIn('if (!existing && lan.ip)', patched)
                self.assertNotIn('if (!existing && lanNode', patched)

    def test_reapplying_or_incompatible_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'already applied'):
            patch(self.source)
        with self.assertRaises(ValueError):
            patch('print("unrelated application")')


class InstallationTests(unittest.TestCase):
    def setUp(self):
        temp_root = ROOT / '.tmp'
        temp_root.mkdir(exist_ok=True)
        # CPython 3.14's mode-0700 temporary directories exclude the Windows
        # sandbox SID. Use an inherited workspace directory for these fixtures.
        self.base = (temp_root / ('rpi-identity-test-' + uuid.uuid4().hex)).resolve()
        self.assertEqual(self.base.parent, temp_root.resolve())
        self.base.mkdir()
        self.addCleanup(shutil.rmtree, self.base)
        self.docs = self.base / 'Documents'
        self.host = self.docs / 'host'
        self.host.mkdir(parents=True)
        original = SOURCE.read_bytes()
        self.installed = self.base / 'installed.py'
        self.selected = self.host / 'portal.py'
        self.mirror = self.docs / 'hotspot_info_server.py'
        for path in (self.installed, self.selected, self.mirror):
            path.write_bytes(original)
        (self.host / 'bmi30_split_active_version.env').write_text('BMI30_PORTAL_PATH=host/portal.py\n')
        (self.host / 'bmi30_config.json').write_text('{"settings_preserved":true}')

    def plan(self):
        return installer.build_plan(self.docs, self.installed)

    def test_plan_validates_every_mirror_without_writing_sources(self):
        before = {path: path.read_bytes() for path in (self.installed, self.selected, self.mirror)}
        plan, protected = self.plan()
        self.assertEqual(len(plan), 3)
        self.assertTrue(protected)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_forced_verification_failure_restores_every_source(self):
        plan, protected = self.plan()
        with mock_patch.object(installer, 'core_pid', return_value=1234), mock_patch.object(
                installer, 'restart_portal') as restart, mock_patch.object(
                installer, 'verify_portal', side_effect=RuntimeError('forced failure')):
            with self.assertRaisesRegex(RuntimeError, 'original portal files restored'):
                installer.apply_plan(plan, protected, 'BMI30-27448D72B', self.base / 'backup')
            self.assertEqual(restart.call_count, 2)
        for item in plan:
            self.assertEqual(item['path'].read_bytes(), item['original'])
        installer.check_unchanged(protected)

    def test_success_preserves_configuration_and_acquisition_owner(self):
        plan, protected = self.plan()
        with mock_patch.object(installer, 'core_pid', return_value=1234), mock_patch.object(
                installer, 'restart_portal') as restart, mock_patch.object(installer, 'verify_portal') as verify:
            result = installer.apply_plan(plan, protected, 'BMI30-27448D72B', self.base / 'backup')
            self.assertTrue(result['applied'])
            restart.assert_called_once_with()
            verify.assert_called_once_with('BMI30-27448D72B')
        for item in plan:
            self.assertEqual(item['path'].read_bytes(), item['candidate'])
        installer.check_unchanged(protected)

    def test_source_changed_since_inspection_aborts_before_backup_or_write(self):
        plan, protected = self.plan()
        self.mirror.write_text('concurrent source edit')
        with mock_patch.object(installer, 'core_pid', return_value=1234), mock_patch.object(
                installer, 'restart_portal') as restart:
            with self.assertRaisesRegex(RuntimeError, 'changed since validation'):
                installer.apply_plan(plan, protected, 'BMI30-27448D72B', self.base / 'backup')
            restart.assert_not_called()
        self.assertFalse((self.base / 'backup').exists())
        self.assertEqual(self.installed.read_bytes(), plan[0]['original'])

    def test_wrong_physical_device_is_rejected_before_reading_portal(self):
        with mock_patch('sys.argv', ['install_rpi_identity.py', '--expected-id', 'BMI30-27448D72B', '--apply']), mock_patch.object(
                installer, 'hardware_identity', return_value='BMI30-7AA5CED27'), mock_patch.object(
                installer, 'build_plan') as build:
            with self.assertRaisesRegex(RuntimeError, 'Wrong or unavailable'):
                installer.main()
            build.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
