"""Immutable local LED policy and portal code-version release."""
import argparse,base64,hashlib,json,subprocess,tarfile
from pathlib import Path
from prepare_local_led import candidate,ENGINE
from prepare_led_page import WORK,BASE_VERSION,ROOT,embed
from build_relay_display_release import HERE,BUILD_PORTAL,sources as old_sources
from validate_display_portal import SCRIPT as OLD_RENDER
from deploy import remote,SSH,CONFIG
VERSION='2026-10-05-1930'
NETWORK_RELEASE='2026-10-05-network-13'
NETWORK_VERSION='0.1.13'
LABEL='Automatic LEDs require local ADC detection; open portal code version check'
BUILDER='BMI30-D5EB8F26C'
def sources():
    files=old_sources()
    for name in ('portal_ui_version.js','test_portal_ui_version.js','test_local_led.py'):files[name]=HERE/name
    return files
def render():
    script=OLD_RENDER.replace('import ast,base64,json,runpy,re,sys','import ast,base64,hashlib,json,runpy,re,sys')
    script=script.replace('tree=ast.parse(source)',"namespace['_PORTAL_UI_SOURCE_SHA256']=hashlib.sha256(source.encode()).hexdigest()\ntree=ast.parse(source)")
    data=json.loads(remote(BUILDER,script.replace('BMI30-7AA5CED27',BUILDER),
        {'source':base64.b64encode((WORK/'portal-candidate.py').read_bytes()).decode()},timeout=40))
    (WORK/'candidate-render-scripts.json').write_text(json.dumps(data,indent=2))
    print(json.dumps({'rendered_scripts':len(data['scripts']),'rendered_bytes':data['bytes']}),flush=True)
def build():
    candidate()
    portal=(WORK/'portal-candidate.py').read_bytes();engine=(WORK/'engine-local-led.py').read_bytes()
    script=BUILD_PORTAL.replace('fleet-portal-20261002','local-led-20261005')
    script=script.replace('host/bmi30_split_bundles/2026-09-30-1650/project','host/bmi30_split_bundles/'+BASE_VERSION+'/project')
    marker="(stage/'hotspot_info_server.py').write_bytes(portal)"
    assert script.count(marker)==1
    script=script.replace(marker,marker+"\n(stage/'"+ENGINE+"').write_bytes(base64.b64decode(p['engine']))")
    script=script.replace('Portal TLS client isolation and timeouts',LABEL)
    script=script.replace('Based on 2026-09-30-1650; portal-only fix; acquisition bytes unchanged',
        'Based on '+BASE_VERSION+'; local LED permission guard; detector filters and USB unchanged')
    info=json.loads(remote(BUILDER,script,{'host':BUILDER,'version':VERSION,
        'portal':base64.b64encode(portal).decode(),'engine':base64.b64encode(engine).decode()},timeout=110))
    baseline=json.loads((ROOT/BASE_VERSION/'release.json').read_text())
    modules=[n for n in info['hashes'] if n.startswith('host/') and '.py' in Path(n).name]
    assert len(modules)==22
    for name in modules:
        if name!=ENGINE:assert info['hashes'][name]==baseline['hashes'][name],name
    assert info['hashes'][ENGINE]==hashlib.sha256(engine).hexdigest()
    dest=ROOT/VERSION;dest.mkdir(exist_ok=False);name='BMI30-RPi-'+VERSION+'.tar.gz'
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',
        BUILDER+':/home/techaid/Documents/.tmp/local-led-20261005/'+name,str(dest/name)],check=True)
    assert hashlib.sha256((dest/name).read_bytes()).hexdigest()==info['sha256']
    (dest/'release.json').write_text(json.dumps(info,indent=2)+'\n');(dest/'SHA256SUMS').write_text(info['sha256']+'  '+name+'\n')
    (WORK/'release.json').write_text(json.dumps(info,indent=2))
    net=ROOT/NETWORK_RELEASE;net.mkdir(exist_ok=False);files=sources()
    manifest={'version':NETWORK_VERSION,'release_id':NETWORK_RELEASE,'app_bundle':VERSION,'scope':LABEL,
              'hashes':{name:hashlib.sha256(f.read_bytes()).hexdigest() for name,f in files.items()}}
    (net/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive=net/('BMI30-Network-'+NETWORK_RELEASE+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tf:
        for name,f in files.items():tf.add(f,arcname=NETWORK_RELEASE+'/'+name)
        tf.add(net/'release.json',arcname=NETWORK_RELEASE+'/release.json')
    (net/'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(json.dumps({'bundle':VERSION,'network':NETWORK_VERSION,'unchanged_modules':21,'release':info['release']}),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['render','build']);a=p.parse_args()
    if a.action=='render':render()
    else:build()
