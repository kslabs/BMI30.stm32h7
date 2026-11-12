"""Launch the guarded worker; keep backups on their original device."""
import base64,json,sys,time
from pathlib import Path
from media_sync import WORK,call

EXCLUDES=['/boot/firmware/***','/dev/***','/proc/***','/sys/***','/tmp/***',
 '/run/***','/mnt/***','/media/***','/lost+found/***','/swapfile','/var/swap',
 '/var/backups/***','/var/log/***','/var/cache/***',
 '/home/techaid/Documents/.tmp/***','/home/techaid/Documents/host/player_recordings/***',
 '/home/*/thinclient_drives/***','/home/*/.cache/***','/home/*/.gvfs/***']
REMOTE=r'''
import base64,json,pathlib,subprocess,sys,urllib.request
p=json.load(sys.stdin);plan=p['plan'];P=pathlib.Path
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')==plan['host']
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/mmcblk0p2'
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==plan['serial']
assert P('/sys/block/mmcblk0/device/cid').read_text()==plan['emmc_cid']
assert subprocess.run(['systemctl','is-active','--quiet','bmi30-media-sync']).returncode!=0
assert subprocess.run(['findmnt','-rn','-S','/dev/sda2'],stdout=subprocess.DEVNULL).returncode!=0
s=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=5))
assert s['ok'] and not s['stale'] and s['connection']['connected']
assert not s['player_recorder']['recording'] and not s['player_recorder']['playback_active']
for line in run('loginctl','list-sessions','--no-legend').splitlines():
 assert run('loginctl','show-session',line.split()[0],'-p','Service','--value')=='sshd'
root=P('/var/lib/bmi30-media-sync');root.mkdir(mode=0o700,exist_ok=True)
(root/'worker.py').write_bytes(base64.b64decode(p['worker']))
(root/'plan.json').write_text(json.dumps(plan))
run('python3','-m','py_compile',str(root/'worker.py'))
run('systemd-run','--unit=bmi30-media-sync','--property=Type=exec','--property=Nice=15',
 '--property=IOSchedulingClass=idle','/usr/bin/python3',str(root/'worker.py'),str(root/'plan.json'))
print(json.dumps({'started':True,'host':plan['host'],'usb_serial':plan['serial'],'version':plan['version'],'mode':s['mode']}))
'''
for board in sys.argv[1:]:
    i=json.loads((WORK/(board+'-inventory.json')).read_text())
    probe=json.loads((WORK/(board+'-media_probe.json')).read_text())
    assert all(v['rc']==0 for v in probe['fsck'].values())
    usb=next(d for d in i['disks']['blockdevices'] if d['name']=='sda')
    plan={'host':i['hostname'],'serial':usb['serial'],'uuid':next(d['uuid'] for d in usb['children'] if d['name']=='sda2'),
          'cm_serial':i['serial'],'emmc_cid':i['emmc_cid'],'version':'2026-10-13-1534',
          'stamp':time.strftime('%Y%m%d-%H%M%S'),'excludes':EXCLUDES}
    payload={'plan':plan,'worker':base64.b64encode(Path(__file__).with_name('media_copy_worker.py').read_bytes()).decode()}
    r=call(board,REMOTE,'copy-start',payload)
    print(json.dumps({'board':board,**r}),flush=True)
