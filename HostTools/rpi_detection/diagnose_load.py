"""Read-only CPU, detector latency and rejection sampling on the five instruments."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bmi30_network'))
from deploy import remote
from capture_status import HOSTS, summarize

REMOTE = r'''
import json,os,pathlib,subprocess,sys,time,urllib.request
params=json.load(sys.stdin)
pid=subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()
def cpu():
 lines=pathlib.Path('/proc/stat').read_text().splitlines()
 ticks={x.split()[0]:[int(v) for v in x.split()[1:9]] for x in lines if x.startswith('cpu')}
 threads={}
 for p in (pathlib.Path('/proc')/pid/'task').glob('*/stat'):
  try:
   line=p.read_text();fields=line[line.rfind(')')+2:].split()
   threads[p.parent.name]={'name':line[line.find('(')+1:line.rfind(')')],'ticks':int(fields[11])+int(fields[12])}
  except (OSError,ValueError):pass
 temp=pathlib.Path('/sys/class/thermal/thermal_zone0/temp')
 return dict(epoch=time.monotonic(),ticks=ticks,threads=threads,temp_c=int(temp.read_text())/1000)
def throttle():
 try:return subprocess.check_output(['vcgencmd','get_throttled'],text=True).strip()
 except Exception:return None
start=time.monotonic();records=[dict(kind='start',core_pid=pid,epoch=time.time(),throttle=throttle(),clock_ticks=os.sysconf('SC_CLK_TCK'),cpu_count=os.cpu_count())]
next_cpu=start
while time.monotonic()-start<params['seconds']:
 before=time.monotonic()
 try:
  with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3) as r:s=json.load(r)
  rec=dict(kind='sample',elapsed_s=time.monotonic()-start,epoch=time.time(),request_ms=(time.monotonic()-before)*1000,status=s)
  if before>=next_cpu:rec['cpu']=cpu();next_cpu=before+1
  records.append(rec)
 except Exception as e:records.append(dict(kind='error',error=str(e)))
 time.sleep(max(0,params['interval']-(time.monotonic()-before)))
records.append(dict(kind='end',core_pid=subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip(),epoch=time.time(),throttle=throttle()))
print(json.dumps(records))
'''


def quantiles(values):
    values = sorted(v for v in values if isinstance(v, (int, float)))
    return dict(mean=statistics.mean(values), p95=values[int((len(values)-1)*.95)], max=max(values)) if values else None


def analyze(records):
    summary = summarize(records)
    samples = [r for r in records if r.get('kind') == 'sample']
    cpus = [r['cpu'] for r in samples if 'cpu' in r]
    cpu_busy, thread_busy = [], []
    for a, b in zip(cpus, cpus[1:]):
        dt = b['epoch']-a['epoch']
        ticks = [y-x for x, y in zip(a['ticks']['cpu'], b['ticks']['cpu'])]
        cpu_busy.append(100*(sum(ticks)-ticks[3]-ticks[4])/sum(ticks))
        thread_busy.append({tid: 100*(t['ticks']-a['threads'][tid]['ticks'])/records[0]['clock_ticks']/dt
                            for tid,t in b['threads'].items() if tid in a['threads']})
    summary['cpu_busy_pct_all_cores'] = quantiles(cpu_busy)
    summary['core_process_pct_one_cpu'] = quantiles([sum(t.values()) for t in thread_busy])
    summary['busiest_core_thread_pct_one_cpu'] = quantiles([max(t.values(),default=0) for t in thread_busy])
    summary['temperature_c'] = quantiles([c['temp_c'] for c in cpus])
    summary['throttle'] = [records[0].get('throttle'),records[-1].get('throttle')]
    summary['latency_ms'] = {k:quantiles([r['status'].get('detector',{}).get('worker',{}).get('latency',{}).get(k) for r in samples])
                             for k in ('worker_ms','delivery_ms','total_ms')}
    summary['width_sampling'] = {}
    for ch in (0,1):
        seen=set();observed=[];sequence_resets=0;previous=None;missed=0
        for r in samples:
            d=r['status'].get('detector',{});seq=d.get('worker',{}).get(f'source_seq{ch}')
            if seq is None:continue
            if previous is not None and seq < previous:sequence_resets+=1;seen.clear()
            if previous is not None and seq > previous+1:missed+=seq-previous-1
            previous=seq
            if seq in seen:continue
            seen.add(seq);observed.append(d)
        likely=[d for d in observed if d.get(f'peak_position_ok{ch}') and d.get(f'lvl{ch}',0)>=d.get(f'trigger{ch}',float('inf'))]
        summary['width_sampling'][str(ch)] = dict(distinct_sampled_frames=len(observed),
            sequence_resets=sequence_resets,unsampled_source_sequences=missed,
            level_and_position_pass=len(likely),width_fail=sum(not d.get(f'base_width{ch}',{}).get('ok',True) for d in likely),
            widths=[d.get(f'base_width{ch}',{}) for d in likely],
            confirm_need=sorted({d.get(f'confirm_need{ch}',0) for d in observed}))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--boards',choices=list(HOSTS),nargs='+',default=list(HOSTS))
    parser.add_argument('--seconds',type=float,default=40)
    parser.add_argument('--interval',type=float,default=.2)
    args=parser.parse_args()
    if not 5<=args.seconds<=120 or not .02<=args.interval<=2:parser.error('Invalid observation interval or duration')
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    def observe(board):
        records=json.loads(remote(HOSTS[board],REMOTE,{'seconds':args.seconds,'interval':args.interval},timeout=args.seconds+25))
        (out/(board+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in records)+'\n')
        summary=analyze(records)
        (out/(board+'-summary.json')).write_text(json.dumps(summary,indent=2))
        compact={k:summary[k] for k in ('received_fps','stream_delta','worker_delta','over_100ms_delta','cpu_busy_pct_all_cores','core_process_pct_one_cpu','busiest_core_thread_pct_one_cpu','temperature_c','throttle','latency_ms')}
        compact['width_sampling']={ch:{k:v for k,v in s.items() if k!='widths'} for ch,s in summary['width_sampling'].items()}
        return dict(board=board,**compact)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(args.boards)) as pool:
        for result in pool.map(observe,args.boards):print(json.dumps(result),flush=True)


if __name__=='__main__':main()
