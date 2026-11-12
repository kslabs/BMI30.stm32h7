import argparse,concurrent.futures,hashlib,json
from pathlib import Path
from audit_stability import HOSTS
from deploy import remote
from inspect_realtime import WORK
from inspect_group_display import SCRIPT as OBSERVE
from prepare_realtime import audit
from build_realtime_release import VERSION,NETWORK_RELEASE,ROOT,STREAM,OWNER,ENGINE
PREFIX='home/techaid/Documents/host/bmi30_active_runtime/project/'
SETTINGS_CHECK=r'''
import hashlib,json,pathlib,subprocess,sys
p=json.load(sys.stdin);run=lambda *a:subprocess.check_output(a,text=True).strip()
assert run('hostname')==p['host'];assert run('findmnt','-n','-o','SOURCE','/')==p['live']
root=pathlib.Path('/');mounted=False
if p['device']!=p['live']:
 assert run('blkid','-s','UUID','-o','value',p['device'])==p['uuid']
 assert subprocess.run(['findmnt','-rn','-S',p['device']],stdout=subprocess.DEVNULL).returncode!=0
 root=pathlib.Path('/mnt/bmi30-realtime-settings-audit');root.mkdir(exist_ok=False)
 run('mount','-o','ro,noload',p['device'],str(root));mounted=True
try:
 assert (root/'etc/hostname').read_text().strip()==p['host']
 d=root/'home/techaid/Documents/host';backup=root/p['backup'];metadata=[]
 for name in ('bmi30_config.json','bmi30_sel.json','bmi30_relay.json'):
  a=(backup/('settings-'+name)).read_bytes();b=(d/name).read_bytes()
  if a==b:continue
  assert name=='bmi30_sel.json',name
  old=json.loads(a);new=json.loads(b)
  assert {k:v for k,v in old.items() if k!='ts'}=={k:v for k,v in new.items() if k!='ts'},name
  metadata.append('bmi30_sel.json: ts')
 assert (backup/'settings-config.json').read_bytes()==(root/'etc/bmi30-network/config.json').read_bytes()
 print(json.dumps({'settings_preserved':True,'metadata_only_changes':metadata}))
finally:
 if mounted:run('umount',str(root));root.rmdir()
'''

def observe(board):
    data=json.loads(remote(HOSTS[board],OBSERVE,{'board':board,'host':HOSTS[board],'duration':90},timeout=120))
    (WORK/(board+'-observation.json')).write_text(json.dumps(data,indent=2))
    print(json.dumps({'board':board,'samples':len(data['samples'])}),flush=True)

def verify():
    release=json.loads((ROOT/VERSION/'release.json').read_text());manifest=json.loads((ROOT/NETWORK_RELEASE/'release.json').read_text())
    result={'bundle':VERSION,'network':manifest['version'],'firmware':release['release'].splitlines()[0].split('=',1)[1],
      'roots':[],'observation':{},'integration':json.loads((WORK/'integration.json').read_text())}
    for board,host in HOSTS.items():
        before=json.loads((WORK/(board+'-inventory-final-before.json')).read_text())
        after=json.loads((WORK/(board+'-inventory-after.json')).read_text())
        assert before['root']==after['root']
        assert after['core']['ok'] and not after['core']['stale'] and after['core']['connection']['connected']
        installs=json.loads((WORK/(board+'-install.json')).read_text())
        for root in after['roots']:
            original=next(r for r in before['roots'] if r['device']==root['device'])
            assert root['hostname']==host and root['network_release']==manifest
            assert {k:v for k,v in root['settings_hashes'].items() if k!='bmi30_sel.json'}=={k:v for k,v in original['settings_hashes'].items() if k!='bmi30_sel.json'},board+': settings changed'
            for name,digest in manifest['hashes'].items():assert root['hashes']['opt/bmi30-network/'+name]==digest,name
            for name,digest in release['hashes'].items():
                if name.startswith('host/') and '.py' in Path(name).name:assert root['hashes'][PREFIX+name]==digest,name
            for name in ('usr/local/bin/bmi30-hotspot-info-server.py','home/techaid/Documents/hotspot_info_server.py',PREFIX+'hotspot_info_server.py'):
                assert root['hashes'][name]==release['hashes']['hotspot_info_server.py']
            first=json.loads((WORK.parent/'group-display-20261005'/(board+'-inventory-led-after.json')).read_text())
            first=next(r for r in first['roots'] if r['device']==root['device'])
            protected=[n for n in first['hashes'] if n.startswith(PREFIX+'host/') and n not in (PREFIX+STREAM,PREFIX+ENGINE)]
            assert len(protected)==19
            for name in protected:assert root['hashes'][name]==first['hashes'][name],name
            for key in ('bmi30_config.json','bmi30_relay.json','network_config_sha256'):
                assert root['settings_hashes'][key]==first['settings_hashes'][key],board+': actual settings changed'
            assert 'BMI30_SPLIT_VERSION='+VERSION in root['env']['home/techaid/Documents/host/bmi30_split_active_version.env']
            for name,value in root['env'].items():
                if name.endswith('bmi30_firmware_release.env'):assert 'BMI30_FIRMWARE_VERSION='+result['firmware'] in value
            installed=next(i for i in installs if i['root']==root['device'])
            settings=json.loads(remote(host,SETTINGS_CHECK,{'host':host,'live':after['root'],'device':root['device'],
                'uuid':root.get('uuid'),'backup':installed['backup']},timeout=35))
            result['roots'].append({'board':board,'device':root['device'],'active':root['active'],'settings_preserved':True,
              'unchanged_modules':len(protected),'modules_verified':22,'backup':installed['backup'],'boot_tested':False,**settings})
        samples=json.loads((WORK/(board+'-observation.json')).read_text())['samples'];assert len(samples)>=350
        for s in samples:
            assert 'error' not in s and s['core_ok'] and not s['stale'] and s['connection']['connected'] and s['worker_alive']
            assert len(s['nodes'])==5 and all(n['available'] and n['age']<4 for n in s['nodes'])
            assert all(n['sync']['sync_seen_mask']==0x3c02 for n in s['nodes'])
            for n in s['nodes']:
                assert n['optic']['led_pattern_selected']==0 and n['optic']['led_output_active'] is False
                assert (n['optic']['reaction_enabled'],n['optic']['neighbor_reaction_enabled'],n['optic']['neighbor_device_id'])==((True,False,None) if n['id']==HOSTS['M01'] else (False,True,1))
        a,b=samples[0]['stream_stats'],samples[-1]['stream_stats']
        delta={k:b[k]-a[k] for k in ('rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail')}
        assert all(delta[k]==0 for k in delta if not k.startswith('rx_cnt')) and min(delta['rx_cnt_ch0'],delta['rx_cnt_ch1'])>0
        result['observation'][board]={'samples':len(samples),'duration_s':round(samples[-1]['time']-samples[0]['time'],1),
          'all_available':True,'stream_delta':delta}
    assert len(result['roots'])==9
    (WORK/'verification-summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('observe','audit','verify'));args=parser.parse_args()
    if args.action=='verify':verify()
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(observe if args.action=='observe' else lambda b:audit(b,'after'),HOSTS))
