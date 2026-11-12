"""Verify a real USB boot, portals, acquisition, antenna and server/network services."""
import concurrent.futures
import json
import sys
from deploy_fleet_release import remote, HOSTS
from usb_clone_preflight import WORK

SCRIPT = r'''
import datetime,hashlib,json,pathlib,socket,ssl,subprocess,sys,time,urllib.request
p=json.load(sys.stdin)
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
def fetch(url):
 start=time.monotonic()
 with urllib.request.urlopen(url,timeout=6,context=ssl._create_unverified_context()) as r:
  return r.status,r.read(),round((time.monotonic()-start)*1000,1)
assert run('hostname')==p['host']
assert run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2'
assert run('findmnt','-n','-o','SOURCE','/boot/firmware')=='/dev/sda1'
assert run('lsblk','-dn','-o','SERIAL','/dev/sda')==p['serial']
boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert boot_id!=p['boot_id_before']
with socket.create_connection(('127.0.0.1',443),timeout=3) as stalled:
 time.sleep(.1);https=fetch('https://127.0.0.1/')
http=fetch('http://127.0.0.1/')
samples=[]
for index in range(11):
 _,body,_=fetch('http://127.0.0.1:8765/api/status')
 samples.append(json.loads(body))
 if index<10:time.sleep(2)
fresh=all(s.get('ok') and not s.get('stale') and s['connection']['connected'] and s['connection']['last_frame_age_s']<2 for s in samples)
workers=all(s['detector']['worker']['alive'] for s in samples)
stats={k:samples[-1]['stream_stats'][k]-samples[0]['stream_stats'][k]
 for k in ('rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail')}
overflows=samples[-1]['detector']['worker']['overflows']-samples[0]['detector']['worker']['overflows']
services={s:run('systemctl','is-active',s) for s in ('bmi30-core','bmi30-hotspot-info','bmi30-network','bmi30-agent','bmi30-tunnel','bmi30-antenna-check')}
network=json.loads(pathlib.Path('/run/bmi30-network/status.json').read_text())
agent=json.loads(pathlib.Path('/var/lib/bmi30-agent/state.json').read_text())
_,body,_=fetch('http://127.0.0.1/api/status');portal=json.loads(body)
mem={k:int(v.split()[0]) for line in pathlib.Path('/proc/meminfo').read_text().splitlines() for k,v in [line.split(':',1)]}
pins=[line for line in run('pinctrl','get').splitlines() if 'ANT1/' in line or 'ANT2/' in line]
assert any('ANT1/' in line and '| lo ' in line for line in pins)
assert any('ANT2/' in line and '| hi ' in line for line in pins)
result={'host':p['host'],'root':'/dev/sda2','boot':'/dev/sda1','usb_serial':p['serial'],
 'boot_id':boot_id,'boot_tested':True,'time':datetime.datetime.now().astimezone().isoformat(),
 'http_ms':http[2],'https_stalled_client_ms':https[2],'samples':len(samples),'fresh':fresh,'workers_alive':workers,
 'stream_delta':stats,'worker_overflows':overflows,'services':services,
 'network':{'status':network.get('status'),'coordinator':network.get('coordinator'),'nodes':len(network.get('nodes',[])),
            'id':network.get('local',{}).get('id'),'ssid':network.get('local',{}).get('ssid')},
 'agent':{k:agent[k] for k in ('http_status','status','approved','last_success','last_checkin','last_checkin_at','message') if k in agent},
 'agent_state_keys':list(agent),
 'portal_mode':portal.get('sync_mode'),'optic_state':samples[-1].get('optic'),
 'antenna_pins':pins,'external_antenna_config':'dtparam=ant2' in pathlib.Path('/boot/firmware/config.txt').read_text(),
 'used_ram_mib':round((mem['MemTotal']-mem['MemAvailable'])/1024,1),
 'available_ram_mib':round(mem['MemAvailable']/1024,1),'swap_used_mib':round((mem['SwapTotal']-mem['SwapFree'])/1024,1),
 'failed_services':run('systemctl','--failed','--no-legend','--no-pager')}
assert fresh and workers and stats['rx_cnt_ch0']>0 and stats['rx_cnt_ch1']>0
assert stats['crc_bad']==0 and stats['magic_bad']==0 and stats['host_rx_ack_fail']==0 and overflows==0
assert all(v=='active' for v in services.values())
assert result['external_antenna_config']
print(json.dumps(result))
'''


def verify(board):
    plan=json.loads((WORK/(board+'-boot-plan.json')).read_text())
    result=json.loads(remote(HOSTS[board],SCRIPT,plan,timeout=65))
    (WORK/(board+'-usb-boot-verified.json')).write_text(json.dumps(result,indent=2))
    return {'board':board,**result}


if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(verify,sys.argv[1:]):print(json.dumps(result),flush=True)
