"""Leased, cursor-based hardware test for one paired Bluetooth remote.

Reads evdev only. Starting/polling a test never creates button events. Touch
directions use the historical T33 mapping provisionally, without calibration.
"""
from __future__ import annotations

from collections import deque
import errno
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import threading
import time
import uuid

from ring_gestures import TouchGestures

DEFAULT_HOSTNAME = 'BMI30-C9775FBAE'
BUTTONS = ('center', 'up', 'down', 'left', 'right')
TOUCH_BUTTONS = {'tap': 'center', 'y-': 'up', 'y+': 'down', 'x-': 'left', 'x+': 'right'}
KEY_BUTTONS = {28: 'center', 352: 'center', 103: 'up', 108: 'down', 105: 'left', 106: 'right'}


class InputBusy(RuntimeError):
    pass


class InputDisconnected(RuntimeError):
    pass


def normalize_mac(mac):
    if not isinstance(mac, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', mac):
        raise ValueError('Select one explicit Bluetooth MAC address')
    return mac.upper()


class RemoteInputDecoder:
    """Emit actions only after a complete hardware press/release or touch contact."""
    def __init__(self, touch=False, x=None, y=None):
        self.touch = TouchGestures() if touch else None
        if self.touch is not None:
            self.touch.x, self.touch.y = x, y
        self.keys = {}

    def feed(self, kind, code, value, mono):
        if kind == 0 and code == 3:
            raise RuntimeError('SYN_DROPPED: Bluetooth input was lost; restart the test')
        if self.touch is not None and kind == 1 and code in (325, 330):
            # BTN_TOOL_FINGER / BTN_TOUCH describe the same contact whose
            # completed gesture is decoded below, not a second remote button.
            return []
        result = []
        if kind == 1:
            if value == 1 and code not in self.keys:
                self.keys[code] = mono
                if code not in KEY_BUTTONS:
                    result.append({'button': None, 'complete': False, 'phase': 'diagnostic',
                                   'source': 'key', 'type': kind, 'code': code, 'value': value,
                                   'start_monotonic': mono, 'end_monotonic': mono})
            elif value == 0:
                start = self.keys.pop(code, None)
                button = KEY_BUTTONS.get(code) if start is not None else None
                result.append({'button': button, 'complete': button is not None,
                               'phase': 'complete' if button is not None else 'diagnostic',
                               'pressed': False, 'source': 'key', 'type': kind, 'code': code, 'value': value,
                               'start_monotonic': mono if start is None else start, 'end_monotonic': mono})
            # Held-key repeats do not become additional physical presses.
        elif kind != 0 and (self.touch is None or kind != 3):
            # A different HID mode can send relative motion/scan events. Show
            # their real receipt without guessing a physical button or release.
            result.append({'button': None, 'complete': False, 'phase': 'diagnostic',
                           'source': 'raw', 'type': kind, 'code': code, 'value': value,
                           'start_monotonic': mono, 'end_monotonic': mono})
        if self.touch is not None:
            gesture = self.touch.update(kind, code, value, mono)
            if gesture is not None:
                button = TOUCH_BUTTONS.get(gesture['gesture'])
                result.append({'button': button, 'complete': button is not None,
                               'phase': 'complete' if button is not None else 'diagnostic',
                               'pressed': False, 'source': 'touch', 'gesture': gesture['gesture'],
                               'start_monotonic': gesture['start_mono'], 'end_monotonic': gesture['end_mono'],
                               **{key: gesture[key] for key in ('dx', 'dy', 'excursion', 'reason') if key in gesture}})
        return result


class LinuxBluetoothInputs:
    """Own all matching evdev nodes, and release them together on every exit."""
    def __init__(self, mac, expected_hostname=DEFAULT_HOSTNAME):
        self.mac, self.expected_hostname = normalize_mac(mac), expected_hostname
        self.inputs = {}
        self.event = struct.Struct('llHHi')
        self.next_connection_check = 0.0

    def check_connection(self):
        env = dict(os.environ, LC_ALL='C')
        result = subprocess.run(['bluetoothctl', 'info', self.mac], capture_output=True,
                                text=True, timeout=2, env=env)
        fields = dict(line.strip().split(': ', 1) for line in result.stdout.splitlines() if ': ' in line)
        if result.returncode or fields.get('Paired') != 'yes':
            raise InputDisconnected('The selected Bluetooth device is not paired')
        if fields.get('Connected') != 'yes':
            raise InputDisconnected('The selected Bluetooth remote is disconnected')
        self.next_connection_check = time.monotonic() + 1.0

    def open(self):
        import fcntl
        if self.expected_hostname and socket.gethostname() != self.expected_hostname:
            raise RuntimeError('Bluetooth remote test is outside its configured instrument scope')
        self.check_connection()
        try:
            for path in Path('/sys/class/input').glob('event*'):
                base = path/'device'
                opened_fd = None
                try:
                    if (base/'uniq').read_text().strip().upper() != self.mac:
                        continue
                    if int((base/'id/bustype').read_text().strip(), 16) != 5:
                        continue
                    device = '/dev/input/' + path.name
                    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
                    opened_fd = fd
                    self.inputs[fd] = {'device': device, 'pending': b''}
                    try:
                        fcntl.ioctl(fd, 0x40044590, 1)  # EVIOCGRAB: fail if a capture owns it.
                    except OSError as exc:
                        if exc.errno == errno.EBUSY:
                            raise InputBusy('Bluetooth remote input is busy in another test or active capture') from exc
                        raise
                    fcntl.ioctl(fd, 0x400445a0, struct.pack('i', time.CLOCK_MONOTONIC))
                    # Never replay input queued before this test claimed the node.
                    for _ in range(256):
                        try:
                            if not os.read(fd, 4096):
                                raise InputDisconnected('Bluetooth input disappeared during test startup')
                        except BlockingIOError:
                            break
                    else:
                        raise RuntimeError('Could not drain old Bluetooth input')
                    absolute = int((base/'capabilities/abs').read_text().replace(' ', ''), 16)
                    touch = all(absolute & (1 << axis) for axis in (53, 54, 57))
                    positions = {}
                    if touch:
                        for axis, name in ((53, 'x'), (54, 'y')):
                            state = bytearray(struct.pack('2i', axis, 0))
                            fcntl.ioctl(fd, 0x8008450a, state, True)
                            positions[name] = struct.unpack('2i', state)[1]
                    self.inputs[fd]['decoder'] = RemoteInputDecoder(touch, **positions)
                except FileNotFoundError:
                    if opened_fd is not None:
                        os.close(opened_fd)
                        self.inputs.pop(opened_fd, None)
                    continue
            if not self.inputs:
                raise InputDisconnected('The selected paired remote has no Bluetooth input interface')
        except BaseException:
            self.close()
            raise

    def poll(self, timeout):
        import select
        if time.monotonic() >= self.next_connection_check:
            self.check_connection()
        result = []
        for fd in select.select(list(self.inputs), [], [], timeout)[0]:
            info = self.inputs[fd]
            try:
                chunk = os.read(fd, self.event.size * 64)
            except BlockingIOError:
                continue
            except OSError as exc:
                if exc.errno in (errno.ENODEV, errno.ENXIO):
                    raise InputDisconnected('Bluetooth remote input disconnected') from exc
                raise
            if not chunk:
                raise InputDisconnected('Bluetooth remote input disconnected')
            info['pending'] += chunk
            while len(info['pending']) >= self.event.size:
                sec, usec, kind, code, value = self.event.unpack(info['pending'][:self.event.size])
                info['pending'] = info['pending'][self.event.size:]
                for event in info['decoder'].feed(kind, code, value, sec + usec/1e6):
                    result.append({'device': info['device'], **event})
        return result

    def close(self):
        errors = []
        for fd in list(self.inputs):
            try:
                os.close(fd)  # Releases every EVIOCGRAB, including partially opened sets.
            except OSError as exc:
                errors.append(str(exc))
            finally:
                self.inputs.pop(fd, None)
        if errors:
            raise RuntimeError('; '.join(errors))


class BluetoothRemoteTest:
    """One leased hardware reader; every subscriber supplies its own event cursor."""
    def __init__(self, expected_hostname=DEFAULT_HOSTNAME, *, lease_s=30, history_size=512,
                 provider_factory=None, monotonic=None, wall_clock=None):
        if lease_s <= 0 or history_size < 1:
            raise ValueError('Positive lease and finite nonempty history are required')
        self.lease_s, self.history_size = lease_s, history_size
        self.monotonic, self.wall_clock = monotonic or time.monotonic, wall_clock or time.time
        self.provider_factory = provider_factory or (lambda mac: LinuxBluetoothInputs(mac, expected_hostname))
        self.lock = threading.RLock()
        self.control_lock = threading.Lock()
        self.sessions = {}
        self.active = None

    def start(self, mac):
        with self.control_lock:
            return self._start(mac)

    def _start(self, mac):
        mac = normalize_mac(mac)
        with self.lock:
            old = self.active
        if old:
            self.stop(old)
        test_id = str(uuid.uuid4())
        state = {'test_id': test_id, 'mac': mac, 'status': 'starting', 'reason': '',
                 'connected': False, 'ready': False, 'seq': 0,
                 'history': deque(maxlen=self.history_size), 'button_counts': {key: 0 for key in BUTTONS},
                 'deadline': self.monotonic() + self.lease_s, 'stop': threading.Event()}
        thread = threading.Thread(target=self._read, args=(state,), daemon=True,
                                  name='BMI30-Bluetooth-Remote-Test')
        state['thread'] = thread
        with self.lock:
            self.sessions[test_id] = state
            self.active = test_id
            while len(self.sessions) > 4:
                self.sessions.pop(next(iter(self.sessions)))
        thread.start()
        return self.snapshot(test_id, refresh=False)

    def _read(self, state):
        provider = None
        try:
            provider = self.provider_factory(state['mac'])
            provider.open()
            with self.lock:
                if not state['stop'].is_set():
                    state.update(status='ready', ready=True, connected=True)
            while not state['stop'].is_set():
                with self.lock:
                    if self.monotonic() >= state['deadline']:
                        state.update(status='expired', reason='Test lease expired; start a new test', ready=False)
                        state['stop'].set()
                        break
                events = provider.poll(.1)
                with self.lock:
                    if state['stop'].is_set():
                        break
                    for event in events:
                        state['seq'] += 1
                        mono, epoch = self.monotonic(), self.wall_clock()
                        item = {**event, 'seq': state['seq'], 'provisional': True,
                                'at': epoch + event['end_monotonic'] - mono,
                                'start_at': epoch + event['start_monotonic'] - mono}
                        state['history'].append(item)
                        if item.get('complete') and item.get('button') in state['button_counts']:
                            state['button_counts'][item['button']] += 1
        except Exception as exc:
            with self.lock:
                if not state['stop'].is_set():
                    status = 'busy' if isinstance(exc, InputBusy) else 'disconnected' if isinstance(exc, InputDisconnected) else 'error'
                    state.update(status=status, reason=str(exc), ready=False, connected=False)
        finally:
            if provider is not None:
                try:
                    provider.close()
                except Exception as exc:
                    with self.lock:
                        state.update(status='error', reason='Bluetooth input release failed: ' + str(exc))
            with self.lock:
                state.update(ready=False, connected=False)
                if state['status'] in ('starting', 'ready'):
                    state['status'] = 'stopped'

    def snapshot(self, test_id, cursor=0, refresh=True):
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise ValueError('Event cursor must be a nonnegative integer')
        with self.lock:
            state = self.sessions.get(test_id)
            if state is None:
                return {'ok': False, 'test_id': test_id, 'status': 'unknown', 'reason': 'Unknown remote test',
                        'events': [], 'cursor': cursor, 'connected': False, 'ready': False,
                        'server_at': self.wall_clock()}
            now = self.monotonic()
            if state['status'] in ('starting', 'ready'):
                if now >= state['deadline']:
                    state.update(status='expired', reason='Test lease expired; start a new test', ready=False)
                    state['stop'].set()
                elif refresh:
                    state['deadline'] = now + self.lease_s
            if cursor > state['seq']:
                raise ValueError('Event cursor is ahead of this test')
            first = state['history'][0]['seq'] if state['history'] else state['seq'] + 1
            return {'ok': state['status'] not in ('busy', 'error', 'disconnected', 'unknown'),
                    'test_id': test_id, 'mac': state['mac'], 'status': state['status'], 'reason': state['reason'],
                    'connected': state['connected'], 'ready': state['ready'],
                    'cursor': state['seq'], 'events': [dict(event) for event in state['history'] if event['seq'] > cursor],
                    'history_start_seq': first, 'lost_events': max(0, first-cursor-1),
                    'lease_s': self.lease_s, 'lease_remaining_s': max(0.0, state['deadline']-now),
                    'button_counts': dict(state['button_counts']), 'mapping_provisional': True,
                    'physical_calibration_verified': False, 'server_at': self.wall_clock()}

    def stop(self, test_id):
        with self.lock:
            state = self.sessions.get(test_id)
            if state is None:
                return self.snapshot(test_id, refresh=False)
            state['stop'].set()
            if state['status'] in ('starting', 'ready'):
                state.update(status='stopped', reason='Test stopped', ready=False)
            thread = state['thread']
        if thread is not threading.current_thread():
            thread.join(timeout=3)
        return self.snapshot(test_id, refresh=False)

    def close(self):
        with self.lock:
            test_ids = list(self.sessions)
        for test_id in test_ids:
            self.stop(test_id)
