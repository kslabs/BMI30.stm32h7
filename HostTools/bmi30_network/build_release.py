"""Create a portal-only websplit release with byte-identical acquisition code."""
import base64
import json
from pathlib import Path
import subprocess

from deploy import HOSTS, WORK, remote, SSH, CONFIG
from patch_group_portal import patch

VERSION = '2026-09-30-1449'
SCRIPT = '''
import ast,base64,hashlib,json,pathlib,shutil,subprocess,sys
p=json.load(sys.stdin);version=p['version'];docs=pathlib.Path('/home/techaid/Documents');work=docs/'.tmp/network-20260930'
source=docs/'host/bmi30_split_bundles/2026-09-30-1135/project';stage=work/('stage-'+version)
if not stage.exists():shutil.copytree(source,stage,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','player_recordings','.lgd-*'))
portal=base64.b64decode(p['portal']);ast.parse(portal);(stage/'hotspot_info_server.py').write_bytes(portal)
(stage/'host/bmi30_split_active_version.env').write_text('\\n'.join([
 'BMI30_CORE_PATH=host/BMI30.001.py.2026-09-30-1135',
 'BMI30_ENGINE_SOURCE=host/BMI30.200.py.2026-09-30-1135',
 'BMI30_GUI_PATH=host/BMI30.GUI.001.py.2026-09-30-1135',
 'BMI30_PORTAL_PATH=hotspot_info_server.py','']))
# Existing paired core/engine filenames and bytes are retained: this release
# changes the portal only and can be applied without restarting acquisition.
subprocess.run(['bash',str(docs/'utilities/create_bmi30_split_bundle.sh'),'--id',version,
 '--label','BMI30 group channel, per-device controls and RS485 fallback',
 '--origin','Portal-only release from 2026-09-30-1135; acquisition files unchanged; bmi30-network service required for fast remote control',
 '--source-project',str(stage),'--portal-source','hotspot_info_server.py','--without-player','--for-server'],check=True,stdout=subprocess.PIPE)
subprocess.run(['bash',str(docs/'switch_bmi30_split_versions.sh'),'--validate',version],check=True,stdout=subprocess.PIPE)
bundle=docs/'host/bmi30_split_bundles'/version;archive=work/('BMI30-RPi-'+version+'.tar.gz')
subprocess.run(['tar','-czf',str(archive),'-C',str(bundle.parent),version],check=True)
print(json.dumps({'version':version,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size,
 'portal_sha256':hashlib.sha256(portal).hexdigest(),'release':(bundle/'project/host/bmi30_firmware_release.env').read_text()}))
'''


if __name__ == '__main__':
    source = patch((WORK/'portal.py').read_text(encoding='utf-8')).encode()
    result=json.loads(remote(HOSTS[-1],SCRIPT,{'version':VERSION,'portal':base64.b64encode(source).decode()},timeout=120))
    (WORK/'release.json').write_text(json.dumps(result,indent=2))
    dest=Path('FirmwareReleases/RaspberryPi')/VERSION
    dest.mkdir(exist_ok=False)
    scp=SSH.with_name('scp.exe');name='BMI30-RPi-'+VERSION+'.tar.gz'
    subprocess.run([str(scp),'-F',str(CONFIG),'-o','BatchMode=yes',HOSTS[-1]+':/home/techaid/Documents/.tmp/network-20260930/'+name,str(dest/name)],check=True)
    print(json.dumps(result))
