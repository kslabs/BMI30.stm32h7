"""Fleet-authenticated discovery and AES-256-GCM private exchanges.

The installation key is provisioned over already authenticated SSH, never
derived from SSID, hostname, serial number or a public factory password.
"""
import base64
import hashlib
import hmac
import json
import math
import os
import re
import time
import threading
from freshness import Receipts, age

MAX_NODES = 128
MAX_PACKET = 512 * 1024
ID = re.compile(r"BMI30-[A-Z0-9-]{1,50}\Z")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class Protocol:
    def __init__(self, key):
        self.key = bytes.fromhex(key)
        if len(self.key) != 32:
            raise ValueError("Fleet key must contain 256 random bits")
        self.replays = {}
        self.lock = threading.RLock()

    def sign(self, data):
        raw = encode(data)
        return {"version": 2, "data": data, "mac": hmac.new(self.key, b"discovery-v2" + raw, hashlib.sha256).hexdigest()}

    def verify(self, record, now=None):
        data = record["data"]
        if record.get('version') != 2 or not hmac.compare_digest(self.sign(data)["mac"], str(record["mac"])):
            raise ValueError("Invalid authentication")
        if not isinstance(data.get('id'), str) or not ID.fullmatch(data['id']):
            raise ValueError("Invalid identity")
        if type(data.get('seq')) is not int or data['seq'] < 0 or not isinstance(data.get('boot'), str) or not 1 <= len(data['boot']) <= 64:
            raise ValueError("Invalid sequence")
        if not math.isfinite(data.get("cost", 10000)) or len(data.get("path", [])) > 4:
            raise ValueError("Invalid path")
        if len(data.get("visible", [])) > 256 or len(data.get("addresses", [])) > 16:
            raise ValueError("Record exceeds limits")
        return data

    def seal(self, data):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        body = encode({"version": 2, "payload": data})
        return base64.b64encode(nonce + AESGCM(self.key).encrypt(nonce, body, b"bmi30-network-v2"))

    def open(self, raw, *, track_replay=True):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if len(raw) > MAX_PACKET:
            raise ValueError("Packet too large")
        binary = base64.b64decode(raw, validate=True)
        if len(binary) < 29:
            raise ValueError("Short packet")
        nonce, body = binary[:12], binary[12:]
        data = json.loads(AESGCM(self.key).decrypt(nonce, body, b"bmi30-network-v2"))
        if data.get('version') != 2:
            raise ValueError('Incompatible packet')
        # A live Bluetooth stream has a nonce-bound session and a strict
        # directional counter. Its frames must not consume the HTTP datagram
        # replay window at detector-event rates.
        if not track_replay:
            return data['payload']
        now = time.monotonic()
        with self.lock:
            if nonce in self.replays:
                raise ValueError("Replayed or incompatible packet")
            self.replays = {n: expiry for n, expiry in self.replays.items() if expiry > now}
            if len(self.replays) > 4096:
                raise ValueError("Replay window capacity exceeded")
            self.replays[nonce] = now + 110
        return data["payload"]


class Records:
    def __init__(self, protocol):
        self.protocol = protocol
        self.receipts = Receipts(100, MAX_NODES)
        self.records = self.receipts.records

    def merge(self, records, ages=None, transit=0):
        if len(records) > MAX_NODES:
            raise ValueError("Too many devices")
        transit = age(transit)
        self.receipts.prune()
        for record in records:
            try:
                data = self.protocol.verify(record)
                if ages is not None and data['id'] not in ages:
                    continue
                relative_age = age((ages or {}).get(data['id'], 0)) + transit
                self.receipts.put(record, relative_age)
            except (ValueError, KeyError, TypeError):
                continue

    def ages(self):
        return self.receipts.ages()

    def nodes(self):
        self.merge([])
        return [{**r['data'],'age_s':self.receipts.age(ident)} for ident,r in self.records.items()]
