"""Calibrate every evdev interface of one Bluetooth ring, including touch gestures.

Waits for the selected ring to reconnect; never reads unrelated input devices.
This does not start a tag-passage test and does not change detector settings.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from capture_status import HOSTS


REMOTE = r'''
import errno,fcntl,json,os,pathlib,select,signal,socket,struct,sys,time
hostname,mac,duration=sys.argv[1],sys.argv[2].casefold(),float(sys.argv[3])
if socket.gethostname()!=hostname:raise RuntimeError('Unexpected instrument')
inputs={}
stopped=False
def stop(*_):
    global stopped
    stopped=True
for sig in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT):signal.signal(sig,stop)
def emit(kind,**fields):
    print(json.dumps(dict(kind=kind,epoch=time.time(),monotonic=time.monotonic(),**fields),separators=(',',':')),flush=True)
event=struct.Struct('llHHi')
started=time.monotonic()
last_scan=0
count=0
open_errors={}
emit('waiting_for_ring',hostname=hostname,mac=mac,duration_s=duration)
try:
    while not stopped and time.monotonic()-started<duration:
        if time.monotonic()-last_scan>0.25:
            last_scan=time.monotonic()
            existing=set()
            for path in pathlib.Path('/sys/class/input').glob('event*'):
                device='/dev/input/'+path.name
                try:
                    base=path/'device'
                    if (base/'uniq').read_text().strip().casefold()!=mac:continue
                    if int((base/'id/bustype').read_text().strip(),16)!=5:continue
                    existing.add(device)
                    if any(info['device']==device for info in inputs.values()):continue
                    name=(base/'name').read_text().strip()
                    fd=os.open(device,os.O_RDONLY|os.O_NONBLOCK)
                    try:
                        fcntl.ioctl(fd,0x40044590,1)
                        fcntl.ioctl(fd,0x400445a0,struct.pack('i',time.CLOCK_MONOTONIC))
                    except BaseException:
                        os.close(fd)
                        raise
                    inputs[fd]=dict(device=device,name=name,buffer=b'')
                    open_errors.pop(device,None)
                    absolute_state={}
                    for axis in (53,54):
                        state=bytearray(struct.pack('2i',axis,0))
                        try:
                            # MT positions live in slots, not absinfo.value.
                            fcntl.ioctl(fd,0x8008450a,state,True)
                            absolute_state[str(axis)]=struct.unpack('2i',state)[1]
                        except OSError as error:
                            if error.errno not in (errno.EINVAL,errno.ENOTTY):raise
                    emit('input_ready',device=device,name=name,absolute_state=absolute_state)
                except OSError as error:
                    if error.errno in (errno.ENOENT,errno.ENODEV,errno.ENXIO):continue
                    if error.errno != errno.EACCES:raise
                    # udev may expose a new node before assigning input-group access.
                    if device not in open_errors:
                        open_errors[device]=time.monotonic()
                        emit('input_wait_access',device=device,reason=str(error))
                    elif time.monotonic()-open_errors[device]>8:
                        raise RuntimeError('Input permission unavailable after 8s: '+device) from error
            for device in list(open_errors):
                if device not in existing:open_errors.pop(device,None)
            for fd,info in list(inputs.items()):
                if info['device'] not in existing:
                    emit('input_removed',device=info['device'])
                    os.close(fd)
                    del inputs[fd]
        readable,_,_=select.select(list(inputs),[],[],0.1)
        for fd in readable:
            info=inputs[fd]
            try:
                data=os.read(fd,event.size*64)
                if not data:raise OSError('Input disconnected')
            except BlockingIOError:continue
            except OSError as error:
                emit('input_removed',device=info['device'],reason=str(error))
                os.close(fd)
                del inputs[fd]
                continue
            info['buffer']+=data
            while len(info['buffer'])>=event.size:
                sec,usec,kind,code,value=event.unpack(info['buffer'][:event.size])
                info['buffer']=info['buffer'][event.size:]
                if kind==0 and code==3:raise RuntimeError('SYN_DROPPED: input events lost')
                emit('input',device=info['device'],input_mono=sec+usec/1e6,type=kind,code=code,value=value)
                count+=1
    emit('end',input_events=count)
except BaseException as error:
    emit('error',error=repr(error),input_events=count)
    raise
finally:
    for fd in inputs:os.close(fd)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', choices=HOSTS, default='S10')
    parser.add_argument('--mac', required=True)
    parser.add_argument('--seconds', type=float, default=180)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 3600:
        parser.error('seconds must be 5..3600')
    hostname = HOSTS[args.board]
    command = 'nice -n 10 python3 - ' + ' '.join(shlex.quote(str(x)) for x in (hostname,args.mac,args.seconds))
    with args.output.open('xb') as log:
        process = subprocess.Popen([r'C:\Windows\System32\OpenSSH\ssh.exe', '-F', str(Path.home()/'.ssh/bmi30_config'),
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8', hostname, command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            process.stdin.write(REMOTE.encode())
            process.stdin.close()
            for line in process.stdout:
                log.write(line)
                log.flush()
                record = json.loads(line)
                if record['kind'] != 'input':
                    print(line.decode().strip(), flush=True)
            if process.wait() != 0:
                raise RuntimeError('Ring calibration failed; inspect stderr and JSONL')
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == '__main__':
    main()
