"""Read-only checks of the installed M02 runtime and actual frozen events."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import time
import urllib.request

p = json.load(sys.stdin)
i, r = p['before'], p['release']
D = Path('/home/techaid/Documents')
A = D/'host/bmi30_active_runtime/project'
assert subprocess.check_output(['hostname'], text=True).strip() == i['hostname'] == 'BMI30-E29805C7C'
assert Path('/proc/device-tree/serial-number').read_text().strip('\0\n') == i['serial']
assert subprocess.check_output(['findmnt','-n','-o','SOURCE','/'], text=True).strip() == i['root'] == '/dev/sda2'
assert 'BMI30_SPLIT_VERSION='+r['version']+'\n' in (D/'host/bmi30_split_active_version.env').read_text()
for name, relative in r['paths'].items():
    assert hashlib.sha256((A/relative).read_bytes()).hexdigest() == r['hashes'][name], name


def get(path='/api/status', binary=False):
    with urllib.request.urlopen('http://127.0.0.1:8765'+path, timeout=5) as reply:
        return reply.read() if binary else json.load(reply)


def healthy(status):
    assert status['ok'] and not status['stale'] and status['connection']['connected']
    assert status['connection']['last_frame_age_s'] < 2
    assert status['detector']['worker']['alive']
    assert status['service']['engine_source'] == str(A/r['paths']['engine'])
    for key in ['selected','avg_n','rpi_avg_n','stream_mode','base_buf_len','freq_hz']:
        assert status['mode'][key] == i['status']['mode'][key], key
    assert status['detector']['settings'] == i['status']['detector']['settings']
    for ch in ['upper','lower']:
        for key in ['enabled','confirm_count','threshold','threshold_high','auto_threshold','noise_up','noise_down','noise_unit']:
            assert status['detector']['channels'][ch][key] == i['status']['detector']['channels'][ch][key], (ch,key)
    return status


before = healthy(get())
config = (D/'host/bmi30_config.json').read_bytes()
time.sleep(10)
after = healthy(get())
errors = ['seq_gap_ch0','seq_gap_ch1','seq_dup_ch0','seq_dup_ch1','crc_bad','magic_bad','host_rx_ack_fail']
deltas = {key:after['stream_stats'][key]-before['stream_stats'][key] for key in errors}
assert all(value == 0 for value in deltas.values()), deltas
worker0, worker1 = before['detector']['worker'], after['detector']['worker']
assert worker1['results'] > worker0['results']
assert worker1['overflows'] == worker0['overflows']
assert worker1['latency']['over_100ms'] == worker0['latency']['over_100ms']
live = get('/api/frame?max_points=200')
assert all(len(live[name]) == 200 for name in ['data0_even','data0_odd','data1_even','data1_odd','prod0','prod1'])
events = []
for ch in (0,1):
    seq = after['detector']['fire_seq'+str(ch)]
    if not seq:
        continue
    query = '?max_points=200&fire_seq'+str(ch)+'='+str(seq)
    frame = get('/api/frame'+query)
    assert frame['exact_event_frame'] and frame['exact_event_channel'] == ch
    assert frame['event_snapshot']['seq'] == seq
    lengths = {name:len(frame[name]) for name in ['data0_even','data0_odd','data1_even','data1_odd','prod0','prod1']}
    assert all(n == 200 for n in lengths.values()), lengths
    ref = frame['reference_snapshot'+str(1-ch)]
    assert ref['reference_only'] and not ref['fired']
    confirmations = [len(frame['prod_confirm'+str(k)]) for k in (0,1)]
    assert confirmations[0] == confirmations[1] == frame['confirmation_context_count']
    raw = get('/api/frame.bin'+query+'&channels=both', binary=True)
    header_size = struct.unpack_from('<I',raw,4)[0]
    header = json.loads(raw[8:8+header_size])
    assert all(name in header['arrays'] for name in lengths)
    assert len(raw) == ((8+header_size+1)&~1)+header['count']*2*len(header['arrays'])
    events.append({'channel':ch,'seq':seq,'lengths':lengths,'confirmations':confirmations,
                   'reference':ref,'event_snapshot':frame['event_snapshot']})
assert (D/'host/bmi30_config.json').read_bytes() == config
html = urllib.request.urlopen('http://127.0.0.1:8765/',timeout=5).read().decode()
assert "snap.synchronized?'SYNC ':'REF '" in html
print(json.dumps({'version':r['version'],'root':i['root'],'settings_preserved':True,
    'mode':after['mode'],'worker_results_delta':worker1['results']-worker0['results'],
    'transport_deltas':deltas,'overflow_delta':worker1['overflows']-worker0['overflows'],
    'over_100ms_delta':worker1['latency']['over_100ms']-worker0['latency']['over_100ms'],
    'events':events,'fire_sequences':[after['detector']['fire_seq0'],after['detector']['fire_seq1']]}))
