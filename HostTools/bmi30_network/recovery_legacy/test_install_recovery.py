"""Preserve working access, arm rollback first, and never overwrite a later update."""
import contextlib
import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

import install_recovery as install
import patch_recovery
from test_recovery import HERE, ORIGINAL, DONOR


@contextlib.contextmanager
def fixture():
    root = HERE / ('install-test-' + uuid.uuid4().hex)
    root.mkdir()
    try:
        runtime, payload, backups = root / 'runtime', root / 'payload', root / 'backups'
        runtime.mkdir()
        payload.mkdir()
        config = root / 'config.json'
        config.write_text(json.dumps({'id': DONOR, 'fleet_key': 'test-key-never-output'}))
        (runtime / 'service.py').write_text(ORIGINAL, encoding='utf-8', newline='\n')
        protected = {str(config): install.digest(config.read_bytes())}
        for name in ('protocol.py', 'network.py', 'group.py', 'policy.py'):
            path = runtime / name
            path.write_text('# unchanged dependency ' + name + '\n')
            protected[str(path)] = install.digest(path.read_bytes())
        for name in ('engine.py', 'portal.py', 'updater.conf', 'wifi-profile'):
            path = root / name
            path.write_text('# preserved ' + name + '\n')
            protected[str(path)] = install.digest(path.read_bytes())
        source = patch_recovery.patch(ORIGINAL).encode()
        (payload / 'service.py').write_bytes(source)
        (payload / 'legacy_recovery.py').write_bytes((HERE / 'legacy_recovery.py').read_bytes())
        manifest = {'target_id': install.TARGET_ID, 'donor_ids': list(install.DONOR_IDS),
                    'network_version': patch_recovery.VERSION, 'changed_runtime_files': list(install.FILES),
                    'base_service_sha256': install.digest(ORIGINAL.encode()),
                    'payload_sha256': {name: install.digest((payload / name).read_bytes()) for name in install.FILES}}
        (payload / 'manifest.json').write_text(json.dumps(manifest))
        with patch.object(install, 'hardware_identity', return_value=DONOR), patch.object(
                install, 'protected_files', return_value=protected):
            yield root, runtime, payload, backups, config, protected
    finally:
        # Every recursive cleanup is checked against this test's workspace.
        assert root.resolve().parent == HERE.resolve() and root.name.startswith('install-test-')
        shutil.rmtree(root)


class InstallTests(unittest.TestCase):
    def test_approved_previous_pair_upgrades_and_rolls_back_to_that_pair(self):
        with fixture() as (root, runtime, payload, backups, config, protected):
            previous = {'service.py': b'# approved recovery3 service\n',
                        'legacy_recovery.py': b'# approved recovery3 helper\n'}
            for name, content in previous.items():
                (runtime / name).write_bytes(content)
            manifest = json.loads((payload / 'manifest.json').read_text())
            manifest['accepted_previous_payloads'] = [{name: install.digest(content) for name, content in previous.items()}]
            (payload / 'manifest.json').write_text(json.dumps(manifest))
            with patch.object(install, 'command', side_effect=self.commands), patch.object(install, 'verify'):
                result = self.apply(runtime, payload, backups, config)
                self.assertTrue(install.rollback(Path(result['backup']), DONOR)['restored'])
            self.assertEqual({name: (runtime / name).read_bytes() for name in previous}, previous)
            (runtime / 'legacy_recovery.py').write_bytes(b'# unrelated helper\n')
            with self.assertRaises(RuntimeError):
                install.prepare(DONOR, runtime, payload, config)

    def commands(self, *args, **kwargs):
        if args[:3] == ('systemctl', 'is-active', 'bmi30-network'):
            return 'active'
        if args[:3] == ('systemctl', 'show', 'bmi30-core'):
            return '4242'
        return ''

    def apply(self, runtime, payload, backups, config):
        return install.apply(DONOR, runtime, payload, backups, config)

    def test_dry_run_changes_no_files_and_checks_exact_identity(self):
        with fixture() as (root, runtime, payload, backups, config, protected):
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            plan, manifest, hashes = install.prepare(DONOR, runtime, payload, config)
            self.assertEqual([p['name'] for p in plan], list(install.FILES))
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            self.assertFalse(backups.exists())
            install.unchanged(hashes)
            with patch.object(install, 'hardware_identity', return_value=install.TARGET_ID):
                with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
                    install.prepare(DONOR, runtime, payload, config)

    def test_timer_is_armed_before_any_runtime_write(self):
        with fixture() as (root, runtime, payload, backups, config, protected):
            calls = []

            def command(*args, **kwargs):
                calls.append(args)
                if args[0] == 'systemd-run':
                    self.assertEqual((runtime / 'service.py').read_text(), ORIGINAL)
                    self.assertFalse((runtime / 'legacy_recovery.py').exists())
                    self.assertIn('--automatic', args)
                    self.assertIn('--on-active=300s', args)
                return self.commands(*args, **kwargs)

            with patch.object(install, 'command', side_effect=command), patch.object(install, 'verify'):
                result = self.apply(runtime, payload, backups, config)
            self.assertEqual(result['stage'], 'awaiting-external-commit')
            self.assertEqual((runtime / 'service.py').read_bytes(), (payload / 'service.py').read_bytes())
            self.assertTrue((Path(result['backup']) / 'rollback_tool.py').is_file())
            self.assertTrue((runtime / 'legacy_recovery.py').is_file())
            self.assertEqual([c for c in calls if c[:2] == ('systemctl', 'restart')],
                             [('systemctl', 'restart', 'bmi30-network')])
            self.assertFalse(any(c[0] in ('nmcli', 'iw', 'date', 'timedatectl') for c in calls))
            install.unchanged(protected)

    def test_health_failure_rolls_back_immediately(self):
        with fixture() as (root, runtime, payload, backups, config, protected):
            with patch.object(install, 'command', side_effect=self.commands) as command, patch.object(
                    install, 'verify', side_effect=RuntimeError('forced health failure')):
                with self.assertRaisesRegex(RuntimeError, 'forced health failure'):
                    self.apply(runtime, payload, backups, config)
            self.assertEqual((runtime / 'service.py').read_text(), ORIGINAL)
            self.assertFalse((runtime / 'legacy_recovery.py').exists())
            state = json.loads(next(backups.glob('*/state.json')).read_text())
            self.assertEqual(state['stage'], 'rolled-back')
            self.assertEqual(sum(c.args == ('systemctl', 'restart', 'bmi30-network') for c in command.call_args_list), 2)
            install.unchanged(protected)

    def test_automatic_rollback_restores_original_without_external_commit(self):
        with fixture() as (root, runtime, payload, backups, config, protected):
            with patch.object(install, 'command', side_effect=self.commands), patch.object(install, 'verify'):
                result = self.apply(runtime, payload, backups, config)
                state = install.rollback(Path(result['backup']), DONOR, automatic=True)
            self.assertTrue(state['restored'])
            self.assertEqual((runtime / 'service.py').read_text(), ORIGINAL)
            self.assertFalse((runtime / 'legacy_recovery.py').exists())
            install.unchanged(protected)

    def test_commit_cancels_timer_and_late_timer_cannot_undo_commit(self):
        with fixture() as (root, runtime, payload, backups, config, protected):
            with patch.object(install, 'command', side_effect=self.commands) as command, patch.object(install, 'verify'):
                result = self.apply(runtime, payload, backups, config)
                backup = Path(result['backup'])
                self.assertEqual(install.commit(backup, DONOR)['stage'], 'committed')
                self.assertEqual(install.rollback(backup, DONOR, automatic=True)['stage'], 'committed')
                self.assertEqual((runtime / 'service.py').read_bytes(), (payload / 'service.py').read_bytes())
                self.assertTrue(install.rollback(backup, DONOR)['restored'])
            self.assertEqual((runtime / 'service.py').read_text(), ORIGINAL)
            install.unchanged(protected)

    def test_later_firmware_wins_over_pending_or_manual_rollback(self):
        for changed_file in ('service.py', 'protocol.py'):
            with self.subTest(changed_file=changed_file), fixture() as (root, runtime, payload, backups, config, protected):
                with patch.object(install, 'command', side_effect=self.commands), patch.object(install, 'verify'):
                    result = self.apply(runtime, payload, backups, config)
                    newer = b'# a separately installed later firmware\n'
                    (runtime / changed_file).write_bytes(newer)
                    state = install.rollback(Path(result['backup']), DONOR, automatic=True)
                self.assertEqual(state['stage'], 'superseded-by-another-update')
                self.assertEqual((runtime / changed_file).read_bytes(), newer)

    def test_corrupt_backup_is_detected_before_deleting_the_working_module(self):
        with fixture() as (root, runtime, payload, backups, config, protected):
            with patch.object(install, 'command', side_effect=self.commands), patch.object(install, 'verify'):
                result = self.apply(runtime, payload, backups, config)
                backup = Path(result['backup'])
                (backup / 'original-0').write_bytes(b'corrupt backup')
                with self.assertRaisesRegex(RuntimeError, 'backup checksum mismatch'):
                    install.rollback(backup, DONOR)
            for name in install.FILES:
                self.assertEqual((runtime / name).read_bytes(), (payload / name).read_bytes())

    def test_unknown_source_or_config_identity_is_rejected(self):
        with fixture() as (root, runtime, payload, backups, config, protected):
            config.write_text(json.dumps({'id': install.TARGET_ID}))
            with self.assertRaisesRegex(RuntimeError, 'Network identity'):
                install.prepare(DONOR, runtime, payload, config)
            config.write_text(json.dumps({'id': DONOR}))
            (runtime / 'service.py').write_text('# different installed code\n')
            with self.assertRaisesRegex(RuntimeError, 'differs from the shipped'):
                install.prepare(DONOR, runtime, payload, config)
            self.assertFalse((runtime / 'legacy_recovery.py').exists())
            self.assertFalse(backups.exists())

    def test_retry_after_lost_ssh_output_recovers_the_pending_transaction(self):
        with fixture() as (root, runtime, payload, backups, config, protected):
            with patch.object(install, 'command', side_effect=self.commands) as command, patch.object(install, 'verify'):
                first = self.apply(runtime, payload, backups, config)
                second = self.apply(runtime, payload, backups, config)
            self.assertEqual(second['stage'], 'awaiting-external-commit')
            self.assertEqual(second['backup'], first['backup'])
            self.assertEqual(sum(c.args[0] == 'systemd-run' for c in command.call_args_list), 1)


if __name__ == '__main__':
    unittest.main()
