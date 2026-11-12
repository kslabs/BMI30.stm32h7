"""M02-only tooling; source/settings guards are rechecked on the target."""
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
WORK = ROOT / '.tmp/noise-area-20261014/M02-deploy'
WORK.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / '.tmp/new-device-20261009'))
from device_remote import remote


def call(name, payload=None, timeout=90):
    script = (HERE / ('noise_area_' + name + '_remote.py')).read_text(encoding='utf8')
    value = remote(script, payload=payload, timeout=timeout)
    if name == 'build':
        archive = base64.b64decode(value.pop('archive'))
        assert hashlib.sha256(archive).hexdigest() == value['archive_sha256']
        folder = ROOT / 'FirmwareReleases/RaspberryPi' / value['version']
        folder.mkdir(exist_ok=False)
        file = folder / ('BMI30-RPi-' + value['version'] + '.tar.gz')
        file.write_bytes(archive)
        file.with_name(file.name + '.sha256').write_text(value['archive_sha256'] + '  ' + file.name + '\n')
        value['archive_path'] = str(file)
    if name == 'inspect':
        source = WORK / 'source'
        source.mkdir(exist_ok=True)
        for key, raw in value.pop('sources').items():
            (source / (key + '.py')).write_bytes(base64.b64decode(raw))
    (WORK / (name + '.json')).write_text(json.dumps(value, indent=2), encoding='utf8')
    return value


if __name__ == '__main__':
    action = sys.argv[1]
    payload = json.loads(Path(sys.argv[2]).read_text(encoding='utf8')) if len(sys.argv) > 2 else None
    result = call(action, payload, timeout=210 if action == 'build' else 90)
    if action == 'inspect':
        d = result['status']['detector']
        result = {key: result[key] for key in ['hostname', 'serial', 'root', 'hashes', 'installed_portal_sha256']}
        result['detector'] = {'settings': d['settings'], 'upper': d['channels']['upper'], 'lower': d['channels']['lower']}
    if action == 'verify':
        result = {key:value for key,value in result.items() if key != 'inline_scripts'}
    print(json.dumps(result, indent=2))
