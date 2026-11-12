"""Verify all installed roots and live acquisition against the preserved baseline."""
import collections
import hashlib
import json
from audit_stability import HOSTS
from inspect_group_display import WORK
from build_display_release import VERSION, NETWORK_RELEASE, ROOT

def main():
    release=json.loads((ROOT/VERSION/'release.json').read_text())
    manifest=json.loads((ROOT/NETWORK_RELEASE/'release.json').read_text())
    firmware=next(x.split('=',1)[1] for x in release['release'].splitlines() if x.startswith('BMI30_FIRMWARE_VERSION='))
    result={'bundle':VERSION,'network':manifest['version'],'firmware':firmware,'roots':[], 'observation':{}}
    for board,host in HOSTS.items():
        before=json.loads((WORK/(board+'-inventory-led-before.json')).read_text())
        after=json.loads((WORK/(board+'-inventory-led-after.json')).read_text())
        assert before['root']==after['root']
        def core_pid(data):return data['services'].split('MainPID=',1)[1].splitlines()[0]
        assert core_pid(before)==core_pid(after),board+': core restarted'
        assert after['core']['ok'] and not after['core'].get('stale') and after['core']['connection']['connected']
        assert len(after['group']['nodes'])==5
        for node in after['group']['nodes']:
            assert node['available'] and node['optic']['led_pattern_selected'] in range(16)
        for root in after['roots']:
            original=next(r for r in before['roots'] if r['device']==root['device'])
            first=json.loads((WORK/(board+'-inventory.json')).read_text())
            first=next(r for r in first['roots'] if r['device']==root['device'])
            assert root['hostname']==host
            assert root['settings_hashes']==original['settings_hashes']==first['settings_hashes'],board+': settings changed'
            assert root['network_release']==manifest
            for name,digest in manifest['hashes'].items():assert root['hashes']['opt/bmi30-network/'+name]==digest
            for name in ('usr/local/bin/bmi30-hotspot-info-server.py','home/techaid/Documents/hotspot_info_server.py',
                         'home/techaid/Documents/host/bmi30_active_runtime/project/hotspot_info_server.py'):
                assert root['hashes'][name]==release['hashes']['hotspot_info_server.py']
            protected=[n for n in original['hashes'] if n.startswith('home/techaid/Documents/host/bmi30_active_runtime/project/host/')]
            for name in protected:assert root['hashes'][name]==original['hashes'][name]==first['hashes'][name]
            env=root['env']
            assert 'BMI30_SPLIT_VERSION='+VERSION in env['home/techaid/Documents/host/bmi30_split_active_version.env']
            for name,text in env.items():
                if name.endswith('bmi30_firmware_release.env'):assert 'BMI30_FIRMWARE_VERSION='+firmware in text
            result['roots'].append({'board':board,'device':root['device'],'active':root['active'],
                                    'acquisition_files':len(protected),'core_pid':core_pid(after),'settings_preserved':True})
        samples=json.loads((WORK/(board+'-led-final.json')).read_text())['samples']
        assert len(samples)>=200
        for s in samples:
            assert 'error' not in s
            assert s['core_ok'] and not s['stale'] and s['connection']['connected'] and s['worker_alive']
            assert len(s['nodes'])==5 and all(n['available'] and n['age'] < 4 for n in s['nodes'])
            assert all(n['sync']['sync_seen_mask']==0x3c02 for n in s['nodes'])
            for n in s['nodes']:
                assert n['optic']['led_pattern_selected']==0
                assert n['optic']['led_output_active'] is False
                config=(n['optic']['reaction_enabled'], n['optic']['neighbor_reaction_enabled'], n['optic']['neighbor_device_id'])
                assert config==((True,False,None) if n['id']==HOSTS['M01'] else (False,True,1))
        a,b=samples[0]['stream_stats'],samples[-1]['stream_stats']
        delta={k:b[k]-a[k] for k in ('rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail')}
        assert all(delta[k]==0 for k in delta if not k.startswith('rx_cnt'))
        assert delta['rx_cnt_ch0']>0 and delta['rx_cnt_ch1']>0
        result['observation'][board]={'samples':len(samples),'duration_s':round(samples[-1]['time']-samples[0]['time'],1),
                                     'all_available':True,'all_patterns':0,'stream_delta':delta}
    assert len(result['roots'])==9
    (WORK/'led-verification-summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
