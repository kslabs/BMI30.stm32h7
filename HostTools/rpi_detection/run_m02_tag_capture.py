"""Save M02 waveform/status snapshots and T33 marks in a new local session folder.

Uses the existing pinned, authenticated M02 SSH transport. No installation,
detector configuration change, service restart, or Player Recorder commands.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def capture_summary(counts, remote_end, error, archive_closed, returncode):
    """Report transport/archive closure separately from observational usability."""
    transport_complete = bool(remote_end and error is None and returncode == 0)
    errors = {kind: count for kind, count in counts.items()
              if count and (kind.endswith('_error') or kind == 'fatal')}
    observation_complete = bool(transport_complete and not errors and
        remote_end.get('observation_complete', remote_end.get('observer_complete', False)))
    marking_complete = bool(transport_complete and remote_end.get('pass_marking_complete', False))
    return dict(transport_complete=transport_complete,
                archive_complete=bool(transport_complete and archive_closed),
                observation_complete=observation_complete,
                pass_marking_complete=marking_complete,
                observer_finished=bool(observation_complete and marking_complete and
                                       remote_end.get('observer_complete', False)),
                recorded_errors=errors, exact_raw_replay=False,
                counts=counts, error=error, remote_end=remote_end)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=1800)
    parser.add_argument('--tag')
    parser.add_argument('--distance-cm', type=float)
    parser.add_argument('--orientation')
    parser.add_argument('--ring-delivery-verified', action='store_true',
                        help='Assert a fresh counted center/up/down delivery check on this instrument; '
                             'pairing or historical calibration alone does not verify delivery')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--stop', type=Path, help='Request clean stop of this session directory')
    args = parser.parse_args()
    if args.stop:
        if not (args.stop / 'session.json').is_file():
            parser.error('Expected an existing capture session directory')
        (args.stop / 'STOP').write_text('stop\n', encoding='ascii')
        print('Stop requested: ' + str(args.stop))
        return
    if not 1 <= args.seconds <= 7200:
        parser.error('seconds must be 1..7200')
    if args.distance_cm is not None and args.distance_cm <= 0:
        parser.error('distance must be positive')
    calibration_path = HERE / 'T33_S10_CALIBRATION_20261002.json'
    calibration = json.loads(calibration_path.read_text(encoding='utf-8-sig'))
    if calibration.get('status') != 'verified' or calibration.get('mac') != 'FF:24:01:26:A0:4A':
        parser.error('Expected verified calibration for the identified T33')
    actions = {'center': 'start', 'up': 'hit', 'down': 'miss'}
    mapping = {calibration['button_gestures'][button]: action for button, action in actions.items()}
    if len(mapping) != 3:
        parser.error('Three distinct calibrated gestures are required')
    options = dict(seconds=args.seconds, tag=args.tag, distance_cm=args.distance_cm,
                   orientation=args.orientation, gesture_mapping=mapping,
                   ring_delivery_verified=args.ring_delivery_verified,
                   calibration={'file': calibration_path.name, 'verified_on': calibration['hostname'],
                                'mac': calibration['mac'],
                                'm02_delivery_check': 'operator_verified' if args.ring_delivery_verified else 'pending'})
    try:
        now = datetime.datetime.now(ZoneInfo('Europe/Prague'))
    except ZoneInfoNotFoundError:
        # Windows does not bundle the IANA timezone database with Python.
        powershell = Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        stamp = subprocess.check_output([str(powershell), '-NoProfile', '-Command',
            "[TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow,'Central Europe Standard Time').ToString('o')"],
            text=True).strip()
        now = datetime.datetime.fromisoformat(stamp)
    output = args.output or HERE / 'recordings' / now.strftime('%Y-%m-%d') / ('M02-upper-' + now.strftime('%H%M%S'))
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    (output / 'calibration.json').write_bytes(calibration_path.read_bytes())
    sources = {}
    for name in ('ring_gestures', 'remote_pass_logger', 'remote_tag_capture'):
        sources[name] = (HERE / (name + '.py')).read_text(encoding='utf-8')
        (output / (name + '.py')).write_text(sources[name], encoding='utf-8')
    bootstrap = 'import json,sys,types\n'
    for name, source in sources.items():
        bootstrap += f'm=types.ModuleType({name!r});sys.modules[{name!r}]=m;exec(compile({source!r},{name!r},"exec"),m.__dict__)\n'
    bootstrap += f'sys.modules["remote_tag_capture"].run({options!r})\n'
    payload = bootstrap.encode()
    # Reuse the pinned known-host entry already used for M02 deployments.
    sys.path.insert(0, str(ROOT / '.tmp/new-device-20261009'))
    from device_remote import ARGS
    loader = ('import os\nlength=b""\n'
              'while not length.endswith(b"\\n"): length+=os.read(0,1)\n'
              'n=int(length); source=b""\n'
              'while len(source)<n: source+=os.read(0,n-len(source))\n'
              'exec(compile(source,"capture_bundle","exec"))\n')
    import shlex
    command = ARGS + ['nice -n 10 sudo -n python3 -u -c ' + shlex.quote(loader)]
    metadata = dict(hostname='BMI30-E29805C7C', serial='490df72e29805c7c', channel='upper',
                    started_local=now.isoformat(), options=options,
                    source_sha256={k: hashlib.sha256(v.encode()).hexdigest() for k, v in sources.items()},
                    archive='capture.jsonl.gz', stop_file='STOP', format_version=1)
    (output / 'session.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'session_directory': str(output), 'duration_s': args.seconds}), flush=True)
    stopped = threading.Event()
    started = time.monotonic()
    process = None
    ended = False
    last = None
    error = None
    counts = {}
    archive_closed = False
    returncode = None
    remote_end = None
    try:
        with (output / 'ssh-stderr.txt').open('wb') as stderr:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
            process.stdin.write(str(len(payload)).encode() + b'\n' + payload)
            process.stdin.flush()

            def control():
                while not stopped.wait(.25):
                    if (output / 'STOP').exists() or time.monotonic()-started > args.seconds + 20:
                        try:
                            process.stdin.write(b'STOP\n')
                            process.stdin.flush()
                        except (OSError, ValueError):
                            pass
                        return

            threading.Thread(target=control, daemon=True).start()
            last_progress = 0
            with (output / 'events.jsonl').open('w', encoding='utf-8') as events:
                with gzip.open(output / 'capture.jsonl.gz', 'xb', compresslevel=1) as archive:
                    for line in process.stdout:
                        archive.write(line)
                        record = json.loads(line)
                        last = record
                        kind = record['kind']
                        counts[kind] = counts.get(kind, 0) + 1
                        if time.monotonic()-last_progress >= 1:
                            archive.flush()
                            progress = dict(counts=counts, last_remote_epoch=record.get('epoch'),
                                            elapsed_s=time.monotonic()-started)
                            pending = output / 'progress.tmp'
                            pending.write_text(json.dumps(progress)+'\n', encoding='utf-8')
                            os.replace(pending, output / 'progress.json')
                            last_progress = time.monotonic()
                        if kind not in ('sample', 'waveform', 'input'):
                            archive.flush()
                            events.write(line.decode())
                            events.flush()
                            brief = {k: v for k, v in record.items() if k not in ('initial', 'final')}
                            print(json.dumps(brief), flush=True)
                        if kind == 'end':
                            ended = True
                            remote_end = record
            archive_closed = True
            returncode = process.wait(timeout=10)
            if returncode or not ended:
                raise RuntimeError('Capture did not finish normally; inspect ssh-stderr.txt')
    except BaseException as exception:
        error = repr(exception)
        raise
    finally:
        stopped.set()
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
        summary = capture_summary(counts, remote_end, error, archive_closed, returncode)
        summary['duration_s'] = time.monotonic()-started
        archive_path = output / 'capture.jsonl.gz'
        if archive_path.exists():
            digest = hashlib.sha256()
            with archive_path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(chunk)
            summary['archive_sha256'] = digest.hexdigest()
            summary['archive_bytes'] = archive_path.stat().st_size
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'session_directory': str(output), 'observer_finished': summary['observer_finished'],
                          'counts': counts, 'error': error}), flush=True)


if __name__ == '__main__':
    main()
