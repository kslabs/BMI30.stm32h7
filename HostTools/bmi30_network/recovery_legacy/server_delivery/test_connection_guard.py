"""Connectivity regression cases without touching live networking or services."""
import importlib.util
import ast
import contextlib
import json
import os
import pathlib
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

import full_component as component


class ConnectionGuardTests(unittest.TestCase):
    def state(self):
        with patch.object(component, 'boot_id', return_value='boot-a'), \
             patch.object(component.time, 'monotonic', return_value=100), \
             patch.object(component, 'run', return_value='active'), \
             patch.object(component, 'server_probe', return_value={
                 'ok': True, 'http_code': 200, 'reason': 'reachable', 'address': '203.0.113.20'}):
            watch = component.arm_connectivity_watch({'uuid': 'saved-station', 'address': '172.18.10.68/24'})
        return {'expected_id': 'BMI30-TEST', 'bundle_id': 'test-release', 'connectivity_watch': watch}

    def healthy_local(self, module=component):
        return patch.multiple(module,
            station_identity=lambda: {'uuid': 'saved-station', 'address': '172.18.10.68/24'},
            api=lambda port: {'hostname': 'BMI30-TEST', 'raspberry_firmware_version': 'test-release',
                              'firmware_release': {'portal_matches_release': True}},
            boot_id=lambda: 'boot-a')

    def test_published_guard_can_rollback_for_dns_failure_with_healthy_local_services(self):
        root = pathlib.Path(__file__).resolve().parents[4]
        published = root / 'FirmwareReleases/RaspberryPi/2026-10-30-095430/review/bundle/project/host/bmi30_full_update/component.py'
        spec = importlib.util.spec_from_file_location('published_095430_guard', published)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        state = self.state()
        with self.healthy_local(old), patch.object(old, 'run', side_effect=lambda *args, **kw: '000' if args[0] == 'curl' else 'active'):
            self.assertFalse(old.connectivity_step(state, 110))
            self.assertTrue(old.connectivity_step(state, 231))
        self.assertEqual(state['connectivity_watch']['status'], 'rollback-required')

    def test_dns_failure_uses_verified_ip_with_same_tls_hostname_and_keeps_new_release(self):
        state = self.state()
        calls = []
        def run(*args, **kwargs):
            calls.append(args)
            if args[0] != 'curl':
                return 'active'
            return '200 203.0.113.20 0' if '--resolve' in args else '000  6'
        with self.healthy_local(), patch.object(component, 'run', side_effect=run):
            self.assertFalse(component.connectivity_step(state, 110))
            self.assertFalse(component.connectivity_step(state, 231))
            self.assertFalse(component.connectivity_step(state, 1001))
        watch = state['connectivity_watch']
        self.assertEqual(watch['status'], 'passed')
        self.assertEqual(watch['server_probe']['primary_reason'], 'dns-failed')
        self.assertEqual(watch['server_probe']['method'], 'cached-ip-tls')
        fallback = next(c for c in calls if '--resolve' in c)
        self.assertIn('www.teiots.net:443:203.0.113.20', fallback)
        self.assertEqual(fallback[-1], component.CONNECTIVITY_URL)
        self.assertNotIn('--insecure', fallback)
        self.assertNotIn('-k', fallback)
        self.assertNotIn('--location', fallback)

    def test_certificate_failure_on_cached_address_still_requires_rollback(self):
        state = self.state()
        def run(*args, **kwargs):
            if args[0] != 'curl':
                return 'active'
            return '000 203.0.113.20 60' if '--resolve' in args else '000  6'
        with self.healthy_local(), patch.object(component, 'run', side_effect=run):
            self.assertFalse(component.connectivity_step(state, 110))
            self.assertTrue(component.connectivity_step(state, 231))
        self.assertEqual(state['connectivity_watch']['last_observation']['reason'], 'server-tls-verification-failed')

    def test_no_fallback_address_is_guessed_if_baseline_did_not_record_one(self):
        watch = {}
        with patch.object(component, 'server_probe', return_value={'ok': False, 'reason': 'dns-failed'}) as probe:
            self.assertFalse(component.server_reachable(watch))
        probe.assert_called_once_with()
        self.assertNotIn('verified_server_address', watch)

    def test_normal_success_refreshes_transaction_address(self):
        watch = {'verified_server_address': '203.0.113.20'}
        with patch.object(component, 'server_probe', return_value={
                'ok': True, 'reason': 'reachable', 'address': '203.0.113.21'}):
            self.assertTrue(component.server_reachable(watch))
        self.assertEqual(watch['verified_server_address'], '203.0.113.21')

    def test_invalid_cached_addresses_never_launch_curl(self):
        for value in ('127.0.0.1', '::1', '0.0.0.0', '224.0.0.1', '169.254.1.2', 'fe80::1%wlan0', 'evil:443'):
            with self.subTest(address=value), patch.object(component, 'run') as run:
                self.assertFalse(component.server_probe(value)['ok'])
                run.assert_not_called()

    def test_ipv6_cached_address_is_bracketed_and_tls_remains_enabled(self):
        with patch.object(component, 'run', return_value='200 2001:db8::20 0') as run:
            self.assertTrue(component.server_probe('2001:db8::20')['ok'])
        self.assertIn('www.teiots.net:443:[2001:db8::20]', run.call_args.args)

    def test_station_or_agent_failure_is_distinguished_from_server_dns(self):
        state = self.state()
        with self.healthy_local(), patch.object(component, 'station_identity', return_value={'uuid': '', 'address': ''}), \
             patch.object(component, 'server_probe') as server:
            self.assertFalse(component.connection_healthy(state))
            server.assert_not_called()
        self.assertEqual(state['connectivity_watch']['last_observation']['reason'], 'station-unavailable')
        with self.healthy_local(), patch.object(component, 'run', return_value='inactive'):
            self.assertFalse(component.connection_healthy(state))
        self.assertEqual(state['connectivity_watch']['last_observation']['reason'], 'agent-inactive')

    def test_portal_timeout_is_recorded_without_stm32_dependency(self):
        state = self.state()
        with self.healthy_local(), patch.object(component, 'api', side_effect=TimeoutError('private error text')):
            self.assertFalse(component.connection_healthy(state))
        self.assertEqual(state['connectivity_watch']['last_observation']['reason'], 'portal-probe-error')
        self.assertNotIn('private error text', json.dumps(state))

    @unittest.skipUnless(os.name == 'posix', 'Atomic Linux filesystem diagnostics')
    def test_diagnostics_survive_rollback_but_do_not_copy_secrets(self):
        state = self.state()
        state['station_before'] = {'uuid': 'private-baseline'}
        state['connectivity_watch'].update(last_observation={'ok': False, 'reason': 'server-dns-failed'},
            secret='must-not-export', history=[{'ok': False, 'reason': 'server-dns-failed'}])
        with tempfile.TemporaryDirectory(prefix='bmi30-guard-diagnosis-') as temp, \
             patch.object(component, 'SYSTEM_ROOT', pathlib.Path(temp)), patch.object(component, 'boot_id', return_value='boot-a'), \
             patch('builtins.print'):
            component.record_rollback_diagnostics(state, 'connection-loss')
            path = pathlib.Path(temp) / 'var/lib/bmi30-update-diagnostics/last-rollback.json'
            text = path.read_text()
        self.assertIn('server-dns-failed', text)
        self.assertNotIn('must-not-export', text)
        self.assertNotIn('private-baseline', text)

    def test_unwritable_diagnostic_file_does_not_prevent_recovery(self):
        with patch.object(component, 'write', side_effect=OSError('read only')), \
             patch.object(component, 'boot_id', return_value='boot-a'), patch('builtins.print'):
            component.record_rollback_diagnostics(self.state(), 'connection-loss')

    @unittest.skipUnless(os.name == 'posix', 'Atomic Linux operation journal')
    def test_previous_portal_can_read_the_diagnosis_after_completed_rollback(self):
        state = self.state()
        state['rollback_diagnostics'] = {'last_observation': {'reason': 'server-dns-failed'}}
        root = pathlib.Path(__file__).resolve().parents[4]
        source = (root / 'FirmwareReleases/RaspberryPi/2026-10-29-171220/review/bundle/project/hotspot_info_server.py').read_text(encoding='utf-8')
        function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_read_firmware_operation_state')
        with tempfile.TemporaryDirectory(prefix='bmi30-rollback-status-') as temp, \
             patch.object(component, 'DOCS', pathlib.Path(temp)), \
             patch.object(component, 'firmware_idle', side_effect=contextlib.nullcontext), \
             patch.object(component, 'active', return_value='previous-release'):
            path = pathlib.Path(temp) / '.bmi30_cloud_sync/portal_operation.json'
            original = {'action': 'rollback', 'status': 'succeeded', 'progress': 100, 'error': '',
                        'updated_at': 12345, 'message': 'Firmware operation completed.'}
            component.write(path, original)
            self.assertTrue(component.annotate_completed_rollback(state))
            namespace = {'FIRMWARE_OPERATION_FILE': path, 'json': json, 'time': component.time, 'Any': Any,
                         '_firmware_text': lambda value, limit: str(value or '')[:limit]}
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
            exposed = namespace['_read_firmware_operation_state']()
        self.assertIn('server-dns-failed', exposed['message'])
        self.assertIn('test-release', exposed['message'])
        self.assertEqual(exposed['status'], 'succeeded')
        self.assertEqual(exposed['error'], '')
        self.assertEqual(exposed['updated_at'], 12345)

    @unittest.skipUnless(os.name == 'posix', 'Atomic Linux operation journal')
    def test_new_update_operation_is_never_overwritten_by_rollback_diagnosis(self):
        state = self.state()
        with tempfile.TemporaryDirectory(prefix='bmi30-rollback-race-') as temp, \
             patch.object(component, 'DOCS', pathlib.Path(temp)), \
             patch.object(component, 'firmware_idle', side_effect=contextlib.nullcontext), \
             patch.object(component, 'active', return_value='another-release'):
            path = pathlib.Path(temp) / '.bmi30_cloud_sync/portal_operation.json'
            original = {'action': 'update', 'status': 'running', 'progress': 20, 'message': 'Downloading'}
            component.write(path, original)
            self.assertFalse(component.annotate_completed_rollback(state))
            self.assertEqual(json.loads(path.read_text()), original)

    def test_busy_firmware_operation_defers_annotation(self):
        with patch.object(component, 'firmware_idle', side_effect=BlockingIOError), patch.object(component, 'write') as write:
            self.assertFalse(component.annotate_completed_rollback(self.state()))
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
