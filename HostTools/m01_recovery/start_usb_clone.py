"""Launch an auditable USB copy as a persistent systemd job on selected devices."""
import base64
import concurrent.futures
import datetime
import json
from pathlib import Path
import sys
from deploy_fleet_release import remote, HOSTS
from usb_clone_preflight import EXCLUDES, WORK

SCRIPT = r'''
import base64,json,pathlib,subprocess,sys
p=json.load(sys.stdin)
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')==p['plan']['host']
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/mmcblk0p2'
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==p['plan']['serial']
assert subprocess.run(['systemctl','is-active','--quiet','bmi30-usb-copy.service']).returncode!=0
subprocess.run(['systemctl','reset-failed','bmi30-usb-copy.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
root=pathlib.Path('/var/lib/bmi30-usb-copy');root.mkdir(mode=0o700,exist_ok=True)
(root/'clone.py').write_bytes(base64.b64decode(p['script']))
(root/'plan.json').write_text(json.dumps(p['plan']))
subprocess.run(['python3','-m','py_compile',str(root/'clone.py')],check=True)
run('systemd-run','--unit=bmi30-usb-copy','--property=Type=exec',
    '--property=Nice=15','--property=IOSchedulingClass=idle',
    '/usr/bin/python3',str(root/'clone.py'),str(root/'plan.json'))
print(json.dumps({'host':p['plan']['host'],'started':True,'stamp':p['plan']['stamp']}))
'''


def start(board):
    preflight=json.loads((WORK/(board+'-clone-preflight.json')).read_text())
    plan={k:preflight[k] for k in ('host','serial','uuid')}
    plan.update(excludes=EXCLUDES,stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    encoded=base64.b64encode(Path(__file__).with_name('clone_usb_system_remote.py').read_bytes()).decode()
    result=json.loads(remote(HOSTS[board],SCRIPT,{'script':encoded,'plan':plan}))
    (WORK/(board+'-clone-start.json')).write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(start,sys.argv[1:]): print(json.dumps(result),flush=True)
