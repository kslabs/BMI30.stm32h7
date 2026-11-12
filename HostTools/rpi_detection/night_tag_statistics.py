"""Headless M02 no-tag statistics with balanced confirmation trials and rollback.

Executed by a temporary systemd unit on M02. Observes the HTTP waveform/status
API, changes only runtime confirm0, rotates compressed blocks and restores the
original count. The unit also invokes --restore-only as ExecStopPost.
"""
import argparse
import collections
import copy
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

HOSTNAME = 'BMI30-E29805C7C'
SERIAL = '490df72e29805c7c'
CONFIG = Path('/home/techaid/Documents/host/bmi30_config.json')
CHANNEL_KEYS = ('enabled', 'confirm_count', 'threshold', 'threshold_high', 'auto_threshold',
                'noise_up', 'noise_down', 'noise_unit', 'burst_gate', 'burst_blank_s', 'burst_max_ratio')
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def atomic(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(temporary, path)


def identity():
    assert socket.gethostname() == HOSTNAME, 'Unexpected hostname'
    assert Path('/proc/device-tree/serial-number').read_text().strip('\0\n') == SERIAL, 'Unexpected serial'


def status():
    for attempt in range(3):
        try:
            with HTTP.open('http://127.0.0.1:8765/api/status', timeout=3) as reply:
                return json.load(reply)
        except OSError:
            if attempt == 2:
                raise
            time.sleep(.3)


def static(s):
    return dict(mode=s['mode'], settings=s['detector']['settings'],
                channels={name:{key:value for key,value in row.items() if key in CHANNEL_KEYS}
                          for name,row in s['detector']['channels'].items()})


def core_pid():
    return subprocess.check_output(['systemctl', 'show', 'bmi30-core', '-p', 'MainPID', '--value'], text=True).strip()


def config_hash():
    return hashlib.sha256(CONFIG.read_bytes()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def set_confirm(value):
    body = json.dumps({'action':'runtime_detector_settings', 'params':{'confirm0':value}}).encode()
    request = urllib.request.Request('http://127.0.0.1:8765/api/command', data=body,
                                     headers={'Content-Type':'application/json'})
    with HTTP.open(request, timeout=8) as reply:
        result = json.load(reply)
    assert result.get('ok') and result.get('persisted') is False, result


def restore(directory, reason):
    identity()
    baseline_path = directory/'baseline.json'
    if not baseline_path.exists():
        return {'restored':False, 'reason':'No baseline; no runtime change was attempted'}
    baseline = json.loads(baseline_path.read_text())
    result = dict(epoch=time.time(), reason=reason, restored=False)
    try:
        current = status()
        count = current['detector']['channels']['upper']['confirm_count']
        same_process = core_pid() == baseline['core_pid']
        same_file = config_hash() == baseline['config_sha256']
        original = baseline['static']['channels']['upper']['confirm_count']
        expected = copy.deepcopy(baseline['static'])
        expected['channels']['upper']['confirm_count'] = count
        if count == original:
            result.update(restored=True, already_original=True)
        elif count in (3,4,6) and same_process and same_file and static(current) == expected:
            set_confirm(original)
            assert status()['detector']['channels']['upper']['confirm_count'] == original
            result.update(restored=True, already_original=False)
        else:
            result['error'] = 'External runtime/configuration change preserved; this job no longer owns confirm0'
        result.update(original_confirm_count=original, observed_confirm_count=count,
                      same_core_pid=same_process, persistent_config_unchanged=same_file,
                      final_status=status())
        result['all_static_settings_restored'] = static(result['final_status']) == baseline['static']
    except Exception as error:
        result['error'] = repr(error)
    atomic(directory/('restore-post.json' if reason == 'ExecStopPost' else 'restore.json'), result)
    if not result['restored']:
        raise RuntimeError(result.get('error', 'Restoration failed'))
    return result


def run_block(directory, job, number, count, seconds, stop, baseline):
    block = directory/'blocks'/f'{number:03d}-confirm{count}'
    block.mkdir(parents=True, exist_ok=False)
    before = status()
    expected = copy.deepcopy(baseline['static'])
    expected['channels']['upper']['confirm_count'] = before['detector']['channels']['upper']['confirm_count']
    assert static(before) == expected and core_pid() == baseline['core_pid'], 'External runtime change'
    assert config_hash() == baseline['config_sha256'], 'Persistent configuration changed'
    if before['detector']['channels']['upper']['confirm_count'] != count:
        set_confirm(count)
    expected['channels']['upper']['confirm_count'] = count
    assert static(status()) == expected, 'Unexpected profile after runtime update'
    options = dict(seconds=seconds, tag='NO_TAG_CONTROL', distance_cm=None, orientation=None,
                   gesture_mapping={'tap':'start','y-':'hit','y+':'miss'}, calibration=None,
                   read_ring=False, stdin_control=False)
    condition = dict(test_type='no_tag_control', tag_present=False, tag_removed_confirmed_by_user=True,
                     comparison_tag='B51(250/74)', antenna_spacing_cm=110, passage_location='between_antennas',
                     upper_confirm_count=count, warmup_exclude_s=min(10,max(0,seconds-1)),
                     evaluation_duration_s=max(0,seconds-10), block_number=number,
                     note='Count all events including transition; report the first ten seconds separately.')
    atomic(block/'test-conditions.json', condition)
    atomic(block/'session.json', dict(hostname=HOSTNAME, serial=SERIAL, channel='upper',
        upper_confirm_count=count, requested_duration_s=seconds, observer_options=options,
        preceding_status=before, archive='capture.jsonl.gz'))
    source = 'import remote_tag_capture;remote_tag_capture.run('+repr(options)+')'
    stderr = (block/'observer-stderr.txt').open('wb')
    child = subprocess.Popen([sys.executable,'-u','-c',source], cwd=Path(__file__).parent,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr)
    block_stop = threading.Event()
    started = time.monotonic()
    seen = {'last_line':started}
    faults = []
    counts = collections.Counter()
    first = last_sample = end = None
    warmup_sample = None
    last_check = started
    size_at_start = sum(p.stat().st_size for p in directory.rglob('*.gz'))

    def watch():
        while not block_stop.wait(.5):
            try:
                disk = os.statvfs(directory)
                available = disk.f_bavail*disk.f_frsize
                current_bytes = sum(p.stat().st_size for p in directory.rglob('*.gz'))
                if (directory/'STOP').exists():
                    faults.append('stop_file');stop.set()
                if time.time() >= job['end_epoch']:
                    stop.set()
                if available < job['reserve_bytes'] or current_bytes >= job['max_bytes']:
                    faults.append('disk_budget_or_reserve');stop.set()
                if time.monotonic()-seen['last_line'] > 15:
                    faults.append('observer_output_stalled');stop.set()
                if stop.is_set():
                    child.terminate()
                    return
            except Exception as error:
                faults.append(repr(error));stop.set();child.terminate();return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        with gzip.open(block/'capture.jsonl.gz','xb',compresslevel=1) as archive, (block/'events.jsonl').open('wb') as events:
            flushed = 0
            for line in child.stdout:
                seen['last_line'] = time.monotonic()
                archive.write(line)
                row = json.loads(line)
                counts[row['kind']] += 1
                if row['kind'] == 'start':
                    first = row
                elif row['kind'] == 'sample':
                    last_sample = row
                    if first and warmup_sample is None and row['observed_mono'] >= first['observed_mono']+10:
                        warmup_sample = row
                elif row['kind'] == 'end':
                    end = row
                if row['kind'] not in ('sample','waveform'):
                    events.write(line);events.flush()
                if time.monotonic()-flushed >= 2:
                    archive.flush()
                    progress = dict(state='recording', epoch=time.time(), block=number, confirm0=count,
                        block_elapsed_s=time.monotonic()-started, planned_end_epoch=job['end_epoch'], counts=dict(counts),
                        bytes_before_block=size_at_start, current_archive_bytes=(block/'capture.jsonl.gz').stat().st_size,
                        last_status=last_sample['status'] if last_sample else None)
                    atomic(directory/'progress.json', progress)
                    flushed = time.monotonic()
                if time.monotonic()-last_check >= 5:
                    assert core_pid() == baseline['core_pid'] and config_hash() == baseline['config_sha256'], 'Core or saved settings changed'
                    assert static(status()) == expected, 'External profile edit'
                    last_check = time.monotonic()
        rc = child.wait(timeout=10)
        assert rc == 0 and end is not None, f'Observer failed: rc={rc}'
        assert not end.get('queue_drops'), 'Observer queue overflow'
    finally:
        block_stop.set()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill();child.wait(timeout=5)
        watcher.join(timeout=2)
        stderr.close()
    final = end['final']['status']
    after_warmup = warmup_sample['status'] if warmup_sample else None
    total_seconds = max(.001,time.monotonic()-started)
    result = dict(block=number, confirm0=count, started_epoch=first['epoch'], ended_epoch=end['epoch'],
        duration_s=total_seconds, recording_complete=True, observer_counts=dict(counts), faults=faults,
        total_upper_events_including_transition=final['detector']['fire_seq0']-before['detector']['fire_seq0'],
        total_lower_events_including_transition=final['detector']['fire_seq1']-before['detector']['fire_seq1'],
        steady_duration_s=max(0,end['observed_mono']-warmup_sample['observed_mono']) if warmup_sample else 0,
        steady_upper_events=final['detector']['fire_seq0']-after_warmup['detector']['fire_seq0'] if after_warmup else None,
        steady_lower_events=final['detector']['fire_seq1']-after_warmup['detector']['fire_seq1'] if after_warmup else None,
        archive_bytes=(block/'capture.jsonl.gz').stat().st_size,
        archive_sha256=file_hash(block/'capture.jsonl.gz'),
        core_pid_unchanged=end['final']['core_pid']==baseline['core_pid'],
        persistent_config_unchanged=config_hash()==baseline['config_sha256'])
    result['upper_events_per_hour_including_transition'] = result['total_upper_events_including_transition']*3600/total_seconds
    result['transport_deltas'] = {key:final['stream_stats'][key]-before['stream_stats'][key]
        for key in ('seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail')}
    result['worker_overflow_delta'] = final['detector']['worker']['overflows']-before['detector']['worker']['overflows']
    atomic(block/'summary.json', result)
    return result


def aggregate(blocks):
    rows = []
    for count in (3,4,6):
        group = [row for row in blocks if row['confirm0']==count]
        duration = sum(row['duration_s'] for row in group)
        events = sum(row['total_upper_events_including_transition'] for row in group)
        steady = sum(row['steady_duration_s'] for row in group)
        steady_events = sum(row['steady_upper_events'] or 0 for row in group)
        rows.append(dict(confirm0=count, blocks=len(group), recorded_hours=duration/3600,
            upper_events=events, upper_events_per_hour=events*3600/duration if duration else None,
            steady_hours=steady/3600, steady_upper_events=steady_events,
            steady_upper_events_per_hour=steady_events*3600/steady if steady else None))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--restore-only', action='store_true')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.restore_only:
        print(json.dumps(restore(directory,'ExecStopPost')))
        return
    identity()
    job = json.loads((directory/'job.json').read_text())
    assert job['tag_absent_confirmed'] is True
    assert 1 <= job['block_seconds'] <= 1800
    assert time.time() < job['end_epoch']
    initial = status()
    assert initial['ok'] and not initial['stale'] and initial['detector']['active']
    assert not initial['player_recorder']['recording'] and not initial['player_recorder']['playback_active']
    assert initial['detector']['channels']['upper']['confirm_count'] == 6
    assert initial['detector']['worker']['alive']
    assert subprocess.check_output(['findmnt','-no','SOURCE','/'],text=True).strip() == job['expected_root']
    assert config_hash() == job['expected_config_sha256']
    engine_path = Path(initial['service']['engine_source'])
    assert str(engine_path) == job['expected_engine_path']
    assert file_hash(engine_path) == job['expected_engine_sha256']
    baseline = dict(epoch=time.time(), hostname=HOSTNAME, serial=SERIAL, core_pid=core_pid(),
                    config_sha256=config_hash(), static=static(initial), status=initial)
    atomic(directory/'baseline.json', baseline)
    stop = threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
        signal.signal(sig,lambda *_:stop.set())
    blocks=[]
    failure=None
    try:
        while not stop.is_set() and time.time() < job['end_epoch']:
            number=len(blocks)+1
            # Each 90-minute cycle contains all profiles. Rotate order between cycles.
            cycle=(number-1)//3
            order=(6,4,3)
            count=order[((number-1)%3+cycle)%3]
            seconds=min(job['block_seconds'],job['end_epoch']-time.time())
            if seconds < 1:
                break
            result=run_block(directory,job,number,count,seconds,stop,baseline)
            blocks.append(result)
            atomic(directory/'results.json',dict(state='running',epoch=time.time(),blocks=blocks,
                profiles=aggregate(blocks),planned_end_epoch=job['end_epoch']))
    except Exception as error:
        failure=repr(error)
        raise
    finally:
        stop.set()
        restored=restore(directory,'supervisor_finally')
        result=dict(state='complete' if failure is None and time.time()>=job['end_epoch']-2 else 'stopped',
            epoch=time.time(),error=failure,blocks=blocks,profiles=aggregate(blocks),restoration=restored,
            planned_end_epoch=job['end_epoch'],data_bytes=sum(p.stat().st_size for p in directory.rglob('*.gz')))
        atomic(directory/'results.json',result)
        atomic(directory/'progress.json',dict(state=result['state'],epoch=time.time(),error=failure,
            completed_blocks=len(blocks),restored=restored['restored'],planned_end_epoch=job['end_epoch']))


if __name__=='__main__':
    main()
