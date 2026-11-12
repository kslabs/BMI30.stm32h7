"""Verify readback, unchanged acquisition/settings/PID and bounded observation."""
import json
from pathlib import Path
from build_relay_display_release import VERSION,NETWORK_RELEASE,ROOT
from prepare_relay_display import WORK
PREFIX='home/techaid/Documents/host/bmi30_active_runtime/project/'
def verify(board='S10'):
    before=json.loads((WORK/(board+'-inventory-before.json')).read_text())
    after=json.loads((WORK/(board+'-inventory-after.json')).read_text())
    release=json.loads((ROOT/VERSION/'release.json').read_text())
    manifest=json.loads((ROOT/NETWORK_RELEASE/'release.json').read_text())
    installs=json.loads((WORK/(board+'-install.json')).read_text())
    observation=json.loads((WORK/(board+'-after.json')).read_text())
    assert before['root']==after['root']
    assert before['services'].splitlines()[0]==after['services'].splitlines()[0]
    assert after['core']['ok'] and not after['core']['stale'] and after['core']['connection']['connected']
    result={'board':board,'bundle':VERSION,'network':'0.1.12',
            'firmware':release['release'].splitlines()[0].split('=',1)[1],'roots':[]}
    modules=[n for n in release['hashes'] if n.startswith('host/') and '.py' in Path(n).name]
    assert len(modules)==22
    for root in after['roots']:
        original=next(r for r in before['roots'] if r['device']==root['device'])
        installed=next(r for r in installs if r['root']==root['device'])
        assert root['hostname']==before['host'] and root['network_release']==manifest
        assert root['settings_hashes']==original['settings_hashes'],'Settings changed'
        for name,digest in manifest['hashes'].items():assert root['hashes']['opt/bmi30-network/'+name]==digest,name
        for name in modules:assert root['hashes'][PREFIX+name]==original['hashes'][PREFIX+name]==release['hashes'][name],name
        for name in ('usr/local/bin/bmi30-hotspot-info-server.py','home/techaid/Documents/hotspot_info_server.py',PREFIX+'hotspot_info_server.py'):
            assert root['hashes'][name]==release['hashes']['hotspot_info_server.py']
        assert 'BMI30_SPLIT_VERSION='+VERSION in root['env']['home/techaid/Documents/host/bmi30_split_active_version.env']
        for name,value in root['env'].items():
            if name.endswith('bmi30_firmware_release.env'):assert 'BMI30_FIRMWARE_VERSION='+result['firmware'] in value
        result['roots'].append({'device':root['device'],'active':root['active'],'modules_unchanged':22,
            'settings_preserved':True,'core_pid':installed['core_pid'],'backup':installed['backup'],'boot_tested':False})
    samples=observation['samples'];assert len(samples)>850
    for sample in samples:
        assert 'error' not in sample and sample['relay']['available']
        assert (sample['relay']['enabled'],sample['relay']['active'],sample['relay']['test_enabled'])==(False,False,False)
        own=next(n for n in sample['group'] if n['id']==before['host'])
        assert own['available'] and own['relay']['available'] and own['relay']['sequence'] is not None
    a=observation['core_before']['stream_stats'];b=observation['core']['stream_stats']
    delta={k:b[k]-a[k] for k in ('rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail')}
    assert all(delta[k]==0 for k in delta if not k.startswith('rx_cnt'))
    assert min(delta['rx_cnt_ch0'],delta['rx_cnt_ch1'])>0
    browser=json.loads((WORK/(board+'-browser-relay-visible.json')).read_text(encoding='utf-8'));assert len(browser)>=50
    for sample in browser:
        assert sample['relay']=='Relay: OFF' and sample['disabled'] is False and sample['enabled'] is False
        assert sample['duration']=='1000' and sample['live']=='Live · WebSocket' and sample['group_visible']
    result.update(samples=len(samples),duration_s=round(samples[-1]['time']-samples[0]['time'],1),
                  relay_available_all_samples=True,stream_delta=delta,browser_samples=len(browser),
                  browser_duration_s=round((browser[-1]['at']-browser[0]['at'])/1000,1),browser_stable=True)
    (WORK/'verification-summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':verify()
