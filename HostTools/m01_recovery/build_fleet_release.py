"""Build an immutable portal-only release using the device's standard builder."""
import base64
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bmi30_network'))
from deploy import remote, SSH, CONFIG

VERSION = '2026-10-02-0948'
WORK = Path(__file__).resolve().parents[2] / '.tmp/fleet-portal-20261002'
BUILDER_HOST = 'BMI30-7AA5CED27'
SCRIPT = r'''
import ast,base64,hashlib,json,pathlib,shutil,subprocess,sys
p=json.load(sys.stdin);v=p['version'];d=pathlib.Path('/home/techaid/Documents')
assert subprocess.check_output(['hostname'],text=True).strip()==p['host']
w=d/'.tmp/fleet-portal-20261002';w.mkdir(exist_ok=True)
src=d/'host/bmi30_split_bundles/2026-09-30-1650/project';stage=w/('stage-'+v)
assert not stage.exists()
shutil.copytree(src,stage,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','player_recordings','.lgd-*'))
portal=base64.b64decode(p['portal']);ast.parse(portal)
(stage/'hotspot_info_server.py').write_bytes(portal)
(stage/'host/bmi30_split_active_version.env').write_text('\n'.join([
 'BMI30_CORE_PATH=host/BMI30.001.py.2026-09-30-1650',
 'BMI30_ENGINE_SOURCE=host/BMI30.200.py.2026-09-30-1650',
 'BMI30_GUI_PATH=host/BMI30.GUI.001.py.2026-09-30-1650',
 'BMI30_PORTAL_PATH=hotspot_info_server.py','']))
cmd=['bash',str(d/'utilities/create_bmi30_split_bundle.sh'),'--id',v,
 '--label','Portal TLS client isolation and timeouts',
 '--origin','Based on 2026-09-30-1650; portal-only fix; acquisition bytes unchanged',
 '--source-project',str(stage),'--portal-source','hotspot_info_server.py','--without-player','--for-server']
r=subprocess.run(cmd,capture_output=True,text=True);(w/'build.log').write_text(r.stdout+r.stderr)
assert r.returncode==0,r.stdout+r.stderr
r=subprocess.run(['bash',str(d/'switch_bmi30_split_versions.sh'),'--validate',v],capture_output=True,text=True)
(w/'validate.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stdout+r.stderr
b=d/'host/bmi30_split_bundles'/v;a=w/('BMI30-RPi-'+v+'.tar.gz')
subprocess.run(['tar','-czf',str(a),'-C',str(b.parent),v],check=True)
hashes={str(x.relative_to(b/'project')):hashlib.sha256(x.read_bytes()).hexdigest()
 for x in (b/'project').rglob('*') if x.is_file()}
print(json.dumps(dict(version=v,sha256=hashlib.sha256(a.read_bytes()).hexdigest(),
 bytes=a.stat().st_size,hashes=hashes,release=(b/'project/host/bmi30_firmware_release.env').read_text(),
 manifest=(b/'manifest.env').read_text())))
'''

if __name__ == '__main__':
    source = WORK.parent / 'm01-repair-20261002/portal-fixed.py'
    info = json.loads(remote(BUILDER_HOST, SCRIPT, {'host': BUILDER_HOST, 'version': VERSION,
        'portal': base64.b64encode(source.read_bytes()).decode()}, timeout=110))
    (WORK/'release.json').write_text(json.dumps(info,indent=2))
    dest = WORK.parents[1]/'FirmwareReleases/RaspberryPi'/VERSION
    dest.mkdir(exist_ok=False)
    name = 'BMI30-RPi-'+VERSION+'.tar.gz'
    subprocess.run([str(SSH.with_name('scp.exe')),'-F',str(CONFIG),'-o','BatchMode=yes',
        BUILDER_HOST+':/home/techaid/Documents/.tmp/fleet-portal-20261002/'+name,str(dest/name)],check=True)
    print(json.dumps({k:info[k] for k in ('version','sha256','bytes','release')}))
