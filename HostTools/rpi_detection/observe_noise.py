"""Read-only M02 noise observation; never enables Player Recorder or changes settings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.tmp/new-device-20261009'))
from device_remote import ARGS


REMOTE = r'''
import hashlib,json,pathlib,subprocess,time,urllib.request
P=pathlib.Path;D=P('/home/techaid/Documents')
assert subprocess.check_output(['hostname'],text=True).strip()=='BMI30-E29805C7C'
assert P('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
duration,interval=DURATION,INTERVAL
def pid():return subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()
def emit(x):print(json.dumps(x,separators=(',',':')),flush=True)
def get(path):
 with urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=3) as r:return json.load(r)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
s=get('/api/status');assert s['ok'] and not s['stale'] and s['detector']['active']
assert not s['player_recorder']['recording'] and not s['player_recorder']['playback_active']
start=time.monotonic();deadline=start+duration;seen=[s['detector']['fire_seq0'],s['detector']['fire_seq1']]
initial_pid=pid();config_sha=sha(D/'host/bmi30_config.json');env_sha=sha(D/'host/bmi30_split_active_version.env')
emit({'kind':'start','epoch':time.time(),'duration_s':duration,'interval_s':interval,'core_pid':initial_pid,
      'root':subprocess.check_output(['findmnt','-n','-o','SOURCE','/'],text=True).strip(),
      'config_sha256':config_sha,'env_sha256':env_sha,'status':s})
while time.monotonic()<deadline:
 t=time.monotonic()
 try:
  s=get('/api/status');now=time.monotonic()
  emit({'kind':'sample','epoch':time.time(),'elapsed_s':now-start,'request_ms':1000*(now-t),'status':s})
  for ch in (0,1):
   seq=s['detector'].get('fire_seq'+str(ch),seen[ch])
   if seq>seen[ch]:
    for event in range(max(seen[ch]+1,seq-31),seq+1):
     try:
      f=get('/api/frame?max_points=200&fire_seq'+str(ch)+'='+str(event))
      emit({'kind':'event_frame','epoch':time.time(),'elapsed_s':time.monotonic()-start,'channel':ch,'fire_seq':event,'frame':f})
     except Exception as e:emit({'kind':'event_error','channel':ch,'fire_seq':event,'error':str(e)})
   seen[ch]=seq
 except Exception as e:emit({'kind':'error','epoch':time.time(),'elapsed_s':time.monotonic()-start,'error':str(e)})
 time.sleep(max(0,min(interval-(time.monotonic()-t),deadline-time.monotonic())))
emit({'kind':'end','epoch':time.time(),'elapsed_s':time.monotonic()-start,'core_pid':pid(),
      'config_sha256':sha(D/'host/bmi30_config.json'),'env_sha256':sha(D/'host/bmi30_split_active_version.env'),
      'status':get('/api/status')})
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=600)
    parser.add_argument('--interval', type=float, default=.05)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert 5 <= args.seconds <= 600 and .025 <= args.interval <= 1
    args.output.mkdir(parents=True, exist_ok=False)
    script = REMOTE.replace('DURATION', str(args.seconds)).replace('INTERVAL', str(args.interval))
    proc = subprocess.Popen(ARGS + ['python3 -'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timer = threading.Timer(args.seconds + 35, proc.kill)
    timer.daemon = True
    timer.start()
    counts = {'samples':0,'errors':0,'event_frames':0}
    next_progress = 0
    try:
        proc.stdin.write(script.encode())
        proc.stdin.close()
        with (args.output/'M02.jsonl').open('wb') as file:
            for line in proc.stdout:
                file.write(line)
                record = json.loads(line)
                kind = record.get('kind')
                if kind == 'sample':counts['samples'] += 1
                if kind == 'error':counts['errors'] += 1
                if kind == 'event_frame':counts['event_frames'] += 1
                if kind in ('start','end'):
                    (args.output/(kind+'.json')).write_text(json.dumps(record,indent=2),encoding='utf8')
                elapsed = record.get('elapsed_s', 0)
                if kind in ('start','end') or kind == 'sample' and elapsed >= next_progress:
                    s=record['status'];d=s['detector']
                    progress={'kind':kind,'elapsed_s':round(elapsed,1),**counts,'core_pid':record.get('core_pid'),
                              'fires':[d.get('fire_seq0'),d.get('fire_seq1')],
                              'noise_ref':[d.get('noise_ref0'),d.get('noise_ref1')],
                              'noise_avg':[d.get('noise_avg0'),d.get('noise_avg1')],
                              'worker_results':d['worker']['results'],'overflows':d['worker']['overflows']}
                    print(json.dumps(progress),flush=True)
                    (args.output/'progress.json').write_text(json.dumps(progress,indent=2),encoding='utf8')
                    file.flush()
                    next_progress=elapsed+30
        proc.wait(timeout=5)
        if proc.returncode:
            raise RuntimeError(proc.stderr.read().decode(errors='replace')[-2000:])
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()


if __name__ == '__main__':
    main()
