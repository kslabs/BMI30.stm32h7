"""Persistent read-only observation that survives planned Wi-Fi associations."""
import argparse
import concurrent.futures
import json

from audit_stability import HOSTS, WORK, OBSERVE
from deploy import remote

PREFIX = '/tmp/bmi30-group-stability-20261005-after'
START = r'''
import json,pathlib,subprocess,sys
p=json.load(sys.stdin);assert subprocess.check_output(['hostname'],text=True).strip()==p['host']
source=pathlib.Path(p['prefix']+'.py');assert not source.exists()
code=p['code'].replace('p=json.load(sys.stdin)', 'p=json.loads('+repr(json.dumps(p['params']))+')',1)
compile(code,str(source),'exec');source.write_text(code);source.chmod(0o600)
r=subprocess.run(['systemd-run','--quiet','--collect','--unit=bmi30-group-stability-20261005-after',
 '--property=Nice=15','--property=MemoryMax=160M','--property=RuntimeMaxSec='+str(p['params']['duration']+60),
 '--property=StandardOutput=file:'+p['prefix']+'.json','--property=StandardError=file:'+p['prefix']+'.log',
 '/usr/bin/python3',str(source)],capture_output=True,text=True)
assert r.returncode==0,r.stderr
print(json.dumps({'board':p['params']['board'],'started':True,'duration':p['params']['duration'],'settle':p['params']['settle']}))
'''


def one(item,action,duration,settle):
    board,host=item
    if action=='start':
        params={'host':host,'board':board,'duration':duration,'settle':settle,
                'hosts':list(HOSTS.values()),'progress_path':PREFIX+'-progress.json'}
        result=json.loads(remote(host,START,{'host':host,'prefix':PREFIX,'code':OBSERVE,'params':params},timeout=20))
    else:
        suffix='-progress.json' if action=='progress' else '.json'
        script="import pathlib,json,sys; p=json.load(sys.stdin); print(pathlib.Path(p['path']).read_text())"
        result=json.loads(remote(host,script,{'path':PREFIX+suffix},timeout=15))
        if action=='collect':
            (WORK/(board+'-after-observe.json')).write_text(json.dumps(result,indent=2))
            window=[s for s in result['samples'] if s['time']>=result['samples'][0]['time']+settle]
            import collections
            missing=collections.Counter();unavailable=collections.Counter()
            for s in window:
                nodes={n['id']:n for n in s.get('group',{}).get('nodes',[])}
                for ident in HOSTS.values():
                    if ident not in nodes or nodes[ident]['age']>=4:missing[ident]+=1
                    elif not nodes[ident].get('available'):unavailable[ident]+=1
            first,last=window[0]['core']['stream_stats'],window[-1]['core']['stream_stats']
            result={'board':board,'samples':len(window),'seconds':round(window[-1]['time']-window[0]['time'],1),
                    'missing':dict(missing),'unavailable':dict(unavailable),
                    'stream_delta':{k:last[k]-first[k] for k in ('rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail')},
                    'max_group_file_age':max(s.get('group',{}).get('file_age',0) for s in window),
                    'max_frame_age_s':max(s.get('core',{}).get('connection',{}).get('last_frame_age_s',999) for s in window),
                    'core_issues':sum(not s.get('core',{}).get('ok') or s['core'].get('stale') or not s.get('worker_alive')
                                     or not s.get('core',{}).get('connection',{}).get('connected')
                                     or s.get('core',{}).get('connection',{}).get('last_frame_age_s',999)>=2 for s in window),
                    'history':window[-1].get('status',{}).get('history')}
            (WORK/(board+'-after-summary.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','progress','collect'])
    parser.add_argument('--duration',type=int,default=660)
    parser.add_argument('--settle',type=int,default=180)
    args=parser.parse_args()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda item:one(item,args.action,args.duration,args.settle),HOSTS.items()))
