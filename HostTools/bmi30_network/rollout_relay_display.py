"""Roll out the verified relay display release to each currently attached OS root."""
import argparse,base64,hashlib,json,subprocess
from pathlib import Path
from audit_stability import HOSTS
from build_relay_display_release import VERSION,NETWORK_RELEASE,ROOT,sources
from deploy_relay_display_release import installer
from prepare_relay_display import WORK,BASE_VERSION
from deploy import remote,SSH,CONFIG
PREFIX='home/techaid/Documents/host/bmi30_active_runtime/project/'
def release():
    info=json.loads((ROOT/VERSION/'release.json').read_text())
    manifest=json.loads((ROOT/NETWORK_RELEASE/'release.json').read_text())
    archive=ROOT/VERSION/('BMI30-RPi-'+VERSION+'.tar.gz')
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==info['sha256']
    files=sources()
    for name,digest in manifest['hashes'].items():assert hashlib.sha256(files[name].read_bytes()).hexdigest()==digest,name
    return info,manifest,archive,files
def inventory_summary():
    out=[]
    for board in HOSTS:
        inv=json.loads((WORK/(board+'-inventory-rollout-before.json')).read_text())
        out.append({'board':board,'boot_root':inv['root'],'disks':[{k:d.get(k) for k in ('name','path','tran','size')} for d in inv['disks']['blockdevices']],
          'roots':[{'device':r['device'],'active':r['active'],'version':r['network_release']['version'],
                    'bundle_env':r['env']['home/techaid/Documents/host/bmi30_split_active_version.env']} for r in inv['roots']]})
    print(json.dumps(out,indent=2))
def install(board):
    inv=json.loads((WORK/(board+'-inventory-rollout-before.json')).read_text())
    info,manifest,archive,files=release();results=[];uploaded=False
    for root in sorted(inv['roots'],key=lambda r:r['active']):
        assert root['hostname']==HOSTS[board]
        modules=[n for n in info['hashes'] if n.startswith('host/') and '.py' in Path(n).name]
        assert len(modules)==22
        for name in modules:assert root['hashes'][PREFIX+name]==info['hashes'][name],board+': acquisition differs: '+name
        if root['network_release']['version']==manifest['version']:
            assert root['network_release']==manifest
            for n,digest in manifest['hashes'].items():assert root['hashes']['opt/bmi30-network/'+n]==digest,n
            for n in ('usr/local/bin/bmi30-hotspot-info-server.py','home/techaid/Documents/hotspot_info_server.py',PREFIX+'hotspot_info_server.py'):
                assert root['hashes'][n]==info['hashes']['hotspot_info_server.py'],n
            assert 'BMI30_SPLIT_VERSION='+VERSION in root['env']['home/techaid/Documents/host/bmi30_split_active_version.env']
            result={'host':HOSTS[board],'root':root['device'],'version':VERSION,'already_installed':True,
                    'settings_preserved':True,'modules_unchanged':22}
        else:
            assert root['network_release']['version']=='0.1.11'
            assert 'BMI30_SPLIT_VERSION='+BASE_VERSION in root['env']['home/techaid/Documents/host/bmi30_split_active_version.env']
            if not uploaded:
                subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',str(archive),HOSTS[board]+':/tmp/'+archive.name],check=True)
                uploaded=True
            payload={'host':HOSTS[board],'version':VERSION,'live_root':inv['root'],
              'device':None if root['active'] else root['device'],'uuid':root.get('uuid'),
              'archive_sha256':info['sha256'],'hashes':info['hashes'],
              'expected_portal':[root['hashes']['usr/local/bin/bmi30-hotspot-info-server.py']],
              'expected':root['hashes'],'network_manifest':manifest,
              'network_files':{n:base64.b64encode(files[n].read_bytes()).decode() for n in manifest['hashes']}}
            result=json.loads(remote(HOSTS[board],installer(),payload,timeout=130))
        results.append(result);(WORK/(board+'-rollout-install.json')).write_text(json.dumps(results,indent=2))
        print(json.dumps(result),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['summary','install']);p.add_argument('--board',choices=HOSTS);a=p.parse_args()
    if a.action=='summary':inventory_summary()
    else:
        for board in ([a.board] if a.board else ['S10','S12','S13','S11','M01']):install(board)
