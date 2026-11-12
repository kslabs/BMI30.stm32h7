"""BMI30 distributed network service; acquisition and RS485 are never opened."""
from __future__ import annotations
import argparse
import base64
import concurrent.futures
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import random
import socket
import threading
import time
import urllib.request
import uuid
import network
import access_policy
import policy
import group
from protocol import Protocol, Records, encode, MAX_PACKET, ID
from freshness import Challenges, age
from bluetooth_transport import Manager as BluetoothManager
from transport_policy import Selection, allows
PORT = 8791
DISCOVERY = 8792
"""Credential-only v1 replies for one named, already enrolled recovery target.

The old caller supplies its own clock inside an authenticated AES-GCM request.
Only outgoing v1 fields are translated; neither system clock is changed.
This endpoint never imports old routing records or executes old commands.
"""
import base64
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import threading
import time
import copy

import policy

from protocol import ID, MAX_PACKET, encode

TARGET_ID = 'BMI30-27448D72B'
DONOR_IDS = frozenset(('BMI30-05AB38E6A', 'BMI30-D5507EDFC', 'BMI30-7AA5CED27', 'BMI30-27448D72B', 'BMI30-E29805C7C'))
REQUEST_ID = re.compile(r'[0-9a-f]{32}\Z')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def forgotten(ssid):
    # Older 0.1.13 images have no access_policy module. Honour an existing
    # newer local block list without introducing a dependency or changing it.
    try:
        settings = json.loads(Path('/var/lib/bmi30-access/settings.json').read_text())
        return ssid in settings.get('forgotten_ssids', [])
    except FileNotFoundError:
        return False


class LegacyRecovery:
    def __init__(self, agent):
        self.agent = agent
        self.lock = threading.RLock()
        self.nonces = {}
        self.requests = {}
        self.last_seen = None
        self.state = {'target_id': TARGET_ID, 'mode': 'credential-only-v1',
                      'stage': 'waiting-for-target', 'requests': 0,
                      'offered_count': 0, 'confirmed_count': 0,
                      'future_timestamp_conflicts': 0}

    def enabled(self):
        return self.agent.config['id'] in DONOR_IDS

    def is_forgotten(self, ssid):
        return forgotten(ssid)

    def snapshot(self):
        with self.lock:
            return {**self.state, 'enabled': self.enabled(),
                    'last_seen_age_s': None if self.last_seen is None else
                    round(max(0, time.monotonic() - self.last_seen), 3)}

    def check_replay(self, raw):
        binary = base64.b64decode(raw, validate=True)
        with self.lock:
            if self.nonces.get(binary[:12], -1) > time.monotonic():
                raise ValueError('Replayed recovery packet')

    def sign(self, data):
        return {'data': data, 'mac': hmac.new(self.agent.protocol.key,
                b'discovery-v1' + encode(data), hashlib.sha256).hexdigest()}

    def open(self, raw):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if not self.enabled() or not 1 <= len(raw) <= MAX_PACKET:
            raise ValueError('Legacy recovery unavailable')
        binary = base64.b64decode(raw, validate=True)
        if len(binary) < 29:
            raise ValueError('Short legacy packet')
        nonce = binary[:12]
        envelope = json.loads(AESGCM(self.agent.protocol.key).decrypt(
            nonce, binary[12:], b'bmi30-network-v1'))
        peer_at, body = envelope['at'], envelope['payload']
        if not number(peer_at) or peer_at < 0 or not isinstance(body, dict):
            raise ValueError('Invalid legacy envelope')
        record = body['self']
        data = record['data']
        if (data.get('id') != TARGET_ID or record.get('version') is not None
                or not hmac.compare_digest(self.sign(data)['mac'], str(record.get('mac', '')))):
            raise ValueError('Wrong legacy target or authentication')
        issued = data.get('issued')
        if (not number(issued) or not -15 <= peer_at - issued <= 100
                or type(data.get('seq')) is not int or data['seq'] < 0
                or not isinstance(data.get('boot'), str) or not 1 <= len(data['boot']) <= 64
                or not REQUEST_ID.fullmatch(str(body.get('request_id', '')))):
            raise ValueError('Invalid legacy request')
        now = time.monotonic()
        request = body['request_id']
        with self.lock:
            self.nonces = {k: expiry for k, expiry in self.nonces.items() if expiry > now}
            self.requests = {k: expiry for k, expiry in self.requests.items() if expiry > now}
            if nonce in self.nonces or request in self.requests:
                raise ValueError('Replayed legacy request')
            # A request already accepted through the ordinary v1 endpoint
            # cannot be replayed through recovery after either clock changes.
            if nonce in self.agent.protocol.replays:
                raise ValueError('Request already handled by normal exchange')
            if len(self.nonces) >= 4096:
                raise ValueError('Legacy replay capacity exceeded')
            self.nonces[nonce] = self.requests[request] = now + 180
        return peer_at, body

    def respond(self, raw):
        started = time.monotonic()
        peer_at, body = self.open(raw)
        incoming = body.get('credentials', {})
        if not isinstance(incoming, dict):
            raise ValueError('Invalid legacy credentials')
        offered, confirmed, conflicts = {}, 0, 0
        ap_offered = ap_confirmed = 0
        with self.agent.lock:
            local = self.agent.local or {}
            own_ap_key = 'ap:' + self.agent.config['id']
            # These are already shared fleet credentials, not every NM profile.
            for key, secret in list(self.agent.secrets.items())[:256]:
                wifi = key.startswith('wifi:')
                own_ap = key == own_ap_key
                if (not (wifi or own_ap) or not isinstance(secret, dict)
                        or (wifi and (secret.get('ssid') != key[5:] or forgotten(key[5:])))
                        or (own_ap and (secret.get('origin') != self.agent.config['id']
                                       or secret.get('ssid') != local.get('ap', {}).get('ssid')))
                        or not isinstance(secret.get('psk'), str) or not 8 <= len(secret['psk']) <= 64
                        or secret.get('security') not in ('wpa-psk', 'sae')
                        or not ID.fullmatch(str(secret.get('origin', '')))):
                    continue
                old = incoming.get(key, {})
                if not isinstance(old, dict):
                    old = {}
                if old.get('psk') == secret['psk'] and old.get('security') == secret['security']:
                    if wifi: confirmed += 1
                    else: ap_confirmed += 1
                    continue
                old_at = old.get('updated', 0)
                old_at = old_at if number(old_at) else 0
                stamp = max(peer_at, old_at + .001)
                # The unchanged receiver requires updated <= its own now + 15.
                # An old future-dated conflicting password cannot be fixed here.
                if stamp > peer_at + 10:
                    conflicts += 1
                    continue
                offered[key] = {k: secret[k] for k in ('ssid', 'psk', 'security', 'origin')}
                offered[key]['updated'] = stamp
                if own_ap: ap_offered += 1
            own = {'id': self.agent.config['id'], 'boot': self.agent.boot,
                   'seq': self.agent.seq, 'issued': peer_at, 'role': local.get('role', 'unknown'),
                   'rs485_id': local.get('rs485_id', 255), 'path': [], 'cost': 1000000,
                   'internet': {'ok': False}, 'addresses': [], 'broadcasts': [], 'ap': {}}
            relay_available = bool(policy.valid_path(local, TARGET_ID) and local.get('ap', {}).get('bssid')
                                   and local.get('ap', {}).get('address') and local.get('ap', {}).get('ssid'))
            reserved = False
            if relay_available:
                for field in ('role', 'rs485_id', 'path', 'cost', 'internet', 'ap', 'ssid', 'bssid',
                              'freq', 'signal', 'rssi_dbm', 'uplink', 'iface', 'gateway', 'addresses', 'broadcasts'):
                    if field in local:
                        own[field] = copy.deepcopy(local[field])
                if body.get('reserve') is True and not self.agent.switching:
                    self.agent.hold_until = max(self.agent.hold_until, time.monotonic() + 120)
                    reserved = True
            signed = self.sign(own)
            reply = {'id': own['id'], 'request_id': body['request_id'], 'self': signed,
                     'records': [signed], 'credentials': offered, 'reserved': reserved}
        elapsed = time.monotonic() - started
        if elapsed > 2:
            raise ValueError('Legacy reply deadline exceeded')
        # Keep both the envelope and the signed record in the caller's time.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import os
        nonce = os.urandom(12)
        response = base64.b64encode(nonce + AESGCM(self.agent.protocol.key).encrypt(
            nonce, encode({'at': peer_at + elapsed, 'payload': reply}), b'bmi30-network-v1'))
        if len(response) > MAX_PACKET:
            raise ValueError('Legacy reply too large')
        with self.lock:
            self.last_seen = time.monotonic()
            wifi_offered = len(offered) - ap_offered
            self.state.update(stage=('settings-offered' if wifi_offered else 'future-timestamp-conflict' if conflicts
                                     else 'credentials-confirmed' if confirmed else 'relay-ready' if relay_available
                                     else 'no-shared-wifi-settings'),
                              requests=self.state['requests'] + 1, offered_count=wifi_offered,
                              confirmed_count=confirmed, future_timestamp_conflicts=conflicts,
                              peer_clock=peer_at, ap_offered_count=ap_offered, ap_confirmed_count=ap_confirmed,
                              relay_available=relay_available, relay_reserved=reserved)
        return response

"""Bounded outbound v1 recovery; folded into the two-file menu payload.

An unauthenticated HTTP date is only a clock hint. The first encrypted RPC
contains no credentials; the named peer must authenticate its response before
any Wi-Fi settings are offered. System clocks and installed profiles are kept.
"""
import base64
import copy
import hashlib
import hmac
import ipaddress
import json
import math
from pathlib import Path
import threading
import time
import uuid
import urllib.request
from email.utils import parsedate_to_datetime

from protocol import encode, MAX_PACKET

ACTIVE_DONOR = 'BMI30-05AB38E6A'
ACTIVE_TARGET = 'BMI30-27448D72B'
TARGET_SSIDS = ('BMI30-27448D72B', 'BMI30-A043B0627448D72B')

class RecoveryDeferred(RuntimeError):
    """Maintenance owns the firmware lock; no radio change has started."""
    pass

# A service crash must not strand the original AP/STA or its busy marker.
# This guardian runs independently in systemd and never contains a Wi-Fi PSK.
RADIO_GUARD = r'''
import pathlib,json,subprocess,sys,hashlib
P=pathlib.Path;folder=P(sys.argv[1]);p=json.loads((folder/'state.json').read_text())
assert 'BMI30-'+P('/proc/device-tree/serial-number').read_text().strip('\0\n')[-9:].upper()==p['id']
def run(*args):return subprocess.run(args,capture_output=True,text=True,timeout=35)
def active(iface):return run('nmcli','-g','GENERAL.CON-UUID','dev','show',iface).stdout.strip()
busy=P('/run/bmi30-access/busy');owns=busy.is_file() and busy.read_text()==p['token']
if not owns:raise SystemExit
if hashlib.sha256(P('/opt/bmi30-network/service.py').read_bytes()).hexdigest()!=p['service_sha']:
 busy.unlink();raise SystemExit
if active(p['ap'])!=p['ap_uuid']:run('nmcli','--wait','20','con','up','uuid',p['ap_uuid'])
if active(p['sta'])!=p['station_uuid']:run('nmcli','--wait','25','con','up','uuid',p['station_uuid'])
restored=active(p['sta'])==p['station_uuid'] and active(p['ap'])==p['ap_uuid']
if restored:
 run('nmcli','con','delete','uuid',p['temporary_id']);busy.unlink(missing_ok=True)
(folder/'guard-result.json').write_text(json.dumps({'restored':restored}))
'''


class ActiveRecovery:
    def __init__(self, recovery):
        self.recovery, self.agent = recovery, recovery.agent
        self.lock = threading.RLock()
        self.started = time.monotonic()
        self.last_attempt = None
        self.retry_not_before = 0
        self.clock_counter = 0
        self.port = 8791
        self.state = {'enabled': self.agent.config['id'] == ACTIVE_DONOR,
                      'stage': 'warming-up' if self.agent.config['id'] == ACTIVE_DONOR else 'standby',
                      'attempts': 0, 'authenticated': False, 'confirmed_wifi': 0,
                      'continuous_search': True, 'attempt_limit': None,
                      'scan_interval_s': 65, 'retry_interval_s': 600}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def mark(self, **values):
        with self.lock:
            self.state.update(values)

    def seal(self, payload, at):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import os
        nonce = os.urandom(12)
        return base64.b64encode(nonce + AESGCM(self.agent.protocol.key).encrypt(
            nonce, encode({'at': at, 'payload': payload}), b'bmi30-network-v1'))

    def open_reply(self, raw, ident, at, elapsed):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if not 1 <= len(raw) <= MAX_PACKET:
            raise ValueError('Invalid recovery reply size')
        binary = base64.b64decode(raw, validate=True)
        packet = json.loads(AESGCM(self.agent.protocol.key).decrypt(
            binary[:12], binary[12:], b'bmi30-network-v1'))
        timestamp = packet.get('at')
        if (type(timestamp) not in (int, float) or not math.isfinite(timestamp)
                or not -15 <= at + elapsed - timestamp <= 90):
            raise ValueError('Recovery reply clock mismatch')
        reply = packet['payload']
        if reply.get('id') != ACTIVE_TARGET or reply.get('request_id') != ident:
            raise ValueError('Recovery reply origin mismatch')
        record = reply['self']; data = record['data']
        signature = hmac.new(self.agent.protocol.key, b'discovery-v1' + encode(data), hashlib.sha256).hexdigest()
        issued = data.get('issued')
        if (data.get('id') != ACTIVE_TARGET or not hmac.compare_digest(signature, str(record.get('mac', '')))
                or type(issued) not in (int, float) or not math.isfinite(issued)
                or not -15 <= timestamp - issued <= 100):
            raise ValueError('Recovery peer authentication failed')
        return reply, timestamp

    def rpc(self, address, at, credentials):
        # Every probe is bound to a new request id, not merely a signed old ad.
        ident = uuid.uuid4().hex
        with self.agent.lock:
            self.clock_counter += 1
            own = {'id': self.agent.config['id'], 'boot': self.agent.boot,
                   'seq': self.agent.seq + self.clock_counter, 'issued': at,
                   'role': (self.agent.local or {}).get('role', 'unknown'),
                   'path': [], 'cost': 1000000, 'internet': {'ok': False},
                   'ap': copy.deepcopy((self.agent.local or {}).get('ap', {}))}
        body = {'request_id': ident, 'self': self.recovery.sign(own),
                'records': [], 'credentials': credentials, 'reserve': False}
        request = urllib.request.Request('http://' + address + ':' + str(self.port) + '/exchange',
                    self.seal(body, at), {'Content-Type': 'application/octet-stream'})
        started = time.monotonic()
        with self.agent.opener.open(request, timeout=4) as response:
            raw = response.read(MAX_PACKET + 1)
        return self.open_reply(raw, ident, at, time.monotonic() - started)

    def provision(self, address):
        address = str(ipaddress.IPv4Address(address))
        ip = ipaddress.IPv4Address(address)
        if not ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_multicast:
            raise ValueError('Recovery requires the associated private gateway')
        with self.agent.opener.open('http://' + address + ':' + str(self.port) + '/status', timeout=4) as response:
            hint = json.loads(response.read(MAX_PACKET + 1))
            date = response.headers.get('Date')
        if (hint.get('local') or {}).get('id') != ACTIVE_TARGET:
            raise ValueError('Recovery gateway advertises another device')
        at = parsedate_to_datetime(date).timestamp() if date else hint.get('checked_at')
        if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
            raise ValueError('Recovery peer clock unavailable')
        # The status/Date hint is untrusted: authenticate with an empty probe.
        proof, authenticated_at = self.rpc(address, at, {})
        self.mark(authenticated=True, stage='peer-authenticated', peer_clock=authenticated_at)
        self.mark(reported_target_automatic=hint.get('automatic'),
                  reported_target_clients=(hint.get('local') or {}).get('clients'),
                  reported_target_internet=bool(((hint.get('local') or {}).get('internet') or {}).get('ok')))
        credentials = {}
        with self.agent.lock:
            for key, value in list(self.agent.secrets.items())[:256]:
                if (not key.startswith('wifi:') and key != 'ap:' + self.agent.config['id']):
                    continue
                if not isinstance(value, dict) or not isinstance(value.get('psk'), str):
                    continue
                if not 8 <= len(value['psk']) <= 64 or value.get('security') not in ('wpa-psk', 'sae'):
                    continue
                if key.startswith('wifi:'):
                    # Reuse the passive path's existing local forget policy.
                    if self.recovery.is_forgotten(key[5:]):
                        continue
                old = (proof.get('credentials') or {}).get(key) or {}
                old_at = old.get('updated', 0) if isinstance(old, dict) else 0
                old_at = old_at if type(old_at) in (int, float) and math.isfinite(old_at) else 0
                stamp = max(authenticated_at + .001, old_at + .001)
                if stamp > authenticated_at + 10:
                    self.mark(future_timestamp_conflict=True)
                    continue
                credentials[key] = {k: value[k] for k in ('ssid', 'psk', 'security', 'origin')}
                credentials[key]['updated'] = stamp
        if not any(k.startswith('wifi:') for k in credentials):
            self.mark(stage='no-shared-wifi-settings')
            return
        reply, _ = self.rpc(address, authenticated_at + .01, credentials)
        retained = reply.get('credentials') or {}
        confirmed = sum(1 for key, value in credentials.items() if key.startswith('wifi:')
                        and isinstance(retained.get(key), dict)
                        and all(retained[key].get(k) == value[k] for k in ('ssid', 'psk', 'security')))
        self.mark(stage='settings-confirmed' if confirmed else 'settings-not-retained',
                  confirmed_wifi=confirmed, offered_wifi=sum(k.startswith('wifi:') for k in credentials))

    def known_target(self):
        with self.agent.lock:
            for peer in self.agent.config.get('peers', []):
                if peer.get('id') in TARGET_SSIDS and isinstance(peer.get('psk'), str):
                    return peer['psk']
            secret = self.agent.secrets.get('ap:' + ACTIVE_TARGET) or {}
            return secret.get('psk')

    @staticmethod
    def select_target(visible):
        choices = [a for a in visible if a.get('ssid') in TARGET_SSIDS]
        return min(choices, key=lambda a: (a.get('freq', 0) >= 4900,
                                         -a.get('signal', 0)), default=None)

    def refresh_target_scan(self, adapter):
        # An AP on the same PHY can prevent discovery on another channel.
        # Called only inside the existing guarded visit, after our AP is down.
        # RequestScan completion is asynchronous: allow its cache to refresh.
        self.mark(stage='scanning-with-donor-ap-down', scan_refresh_attempted=True)
        import network
        network.run('nmcli', '--wait', '5', 'device', 'wifi', 'rescan', 'ifname', adapter.sta,
                    'ssid', TARGET_SSIDS[0], 'ssid', TARGET_SSIDS[1], check=False)
        visible = adapter.visible(rescan=False)
        for attempt in range(7):
            choice = self.select_target(visible)
            if choice is not None:
                self.mark(scan_target_visible=True)
                return choice
            if attempt == 6:
                break
            time.sleep(1)
            visible = adapter.visible(rescan=False)
        self.mark(stage='target-not-visible-after-ap-down-scan', scan_target_visible=False)
        return None

    @staticmethod
    def wireless_settings(choice):
        result = {'ssid': choice['ssid'].encode(), 'mode': 'infrastructure'}
        if choice.get('bssid'):
            result['bssid'] = bytes.fromhex(choice['bssid'].replace(':', ''))
        if choice.get('freq'):
            result['band'] = 'a' if choice['freq'] >= 4900 else 'bg'
        else:
            result['hidden'] = True
        return result

    def maybe_visit(self, local):
        if not self.state['enabled'] or self.state['stage'] == 'settings-confirmed':
            return False
        now = time.monotonic()
        if now - self.started < 180:
            return False
        if now < self.retry_not_before:
            return False
        if self.last_attempt is not None and now - self.last_attempt < 600:
            self.mark(stage='waiting-for-retry', retry_in_s=round(600 - (now - self.last_attempt)))
            return False
        if self.state.get('restored') is False:
            return False
        if (self.agent.switching or local.get('clients') or not (local.get('internet') or {}).get('ok')
                or self.agent.config.get('observe') or self.agent.network.remote_desktop_active()
                or not self.agent.network.permissions().get('wifi', True)):
            self.mark(stage='waiting-for-idle-radio')
            return False
        psk = self.known_target()
        if not isinstance(psk, str) or not 8 <= len(psk) <= 64:
            self.mark(stage='target-ap-credential-unavailable')
            return False
        choice = self.select_target(local.get('visible', []))
        if choice is None:
            # The ordinary service keeps scanning every 65 seconds. Do not
            # take the donor AP/STA down repeatedly to probe an absent SSID.
            self.mark(stage='target-not-visible')
            return False
        previous_attempt = self.last_attempt
        attempts_before = self.state['attempts']
        self.last_attempt = now
        self.mark(stage='connecting-to-target',
                  attempts=self.state['attempts'] + 1,
                  target_frequency_mhz=choice.get('freq') if choice else None,
                  error_type=None, failed_at=None)
        try:
            self.visit(choice, psk)
        except RecoveryDeferred:
            self.last_attempt = previous_attempt
            self.retry_not_before = now + 30
            self.mark(stage='waiting-for-maintenance', attempts=attempts_before,
                      deferred_count=self.state.get('deferred_count', 0) + 1,
                      error_type=None, failed_at=None, retry_in_s=30)
            return False
        except Exception as error:
            # Exception text may contain NetworkManager details; do not export it.
            self.mark(failed_at=self.state['stage'], stage='active-attempt-failed', error_type=type(error).__name__)
        return True

    def release_radio(self, network, busy, token, unit, temporary_id, restored):
        try:
            if restored:
                # Release the marker before cancelling its independent guard.
                # A failed nmcli/systemctl cleanup must not keep switching=True.
                if busy.is_file() and busy.read_text() == token:
                    busy.unlink()
                for command in (('nmcli', 'con', 'delete', 'uuid', temporary_id),
                                ('systemctl', 'stop', unit + '.timer')):
                    try:
                        network.run(*command, check=False)
                    except Exception:
                        self.mark(cleanup_error=True)
        finally:
            self.agent.switching = False
            self.mark(restored=restored)

    def visit(self, choice, psk):
        import dbus
        import network
        import fcntl
        adapter = self.agent.network
        old = adapter.snapshot()
        if old['clients']:
            raise RuntimeError('Recovery radio became busy')
        lock_path = Path('/home/techaid/Documents/.bmi30_cloud_sync/server_operation.lock')
        with lock_path.open('rb') as firmware_lock:
            try:
                fcntl.flock(firmware_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RecoveryDeferred() from error
            bus = dbus.SystemBus()
            manager = dbus.Interface(bus.get_object('org.freedesktop.NetworkManager',
                            '/org/freedesktop/NetworkManager'), 'org.freedesktop.NetworkManager')
            checkpoint = network.Checkpoint((adapter.sta, adapter.ap), timeout=120)
            temporary_id = str(uuid.uuid4())
            busy = Path('/run/bmi30-access/busy')
            if busy.exists():
                checkpoint.rollback()
                raise RuntimeError('Another access operation is running')
            busy.parent.mkdir(parents=True, exist_ok=True)
            folder = Path('/run/bmi30-legacy-active') / uuid.uuid4().hex
            folder.mkdir(parents=True, mode=0o700)
            token = 'bmi30-legacy-active:' + folder.name
            unit = 'bmi30-active-radio-rollback-' + folder.name[:12]
            state = {k: old[k] for k in ('station_uuid', 'ap_uuid')}
            state.update(id=self.agent.config['id'], sta=adapter.sta, ap=adapter.ap,
                         temporary_id=temporary_id, token=token,
                         service_sha=hashlib.sha256(Path('/opt/bmi30-network/service.py').read_bytes()).hexdigest())
            (folder / 'state.json').write_text(json.dumps(state))
            (folder / 'guard.py').write_text(RADIO_GUARD)
            network.run('systemd-run', '--quiet', '--unit=' + unit, '--on-active=90s',
                        '--timer-property=AccuracySec=1s', '/usr/bin/python3', str(folder / 'guard.py'), str(folder))
            busy.write_text(token)
            self.agent.switching = True
            try:
                # Keep our AP down during the visit: it may use the same subnet
                # as S12. Re-enabling it here would route HTTP to ourselves.
                network.run('nmcli', '--wait', '8', 'con', 'down', 'uuid', old['ap_uuid'], check=False)
                if choice is None:
                    choice = self.refresh_target_scan(adapter)
                    if choice is None:
                        choice = {'ssid': TARGET_SSIDS[(self.state['attempts'] - 1) % len(TARGET_SSIDS)]}
                        self.mark(direct_name_probe=True)
                    self.mark(stage='connecting-to-target', target_frequency_mhz=choice.get('freq'))
                wireless = self.wireless_settings(choice)
                wireless['ssid'] = dbus.ByteArray(wireless['ssid'])
                if 'bssid' in wireless:
                    wireless['bssid'] = dbus.ByteArray(wireless['bssid'])
                settings = dbus.Dictionary({
                    'connection': dbus.Dictionary({'id': 'bmi30-recovery-ephemeral', 'uuid': temporary_id,
                        'type': '802-11-wireless', 'interface-name': adapter.sta, 'autoconnect': False}, signature='sv'),
                    '802-11-wireless': dbus.Dictionary(wireless, signature='sv'),
                    '802-11-wireless-security': dbus.Dictionary({'key-mgmt': 'wpa-psk', 'psk': psk}, signature='sv'),
                    'ipv4': dbus.Dictionary({'method': 'auto', 'route-metric': dbus.UInt32(600)}, signature='sv'),
                    'ipv6': dbus.Dictionary({'method': 'disabled'}, signature='sv')}, signature='sa{sv}')
                device = manager.GetDeviceByIpIface(adapter.sta)
                manager.AddAndActivateConnection2(settings, device, dbus.ObjectPath('/'),
                           dbus.Dictionary({'persist': 'memory'}, signature='sv'))
                props = dbus.Interface(bus.get_object('org.freedesktop.NetworkManager', device),
                                       'org.freedesktop.DBus.Properties')
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if int(props.Get('org.freedesktop.NetworkManager.Device', 'State')) == 100:
                        break
                    time.sleep(.5)
                else:
                    self.mark(stage='target-association-failed')
                    raise RuntimeError('Target association deadline')
                current = adapter.snapshot()
                if current.get('ssid') != choice['ssid']:
                    raise RuntimeError('Recovery associated to another SSID')
                if choice.get('bssid') and current.get('bssid', '').lower() != choice['bssid'].lower():
                    raise RuntimeError('Recovery associated to another BSSID')
                self.mark(stage='target-associated', target_frequency_mhz=current.get('freq'))
                self.provision(current['gateway'])
            finally:
                restored = False
                try:
                    try:
                        checkpoint.rollback()
                    except Exception:
                        self.mark(checkpoint_error=True)
                    if adapter.active(adapter.sta) != old['station_uuid']:
                        network.run('nmcli', '--wait', '25', 'con', 'up', 'uuid', old['station_uuid'], timeout=30)
                    if adapter.active(adapter.ap) != old['ap_uuid']:
                        network.run('nmcli', '--wait', '15', 'con', 'up', 'uuid', old['ap_uuid'], timeout=20)
                    adapter.wait_restored(old)
                    restored = (adapter.active(adapter.sta) == old['station_uuid']
                                and adapter.active(adapter.ap) == old['ap_uuid'])
                finally:
                    self.release_radio(network, busy, token, unit, temporary_id, restored)

# Resume the independent menu-rollback guard after a board reboot.
_CredentialRecovery = LegacyRecovery
class LegacyRecovery(_CredentialRecovery):
    def __init__(self, agent):
        super().__init__(agent)
        self.active = ActiveRecovery(self)
        self.state['mode'] = 'active-and-passive-v1'
        try:
            import subprocess
            directory = Path('/var/lib/bmi30-legacy-menu') / BOUND_BUNDLE
            guard = directory / 'guard.py'
            unit = 'bmi30-legacy-guard-' + BOUND_BUNDLE
            if (directory / 'state.json').is_file() and guard.is_file() and hashlib.sha256(guard.read_bytes()).hexdigest() == GUARD_SHA:
                status = subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=3)
                if status.stdout.strip() != 'active':
                    if (Path('/etc/systemd/system') / (unit + '.service')).is_file():
                        subprocess.run(['systemctl','start',unit],capture_output=True,timeout=5)
                    else:
                        subprocess.run(['systemd-run','--quiet','--collect','--unit='+unit,
                                        '--property=Restart=on-failure','--property=RestartSec=3',
                                        '--property=StartLimitIntervalSec=0',
                                        '/usr/bin/python3',str(guard),'--state',str(directory/'state.json')],
                                       capture_output=True,timeout=5)
        except Exception:
            pass

    def snapshot(self):
        return {**super().snapshot(), 'active': self.active.snapshot()}

BOUND_BUNDLE = '2026-10-30-112226'
GUARD_SHA = 'd15bb8ac554a4b9dd42e3a9501a029ed5c09f5a099c9a9e7041b00a77d57dccd'

VERSION = '0.2.9'
LOG = logging.getLogger('bmi30-network')

def private_ip(value):
    address = ipaddress.IPv4Address(value)
    if not address.is_private or address.is_loopback or address.is_multicast or address.is_unspecified:
        raise ValueError('Not a LAN address')
    return str(address)

class Agent:

    def __init__(self, config):
        config = {**config, 'id': network.hardware_identity()}
        self.config = config
        self.network = network.Network(config)
        self.protocol = Protocol(config['fleet_key'])
        self.challenges = Challenges()
        self.records = Records(self.protocol)
        self.lock = threading.RLock()
        self.peers = {}
        self.boot, self.seq = (uuid.uuid4().hex, 0)
        self.decision = policy.Decision()
        self.state = {'version': VERSION, 'status': 'starting', 'clock_mode': 'monotonic-v2'}
        self.local = None
        self.secrets_path = Path(config.get('state_dir', '/var/lib/bmi30-network')) / 'credentials.json'
        self.secrets = json.loads(self.secrets_path.read_text()) if self.secrets_path.exists() else {}
        self.own_sources_path = self.secrets_path.with_name('credential-sources.json')
        self.own_sources = json.loads(self.own_sources_path.read_text()) if self.own_sources_path.exists() else {}
        self.profile_cache = {}
        self.last_scan = self.last_profiles = 0
        self.hold_until = 0
        self.history = []
        self.offline = 0
        self.switching = False
        self.last_visit = {}
        self.session_start = time.monotonic()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.transports = Selection(config.get('state_dir', '/var/lib/bmi30-network'))
        self.group = group.Group(self)
        self.bluetooth = BluetoothManager(self)
        self.legacy_recovery = LegacyRecovery(self)

    def remember_secret(self, key, value):
        if isinstance(key, str) and key.startswith('wifi:') and access_policy.forgotten(key[5:]):
            return
        if not isinstance(key, str) or len(key) > 100 or (not isinstance(value, dict)):
            return
        if not isinstance(value.get('psk'), str) or not 8 <= len(value['psk']) <= 64 or value.get('security') not in ('wpa-psk', 'sae') or (not ID.fullmatch(value.get('origin', ''))) or (not isinstance(value.get('updated'), (int, float))) or (type(value.get('revision')) is not int) or (value['revision'] < 1) or (not isinstance(value.get('ssid'), str)) or (not 1 <= len(value['ssid'].encode()) <= 32):
            return
        old = self.secrets.get(key)
        if not old or (value['revision'], value['origin']) > (old.get('revision', 0), old.get('origin', '')):
            if len(self.secrets) >= 256 and key not in self.secrets:
                return
            self.secrets[key] = value
            network.atomic(self.secrets_path, json.dumps(self.secrets))

    def own_secret(self, key, secret):
        value = {k: secret[k] for k in ('ssid', 'psk', 'security')}
        fingerprint = hashlib.sha256(encode(value)).hexdigest()
        if self.own_sources.get(key) == fingerprint and self.secrets.get(key, {}).get('revision', 0) >= 1:
            return
        self.own_sources[key] = fingerprint
        network.atomic(self.own_sources_path, json.dumps(self.own_sources))
        revision = max((int(v.get('revision', 0)) for v in self.secrets.values()), default=0) + 1
        self.remember_secret(key, {**value, 'origin': self.config['id'], 'revision': revision, 'updated': time.time()})

    def exchange(self, incoming, source, transit=0):
        with self.lock:
            sender = self.protocol.verify(incoming['self'])
            if sender['id'] != incoming.get('_source_id'):
                raise ValueError('Wrong exchange origin')
            if incoming.get('request_id') is None:
                raise ValueError('Missing request identifier')
            self.merge_peer_records([incoming['self']] + incoming.get('records', [])[:127], incoming['record_ages'], transit)
            self.peers[sender['id'], private_ip(source)] = time.monotonic()
            if len(self.peers) > 512:
                self.peers = dict(sorted(self.peers.items(), key=lambda kv: kv[1], reverse=True)[:256])
            for key, secret in list(incoming.get('credentials', {}).items())[:256]:
                self.remember_secret(key, secret)
            accepted = False
            if incoming.get('reserve'):
                accepted = bool(self.local and (not self.switching) and policy.valid_path(self.local, sender['id']))
                if accepted:
                    self.hold_until = max(self.hold_until, time.monotonic() + 120)
            return {'id': self.config['id'], 'request_id': incoming['request_id'], 'self': self.records.records.get(self.config['id']), 'records': list(self.records.records.values()), 'record_ages': self.records.ages(), 'credentials': self.secrets, 'reserved': accepted}

    def contact(self, address, expected=None, reserve=False):
        address = private_ip(address)
        with self.lock:
            if self.local is None:
                return None
            request_id = uuid.uuid4().hex
            body = {'request_id': request_id, 'self': self.records.records[self.config['id']], 'records': list(self.records.records.values()), 'record_ages': self.records.ages(), 'credentials': dict(self.secrets), 'reserve': reserve}
        reply, elapsed = self.rpc(address, 'exchange', body, expected, timeout=8)
        with self.lock:
            if reply.get('request_id') != request_id or (expected and reply.get('id') != expected):
                raise ValueError('Wrong peer or reply')
            sender = self.protocol.verify(reply['self'])
            if sender['id'] != reply['id']:
                raise ValueError('Wrong origin')
            self.merge_peer_records(reply.get('records', [])[:128], reply['record_ages'], elapsed)
            self.peers[sender['id'], address] = time.monotonic()
            for key, secret in list(reply.get('credentials', {}).items())[:256]:
                self.remember_secret(key, secret)
            return reply

    def rpc(self, address, purpose, payload, expected=None, timeout=8):
        """Fresh receiver challenge plus request-bound reply; no shared clock."""
        if purpose == 'command' and (not allows(self, 'wifi')):
            raise ValueError('Wi-Fi service channel disabled')
        address = private_ip(address)
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        hello = self.protocol.sign({'id': self.config['id'], 'boot': self.boot, 'seq': self.seq, 'purpose': purpose, 'request_id': request_id})
        request = urllib.request.Request(f'http://{address}:{PORT}/challenge', encode(hello), {'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=min(4, timeout)) as response:
            granted = self.protocol.verify(json.loads(response.read(16384)))
        if granted.get('request_id') != request_id or granted.get('purpose') != purpose or (expected and granted['id'] != expected):
            raise ValueError('Wrong challenge reply')
        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            raise ValueError('Local request deadline exceeded')
        body = {**payload, '_challenge': granted['challenge'], '_source_id': self.config['id'], '_request_id': request_id, '_prepared_elapsed_s': elapsed}
        raw = self.protocol.seal(body)
        request = urllib.request.Request(f'http://{address}:{PORT}/{purpose}', raw, {'Content-Type': 'application/octet-stream'})
        with self.opener.open(request, timeout=max(0.1, timeout - elapsed)) as response:
            reply = self.protocol.open(response.read(MAX_PACKET + 1))
        elapsed = time.monotonic() - started
        if elapsed >= timeout or reply.get('_request_id') != request_id or reply.get('_source_id') != granted['id']:
            raise ValueError('Expired or wrong request reply')
        return (reply, elapsed)

    def accept_rpc(self, data, purpose):
        elapsed = self.challenges.elapsed(data.get('_challenge'), purpose, data.get('_source_id'))
        return elapsed + age(data.get('_prepared_elapsed_s'))

    def discovery_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', DISCOVERY))
        while True:
            try:
                raw, source = sock.recvfrom(60000)
                record = json.loads(raw)
                with self.lock:
                    node = self.protocol.verify(record)
                    if node['id'] != self.config['id']:
                        self.peers[node['id'], private_ip(source[0])] = time.monotonic()
            except Exception:
                pass

    def publish(self, local):
        with self.lock:
            self.seq += 1
            local.update(boot=self.boot, seq=self.seq, issued=time.time())
            self.local = local
            record = self.protocol.sign(local)
            self.records.merge([record])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for address in local['broadcasts']:
                try:
                    sock.sendto(encode(record), (address, DISCOVERY))
                except OSError:
                    pass

    def gossip(self):
        now = time.monotonic()
        with self.lock:
            self.peers = {k: t for k, t in self.peers.items() if now - t < 180}
            targets = dict(self.peers)
            for peer in self.config.get('peers', []):
                if peer['id'] != self.config['id']:
                    for address in peer.get('addresses', []):
                        targets.setdefault((peer['id'], address), 0)
            if self.local.get('gateway'):
                targets.setdefault((None, self.local['gateway']), 0)

        def poll(item):
            ident, address = item
            try:
                self.contact(address, ident)
            except Exception:
                pass
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(poll, list(targets)[:128]))

    def route(self, local, nodes):
        local.update(parent=None, path=[], cost=10000)
        if not local['internet']['ok']:
            return
        parent = next((n for n in nodes if local['uplink'] == 'wifi' and n.get('ap', {}).get('bssid') == local['bssid']), None)
        if parent:
            if policy.valid_path(parent, local['id']):
                local.update(parent=parent['id'], path=[local['id']] + parent['path'], cost=policy.radio_cost(local['signal'], local['freq']) + 25 + parent['cost'])
        elif not local['ssid'].upper().startswith('BMI30') or local['uplink'] == 'ethernet':
            local.update(path=[local['id']], cost=policy.internet_cost(local['internet']) + (policy.radio_cost(local['signal'], local['freq']) if local['uplink'] == 'wifi' else 5))

    def router_secret(self, ssid):
        """Prefer a peer's confirmed Internet credential to an untested saved copy."""
        saved = self.profile_cache.get(ssid)
        shared = self.secrets.get('wifi:' + ssid)
        local = self.local or {}
        if saved and local.get('ssid') == ssid and (local.get('uplink') == 'wifi') and local.get('internet', {}).get('ok') and (saved.get('uuid') == local.get('station_uuid')):
            return saved
        if shared:
            return shared
        if saved:
            return saved
        raise RuntimeError('Router credentials are unavailable')

    def execute(self, choice):
        with self.lock:
            self.switching = True
        started = time.time()
        success = False
        error = ''
        try:
            if choice['kind'] == 'relay':
                parent = choice['parent']
                secret = self.secrets['ap:' + parent]
                peer = next((n for n in self.records.nodes() if n['id'] == parent))
                addresses = [a for p, a in self.peers if p == parent]
                if addresses:
                    reply = self.contact(addresses[0], parent, reserve=True)
                    if not reply['reserved']:
                        raise RuntimeError('Parent is changing its route')

                def verify(gateway):
                    reply = self.contact(gateway, parent, reserve=True)
                    if not reply['reserved'] or not policy.valid_path(reply['self']['data'], self.config['id']):
                        raise RuntimeError('Parent path is no longer valid')
                health = self.network.activate(choice, secret, verify)
            else:
                secret = self.router_secret(choice['ssid'])
                health = self.network.activate(choice, secret)
            success = True
            LOG.info('Route switched: %s %s %s MHz', choice['kind'], choice.get('parent'), choice['freq'])
        except Exception as exc:
            error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            LOG.warning('Route candidate failed; previous route restored: %s', error)
        finally:
            self.decision.completed(choice, time.monotonic(), success)
            with self.lock:
                self.switching = False
                self.history = (self.history + [{'at': started, 'kind': choice['kind'], 'parent': choice.get('parent'), 'bssid': choice['bssid'], 'ok': success, 'error': error}])[-30:]
        return success

    def visit_peer(self, local):
        """Authenticate a visible node outside our LAN, then restore the uplink.

        Wi-Fi scans only reveal beacons, never another network's IP topology.
        A brief association is needed on this single radio. Active AP clients
        and native RDP sessions suppress these exploratory associations.
        """
        if local['clients'] or self.network.remote_desktop_active():
            return False
        known = {p['ap_ssid']: p for p in self.config.get('peers', []) if p['id'] != local['id']}
        for ident, value in self.secrets.items():
            if ident.startswith('ap:') and value['origin'] != local['id']:
                known[value['ssid']] = {'id': value['origin'], 'ap_ssid': value['ssid'], 'psk': value['psk']}
        nodes = {n['id'] for n in self.records.nodes()}
        aps = [a for a in local['visible'] if a['ssid'] in known and known[a['ssid']]['id'] not in nodes and (time.monotonic() - self.last_visit.get(a['bssid'], 0) > 300)]
        if not aps:
            return False
        aps.sort(key=lambda a: (known[a['ssid']].get('role') != 'master', known[a['ssid']].get('rs485_id', 255), -a['signal']))
        choice = aps[0]
        peer = known[choice['ssid']]
        self.last_visit[choice['bssid']] = time.monotonic()

        def verify(gateway):
            self.contact(gateway, peer['id'])
        with self.lock:
            self.switching = True
        try:
            self.network.activate(choice, {'psk': peer['psk']}, verify, commit=False, require_internet=False)
            LOG.info('Discovered adjacent network through %s; previous uplink restored', peer['id'])
        except Exception as exc:
            LOG.warning('Adjacent network visit failed: %s', type(exc).__name__)
        finally:
            with self.lock:
                self.switching = False
        return True

    def tick(self):
        now = time.monotonic()
        permissions = self.network.permissions()
        local = self.network.snapshot()
        local['internet'] = network.probe(local['iface'])
        if now - self.last_profiles > 60 or not self.profile_cache or getattr(self, 'profile_station_uuid', None) != local.get('station_uuid'):
            self.profile_cache = self.network.profiles()
            self.last_profiles = now
            self.profile_station_uuid = local.get('station_uuid')
        rescan = now - self.last_scan > 65 and (not local['clients']) and (not access_policy.maintenance())
        local['visible'] = self.network.visible(rescan=rescan and permissions.get('wifi', True))
        active_ap = next((a for a in local['visible'] if a['bssid'] == local['bssid']), None)
        if active_ap:
            local['signal'] = active_ap['signal']
        if rescan:
            self.last_scan = now
        ap_psk = network.nm_value(local['ap_uuid'], '802-11-wireless-security.psk', secrets=True)
        desktop_active = self.network.remote_desktop_active()
        with self.lock:
            nodes = self.records.nodes()
            self.route(local, nodes)
            if 8 <= len(ap_psk) <= 64:
                self.own_secret('ap:' + local['id'], {'ssid': local['ap']['ssid'], 'psk': ap_psk, 'security': 'wpa-psk'})
            if local['uplink'] == 'wifi' and local['path'] == [local['id']] and (local['ssid'] in self.profile_cache) and (not access_policy.forgotten(local['ssid'])):
                self.own_secret('wifi:' + local['ssid'], self.profile_cache[local['ssid']])
        self.publish(local)
        self.gossip()
        with self.lock:
            nodes = self.records.nodes()
            profiles = {s['ssid']: s for k, s in self.secrets.items() if k.startswith('wifi:')}
            profiles.update(self.profile_cache)
            profiles = {ssid: self.router_secret(ssid) for ssid in profiles}
            profiles = {k: v for k, v in profiles.items() if not access_policy.forgotten(k)}
            blocked = [k for k in self.secrets if k.startswith('wifi:') and access_policy.forgotten(k[5:])]
            for k in blocked:
                self.secrets.pop(k, None)
            if blocked:
                network.atomic(self.secrets_path, json.dumps(self.secrets))
            choices = policy.candidates(local, nodes, local['visible'], profiles)
            self.offline = 0 if local['internet']['ok'] else self.offline + 1
            choice = self.decision.choose(local, choices, now)
            status = 'online' if local['internet']['ok'] else 'offline'
            can_switch = self.config.get('automatic', False) and permissions.get('wifi', True)
            can_switch = can_switch and now >= self.hold_until and (now - self.session_start > 90)
            if local['internet']['ok'] and desktop_active:
                can_switch = False
            if local.get('clients', 0) or access_policy.offline_mode() or access_policy.maintenance():
                can_switch = False
            if self.config.get('observe', False):
                can_switch = False
            self.state = {'version': VERSION, 'status': status, 'checked_at': time.time(), 'checked_mono': time.monotonic(), 'clock_mode': 'monotonic-v2', 'bluetooth': self.bluetooth.snapshot(), 'automatic': can_switch, 'coordinator': policy.coordinator(nodes), 'local': local, 'nodes': nodes, 'candidates': choices, 'history': self.history, 'note': 'Estimated route cost; 5 GHz preferred at usable signal; up to 4 nodes per route.'}
            network.atomic('/run/bmi30-network/status.json', json.dumps(self.state), 420)
        if self.legacy_recovery.active.maybe_visit(local):
            return
        if choice and can_switch:
            self.execute(choice)
        elif can_switch and self.offline >= 3:
            self.visit_peer(local)

    def merge_peer_records(self, records, ages=None, transit=0):
        records = [r for r in records if isinstance(r, dict) and isinstance(r.get('data'), dict) and (r['data'].get('id') != self.config['id'])]
        self.records.merge(records, ages, transit)

class NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, *args, **kwargs):
        return None

def serve(agent, bind='0.0.0.0', port=PORT, ready=None):

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, *_args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(6)

        def do_GET(self):
            if self.path in ('/live', '/peer-live'):
                local = self.path == '/live'
                if local:
                    if self.client_address[0] != '127.0.0.1' or self.headers.get('X-BMI30-Local') != '1':
                        self.send_error(403)
                        return
                else:
                    try:
                        if not allows(agent, 'wifi'):
                            raise ValueError('Wi-Fi service channel disabled')
                        token = self.headers.get('X-BMI30-Stream', '')
                        if len(token) > 4096:
                            raise ValueError('Token exceeds limit')
                        request = agent.protocol.verify(json.loads(base64.b64decode(token, validate=True)))
                        if request.get('purpose') != 'group-stream' or len(request.get('request_id', '')) != 32:
                            raise ValueError('Invalid stream request')
                    except Exception:
                        self.send_error(403)
                        return
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.connection.settimeout(2)
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                revision = request.get('revision', 0) if not local and request.get('stream_boot') == agent.group.boot else 0
                if type(revision) is not int or not 0 <= revision <= agent.group.hub.seq:
                    revision = 0
                stream_until = time.monotonic() + 2
                try:
                    while True:
                        if not local and (time.monotonic() >= stream_until or not allows(agent, 'wifi')):
                            break
                        events = agent.group.hub.after(revision)
                        for event in events:
                            value = {k: v for k, v in event.items() if k != 'packet'} if local else agent.group.packet(request['request_id'], snapshot=event.get('packet'), revision=event['revision'])
                            if value:
                                self.wfile.write(encode(value) + b'\n')
                                self.wfile.flush()
                            revision = event['revision']
                except (OSError, ValueError):
                    pass
                return
            if self.path == '/status':
                with agent.lock:
                    raw = encode({**agent.state, 'bluetooth': agent.bluetooth.snapshot(), 'service_transports': agent.group.transport_status(), 'legacy_recovery': agent.legacy_recovery.snapshot()})
                content = 'application/json'
            elif self.path == '/':
                raw = Path(__file__).with_name('status.html').read_bytes()
                content = 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path == '/local-service-transports':
                if self.client_address[0] != '127.0.0.1' or self.headers.get('X-BMI30-Local') != '1':
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 1 <= length <= 1024:
                        raise ValueError('Invalid request size')
                    body = json.loads(self.rfile.read(length))
                    result = agent.group.set_transports(body.get('mode'), body.get('revision'))
                    reply = {'ok': True, 'service_transports': result}
                except (ValueError, TypeError, AttributeError):
                    reply = {'ok': False, 'error': 'Selection changed or invalid; refresh before retrying', 'service_transports': agent.group.transport_status()}
                except OSError:
                    reply = {'ok': False, 'error': 'Cannot save service channel selection', 'service_transports': agent.group.transport_status()}
                raw = encode(reply)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if self.path == '/challenge':
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 1 <= length <= 8192:
                        raise ValueError('Invalid challenge size')
                    hello = agent.protocol.verify(json.loads(self.rfile.read(length)))
                    purpose = hello.get('purpose')
                    if purpose == 'command' and (not allows(agent, 'wifi')):
                        raise ValueError('Wi-Fi service channel disabled')
                    if purpose not in ('exchange', 'command') or len(hello.get('request_id', '')) != 32:
                        raise ValueError('Invalid challenge request')
                    token = agent.challenges.issue(purpose, 8, hello['id'])
                    raw = encode(agent.protocol.sign({'id': agent.config['id'], 'boot': agent.boot, 'seq': agent.seq, 'purpose': purpose, 'request_id': hello['request_id'], 'challenge': token}))
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except Exception:
                    self.send_error(403)
                return
            if self.path in ('/command', '/local-command'):
                local = self.path == '/local-command'
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 1 <= length <= 16384:
                        raise ValueError('Invalid length')
                    if local and (self.client_address[0] != '127.0.0.1' or self.headers.get('X-BMI30-Local') != '1'):
                        raise ValueError('Local portal access required')
                    raw = self.rfile.read(length)
                    if local:
                        data = json.loads(raw)
                        data = {'target': data['target'], 'action': data['action'], 'params': data['params'], 'command_id': uuid.uuid4().hex, 'age_s': 0, 'via': []}
                        transit = 0
                    else:
                        with agent.lock:
                            data = agent.protocol.open(raw)
                        transit = agent.accept_rpc(data, 'command')
                    try:
                        reply = agent.group.command(data, transit, ingress=None if local else 'wifi')
                    except Exception as exc:
                        reply = {'ok': False, 'command_id': data.get('command_id'), 'target': data.get('target'), 'error': str(exc)[:240] if isinstance(exc, ValueError) else 'Channel unavailable; refresh state before retrying'}
                    with agent.lock:
                        if not local:
                            reply = {**reply, '_request_id': data['_request_id'], '_source_id': agent.config['id']}
                        raw = encode(reply) if local else agent.protocol.seal(reply)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json' if local else 'application/octet-stream')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except Exception:
                    self.send_error(403)
                return
            if self.path != '/exchange':
                self.send_error(404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= MAX_PACKET:
                    raise ValueError('Invalid length')
                raw = self.rfile.read(length)
                try:
                    with agent.lock:
                        agent.legacy_recovery.check_replay(raw)
                        data = agent.protocol.open(raw)
                except Exception:
                    if not allows(agent, 'wifi'):
                        raise ValueError('Wi-Fi service channel disabled')
                    raw = agent.legacy_recovery.respond(raw)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                transit = agent.accept_rpc(data, 'exchange')
                reply = agent.exchange(data, self.client_address[0], transit)
                with agent.lock:
                    reply = {**reply, '_request_id': data['_request_id'], '_source_id': agent.config['id']}
                    raw = agent.protocol.seal(reply)
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                self.send_error(403)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        slots = threading.BoundedSemaphore(24)

        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            super().process_request(request, client_address)

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()
    server = Server((bind, port), Handler)
    if ready:
        ready(server)
    server.serve_forever()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/bmi30-network/config.json')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    agent = Agent(json.loads(Path(args.config).read_text()))
    if args.once:
        agent.tick()
        print(json.dumps(agent.state))
        return
    threading.Thread(target=serve, args=(agent,), daemon=True).start()
    threading.Thread(target=agent.discovery_loop, daemon=True).start()
    threading.Thread(target=agent.group.run, daemon=True).start()
    threading.Thread(target=agent.bluetooth.run, daemon=True, name='bmi30-bluez').start()
    while True:
        try:
            agent.tick()
        except Exception as exc:
            LOG.error('Network observation failed: %s', type(exc).__name__)
        time.sleep(15 + random.uniform(0, 3))
if __name__ == '__main__':
    main()
