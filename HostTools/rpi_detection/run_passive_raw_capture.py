"""Stream a guarded passive capture through existing pinned M02/S07 SSH routes.

Use --output NEW_DIRECTORY --expect-host BMI30-E29805C7C
--expect-serial 490df72e29805c7c --expect-root /dev/mmcblk0p2 --seconds 60.
Append marks with --control DIRECTORY --action pass_start --pass-id 1, then
--control DIRECTORY --action pass_end --pass-id 1 --result hit (or miss).
Those commands describe the default computer input route. For T33 on S07,
--ring-mode calibrate observes input without passage marks. --ring-mode mark
requires fresh physically confirmed calibration and its verification session.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time
import uuid

from passive_raw_capture import AVERAGE_DEPTHS, WireCheck, _acquisition_profile, _healthy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def transport_path(expected_host, expected_serial):
    """A caller cannot choose an address or silently route an unknown board."""
    routes = {'BMI30-E29805C7C': ('490df72e29805c7c', '.tmp/new-device-20261009/device_remote.py'),
              'BMI30-C9775FBAE': ('abdd2dbc9775fbae', '.tmp/tx-phase-repair-20261012/s07_remote.py')}
    if expected_host not in routes:
        raise ValueError('no verified passive-capture SSH route for ' + str(expected_host))
    serial, relative_path = routes[expected_host]
    if expected_serial != serial:
        raise ValueError('serial does not match the verified ' + expected_host + ' route')
    return ROOT/relative_path


def operator_event(record):
    """Small receipt sidecar; preserve remote timestamps and mark fields."""
    kind = record.get('kind', '')
    if not kind.startswith('ring_') and kind not in ('ready', 'input_ready', 'pass_start', 'pass_end',
                                  'orphan_pass_end', 'ignored_mark', 'note', 'end', 'control_error'):
        return None
    return {key: value for key, value in record.items() if key not in ('status', 'data')}


def operator_heartbeat(record):
    if record.get('kind') not in ('ready', 'status'):
        return None
    acquisition_only = bool(record.get('acquisition_only', record.get('metadata', {}).get('acquisition_only', False)))
    return {'kind': 'heartbeat', 'at': record.get('at'), 'monotonic': record.get('monotonic'),
            'updated_local_at': time.time(), 'healthy': bool(_healthy(record.get('status', {}), acquisition_only)),
            'acquisition_only': acquisition_only,
            'acquisition_profile': _acquisition_profile(record.get('status', {})) if acquisition_only else None,
            'recording_mode': 'acquisition_only' if acquisition_only else 'live_detection'}


def build_payload(source, options, dependencies):
    """Compile each streamed module separately so future imports remain valid."""
    bootstrap = 'import sys,types\n'
    for name, dependency in dependencies.items():
        bootstrap += (f'm=types.ModuleType({name!r});m.__file__={name+".py"!r};sys.modules[{name!r}]=m;'
                      f'exec(compile({dependency!r},{name!r},"exec"),m.__dict__)\n')
    return (bootstrap+f'exec(compile({source!r},"passive_raw_capture","exec"),globals())\n'
            + 'run(' + repr(options) + ')\n').encode('utf-8')


def verify_archive(path):
    counts = {}
    end = None
    ready = False
    probe = None
    errors = []
    wire = None
    acquisition_only = False
    acquisition_profile = None
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if end is not None:
                errors.append('record_after_end')
            record = json.loads(line)
            kind = record['kind']
            counts[kind] = counts.get(kind, 0) + 1
            if kind == 'ready':
                ready = record.get('ready') is True
                acquisition_only = bool(record.get('metadata', {}).get('acquisition_only', False))
                acquisition_profile = _acquisition_profile(record.get('status', {})) if acquisition_only else None
                expected_average = record.get('metadata', {}).get('expected_average')
                if expected_average is not None and acquisition_profile!='averaged_roi_'+str(expected_average):
                    errors.append('acquisition_average_does_not_match_requested_depth')
                if acquisition_only and not _healthy(record.get('status', {}), True):
                    errors.append('acquisition_ready_unhealthy')
                sample_count = record.get('status', {}).get('mode', {}).get('base_buf_len')
                if isinstance(sample_count, int) and 1 <= sample_count <= 8192:
                    wire = WireCheck(sample_count)
                else:
                    errors.append('missing_sample_count')
            if kind == 'status' and acquisition_only and not _healthy(record.get('status', {}), True):
                errors.append('acquisition_status_unhealthy')
            elif kind == 'status' and acquisition_only and _acquisition_profile(record.get('status', {})) != acquisition_profile:
                errors.append('acquisition_profile_changed')
            if kind == 'input_ready' and record.get('probe') is not None:
                probe = record.get('probe')
            if kind == 'end':
                end = record
            if kind == 'transfer':
                if wire is None:
                    errors.append('transfer_before_ready')
                else:
                    data = base64.b64decode(record['data'], validate=True)
                    if len(data) != record.get('captured'):
                        errors.append('transfer_length_mismatch')
                    wire.feed(data)
    summary = end.get('summary', {}) if end else {}
    if acquisition_only:
        if summary.get('acquisition_only') is not True:
            errors.append('acquisition_mode_summary_mismatch')
        if end and not _healthy(end.get('status') or {}, True):
            errors.append('acquisition_final_status_unhealthy')
        elif end and _acquisition_profile(end.get('status') or {}) != acquisition_profile:
            errors.append('acquisition_final_profile_changed')
    if counts.get('ready') != 1 or counts.get('end') != 1:
        errors.append('missing_or_repeated_boundary_records')
    if counts.get('transfer', 0) != summary.get('transfers', 0):
        errors.append('transfer_count_mismatch')
    if counts.get('status', 0) != summary.get('status_samples', 0):
        errors.append('status_count_mismatch')
    if wire is None or wire.summary() != summary.get('wire'):
        errors.append('wire_integrity_readback_mismatch')
    return {'record_counts': counts, 'ready_seen': ready, 'end_seen': end is not None,
            'acquisition_only': acquisition_only,
            'acquisition_profile': acquisition_profile,
            'recording_mode': 'acquisition_only' if acquisition_only else 'live_detection',
            'remote_summary': summary, 'mark_probe': probe, 'readback_errors': errors,
            'wire_readback': wire.summary() if wire else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--acquisition-only', action='store_true',
                        help='Explicit LATEST600, LOSSLESS_ROI200, or mode5 AVG_ROI200 with supported N and Raspberry depth1 '
                             'recording without requiring live detection; '
                             'both USB channels must still advance with valid CRC/sequence')
    parser.add_argument('--expect-average', type=int, choices=AVERAGE_DEPTHS,
                        help='Acquisition-only AVG_ROI: require this actual snapshot depth before starting')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--expect-host')
    parser.add_argument('--expect-serial')
    parser.add_argument('--expect-root')
    parser.add_argument('--tag')
    parser.add_argument('--channel', choices=('upper', 'lower'))
    parser.add_argument('--distance-cm', type=float)
    parser.add_argument('--orientation')
    parser.add_argument('--ring-mode', choices=('calibrate', 'mark'))
    parser.add_argument('--ring-device')
    parser.add_argument('--ring-calibration', type=Path)
    parser.add_argument('--ring-verification-session')
    parser.add_argument('--control', type=Path)
    parser.add_argument('--action', choices=('pass_start', 'pass_end', 'note', 'stop'))
    parser.add_argument('--pass-id', type=int)
    parser.add_argument('--result', choices=('hit', 'miss', 'unlabelled'))
    parser.add_argument('--text')
    args = parser.parse_args()
    if args.control:
        if not args.action or not (args.control/'session.json').is_file():
            parser.error('control needs an existing session directory and an action')
        if args.action in ('pass_start', 'pass_end') and args.pass_id is None:
            parser.error('pass marks require --pass-id')
        if args.action == 'pass_end' and not args.result:
            parser.error('pass_end requires --result')
        command = {k: v for k, v in {'action': args.action, 'pass_id': args.pass_id,
                   'result': args.result, 'text': args.text}.items() if v is not None}
        # A single append write keeps one complete line together.
        with (args.control/'commands.jsonl').open('ab', buffering=0) as stream:
            stream.write((json.dumps(command)+'\n').encode())
        print(json.dumps({'command_submitted': command, 'remote_receipt': 'pending'}))
        return 0
    if not all((args.output, args.expect_host, args.expect_serial, args.expect_root)):
        parser.error('capture requires --output, --expect-host, --expect-serial and --expect-root')
    if not 1 <= args.seconds <= 7200:
        parser.error('seconds must be in 1..7200')
    if args.expect_average is not None and not args.acquisition_only:
        parser.error('--expect-average requires --acquisition-only')
    if args.distance_cm is not None and not args.distance_cm > 0:
        parser.error('distance-cm must be positive')
    try:
        transport = transport_path(args.expect_host, args.expect_serial)
    except ValueError as exc:
        parser.error(str(exc))
    spec = importlib.util.spec_from_file_location('capture_transport', transport)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    conditions = {k: v for k, v in {'tag': args.tag, 'channel': args.channel,
                  'distance_cm': args.distance_cm, 'orientation': args.orientation}.items() if v is not None}
    session_id = str(uuid.uuid4())
    options = {'seconds': args.seconds, 'expect_host': args.expect_host,
               'acquisition_only': args.acquisition_only,
               'expect_average': args.expect_average,
               'expect_serial': args.expect_serial, 'expect_root': args.expect_root,
               'test_conditions': conditions, 'session_id': session_id}
    dependencies = {}
    if args.ring_mode:
        if (args.expect_host, args.expect_serial) != ('BMI30-C9775FBAE', 'abdd2dbc9775fbae'):
            parser.error('T33 capture is currently verified only for the identified S07')
        if not args.ring_device:
            parser.error('ring capture requires the identified --ring-device')
        if args.ring_mode == 'mark' and not all((args.ring_calibration, args.ring_verification_session,
                                                args.tag, args.channel, args.distance_cm)):
            parser.error('ring marking requires fresh calibration/session, tag, channel and distance')
        if args.ring_mode == 'calibrate' and any((args.ring_calibration, args.ring_verification_session)):
            parser.error('ring calibration observes input only; do not supply a prior calibration')
        ring = {'mode': args.ring_mode, 'device': args.ring_device, 'mac': 'FF:24:01:26:A0:4A',
                'hostname': args.expect_host, 'serial': args.expect_serial, 'session_id': session_id}
        if args.ring_mode == 'mark':
            from passive_ring_input import validate_verification
            try:
                ring['calibration'] = json.loads(args.ring_calibration.read_text(encoding='utf-8-sig'))
                validate_verification(ring['calibration'], args.ring_verification_session)
            except (OSError, ValueError, TypeError) as exc:
                parser.error('ring calibration is not valid for this S07 session: '+str(exc))
            ring['verification_session_id'] = args.ring_verification_session
        options['ring'] = ring
        for name in ('ring_gestures', 'remote_pass_logger', 'passive_ring_input'):
            dependencies[name] = (HERE/(name+'.py')).read_text(encoding='utf-8')
    elif any((args.ring_device, args.ring_calibration, args.ring_verification_session)):
        parser.error('ring options require explicit --ring-mode')
    source = (HERE/'passive_raw_capture.py').read_text(encoding='utf-8')
    payload = build_payload(source, options, dependencies)
    loader = ('import os\nlength=b""\n'
              'while not length.endswith(b"\\n"): length+=os.read(0,1)\n'
              'n=int(length); source=b""\n'
              'while len(source)<n: source+=os.read(0,n-len(source))\n'
              'exec(compile(source,"passive_raw_capture","exec"))\n')
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    probe = uuid.uuid4().hex
    (output/'commands.jsonl').write_text(json.dumps({'action': 'mark_check', 'probe': probe})+'\n', encoding='utf-8')
    metadata = {'format': 'bmi30-passive-wire-v1', 'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'acquisition_only': args.acquisition_only,
                'recording_mode': 'acquisition_only' if args.acquisition_only else 'live_detection',
                'session_id': session_id,
                'options': options, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                'dependency_sha256': {name:hashlib.sha256(value.encode()).hexdigest() for name,value in dependencies.items()},
                'exact_replay': False, 'marking_source': 'ring' if args.ring_mode else 'control_file',
                'mark_probe': probe,
                'test_conditions': conditions, 'test_conditions_source': 'user_supplied'}
    (output/'session.json').write_text(json.dumps(metadata, indent=2)+'\n', encoding='utf-8')
    (output/'passive_raw_capture.py').write_text(source, encoding='utf-8')
    for name, dependency in dependencies.items():
        (output/(name+'.py')).write_text(dependency, encoding='utf-8')
    archive = output/'capture.jsonl.gz'
    stop_control = threading.Event()
    local_errors = []
    process = None
    timed_out = threading.Event()
    result = {}
    try:
        with (output/'ssh-stderr.txt').open('wb') as stderr:
            process = subprocess.Popen(module.ARGS + ['nice -n 10 sudo -n python3 -u -c ' + shlex.quote(loader)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
            process.stdin.write(str(len(payload)).encode()+b'\n'+payload)
            process.stdin.flush()

            def controls():
                try:
                    with (output/'commands.jsonl').open('rb') as stream:
                        pending = b''
                        while not stop_control.wait(.05):
                            pending += stream.read()
                            while b'\n' in pending:
                                line, pending = pending.split(b'\n', 1)
                                process.stdin.write(line+b'\n')
                                process.stdin.flush()
                except (OSError, ValueError) as exc:
                    local_errors.append('controls:' + type(exc).__name__)

            def watchdog():
                if not stop_control.wait(args.seconds+45):
                    timed_out.set()
                    try:
                        process.stdin.write(b'{"action":"stop"}\n')
                        process.stdin.flush()
                    except (OSError, ValueError):
                        pass
                    if not stop_control.wait(20):
                        process.terminate()

            threading.Thread(target=controls, daemon=True).start()
            threading.Thread(target=watchdog, daemon=True).start()
            with gzip.open(archive, 'xb', compresslevel=1) as stream, \
                    (output/'events.jsonl').open('x', encoding='utf-8') as events:
                last_heartbeat = 0.
                while True:
                    try:
                        line = process.stdout.readline()
                    except KeyboardInterrupt:
                        process.stdin.write(b'{"action":"stop"}\n')
                        process.stdin.flush()
                        continue
                    if not line:
                        break
                    stream.write(line)
                    record = json.loads(line)
                    event = operator_event(record)
                    if event is not None:
                        events.write(json.dumps(event, ensure_ascii=False)+'\n')
                        events.flush()
                    if record.get('kind') in ('ready', 'status') and time.monotonic()-last_heartbeat >= 1:
                        heartbeat = operator_heartbeat(record)
                        events.write(json.dumps(heartbeat, ensure_ascii=False)+'\n')
                        events.flush()
                        last_heartbeat = time.monotonic()
                    if record.get('kind') in ('ready', 'input_ready', 'pass_start', 'pass_end'):
                        # Do not dump full statuses or waveforms into the chat/terminal.
                        print(json.dumps({k: v for k, v in record.items() if k not in ('status',)},
                                         ensure_ascii=False), flush=True)
            code = process.wait(timeout=15)
            stop_control.set()
            result = verify_archive(archive)
            result.update(ssh_returncode=code, local_errors=local_errors,
                          timed_out=timed_out.is_set(), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                          archive_bytes=archive.stat().st_size,
                          control_channel_verified=result.get('mark_probe')==probe)
            result['mark_channel_verified'] = (result['control_channel_verified'] if not args.ring_mode
                else bool(args.ring_mode=='mark' and result['remote_summary'].get('pass_marking_ready')
                          and result['remote_summary'].get('ring', {}).get('ring_input_complete')))
            result['capture_complete'] = bool(code==0 and result['ready_seen'] and result['end_seen']
                    and not local_errors and not result['readback_errors'] and not timed_out.is_set()
                    and result['remote_summary'].get('capture_complete'))
    except BaseException as exc:
        local_errors.append(type(exc).__name__ + ':' + str(exc))
        result.update(capture_complete=False, local_errors=local_errors)
        if process is not None and process.poll() is None:
            try:
                process.stdin.write(b'{"action":"stop"}\n')
                process.stdin.flush()
                process.wait(timeout=15)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                process.terminate()
    finally:
        stop_control.set()
        (output/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'session': str(output), **result}, ensure_ascii=False), flush=True)
    return 0 if result.get('capture_complete') else 1


if __name__ == '__main__':
    raise SystemExit(main())
