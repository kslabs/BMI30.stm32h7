"""Temporary S10 upper-channel confirmation trial with verified restoration.

Only confirm0 changes, through the existing nonpersistent API. The remote
process restores it on timeout/TERM/HUP. External edits are not overwritten.
"""
import argparse
from pathlib import Path
import shlex
import subprocess


REMOTE = r'''
import copy,hashlib,json,pathlib,signal,socket,subprocess,sys,threading,time,urllib.request
duration,expected_hash=float(sys.argv[1]),sys.argv[2]
assert socket.gethostname()=='BMI30-D5EB8F26C'
config=pathlib.Path('/home/techaid/Documents/host/bmi30_config.json')
def digest():return hashlib.sha256(config.read_bytes()).hexdigest()
def pid():return subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()
def get():return json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=4))
def emit(kind,**fields):print(json.dumps(dict(kind=kind,epoch=time.time(),monotonic=time.monotonic(),**fields)),flush=True)
def stable(s):
    keys=('enabled','confirm_count','threshold','threshold_high','auto_threshold','noise_up','noise_down','noise_unit','burst_gate','burst_blank_s','burst_max_ratio')
    return {'mode':s['mode'],'settings':s['detector']['settings'],
            'channels':{name:{k:v for k,v in values.items() if k in keys} for name,values in s['detector']['channels'].items()}}
def change(value):
    data=json.dumps({'action':'runtime_detector_settings','params':{'confirm0':value}}).encode()
    req=urllib.request.Request('http://127.0.0.1:8765/api/command',data=data,headers={'Content-Type':'application/json'})
    response=json.load(urllib.request.urlopen(req,timeout=8))
    assert response.get('ok') and response.get('persisted') is False,response
stop=threading.Event()
for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,lambda *_:stop.set())
before=get(); original_pid=pid(); original=stable(before)
assert digest()==expected_hash,'Persistent settings changed since baseline'
assert original['channels']['upper']['confirm_count']==3,'Expected three confirmations'
assert before.get('ok') and not before.get('stale') and before['detector']['active']
assert not before['player_recorder']['recording'] and not before['player_recorder']['playback_active']
expected=copy.deepcopy(original);expected['channels']['upper']['confirm_count']=2
emit('baseline',config_sha256=expected_hash,core_pid=original_pid,state=original)
attempted=False
try:
    attempted=True
    change(2)
    after=get()
    assert stable(after)==expected,'Unexpected parameter change'
    assert digest()==expected_hash,'API unexpectedly persisted settings'
    emit('trial_started',confirm0=2,original_confirm0=3,seconds=duration,remote_pid=__import__('os').getpid())
    deadline=time.monotonic()+duration
    while not stop.wait(min(1,max(0,deadline-time.monotonic()))) and time.monotonic()<deadline:
        now=get()
        if pid()!=original_pid or digest()!=expected_hash or stable(now)!=expected:
            raise RuntimeError('Runtime changed during trial; stopping comparison')
finally:
    if attempted:
        failure=None
        for attempt in range(3):
            try:
                current=get()
                count=current['detector']['channels']['upper']['confirm_count']
                persistent_unchanged=digest()==expected_hash
                if count==2 and pid()==original_pid and persistent_unchanged:
                    change(3)
                elif count!=3:
                    raise RuntimeError('External change or core restart: original trial no longer owns confirm0')
                after=get()
                assert after['detector']['channels']['upper']['confirm_count']==3
                emit('restored',confirm0=3,persistent_config_unchanged=digest()==expected_hash,
                     original_core_pid=original_pid,core_pid=pid(),all_static_settings_restored=stable(after)==original)
                failure=None;break
            except Exception as error:
                failure=error;time.sleep(0.5)
        if failure is not None:
            emit('restore_failed',error=str(failure));raise failure
emit('end')
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--expected-config-sha256',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 5<=args.seconds<=900 or len(args.expected_config_sha256)!=64:
        parser.error('Use 5..900 seconds and a SHA256 baseline')
    compile(REMOTE,'runtime-confirmation-trial','exec')
    remote='python3 - '+shlex.quote(str(args.seconds))+' '+shlex.quote(args.expected_config_sha256)
    command=[r'C:\Windows\System32\OpenSSH\ssh.exe','-F',str(Path.home()/'.ssh/bmi30_config'),
             '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8','BMI30-D5EB8F26C',remote]
    with args.output.open('xb') as log:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE)
        try:
            process.stdin.write(REMOTE.encode());process.stdin.close()
            for line in process.stdout:
                log.write(line);log.flush();print(line.decode().strip(),flush=True)
            if process.wait():raise RuntimeError('Trial failed; inspect restoration result')
        finally:
            if process.poll() is None:
                process.terminate();process.wait(timeout=5)


if __name__=='__main__':main()
