"""One final read of all five devices after USB boot testing."""
import concurrent.futures
import json
from deploy_fleet_release import remote, HOSTS
from usb_clone_preflight import WORK

SCRIPT = r'''
import datetime,json,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin)
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
def api(url):
 with urllib.request.urlopen(url,timeout=5) as response:return json.load(response)
assert run('hostname')==p['host']
portal=api('http://127.0.0.1/api/status')
core=api('http://127.0.0.1:8765/api/status')
net=json.loads(pathlib.Path('/run/bmi30-network/status.json').read_text())
agent=json.loads(pathlib.Path('/var/lib/bmi30-agent/state.json').read_text())
memory={k:int(v.split()[0]) for l in pathlib.Path('/proc/meminfo').read_text().splitlines() for k,v in [l.split(':',1)]}
version=next(l.split('=',1)[1] for l in pathlib.Path('/home/techaid/Documents/host/bmi30_split_active_version.env').read_text().splitlines() if l.startswith('BMI30_SPLIT_VERSION='))
root=run('findmnt','-n','-o','SOURCE','/')
assert root==('/dev/mmcblk0p2' if p['board']=='S12' else '/dev/sda2')
assert portal['sync_mode']['code']==p['board']
assert version=='2026-10-02-0948'
assert core['connection']['connected'] and core['detector']['worker']['alive'] and not core.get('stale')
assert agent['http_status']==200
assert run('systemctl','is-active','bmi30-network')=='active'
print(json.dumps({'board':p['board'],'host':p['host'],'time':datetime.datetime.now().astimezone().isoformat(),
 'root':root,'boot':run('findmnt','-n','-o','SOURCE','/boot/firmware'),'version':version,
 'mode':portal['sync_mode']['code'],'portal_ok':True,'stream_fresh':True,'network_nodes':len(net.get('nodes',[])),
 'network_coordinator':net.get('coordinator'),'server_http':agent['http_status'],
 'server_checkin_age_s':round(time.time()-agent.get('last_checkin_unix',0),1),
 'memory_mib':{'used':round((memory['MemTotal']-memory['MemAvailable'])/1024,1),'total':round(memory['MemTotal']/1024,1),
               'swap_used':round((memory['SwapTotal']-memory['SwapFree'])/1024,1)},
 'failed_services':run('systemctl','--failed','--no-legend','--no-pager')}))
'''

def one(item):
    board,host=item
    return json.loads(remote(host,SCRIPT,{'board':board,'host':host},timeout=25))

if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        result=list(pool.map(one,HOSTS.items()))
    (WORK/'final-fleet.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
