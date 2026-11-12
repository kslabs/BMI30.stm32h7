"""Read-only readback and bounded acquisition/group verification of the rollout."""
import argparse,concurrent.futures,json
from pathlib import Path
from audit_stability import HOSTS
from deploy import remote
from prepare_relay_display import WORK
from rollout_relay_display import release,PREFIX
from inspect_group_display import SCRIPT as GROUP_OBSERVE
OBSERVE=GROUP_OBSERVE.replace("'sensors':n.get('sensors')}","'sensors':n.get('sensors'),'relay':n.get('relay')}")
def observe(board,duration):
    data=json.loads(remote(HOSTS[board],OBSERVE,{'board':board,'host':HOSTS[board],'duration':duration},timeout=duration+30))
    (WORK/(board+'-rollout-observation.json')).write_text(json.dumps(data,indent=2))
    print(json.dumps({'board':board,'samples':len(data['samples'])}),flush=True)
def verify():
    info,manifest,_,_=release()
    result={'bundle':info['version'],'network':manifest['version'],'firmware':info['release'].splitlines()[0].split('=',1)[1],
            'scope':'5 eMMC and S10 USB; other USB excluded by user','roots':[],'observation':{}}
    modules=[n for n in info['hashes'] if n.startswith('host/') and '.py' in Path(n).name];assert len(modules)==22
    for board,host in HOSTS.items():
        before=json.loads((WORK/(board+'-inventory-rollout-before.json')).read_text())
        after=json.loads((WORK/(board+'-inventory-rollout-after.json')).read_text())
        installs=json.loads((WORK/(board+'-rollout-install.json')).read_text())
        assert before['root']==after['root']
        assert before['services'].splitlines()[0]==after['services'].splitlines()[0],board+': core PID changed'
        assert after['core']['ok'] and not after['core']['stale'] and after['core']['connection']['connected']
        assert len(after['roots'])==(2 if board=='S10' else 1)
        for root in after['roots']:
            original=next(r for r in before['roots'] if r['device']==root['device'])
            installed=next(i for i in installs if i['root']==root['device'])
            assert root['hostname']==host and root['network_release']==manifest
            assert root['settings_hashes']==original['settings_hashes'],board+': settings changed'
            for n,digest in manifest['hashes'].items():assert root['hashes']['opt/bmi30-network/'+n]==digest,n
            for n in modules:assert root['hashes'][PREFIX+n]==original['hashes'][PREFIX+n]==info['hashes'][n],n
            for n in ('usr/local/bin/bmi30-hotspot-info-server.py','home/techaid/Documents/hotspot_info_server.py',PREFIX+'hotspot_info_server.py'):
                assert root['hashes'][n]==info['hashes']['hotspot_info_server.py'],n
            assert 'BMI30_SPLIT_VERSION='+info['version'] in root['env']['home/techaid/Documents/host/bmi30_split_active_version.env']
            for n,value in root['env'].items():
                if n.endswith('bmi30_firmware_release.env'):assert 'BMI30_FIRMWARE_VERSION='+result['firmware'] in value
            result['roots'].append({'board':board,'device':root['device'],'active':root['active'],
                'modules_unchanged':22,'settings_preserved':True,'core_pid_unchanged':True,
                'backup':installed.get('backup'),'already_installed':installed.get('already_installed',False),'boot_tested':False})
        samples=json.loads((WORK/(board+'-rollout-observation.json')).read_text())['samples']
        assert len(samples)>=350,board+': too few samples'
        max_relay_age=0
        for s in samples:
            assert 'error' not in s and s['core_ok'] and not s['stale'] and s['connection']['connected'] and s['worker_alive'],board+': owner unavailable'
            assert len(s['nodes'])==5 and {n['id'] for n in s['nodes']}==set(HOSTS.values())
            assert all(n['available'] and n['age']<4 for n in s['nodes']),board+': missing peer'
            own=next(n for n in s['nodes'] if n['id']==host)
            assert own['relay']['available'] and own['relay']['sequence'] is not None,board+': relay unavailable'
            max_relay_age=max(max_relay_age,s['time']-own['relay']['observed_at'])
        a,b=samples[0]['stream_stats'],samples[-1]['stream_stats']
        delta={k:b[k]-a[k] for k in ('rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail')}
        assert all(delta[k]==0 for k in delta if not k.startswith('rx_cnt')),board+': USB errors'
        assert min(delta['rx_cnt_ch0'],delta['rx_cnt_ch1'])>0
        result['observation'][board]={'samples':len(samples),'duration_s':round(samples[-1]['time']-samples[0]['time'],1),
            'all_five_available':True,'own_relay_available':True,'max_relay_age_s':round(max_relay_age,3),'stream_delta':delta}
    assert len(result['roots'])==6
    (WORK/'fleet-verification-summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['observe','verify']);p.add_argument('--duration',type=int,default=90);a=p.parse_args()
    if a.action=='verify':verify()
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(lambda b:observe(b,a.duration),HOSTS))
