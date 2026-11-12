#!/usr/bin/env python3
"""Independent local access supervisor. No detector, USB, GPIO or reboot APIs."""
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import select
import signal
import socket
import struct
import subprocess
import threading
import time
import urllib.request
import uuid

import access_policy
from access_power import PowerTrial

STATE = Path('/var/lib/bmi30-access')
RUNTIME = Path('/run/bmi30-access')
CONFIG = Path('/home/techaid/Documents/host/bmi30_config.json')
def retained_ap_cidr():
    import ipaddress
    for path,key in ((Path('/var/lib/bmi30-access/retained-ap.json'),'cidr'),
                     (Path('/etc/bmi30-network/config.json'),'ap_address')):
        try:
            value=ipaddress.IPv4Interface(json.loads(path.read_text())[key])
            if value.ip.is_private:return str(value)
        except (OSError,ValueError,KeyError,TypeError):pass
    return '10.42.0.1/24'

AP_CIDR = retained_ap_cidr()
AP_IP = AP_CIDR.split('/')[0]

LOG = logging.getLogger('bmi30-access')

def atomic(path, value, mode=0o600):
    path = Path(path)
    original = path.stat() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.access-new')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    temp.chmod(mode)
    if original is not None:
        os.chown(temp, original.st_uid, original.st_gid)
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def claim_busy(path):
    """Claim without replacing a maintenance owner; return a unique file token."""
    path = Path(path)
    token = uuid.uuid4().hex
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return None
    info = os.fstat(fd)
    ownership = (token, info.st_dev, info.st_ino)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'started': time.time(), 'owner': 'bmi30-access', 'token': token}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        # A failed write did not consume the queued job. Remove only the inode
        # we just created; an externally replaced maintenance file survives.
        try:
            current = path.stat()
            if (current.st_dev, current.st_ino) == ownership[1:]:
                path.unlink()
        except OSError:
            pass
        raise
    return ownership


def release_busy(path, ownership):
    """Never release another maintenance owner's token, even after replacement."""
    path = Path(path)
    try:
        with path.open('r', encoding='utf-8') as stream:
            value = json.load(stream)
            info = os.fstat(stream.fileno())
        current = path.stat()
        if (isinstance(value, dict) and value.get('token') == ownership[0]
                and (info.st_dev, info.st_ino) == ownership[1:]
                and (current.st_dev, current.st_ino) == ownership[1:]):
            path.unlink()
            return True
    except (OSError, ValueError):
        pass
    return False


def run(*args, timeout=12, check=True):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, 'LC_ALL': 'C'})
    if check and r.returncode:
        raise RuntimeError(args[0] + ' failed')
    return r.stdout.strip()

def nm(uuid_value, name):
    return run('nmcli', '-g', name, 'connection', 'show', 'uuid', uuid_value)

def valid_uuid(value):
    return isinstance(value, str) and str(uuid.UUID(value)) == value.lower()

def profile_list():
    result = []
    for ident in run('nmcli', '-g', 'UUID', 'connection', 'show').splitlines():
        if not valid_uuid(ident): continue
        kind = nm(ident, 'connection.type')
        if kind not in ('wifi', '802-11-wireless'): continue
        mode = nm(ident, '802-11-wireless.mode')
        ssid = nm(ident, '802-11-wireless.ssid')
        result.append({'uuid': ident, 'mode': mode, 'ssid': ssid,
                       'iface': nm(ident, 'connection.interface-name')})
    return result

def active_uuid(iface):
    value = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', iface, check=False)
    return value if value and value != '--' else ''

def http_ready():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1/login', timeout=3) as r:
            return r.status == 200 and b'BMI30' in r.read(4096)
    except Exception:
        return False

def dns_ready():
    try:
        ident = os.urandom(2)
        query = ident + b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
        query += b'\x05bmi30\x05local\x00\x00\x01\x00\x01'
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(1)
            s.sendto(query, (AP_IP, 53))
            data, sender = s.recvfrom(2048)
        return (sender[0] == AP_IP and data[:2] == ident and len(data) >= 12
                and (data[3] & 15) == 0 and data[6:8] != b'\x00\x00'
                and socket.inet_aton(AP_IP) in data[12:])
    except OSError:
        return False

def dhcp_running():
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            cmd = (proc / 'cmdline').read_bytes().split(b'\0')
            if (cmd and cmd[0].endswith(b'dnsmasq')
                    and (b'--interface=wlan0ap' in cmd or any(b'nm-dnsmasq-wlan0ap' in x for x in cmd))):
                return True
        except OSError:
            continue
    return False

def station_metrics(text):
    rows = []
    for block in re.split(r'(?=Station )', text):
        mac = re.search(r'^Station ([0-9a-f:]{17})', block, re.I)
        if not mac: continue
        def integer(name, default=0):
            m = re.search(r'^\s*' + name + r':\s*(-?\d+)', block, re.M)
            return int(m[1]) if m else default
        rows.append({'mac': mac[1].lower(), 'signal': integer('signal', -100),
                     'packets': integer('tx packets'), 'failed': integer('tx failed'),
                     'connected': integer('connected time')})
    return rows

class Button:
    def __init__(self, callback, clock=time.monotonic):
        self.callback, self.clock = callback, clock
        self.pressed = None
        self.last = -100

    def event(self, kind, code, value):
        if kind != 1 or code != 116: return
        now = self.clock()
        if value == 1 and self.pressed is None:
            self.pressed = now
        elif value == 0 and self.pressed is not None:
            duration, self.pressed = now - self.pressed, None
            if .04 <= duration <= 1.5 and now - self.last >= 10:
                self.last = now
                try: self.callback()
                except ValueError: pass  # Already queued; keep watching the button.

def button_loop(callback):
    # Exact hardware device name: never read a keyboard, HDMI device or STM32.
    event = next((p for p in Path('/sys/class/input').glob('event*')
                  if (p/'device/name').read_text().strip() == 'pwr_button'), None)
    if event is None:
        LOG.warning('Power button input unavailable')
        return
    fmt = struct.Struct('@llHHi')
    decoder = Button(callback)
    with open('/dev/input/' + event.name, 'rb', buffering=0) as stream:
        fcntl.ioctl(stream.fileno(), 0x40044590, 1)  # EVIOCGRAB
        while True:
            if select.select([stream], [], [], 1)[0]:
                data = stream.read(fmt.size)
                if len(data) != fmt.size: return
                _, _, kind, code, value = fmt.unpack(data)
                decoder.event(kind, code, value)

class Supervisor:
    def __init__(self):
        self.lock = threading.RLock()
        self.pending = None
        self.failures = {'ap': 0, 'portal': 0}
        self.next_repair = 0
        self.status = {'status': 'starting'}
        self.power = PowerTrial(self.limit_power, self.restore_power, time.monotonic)
        self.last_action = None

    def save(self, changes):
        data = access_policy.preferences()
        data.update(changes)
        atomic(access_policy.SETTINGS, data)
        return data

    def limit_power(self, dbm):
        try:
            run('iw', 'dev', 'wlan0ap', 'set', 'txpower', 'limit', str(dbm * 100))
            info = run('iw', 'dev', 'wlan0ap', 'info')
            m = re.search(r'txpower ([\d.]+)', info)
            return bool(m and float(m[1]) <= dbm + .5)
        except Exception:
            return False

    def restore_power(self):
        try:
            run('iw', 'dev', 'wlan0ap', 'set', 'txpower', 'auto')
            return True
        except Exception:
            return False

    def queue(self, action, value=None):
        with self.lock:
            if self.pending is not None or (RUNTIME/'busy').exists():
                raise ValueError('Восстановление уже выполняется. Подождите.')
            if action not in ('recover', 'forget', 'offline', 'resume', 'power'):
                raise ValueError('Неизвестное действие')
            if action == 'forget':
                if not isinstance(value, str) or not value or len(value.encode()) > 32:
                    raise ValueError('Выберите сохранённую сеть роутера')
                available = {p['ssid'] for p in profile_list() if p['mode'] == 'infrastructure'
                             and p['iface'] in ('wlan0', '--', '') and not p['ssid'].upper().startswith('BMI30')}
                if value not in available: raise ValueError('Сохранённая сеть не найдена')
            if action == 'power' and type(value) is not bool:
                raise ValueError('Ожидается переключатель мощности')
            self.pending = (time.monotonic()+2, action, value)

    def repair_ap(self):
        allowed = json.loads(CONFIG.read_text()).get('channel_permissions', {})
        if not allowed.get('hotspot', True): return
        profiles = [p for p in profile_list() if p['mode'] == 'ap' and p['iface'] == 'wlan0ap']
        if not profiles: raise RuntimeError('No saved AP profile; credentials preserved')
        current = active_uuid('wlan0ap')
        profile = next((p for p in profiles if p['uuid'] == current), profiles[0])
        link = run('iw', 'dev', 'wlan0', 'link', check=False)
        freq = re.search(r'freq: (\d+)', link)
        channel = (int(freq[1])-5000)//5 if freq and int(freq[1]) >= 5000 else ((int(freq[1])-2407)//5 if freq else 6)
        band = 'a' if freq and int(freq[1]) >= 5000 else 'bg'
        if not Path('/sys/class/net/wlan0ap').exists():
            run('iw', 'dev', 'wlan0', 'interface', 'add', 'wlan0ap', 'type', '__ap')
        run('nmcli', 'device', 'set', 'wlan0ap', 'managed', 'yes')
        run('nmcli', 'connection', 'modify', 'uuid', profile['uuid'],
            'ipv4.method', 'shared', 'ipv4.addresses', AP_CIDR,
            'ipv6.method', 'disabled', 'wifi.band', band, 'wifi.channel', str(channel),
            'wifi.hidden', 'no', 'wifi-sec.wps-method', 'disabled', 'connection.autoconnect', 'yes')
        run('nmcli', '--wait', '25', 'connection', 'up', 'uuid', profile['uuid'], timeout=30)

    def action(self, action, value):
        self.power.reset('firmware-auto')
        if action == 'power':
            self.save({'adaptive_power': value})
            return
        if action in ('offline', 'forget'):
            changes = {'offline_mode': True}
            if action == 'forget':
                prefs = access_policy.preferences()
                changes['forgotten_ssids'] = sorted(set(prefs.get('forgotten_ssids', [])) | {value})
            self.save(changes)  # Durable block before disconnect/profile deletion.
            run('nmcli', 'device', 'set', 'wlan0', 'autoconnect', 'no')
            run('nmcli', '--wait', '10', 'device', 'disconnect', 'wlan0', check=False, timeout=15)
            if action == 'forget':
                for p in profile_list():
                    if p['mode'] == 'infrastructure' and p['ssid'] == value and p['iface'] in ('wlan0', '--', ''):
                        run('nmcli', 'connection', 'delete', 'uuid', p['uuid'])
                # Purge local replicas; peers cannot re-enrol a locally blocked SSID.
                for filename in ('credentials.json', 'credential-sources.json'):
                    path = Path('/var/lib/bmi30-network')/filename
                    if path.exists():
                        data = json.loads(path.read_text())
                        data.pop('wifi:'+value, None)
                        atomic(path, data)
            self.repair_ap()
        elif action == 'resume':
            self.save({'offline_mode': False})
            run('python3', '/opt/bmi30-network/communication_recovery.py', '--prepare')
            run('nmcli', 'connection', 'reload')
            run('nmcli', 'device', 'set', 'wlan0', 'autoconnect', 'yes')
            # Do not force a channel switch under the connected AP user.
        elif action == 'recover':
            config = json.loads(CONFIG.read_text())
            if config.get('channel_permissions', {}).get('hotspot') is False:
                config.setdefault('channel_permissions', {})['hotspot'] = True
                atomic(CONFIG, config, CONFIG.stat().st_mode & 0o777)
            self.repair_ap()
            if not http_ready():
                run('systemctl', 'restart', 'bmi30-hotspot-info')

    def tick(self):
        now = time.monotonic()
        with self.lock:
            job = self.pending if self.pending and now >= self.pending[0] else None
            ownership = claim_busy(RUNTIME/'busy') if job else None
            if job and ownership is not None:
                self.pending = None
            else:
                job = None
        if job:
            try:
                self.action(job[1], job[2])
                self.last_action = {'action': job[1], 'ok': True, 'time': time.time()}
            except Exception as exc:
                LOG.error('Access action failed: %s', type(exc).__name__)
                self.last_action = {'action': job[1], 'ok': False, 'time': time.time(), 'error': type(exc).__name__}
            finally:
                release_busy(RUNTIME/'busy', ownership)
                self.next_repair = now + 60
        config = json.loads(CONFIG.read_text())
        allowed = config.get('channel_permissions', {}).get('hotspot', True)
        info = run('iw', 'dev', 'wlan0ap', 'info', check=False)
        addresses = run('ip', '-4', '-o', 'addr', 'show', 'dev', 'wlan0ap', check=False)
        ap_ok = bool(re.search(r'\btype AP\b', info) and AP_IP+'/24' in addresses)
        dns_ok = dns_ready() if ap_ok else False
        dhcp_ok = dhcp_running() if ap_ok else False
        web_ok = http_ready()
        for key, good in (('ap', ap_ok and dns_ok and dhcp_ok), ('portal', web_ok)):
            self.failures[key] = 0 if good else self.failures[key]+1
        if allowed and now >= self.next_repair and not self.pending and not (RUNTIME/'busy').exists():
            if self.failures['ap'] >= 3:
                self.queue('recover')
            elif self.failures['portal'] >= 3:
                run('systemctl', 'restart', 'bmi30-hotspot-info')
                self.next_repair = now + 90
        stations = station_metrics(run('iw', 'dev', 'wlan0ap', 'station', 'dump', check=False))
        uplink = 'Connected to ' in run('iw', 'dev', 'wlan0', 'link', check=False)
        tx = re.search(r'txpower ([\d.]+)', info)
        prefs = access_policy.preferences()
        power_status = self.power.sample(stations, uplink, prefs.get('adaptive_power', True), float(tx[1]) if tx else 20)
        ready = allowed and ap_ok and dns_ok and dhcp_ok and web_ok
        self.status = {'version': '0.1.0', 'checked_at': time.time(),
                       'status': 'ready' if ready else ('disabled' if not allowed else 'recovering'),
                       'ap': ap_ok, 'dns': dns_ok, 'dhcp_process': dhcp_ok, 'portal': web_ok,
                       'clients': len(stations), 'offline_mode': prefs.get('offline_mode', False),
                       'power': power_status, 'adaptive_power': prefs.get('adaptive_power', True),
                       'portal_url': 'http://'+AP_IP+'/', 'last_action': self.last_action,
                       'button': 'short-press-recovery', 'pending': bool(self.pending)}
        atomic(RUNTIME/'status.json', self.status, 0o644)

    def public(self):
        result = dict(self.status)
        result['saved_networks'] = sorted({p['ssid'] for p in profile_list() if p['mode'] == 'infrastructure'
                                          and p['iface'] in ('wlan0', '', '--') and not p['ssid'].upper().startswith('BMI30')})
        return result

    def prepare_connect(self, ssid):
        if self.pending is not None or (RUNTIME/'busy').exists():
            raise ValueError('Access action in progress')
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
            raise ValueError('Invalid SSID')
        prefs = access_policy.preferences()
        self.save({'offline_mode': False, 'forgotten_ssids': [s for s in prefs.get('forgotten_ssids', []) if s != ssid]})
        run('python3', '/opt/bmi30-network/communication_recovery.py', '--prepare')
        run('nmcli', 'connection', 'reload')
        run('nmcli', 'device', 'set', 'wlan0', 'autoconnect', 'yes')
        return {'ok': True}

def serve(supervisor, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('X-BMI30-Token', ''), token):
                self.send_error(403); return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096: raise ValueError('Invalid size')
                data = json.loads(self.rfile.read(size))
                if self.path == '/status': result = supervisor.public()
                elif self.path == '/prepare-connect':
                    with supervisor.lock:
                        result = supervisor.prepare_connect(data.get('ssid'))
                elif self.path == '/action':
                    supervisor.queue(data.get('action'), data.get('value'))
                    result = {'ok': True, 'message': 'Выполняется. При изменении сети подключитесь к BMI30 повторно.'}
                else: self.send_error(404); return
                body = json.dumps(result, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
            except (ValueError, TypeError):
                self.send_error(400)
            except Exception:
                self.send_error(503)
    ThreadingHTTPServer(('127.0.0.1', 8793), Handler).serve_forever()

def main():
    logging.basicConfig(level=logging.INFO)
    for p in (STATE, RUNTIME): p.mkdir(parents=True, exist_ok=True)
    if not access_policy.maintenance():
        (RUNTIME/'busy').unlink(missing_ok=True)
    token = os.urandom(32).hex()
    atomic(RUNTIME/'token.json', {'token': token})
    supervisor = Supervisor()
    def stop(*unused):
        supervisor.power.reset('stopped')
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    threading.Thread(target=serve, args=(supervisor, token), daemon=True).start()
    threading.Thread(target=button_loop, args=(lambda: supervisor.queue('recover'),), daemon=True).start()
    while True:
        try: supervisor.tick()
        except Exception as exc: LOG.error('Access check failed: %s', type(exc).__name__)
        time.sleep(10)

if __name__ == '__main__': main()
