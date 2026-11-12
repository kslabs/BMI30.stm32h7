"""Passive BMI30 wire capture; execute through run_passive_raw_capture.py.

Reads usbmon, never opens the vendor device or sends a device command. Raw
transfers and status snapshots coexist with live detection. This is wire data,
not a serialization of the detector's initial state or an exact replay claim.
"""
from __future__ import annotations

import ast
import base64
import binascii
from collections import Counter
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import select
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

AVERAGE_DEPTHS = (2, 4, 8, 16, 24, 32, 40, 48, 56, 64)


def select_transfer(header, data, *, bus, address, endpoint):
    """Return target completed Bulk IN, retaining errors as evidence."""
    if len(header) != 64:
        raise ValueError('usbmon header must contain 64 bytes')
    if (header[8], header[9], header[10], header[11],
            struct.unpack_from('<H', header, 12)[0]) != (ord('C'), 3, endpoint, address, bus):
        return None
    result, length, captured = struct.unpack_from('<iII', header, 28)
    if captured > len(data) or captured > length:
        raise ValueError('invalid usbmon captured length')
    seconds, micros = struct.unpack_from('<qi', header, 16)
    return {'usb_at': seconds + micros / 1e6, 'result': result,
            'length': length, 'captured': captured,
            'truncated': captured != length, 'data': bytes(data[:captured])}


class WireCheck:
    """Streaming ADC CRC/sequence diagnostics; preserves original bytes elsewhere.

    ACK/EVT messages may share the endpoint. Only plausible ADC headers for the
    configured sample count enter this diagnostic parser. Boundary fragments
    are reported separately and never silently promoted to verified frames.
    """
    def __init__(self, samples):
        self.samples = samples
        self.buffer = bytearray()
        self.frames = [0, 0]
        self.crc_bad = 0
        self.crc_missing = 0
        self.seq_gaps = [0, 0]
        self.seq_duplicates = [0, 0]
        self.last_seq = [None, None]
        self.phases = [Counter(), Counter()]

    def feed(self, data):
        self.buffer.extend(data)
        while True:
            pos = self.buffer.find(b'\x5a\xa5')
            if pos < 0:
                self.buffer[:] = self.buffer[-1:]
                return
            if pos:
                del self.buffer[:pos]
            if len(self.buffer) < 32:
                return
            fields = struct.unpack_from('<HBBIIHHIIIHH', self.buffer)
            _, ver, flags, seq, timestamp, count, zones, offset, length, dma, reserved2, crc = fields
            if count != self.samples or not flags & 3 or (flags & 3) == 3:
                del self.buffer[0]
                continue
            end = 32 + count * 2
            if len(self.buffer) < end:
                return
            frame = bytes(self.buffer[:end])
            del self.buffer[:end]
            if not flags & 4:
                self.crc_missing += 1
                continue
            if binascii.crc_hqx(frame[:30] + frame[32:], 0xffff) != crc:
                self.crc_bad += 1
                continue
            ch = 0 if flags & 1 else 1
            if self.last_seq[ch] is not None:
                delta = (seq - self.last_seq[ch]) & 0xffffffff
                if delta == 0:
                    self.seq_duplicates[ch] += 1
                elif delta != 1:
                    self.seq_gaps[ch] += 1
            self.last_seq[ch] = seq
            self.frames[ch] += 1
            self.phases[ch][str(reserved2 & 1)] += 1

    def summary(self):
        return {'crc_verified_frames': self.frames, 'crc_bad': self.crc_bad,
                'crc_missing': self.crc_missing, 'sequence_discontinuities': self.seq_gaps,
                'sequence_duplicates': self.seq_duplicates,
                'phase_counts': [dict(p) for p in self.phases],
                'trailing_fragment_bytes': len(self.buffer)}


def settings_fingerprint(status):
    detector = status.get('detector', {})
    channels = detector.get('channels', {})
    names = ('enabled', 'confirm_count', 'threshold', 'threshold_high', 'auto_threshold',
             'noise_up', 'noise_down', 'noise_unit', 'burst_gate', 'burst_blank_s', 'burst_max_ratio')
    return {'mode': status.get('mode'), 'averaging_depth': status.get('averaging', {}).get('depth'),
            'settings': detector.get('settings'),
            'channels': {ch: {k: c.get(k) for k in names} for ch, c in channels.items()},
            # dc_guard is the current adaptive runtime profile. Its enabled
            # state and settle time may change normally without a settings
            # edit; retain every transition in status, while config hashes
            # below independently guard the persistent policy.
            'smoothing': status.get('smoothing')}


def capture_complete(summary):
    """Completeness is independent of whether detections occurred."""
    w = summary['wire']
    acquisition_advanced = (not summary.get('acquisition_only') or
                            all(count >= 2 for count in w['crc_verified_frames']))
    return bool(acquisition_advanced and summary.get('monitor_restored') and summary.get('settings_unchanged')
                and summary.get('config_unchanged') and summary.get('core_process_unchanged')
                and not summary.get('errors') and not summary.get('queue_dropped')
                and not summary.get('kernel_dropped') and not summary.get('usb_errors')
                and not summary.get('truncated_transfers')
                and (not summary.get('ring_requested') or summary.get('ring', {}).get('ring_input_complete'))
                and summary.get('max_status_gap_s', float('inf')) <= .5
                and all(w['crc_verified_frames']) and not w['crc_bad']
                and not w['crc_missing'] and not any(w['sequence_discontinuities'])
                and not any(w['sequence_duplicates']))


def _status():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open('http://127.0.0.1:8765/api/status', timeout=3) as response:
        raw = json.load(response)
    return {k: raw.get(k) for k in ('ok', 'busy', 'stale', 'connection', 'mode', 'averaging',
            'smoothing', 'dc_guard', 'lcd_role_overlay', 'detector', 'frames', 'stream_stats', 'player_recorder',
            'sound', 'optic', 'sync', 'frame_integrity')}


def _acquisition_profile(s):
    mode = s.get('mode') or {}
    if not isinstance(mode, dict):
        return None
    if any(type(mode.get(key)) is not int for key in ('selected','stream_mode','base_buf_len')):
        return None
    identity = (mode.get('selected'), mode.get('stream_mode'), mode.get('base_buf_len'))
    if identity == (5, 2, 200):
        depth = mode.get('avg_n')
        averaging = s.get('averaging') or {}
        if (type(depth) is int and depth in AVERAGE_DEPTHS
                and type(mode.get('rpi_avg_n')) is int and mode['rpi_avg_n']==1
                and isinstance(averaging, dict) and type(averaging.get('depth')) is int
                and averaging['depth']==1):
            return 'averaged_roi_'+str(depth)
        return None
    return {(3, 0, 600): 'latest_full', (4, 1, 200): 'lossless_roi'}.get(identity)


def _healthy(s, acquisition_only=False):
    connected = bool(s.get('ok') and not s.get('stale') and s.get('connection', {}).get('connected'))
    if acquisition_only:
        return bool(connected and not s.get('busy') and _acquisition_profile(s) is not None)
    return bool(connected and s.get('detector', {}).get('active')
                and s.get('detector', {}).get('worker', {}).get('alive'))


def run(options):
    preflight_started = time.monotonic()
    capture_started = capture_ended = None
    serial = Path('/proc/device-tree/serial-number').read_text().strip('\0\n ')
    hostname = socket.gethostname()
    root = subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '/'], text=True).strip()
    if (hostname, serial, root) != (options['expect_host'], options['expect_serial'], options['expect_root']):
        raise RuntimeError('Host, serial or root identity mismatch; capture was not started')
    acquisition_only = options.get('acquisition_only') is True
    expected_average = options.get('expect_average')
    if expected_average is not None and (not acquisition_only or type(expected_average) is not int
                                         or expected_average not in AVERAGE_DEPTHS):
        raise RuntimeError('Expected average depth requires an explicit supported acquisition-only profile')
    recording_mode = 'acquisition_only' if acquisition_only else 'live_detection'
    before = _status()
    if not _healthy(before, acquisition_only):
        reason = ('Expected healthy LATEST600, LOSSLESS_ROI200 or mode5 AVG_ROI200 with supported N and Raspberry depth1'
                  if acquisition_only else 'Live detector is not healthy')
        raise RuntimeError(reason + '; capture was not started')
    acquisition_profile = _acquisition_profile(before) if acquisition_only else None
    if expected_average is not None and acquisition_profile!='averaged_roi_'+str(expected_average):
        raise RuntimeError('Actual acquisition average profile does not match the requested depth; capture was not started')
    docs = Path('/home/techaid/Documents')
    env = {}
    for line in (docs/'host/bmi30_split_active_version.env').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            parts = shlex.split(value)
            if len(parts) == 1:
                env[key] = parts[0]
    engine = Path(env['BMI30_ENGINE_SOURCE'])
    if not engine.is_absolute():
        engine = docs / engine
    parser = engine.parent / 'usb_vendor/usb_stream.py'
    tree = ast.parse(parser.read_text())
    endpoint = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(getattr(t, 'id', '') == 'EP_IN' for t in n.targets))
    targets = []
    for device in Path('/sys/bus/usb/devices').iterdir():
        if ((device/'idVendor').is_file() and (device/'idProduct').is_file()
                and (device/'idVendor').read_text().strip() == 'cafe'
                and (device/'idProduct').read_text().strip() == '4001'):
            targets.append((int((device/'busnum').read_text()), int((device/'devnum').read_text())))
    if len(targets) != 1:
        raise RuntimeError('Expected exactly one BMI30 vendor USB device')
    bus, address = targets[0]
    was_loaded = any(line.startswith('usbmon ') for line in Path('/proc/modules').read_text().splitlines())
    outgoing = queue.Queue(maxsize=options.get('queue_size', 1024))
    stopped = threading.Event()
    counts = Counter()
    errors = []
    timing = {'last_status': time.monotonic(), 'max_status_gap_s': 0.0}
    marking = {'ready': False, 'active': None, 'finished': 0, 'invalid': 0, 'unlabelled': 0}
    pass_ids = set()
    ring_result = {}
    ring_thread = None
    check = WireCheck(before['mode']['base_buf_len'])
    metadata = {'hostname': hostname, 'serial': serial, 'root': root, 'bus': bus,
                'acquisition_only': acquisition_only, 'recording_mode': recording_mode,
                'acquisition_profile': acquisition_profile,
                'expected_average': expected_average,
                'actual_stm32_average': (before.get('mode') or {}).get('avg_n') if acquisition_profile and acquisition_profile.startswith('averaged_roi_') else None,
                'session_id': options.get('session_id'),
                'ring_mode': options.get('ring', {}).get('mode'),
                'address': address, 'endpoint': endpoint, 'wire_format': '<HBBIIHHIIIHH',
                'engine_sha256': hashlib.sha256(engine.read_bytes()).hexdigest(),
                'usb_parser_sha256': hashlib.sha256(parser.read_bytes()).hexdigest(),
                'module_loaded_before': was_loaded,
                'test_conditions': options.get('test_conditions', {}),
                'test_conditions_source': 'user_supplied',
                'replay_limitation': 'No detector initial state; warmup and equivalence validation required'}
    for key in ('BMI30_CORE_PATH', 'BMI30_FIRMWARE_MANIFEST'):
        candidate = Path(env[key]) if key in env else None
        if candidate is not None:
            if not candidate.is_absolute():
                candidate = docs/candidate
            if candidate.is_file():
                metadata[key.lower()+'_sha256'] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    metadata['bundle_version'] = env.get('BMI30_SPLIT_VERSION')
    metadata['config_sha256'] = hashlib.sha256((docs/'host/bmi30_config.json').read_bytes()).hexdigest()
    metadata['core_pid'] = int(subprocess.check_output(
        ['systemctl', 'show', 'bmi30-core', '-p', 'MainPID', '--value'], text=True).strip())
    if metadata['core_pid'] <= 0:
        raise RuntimeError('Core service has no running process')

    def enqueue(kind, **values):
        record = {'kind': kind, 'at': time.time(), 'monotonic': time.monotonic(), **values}
        try:
            outgoing.put_nowait(record)
        except queue.Full:
            counts['queue_dropped'] += 1

    def writer():
        try:
            while True:
                record = outgoing.get()
                if record is None:
                    return
                print(json.dumps(record, separators=(',', ':')), flush=True)
                counts['written'] += 1
        except (OSError, ValueError) as exc:
            errors.append('writer:' + type(exc).__name__)
            stopped.set()

    def statuses():
        while not stopped.wait(.1):
            try:
                value = _status()
                now = time.monotonic()
                timing['max_status_gap_s'] = max(timing['max_status_gap_s'], now-timing['last_status'])
                timing['last_status'] = now
                counts['status_samples'] += 1
                if not _healthy(value, acquisition_only):
                    errors.append('acquisition_unhealthy' if acquisition_only else 'detector_unhealthy')
                elif acquisition_only and _acquisition_profile(value) != acquisition_profile:
                    errors.append('acquisition_profile_changed')
                enqueue('status', status=value, acquisition_only=acquisition_only, recording_mode=recording_mode,
                        acquisition_profile=_acquisition_profile(value) if acquisition_only else None)
            except Exception as exc:
                counts['status_errors'] += 1
                errors.append('status:' + type(exc).__name__)

    def controls():
        for line in sys.stdin:
            try:
                cmd = json.loads(line)
                if cmd.get('action') == 'stop':
                    stopped.set()
                    return
                if cmd.get('action') == 'mark_check':
                    if not options.get('ring'):
                        marking['ready'] = True
                    enqueue('input_ready', marks_enabled=not bool(options.get('ring')), source='control_file',
                            probe=cmd.get('probe'))
                    continue
                if cmd.get('action') not in ('pass_start', 'pass_end', 'note'):
                    raise ValueError('unknown control action')
                if options.get('ring') and cmd.get('action') in ('pass_start', 'pass_end'):
                    raise ValueError('ring session accepts physical ring pass marks only')
                mono, epoch = time.monotonic(), time.time()
                if cmd['action'] == 'pass_start':
                    if not isinstance(cmd.get('pass_id'), int) or cmd['pass_id'] <= 0:
                        raise ValueError('pass_id must be a positive integer')
                    if cmd['pass_id'] in pass_ids:
                        marking['invalid'] += 1
                    pass_ids.add(cmd['pass_id'])
                    if marking['active'] is not None:
                        marking['invalid'] += 1
                    marking['active'] = {'pass_id': cmd['pass_id'], 'start_mono': mono}
                    channel = options.get('test_conditions', {}).get('channel')
                    if channel in ('upper', 'lower'):
                        marking['active']['channel'] = channel
                    enqueue('pass_start', **marking['active'], epoch=epoch, source='control_file')
                elif cmd['action'] == 'pass_end':
                    result = cmd.get('result')
                    if result not in ('hit', 'miss', 'unlabelled'):
                        raise ValueError('invalid pass result')
                    active = marking['active']
                    if active is None or active['pass_id'] != cmd['pass_id']:
                        marking['invalid'] += 1
                        enqueue('orphan_pass_end', pass_id=cmd['pass_id'], human_result=result,
                                observed_mono=mono, epoch=epoch)
                    else:
                        enqueue('pass_end', **active, end_mono=mono, observed_mono=mono,
                                human_result=result, epoch=epoch, source='control_file')
                        marking['active'] = None
                        marking['finished'] += 1
                        marking['unlabelled'] += result == 'unlabelled'
                else:
                    enqueue('note', text=str(cmd.get('text', ''))[:1000], epoch=epoch)
            except Exception as exc:
                counts['control_errors'] += 1
                errors.append('control:' + type(exc).__name__)
                enqueue('control_error', error=type(exc).__name__)
        # EOF is a graceful stop; it must not leave a remote monitor running.
        stopped.set()

    def ring_input():
        def emit(record):
            kind = record['kind']
            values = {k:v for k,v in record.items() if k != 'kind'}
            if kind == 'input_ready' and record.get('source') == 'ring':
                marking['ready'] = bool(record.get('marks_enabled'))
            if kind == 'pass_start':
                pass_id = record['pass_id']
                if pass_id in pass_ids or marking['active'] is not None:
                    marking['invalid'] += 1
                pass_ids.add(pass_id)
                marking['active'] = record
                channel = options.get('test_conditions', {}).get('channel')
                if channel in ('upper', 'lower'):
                    values['channel'] = channel
            elif kind == 'pass_end':
                active = marking['active']
                if active is None or active.get('pass_id') != record.get('pass_id'):
                    marking['invalid'] += 1
                else:
                    marking['finished'] += 1
                    marking['unlabelled'] += record.get('human_result') == 'unlabelled'
                    marking['active'] = None
                channel = options.get('test_conditions', {}).get('channel')
                if channel in ('upper', 'lower'):
                    values['channel'] = channel
            elif kind == 'ignored_mark':
                marking['invalid'] += 1
            elif kind == 'ring_error':
                marking['ready'] = False
                errors.append('ring_input_error')
            enqueue(kind, **values)

        try:
            from passive_ring_input import run_ring
            ring_result.update(run_ring(options['ring'], emit, stopped))
            if not ring_result.get('ring_input_complete'):
                errors.append('ring_input_incomplete')
                stopped.set()
        except BaseException as exc:
            errors.append('ring:' + type(exc).__name__)
            ring_result.update(ring_input_complete=False, error=type(exc).__name__)
            enqueue('ring_error', error=type(exc).__name__)
            stopped.set()

    writer_thread = threading.Thread(target=writer, daemon=True)
    writer_thread.start()
    fd = None
    status_thread = None
    monitor_restored = False
    kernel_dropped = 0
    queued_at_end = 0
    libc = ctypes.CDLL(None, use_errno=True)
    libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
    libc.ioctl.restype = ctypes.c_int
    header = ctypes.create_string_buffer(64)
    data = ctypes.create_string_buffer(65536)

    class GetArg(ctypes.Structure):
        _fields_ = [('hdr', ctypes.c_void_p), ('data', ctypes.c_void_p), ('alloc', ctypes.c_size_t)]

    args = GetArg(ctypes.addressof(header), ctypes.addressof(data), len(data))
    if ctypes.sizeof(args) != 24:
        raise RuntimeError('Unsupported usbmon ioctl ABI (requires 64-bit host)')
    getx = (1 << 30) | (24 << 16) | (0x92 << 8) | 10
    getstats = (2 << 30) | (8 << 16) | (0x92 << 8) | 3
    try:
        if not was_loaded:
            subprocess.run(['modprobe', 'usbmon'], check=True, capture_output=True)
        fd = os.open('/dev/usbmon' + str(bus), os.O_RDONLY | os.O_NONBLOCK)
        capture_started = time.monotonic()
        timing['last_status'] = capture_started
        enqueue('ready', ready=True, metadata=metadata, status=before,
                pass_marking_ready=False, acquisition_only=acquisition_only, recording_mode=recording_mode,
                acquisition_profile=acquisition_profile)
        status_thread = threading.Thread(target=statuses, daemon=True)
        status_thread.start()
        threading.Thread(target=controls, daemon=True).start()
        if options.get('ring'):
            ring_thread = threading.Thread(target=ring_input, daemon=True)
            ring_thread.start()
        while not stopped.is_set() and time.monotonic() - capture_started < options['seconds']:
            if not select.select([fd], [], [], .1)[0]:
                continue
            if libc.ioctl(fd, getx, ctypes.byref(args)) < 0:
                errno = ctypes.get_errno()
                if errno in (11, 4):
                    continue
                raise OSError(errno, 'MON_IOCX_GETX')
            counts['monitor_events'] += 1
            transfer = select_transfer(header.raw, data.raw, bus=bus, address=address, endpoint=endpoint)
            if transfer is None:
                continue
            counts['transfers'] += 1
            counts['usb_errors'] += transfer['result'] != 0
            counts['truncated_transfers'] += transfer['truncated']
            check.feed(transfer['data'])
            transfer['data'] = base64.b64encode(transfer['data']).decode('ascii')
            enqueue('transfer', **transfer)
            # MON_IOCG_STATS resets the lost-event count on each read.
            stats = ctypes.create_string_buffer(8)
            if libc.ioctl(fd, getstats, ctypes.byref(stats)) != 0:
                raise OSError(ctypes.get_errno(), 'MON_IOCG_STATS')
            queued_at_end, lost = struct.unpack('<II', stats.raw)
            kernel_dropped += lost
    except BaseException as exc:
        errors.append('capture:' + type(exc).__name__ + ':' + str(exc))
    finally:
        capture_ended = time.monotonic()
        stopped.set()
        if ring_thread:
            ring_thread.join(timeout=4)
            if ring_thread.is_alive():
                errors.append('ring_thread_did_not_stop')
        if status_thread:
            status_thread.join(timeout=4)
            if status_thread.is_alive():
                errors.append('status_thread_did_not_stop')
        if fd is not None:
            stats = ctypes.create_string_buffer(8)
            if libc.ioctl(fd, getstats, ctypes.byref(stats)) == 0:
                queued_at_end, lost = struct.unpack('<II', stats.raw)
                kernel_dropped += lost
            else:
                errors.append('final_kernel_stats_failed')
            os.close(fd)
        if not was_loaded:
            try:
                subprocess.run(['modprobe', '-r', 'usbmon'], check=True, capture_output=True)
            except Exception as exc:
                errors.append('module_restore:' + type(exc).__name__)
        monitor_restored = was_loaded == any(line.startswith('usbmon ') for line in Path('/proc/modules').read_text().splitlines())
    try:
        after = _status()
        if acquisition_only and not _healthy(after, True):
            errors.append('final_acquisition_unhealthy')
        elif acquisition_only and _acquisition_profile(after) != acquisition_profile:
            errors.append('final_acquisition_profile_changed')
    except Exception as exc:
        after = None
        errors.append('final_status:' + type(exc).__name__)
    config_after = core_pid_after = None
    try:
        config_after = hashlib.sha256((docs/'host/bmi30_config.json').read_bytes()).hexdigest()
        core_pid_after = int(subprocess.check_output(
            ['systemctl', 'show', 'bmi30-core', '-p', 'MainPID', '--value'], text=True).strip())
    except Exception as exc:
        errors.append('final_identity:' + type(exc).__name__)
    summary = dict(counts, errors=errors,
                   acquisition_only=acquisition_only, recording_mode=recording_mode,
                   acquisition_profile=acquisition_profile,
                   acquisition_stream_advanced=all(count >= 2 for count in check.frames),
                   duration_s=capture_ended-capture_started if capture_started is not None else 0,
                   total_elapsed_s=time.monotonic()-preflight_started,
                   kernel_dropped=kernel_dropped, queued_at_end=queued_at_end,
                   monitor_restored=monitor_restored, wire=check.summary(),
                   config_sha256_before=metadata['config_sha256'], config_sha256_after=config_after,
                   config_unchanged=config_after==metadata['config_sha256'],
                   core_pid_before=metadata['core_pid'], core_pid_after=core_pid_after,
                   core_process_unchanged=core_pid_after==metadata['core_pid'],
                   max_status_gap_s=timing['max_status_gap_s'],
                   pass_marking_ready=marking['ready'], marked_passes=marking['finished'],
                   invalid_marks=marking['invalid'], unlabelled_passes=marking['unlabelled'],
                   incomplete_pass=marking['active'],
                   ground_truth_complete=bool(marking['ready'] and marking['finished']
                       and not marking['invalid'] and marking['active'] is None
                       and not marking['unlabelled']
                       and (not options.get('ring') or ring_result.get('ring_input_complete'))
                       and not counts['control_errors']),
                   ring_requested=bool(options.get('ring')), ring=ring_result,
                   settings_unchanged=after is not None and settings_fingerprint(before)==settings_fingerprint(after),
                   changed_device_commands=False, exact_replay=False)
    summary['capture_complete'] = capture_complete(summary)
    # End is never dropped silently: this is outside the USB reader and after cleanup.
    outgoing.put({'kind': 'end', 'at': time.time(), 'monotonic': time.monotonic(),
                  'observer_complete': summary['capture_complete'],
                  'summary': summary, 'status': after}, timeout=10)
    outgoing.put(None, timeout=10)
    writer_thread.join(timeout=15)
    if writer_thread.is_alive():
        raise RuntimeError('Output did not drain; capture is incomplete')

