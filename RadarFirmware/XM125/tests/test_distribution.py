"""Release integrity and multi-device provisioning failure-path checks."""
from pathlib import Path
import copy
from contextlib import contextmanager
import hashlib
import json
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import flash
import provision

PRESET = json.loads(provision.PRESET.read_text(encoding='utf-8'))


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Client:
    def __init__(self, clock, never_clear=False, stalled=False, corrupt_load=False):
        self.clock = clock
        self.never_clear = never_clear
        self.stalled = stalled
        self.corrupt_load = corrupt_load
        self.commands = []
        self.started = False
        self.state = {k: PRESET[k] for k in ('device', 'firmware', 'gpio0_mode')}
        self.state.update(config=copy.deepcopy(PRESET['config']), armed=1, calibrating=0,
                          fault=0, occupied=0, gpio0=0, gap_mm=1234, frame_count=0)

    def status(self):
        state = copy.deepcopy(self.state)
        state['frame_count'] = 0 if self.stalled else int(self.clock() * 30)
        if self.started and self.clock() < 3:
            state.update(armed=0, calibrating=1)
        if self.never_clear:
            state.update(occupied=1, gpio0=1)
        return state

    def apply(self, settings):
        self.commands.append('CONFIG')
        self.state['config'].update(settings)
        return self.status()

    def request(self, command):
        self.commands.append(command)
        if command == 'CALIBRATE':
            self.started = True
        if command == 'LOAD' and self.corrupt_load:
            self.state['config']['off_delay_ms'] = 700
        return {'ok': True}


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp_root = ROOT / 'build/distribution-tests'
        self.temp_root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def temporary(self):
        # Python 3.14's Windows mode=0700 temp directories exclude the sandbox
        # token. Use an inherited ACL inside the ignored build workspace.
        path = self.temp_root / ('test-' + uuid.uuid4().hex)
        path.mkdir()
        try:
            yield path
        finally:
            if not path.resolve().is_relative_to(self.temp_root.resolve()):
                raise RuntimeError('Temporary path escaped the test workspace')
            shutil.rmtree(path)

    def run_provision(self, client, clock, report):
        return provision.provision(client, PRESET, report, timeout=5,
                                   clock=clock, sleep=clock.sleep)

    def test_sealed_release_and_preset(self):
        elf, manifest = flash.verified_image(flash.RELEASE)
        self.assertEqual(manifest['bin_sha256'], PRESET['bin_sha256'])
        self.assertEqual(elf.with_suffix('.bin').stat().st_size, 103040)
        self.assertEqual(PRESET['config']['off_delay_ms'], 1000)
        self.assertEqual(PRESET['config']['adaptation_ms'], 180000)
        self.assertNotIn('gap_mm', PRESET)

    def test_corrupt_release_is_rejected(self):
        with self.temporary() as tmp:
            release = Path(tmp) / 'release'
            shutil.copytree(flash.RELEASE, release)
            image = release / 'firmware/bmi30_xm125_gate_0.1.2.elf'
            image.write_bytes(image.read_bytes() + b'corruption')
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                flash.verified_image(release)

    def test_calibrate_save_load_preserves_individual_gap(self):
        clock = Clock()
        client = Client(clock)
        client.state['config']['off_delay_ms'] = 100
        report = {}
        state = self.run_provision(client, clock, report)
        self.assertTrue(report['verified'])
        self.assertEqual(state['gap_mm'], 1234)
        self.assertEqual(state['config']['off_delay_ms'], 1000)
        self.assertEqual(client.commands, ['CONFIG', 'CALIBRATE', 'SAVE', 'LOAD'])
        self.assertGreaterEqual(clock(), 3.5)

    def test_wrong_firmware_is_rejected_before_configuration(self):
        clock = Clock()
        client = Client(clock)
        client.state['firmware'] = '0.1.1'
        with self.assertRaisesRegex(provision.RadarError, 'firmware'):
            self.run_provision(client, clock, {})
        self.assertFalse(client.commands)

    def test_unexpected_scan_limits_are_rejected(self):
        clock = Clock()
        client = Client(clock)
        client.state['config']['range_end_mm'] = 1500
        with self.assertRaisesRegex(provision.RadarError, 'range_end_mm'):
            self.run_provision(client, clock, {})
        self.assertFalse(client.commands)

    def test_occupied_calibration_is_never_saved(self):
        clock = Clock()
        client = Client(clock, never_clear=True)
        with self.assertRaisesRegex(provision.RadarError, 'not saved'):
            self.run_provision(client, clock, {})
        self.assertNotIn('SAVE', client.commands)

    def test_stalled_measurements_are_never_saved(self):
        clock = Clock()
        client = Client(clock, stalled=True)
        with self.assertRaisesRegex(provision.RadarError, 'not saved'):
            self.run_provision(client, clock, {})
        self.assertNotIn('SAVE', client.commands)

    def test_persistent_readback_mismatch_is_reported(self):
        clock = Clock()
        client = Client(clock, corrupt_load=True)
        report = {}
        with self.assertRaisesRegex(provision.RadarError, 'readback mismatch'):
            self.run_provision(client, clock, report)
        self.assertTrue(report['saved'])
        self.assertNotIn('verified', report)

    def flash_mock(self, identity, backup_size):
        with self.temporary() as tmp:
            backup = Path(tmp) / 'before.bin'
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                if '-u' in command:
                    backup.write_bytes(b'\xff' * backup_size)
                return subprocess.CompletedProcess(command, 0, identity.encode())

            argv = ['flash.py', '--serial', 'TEST123', '--programmer', 'test-cli',
                    '--backup', str(backup)]
            with patch.object(sys, 'argv', argv), patch.object(flash.subprocess, 'run', side_effect=run):
                try:
                    flash.main()
                except RuntimeError:
                    return calls, False
            self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(),
                             hashlib.sha256(b'\xff' * backup_size).hexdigest())
            return calls, True

    def test_flash_backs_up_before_verified_download(self):
        calls, success = self.flash_mock('Device ID   : 0x435\nFlash size  : 128 KBytes', 131072)
        self.assertTrue(success)
        self.assertIn('-u', calls[1])
        self.assertIn('-d', calls[2])
        self.assertIn('-v', calls[2])
        self.assertFalse(any('-e' in c for c in calls))

    def test_flash_wrong_target_never_downloads(self):
        calls, success = self.flash_mock('Device ID   : 0x450\nFlash size  : 2048 KBytes', 131072)
        self.assertFalse(success)
        self.assertEqual(len(calls), 1)

    def test_incomplete_backup_prevents_download(self):
        calls, success = self.flash_mock('Device ID   : 0x435\nFlash size  : 128 KBytes', 4096)
        self.assertFalse(success)
        self.assertFalse(any('-d' in c for c in calls))


if __name__ == '__main__':
    unittest.main()
