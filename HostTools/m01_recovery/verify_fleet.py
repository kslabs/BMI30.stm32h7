"""Verify each live portal/stream and measure actual RAM use after deployment."""
import concurrent.futures
import json
from build_fleet_release import WORK, VERSION
from deploy_fleet_release import HOSTS, remote

SCRIPT = r'''
import datetime,hashlib,json,pathlib,socket,ssl,subprocess,time,urllib.request
p=json.load(__import__('sys').stdin)
run=lambda *a:subprocess.check_output(a,text=True).strip()
assert run('hostname')==p['host']
def fetch(url):
 start=time.monotonic()
 with urllib.request.urlopen(url,timeout=4,context=ssl._create_unverified_context()) as r:
  return r.status,r.read(),round((time.monotonic()-start)*1000,2)
with socket.create_connection(('127.0.0.1',443),timeout=2) as stalled:
 time.sleep(.1);https=fetch('https://127.0.0.1/')
http=fetch('http://127.0.0.1/')
samples=[]
for i in range(16):
 code,data,ms=fetch('http://127.0.0.1:8765/api/status');s=json.loads(data)
 samples.append({'ok':s.get('ok'),'stale':s.get('stale'),'connection':s['connection'],
  'stats':s['stream_stats'],'worker':s['detector']['worker'],'mode':s['mode']})
 if i<15:time.sleep(2)
memory={}
for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
 key,value=line.split(':',1);memory[key]=int(value.split()[0])
total,available=memory['MemTotal'],memory['MemAvailable'];used=total-available
_,data,_=fetch('http://127.0.0.1/api/status');portal=json.loads(data)
result=dict(host=p['host'],time= datetime.datetime.now().astimezone().isoformat(),
 root=run('findmnt','-n','-o','SOURCE','/'),core_pid=run('systemctl','show','bmi30-core','-p','MainPID','--value'),
 portal_sha256=hashlib.sha256(pathlib.Path('/usr/local/bin/bmi30-hotspot-info-server.py').read_bytes()).hexdigest(),
 env=pathlib.Path('/home/techaid/Documents/host/bmi30_split_active_version.env').read_text(),
 http={'code':http[0],'ms':http[2]},https_with_stalled_client={'code':https[0],'ms':https[2]},
 samples=len(samples),all_fresh=all(s['ok'] and not s['stale'] and s['connection']['connected'] and s['connection']['last_frame_age_s']<2 for s in samples),
 all_workers_alive=all(s['worker']['alive'] for s in samples),
 stream_delta={k:samples[-1]['stats'][k]-samples[0]['stats'][k] for k in ('rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail')},
 worker_overflows=samples[-1]['worker']['overflows']-samples[0]['worker']['overflows'],
 mode_unchanged=all(s['mode']==samples[0]['mode'] for s in samples),
 memory_mib=dict(total=round(total/1024,1),used=round(used/1024,1),available=round(available/1024,1),used_percent=round(100*used/total,1),
 swap_used=round((memory['SwapTotal']-memory['SwapFree'])/1024,1)),
 failed_services=run('systemctl','--failed','--no-legend','--no-pager'),
 usb=run('lsusb'),disks=json.loads(run('lsblk','-J','-o','NAME,PATH,TRAN,SERIAL,UUID,MOUNTPOINTS')),
 interfaces=portal.get('interfaces'),firmware=portal.get('raspberry_firmware_version'))
assert result['all_fresh'] and result['all_workers_alive'] and result['mode_unchanged']
assert result['core_pid']==p['core_pid']
assert result['portal_sha256']==p['portal_sha256']
assert 'BMI30_SPLIT_VERSION='+p['version'] in result['env']
print(json.dumps(result))
'''

def verify(item):
    board,host=item
    audit=json.loads((WORK/(host+'-audit.json')).read_text())
    release=json.loads((WORK/'release.json').read_text())
    result=json.loads(remote(host,SCRIPT,{'host':host,'version':VERSION,'core_pid':audit['core_pid'],
        'portal_sha256':release['hashes']['hotspot_info_server.py']},timeout=65))
    (WORK/(board+'-verification.json')).write_text(json.dumps(result,indent=2))
    return {'board':board,**{k:v for k,v in result.items() if k not in ('env','usb','disks','interfaces')}}

if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for result in pool.map(verify,HOSTS.items()):print(json.dumps(result),flush=True)
