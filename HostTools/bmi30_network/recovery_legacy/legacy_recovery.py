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
DONOR_IDS = frozenset(('BMI30-05AB38E6A', 'BMI30-D5507EDFC', 'BMI30-7AA5CED27'))
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
