"""Independent relay service; all EP0 work runs outside the detector thread."""
import json
import os
from pathlib import Path
import queue
import struct
import tempfile
import threading
import time

MAX_DURATION_MS = 0x7fffffff


def decode_status(raw):
    raw = bytes(raw)
    if len(raw) != 20 or raw[:5] != b'RLY1\x01':
        raise ValueError('Relay requires STM32 RLY1 firmware 1.2.50+')
    if any(v > 1 for v in raw[5:8]) or raw[8] not in (0, 1, 2):
        raise ValueError('Invalid RLY1 state')
    return dict(enabled=bool(raw[5]), active=bool(raw[6]), pc1=raw[7],
                mode=raw[8], test_enabled=raw[8] == 2,
                remaining_ms=struct.unpack_from('<I', raw, 12)[0])


class RelayService:
    def __init__(self, scope, config_path, *, start=True, clock=time.monotonic):
        self.scope = scope
        self.config_path = Path(config_path)
        self.clock = clock
        self._requests = queue.Queue(maxsize=16)
        self._stop = threading.Event()
        self._stream = None
        self._serial = ''
        self._enabled = False
        self._duration_ms = 1000
        self._test = False
        self._auto = False
        self._minimum_until = 0.0
        self._last_write = -1e9
        self._next_poll = 0.0
        self._suppress = True
        self._seen_rise = 0.0
        # Single immutable publication: no lock, USB call, or queue in hot path.
        self._detection = (False, 0.0, 0.0)
        self._cache = dict(available=False, enabled=False, active=False,
                           test_enabled=False, pc1=None, mode=0)
        self._updated = None
        self._sequence = 0
        self._thread = None
        if start:
            self._thread = threading.Thread(target=self._run, name='relay-control', daemon=True)
            self._thread.start()

    def observe_detection(self, active):
        previous, rise, _ = self._detection
        now = self.clock()
        self._detection = (bool(active), now if active and not previous else rise, now)

    def snapshot(self):
        result = dict(self._cache)
        age = None if self._updated is None else max(0.0, self.clock() - self._updated)
        result.update(duration_ms=self._duration_ms, saved_enabled=self._enabled,
                      age_s=age, usb_serial=self._serial, sequence=self._sequence)
        if age is None or age > 2.5:
            result['available'] = False
        return result

    @staticmethod
    def validate(params):
        if not isinstance(params, dict) or set(params) - {'enabled', 'duration_ms', 'test_enabled', 'persist'}:
            raise ValueError('Invalid relay command')
        for key in ('enabled', 'test_enabled', 'persist'):
            if key in params and type(params[key]) is not bool:
                raise ValueError(key + ' must be boolean')
        if 'duration_ms' in params and (type(params['duration_ms']) is not int or
                                      not 1 <= params['duration_ms'] <= MAX_DURATION_MS):
            raise ValueError('Relay minimum duration must be 1..2147483647 ms')

    def command(self, params):
        try:
            self.validate(params)
            request = dict(params=dict(params), deadline=self.clock()+1.5,
                           done=threading.Event(), result=None)
            self._requests.put_nowait(request)
            if not request['done'].wait(1.7):
                return dict(ok=False, error='Relay command timed out', relay=self.snapshot())
            return request['result']
        except Exception as exc:
            return dict(ok=False, error=str(exc), relay=self.snapshot())

    def _read(self, stream):
        state = decode_status(stream._ep0_ctrl_transfer(0xC0, 0x4C, 0, 0, 20, timeout=100))
        state['available'] = True
        self._cache = state
        self._updated = self.clock()
        self._sequence += 1
        self._next_poll = self._updated + 1.0
        return state

    @staticmethod
    def _write(stream, command, value=0):
        if command == 0x4B:
            stream._ep0_ctrl_transfer(0x40, command, 0, 0, struct.pack('<I', value), timeout=100)
        else:
            stream._ep0_ctrl_transfer(0x40, command, int(value), 0, None, timeout=100)

    def _settings(self):
        try:
            data = json.loads(self.config_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            data = {}
        if not isinstance(data, dict):
            raise ValueError('Invalid relay settings')
        return data

    def _save(self):
        data = self._settings()
        data[self._serial] = dict(enabled=self._enabled, duration_ms=self._duration_ms)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.relay-', dir=self.config_path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(data, out, indent=2)
                out.write('\n')
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.config_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _connect(self, stream):
        self._read(stream)  # Capability check before any writes.
        self._write(stream, 0x4A, False)
        self._serial = str(stream.dev.serial_number or '').strip()
        if not self._serial:
            raise ValueError('USB serial is required for relay settings')
        cfg = self._settings().get(self._serial, dict(enabled=False, duration_ms=1000))
        self.validate(cfg)
        self._enabled = cfg.get('enabled', False)
        self._duration_ms = cfg.get('duration_ms', 1000)
        self._test = self._auto = False
        self._minimum_until = 0.0
        self._suppress = True  # No old event or held test after reconnect/reset.
        self._seen_rise = self._detection[1]
        self._write(stream, 0x4A, self._enabled)
        self._read(stream)
        self._stream = stream

    def _configure(self, stream, params):
        self.validate(params)
        enabled = params.get('enabled', self._enabled)
        test = params.get('test_enabled')
        if test and not enabled:
            raise ValueError('Relay permission is disabled')
        if 'enabled' in params:
            self._write(stream, 0x4A, enabled)
            self._enabled = enabled
            if not enabled:
                self._test = self._auto = False
                self._minimum_until = 0.0
                self._suppress = True
        if test is not None:
            self._write(stream, 0x4D, test)
            self._test = test
            self._auto = False
            self._minimum_until = 0.0
            self._suppress = True  # Stop must not resume an interrupted event.
        self._duration_ms = params.get('duration_ms', self._duration_ms)
        actual = self._read(stream)
        if actual['enabled'] != enabled or (test is not None and actual['test_enabled'] != test):
            raise RuntimeError('STM32 did not confirm relay command')
        persist = params.get('persist', 'enabled' in params or 'duration_ms' in params)
        return dict(ok=True, relay=self.snapshot(), persisted=persist)

    def _automatic(self, stream, now):
        active, rise, observed = self._detection
        inhibited = bool(getattr(self.scope, '_recovery_outputs_inhibited', False))
        if inhibited:
            if self._auto or self._test or self._cache.get('active'):
                self._write(stream, 0x4D, False)
                self._read(stream)
            self._auto = self._test = False
            self._suppress = True
            self._seen_rise = rise
            return
        if not self._enabled or self._test:
            self._seen_rise = rise
            return
        if now - observed > 0.75:
            active = False  # Do not renew a stale detector publication.
        if self._suppress:
            self._seen_rise = rise
            if not active:
                self._suppress = False
            return
        new_event = rise > self._seen_rise and now - rise <= 0.75
        self._seen_rise = rise
        if not self._auto and (active or new_event):
            self._auto = True
            self._minimum_until = now + self._duration_ms/1000.0
            self._last_write = -1e9
        if self._auto:
            remaining = self._minimum_until - now
            if not active and remaining <= 0:
                self._write(stream, 0x4B, 0)
                self._auto = False
                self._read(stream)
            elif now - self._last_write >= 0.15:
                # Firmware owns the minimum timer. Finite renewals also bound
                # output after the host stops publishing a live detection.
                duration = min(MAX_DURATION_MS, max(300, int(max(0, remaining)*1000)+1))
                self._write(stream, 0x4B, duration)
                self._last_write = now
                if self._cache.get('mode') != 1:
                    self._read(stream)

    def step(self):
        stream = getattr(self.scope, 'stream', None)
        if stream is None or getattr(stream, 'disconnected', False) or not getattr(stream, '_running', False):
            self._stream = None
            self._cache = dict(self._cache, available=False)
            return
        # Existing USB owner's lock; never wait behind higher-priority work.
        if not stream._ep0_lock.acquire(False):
            return
        request = None
        try:
            if self._stream is not stream:
                self._connect(stream)
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                request = None
            if request is not None:
                try:
                    if self.clock() > request['deadline']:
                        raise RuntimeError('Relay command expired; no action applied')
                    request['result'] = self._configure(stream, request['params'])
                except Exception as exc:
                    request['result'] = dict(ok=False, error=str(exc), relay=self.snapshot())
            self._automatic(stream, self.clock())
            if self.clock() >= self._next_poll:
                state = self._read(stream)
                if state['enabled'] != self._enabled:
                    self._stream = None  # MCU reset: restore permission, never test.
                if self._test and not state['test_enabled']:
                    self._test = False
                    self._suppress = True
        finally:
            stream._ep0_lock.release()
            if request is not None:
                try:
                    if request['result'].get('ok') and request['result'].get('persisted'):
                        self._save()  # Filesystem I/O never holds the USB lock.
                except Exception as exc:
                    request['result'] = dict(ok=False, error='Unable to save relay settings: '+str(exc),
                                             relay=self.snapshot())
                finally:
                    request['done'].set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as exc:
                self._cache = dict(self._cache, available=False, error=str(exc))
                self._stream = None
                self._stop.wait(0.5)
            self._stop.wait(0.05)
        stream = self._stream
        if stream is not None and stream._ep0_lock.acquire(timeout=0.2):
            try:
                self._write(stream, 0x4A, False)
            except Exception:
                pass
            finally:
                stream._ep0_lock.release()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
