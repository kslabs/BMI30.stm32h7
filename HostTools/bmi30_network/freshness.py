"""Local elapsed-time leases and sequence history; never uses calendar time."""
import math
import threading
import time
import uuid


class ChallengeError(ValueError):
    """A stale nonce invalidates one message, not its live transport."""


def age(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid relative age')
    return float(value)


class Challenges:
    """Receiver-issued nonce: even a first-ever packet needs a live exchange."""
    def __init__(self):
        self.pending = {}
        self.lock = threading.RLock()

    def issue(self, purpose, lifetime, peer=None):
        now = time.monotonic()
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if now < v[1]}
            if len(self.pending) >= 1024:
                raise ValueError('Challenge capacity exceeded')
            token = uuid.uuid4().hex
            self.pending[token] = (now, now + lifetime, purpose, peer)
            return token

    def elapsed(self, token, purpose, peer=None, consume=True):
        with self.lock:
            value = self.pending.get(token)
            now = time.monotonic()
            if not value or now >= value[1] or value[2] != purpose or (value[3] is not None and value[3] != peer):
                raise ChallengeError('Missing, expired or wrong challenge')
            if consume:
                del self.pending[token]
            return now - value[0]


class Receipts:
    """A duplicate cannot extend a record's deadline, even after expiration."""
    def __init__(self, lifetime, limit=128):
        self.lifetime, self.limit = lifetime, limit
        self.records, self.deadlines, self.history = {}, {}, {}

    def put(self, record, relative_age=0):
        relative_age = age(relative_age)
        data = record['data']
        ident, boot, seq = data['id'], data['boot'], data['seq']
        old = self.history.get(ident)
        if relative_age >= self.lifetime:
            return False
        if old:
            if boot in old['retired'] or (boot == old['boot'] and seq <= old['seq']):
                return False
            if boot != old['boot']:
                if len(old['retired']) >= 128:
                    raise ValueError('Boot history capacity exceeded')
                old['retired'].add(old['boot'])
        else:
            if len(self.history) >= self.limit:
                raise ValueError('Identity capacity exceeded')
            old = {'retired': set()}
        old.update(boot=boot, seq=seq)
        self.history[ident] = old
        self.records[ident] = record
        self.deadlines[ident] = time.monotonic() + self.lifetime - relative_age
        return True

    def fresh(self, ident):
        return ident in self.records and time.monotonic() < self.deadlines.get(ident, -1)

    def age(self, ident):
        return max(0, self.lifetime - (self.deadlines.get(ident, -1) - time.monotonic()))

    def prune(self):
        for ident in list(self.records):
            if not self.fresh(ident):
                del self.records[ident]
                self.deadlines.pop(ident, None)

    def ages(self):
        return {ident: self.age(ident) for ident in self.records if self.fresh(ident)}
