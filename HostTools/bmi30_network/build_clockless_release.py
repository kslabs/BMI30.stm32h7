"""Prepare a reviewable network/portal update without accessing any device."""
import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

from install_clockless import MODULES, portal_candidate
from patch_rpi_identity import group_javascript

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT/'FirmwareReleases/RaspberryPi/2026-10-21-1819/review/portal.py'
OUTPUT = ROOT/'FirmwareReleases/RaspberryPi/2026-10-23-service-transports-r2'


def live_javascript(source):
    # Inspect the compiled f-string constant, including Python escaping.
    marker='// Push every group change over one authenticated same-origin WebSocket.'
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,ast.Constant) and isinstance(node.value,str) and marker in node.value:
            return node.value[node.value.index(marker):].split('    function _pollSensors()',1)[0]
    raise ValueError('Live JavaScript was not found')


def build(source=SOURCE, output=OUTPUT):
    original=source.read_text(encoding='utf-8')
    candidate=portal_candidate(original);compile(candidate,'clockless-portal','exec')
    review=output/'review';review.mkdir(parents=True,exist_ok=True)
    payload=output/'bmi30-clockless';payload.mkdir(parents=True,exist_ok=True)
    (review/'portal.py').write_text(candidate,encoding='utf-8',newline='\n')
    (review/'group-rendered.js').write_text(group_javascript(candidate),encoding='utf-8',newline='\n')
    (review/'live-rendered.js').write_text(live_javascript(candidate),encoding='utf-8',newline='\n')
    (review/'portal.diff').write_text(''.join(difflib.unified_diff(original.splitlines(keepends=True),
        candidate.splitlines(keepends=True),fromfile='portal-before.py',tofile='portal-after.py')),encoding='utf-8')
    for name in (*MODULES,'patch_rpi_identity.py','patch_service_clock_portal.py','patch_service_transport_portal.py',
                 'install_rpi_identity.py','install_clockless.py','group_ui.js','live_ui.js'):
        shutil.copy2(HERE/name,payload/name)
    shutil.copy2(HERE/'CLOCKLESS_RU.md',payload/'README_RU.md')
    manifest={'version':'2026-10-23-service-transports-r2','network_version':'0.2.4','wire_protocol':2,
              'deployed':False,'requires_cohort_update':True,'bluetooth_transport_implemented':True,
              'regulatory_domain_changed':False,'base_source':str(source.resolve()),
              'base_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'files':{p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted([*review.iterdir(),*payload.iterdir()]) if p.is_file()}}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    archive=output/'BMI30-Network-service-transports-2026-10-23-r2.tar.gz'
    with tarfile.open(archive,'w:gz') as bundle:
        for item in sorted(payload.iterdir()):
            if item.is_file():bundle.add(item,arcname='bmi30-clockless/'+item.name)
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.gz.sha256').write_text(checksum+'  '+archive.name+'\n',encoding='ascii')
    return {'archive':str(archive),'sha256':checksum,'deployed':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args();print(json.dumps(build(args.source,args.output),indent=2))
