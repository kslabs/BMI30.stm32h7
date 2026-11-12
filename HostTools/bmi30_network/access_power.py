"""Bounded A/B transmit-power trial, only for an isolated single-client AP.

RSSI is measured from the client, not at the client's receiver. Strong RSSI
alone is never evidence of receiver overload. Keep firmware power control unless
strong RSSI AND sustained transmit failures warrant a reversible trial.
"""
class PowerTrial:
    def __init__(self, apply_limit, restore, clock):
        self.apply_limit, self.restore, self.clock = apply_limit, restore, clock
        self.previous = None
        self.active = False
        self.disabled = False
        self.until = self.next_trial = 0
        self.baseline = 0
        self.strong_failures = 0
        self.status = 'firmware-auto'

    def reset(self, reason):
        if self.active:
            if not self.restore():
                self.disabled = True
                self.status = 'restore-failed'
                return self.status
        self.active = False
        self.previous = None
        self.strong_failures = 0
        self.status = reason
        return reason

    def sample(self, stations, uplink, enabled=True, current_dbm=20):
        now = self.clock()
        if not enabled or uplink or len(stations) != 1:
            return self.reset('shared-radio' if uplink else 'firmware-auto')
        if self.disabled:
            return self.status
        if not self.active and not 8 <= current_dbm <= 23:
            self.status = 'unreliable-driver-power'
            return self.status
        s = stations[0]
        key = (s['mac'], s.get('connected', 0))
        if s['signal'] < -55:
            return self.reset('firmware-auto')
        previous = self.previous
        self.previous = (key, s['packets'], s['failed'])
        if previous is None or previous[0][0] != key[0] or key[1] < previous[0][1]:
            return self.reset('firmware-auto') if self.active else self.status
        packets, failures = s['packets'] - previous[1], s['failed'] - previous[2]
        if packets < 0 or failures < 0:
            return self.reset('counter-reset')
        rate = failures / max(1, packets + failures)
        if self.active:
            if packets + failures < 20 or rate > self.baseline * .7:
                self.next_trial = now + 600
                return self.reset('trial-reverted')
            if now >= self.until:
                self.next_trial = now + 600
                return self.reset('trial-complete')
            self.status = 'limited-trial'
            return self.status
        bad = s['signal'] >= -35 and packets + failures >= 20 and rate >= .1
        self.strong_failures = self.strong_failures + 1 if bad else 0
        if self.strong_failures >= 2 and now >= self.next_trial:
            limit = max(8, int(current_dbm) - 3)
            self.next_trial = now + 600
            if not self.apply_limit(limit):
                self.restore()
                self.disabled = True
                self.status = 'driver-unsupported'
                return self.status
            self.active, self.until, self.baseline = True, now + 90, rate
            self.status = 'limited-trial'
        return self.status
