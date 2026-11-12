"""Read-only verification of the installed M02 mean/P95 noise estimator."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import urllib.request

p = json.load(sys.stdin)
r, expected = p['release'], p['before']['status']
D = Path('/home/techaid/Documents')
A = D / 'host/bmi30_active_runtime/project'
assert subprocess.check_output(['hostname'], text=True).strip() == 'BMI30-E29805C7C'
assert Path('/proc/device-tree/serial-number').read_text().strip('\0\n') == '490df72e29805c7c'
assert subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '/'], text=True).strip() == '/dev/sda2'
assert 'BMI30_SPLIT_VERSION=' + r['version'] + '\n' in (D / 'host/bmi30_split_active_version.env').read_text()
assert all(hashlib.sha256((A / rel).read_bytes()).hexdigest() == r['hashes'][name] for name, rel in r['paths'].items())
assert hashlib.sha256(Path('/usr/local/bin/bmi30-hotspot-info-server.py').read_bytes()).hexdigest() == r['hashes']['portal']


def get(path='/api/status'):
    return json.load(urllib.request.urlopen('http://127.0.0.1:8765' + path, timeout=5))


def validate(status):
    assert status['ok'] and not status['stale'] and status['connection']['connected']
    assert status['connection']['last_frame_age_s'] < 2
    assert status['detector']['worker']['alive'] and status['detector']['active']
    assert status['service']['engine_source'] == str(A / r['paths']['engine'])
    for key in ['selected', 'avg_n', 'rpi_avg_n', 'stream_mode', 'base_buf_len', 'freq_hz']:
        assert status['mode'][key] == expected['mode'][key], key
    det = status['detector']
    assert det['noise_estimator'] == det['settings']['noise_estimator'] == 'max_mean_p95'
    assert det['settings']['noise_percentile'] == 95.0
    assert all(det['settings'][key] == value for key, value in expected['detector']['settings'].items())
    for ch, name in enumerate(['upper', 'lower']):
        channel = det['channels'][name]
        for key in ['enabled', 'confirm_count', 'threshold', 'threshold_high', 'auto_threshold', 'noise_up', 'noise_down', 'noise_unit']:
            assert channel[key] == expected['detector']['channels'][name][key], (name, key)
        values = [float(det[key + str(ch)]) for key in ['noise_avg', 'noise_mean', 'noise_p95']]
        assert all(math.isfinite(value) and value >= 0 for value in values), (ch, values)
        assert math.isclose(values[0], max(values[1:]), rel_tol=1e-12, abs_tol=1e-9), (ch, values)
        assert det['noise_samples' + str(ch)] >= 50
        for key in ['noise_avg', 'noise_mean', 'noise_p95', 'noise_samples']:
            assert channel[key] == det[key + str(ch)], (name, key)
    return status


config = (D / 'host/bmi30_config.json').read_bytes()
for attempt in range(100):
    sample = get()
    if (sample['service']['uptime_s'] >= 25
            and min(sample['detector']['noise_samples0'], sample['detector']['noise_samples1']) >= 50):
        break
    time.sleep(.25)
else:
    raise RuntimeError('Noise histories did not fill after startup')
sample0 = validate(sample)
diagnostics = []
start = time.monotonic()
for attempt in range(20):
    time.sleep(.5)
    sample1 = validate(get())
    diagnostics.append({
        'noise_mean': [sample1['detector']['noise_mean' + str(ch)] for ch in (0, 1)],
        'noise_p95': [sample1['detector']['noise_p95' + str(ch)] for ch in (0, 1)],
        'noise_estimate': [sample1['detector']['noise_avg' + str(ch)] for ch in (0, 1)],
        'noise_samples': [sample1['detector']['noise_samples' + str(ch)] for ch in (0, 1)],
    })
frame = get('/api/frame?max_points=200')
assert frame['available']
assert all(frame.get('prod_peak' + str(ch)) for ch in (0, 1))
assert all(frame['prod_peak' + str(ch)]['noise_area_percent'] == expected['detector']['settings']['noise_area_percent'] for ch in (0, 1))
errors = ['seq_gap_ch0', 'seq_gap_ch1', 'seq_dup_ch0', 'seq_dup_ch1', 'crc_bad', 'magic_bad', 'host_rx_ack_fail']
deltas = {key: sample1['stream_stats'][key] - sample0['stream_stats'][key] for key in errors}
assert all(delta == 0 for delta in deltas.values()), deltas
worker0, worker1 = [sample['detector']['worker'] for sample in [sample0, sample1]]
assert worker1['results'] > worker0['results']
assert worker1['overflows'] == worker0['overflows']
assert worker1['latency']['over_100ms'] == worker0['latency']['over_100ms']

# Exercise the installed portal's renderer without authenticating or writing settings.
os.environ['BMI30_CONFIG_JSON'] = str(D / 'host/bmi30_config.json')
namespace = runpy.run_path('/usr/local/bin/bmi30-hotspot-info-server.py')
html = namespace['render_portal_page']('BMI30-E29805C7C', session_username='verification',
                                      session_role='user', request_host='192.168.0.154').decode()
assert 'Noise estimator' in html and 'max(mean, P95)' in html and 'Noise history window' in html
area_field = re.search(r'<input name="noise_area_percent"[^>]*>', html).group(0)
window_field = re.search(r'<input name="noise_window_s"[^>]*>', html).group(0)
for field, key, minimum, maximum, step in [(area_field, 'noise_area_percent', '5', '95', '1'),
                                          (window_field, 'noise_window_s', '0.05', '10', '0.05')]:
    assert f'min="{minimum}"' in field and f'max="{maximum}"' in field and f'step="{step}"' in field
    assert float(re.search(r'value="([^"]+)"', field).group(1)) == expected['detector']['settings'][key]
assert html.index('name="noise_area_percent"') < html.index('Noise estimator') < html.index('name="noise_window_s"')
assert (D / 'host/bmi30_config.json').read_bytes() == config
print(json.dumps({
    'firmware_version': r['firmware_version'], 'active_source': sample1['service']['engine_source'],
    'settings_preserved': True, 'mode': sample1['mode'], 'noise_area_percent': sample1['detector']['settings']['noise_area_percent'],
    'noise_window_s': sample1['detector']['settings']['noise_window_s'], 'noise_estimator': 'max_mean_p95',
    'observation_s': time.monotonic() - start, 'diagnostics': diagnostics, 'error_deltas': deltas,
    'new_worker_results': worker1['results'] - worker0['results'], 'worker_overflow_delta': worker1['overflows'] - worker0['overflows'],
    'worker_over_100ms_delta': worker1['latency']['over_100ms'] - worker0['latency']['over_100ms'],
    'portal_fields': [area_field, window_field], 'portal_render_ok': True,
    'inline_scripts': re.findall(r'<script(?:\s[^>]*)?>([\s\S]*?)</script>', html),
}))
