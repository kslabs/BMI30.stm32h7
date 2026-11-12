import base64
import hashlib
import json
from pathlib import Path
import sys
from read_runtime import ROOT, WORK, s07, m02

alignment = '--alignment' in sys.argv
VERSION = '2026-10-13-1534' if alignment else '2026-10-13-1519'
board = sys.argv[1]
assert board in ('S07','M02')
source = WORK/board/'source'
inspection=json.loads((WORK/board/'inspection.json').read_text(encoding='utf8'))
expected={name:inspection['source_sha256'][name] for name in ('core','engine','portal')}
if alignment:
    expected={name:hashlib.sha256((WORK/'candidate'/(name+'.py')).read_bytes()).hexdigest() for name in ('core','engine','portal')}
    expected['core']=hashlib.sha256((WORK/'before-alignment-core.py').read_bytes()).hexdigest()
files={name:base64.b64encode((WORK/'candidate'/(name+'.py')).read_bytes()).decode() for name in ('core','engine','portal')}
files['helper']=base64.b64encode((Path(__file__).parent/'bmi30_averaging.py').read_bytes()).decode()
payload={'board':board,'hostname':inspection['hostname'],'root':inspection['root'],'paths':inspection['paths'],
         'version':VERSION,'expected':expected,'files':files}
if alignment:payload['base_version']='2026-10-13-1519'
result=(s07 if board=='S07' else m02)((Path(__file__).parent/'deploy_remote.py').read_text(),payload=payload,timeout=270)
archive=base64.b64decode(result.pop('archive'))
assert hashlib.sha256(archive).hexdigest()==result['archive_sha256']
release=ROOT/'FirmwareReleases/RaspberryPi'/VERSION
release.mkdir(parents=True,exist_ok=True)
name='BMI30-RPi-'+VERSION+('.tar.gz' if board=='S07' else '-M02.tar.gz')
(release/name).write_bytes(archive)
(release/(name+'.sha256')).write_text(result['archive_sha256']+'  '+name+'\n')
(WORK/board/'deployment.json').write_text(json.dumps(result,indent=2),encoding='utf8')
print(json.dumps(result))
