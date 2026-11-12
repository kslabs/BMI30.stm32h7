"""Identified S07 T33 input for passive capture; no Bluetooth or HTTP commands.

Calibration only records observations. A separate explicit physical confirmation
creates a short-lived manifest before passage marking is permitted.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import socket
import struct
import time
import uuid

from remote_pass_logger import PassMarks, device_identity
from ring_gestures import TouchGestures

HOSTNAME = 'BMI30-C9775FBAE'
SERIAL = 'abdd2dbc9775fbae'
MAC = 'FF:24:01:26:A0:4A'
FORMAT = 'bmi30-t33-physical-delivery-v1'
MAX_AGE_S = 3600
GESTURES = {'tap', 'x+', 'x-', 'y+', 'y-'}


def session_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Expected a canonical fresh calibration session UUID')
    return value


def validate_verification(manifest, expected_session_id, now=None):
    now = time.time() if now is None else now
    session_id(expected_session_id)
    if (manifest.get('format') != FORMAT or manifest.get('status') != 'verified' or
            manifest.get('hostname') != HOSTNAME or manifest.get('serial') != SERIAL or
            str(manifest.get('mac', '')).upper() != MAC or
            manifest.get('session_id') != expected_session_id):
        raise ValueError('Calibration identity/session does not match the selected S07 T33')
    if (manifest.get('user_confirmed_order') is not True or
            manifest.get('user_confirmed_count') is not True or
            manifest.get('expected_count') != 9 or manifest.get('received_count') != 9 or
            manifest.get('connection_changes_during_series') != 0):
        raise ValueError('A physically confirmed complete nine-button delivery check is required')
    sequence = manifest.get('physical_sequence', [])
    if Counter(sequence) != Counter({'center': 3, 'up': 3, 'down': 3}):
        raise ValueError('Physical sequence must identify three presses of each button')
    mapping = manifest.get('button_gestures', {})
    values = [mapping.get(name) for name in ('center', 'up', 'down')]
    if any(value not in GESTURES for value in values) or len(set(values)) != 3:
        raise ValueError('Three distinct physically confirmed button gestures are required')
    for key in ('first_gesture_epoch', 'last_gesture_epoch', 'confirmed_epoch'):
        value = manifest.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= now-value <= MAX_AGE_S:
            raise ValueError('Physical delivery evidence is stale or has an invalid timestamp')
    if not manifest['first_gesture_epoch'] <= manifest['last_gesture_epoch'] <= manifest['confirmed_epoch']:
        raise ValueError('Physical delivery evidence timestamps are out of order')
    digest = manifest.get('source_sha256', '')
    if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
        raise ValueError('Missing calibration evidence hash')
    return dict(zip(values, ('start', 'hit', 'miss')))


def build_verification(records, expected_session_id, physical_sequence,
                       user_confirmed_order=False, user_confirmed_count=False, now=None):
    """Never infer that nine received gestures equal nine physical button presses."""
    if user_confirmed_order is not True or user_confirmed_count is not True:
        raise ValueError('Explicit physical count and order confirmation are required')
    session_id(expected_session_id)
    selected = [row for row in records if row.get('kind', '').startswith('ring_')]
    starts = [row for row in selected if row.get('kind') == 'ring_observation_start']
    ends = [row for row in selected if row.get('kind') == 'ring_observation_end']
    ready = [row for row in selected if row.get('kind') == 'ring_input_ready']
    gestures = [row for row in selected if row.get('kind') == 'ring_gesture']
    if len(starts) != 1 or len(ends) != 1 or len(ready) != 1:
        raise ValueError('Expected one complete identified ring calibration observation')
    for row in selected:
        if (row.get('ring_session_id') != expected_session_id or row.get('hostname') != HOSTNAME or
                str(row.get('mac', '')).upper() != MAC):
            raise ValueError('Mixed or unexpected ring observation identity/session')
    if (starts[0].get('serial') != SERIAL or starts[0].get('mode') != 'calibrate' or
            ends[0].get('ring_input_complete') is not True or
            any(row['kind'] in ('ring_error', 'ring_input_removed') for row in selected)):
        raise ValueError('Ring calibration input was interrupted or is not a calibration observation')
    sequence = list(physical_sequence)
    if (len(sequence) != 9 or len(gestures) != 9 or
            Counter(sequence) != Counter({'center': 3, 'up': 3, 'down': 3})):
        raise ValueError('Exactly nine observed gestures and three physical presses per button are required')
    mapping = {}
    for button, gesture in zip(sequence, gestures):
        value = gesture.get('gesture')
        if value not in GESTURES or button in mapping and mapping[button] != value:
            raise ValueError('Physical button observations are inconsistent or ambiguous')
        mapping[button] = value
    confirmed = time.time() if now is None else now
    manifest = {'format': FORMAT, 'status': 'verified', 'hostname': HOSTNAME, 'serial': SERIAL, 'mac': MAC,
                'session_id': expected_session_id, 'physical_sequence': sequence,
                'button_gestures': mapping, 'expected_count': 9, 'received_count': len(gestures),
                'user_confirmed_order': True, 'user_confirmed_count': True,
                'connection_changes_during_series': 0,
                'first_gesture_epoch': gestures[0]['epoch'], 'last_gesture_epoch': gestures[-1]['epoch'],
                'confirmed_epoch': confirmed,
                'source_sha256': hashlib.sha256(json.dumps(selected, sort_keys=True,
                    separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()}
    validate_verification(manifest, expected_session_id, confirmed)
    return manifest


class RingMarks:
    """Pass state only; physical calibration observations never enter this class."""
    def __init__(self, mapping):
        self.mapping = mapping
        self.marks = PassMarks()
        self.invalid = 0
        self.started = 0

    def gesture(self, gesture):
        action = self.mapping.get(gesture.get('gesture'))
        if action is None:
            self.invalid += 1
            return [{'kind': 'ignored_mark', 'action': 'unmapped_gesture', 'reason': 'uncalibrated_gesture'}]
        if action == 'start':
            self.started += 1
            if self.marks.current is not None:
                self.invalid += 1
        rows = self.marks.mark(action, gesture['start_mono'])
        self.invalid += sum(row['kind'] == 'ignored_mark' for row in rows)
        return rows

    def finish(self, mono):
        if self.marks.current is None:
            return []
        return [{**self.marks.finish('unlabelled', mono), 'reason': 'ring_observation_stopped'}]

    def summary(self):
        return {'pass_started': self.started, 'marked_passes': len(self.marks.completed),
                'invalid_marks': self.invalid,
                'unlabelled_passes': sum(row['human_result'] == 'unlabelled' for row in self.marks.completed),
                'incomplete_pass': self.marks.current}


class RingInputBuffer:
    """Keep per-interface input intact; only the selected touch decodes gestures."""
    def __init__(self, device, role, decoder=None):
        if role not in ('touch', 'companion') or role == 'companion' and decoder is not None:
            raise ValueError('Only the selected touch interface may decode gestures')
        self.device, self.role, self.decoder = device, role, decoder
        self.event = struct.Struct('llHHi')
        self.pending = b''
        self.counts = Counter()

    def feed(self, chunk):
        self.pending += chunk
        while len(self.pending) >= self.event.size:
            sec, usec, kind, code, value = self.event.unpack(self.pending[:self.event.size])
            self.pending = self.pending[self.event.size:]
            mono = sec + usec / 1e6
            self.counts[str(kind)] += 1
            yield {'kind': 'ring_input', 'device': self.device, 'interface_role': self.role,
                   'type': kind, 'code': code, 'value': value, 'input_mono': mono}
            if kind == 0 and code == 3:
                raise ValueError('SYN_DROPPED: ring interface events lost: ' + self.device)
            if self.decoder is not None:
                gesture = self.decoder.update(kind, code, value, mono)
                if gesture is not None:
                    yield {'kind': 'ring_gesture', 'device': self.device, 'interface_role': self.role,
                           **{key: val for key, val in gesture.items() if key != 'kind'}}


def run_ring(options, emit_record, stop_event):
    """Block in the capture's input thread; open only the exactly identified ring."""
    import fcntl
    import os
    import select

    mode = options.get('mode')
    run_id = session_id(options.get('session_id'))
    if (mode not in ('calibrate', 'mark') or options.get('hostname') != HOSTNAME or
            options.get('serial') != SERIAL or str(options.get('mac', '')).upper() != MAC):
        raise ValueError('Ring input is limited to the explicitly identified S07 T33')
    if (socket.gethostname() != HOSTNAME or
            Path('/proc/device-tree/serial-number').read_text().strip('\0\n') != SERIAL):
        raise ValueError('Live S07 hostname/serial mismatch')
    mapping = None
    if mode == 'mark':
        mapping = validate_verification(options.get('calibration', {}), options.get('verification_session_id'))
    marker = RingMarks(mapping) if mapping is not None else None
    descriptors = []
    errors = []
    ready = False
    gestures = 0
    inputs = {}

    def emit(kind, **fields):
        emit_record({'kind': kind, 'epoch': time.time(), 'observed_mono': time.monotonic(),
                     'ring_session_id': run_id, 'hostname': HOSTNAME, 'mac': MAC, **fields})

    emit('ring_observation_start', mode=mode, serial=SERIAL,
         verification_session_id=options.get('verification_session_id'))
    try:
        identity = device_identity(options['device'], MAC)
        base = Path('/sys/class/input') / Path(identity['device']).name / 'device'
        absolute = int((base/'capabilities/abs').read_text().replace(' ', ''), 16)
        if not all(absolute & (1 << axis) for axis in (53, 54, 57)):
            raise ValueError('Selected T33 interface lacks the calibrated touch axes')
        selected_fd = os.open(identity['device'], os.O_RDONLY | os.O_NONBLOCK)
        descriptors.append(selected_fd)
        fcntl.ioctl(selected_fd, 0x40044590, 1)  # EVIOCGRAB, released by close.
        # CLOCK_MONOTONIC is mandatory: no fallback to a different time base.
        fcntl.ioctl(selected_fd, 0x400445a0, struct.pack('i', time.CLOCK_MONOTONIC))
        decoder = TouchGestures()
        inputs[selected_fd] = RingInputBuffer(identity['device'], 'touch', decoder)
        # Read this exact ring's companion reports for diagnostics in both
        # modes. They never receive a gesture decoder or create pass marks.
        for path in Path('/sys/class/input').glob('event*'):
            try:
                if (path/'device/uniq').read_text().strip().upper() != MAC:
                    continue
                companion = device_identity('/dev/input/' + path.name, MAC)
                if companion['device'] == identity['device']:
                    continue
                fd = os.open(companion['device'], os.O_RDONLY | os.O_NONBLOCK)
                descriptors.append(fd)
                fcntl.ioctl(fd, 0x40044590, 1)
                fcntl.ioctl(fd, 0x400445a0, struct.pack('i', time.CLOCK_MONOTONIC))
                inputs[fd] = RingInputBuffer(companion['device'], 'companion')
            except FileNotFoundError:
                continue
        for fd, info in inputs.items():
            for _ in range(256):
                try:
                    if not os.read(fd, 4096):
                        raise RuntimeError('Ring disconnected before input became ready: ' + info.device)
                except BlockingIOError:
                    break
            else:
                raise RuntimeError('Could not drain old ring input before readiness: ' + info.device)
        # Query current slot positions after draining: Linux may omit unchanged
        # coordinates from the next contact's input reports.
        for axis, name in ((53, 'x'), (54, 'y')):
            state = bytearray(struct.pack('2i', axis, 0))
            fcntl.ioctl(selected_fd, 0x8008450a, state, True)
            setattr(decoder, name, struct.unpack('2i', state)[1])
        ready = True
        for info in inputs.values():
            emit('ring_interface_ready', device=info.device, interface_role=info.role, clock='monotonic')
        emit('ring_input_ready', **identity, marks_enabled=mode == 'mark', clock='monotonic',
             physical_delivery_verified=mode == 'mark')
        if mode == 'mark':
            emit('input_ready', **identity, marks_enabled=True, source='ring',
                 physical_delivery_verified=True,
                 verification_session_id=options['verification_session_id'])
        while not stop_event.is_set():
            readable = select.select(list(inputs), [], [], .1)[0]
            for fd in readable:
                info = inputs[fd]
                try:
                    chunk = os.read(fd, info.event.size * 64)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise RuntimeError('Ring input disconnected: ' + info.device)
                for record in info.feed(chunk):
                    kind = record.pop('kind')
                    emit(kind, **record)
                    if kind == 'ring_gesture':
                        gestures += 1
                        if marker is not None:
                            for row in marker.gesture(record):
                                emit(row.pop('kind'), source='ring', **row)
        if any(info.pending for info in inputs.values()):
            raise RuntimeError('Partial ring input event at stop')
    except Exception as error:
        errors.append(type(error).__name__ + ': ' + str(error))
        emit('ring_error', error=errors[-1], source='ring')
    finally:
        for fd in descriptors:
            os.close(fd)
        if marker is not None:
            for row in marker.finish(time.monotonic()):
                emit(row.pop('kind'), source='ring', **row)
    summary = {'ring_input_complete': bool(ready and not errors), 'errors': errors,
               'gestures': gestures, 'mode': mode, 'ring_session_id': run_id,
               'input_event_types_by_device': {info.device: dict(info.counts) for info in inputs.values()}}
    if marker is not None:
        summary.update(marker.summary())
    emit('ring_observation_end', **{key: value for key, value in summary.items() if key != 'ring_session_id'})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-log', type=Path, required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--confirm-physical-sequence', required=True,
                        help='User-confirmed physical order, e.g. up,up,up,down,down,down,center,center,center')
    parser.add_argument('--confirm-physical-count', type=int, choices=(9,), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    opener = gzip.open if args.verify_log.suffix == '.gz' else open
    with opener(args.verify_log, 'rt', encoding='utf-8') as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    manifest = build_verification(records, args.session_id,
        [name.strip() for name in args.confirm_physical_sequence.split(',')],
        user_confirmed_order=True, user_confirmed_count=args.confirm_physical_count == 9)
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'verification': str(args.output), 'session_id': manifest['session_id'],
                      'received_count': manifest['received_count'], 'status': manifest['status']}))


if __name__ == '__main__':
    main()
