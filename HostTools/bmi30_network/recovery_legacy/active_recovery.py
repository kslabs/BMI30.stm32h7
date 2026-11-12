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
