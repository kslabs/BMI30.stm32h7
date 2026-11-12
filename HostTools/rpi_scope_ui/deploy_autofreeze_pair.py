"""Use the existing M02 identity, settings, startup and rollback guards."""
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'HostTools/rpi_detection'))
import deploy_noise_readout_help as previous
from device_remote import remote

WORK = ROOT/'.tmp/autofreeze-20261016'
LABEL = 'AutoFreeze comparison of both antennas'
ORIGIN = 'Preserve both captured antenna waveforms; label independent reference without changing detector gates'


def script(name):
    if name == 'verify':
        return Path(__file__).with_name('verify_autofreeze_pair_remote.py').read_text(encoding='utf8')
    source = previous.script(name)
    source = source.replace('.tmp/noise-readout-help-20261016', '.tmp/autofreeze-20261016')
    source = source.replace('Integer readouts and two-second p/n/t/SN help', LABEL)
    source = source.replace('Integer scope readouts; explain p/n/t/SN after two-second hover while live values refresh', ORIGIN)
    source = source.replace('Integer p/n/t/SN readouts and two-second help; retain precise native arithmetic', ORIGIN)
    source = source.replace('validated-M02-noise-readout-help-update', 'validated-M02-autofreeze-pair-update')
    return source


def call(name, payload=None):
    value = remote(script(name), payload=payload, timeout=210 if name == 'build' else 150)
    if name == 'build':
        archive = base64.b64decode(value.pop('archive'))
        assert hashlib.sha256(archive).hexdigest() == value['archive_sha256']
        folder = ROOT/'FirmwareReleases/RaspberryPi'/value['version']
        folder.mkdir(exist_ok=False)
        file = folder/('BMI30-RPi-'+value['version']+'.tar.gz')
        file.write_bytes(archive)
        file.with_name(file.name+'.sha256').write_text(value['archive_sha256']+'  '+file.name+'\n')
        value['archive_path'] = str(file)
    (WORK/(name+'.json')).write_text(json.dumps(value, indent=2), encoding='utf8')
    return value


if __name__ == '__main__':
    name = sys.argv[1]
    payload = json.loads(Path(sys.argv[2]).read_text(encoding='utf8'))
    print(json.dumps(call(name, payload), ensure_ascii=False, indent=2))
