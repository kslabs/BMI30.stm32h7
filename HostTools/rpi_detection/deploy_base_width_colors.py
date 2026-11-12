"""Activate the reviewed palette-only release, preserving current device settings."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

from deploy_base_width import REMOTE as BASE_REMOTE, remote, HOSTS
from patch_base_width import replace

WORK = Path(__file__).resolve().parents[2] / '.tmp/base-width-colors-20260930'


def deployment_script():
    script = BASE_REMOTE.replace('.tmp/base-width-20260930', '.tmp/base-width-colors-20260930')
    script = replace(script, '2026-09-30-1449', '2026-09-30-1627', count=2)
    script = replace(script, " new_config.update(base_width_enabled=True,base_width_max=24)\n"
                     " new_config['tag_detection']={**old_config.get('tag_detection',{}),'base_width_enabled':True,'base_width_max':24}\n"
                     " atomic(config,(json.dumps(new_config,ensure_ascii=False,indent=2)+'\\n').encode())\n", '')
    script = replace(script,
        "and after['detector']['settings'].get('base_width_enabled') and after['detector']['settings'].get('base_width_max')==24",
        "and all(after['detector']['settings'].get(k)==new_config.get('tag_detection',{}).get(k,new_config.get(k)) for k in ('base_width_enabled','base_width_max'))")
    script = replace(script,
        "'settings':{'base_width_enabled':True,'base_width_max':24}",
        "'settings':{k:after['detector']['settings'][k] for k in ('base_width_enabled','base_width_max')}")
    script = replace(script,
        " assert 'function drawBaseWidthGate(' in html",
        " assert 'function drawBaseWidthGate(' in html\n assert '#b697ff' in html and '#ffa657' in html and 'base-width-pass' in html")
    return script


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', choices=HOSTS)
    args=parser.parse_args()
    info=json.loads((WORK/'bundle.json').read_text())
    audit=json.loads((WORK/(HOSTS[args.board]+'-before.json')).read_text())
    archive=WORK.parents[1]/'FirmwareReleases/RaspberryPi'/info['version']/('BMI30-RPi-'+info['version']+'.tar.gz')
    switcher=WORK.parent/'base-width-20260930/tools/switch_bmi30_split_versions.sh'
    payload=dict(version=info['version'],host=HOSTS[args.board],hashes=info['hashes'],expected=audit['files'],
                 switcher_sha256=hashlib.sha256(switcher.read_bytes()).hexdigest(),
                 archive=base64.b64encode(archive.read_bytes()).decode(),archive_sha256=info['sha256'])
    result=json.loads(remote(HOSTS[args.board],deployment_script(),payload,timeout=110))
    (WORK/(args.board+'-deployment.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':main()
