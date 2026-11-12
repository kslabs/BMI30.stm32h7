"""Read-only M01 verification; the incomplete TLS test closes its own socket."""
import json
import socket
import ssl
import subprocess
import time
import urllib.request

def fetch(url):
    start = time.monotonic()
    with urllib.request.urlopen(url, timeout=4, context=ssl._create_unverified_context()) as response:
        body = response.read()
        return {'code': response.status, 'seconds': round(time.monotonic()-start, 3)}, body

result = {}
with socket.create_connection(('127.0.0.1', 443), timeout=2) as stalled:
    time.sleep(0.1)
    result['https_with_stalled_client'] = fetch('https://127.0.0.1/')[0]
result['http'] = fetch('http://127.0.0.1/')[0]
samples = []
for i in range(21):
    timing, data = fetch('http://127.0.0.1:8765/api/status')
    data = json.loads(data)
    samples.append({'time': time.time(), 'timing': timing, 'ok': data.get('ok'),
        'stale': data.get('stale'), 'connection': data.get('connection'),
        'stream': data.get('stream_stats'), 'optic': data.get('optic'),
        'worker': data.get('detector', {}).get('worker'), 'mode': data.get('mode')})
    if i < 20:
        time.sleep(2)
result['samples'] = len(samples)
result['all_fresh_connected'] = all(s['ok'] and not s['stale'] and s['connection']['connected'] and s['connection']['last_frame_age_s'] < 2 for s in samples)
result['all_workers_alive'] = all(s['worker']['alive'] for s in samples)
result['stream_delta'] = {k: samples[-1]['stream'][k]-samples[0]['stream'][k]
    for k in ('rx_cnt_ch0', 'rx_cnt_ch1', 'seq_gap_ch0', 'seq_gap_ch1', 'crc_bad', 'host_rx_ack_fail')}
result['optic_states'] = sorted(set(str(s['optic']['reaction_local_active']) for s in samples))
result['optic_final'] = samples[-1]['optic']
result['dns'] = sorted(set(x[4][0] for x in socket.getaddrinfo('www.teiots.net', 443)))
result['core_pid'] = subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()
print(json.dumps(result, indent=2))
