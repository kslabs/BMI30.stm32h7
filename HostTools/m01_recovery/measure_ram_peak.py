"""Observe current fleet RAM peaks without changing services or memory limits."""
import concurrent.futures
import json
from pathlib import Path
from deploy_fleet_release import remote, HOSTS

WORK=Path(__file__).resolve().parents[2]/'.tmp/ram-peak-20261002'
SCRIPT=r'''
import datetime,json,pathlib,subprocess,time
def mem():
 return {k:int(v.split()[0]) for line in pathlib.Path('/proc/meminfo').read_text().splitlines() for k,v in [line.split(':',1)]}
def vm():
 return {k:int(v) for line in pathlib.Path('/proc/vmstat').read_text().splitlines() for k,v in [line.split()] if k in ('pswpin','pswpout','oom_kill')}
def run(*args):return subprocess.run(args,capture_output=True,text=True).stdout.strip()
start=datetime.datetime.now().astimezone().isoformat()
before=vm();values=[];begin=time.monotonic()
for index in range(601):
 m=mem();values.append({'at_s':round(time.monotonic()-begin,3),'used_kib':m['MemTotal']-m['MemAvailable'],
  'available_kib':m['MemAvailable'],'swap_used_kib':m['SwapTotal']-m['SwapFree'],
  'anon_kib':m.get('AnonPages',0),'cache_kib':m.get('Cached',0),'slab_unreclaimable_kib':m.get('SUnreclaim',0)})
 if index<600:time.sleep(max(0,begin+(index+1)*.2-time.monotonic()))
peaks=[]
for d in pathlib.Path('/proc').iterdir():
 if not d.name.isdigit():continue
 try:
  fields=dict(line.split(':',1) for line in (d/'status').read_text().splitlines() if ':' in line)
  if 'VmHWM' in fields:
   peaks.append({'pid':int(d.name),'name':fields['Name'].strip(),'rss_mib':round(int(fields['VmRSS'].split()[0])/1024,1),
                 'peak_rss_mib':round(int(fields['VmHWM'].split()[0])/1024,1)})
 except (OSError,KeyError,ValueError):pass
after=vm();used=[x['used_kib']/1024 for x in values]
print(json.dumps({'host':run('hostname'),'start':start,'duration_s':round(time.monotonic()-begin,2),
 'interval_s':.2,'samples':len(values),'total_mib':round(m['MemTotal']/1024,1),
 'used_mib':{'min':round(min(used),1),'mean':round(sum(used)/len(used),1),'max':round(max(used),1),'end':round(used[-1],1)},
 'peak_swap_used_mib':round(max(x['swap_used_kib'] for x in values)/1024,2),
 'swap_total_mib':round(m['SwapTotal']/1024,1),'vmstat_before':before,'vmstat_after':after,
 'vmstat_delta':{k:after[k]-before[k] for k in before},
 'memory_pressure':pathlib.Path('/proc/pressure/memory').read_text() if pathlib.Path('/proc/pressure/memory').exists() else None,
 'largest_live_process_peaks':sorted(peaks,key=lambda p:p['peak_rss_mib'],reverse=True)[:8],
 'kernel_memory_controller':pathlib.Path('/proc/cgroups').read_text(),
 'memory_accounting':run('systemctl','show','bmi30-core','-p','MemoryAccounting','-p','MemoryCurrent','-p','MemoryPeak'),
 'samples_data':values}))
'''

def measure(item):
    board,host=item
    result=json.loads(remote(host,SCRIPT,timeout=150))
    WORK.mkdir(exist_ok=True)
    (WORK/(board+'-measurement.json')).write_text(json.dumps(result,indent=2))
    return {'board':board,**{k:v for k,v in result.items() if k not in ('samples_data','kernel_memory_controller')}}

if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for result in pool.map(measure,HOSTS.items()):print(json.dumps(result),flush=True)
