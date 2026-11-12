"""Temporary, identity-guarded M02 confirmation trial with automatic restoration.

Changes one runtime field only. Does not save settings or restart the core.
The remote supervisor restores confirm0=6 on deadline, stop, or SSH EOF.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]

REMOTE = r'''
import copy,hashlib,json,os,pathlib,select,signal,socket,subprocess,sys,threading,time,urllib.request
options=OPTIONS
assert socket.gethostname()=='BMI30-E29805C7C'
assert pathlib.Path('/proc/device-tree/serial-number').read_text().strip('\0\n')=='490df72e29805c7c'
config=pathlib.Path('/home/techaid/Documents/host/bmi30_config.json')
stop=threading.Event()
def digest():return hashlib.sha256(config.read_bytes()).hexdigest()
def pid():return subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()
http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get():
    for attempt in range(3):
        try:
            with http.open('http://127.0.0.1:8765/api/status',timeout=3) as reply:return json.load(reply)
        except OSError:
            if attempt==2:raise
            time.sleep(.2)
def emit(kind,**fields):print(json.dumps(dict(kind=kind,epoch=time.time(),monotonic=time.monotonic(),**fields)),flush=True)
def stable(s):
    keys=('enabled','confirm_count','threshold','threshold_high','auto_threshold','noise_up','noise_down','noise_unit','burst_gate','burst_blank_s','burst_max_ratio')
    return {'mode':s['mode'],'settings':s['detector']['settings'],
        'channels':{name:{k:v for k,v in values.items() if k in keys} for name,values in s['detector']['channels'].items()}}
def change(value):
    data=json.dumps({'action':'runtime_detector_settings','params':{'confirm0':value}}).encode()
    request=urllib.request.Request('http://127.0.0.1:8765/api/command',data=data,headers={'Content-Type':'application/json'})
    with http.open(request,timeout=8) as reply:response=json.load(reply)
    assert response.get('ok') and response.get('persisted') is False,response
def control():
    pending=b''
    while not stop.is_set():
        if not select.select([0],[],[],.2)[0]:continue
        data=os.read(0,128)
        if not data:stop.set();return
        pending+=data
        if b'STOP\n' in pending:stop.set();return
        if len(pending)>1024:stop.set();return
for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,lambda *_:stop.set())
before=get(); original_pid=pid(); original=stable(before)
assert digest()==options['config_sha256'],'Persistent configuration differs from recorded baseline'
assert original['channels']['upper']['confirm_count']==6,'Expected original confirm0=6'
assert before.get('ok') and not before.get('stale') and before['detector']['active']
assert not before['player_recorder']['recording'] and not before['player_recorder']['playback_active']
expected=copy.deepcopy(original);expected['channels']['upper']['confirm_count']=options['confirm']
emit('baseline',config_sha256=digest(),core_pid=original_pid,state=original)
attempted=False
controller=threading.Thread(target=control,daemon=True);controller.start()
try:
    attempted=True
    change(options['confirm'])
    after=get()
    assert stable(after)==expected,'Unexpected parameter change'
    assert digest()==options['config_sha256'],'API unexpectedly saved settings'
    emit('trial_started',confirm0=options['confirm'],original_confirm0=6,seconds=options['seconds'],remote_pid=os.getpid(),state=stable(after))
    deadline=time.monotonic()+options['seconds']
    while time.monotonic()<deadline and not stop.wait(min(1,max(0,deadline-time.monotonic()))):
        now=get()
        if pid()!=original_pid or digest()!=options['config_sha256'] or stable(now)!=expected:
            raise RuntimeError('External runtime change during trial')
finally:
    stop.set();controller.join(timeout=1)
    if attempted:
        failure=None
        for attempt in range(3):
            try:
                current=get();count=current['detector']['channels']['upper']['confirm_count']
                if count==options['confirm'] and pid()==original_pid and digest()==options['config_sha256']:
                    change(6)
                elif count!=6:
                    raise RuntimeError('Trial no longer owns confirm0; external changes preserved')
                after=get()
                assert after['detector']['channels']['upper']['confirm_count']==6
                emit('restored',confirm0=6,persistent_config_unchanged=digest()==options['config_sha256'],
                     core_pid=pid(),all_static_settings_restored=stable(after)==original)
                failure=None;break
            except Exception as error:failure=error;time.sleep(.3)
        if failure is not None:emit('restore_failed',error=str(failure));raise failure
emit('end')
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=900)
    parser.add_argument('--confirm',type=int,choices=(3,4,5),default=3)
    parser.add_argument('--expected-config-sha256')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stop',action='store_true')
    args=parser.parse_args()
    if args.stop:
        (args.output/'STOP').write_text('stop\n',encoding='ascii')
        print('Trial stop requested')
        return
    if not 10<=args.seconds<=900 or not args.expected_config_sha256 or len(args.expected_config_sha256)!=64:
        parser.error('Use 10..900 seconds and a baseline SHA256')
    args.output.mkdir(parents=True,exist_ok=False)
    options={'seconds':args.seconds,'confirm':args.confirm,'config_sha256':args.expected_config_sha256}
    source=REMOTE.replace('options=OPTIONS','options='+repr(options),1)
    compile(source,'m02-confirmation-trial','exec')
    (args.output/'remote-source.py').write_text(source,encoding='utf-8')
    sys.path.insert(0,str(ROOT/'.tmp/new-device-20261009'))
    from device_remote import ARGS
    process=None
    done=threading.Event()
    records=[]
    try:
        with (args.output/'ssh-stderr.txt').open('wb') as stderr:
            process=subprocess.Popen(ARGS+['sudo -n python3 -u -c '+shlex.quote(source)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr)
            def control():
                while not done.wait(.25):
                    if (args.output/'STOP').exists():
                        try:process.stdin.write(b'STOP\n');process.stdin.flush()
                        except (OSError,ValueError):pass
                        return
            threading.Thread(target=control,daemon=True).start()
            with (args.output/'events.jsonl').open('wb') as stream:
                for line in process.stdout:
                    stream.write(line);stream.flush()
                    record=json.loads(line);records.append(record)
                    print(json.dumps(record),flush=True)
            rc=process.wait(timeout=10)
            if rc or not any(r['kind']=='restored' for r in records):
                raise RuntimeError('Trial or restoration failed; inspect trial logs')
    finally:
        done.set()
        if process is not None:
            process.stdin.close()
            if process.poll() is None:
                try:process.wait(timeout=12)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        (args.output/'summary.json').write_text(json.dumps({'events':records,
            'restored':any(r['kind']=='restored' for r in records)},indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
