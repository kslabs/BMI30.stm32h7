"""Bluetooth Classic RFCOMM lane for the same authenticated group protocol.

No IP, Internet, pairing agent, acquisition device or calendar clock required.
The controller manages BR/EDR frequency hopping and its adaptive channel map.
"""
import concurrent.futures
import hashlib
import json
import logging
from pathlib import Path
import queue
import re
import socket
import struct
import threading
import time
import uuid

from freshness import Challenges, ChallengeError, age
from protocol import encode, MAX_PACKET, ID

SERVICE_UUID = '69c9d6f4-83d7-4b9e-b30a-7a4c0b448251'
RFCOMM_CHANNEL = 22  # Logical service port, unrelated to the 79 RF channels.
MAX_LINKS = 4
MAC = re.compile(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\Z')
LOG = logging.getLogger('bmi30-bluetooth')


def read_frame(sock):
    deadline = time.monotonic() + 4

    def exact(count):
        result = bytearray()
        while len(result) < count:
            if time.monotonic() >= deadline:
                raise TimeoutError('Bluetooth frame deadline exceeded')
            part = sock.recv(count - len(result))
            if not part:
                raise ConnectionError('Bluetooth link closed')
            result.extend(part)
        return bytes(result)

    length = struct.unpack('!I', exact(4))[0]
    if not 1 <= length <= MAX_PACKET:
        raise ValueError('Bluetooth frame exceeds limit')
    return exact(length)


def write_frame(sock, raw):
    if not 1 <= len(raw) <= MAX_PACKET:
        raise ValueError('Bluetooth frame exceeds limit')
    sock.sendall(struct.pack('!I', len(raw)) + raw)


class Session:
    def __init__(self, manager, sock, address, outgoing=False, expected=None):
        self.manager, self.agent, self.sock = manager, manager.agent, sock
        self.address = address.upper()
        self.endpoint = 'bt:' + self.address
        self.outgoing, self.expected = outgoing, expected
        self.peer = None
        self.stopped = threading.Event()
        self.send_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending = {}
        self.outbox = queue.Queue(maxsize=128)
        self.sent = self.received = 0
        self.subscriber = None
        self.revision = 0
        self.session_id = None

    def handshake(self):
        self.sock.settimeout(3)
        challenges = Challenges()
        token = challenges.issue('bt-session', 8)
        own = self.agent.config['id']
        hello = {'id': own, 'boot': self.agent.boot, 'seq': 0, 'purpose': 'bt-session',
                 'service': SERVICE_UUID, 'nonce': token}
        write_frame(self.sock, encode(self.agent.protocol.sign(hello)))
        remote = self.agent.protocol.verify(json.loads(read_frame(self.sock)))
        self.peer = remote['id']
        if (self.peer == own or (self.expected and self.peer != self.expected)
                or remote.get('purpose') != 'bt-session' or remote.get('service') != SERVICE_UUID
                or not re.fullmatch(r'[0-9a-f]{32}', str(remote.get('nonce', '')))):
            raise ValueError('Wrong Bluetooth service identity')
        proof = {'id': own, 'peer': self.peer, 'echo': remote['nonce'], 'nonce': token}
        write_frame(self.sock, self.agent.protocol.seal(proof))
        reply = self.agent.protocol.open(read_frame(self.sock))
        if reply != {'id': self.peer, 'peer': own, 'echo': token, 'nonce': remote['nonce']}:
            raise ValueError('Wrong Bluetooth session proof')
        challenges.elapsed(token, 'bt-session')
        ordered = sorted(((own, token), (self.peer, remote['nonce'])))
        self.session_id = hashlib.sha256(encode(ordered)).hexdigest()

    def send(self, body):
        with self.send_lock:
            self.sent += 1
            packet = {'session': self.session_id, 'from': self.agent.config['id'], 'to': self.peer,
                      'seq': self.sent, 'body': body}
            write_frame(self.sock, self.agent.protocol.seal(packet))

    def receive(self):
        packet = self.agent.protocol.open(read_frame(self.sock), track_replay=False)
        if (packet.get('session') != self.session_id or packet.get('from') != self.peer
                or packet.get('to') != self.agent.config['id'] or type(packet.get('seq')) is not int
                or packet['seq'] != self.received + 1):
            raise ValueError('Wrong, missing or replayed Bluetooth frame')
        self.received = packet['seq']
        return packet['body']

    def enqueue(self, body):
        try:
            self.outbox.put_nowait(body)
        except queue.Full:
            self.close()
            raise ConnectionError('Bluetooth sender is overloaded')

    def rpc(self, payload, timeout=6):
        request_id = uuid.uuid4().hex
        inbox = queue.Queue(maxsize=2)
        started = time.monotonic()
        with self.pending_lock:
            if len(self.pending) >= 8:
                raise ValueError('Too many Bluetooth requests')
            self.pending[request_id] = inbox

        def wait(kind):
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError('Bluetooth request deadline exceeded')
            reply = inbox.get(timeout=remaining)
            if reply.get('kind') != kind or time.monotonic() - started >= timeout:
                raise ValueError('Expired or wrong Bluetooth response')
            return reply

        try:
            self.enqueue({'kind': 'challenge', 'request_id': request_id})
            grant = wait('grant')
            prepared = time.monotonic() - started
            self.enqueue({'kind': 'command', 'request_id': request_id, 'payload': {
                **payload, '_source_id': self.agent.config['id'], '_challenge': grant['token'],
                '_prepared_elapsed_s': prepared}})
            result = wait('result')
            return result['reply'], time.monotonic() - started
        finally:
            with self.pending_lock:
                self.pending.pop(request_id, None)

    def dispatch(self, body):
        if not self.manager.enabled():
            raise ValueError('Bluetooth service channel disabled')
        kind = body.get('kind')
        if kind == 'probe':
            token = body.get('token', '')
            if not re.fullmatch(r'[0-9a-f]{32}', str(token)):
                raise ValueError('Invalid Bluetooth probe')
            self.subscriber = (token, time.monotonic())
        elif kind == 'group':
            packet = self.agent.protocol.verify(body['packet'])
            if packet['id'] != self.peer:
                raise ValueError('Wrong Bluetooth telemetry sender')
            try:
                self.agent.group.merge(packet, self.endpoint)
            except ChallengeError:
                # Inquiry/coexistence may delay a telemetry frame beyond its
                # receiver nonce. Drop that frame and keep the encrypted link;
                # the next periodic probe establishes a fresh lease.
                return
        elif kind == 'challenge':
            request_id = body.get('request_id', '')
            if not re.fullmatch(r'[0-9a-f]{32}', str(request_id)):
                raise ValueError('Invalid Bluetooth request')
            token = self.agent.challenges.issue('command', 8, self.peer)
            self.enqueue({'kind': 'grant', 'request_id': request_id, 'token': token})
        elif kind == 'command':
            payload = body['payload']
            if payload.get('_source_id') != self.peer:
                raise ValueError('Wrong Bluetooth command sender')
            transit = self.agent.accept_rpc(payload, 'command')
            received = time.monotonic()
            if not self.manager.command_slots.acquire(blocking=False):
                raise ValueError('Bluetooth command capacity exceeded')

            def execute():
                try:
                    reply = self.agent.group.command(payload, transit + time.monotonic() - received, ingress='bluetooth')
                except Exception as exc:
                    reply = {'ok': False, 'command_id': payload.get('command_id'), 'target': payload.get('target'),
                             'error': str(exc)[:240] if isinstance(exc, ValueError) else 'Command not confirmed'}
                finally:
                    self.manager.command_slots.release()
                if not self.stopped.is_set():
                    self.enqueue({'kind': 'result', 'request_id': body['request_id'], 'reply': reply})
            self.manager.executor.submit(execute)
        elif kind in ('grant', 'result'):
            with self.pending_lock:
                inbox = self.pending.get(body.get('request_id'))
                if inbox:
                    inbox.put_nowait(body)
        else:
            raise ValueError('Unsupported Bluetooth message')

    def writer(self):
        last_probe = -10
        try:
            while not self.stopped.is_set():
                if not self.manager.enabled():break
                if time.monotonic() - last_probe >= 1:
                    last_probe = time.monotonic()
                    token = self.agent.group.challenges.issue('group', 4, self.peer)
                    self.send({'kind': 'probe', 'token': token})
                for _ in range(8):
                    try:
                        self.send(self.outbox.get_nowait())
                    except queue.Empty:
                        break
                subscriber = self.subscriber
                if subscriber and time.monotonic() - subscriber[1] < 4:
                    for event in self.agent.group.hub.after(self.revision, 0):
                        packet = self.agent.group.packet(subscriber[0], snapshot=event['packet'], revision=event['revision'])
                        if packet:
                            self.send({'kind': 'group', 'packet': packet})
                        self.revision = event['revision']
                self.stopped.wait(.025)
        except Exception:
            self.close()

    def run(self):
        try:
            self.handshake()
            if not self.manager.activate(self):
                return
            writer = threading.Thread(target=self.writer, name='bmi30-bt-send', daemon=True)
            writer.start()
            while not self.stopped.is_set():
                self.dispatch(self.receive())
        except Exception:
            pass  # Never log frames, configuration or keys.
        finally:
            self.close()
            self.manager.remove(self)

    def close(self):
        self.stopped.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class Manager:
    def __init__(self, agent):
        self.agent = agent
        self.lock = threading.RLock()
        self.links = {}
        self.connecting = set()
        self.inbound = threading.BoundedSemaphore(MAX_LINKS)
        self.command_slots = threading.BoundedSemaphore(2)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='bmi30-bt-command')
        self.state = 'starting'
        self.error = ''
        self.address = ''
        self.peer_cache = Path(agent.config.get('state_dir','/var/lib/bmi30-network')) / 'bluetooth-peers.json'
        self.known = {}
        try:
            if self.peer_cache.stat().st_size > 16384:raise ValueError('Peer cache exceeds limit')
            cached=json.loads(self.peer_cache.read_text(encoding='utf-8'))
            if not isinstance(cached,dict) or len(cached)>32:raise ValueError('Invalid peer cache')
            for ident,address in cached.items():
                if ID.fullmatch(ident) and isinstance(address,str) and MAC.fullmatch(address) and ident!=agent.config['id']:
                    self.known[ident]=address
        except (OSError,ValueError,TypeError):
            pass

    def remember_peer(self, ident, address):
        # A hint is retained only from authenticated, fresh telemetry or a
        # completed BT handshake. The next dial must authenticate the same ID.
        if (not isinstance(ident,str) or not ID.fullmatch(ident) or ident==self.agent.config['id']
                or not isinstance(address,str) or not MAC.fullmatch(address)
                or address in ('00:00:00:00:00:00','FF:FF:FF:FF:FF:FF',self.address)):
            return
        with self.lock:
            if self.known.get(ident)==address:return
            self.known[ident]=address
            while len(self.known)>32:self.known.pop(next(iter(self.known)))
            try:
                import network
                network.atomic(self.peer_cache,json.dumps(self.known))
            except OSError:
                pass  # A failed optional cache write never closes a live link.

    def known_candidates(self):
        with self.lock:return {address:ident for ident,address in self.known.items()}

    def enabled(self):
        from transport_policy import allows
        options = self.agent.config.get('bluetooth', {})
        return options.get('enabled', True) is True and allows(self.agent,'bluetooth')

    def snapshot(self):
        with self.lock:
            return {'enabled': self.enabled(), 'mode': 'classic-bredr', 'nominal_channels': 79,
                    'state': self.state, 'error': self.error,
                    'peers': [{'id': s.peer, 'address': s.address} for s in self.links.values() if not s.stopped.is_set()]}

    def activate(self, session):
        with self.lock:
            if not self.enabled():return False
            old = self.links.get(session.peer)
            preferred = session.outgoing == (self.agent.config['id'] < session.peer)
            if old and not old.stopped.is_set():
                old_preferred = old.outgoing == (self.agent.config['id'] < old.peer)
                if old_preferred or not preferred:
                    return False
                old.close()
            if len(self.links) >= MAX_LINKS and not old:
                return False
            self.links[session.peer] = session
            self.remember_peer(session.peer,session.address)
            return True

    def remove(self, session):
        with self.lock:
            if self.links.get(session.peer) is session:
                del self.links[session.peer]
            remaining = any(s.endpoint == session.endpoint and not s.stopped.is_set() for s in self.links.values())
        if not remaining:
            self.agent.group.drop_endpoint(session.endpoint)

    def accept(self, sock, address):
        if not self.enabled() or not self.inbound.acquire(blocking=False):
            sock.close()
            return

        def run():
            try:
                Session(self, sock, address).run()
            finally:
                self.inbound.release()
        threading.Thread(target=run, name='bmi30-bt-recv', daemon=True).start()

    def connect(self, address, expected=None):
        address = address.upper()
        if not MAC.fullmatch(address):
            return
        with self.lock:
            if (not self.enabled() or address in self.connecting or len(self.connecting) >= 2
                    or len(self.links) >= MAX_LINKS or any(s.address == address for s in self.links.values())):
                return
            self.connecting.add(address)

        def run():
            sock = None
            try:
                sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
                sock.settimeout(3)
                sock.connect((address, RFCOMM_CHANNEL))
                # A completed dial no longer occupies a discovery worker slot.
                with self.lock:
                    self.connecting.discard(address)
                Session(self, sock, address, outgoing=True, expected=expected).run()
            except Exception:
                if sock:
                    sock.close()
            finally:
                with self.lock:
                    self.connecting.discard(address)
        threading.Thread(target=run, name='bmi30-bt-dial', daemon=True).start()

    def rpc(self, endpoint, payload, timeout=6):
        if not self.enabled():raise ConnectionError('Bluetooth service channel disabled')
        with self.lock:
            session = next((s for s in self.links.values() if s.endpoint == endpoint and not s.stopped.is_set()), None)
        if not session:
            raise ConnectionError('Bluetooth peer is not connected')
        return session.rpc(payload, timeout)

    def disconnect_all(self):
        with self.lock:
            for session in list(self.links.values()):
                session.close()

    def run(self):
        while True:
            if not self.enabled():
                self.state = 'disabled'
                self.disconnect_all()
                time.sleep(2)
                continue
            try:
                from bluez_service import run
                run(self)
                if self.state == 'listening':self.state = 'reconnecting'
            except ImportError:
                self.state, self.error = 'unavailable', 'bluez-python-dependency'
            except Exception:
                self.state, self.error = 'unavailable', 'bluez-adapter-or-profile'
            self.disconnect_all()
            time.sleep(5)
