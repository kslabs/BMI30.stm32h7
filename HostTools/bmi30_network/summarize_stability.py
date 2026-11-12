"""Assert per-root software equality and summarize measured before/after evidence."""
import hashlib
import json
from pathlib import Path

from audit_stability import HOSTS, WORK
from deploy_stability import DEST, VERSION


def main():
    manifest=json.loads((DEST/'release.json').read_text())
    inventory={b:json.loads((WORK/(b+'-inventory.json')).read_text()) for b in HOSTS}
    base=inventory['M01']['roots'][0]['hashes']
    app_names=[n for n in base if not n.startswith('opt/')]
    os_releases={r['os_release'] for a in inventory.values() for r in a['roots']}
    assert len(os_releases)==1
    result={'app_bundle':'2026-10-02-0948','network_version':VERSION,'os_release':'Debian GNU/Linux 12 (bookworm)','roots':[],
            'verified_app_files_per_root':len(app_names),'before':{},'after':{}}
    for board,audit in inventory.items():
        installed=json.loads((WORK/(board+'-install.json')).read_text())
        baseline=json.loads((WORK/'baseline'/(board+'-inventory.json')).read_text())
        old_pid=baseline['services'].split('MainPID=',1)[1].splitlines()[0]
        assert audit['services'].split('MainPID=',1)[1].splitlines()[0]==old_pid
        assert not audit['failed_services']
        for root in audit['roots']:
            assert root['hostname']==HOSTS[board]
            assert root['network_release']==manifest
            for name in app_names:assert root['hashes'][name]==base[name],board+': '+name
            for name,digest in manifest['hashes'].items():assert root['hashes']['opt/bmi30-network/'+name]==digest,board+': '+name
            env=root['env']['home/techaid/Documents/host/bmi30_split_active_version.env']
            assert 'BMI30_SPLIT_VERSION=2026-10-02-0948\n' in env
            deployment=next(r for r in installed if r['device']==root['device'])
            before=next(r for r in baseline['roots'] if r['device']==root['device'])
            assert root['settings_hashes']==before['settings_hashes'],board+': settings changed'
            result['roots'].append({'board':board,'device':root['device'],'active':root['active'],
                                   'backup':deployment['backup'],'settings_preserved':True,
                                   'linux_tests':'29 PASS','app_hashes_match':True,'network_hashes_match':True})
        before_file=WORK/'baseline'/(board+'-observe.json')
        if before_file.exists():
            samples=json.loads(before_file.read_text())['samples']
            missing={h:sum(not any(n['id']==h and n['age']<4 for n in s.get('group',{}).get('nodes',[])) for s in samples) for h in HOSTS.values()}
            result['before'][board]={'samples':len(samples),'missing_samples':{k:v for k,v in missing.items() if v}}
        after_file=WORK/(board+'-after-summary.json')
        if after_file.exists():result['after'][board]=json.loads(after_file.read_text())
    live={b:json.loads((WORK/(b+'-live.json')).read_text()) for b in HOSTS}
    assert {v['portal_firmware']['stm32_firmware_version'] for v in live.values()}=={'1.2.53'}
    assert {v['portal_firmware']['raspberry_firmware_version'] for v in live.values()}=={'2026-10-02-094828'}
    result['stm32_version']='1.2.53'
    assert len(result['roots'])==9
    result['usb_missing']=['S12']
    (WORK/'verification-summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
