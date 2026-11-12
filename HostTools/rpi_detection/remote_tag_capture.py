"""Observe M02 through its HTTP API and one identified T33, without detector writes.

Frames are complete API waveform snapshots, not a lossless raw USB stream.
This module is sent over SSH; no installation on the detector is needed.
"""
import base64
import collections
import errno
import hashlib
import json
import os
from pathlib import Path
import queue
import select
import shlex
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

from remote_pass_logger import PassMarks, detector_profile, device_identity, validate_status
from ring_gestures import TouchGestures

HOSTNAME = 'BMI30-E29805C7C'
SERIAL = '490df72e29805c7c'
MAC = 'FF:24:01:26:A0:4A'
DOCUMENTS = Path('/home/techaid/Documents')
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    with HTTP.open('http://127.0.0.1:8765' + path, timeout=2) as response:
        return response.read()


def snapshot():
    status = json.loads(get('/api/status'))
    env_raw = (DOCUMENTS / 'host/bmi30_split_active_version.env').read_text()
    env = {k: shlex.split(v)[0] for line in env_raw.splitlines()
           if line and not line.startswith('#') and '=' in line
           for k, v in [line.split('=', 1)] if shlex.split(v)}
    files = {}
    for key in ('BMI30_CORE_PATH', 'BMI30_ENGINE_SOURCE', 'BMI30_GUI_PATH',
                'BMI30_PORTAL_PATH', 'BMI30_CONFIG_JSON', 'BMI30_MODE_STATE_JSON'):
        if key in env:
            path = DOCUMENTS / env[key]
            files[key] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    pid = subprocess.check_output(
        ['systemctl', 'show', 'bmi30-core', '-p', 'MainPID', '--value'], text=True).strip()
    root = subprocess.check_output(['findmnt', '-no', 'SOURCE', '/'], text=True).strip()
    return {'status': status, 'active_env': env_raw, 'files': files, 'core_pid': pid, 'root': root}


def frame_header(raw):
    if raw[:4] != b'BMF1' or len(raw) < 8:
        raise ValueError('Unexpected waveform format')
    size = struct.unpack_from('<I', raw, 4)[0]
    if size > 65536 or len(raw) < 8 + size:
        raise ValueError('Incomplete waveform header')
    header = json.loads(raw[8:8 + size])
    if header.get('available') and not header.get('unchanged'):
        count = header.get('count')
        arrays = header.get('arrays')
        expected_arrays = {'data0_even', 'data0_odd', 'prod0', 'data1_even', 'data1_odd', 'prod1'}
        if (not isinstance(count, int) or count <= 0 or header.get('step') != 1 or
                count != header.get('base_buf_len') or not isinstance(arrays, list) or
                not expected_arrays.issubset(arrays)):
            raise ValueError('Expected full-resolution snapshots of both channels')
        if len(raw) != ((8 + size + 1) & ~1) + 2 * count * len(arrays):
            raise ValueError('Incomplete waveform payload')
    return header


def capture_readiness(read_ring, ring_connected=False, delivery_verified=False):
    marking = bool(read_ring and ring_connected and delivery_verified)
    return dict(observation_ready=True, pass_marking_ready=marking,
                ring_delivery_verified=bool(delivery_verified),
                ready=bool(not read_ring or marking))


def completion_quality(counts, dropped, initial, final, workers_alive, read_ring, delivery_verified):
    """A closed stream is distinct from a usable observational/pass-mark series."""
    reasons = []
    if dropped:
        reasons.append('queue_drops')
    reasons.extend(kind for kind, count in counts.items()
                   if count and (kind.endswith('_error') or kind == 'fatal'))
    if workers_alive:
        reasons.append('workers_did_not_stop')
    for kind in ('sample', 'waveform'):
        if not counts.get(kind):
            reasons.append('missing_' + kind)
    if final.get('error'):
        reasons.append('final_snapshot_error')
    else:
        try:
            validate_status(final['status'], 'upper')
            if detector_profile(initial['status']) != detector_profile(final['status']):
                reasons.append('profile_changed')
            for key in ('core_pid', 'root', 'active_env', 'files'):
                if initial.get(key) != final.get(key):
                    reasons.append(key + '_changed')
        except (KeyError, TypeError, ValueError):
            reasons.append('invalid_final_status')
    marking_reasons = []
    if read_ring:
        if not delivery_verified:
            marking_reasons.append('ring_delivery_unverified')
        if not counts.get('pass_marking_ready'):
            marking_reasons.append('ring_never_ready')
        if counts.get('input_removed') or counts.get('input_error'):
            marking_reasons.append('ring_input_interrupted')
    return dict(observation_complete=not reasons, pass_marking_complete=not marking_reasons,
                observer_complete=not reasons and not marking_reasons,
                incomplete_reasons=reasons + marking_reasons, exact_raw_replay=False)


def run(options):
    assert socket.gethostname() == HOSTNAME, 'Unexpected instrument hostname'
    assert Path('/proc/device-tree/serial-number').read_text().strip('\0\n') == SERIAL, 'Unexpected instrument serial'
    initial = snapshot()
    validate_status(initial['status'], 'upper')
    first_frame = get('/api/frame.bin?max_points=4096&channels=both')
    first_header = frame_header(first_frame)
    if not first_header.get('available'):
        raise RuntimeError('Waveform API is unavailable')
    stop = threading.Event()
    records = queue.Queue(maxsize=512)
    counts, dropped = collections.Counter(), collections.Counter()
    start = time.monotonic()
    deadline = start + options['seconds']
    read_ring = bool(options.get('read_ring', True))
    delivery_verified = options.get('ring_delivery_verified') is True

    def emit(kind, **fields):
        record = dict(kind=kind, epoch=time.time(), observed_mono=time.monotonic(), **fields)
        try:
            records.put_nowait(record)
        except queue.Full:
            dropped[kind] += 1
            stop.set()

    def print_record(record):
        print(json.dumps(record, ensure_ascii=True, separators=(',', ':')), flush=True)
        counts[record['kind']] += 1

    def control():
        # EOF also stops the observer if the supervising SSH connection closes.
        pending = b''
        while not stop.is_set():
            if not select.select([0], [], [], .2)[0]:
                continue
            chunk = os.read(0, 128)
            if not chunk:
                stop.set()
                return
            pending += chunk
            if b'STOP\n' in pending:
                stop.set()
                return
            if len(pending) > 1024:
                emit('control_error', error='Invalid capture control input')
                stop.set()
                return

    def request_stop(*_):
        stop.set()

    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, request_stop)

    def statuses():
        while not stop.is_set():
            before = time.monotonic()
            try:
                status = json.loads(get('/api/status'))
                emit('sample', request_start_mono=before, request_ms=(time.monotonic()-before)*1000,
                     elapsed_s=time.monotonic()-start, status=status)
                validate_status(status, 'upper')
                if detector_profile(status) != detector_profile(initial['status']):
                    raise ValueError('Detector input/settings changed during capture')
            except Exception as error:
                emit('status_error', error=str(error))
            stop.wait(max(0, .1 - (time.monotonic()-before)))

    def frames():
        since = first_header.get('last_frame_t', 0)
        emit('waveform', encoding='BMF1/base64', data=base64.b64encode(first_frame).decode())
        while not stop.is_set():
            before = time.monotonic()
            try:
                raw = get('/api/frame.bin?max_points=4096&channels=both&since=' + str(since))
                header = frame_header(raw)
                if not header.get('available') and not header.get('unchanged'):
                    raise ValueError('Waveform API became unavailable')
                if header.get('available') and not header.get('unchanged'):
                    timestamp = header.get('last_frame_t')
                    if timestamp != since:
                        emit('waveform', request_start_mono=before,
                             request_ms=(time.monotonic()-before)*1000,
                             encoding='BMF1/base64', data=base64.b64encode(raw).decode())
                        since = timestamp
            except Exception as error:
                emit('waveform_error', error=str(error))
            stop.wait(max(0, .04 - (time.monotonic()-before)))

    def ring():
        import fcntl  # Linux only; quality/readiness helpers are testable on Windows.
        event = struct.Struct('llHHi')
        inputs = {}
        marks = PassMarks()
        mapping = options['gesture_mapping']
        verified_delivery = delivery_verified
        try:
            last_scan = 0
            emit('waiting_for_ring', mac=MAC)
            while not stop.is_set():
                if time.monotonic()-last_scan > .25:
                    last_scan = time.monotonic()
                    existing = set()
                    for path in Path('/sys/class/input').glob('event*'):
                        try:
                            if (path/'device/uniq').read_text().strip().casefold() != MAC.casefold():
                                continue
                            identity = device_identity('/dev/input/' + path.name, MAC)
                            device = identity['device']
                            existing.add(device)
                            if device in inputs:
                                continue
                            fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
                            try:
                                fcntl.ioctl(fd, 0x40044590, 1)
                                fcntl.ioctl(fd, 0x400445a0, struct.pack('i', time.CLOCK_MONOTONIC))
                                decoder = None
                                absolute = int((path/'device/capabilities/abs').read_text().replace(' ', ''), 16)
                                if all(absolute & (1 << axis) for axis in (53, 54, 57)):
                                    decoder = TouchGestures()
                                    for axis, name in ((53, 'x'), (54, 'y')):
                                        state = bytearray(struct.pack('2i', axis, 0))
                                        fcntl.ioctl(fd, 0x8008450a, state, True)
                                        setattr(decoder, name, struct.unpack('2i', state)[1])
                                inputs[device] = {'fd': fd, 'decoder': decoder, 'buffer': b''}
                            except BaseException:
                                os.close(fd)
                                raise
                            emit('input_ready', **identity, marks_enabled=decoder is not None)
                            if decoder is not None:
                                readiness = capture_readiness(read_ring, True, verified_delivery)
                                emit('pass_marking_ready' if readiness['ready'] else 'ring_delivery_pending',
                                     **readiness)
                        except OSError as error:
                            # BlueZ can replace evdev nodes while HID services settle.
                            if error.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
                                continue
                            raise
                    for device in list(inputs):
                        if device not in existing:
                            os.close(inputs.pop(device)['fd'])
                            emit('input_removed', device=device)
                            verified_delivery = False
                            emit('pass_marking_unready', reason='ring_disconnected',
                                 **capture_readiness(read_ring))
                            if marks.current is not None:
                                record = marks.finish('unlabelled', time.monotonic())
                                emit(record.pop('kind'), **record, reason='ring_disconnected')
                ready, _, _ = select.select([x['fd'] for x in inputs.values()], [], [], .1)
                for device, info in list(inputs.items()):
                    if info['fd'] not in ready:
                        continue
                    try:
                        chunk = os.read(info['fd'], event.size * 64)
                        if not chunk:
                            raise OSError('Ring disconnected')
                    except OSError as error:
                        os.close(inputs.pop(device)['fd'])
                        emit('input_removed', device=device, reason=str(error))
                        verified_delivery = False
                        emit('pass_marking_unready', reason='ring_disconnected',
                             **capture_readiness(read_ring))
                        if marks.current is not None:
                            record = marks.finish('unlabelled', time.monotonic())
                            emit(record.pop('kind'), **record, reason='ring_disconnected')
                        continue
                    info['buffer'] += chunk
                    while len(info['buffer']) >= event.size:
                        sec, usec, kind, code, value = event.unpack(info['buffer'][:event.size])
                        info['buffer'] = info['buffer'][event.size:]
                        mono = sec + usec / 1e6
                        emit('input', device=device, type=kind, code=code, value=value, input_mono=mono)
                        if kind == 0 and code == 3:
                            raise RuntimeError('SYN_DROPPED: ring marks are incomplete')
                        if info['decoder'] is None:
                            continue
                        gesture = info['decoder'].update(kind, code, value, mono)
                        if gesture is not None:
                            emit('gesture', **{k: v for k, v in gesture.items() if k != 'kind'})
                            action = mapping.get(gesture['gesture'])
                            if action:
                                for record in marks.mark(action, gesture['start_mono']):
                                    emit(record.pop('kind'), **record)
        except Exception as error:
            emit('input_error', error=str(error))
        finally:
            for info in inputs.values():
                os.close(info['fd'])
            if marks.current is not None:
                record = marks.finish('unlabelled', time.monotonic())
                emit(record.pop('kind'), **record, reason='recording_stopped')

    limitations = ('Waveforms are full-resolution API snapshots of processed/averaged signals, '
                   'not every raw ADC packet. They do not support exact detector replay. '
                   'Human marks are independent of detector events; unknown outcomes stay unlabelled.')
    print_record(dict(kind='start', epoch=time.time(), observed_mono=time.monotonic(),
                      hostname=HOSTNAME, serial=SERIAL, channel='upper',
                      **capture_readiness(read_ring, delivery_verified=delivery_verified),
                      duration_s=options['seconds'],
                      tag=options.get('tag'), distance_cm=options.get('distance_cm'),
                      orientation=options.get('orientation'), initial=initial,
                      gesture_mapping=options['gesture_mapping'], calibration=options['calibration'],
                      limitations=limitations))
    targets = [statuses, frames]
    if read_ring:
        targets.append(ring)
    threads = [threading.Thread(target=fn, daemon=True) for fn in targets]
    controller = None
    if options.get('stdin_control', True):
        controller = threading.Thread(target=control, daemon=True)
        controller.start()
    for thread in threads:
        thread.start()
    while time.monotonic() < deadline and not stop.is_set():
        try:
            print_record(records.get(timeout=.2))
        except queue.Empty:
            pass
    stop.set()
    for thread in threads:
        thread.join(timeout=3)
    if controller is not None:
        controller.join(timeout=1)
    workers_alive = any(thread.is_alive() for thread in threads)
    if controller is not None:
        workers_alive = workers_alive or controller.is_alive()
    while not records.empty():
        print_record(records.get_nowait())
    try:
        final = snapshot()
    except Exception as error:
        final = {'error': str(error)}
    print_record(dict(kind='end', epoch=time.time(), observed_mono=time.monotonic(),
                      duration_s=time.monotonic()-start, counts=dict(counts),
                      queue_drops=dict(dropped), final=final,
                      **completion_quality(counts, dropped, initial, final, workers_alive,
                                           read_ring, delivery_verified),
                      limitations=limitations))
