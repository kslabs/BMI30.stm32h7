"""Outbound recovery against the real, unmodified 0.1.13 HTTP server."""
import base64
import json
from email.utils import formatdate
from pathlib import Path
import time
import unittest
import urllib.request
from unittest.mock import patch
from unittest.mock import Mock

from test_recovery import module, agent, server, directory, Clock, KEY, DONOR, TARGET_ID, secret
from active_recovery import ActiveRecovery, RecoveryDeferred
from legacy_recovery import LegacyRecovery


class RewriteOpener:
    def __init__(self, port):
        self.port = port
        self.requests = []
        self.real = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def open(self, request, timeout):
        if isinstance(request, str):
            url, data, headers = request, None, {}
        else:
            url, data, headers = request.full_url, request.data, dict(request.headers)
        self.requests.append(data)
        url = url.replace('http://192.168.4.1:8791', 'http://127.0.0.1:' + str(self.port))
        return self.real.open(urllib.request.Request(url, data, headers), timeout=timeout)


class ActiveTests(unittest.TestCase):
    def test_maintenance_waits_do_not_exhaust_recovery_budget(self):
        with directory() as root:
            _, _, donor, active = self.pair(root)
            active.started = 0
            active.state['enabled'] = True
            donor.network = Mock()
            donor.network.remote_desktop_active.return_value = False
            donor.network.permissions.return_value = {'wifi': True}
            active.known_target = Mock(return_value='existing-known-target-psk')
            active.visit = Mock(side_effect=RecoveryDeferred())
            local = {'clients': 0, 'internet': {'ok': True}, 'visible': [{'ssid': TARGET_ID, 'freq': 2432}]}
            for now in (1000, 1600, 2200, 2800):
                with patch('active_recovery.time.monotonic', return_value=now):
                    self.assertFalse(active.maybe_visit(local))
                self.assertEqual(active.snapshot()['attempts'], 0)
                self.assertEqual(active.snapshot()['stage'], 'waiting-for-maintenance')
            self.assertEqual(active.visit.call_count, 4)
            with patch('active_recovery.time.monotonic', return_value=2829):
                self.assertFalse(active.maybe_visit(local))
            self.assertEqual(active.visit.call_count, 4, 'Maintenance polling must be bounded')
            active.visit.side_effect = None
            with patch('active_recovery.time.monotonic', return_value=2830):
                self.assertTrue(active.maybe_visit(local))
            self.assertEqual(active.snapshot()['attempts'], 1)
            self.assertIsNone(active.snapshot()['error_type'])

    def test_real_firmware_lock_refuses_radio_changes_before_checkpoint(self):
        import fcntl
        import sys
        with directory() as root:
            _, _, donor, active = self.pair(root)
            donor.network = Mock()
            donor.network.snapshot.return_value = {'clients': 0}
            lock_path = root / 'firmware.lock'
            lock_path.touch()
            dbus = Mock()
            with lock_path.open('rb') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.dict(sys.modules, {'dbus': dbus, 'network': Mock()}), patch('active_recovery.Path', return_value=lock_path):
                    with self.assertRaises(RecoveryDeferred):
                        active.visit(None, 'existing-known-target-psk')
            dbus.SystemBus.assert_not_called()
            self.assertFalse(donor.switching)

    def test_actual_failures_do_not_stop_future_search_after_three_attempts(self):
        with directory() as root:
            _, _, donor, active = self.pair(root)
            active.started = 0
            active.state['enabled'] = True
            donor.network = Mock()
            donor.network.remote_desktop_active.return_value = False
            donor.network.permissions.return_value = {'wifi': True}
            active.known_target = Mock(return_value='existing-known-target-psk')
            active.visit = Mock(side_effect=RuntimeError('test failure'))
            local = {'clients': 0, 'internet': {'ok': True}, 'visible': [{'ssid': TARGET_ID, 'freq': 2432}]}
            for now in (1000, 1600, 2200, 2800, 3400, 4000):
                with patch('active_recovery.time.monotonic', return_value=now):
                    active.maybe_visit(local)
            self.assertEqual(active.visit.call_count, 6)
            self.assertEqual(active.snapshot()['attempts'], 6)
            self.assertIsNone(active.snapshot()['attempt_limit'])
            with patch('active_recovery.time.monotonic', return_value=4001):
                self.assertFalse(active.maybe_visit(local))
            self.assertEqual(active.visit.call_count, 6, 'Unlimited search must still respect the retry interval')

    def test_absent_target_is_monitored_without_blind_disconnections(self):
        with directory() as root:
            _, _, donor, active = self.pair(root)
            active.started = 0
            active.state['enabled'] = True
            donor.network = Mock()
            donor.network.remote_desktop_active.return_value = False
            donor.network.permissions.return_value = {'wifi': True}
            active.known_target = Mock(return_value='existing-known-target-psk')
            active.visit = Mock()
            local = {'clients': 0, 'internet': {'ok': True}, 'visible': []}
            for now in (1000, 1065, 1600, 2200, 2800, 3400):
                with patch('active_recovery.time.monotonic', return_value=now):
                    self.assertFalse(active.maybe_visit(local))
            active.visit.assert_not_called()
            self.assertEqual(active.snapshot()['attempts'], 0)
            self.assertEqual(active.snapshot()['stage'], 'target-not-visible')
            local['visible'] = [{'ssid': TARGET_ID, 'freq': 2432}]
            with patch('active_recovery.time.monotonic', return_value=3401):
                self.assertTrue(active.maybe_visit(local))
            active.visit.assert_called_once()

    def test_cleanup_command_failure_cannot_leave_radio_switching_or_busy(self):
        with directory() as root:
            _, _, donor, active = self.pair(root)
            donor.switching = True
            busy = root / 'busy'
            busy.write_text('operation')
            network = Mock()
            network.run.side_effect = RuntimeError('cleanup unavailable')
            active.release_radio(network, busy, 'operation', 'unit', 'temporary', True)
            self.assertFalse(donor.switching)
            self.assertFalse(busy.exists())
            self.assertTrue(active.snapshot()['restored'])
            self.assertTrue(active.snapshot()['cleanup_error'])

    def test_failed_restoration_keeps_the_independent_guard_armed(self):
        with directory() as root:
            _, _, donor, active = self.pair(root)
            donor.switching = True
            busy = root / 'busy'
            busy.write_text('operation')
            network = Mock()
            active.release_radio(network, busy, 'operation', 'unit', 'temporary', False)
            self.assertFalse(donor.switching)
            self.assertTrue(busy.exists())
            self.assertFalse(active.snapshot()['restored'])
            network.run.assert_not_called()

    def pair(self, root, target_at=0, donor_at=2000000000):
        old_clock, own_clock = Clock(target_at), Clock(donor_at)
        old_scope, own_scope = module(old_clock), module(own_clock)
        target = agent(old_scope, TARGET_ID, root, old_clock)
        target.state.update(local=target.local, checked_at=target_at)
        donor = agent(own_scope, DONOR, root, own_clock, secret(donor_at))
        donor.legacy_recovery = LegacyRecovery(donor)
        return old_scope, target, donor, ActiveRecovery(donor.legacy_recovery)

    def run_provision(self, scope, target, donor, active, date):
        with patch.object(scope['BaseHTTPRequestHandler'], 'date_time_string', return_value=formatdate(date, usegmt=True)):
            with server(scope, target) as http:
                donor.opener = RewriteOpener(http.server_port)
                active.provision('192.168.4.1')

    def test_old_target_persists_settings_without_initiating_any_request(self):
        for target_at, donor_at in ((0, 2000000000), (2000000000, 0)):
            with self.subTest(target_at=target_at), directory() as root:
                scope, target, donor, active = self.pair(root, target_at, donor_at)
                before = dict(donor.secrets)
                self.run_provision(scope, target, donor, active, target_at)
                self.assertEqual(active.snapshot()['confirmed_wifi'], 1)
                self.assertEqual(active.snapshot()['stage'], 'settings-confirmed')
                saved = json.loads(target.secrets_path.read_text())
                self.assertEqual(saved['wifi:test-router']['psk'], before['wifi:test-router']['psk'])
                self.assertLess(saved['wifi:test-router']['updated'], target_at + 1)
                self.assertEqual(donor.secrets, before)
                self.assertEqual(donor.legacy_recovery.snapshot()['requests'], 0)
                self.assertEqual(set(donor.records.records), {DONOR})
                self.assertNotIn('test-password', json.dumps(active.snapshot()))
                packets = [r for r in donor.opener.requests if r]
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                payloads = []
                for packet in packets:
                    binary = base64.b64decode(packet)
                    payloads.append(json.loads(AESGCM(bytes.fromhex(KEY)).decrypt(
                        binary[:12], binary[12:], b'bmi30-network-v1'))['payload'])
                self.assertEqual(payloads[0]['credentials'], {})
                self.assertEqual(len(payloads[1]['credentials']), 1)

    def test_untrusted_clock_hint_never_causes_credentials_to_be_sent(self):
        with directory() as root:
            scope, target, donor, active = self.pair(root)
            with self.assertRaises(Exception):
                self.run_provision(scope, target, donor, active, 2000000000)
            self.assertEqual(target.secrets, {})
            self.assertEqual(sum(bool(r) for r in donor.opener.requests), 1)

    def test_different_fleet_key_fails_before_credentials(self):
        with directory() as root:
            scope, target, donor, active = self.pair(root)
            target.protocol.key = bytes.fromhex('19' * 32)
            with self.assertRaises(Exception):
                self.run_provision(scope, target, donor, active, 0)
            self.assertEqual(target.secrets, {})
            self.assertEqual(sum(bool(r) for r in donor.opener.requests), 1)

    def test_newer_receiver_password_is_not_overwritten_with_an_older_timestamp(self):
        with directory() as root:
            scope, target, donor, active = self.pair(root)
            target.secrets = secret(.005, password='old-password')
            self.run_provision(scope, target, donor, active, 0)
            self.assertEqual(active.snapshot()['confirmed_wifi'], 1)
            self.assertGreater(target.secrets['wifi:test-router']['updated'], .005)

    def test_conflicting_future_timestamp_is_reported_without_fake_confirmation(self):
        with directory() as root:
            scope, target, donor, active = self.pair(root)
            target.secrets = secret(1000, password='old-password')
            self.run_provision(scope, target, donor, active, 0)
            self.assertTrue(active.snapshot()['future_timestamp_conflict'])
            self.assertEqual(active.snapshot()['confirmed_wifi'], 0)
            self.assertEqual(target.secrets['wifi:test-router']['psk'], 'old-password')

    def test_other_device_is_refused_before_any_encrypted_exchange(self):
        with directory() as root:
            scope, target, donor, active = self.pair(root)
            target.state['local'] = {**target.local, 'id': 'BMI30-OTHER'}
            with self.assertRaises(ValueError):
                self.run_provision(scope, target, donor, active, 0)
            self.assertFalse(any(donor.opener.requests))

    def test_target_psk_must_already_be_known(self):
        with directory() as root:
            _, _, donor, active = self.pair(root)
            self.assertIsNone(active.known_target())
            donor.config['peers'] = [{'id': TARGET_ID, 'psk': 'existing-known-psk'}]
            self.assertEqual(active.known_target(), 'existing-known-psk')


if __name__ == '__main__':
    unittest.main()
