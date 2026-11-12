"""Recovery discovery must not depend solely on the AP-on scan cache."""
import threading
import types
import json
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from active_recovery import ActiveRecovery, ACTIVE_DONOR, ACTIVE_TARGET, TARGET_SSIDS


class TargetScanTests(unittest.TestCase):
    def active(self, known=True):
        adapter = Mock()
        adapter.sta = 'wlan0'
        adapter.remote_desktop_active.return_value = False
        adapter.permissions.return_value = {'wifi': True}
        agent = types.SimpleNamespace(
            config={'id': ACTIVE_DONOR, 'peers':
                    [{'id': ACTIVE_TARGET, 'psk': 'existing-test-key'}] if known else []},
            secrets={}, lock=threading.RLock(), switching=False, network=adapter)
        active = ActiveRecovery(types.SimpleNamespace(agent=agent))
        active.started = 0
        return active, adapter

    def test_empty_cache_allows_one_guarded_scan_and_obeys_retry_interval(self):
        active, _ = self.active()
        local = {'visible': [], 'clients': 0, 'internet': {'ok': True}}
        with patch('active_recovery.time.monotonic', return_value=1000), patch.object(active, 'visit') as visit:
            self.assertTrue(active.maybe_visit(local))
            self.assertFalse(active.maybe_visit(local))
        visit.assert_called_once_with(None, 'existing-test-key')
        self.assertEqual(active.snapshot()['attempts'], 1)

    def test_unknown_key_keeps_the_donor_online_without_scanning(self):
        active, _ = self.active(known=False)
        with patch('active_recovery.time.monotonic', return_value=1000), patch.object(active, 'visit') as visit:
            self.assertFalse(active.maybe_visit({'visible': [], 'internet': {'ok': True}}))
        visit.assert_not_called()
        self.assertEqual(active.snapshot()['stage'], 'target-ap-credential-unavailable')

    def test_hotspot_clients_prevent_the_ap_down_scan(self):
        active, _ = self.active()
        with patch('active_recovery.time.monotonic', return_value=1000), patch.object(active, 'visit') as visit:
            self.assertFalse(active.maybe_visit({'visible': [], 'clients': 1, 'internet': {'ok': True}}))
        visit.assert_not_called()

    def test_scan_waits_for_asynchronous_cache_refresh(self):
        active, adapter = self.active()
        target = {'ssid': TARGET_SSIDS[1], 'freq': 2412, 'signal': 55}
        adapter.visible.side_effect = [[], [], [target]]
        with patch('active_recovery.time.sleep') as sleep, patch('network.run') as run:
            self.assertEqual(active.refresh_target_scan(adapter), target)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(active.snapshot()['scan_target_visible'])
        self.assertEqual(adapter.visible.call_args_list[0].kwargs, {'rescan': False})
        self.assertEqual(run.call_args.args[-4:], ('ssid', TARGET_SSIDS[0], 'ssid', TARGET_SSIDS[1]))

    def test_absent_target_has_a_bounded_scan_and_explicit_result(self):
        active, adapter = self.active()
        adapter.visible.return_value = [{'ssid': 'BMI30-OTHER', 'freq': 2412, 'signal': 99}]
        with patch('active_recovery.time.sleep'), patch('network.run'):
            self.assertIsNone(active.refresh_target_scan(adapter))
        self.assertEqual(adapter.visible.call_count, 7)
        self.assertEqual(active.snapshot()['stage'], 'target-not-visible-after-ap-down-scan')

    def test_only_known_aliases_qualify_and_24ghz_is_preferred(self):
        target = {'ssid': TARGET_SSIDS[0], 'freq': 2412, 'signal': 30}
        visible = [{'ssid': TARGET_SSIDS[1], 'freq': 5180, 'signal': 99}, target,
                   {'ssid': TARGET_SSIDS[0] + '-OTHER', 'freq': 2412, 'signal': 100}]
        self.assertEqual(ActiveRecovery.select_target(visible), target)

    def test_name_only_probe_is_hidden_capable_without_guessing_a_band_or_bssid(self):
        settings = ActiveRecovery.wireless_settings({'ssid': TARGET_SSIDS[0]})
        self.assertTrue(settings['hidden'])
        self.assertNotIn('band', settings)
        self.assertNotIn('bssid', settings)
        self.assertEqual(settings['ssid'], TARGET_SSIDS[0].encode())

    def test_visible_target_remains_bound_to_the_selected_bssid_and_band(self):
        settings = ActiveRecovery.wireless_settings({'ssid': TARGET_SSIDS[0], 'freq': 5180,
                                                    'bssid': '00:11:22:33:44:55'})
        self.assertEqual(settings['band'], 'a')
        self.assertEqual(settings['bssid'], bytes.fromhex('001122334455'))
        self.assertNotIn('hidden', settings)

    def run_visit(self, failure):
        active, adapter = self.active()
        adapter.ap = 'wlan0ap'
        active.state['attempts'] = 1
        old = {'clients': 0, 'station_uuid': 'original-station', 'ap_uuid': 'original-ap'}
        current = {'ssid': TARGET_SSIDS[0] if failure != 'wrong-ssid' else 'OTHER',
                   'bssid': '00:11:22:33:44:55', 'freq': 2412, 'gateway': '10.112.148.1'}
        adapter.snapshot.side_effect = [old, current]
        adapter.active.side_effect = lambda iface: old['station_uuid' if iface == 'wlan0' else 'ap_uuid']
        store = {'/opt/bmi30-network/service.py': b'test service'}
        class MemoryPath:
            def __init__(self, value): self.value = str(value)
            def __truediv__(self, value): return MemoryPath(self.value + '/' + str(value))
            def __str__(self): return self.value
            @property
            def name(self): return self.value.rsplit('/', 1)[-1]
            @property
            def parent(self): return MemoryPath(self.value.rsplit('/', 1)[0])
            def mkdir(self, **kwargs): pass
            def exists(self): return self.value in store
            def is_file(self): return self.exists()
            def read_bytes(self): return store[self.value]
            def read_text(self): return store[self.value].decode()
            def write_text(self, value): store[self.value] = value.encode()
            def unlink(self, missing_ok=False): store.pop(self.value, None)
            def open(self, mode):
                from unittest.mock import MagicMock
                handle = MagicMock()
                handle.__enter__.return_value.fileno.return_value = 99
                return handle
        manager, properties = Mock(), Mock()
        manager.GetDeviceByIpIface.return_value = '/device'
        properties.Get.return_value = 30 if failure == 'association' else 100
        fake_dbus = types.SimpleNamespace(SystemBus=Mock(return_value=Mock()),
            Interface=lambda obj, iface: properties if iface.endswith('Properties') else manager,
            Dictionary=lambda value, **kwargs: value, ByteArray=bytes, UInt32=int, ObjectPath=str)
        ticks = iter(range(0, 1000, 10))
        with patch.dict(sys.modules, {'dbus': fake_dbus}), patch('fcntl.flock'), \
             patch('active_recovery.Path', MemoryPath), patch('network.run') as run, \
             patch('network.Checkpoint') as checkpoint, patch('active_recovery.time.sleep'), \
             patch('active_recovery.time.monotonic', side_effect=lambda: next(ticks)), \
             patch.object(active, 'refresh_target_scan', return_value=None), \
             patch.object(active, 'provision') as provision:
            if failure == 'authentication': provision.side_effect = ValueError('test authentication failure')
            if failure:
                with self.assertRaises((ValueError, RuntimeError)): active.visit(None, 'existing-test-key')
            else: active.visit(None, 'existing-test-key')
            checkpoint.return_value.rollback.assert_called_once()
            adapter.wait_restored.assert_called_once_with(old)
            self.assertFalse(active.agent.switching)
            self.assertTrue(active.snapshot()['restored'])
            self.assertNotIn('/run/bmi30-access/busy', store)
            if failure in ('association', 'wrong-ssid'): provision.assert_not_called()
            else: provision.assert_called_once_with('10.112.148.1')
            settings = manager.AddAndActivateConnection2.call_args.args[0]
            self.assertTrue(settings['802-11-wireless']['hidden'])
            self.assertEqual(manager.AddAndActivateConnection2.call_args.args[3], {'persist': 'memory'})
            self.assertTrue(any(c.args[:3] == ('systemctl', 'stop', c.args[2]) for c in run.call_args_list if len(c.args) >= 3))
            self.assertFalse(any('bmi30-core' in str(c.args) for c in run.call_args_list))

    def test_hidden_probe_association_failure_restores_the_donor(self):
        self.run_visit('association')

    def test_hidden_probe_authentication_failure_restores_the_donor(self):
        self.run_visit('authentication')

    def test_hidden_probe_refuses_another_ssid_before_credentials_and_restores(self):
        self.run_visit('wrong-ssid')


if __name__ == '__main__':
    unittest.main()
