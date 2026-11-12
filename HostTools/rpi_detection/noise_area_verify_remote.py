"""Verify active M02 source, settings, live apply and installed portal rendering."""
import base64
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
r = p['release']
D = Path('/home/techaid/Documents')
A = D / 'host/bmi30_active_runtime/project'
assert subprocess.check_output(['hostname'], text=True).strip() == 'BMI30-E29805C7C'
assert subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '/'], text=True).strip() == '/dev/sda2'


def get(path='/api/status'):
    return json.load(urllib.request.urlopen('http://127.0.0.1:8765' + path, timeout=5))


def apply(percent):
    request = urllib.request.Request('http://127.0.0.1:8765/api/command',
        data=json.dumps({'action':'runtime_tag_detection', 'params':{'noise_area_percent':percent}}).encode(),
        headers={'Content-Type':'application/json','X-BMI30-Source':'noise_area_verification'}, method='POST')
    result = json.load(urllib.request.urlopen(request, timeout=5))
    assert result['ok'] and not result['persisted']
    assert result['detector']['noise_area_percent'] == percent
    return result


def ready(percent):
    for attempt in range(100):
        s = get()
        frame = get('/api/frame?max_points=200')
        if (s['ok'] and not s['stale'] and s['connection']['connected'] and s['detector']['worker']['alive']
                and s['detector']['settings']['noise_area_percent'] == percent
                and frame.get('available') and frame.get('prod_peak0') and frame.get('prod_peak1')
                and all(frame[f'prod_peak{ch}']['noise_area_percent'] == percent for ch in (0, 1))):
            return s, frame
        time.sleep(.15)
    raise RuntimeError('Live frame did not follow the selected noise area')


config = (D / 'host/bmi30_config.json').read_bytes()
before, frame50 = ready(50)
assert before['service']['engine_source'] == str(A / r['paths']['engine'])
assert all(hashlib.sha256((A / rel).read_bytes()).hexdigest() == r['hashes'][name] for name, rel in r['paths'].items())
assert hashlib.sha256(Path('/usr/local/bin/bmi30-hotspot-info-server.py').read_bytes()).hexdigest() == r['hashes']['portal']
tests = []
try:
    apply(80)
    changed, frame80 = ready(80)
    for channel in ['upper', 'lower']:
        for key in ['threshold', 'threshold_high', 'auto_threshold', 'confirm_count']:
            assert changed['detector']['channels'][channel][key] == before['detector']['channels'][channel][key], (channel, key)
    tests.append({'percent':80,'frame_geometry':[frame80[f'prod_peak{ch}'] for ch in (0,1)],'coefficients_preserved':True})
finally:
    apply(50)
restored, frame50 = ready(50)
assert (D / 'host/bmi30_config.json').read_bytes() == config
sample0 = get()
time.sleep(6)
sample1 = get()
errors = ['seq_gap_ch0', 'seq_gap_ch1', 'crc_bad', 'magic_bad', 'host_rx_ack_fail']
deltas = {key:sample1['stream_stats'][key]-sample0['stream_stats'][key] for key in errors}
assert all(delta == 0 for delta in deltas.values()), deltas
assert sample1['detector']['worker']['results'] > sample0['detector']['worker']['results']
assert sample1['detector']['worker']['overflows'] == sample0['detector']['worker']['overflows']

# Render the installed portal using its actual functions and current settings;
# this does not create an authentication session or change browser permissions.
os.environ['BMI30_CONFIG_JSON'] = str(D / 'host/bmi30_config.json')
namespace = runpy.run_path('/usr/local/bin/bmi30-hotspot-info-server.py')
html = namespace['render_portal_page']('BMI30-E29805C7C', session_username='verification',
                                      session_role='user', request_host='192.168.0.154').decode()
field = re.search(r'<input name="noise_area_percent"[^>]*>', html).group(0)
assert 'min="5"' in field and 'max="95"' in field and 'step="1"' in field and 'value="50"' in field
assert html.index('name="noise_area_percent"') < html.index('name="noise_window_s"')
assert (D / 'host/bmi30_config.json').read_bytes() == config
print(json.dumps({'firmware_version':r['firmware_version'],'active_source':sample1['service']['engine_source'],
    'settings_preserved':True, 'mode':sample1['mode'], 'restored_percent':50,'live_tests':tests,
    'error_deltas':deltas,'new_worker_results':sample1['detector']['worker']['results']-sample0['detector']['worker']['results'],
    'worker_overflow_delta':sample1['detector']['worker']['overflows']-sample0['detector']['worker']['overflows'],
    'portal_field':field, 'portal_render_ok':True,'inline_scripts':re.findall(r'<script(?:\s[^>]*)?>([\s\S]*?)</script>',html)}))
