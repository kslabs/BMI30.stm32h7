"""Hardware events, bounded subscribers, leases and release without real devices."""
import queue
import errno
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmi30_bluetooth_remote_test import (BluetoothRemoteTest, DEFAULT_HOSTNAME, InputBusy,
                                         InputDisconnected, LinuxBluetoothInputs, RemoteInputDecoder)

MAC = 'FF:24:01:26:A0:4A'


class FakeInputs:
    def __init__(self, error=None):
        self.error = error
        self.closed = threading.Event()
        self.incoming = queue.Queue()

    def open(self):
        if self.error:
            raise self.error

    def poll(self, timeout):
        try:
            result = self.incoming.get(timeout=min(timeout, .01))
        except queue.Empty:
            return []
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed.set()


class DecoderTests(unittest.TestCase):
    def test_five_standard_keys_require_complete_press_release_and_ignore_repeat(self):
        decoder = RemoteInputDecoder()
        for code, button in ((28, 'center'), (103, 'up'), (108, 'down'), (105, 'left'), (106, 'right')):
            self.assertEqual(decoder.feed(1, code, 1, 10), [])
            self.assertEqual(decoder.feed(1, code, 2, 10.2), [])
            rows = decoder.feed(1, code, 0, 11)
            self.assertEqual((rows[0]['button'], rows[0]['complete'], rows[0]['start_monotonic']),
                             (button, True, 10))
            self.assertFalse(decoder.feed(1, code, 0, 11.2)[0]['complete'])

    def test_consumer_volume_key_is_receipt_diagnostic_without_guessed_direction(self):
        decoder = RemoteInputDecoder()
        rows = decoder.feed(1, 115, 1, 10) + decoder.feed(1, 115, 0, 11)
        self.assertEqual([row['value'] for row in rows], [1, 0])
        self.assertTrue(all(row['button'] is None and not row['complete'] for row in rows))

    def test_other_hid_mode_activity_is_visible_without_inventing_a_button_release(self):
        decoder = RemoteInputDecoder()
        row = decoder.feed(2, 0, 15, 10)[0]  # EV_REL X, not a complete button press.
        self.assertEqual((row['type'], row['code'], row['value']), (2, 0, 15))
        self.assertIsNone(row['button'])
        self.assertFalse(row['complete'])

    def test_touch_directions_emit_only_on_completed_contact(self):
        for dx, dy, expected in ((0, 0, 'center'), (0, -2000, 'up'), (0, 2000, 'down'),
                                 (-2000, 0, 'left'), (2000, 0, 'right')):
            decoder = RemoteInputDecoder(touch=True, x=3000, y=3000)
            self.assertEqual(decoder.feed(3, 57, 1, 10), [])
            self.assertEqual(decoder.feed(0, 0, 0, 10), [])
            decoder.feed(3, 53, 3000+dx, 10.5)
            decoder.feed(3, 54, 3000+dy, 10.5)
            self.assertEqual(decoder.feed(0, 0, 0, 10.5), [])
            decoder.feed(3, 57, -1, 11)
            rows = decoder.feed(0, 0, 0, 11)
            self.assertEqual((rows[0]['button'], rows[0]['complete']), (expected, True))
        with self.assertRaisesRegex(RuntimeError, 'SYN_DROPPED'):
            decoder.feed(0, 3, 0, 12)

    def test_touch_contact_keys_do_not_create_spurious_unassigned_buttons(self):
        decoder = RemoteInputDecoder(touch=True, x=3000, y=3000)
        rows = []
        # Real T33 touch reports include EV_KEY contact flags as well as MT axes.
        for kind, code, value, mono in (
                (1, 325, 1, 10), (1, 330, 1, 10), (3, 57, 1, 10), (0, 0, 0, 10),
                (3, 53, 5000, 10.5), (0, 0, 0, 10.5), (3, 57, -1, 11),
                (1, 330, 0, 11), (1, 325, 0, 11), (0, 0, 0, 11)):
            rows.extend(decoder.feed(kind, code, value, mono))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['button'], rows[0]['complete'], rows[0]['source']), ('right', True, 'touch'))
        # A distinct Consumer Control interface still reports actual unknown keys.
        consumer = RemoteInputDecoder()
        self.assertEqual(consumer.feed(1, 330, 1, 12)[0]['phase'], 'diagnostic')


class LinuxOwnershipTests(unittest.TestCase):
    def test_exact_mac_and_bluetooth_bus_only_busy_capture_releases_all_claims(self):
        paths = [Path('/sys/class/input/event' + str(number)) for number in range(4)]

        def read_text(path, *args, **kwargs):
            text = str(path).replace('\\', '/')
            if text.endswith('/uniq'):
                return '00:11:22:33:44:55' if 'event0/' in text else MAC
            if text.endswith('/id/bustype'):
                return '0003' if 'event1/' in text else '0005'
            if text.endswith('/capabilities/abs'):
                return '0'
            raise AssertionError(text)

        def ioctl(fd, request, *args):
            if fd == 11 and request == 0x40044590:
                raise OSError(errno.EBUSY, 'capture owns input')
            return 0

        inputs = LinuxBluetoothInputs(MAC)
        with patch('bmi30_bluetooth_remote_test.socket.gethostname', return_value=DEFAULT_HOSTNAME), \
             patch.object(inputs, 'check_connection'), \
             patch.object(Path, 'glob', return_value=paths), \
             patch.object(Path, 'read_text', read_text), \
             patch.dict('sys.modules', {'fcntl': SimpleNamespace(ioctl=ioctl)}), \
             patch('bmi30_bluetooth_remote_test.time.CLOCK_MONOTONIC', 1, create=True), \
             patch('bmi30_bluetooth_remote_test.os.O_NONBLOCK', 0x800, create=True), \
             patch('bmi30_bluetooth_remote_test.os.open', side_effect=[10, 11]) as opened, \
             patch('bmi30_bluetooth_remote_test.os.read', side_effect=BlockingIOError), \
             patch('bmi30_bluetooth_remote_test.os.close') as closed:
            with self.assertRaisesRegex(InputBusy, 'active capture'):
                inputs.open()
        self.assertEqual([call.args[0] for call in opened.call_args_list],
                         ['/dev/input/event2', '/dev/input/event3'])
        self.assertEqual([call.args[0] for call in closed.call_args_list], [10, 11])
        self.assertEqual(inputs.inputs, {})

    def test_unpaired_device_is_rejected_before_input_access(self):
        inputs = LinuxBluetoothInputs(MAC)
        reply = SimpleNamespace(returncode=0, stdout='Paired: no\nConnected: yes\n')
        with patch('bmi30_bluetooth_remote_test.socket.gethostname', return_value=DEFAULT_HOSTNAME), \
             patch('bmi30_bluetooth_remote_test.subprocess.run', return_value=reply), \
             patch('bmi30_bluetooth_remote_test.os.open') as opened, \
             patch.dict('sys.modules', {'fcntl': SimpleNamespace()}):
            with self.assertRaisesRegex(InputDisconnected, 'not paired'):
                inputs.open()
            opened.assert_not_called()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.providers = []

        def factory(mac):
            provider = FakeInputs()
            self.providers.append(provider)
            return provider

        self.manager = BluetoothRemoteTest(provider_factory=factory,
            monotonic=lambda: self.now, wall_clock=lambda: 2000+self.now-1000, history_size=2)
        self.addCleanup(self.manager.close)

    def wait_for(self, test_id, predicate):
        deadline = time.monotonic()+1
        while time.monotonic() < deadline:
            value = self.manager.snapshot(test_id, refresh=False)
            if predicate(value):
                return value
            threading.Event().wait(.005)
        self.fail('Reader did not reach expected state: ' + repr(value))

    def start(self):
        test_id = self.manager.start(MAC)['test_id']
        self.wait_for(test_id, lambda value: value['ready'])
        return test_id

    def hardware_event(self, button='center'):
        return {'button': button, 'complete': True, 'phase': 'complete', 'pressed': False,
                'source': 'touch', 'device': '/dev/input/event6',
                'start_monotonic': self.now-.1, 'end_monotonic': self.now}

    def test_start_and_poll_never_create_hardware_events(self):
        test_id = self.start()
        result = self.manager.snapshot(test_id)
        self.assertEqual(result['events'], [])
        self.assertEqual(result['cursor'], 0)
        self.assertEqual(sum(result['button_counts'].values()), 0)
        self.assertFalse(result['physical_calibration_verified'])
        self.assertEqual(result['server_at'], 2000)

    def test_each_subscriber_cursor_is_independent_and_overflow_is_explicit(self):
        test_id = self.start()
        self.providers[0].incoming.put([self.hardware_event('center'), self.hardware_event('up')])
        self.wait_for(test_id, lambda value: value['cursor'] == 2)
        self.assertEqual([r['seq'] for r in self.manager.snapshot(test_id, 0)['events']], [1, 2])
        self.assertEqual([r['seq'] for r in self.manager.snapshot(test_id, 1)['events']], [2])
        self.assertEqual([r['seq'] for r in self.manager.snapshot(test_id, 0)['events']], [1, 2])
        self.providers[0].incoming.put([self.hardware_event('left')])
        result = self.wait_for(test_id, lambda value: value['cursor'] == 3)
        self.assertEqual(result['lost_events'], 1)
        self.assertEqual(result['history_start_seq'], 2)
        self.assertEqual(result['button_counts'], {'center': 1, 'up': 1, 'down': 0, 'left': 1, 'right': 0})
        self.assertEqual(self.manager.snapshot(test_id, 3)['events'], [])
        with self.assertRaises(ValueError):
            self.manager.snapshot(test_id, 4)

    def test_poll_refreshes_lease_but_expired_lease_never_revives(self):
        test_id = self.start()
        self.now += 29
        self.assertEqual(self.manager.snapshot(test_id)['lease_remaining_s'], 30)
        self.now += 29
        self.assertTrue(self.manager.snapshot(test_id, refresh=False)['ready'])
        self.now += 2
        result = self.manager.snapshot(test_id)
        self.assertEqual(result['status'], 'expired')
        self.assertFalse(result['ready'])
        self.assertTrue(self.providers[0].closed.wait(1))
        self.assertEqual(self.manager.snapshot(test_id)['status'], 'expired')

    def test_expiry_closes_reader_without_any_further_http_poll(self):
        test_id = self.start()
        self.now += 31
        self.assertTrue(self.providers[0].closed.wait(1))
        self.assertEqual(self.manager.snapshot(test_id, refresh=False)['status'], 'expired')

    def test_restart_and_page_leave_release_reader_and_old_stop_cannot_stop_new_test(self):
        old = self.start()
        new = self.start()
        self.assertTrue(self.providers[0].closed.is_set())
        self.manager.stop(old)
        self.assertTrue(self.manager.snapshot(new, refresh=False)['ready'])
        self.assertFalse(self.providers[1].closed.is_set())
        self.manager.stop(new)
        self.assertTrue(self.providers[1].closed.is_set())
        self.assertEqual(self.manager.stop(new)['status'], 'stopped')

    def test_capture_busy_and_disconnect_have_clear_errors_and_no_fake_actions(self):
        busy = FakeInputs(InputBusy('active capture owns input'))
        manager = BluetoothRemoteTest(provider_factory=lambda mac: busy)
        self.addCleanup(manager.close)
        test_id = manager.start(MAC)['test_id']
        self.assertTrue(busy.closed.wait(1))
        result = manager.snapshot(test_id)
        self.assertEqual(result['status'], 'busy')
        self.assertFalse(result['ok'])
        self.assertIn('active capture', result['reason'])
        self.assertEqual(result['events'], [])
        test_id = self.start()
        self.providers[0].incoming.put(InputDisconnected('ring lost connection'))
        result = self.wait_for(test_id, lambda value: value['status'] == 'disconnected')
        self.assertFalse(result['ready'])
        self.assertEqual(result['events'], [])
        self.assertTrue(self.providers[0].closed.wait(1))

    def test_invalid_mac_and_cursor_fail_before_other_device_access(self):
        with self.assertRaises(ValueError):
            self.manager.start('*')
        self.assertEqual(self.providers, [])
        test_id = self.start()
        for cursor in (-1, True, '1'):
            with self.assertRaises(ValueError):
                self.manager.snapshot(test_id, cursor)


if __name__ == '__main__':
    unittest.main()
