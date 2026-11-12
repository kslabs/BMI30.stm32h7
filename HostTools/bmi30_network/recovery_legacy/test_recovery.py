"""Real HTTP against the unchanged October 5 client with independent clocks."""
import contextlib
import importlib.util
import json
from pathlib import Path
import queue
import sys
import tarfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from legacy_recovery import LegacyRecovery, TARGET_ID
import patch_recovery

ARCHIVE = ROOT / 'FirmwareReleases/RaspberryPi/2026-10-05-network-13/BMI30-Network-2026-10-05-network-13.tar.gz'
with tarfile.open(ARCHIVE) as archive:
    ORIGINAL = archive.extractfile('2026-10-05-network-13/service.py').read().decode()
    PROTOCOL = archive.extractfile('2026-10-05-network-13/protocol.py').read().decode()
KEY = '17' * 32
DONOR = 'BMI30-D5507EDFC'


@contextlib.contextmanager
def directory():
    root = HERE / ('test-' + uuid.uuid4().hex)
    root.mkdir()
    try:
        yield root
    finally:
        for item in root.iterdir():
            assert item.resolve().parent == root.resolve()
            item.unlink()
        root.rmdir()


class Clock:
    def __init__(self, value):
        self.value = value


def module(clock, patched=False):
    protocol = types.ModuleType('shipped_protocol')
    exec(compile(PROTOCOL, 'shipped_protocol.py', 'exec'), protocol.__dict__)
    protocol.time = types.SimpleNamespace(time=lambda: clock.value)
    scope = {'__name__': 'shipped_service', '__file__': 'shipped_service.py'}
    with patch.dict(sys.modules, {'protocol': protocol}):
        exec(compile(patch_recovery.patch(ORIGINAL) if patched else ORIGINAL, 'shipped_service.py', 'exec'), scope)
    scope['time'] = types.SimpleNamespace(time=lambda: clock.value, monotonic=time.monotonic)
    scope['private_ip'] = lambda value: value  # Loopback only in this test.
    return scope


def agent(scope, ident, root, clock, secrets=None):
    obj = scope['Agent'].__new__(scope['Agent'])
    obj.config = {'id': ident}
    obj.protocol = scope['Protocol'](KEY)
    obj.records = scope['Records'](obj.protocol)
    obj.lock = threading.RLock()
    obj.boot, obj.seq = 'test-boot', 1
    obj.local = {'id': ident, 'boot': obj.boot, 'seq': obj.seq, 'issued': clock.value,
                 'internet': {'ok': False}, 'path': [], 'cost': 1000000}
    obj.records.records[ident] = obj.protocol.sign(obj.local)
    obj.secrets = dict(secrets or {})
    obj.secrets_path = root / (ident + '-secrets.json')
    obj.peers, obj.hold_until, obj.switching = {}, 0, False
    obj.state = {'version': patch_recovery.VERSION if 'LegacyRecovery' in scope else '0.1.13'}
    obj.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    obj.group = Mock()
    if 'LegacyRecovery' in scope:
        obj.legacy_recovery = LegacyRecovery(obj)
    return obj


def secret(updated, password='test-password'):
    return {'wifi:test-router': {'ssid': 'test-router', 'psk': password, 'security': 'wpa-psk',
                                 'origin': DONOR, 'updated': updated}}


@contextlib.contextmanager
def server(scope, obj):
    ready = queue.Queue()
    original = scope['ThreadingHTTPServer']

    class ReadyServer(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            ready.put(self)

    scope['ThreadingHTTPServer'], scope['PORT'] = ReadyServer, 0
    worker = threading.Thread(target=scope['serve'], args=(obj,), daemon=True)
    worker.start()
    http = ready.get(timeout=3)
    try:
        yield http
    finally:
        http.shutdown()
        http.server_close()
        worker.join(timeout=3)


class PatchTests(unittest.TestCase):
    def test_only_constructor_and_http_credential_endpoint_change(self):
        candidate = patch_recovery.patch(ORIGINAL)
        unchanged = patch_recovery.unchanged_methods(ORIGINAL, candidate)
        for name in ('remember_secret', 'own_secret', 'exchange', 'contact', 'discovery_loop',
                     'publish', 'gossip', 'route', 'execute', 'visit_peer', 'tick'):
            self.assertIn(name, unchanged)
        self.assertEqual(patch_recovery.patch(candidate), candidate)
        self.assertNotIn('time.time =', candidate)
        with self.assertRaises(ValueError):
            patch_recovery.patch(ORIGINAL.replace('VERSION = "0.1.13"', 'VERSION = "different"'))


@unittest.skipUnless(importlib.util.find_spec('cryptography'), 'cryptography required')
class RecoveryTests(unittest.TestCase):
    def setup_pair(self, root, old_date=0, donor_date=2000000000, patched=True, ident=TARGET_ID, old_secrets=None):
        old_clock, donor_clock = Clock(old_date), Clock(donor_date)
        old_module, donor_module = module(old_clock), module(donor_clock, patched)
        old = agent(old_module, ident, root, old_clock, old_secrets)
        donor = agent(donor_module, DONOR, root, donor_clock, secret(donor_date))
        return old_module, donor_module, old, donor, old_clock, donor_clock

    def test_unmodified_shipped_server_fails_with_independent_clocks(self):
        with directory() as folder:
            old_module, donor_module, old, donor, *_ = self.setup_pair(Path(folder), patched=False)
            with server(donor_module, donor) as http:
                old_module['PORT'] = http.server_port
                with self.assertRaises(urllib.error.HTTPError):
                    old.contact('127.0.0.1', DONOR)
            self.assertEqual(old.secrets, {})

    def test_original_receiver_persists_settings_using_its_own_clock(self):
        for old_date, donor_date in ((0, 2000000000), (2000000000, 0), (1600000000, 1900000000)):
            with self.subTest(old_date=old_date, donor_date=donor_date), directory() as folder:
                old_module, donor_module, old, donor, old_clock, donor_clock = self.setup_pair(
                    Path(folder), old_date, donor_date)
                original_settings = json.dumps(donor.secrets, sort_keys=True)
                with server(donor_module, donor) as http:
                    old_module['PORT'] = http.server_port
                    self.assertEqual(old.contact('127.0.0.1', DONOR)['id'], DONOR)
                saved = json.loads(old.secrets_path.read_text())['wifi:test-router']
                self.assertEqual(saved['psk'], 'test-password')
                self.assertLessEqual(abs(saved['updated'] - old_date), 1)
                self.assertEqual(json.dumps(donor.secrets, sort_keys=True), original_settings)
                self.assertEqual((old_clock.value, donor_clock.value), (old_date, donor_date))
                self.assertEqual(donor.legacy_recovery.snapshot()['stage'], 'settings-offered')
                # No old record can change the donor's route/records/commands.
                self.assertEqual(set(donor.records.records), {DONOR})
                donor.group.command.assert_not_called()

    def test_second_request_confirms_credentials_were_retained(self):
        with directory() as folder:
            old_module, donor_module, old, donor, *_ = self.setup_pair(Path(folder))
            with server(donor_module, donor) as http:
                old_module['PORT'] = http.server_port
                old.contact('127.0.0.1', DONOR)
                old.contact('127.0.0.1', DONOR)
                with donor.opener.open(f'http://127.0.0.1:{http.server_port}/status') as response:
                    status = json.load(response)['legacy_recovery']
            self.assertEqual(status['stage'], 'credentials-confirmed')
            self.assertEqual(status['confirmed_count'], 1)
            self.assertNotIn('test-password', json.dumps(status))

    def test_other_peers_keep_the_ordinary_exchange(self):
        with directory() as folder:
            old_module, donor_module, old, donor, *_ = self.setup_pair(
                Path(folder), 2000000000, 2000000000, ident='BMI30-D5EB8F26C')
            with server(donor_module, donor) as http:
                old_module['PORT'] = http.server_port
                self.assertEqual(old.contact('127.0.0.1', DONOR)['id'], DONOR)
            self.assertEqual(donor.legacy_recovery.snapshot()['requests'], 0)
            self.assertEqual(old.secrets['wifi:test-router']['psk'], 'test-password')

    def test_unknown_target_with_wrong_clock_cannot_use_recovery(self):
        with directory() as folder:
            old_module, donor_module, old, donor, *_ = self.setup_pair(Path(folder), ident='BMI30-UNKNOWN')
            with server(donor_module, donor) as http:
                old_module['PORT'] = http.server_port
                with self.assertRaises(urllib.error.HTTPError):
                    old.contact('127.0.0.1', DONOR)
            self.assertEqual(old.secrets, {})

    def test_replayed_recovery_request_is_rejected_after_donor_clock_changes(self):
        with directory() as folder:
            old_module, donor_module, old, donor, old_clock, donor_clock = self.setup_pair(Path(folder))
            captured = []
            original = old.opener.open

            def capture(request, *args, **kwargs):
                captured.append(request)
                return original(request, *args, **kwargs)

            old.opener.open = capture
            with server(donor_module, donor) as http:
                old_module['PORT'] = http.server_port
                old.contact('127.0.0.1', DONOR)
                for date in (donor_clock.value, old_clock.value):
                    donor_clock.value = date
                    with self.assertRaises(urllib.error.HTTPError):
                        original(captured[0], timeout=3)

    def test_wrong_key_and_modified_ciphertext_are_rejected(self):
        with directory() as folder:
            _, _, old, donor, *_ = self.setup_pair(Path(folder))
            body = {'request_id': 'a' * 32, 'self': old.records.records[TARGET_ID]}
            packet = old.protocol.seal(body)
            donor.protocol.key = bytes.fromhex('23' * 32)
            with self.assertRaises(Exception):
                donor.legacy_recovery.respond(packet)
            donor.protocol.key = bytes.fromhex(KEY)
            packet = packet[:-4] + b'AAAA'
            with self.assertRaises(Exception):
                donor.legacy_recovery.respond(packet)

    def test_future_conflicting_password_is_reported_without_false_success(self):
        with directory() as folder:
            old_module, donor_module, old, donor, *_ = self.setup_pair(
                Path(folder), old_secrets=secret(2000000000, 'old-test-password'))
            with server(donor_module, donor) as http:
                old_module['PORT'] = http.server_port
                old.contact('127.0.0.1', DONOR)
            self.assertEqual(old.secrets['wifi:test-router']['psk'], 'old-test-password')
            self.assertEqual(donor.legacy_recovery.snapshot()['stage'], 'future-timestamp-conflict')

    def test_old_receiver_can_reserve_a_real_parent_and_learn_its_ap(self):
        with directory() as folder:
            old_module, donor_module, old, donor, *_ = self.setup_pair(Path(folder))
            donor.local.update(path=[DONOR],cost=25,internet={'ok':True},
                               ap={'ssid':DONOR,'bssid':'00:11:22:33:44:55','address':'10.88.1.1/24'})
            donor.secrets['ap:'+DONOR]={'ssid':DONOR,'psk':'ap-test-password','security':'wpa-psk',
                                      'origin':DONOR,'updated':2000000000}
            with server(donor_module,donor) as http:
                old_module['PORT']=http.server_port
                reply=old.contact('127.0.0.1',DONOR,reserve=True)
            self.assertTrue(reply['reserved'])
            self.assertGreater(donor.hold_until,time.monotonic())
            self.assertTrue(reply['self']['data']['internet']['ok'])
            self.assertEqual(old.secrets['ap:'+DONOR]['psk'],'ap-test-password')
            self.assertEqual(reply['self']['data']['issued'],0)
            # Exercise the actual October 5 route-selection code.
            with tarfile.open(ARCHIVE) as archive:
                policy_source=archive.extractfile('2026-10-05-network-13/policy.py').read()
            scope={};exec(policy_source,scope)
            local={'id':TARGET_ID,'ap':{'address':'10.88.2.1/24'},'internet':{'ok':False}}
            visible=[{'ssid':DONOR,'bssid':'00:11:22:33:44:55','freq':5180,'channel':36,'signal':90}]
            choices=scope['candidates'](local,old.records.nodes(),visible,{})
            self.assertEqual(choices[0]['kind'],'relay')
            self.assertEqual(choices[0]['parent'],DONOR)

    def test_parent_loop_or_route_switch_cannot_receive_a_reservation(self):
        for path,switching in (([DONOR,TARGET_ID],False),([DONOR],True)):
            with self.subTest(path=path,switching=switching),directory() as folder:
                old_module, donor_module, old, donor, *_ = self.setup_pair(Path(folder))
                donor.local.update(path=path,cost=25,internet={'ok':True},
                                   ap={'ssid':DONOR,'bssid':'00:11:22:33:44:55','address':'10.88.1.1/24'})
                donor.switching=switching
                with server(donor_module,donor) as http:
                    old_module['PORT']=http.server_port
                    reply=old.contact('127.0.0.1',DONOR,reserve=True)
                self.assertFalse(reply['reserved'])


if __name__ == '__main__':
    unittest.main()
