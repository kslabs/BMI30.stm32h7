"""Deploy only to identified M02's current active root, with guarded rollback."""
import base64
import hashlib
import json
from pathlib import Path
import sys

import deploy_noise_area as transaction
import patch_confirmation_growth as feature

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
WORK = ROOT/'.tmp/confirmation-growth-20261021/M02-deploy'
WORK.mkdir(parents=True, exist_ok=True)
LABEL = 'Optional first-to-last confirmation peak growth'
ORIGIN = 'Independent per-antenna last/first peak amplitude gate; default off and ratio 2.0; preserve existing settings'


def script(name):
    if name == 'layout_install':
        return (HERE/'confirmation_growth_layout_install_remote.py').read_text(encoding='utf8')
    if name.startswith('layout_'):
        base_name = name.removeprefix('layout_')
        source = script(base_name)
        if base_name == 'build':
            source = feature.once(source, "paths = dict(i['paths'], core='host/BMI30.001.py.' + v, engine='host/BMI30.200.py.' + v)",
                                  "paths = dict(i['paths'])")
        return source
    if name in ('verify', 'registry'):
        return (HERE/f'confirmation_growth_{name}_remote.py').read_text(encoding='utf8')
    source = (HERE/f'noise_area_{name}_remote.py').read_text(encoding='utf8')
    source = (source.replace('.tmp/noise-area-20261014', '.tmp/confirmation-growth-20261021')
              .replace('Adjustable noise measurement area', LABEL)
              .replace('Peak-relative noise boundaries, 5-95 percent; preserve signal/noise coefficients', ORIGIN)
              .replace('Peak-relative noise boundaries; preserve signal/noise coefficients', ORIGIN)
              .replace('validated-M02-noise-area-update', 'validated-M02-confirmation-growth-update'))
    if name == 'inspect':
        source = feature.once(source, "helper='host/bmi30_averaging.py')", "helper='host/bmi30_averaging.py', dual='host/bmi30_dual_detector.py')")
        source = feature.once(source, "    'config': json.loads((D / 'host/bmi30_config.json').read_text()),\n", '')
    elif name == 'install':
        source = feature.once(source, " == i['root'] == '/dev/sda2'", " == i['root']")
        source = feature.once(source, "    assert after['detector']['settings']['noise_area_percent'] == 50",
            "    assert after['detector']['settings'] == dict(before['detector']['settings'], confirm_growth_enabled=False, confirm_growth_ratio=2.0)")
        source = feature.once(source, "'noise_area_percent': 50,", "'confirm_growth_enabled': False, 'confirm_growth_ratio': 2.0,")
        source = source.replace('M02 active USB only', 'M02 current active root only')
    return source


def call(name, payload=None, timeout=90):
    result = transaction.remote(script(name), payload=payload, timeout=timeout)
    if name in ('inspect', 'layout_inspect'):
        folder = WORK/('layout-source' if name.startswith('layout_') else 'source')
        folder.mkdir(exist_ok=True)
        for key, data in result.pop('sources').items():
            (folder/f'{key}.py').write_bytes(base64.b64decode(data))
    if name in ('build', 'layout_build'):
        archive = base64.b64decode(result.pop('archive'))
        assert hashlib.sha256(archive).hexdigest() == result['archive_sha256']
        folder = ROOT/'FirmwareReleases/RaspberryPi'/result['version']
        folder.mkdir(exist_ok=False)
        file = folder/f'BMI30-RPi-{result["version"]}.tar.gz'
        file.write_bytes(archive)
        file.with_suffix(file.suffix+'.sha256').write_text(result['archive_sha256']+'  '+file.name+'\n')
        result['archive_path'] = str(file)
    if name == 'verify':
        for key in ('portal_html', 'inline_scripts'):
            if key in result:
                (WORK/(key+'.json')).write_text(json.dumps(result.pop(key), ensure_ascii=False), encoding='utf8')
    (WORK/f'{name}.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf8')
    return result


if __name__ == '__main__':
    name = sys.argv[1]
    payload = json.loads(Path(sys.argv[2]).read_text(encoding='utf8')) if len(sys.argv)>2 else None
    result = call(name, payload, timeout=210 if name=='build' else 100)
    if name in ('inspect', 'layout_inspect'):
        result = {key: result[key] for key in ('hostname','serial','root','paths','hashes','agents')}
    print(json.dumps(result, ensure_ascii=False, indent=2))
