"""Bounded live checks, regression comparison and final fleet readback."""
import concurrent.futures
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest

from audit_stability import HOSTS, WORK
from deploy import remote

LIVE = r'''
import json,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin)
def api(port):
 with urllib.request.urlopen('http://127.0.0.1'+port+'/api/status',timeout=4) as r:return json.load(r)
assert subprocess.check_output(['hostname'],text=True).strip()==p['host']
s=json.loads(pathlib.Path('/run/bmi30-network/status.json').read_text())
g=json.loads(pathlib.Path('/run/bmi30-network/group.json').read_text())
c=api(':8765');portal=api('')
cache=json.loads(pathlib.Path('/tmp/bmi30_device_state.json').read_text())
print(json.dumps({'board':p['board'],'time':time.time(),'version':s['version'],'history':s.get('history'),
 'network_nodes':len(s.get('nodes',[])),'group_nodes':len(g.get('nodes',[])),'coordinator':s.get('coordinator'),
 'local':{k:s['local'].get(k) for k in ('ssid','signal','freq','internet','clients','addresses')},
 'core_ok':bool(c.get('ok') and not c.get('stale') and c.get('connection',{}).get('connected')),
 'stats':c.get('stream_stats'),'cache_keys':list(cache),'portal_firmware':{k:v for k,v in portal.items() if 'firmware' in k},
 'cache_firmware':{k:v for k,v in cache.items() if 'firmware' in k},
 'group_unavailable':[{'id':n['id'],'cache_age':n.get('cache_age_s')} for n in g['nodes'] if not n.get('available')],
 'core_pid':subprocess.check_output(['systemctl','show','bmi30-core','-p','MainPID','--value'],text=True).strip()}))
'''


def live():
    def one(item):
        board,host=item
        try:
            result=json.loads(remote(host,LIVE,{'board':board,'host':host},timeout=20))
            (WORK/(board+'-live.json')).write_text(json.dumps(result,indent=2))
            print(json.dumps({'board':board,'network_version':result['version'],
                              'app_version':result['portal_firmware'].get('raspberry_firmware_version'),
                              'stm32_version':result['portal_firmware'].get('stm32_firmware_version'),
                              'group_nodes':result['group_nodes'],'coordinator':result['coordinator'],
                              'ssid':result['local']['ssid'],'core_ok':result['core_ok'],
                              'core_pid':result['core_pid'],'history':result['history'],
                              'group_unavailable':result['group_unavailable']}),flush=True)
        except Exception as exc:
            print(json.dumps({'board':board,'error':str(exc)}),flush=True)
            raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(one,HOSTS.items()))


def regressions():
    source=r'''
from pathlib import Path
import json,sys
p=json.load(sys.stdin)
print(Path('/var/backups/bmi30-network-stability/20261005-125539',p['name']).read_text())
'''
    for name in ('policy','group'):
        data=remote(HOSTS['M01'],source,{'name':name+'.py'},timeout=15)
        (WORK/('before-'+name+'.py')).write_bytes(data)
        spec=importlib.util.spec_from_file_location(name,WORK/('before-'+name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);sys.modules[name]=module
    import test_stability,test_group
    names=['test_unknown_wan_does_not_create_an_improvement_during_latency_spikes']
    suite=unittest.TestSuite([test_stability.StabilityTests(n) for n in names])
    suite.addTest(test_group.GroupTest('test_cache_published_during_status_request_does_not_disconnect_owner'))
    output=io.StringIO();result=unittest.TextTestRunner(stream=output,verbosity=2).run(suite)
    (WORK/'before-regressions.log').write_text(output.getvalue())
    assert len(result.failures)==2 and not result.errors,output.getvalue()
    print('Both reproduced regression scenarios FAIL on the original device sources, as expected.')


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='regressions':regressions()
    else:live()
