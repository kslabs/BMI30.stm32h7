import json
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest

from relay_control import RelayService, decode_status


class Clock:
    now = 100.0
    def __call__(self): return self.now


class Stream:
    def __init__(self, clock):
        self.clock = clock
        self._ep0_lock = threading.RLock()
        self._running = True
        self.disconnected = False
        self.dev = SimpleNamespace(serial_number='test-board')
        self.enabled = False
        self.mode = 0
        self.until = 0
        self.calls = []
    def _ep0_ctrl_transfer(self, direction, command, value, index, data, timeout):
        self.calls.append((direction, command, value, data))
        if self.mode == 1 and self.clock() >= self.until: self.mode = 0
        if command == 0x4C:
            raw = bytearray(20)
            raw[:5] = b'RLY1\x01'
            raw[5:9] = bytes((self.enabled, bool(self.mode), bool(self.mode), self.mode))
            struct.pack_into('<I', raw, 12, 0xffffffff if self.mode == 2 else max(0,int((self.until-self.clock())*1000)) if self.mode else 0)
            return raw
        if command == 0x4A:
            self.enabled = bool(value)
            if not self.enabled:self.mode = 0
        elif command == 0x4D:
            self.mode = 2 if self.enabled and value else 0
        elif command == 0x4B:
            duration = struct.unpack('<I',data)[0]
            if not duration:self.mode = 0
            elif self.enabled and self.mode != 2:
                self.mode = 1
                self.until = self.clock()+duration/1000


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.stream = Stream(self.clock)
        self.scope = SimpleNamespace(stream=self.stream, _recovery_outputs_inhibited=False)
        self.service = RelayService(self.scope, Path(self.tmp.name)/'relay.json', start=False, clock=self.clock)
        self.service.observe_detection(False)
        self.service.step()
    def command(self, **params):
        request = dict(params=params, deadline=self.clock()+1, done=threading.Event(), result=None)
        self.service._requests.put_nowait(request)
        self.service.step()
        self.assertTrue(request['done'].is_set())
        return request['result']
    def advance(self, seconds, active=None):
        self.clock.now += seconds
        if active is not None:self.service.observe_detection(active)
        self.service.step()
    def test_permission_does_not_activate(self):
        self.assertTrue(self.command(enabled=True)['ok'])
        self.assertTrue(self.stream.enabled)
        self.assertEqual(self.stream.mode,0)
        self.advance(3,False)
        self.assertEqual(self.stream.mode,0)
    def test_disabled_test_rejected(self):
        self.assertFalse(self.command(test_enabled=True)['ok'])
        self.assertEqual(self.stream.mode,0)
    def test_test_held_until_stop_beyond_minimum(self):
        self.command(enabled=True,duration_ms=100)
        self.assertTrue(self.command(test_enabled=True)['ok'])
        self.advance(12,True)
        self.assertEqual(self.stream.mode,2)
        self.assertTrue(self.command(test_enabled=False)['ok'])
        self.assertEqual(self.stream.mode,0)
        self.advance(.2,True)
        self.assertEqual(self.stream.mode,0)
    def test_disable_cancels_test(self):
        self.command(enabled=True)
        self.command(test_enabled=True)
        self.command(enabled=False)
        self.assertEqual(self.stream.mode,0)
        self.assertFalse(self.stream.enabled)
    def test_minimum_on_time(self):
        self.command(enabled=True,duration_ms=2000)
        self.advance(.1,True)
        self.assertEqual(self.stream.mode,1)
        self.advance(.2,False)
        self.advance(1.7,False)
        self.assertEqual(self.stream.mode,1)
        self.advance(.11,False)
        self.assertEqual(self.stream.mode,0)
    def test_detection_outlasts_minimum_independent_of_leds(self):
        self.scope._non_addressable_led_enabled = False
        self.command(enabled=True,duration_ms=100)
        self.advance(.1,True)
        for _ in range(12):self.advance(.2,True)
        self.assertEqual(self.stream.mode,1)
        self.advance(.05,False)
        self.assertEqual(self.stream.mode,0)
    def test_short_pulse_captured_between_worker_ticks(self):
        self.command(enabled=True)
        self.clock.now += .01
        self.service.observe_detection(True)
        self.clock.now += .01
        self.service.observe_detection(False)
        self.service.step()
        self.assertEqual(self.stream.mode,1)
    def test_reconnect_restores_permission_but_not_test(self):
        self.command(enabled=True,duration_ms=3500)
        self.command(test_enabled=True)
        self.scope.stream = Stream(self.clock)
        self.service.step()
        self.assertTrue(self.scope.stream.enabled)
        self.assertEqual(self.scope.stream.mode,0)
        self.assertEqual(self.service.snapshot()['duration_ms'],3500)
    def test_persistence_never_stores_test(self):
        self.command(enabled=True,duration_ms=3000)
        self.command(test_enabled=True)
        self.assertEqual(json.loads(self.service.config_path.read_text()),
                         {'test-board':{'enabled':True,'duration_ms':3000}})
    def test_invalid_duration_does_not_write(self):
        for invalid in (0,-1,True,1.5,0x80000000,'1000'):
            before=len(self.stream.calls)
            self.assertFalse(self.command(duration_ms=invalid)['ok'])
            self.assertEqual(len(self.stream.calls),before)
    def test_gpio_status_and_protocol_validation(self):
        self.command(enabled=True)
        self.command(test_enabled=True)
        status=self.service.snapshot()
        self.assertTrue(status['active'])
        self.assertEqual(status['pc1'],1)
        with self.assertRaises(ValueError):decode_status(b'TXP1'+bytes(16))
    def test_busy_usb_lock_skips_diagnostic_work(self):
        ready=threading.Event();release=threading.Event()
        def hold():
            with self.stream._ep0_lock:
                ready.set();release.wait()
        thread=threading.Thread(target=hold);thread.start();ready.wait()
        try:
            count=len(self.stream.calls)
            self.service.step()
            self.assertEqual(len(self.stream.calls),count)
        finally:release.set();thread.join()
    def test_save_does_not_hold_usb_lock(self):
        original=self.service._save
        def save():
            self.assertFalse(self.stream._ep0_lock._is_owned())
            original()
        self.service._save=save
        self.assertTrue(self.command(enabled=True)['ok'])
    def test_recovery_stops_held_test(self):
        self.command(enabled=True)
        self.command(test_enabled=True)
        self.scope._recovery_outputs_inhibited=True
        self.service.step()
        self.assertEqual(self.stream.mode,0)


if __name__=='__main__':unittest.main()
