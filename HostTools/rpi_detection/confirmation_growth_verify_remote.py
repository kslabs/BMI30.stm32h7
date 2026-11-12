"""Readback and live stream checks; do not enable the new filter on the bench."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import urllib.request

p = json.load(sys.stdin)
i, r = p['before'], p['release']
reference = json.loads((Path(p['installed']['backup'])/'startup-status.json').read_text())['before']
D = Path('/home/techaid/Documents')
A = D/'host/bmi30_active_runtime/project'
assert subprocess.check_output(['hostname'], text=True).strip() == i['hostname'] == 'BMI30-E29805C7C'
assert Path('/proc/device-tree/serial-number').read_text().strip('\0\n') == i['serial'] == '490df72e29805c7c'
assert subprocess.check_output(['findmnt','-n','-o','SOURCE','/'], text=True).strip() == i['root']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get(path='/api/status'):
    return json.load(urllib.request.urlopen('http://127.0.0.1:8765'+path, timeout=5))


config = D/'host/bmi30_config.json'
config_hash = sha(config)
before = get()
assert before['ok'] and not before['stale'] and before['connection']['connected']
assert before['service']['engine_source'] == str(A/r['paths']['engine'])
assert before['detector']['settings'] == dict(reference['detector']['settings'], confirm_growth_enabled=False, confirm_growth_ratio=2.0)
for key in ('selected','avg_n','rpi_avg_n','stream_mode','base_buf_len','freq_hz'):
    assert before['mode'][key] == reference['mode'][key], key
assert all(sha(A/rel) == r['hashes'][name] for name, rel in r['paths'].items())
assert sha(Path('/usr/local/bin/bmi30-hotspot-info-server.py')) == r['hashes']['portal']
time.sleep(10)
after = get()
errors = ['seq_gap_ch0','seq_gap_ch1','seq_dup_ch0','seq_dup_ch1','crc_bad','magic_bad','host_rx_ack_fail']
deltas = {key: after['stream_stats'].get(key,0)-before['stream_stats'].get(key,0) for key in errors}
assert all(value == 0 for value in deltas.values()), deltas
assert after['detector']['worker']['results'] > before['detector']['worker']['results']
assert after['detector']['worker']['overflows'] == before['detector']['worker']['overflows']
assert after['detector']['settings'] == before['detector']['settings']
for ch in (0, 1):
    diagnostic = after['detector'][f'confirm_growth{ch}']
    assert diagnostic['enabled'] is False and diagnostic['ok'] is True and diagnostic['reason'] == 'disabled'
frame = get('/api/frame?max_points=200')
assert frame.get('available')
os.environ['BMI30_CONFIG_JSON'] = str(config)
namespace = runpy.run_path('/usr/local/bin/bmi30-hotspot-info-server.py')
html = namespace['render_portal_page']('BMI30-E29805C7C', session_username='verification',
                                      session_role='user', request_host='127.0.0.1').decode()
rows = [row for row in re.findall(r'<tr\b[^>]*>[\s\S]*?</tr>',html)
        if 'name="confirm_growth_' in row]
assert len(rows) == (1 if p.get('inline_controls') else 2)
checkbox = re.search(r'<input type="checkbox" name="confirm_growth_enabled"[^>]*>',html).group(0)
number = re.search(r'<input name="confirm_growth_ratio"[^>]*>',html).group(0)
assert ' checked' not in checkbox
assert 'value="2.0"' in number and 'min="1"' in number and 'max="100"' in number
assert 'type="hidden" name="confirm_growth_enabled" value="0"' in html
assert sha(config) == config_hash
bootstrap = namespace['BMI30_I18N_BOOTSTRAP']
print(json.dumps({'version':r['version'], 'active_root':i['root'],
    'active_source':after['service']['engine_source'], 'settings_preserved':True,
    'config_unchanged_during_verification':True, 'confirm_growth_enabled':False, 'confirm_growth_ratio':2.0,
    'mode':after['mode'], 'channels':{name:{key:values[key] for key in ('enabled','confirm_count','threshold','threshold_high')}
                                   for name,values in after['detector']['channels'].items()},
    'error_deltas':deltas, 'new_worker_results':after['detector']['worker']['results']-before['detector']['worker']['results'],
    'worker_overflow_delta':after['detector']['worker']['overflows']-before['detector']['worker']['overflows'],
    'observation_seconds':10, 'portal_render_ok':True,
    'live_growth_diagnostics':[after['detector'][f'confirm_growth{ch}'] for ch in (0,1)],
    'portal_html':'<html><head>'+bootstrap+'</head><body><p class="device-meta"><span>RPI</span></p><table>'+''.join(rows)+'</table></body></html>'}))
