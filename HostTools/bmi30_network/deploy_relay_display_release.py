"""Guarded portal/network-only install; preserve acquisition PID and root settings."""
import argparse,base64,hashlib,json,subprocess
import deploy_display_release as old
from build_relay_display_release import VERSION,NETWORK_RELEASE,ROOT,LABEL,sources
from prepare_relay_display import WORK,BASE_VERSION
from audit_stability import HOSTS
from deploy import remote,SSH,CONFIG
def installer():
    old.BASE_VERSION=BASE_VERSION;old.LABEL=LABEL
    script=old.installer().replace('bmi30-group-display-20261005','bmi30-relay-display-20261005').replace('bmi30-group-display','bmi30-relay-display')
    script=script.replace("'test_stability'],cwd=network", "'test_stability','test_realtime'],env={**os.environ,'BMI30_TEST_ENGINE':str(active/'host/BMI30.200.py.2026-09-30-1650')},cwd=network")
    return script
def install(board):
    inv=json.loads((WORK/(board+'-inventory-before.json')).read_text())
    info=json.loads((WORK/'release.json').read_text())
    manifest=json.loads((ROOT/NETWORK_RELEASE/'release.json').read_text())
    archive=ROOT/VERSION/('BMI30-RPi-'+VERSION+'.tar.gz')
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==info['sha256']
    files=sources()
    for name,digest in manifest['hashes'].items():assert hashlib.sha256(files[name].read_bytes()).hexdigest()==digest,name
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',str(archive),HOSTS[board]+':/tmp/'+archive.name],check=True)
    results=[]
    for root in sorted(inv['roots'],key=lambda r:r['active']):
        assert root['hostname']==HOSTS[board] and root['network_release']['version']=='0.1.11'
        expected=root['hashes']
        payload={'host':HOSTS[board],'version':VERSION,'live_root':inv['root'],
          'device':None if root['active'] else root['device'],'uuid':root.get('uuid'),
          'archive_sha256':info['sha256'],'hashes':info['hashes'],
          'expected_portal':[expected['usr/local/bin/bmi30-hotspot-info-server.py']],
          'expected':expected,'network_manifest':manifest,
          'network_files':{n:base64.b64encode(files[n].read_bytes()).decode() for n in manifest['hashes']}}
        result=json.loads(remote(HOSTS[board],installer(),payload,timeout=130));results.append(result)
        (WORK/(board+'-install.json')).write_text(json.dumps(results,indent=2));print(json.dumps(result),flush=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('board',choices=HOSTS);args=parser.parse_args();install(args.board)
