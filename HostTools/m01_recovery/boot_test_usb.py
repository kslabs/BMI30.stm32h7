"""Reboot a verified USB copy only when no desktop or package/copy work is active."""
import json
import sys
from deploy_fleet_release import remote, HOSTS
from usb_clone_preflight import WORK

SCRIPT = r'''
import json,pathlib,subprocess,sys
p=json.load(sys.stdin)
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')==p['host']
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/mmcblk0p2'
status=json.loads(pathlib.Path('/run/bmi30-usb-copy.json').read_text())
assert status['stage']=='complete'
result=status['result']
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==result['serial']
assert subprocess.run(['systemctl','is-active','--quiet','bmi30-usb-copy'],stdout=subprocess.DEVNULL).returncode!=0
assert subprocess.run(['findmnt','-rn','-S','/dev/sda2'],stdout=subprocess.DEVNULL).returncode!=0
for line in run('loginctl','list-sessions','--no-legend').splitlines():
 service=run('loginctl','show-session',line.split()[0],'-p','Service','--value')
 assert service=='sshd', 'Non-SSH user session present: '+service
processes=run('ps','-eo','comm=').splitlines()
assert not any(x.strip() in ('code','Xorg','lxsession','rsync','dpkg','apt','apt-get') for x in processes), 'Interactive/copy/package work is active'
config=run('vcgencmd','bootloader_config')
order=next(line.split('=',1)[1] for line in config.splitlines() if line.startswith('BOOT_ORDER='))
assert order.endswith('4'), 'USB is not first in existing boot order'
result['boot_id_before']=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
result['reboot_scheduled']=True
run('systemd-run','--unit=bmi30-usb-boot-test','--on-active=4s','/usr/bin/systemctl','reboot')
print(json.dumps(result))
'''

if __name__=='__main__':
    for board in sys.argv[1:]:
        result=json.loads(remote(HOSTS[board],SCRIPT,{'host':HOSTS[board]}))
        (WORK/(board+'-boot-plan.json')).write_text(json.dumps(result,indent=2))
        print(board,json.dumps(result),flush=True)
