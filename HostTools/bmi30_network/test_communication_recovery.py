import configparser
import json
import os
from pathlib import Path
import tempfile
import unittest
import ast
import struct
from unittest.mock import Mock, patch as mock_patch

import communication_recovery as recovery
import patch_communication_recovery as patch


class IdentityTests(unittest.TestCase):
    def test_portable_runtime_accepts_current_hardware_on_either_device(self):
        for identity in ('BMI30-D5EB8F26C', 'BMI30-6922A5A0D'):
            with self.subTest(identity=identity), mock_patch(
                    'network.hardware_identity', return_value=identity, create=True), mock_patch(
                    'socket.gethostname', return_value='BMI30-CLONED-MEDIA'):
                self.assertEqual(recovery.verify_local_identity(), identity)

    def test_explicit_expected_identity_uses_hardware_despite_cloned_hostname(self):
        identity = 'BMI30-6922A5A0D'
        with mock_patch('network.hardware_identity', return_value=identity, create=True), mock_patch(
                'socket.gethostname', return_value='BMI30-D5EB8F26C'):
            self.assertEqual(recovery.verify_local_identity(identity), identity)

    def test_cloned_hostname_cannot_bypass_wrong_hardware_guard(self):
        with mock_patch('network.hardware_identity', return_value='BMI30-6922A5A0D', create=True), mock_patch(
                'socket.gethostname', return_value='BMI30-D5EB8F26C'):
            with self.assertRaisesRegex(RuntimeError, 'Wrong device identity'):
                recovery.verify_local_identity('BMI30-D5EB8F26C')

    def test_missing_hardware_stops_prepare_before_any_profile_change(self):
        with mock_patch('sys.argv', ['communication_recovery.py', '--prepare']), mock_patch(
                'network.hardware_identity', side_effect=RuntimeError('Missing hardware identity'),
                create=True), mock_patch.object(recovery, 'prepare_profiles') as prepare:
            with self.assertRaisesRegex(RuntimeError, 'Missing hardware identity'):
                recovery.main()
            prepare.assert_not_called()

    def test_runtime_units_have_no_fixed_device_identity(self):
        for name, mode in (('bmi30-channel-policy.service', '--prepare'),
                           ('bmi30-communication-recovery.service', '--daemon')):
            test_unit_root = os.environ.get('BMI30_PORTABLE_TEST_UNIT_ROOT')
            if test_unit_root:
                unit_path = Path(test_unit_root) / name
            else:
                unit_path = Path(recovery.__file__).with_name(name)
                if not unit_path.is_file():
                    unit_path = Path('/etc/systemd/system') / name
            unit = unit_path.read_text()
            command = next(line for line in unit.splitlines() if line.startswith('ExecStart='))
            self.assertIn('communication_recovery.py ' + mode, command)
            self.assertNotIn('--expected-host', command)


class TransportTests(unittest.TestCase):
    def setUp(self):
        namespace = {'struct': struct}
        exec(patch.PARSER, namespace)
        self.parse = namespace['parse_communication_recovery']
        import time
        namespace['time'] = time
        exec('class Transport:\n' + patch.METHODS, namespace)
        self.transport = namespace['Transport']()
        self.packet = b'COM1' + bytes([1, 1, 1, 0]) + struct.pack('<HH', 42, 2000) + bytes(range(12))
        self.transport._ep0_ctrl_transfer = Mock(return_value=self.packet)

    def test_valid_packet_keeps_uid_and_generation(self):
        value = self.parse(self.packet)
        self.assertEqual(value['generation'], 42)
        self.assertEqual(value['stm32_uid96'], bytes(range(12)).hex().upper())
        self.assertTrue(value['pending'] and value['durable'])

    def test_malformed_packet_is_ignored(self):
        for packet in (self.packet[:-1], b'BAD1' + self.packet[4:],
                       self.packet[:5] + bytes([2]) + self.packet[6:],
                       self.packet[:8] + b'\x00\x00' + self.packet[10:]):
            self.assertIsNone(self.parse(packet))

    def test_old_firmware_is_optional_and_queries_are_throttled(self):
        self.transport._ep0_ctrl_transfer.side_effect = OSError('STALL')
        self.assertIsNone(self.transport.get_communication_recovery())
        self.assertIsNone(self.transport.get_communication_recovery())
        self.assertEqual(self.transport._ep0_ctrl_transfer.call_count, 1)

    def test_ack_requires_current_durable_matching_generation(self):
        self.assertFalse(self.transport.ack_communication_recovery(43))
        self.transport._ep0_ctrl_transfer.assert_called_once_with(0xC0, 0x4F, 0, 0, 24, timeout=120)
        self.transport._ep0_ctrl_transfer.reset_mock()
        self.assertTrue(self.transport.ack_communication_recovery(42))
        self.assertEqual(self.transport._ep0_ctrl_transfer.call_args.args, (0x40, 0x50, 42, 0, None))

    def test_invalid_generation_never_touches_usb(self):
        for value in (0, 65536, True, '42'):
            with self.assertRaises(ValueError):
                self.transport.ack_communication_recovery(value)
        self.transport._ep0_ctrl_transfer.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.profiles = self.root / 'profiles'
        self.profiles.mkdir()
        self.state = self.root / 'state.json'
        self.config.write_text(json.dumps({'channel_permissions': {'wifi': False},
                                          'detector': {'mode': 6}, 'portal_account': {'secret': 'unchanged'}}))

    def profile(self, name, kind='wifi', mode='infrastructure', autoconnect='true'):
        path = self.profiles / name
        path.write_text('[connection]\nid=' + name + '\nuuid=' + name + '\ntype=' + kind +
                        '\nautoconnect=' + autoconnect + '\n[wifi]\nmode=' + mode +
                        '\n[wifi-security]\npsk=retain_this_secret\n')
        return path

    def prepare(self, config=None):
        return recovery.prepare_profiles(config or self.config, self.profiles, self.state)

    def read_profile(self, path):
        result = configparser.ConfigParser()
        result.read(path)
        return result

    def test_disabled_wifi_blocks_every_saved_sta_profile_across_boots(self):
        a, b = self.profile('home'), self.profile('fleet', autoconnect='false')
        ap = self.profile('hotspot', mode='ap')
        self.assertEqual(self.prepare(), 1)
        self.assertEqual(self.prepare(), 0)
        for path in (a, b):
            p = self.read_profile(path)
            self.assertEqual(p['connection']['autoconnect'], 'false')
            self.assertEqual(p['wifi-security']['psk'], 'retain_this_secret')
        self.assertEqual(self.read_profile(ap)['connection']['autoconnect'], 'true')

    def test_reenable_restores_each_profiles_original_autoconnect(self):
        a, b = self.profile('home'), self.profile('fleet', autoconnect='false')
        self.prepare()
        config = recovery.read_config(self.config)
        config['channel_permissions']['wifi'] = True
        self.config.write_text(json.dumps(config))
        self.prepare()
        self.assertEqual(self.read_profile(a)['connection']['autoconnect'], 'true')
        self.assertEqual(self.read_profile(b)['connection']['autoconnect'], 'false')

    def test_invalid_config_does_not_enable_networks(self):
        path = self.profile('home')
        self.config.write_text('{broken')
        self.prepare()
        self.assertEqual(self.read_profile(path)['connection']['autoconnect'], 'false')

    def test_binary_nmmeta_is_untouched_and_does_not_block_prepare(self):
        path = self.profiles / '277c5066-88f0-4a91-aa97-50ca4ff30a5f.nmmeta'
        data = bytes(128) + b'\x90' + b'not-a-profile'
        path.write_bytes(data)
        path.chmod(0o755)
        self.assertEqual(self.prepare(), 0)
        self.assertEqual(path.read_bytes(), data)
        self.assertFalse(self.state.exists())

    def test_text_nmmeta_is_never_treated_as_a_connection_profile(self):
        path = self.profile('277c5066-88f0-4a91-aa97-50ca4ff30a5f.nmmeta')
        before = path.read_bytes()
        self.assertEqual(self.prepare(), 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.state.exists())

    def test_nm_ignored_temporary_backup_and_certificate_files_are_untouched(self):
        names = ('.hidden', 'router~', 'router.ABC123', 'router.nmmeta~',
                 'certificate.pem', 'certificate.DER', 'router.bmi30bak.20261008')
        for name in names:
            (self.profiles / name).write_bytes(b'\x90ignored-fixture')
        before = {name: (self.profiles / name).read_bytes() for name in names}
        self.assertEqual(self.prepare(), 0)
        self.assertEqual({name: (self.profiles / name).read_bytes() for name in names}, before)

    def test_disabled_sta_still_blocks_extensionless_and_nmconnection_profiles(self):
        paths = (self.profile('home'), self.profile('home.nmconnection'),
                 self.profile('legacy.profile.bak'))
        (self.profiles / 'fixture.nmmeta').write_bytes(b'\x90metadata')
        before_config = self.config.read_bytes()
        self.assertEqual(self.prepare(), 3)
        for path in paths:
            profile = self.read_profile(path)
            self.assertEqual(profile['connection']['autoconnect'], 'false')
            self.assertEqual(profile['wifi-security']['psk'], 'retain_this_secret')
        self.assertEqual(self.config.read_bytes(), before_config)

    def test_all_disabled_channels_remain_blocked_with_metadata_present(self):
        self.config.write_text(json.dumps({'channel_permissions': dict.fromkeys(recovery.CHANNELS, False)}))
        paths = (self.profile('sta.nmconnection'), self.profile('ap', mode='ap'),
                 self.profile('wired', kind='ethernet'))
        metadata = self.profiles / 'fixture.nmmeta'
        metadata.write_bytes(b'\x90metadata')
        self.assertEqual(self.prepare(), 3)
        self.assertEqual(self.prepare(), 0)
        for path in paths:
            self.assertEqual(self.read_profile(path)['connection']['autoconnect'], 'false')
        self.assertEqual(metadata.read_bytes(), b'\x90metadata')

    def test_invalid_utf8_actual_nmconnection_still_fails_closed(self):
        (self.profiles / 'invalid.nmconnection').write_bytes(bytes(128) + b'\x90')
        with self.assertRaises(UnicodeDecodeError):
            self.prepare()
        self.assertFalse(self.state.exists())

    def test_invalid_ini_actual_nmconnection_still_fails_closed(self):
        (self.profiles / 'invalid.nmconnection').write_text('not an INI connection profile')
        with self.assertRaises(configparser.Error):
            self.prepare()
        self.assertFalse(self.state.exists())

    def test_invalid_permissions_still_block_every_network_with_metadata_present(self):
        self.config.write_text('{broken')
        paths = (self.profile('sta'), self.profile('ap', mode='ap'),
                 self.profile('wired', kind='ethernet'))
        (self.profiles / 'fixture.nmmeta').write_bytes(b'\x90metadata')
        self.prepare()
        for path in paths:
            self.assertEqual(self.read_profile(path)['connection']['autoconnect'], 'false')

    def request(self):
        return {'version': 1, 'pending': True, 'durable': True, 'generation': 7,
                'stm32_uid96': '0123456789abcdef01234567'}

    def test_ack_follows_durable_apply_and_preserves_unrelated_settings(self):
        events = []
        def apply():
            c = recovery.read_config(self.config)
            self.assertTrue(all(recovery.permissions(c).values()))
            self.assertIn('in_progress', c['communication_recovery'])
            self.assertNotIn('last_applied', c['communication_recovery'])
            events.append('apply')
        def ack(generation):
            self.assertEqual(generation, 7)
            self.assertIn('last_applied', recovery.read_config(self.config)['communication_recovery'])
            events.append('ack')
        recovery.recover(self.request(), self.config, apply=apply, prepare=self.prepare, acknowledge=ack)
        c = recovery.read_config(self.config)
        self.assertEqual(c['detector'], {'mode': 6})
        self.assertEqual(c['portal_account']['secret'], 'unchanged')
        self.assertEqual(events, ['apply', 'ack'])

    def test_replayed_request_only_retries_ack_after_user_disables_wifi(self):
        events = []
        recovery.recover(self.request(), self.config, apply=lambda: events.append('apply'),
                         prepare=self.prepare, acknowledge=lambda n: events.append('ack'))
        c = recovery.read_config(self.config)
        c['channel_permissions']['wifi'] = False
        self.config.write_text(json.dumps(c))
        recovery.recover(self.request(), self.config, apply=lambda: events.append('apply'),
                         prepare=self.prepare, acknowledge=lambda n: events.append('ack'))
        self.assertFalse(recovery.permissions(recovery.read_config(self.config))['wifi'])
        self.assertEqual(events, ['apply', 'ack', 'ack'])

    def test_apply_failure_keeps_request_unacknowledged_for_retry(self):
        events = []
        def fail():
            raise RuntimeError('service failed')
        with self.assertRaises(RuntimeError):
            recovery.recover(self.request(), self.config, apply=fail, prepare=self.prepare,
                             acknowledge=lambda n: events.append('ack'))
        self.assertEqual(events, [])
        self.assertIn('in_progress', recovery.read_config(self.config)['communication_recovery'])
        recovery.recover(self.request(), self.config, apply=lambda: events.append('apply'),
                         prepare=self.prepare, acknowledge=lambda n: events.append('ack'))
        self.assertEqual(events, ['apply', 'ack'])

    def test_nondurable_or_malformed_requests_cannot_enable_channels(self):
        for change in ({'durable': False}, {'generation': 0}, {'generation': True},
                       {'stm32_uid96': 'neighbor'}, {'pending': False}, {'version': 2}):
            request = {**self.request(), **change}
            self.assertFalse(recovery.recover(request, self.config))
            self.assertFalse(recovery.permissions(recovery.read_config(self.config))['wifi'])


if __name__ == '__main__':
    unittest.main()
