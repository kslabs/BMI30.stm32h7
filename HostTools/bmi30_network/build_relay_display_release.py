"""Immutable UI-only release; all acquisition modules match bundle 1540."""
import argparse,base64,hashlib,json,subprocess,tarfile
from prepare_relay_display import HERE,WORK,BASE_VERSION,candidate
from build_display_release import ROOT,BUILD_PORTAL
from deploy import remote,SSH,CONFIG
from validate_display_portal import SCRIPT as RENDER
VERSION='2026-10-05-1705'
NETWORK_RELEASE='2026-10-05-network-12'
LABEL='Fresh own relay status over WebSocket with source expiry'
BUILDER='BMI30-D5EB8F26C'
def sources():
    baseline=json.loads((ROOT/'2026-10-05-network-11/release.json').read_text())
    files={name:HERE/name for name in baseline['hashes']}
    files['portal_relay.js']=HERE.parent/'rpi_relay_web/portal_relay.js'
    files['test_relay_live_ui.js']=HERE/'test_relay_live_ui.js'
    return files
def render():
    data=json.loads(remote(BUILDER,RENDER.replace('BMI30-7AA5CED27',BUILDER),
        {'source':base64.b64encode((WORK/'portal-candidate.py').read_bytes()).decode()},timeout=40))
    (WORK/'candidate-render-scripts.json').write_text(json.dumps(data,indent=2))
    print(json.dumps({'rendered_scripts':len(data['scripts']),'rendered_bytes':data['bytes']}),flush=True)
def build():
    candidate()
    portal=(WORK/'portal-candidate.py').read_bytes()
    script=BUILD_PORTAL.replace('fleet-portal-20261002','relay-display-20261005')
    script=script.replace('host/bmi30_split_bundles/2026-09-30-1650/project','host/bmi30_split_bundles/'+BASE_VERSION+'/project')
    script=script.replace('Portal TLS client isolation and timeouts',LABEL)
    script=script.replace('Based on 2026-09-30-1650; portal-only fix; acquisition bytes unchanged',
                          'Based on '+BASE_VERSION+'; relay UI freshness fix; acquisition bytes unchanged')
    info=json.loads(remote(BUILDER,script,{'host':BUILDER,'version':VERSION,'portal':base64.b64encode(portal).decode()},timeout=110))
    baseline=json.loads((ROOT/BASE_VERSION/'release.json').read_text())
    modules=[n for n in info['hashes'] if n.startswith('host/') and '.py' in n]
    assert len(modules)==22,len(modules)
    for n in modules:assert info['hashes'][n]==baseline['hashes'][n],n
    dest=ROOT/VERSION;dest.mkdir(exist_ok=False)
    name='BMI30-RPi-'+VERSION+'.tar.gz'
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',
        BUILDER+':/home/techaid/Documents/.tmp/relay-display-20261005/'+name,str(dest/name)],check=True)
    assert hashlib.sha256((dest/name).read_bytes()).hexdigest()==info['sha256']
    (dest/'release.json').write_text(json.dumps(info,indent=2)+'\n');(dest/'SHA256SUMS').write_text(info['sha256']+'  '+name+'\n')
    (WORK/'release.json').write_text(json.dumps(info,indent=2))
    files=sources();net=ROOT/NETWORK_RELEASE;net.mkdir(exist_ok=False)
    manifest={'version':'0.1.12','release_id':NETWORK_RELEASE,'app_bundle':VERSION,'scope':LABEL,
              'hashes':{name:hashlib.sha256(f.read_bytes()).hexdigest() for name,f in files.items()}}
    (net/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive=net/('BMI30-Network-'+NETWORK_RELEASE+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tf:
        for name,f in files.items():tf.add(f,arcname=NETWORK_RELEASE+'/'+name)
        tf.add(net/'release.json',arcname=NETWORK_RELEASE+'/release.json')
    (net/'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(json.dumps({'bundle':VERSION,'network':'0.1.12','acquisition_modules_unchanged':len(modules),'release':info['release']}),flush=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['render','build']);args=parser.parse_args()
    if args.action=='render':render()
    else:build()
