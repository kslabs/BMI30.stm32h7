"""Prepare the audited 1332 baseline for compatibility tests; no device access."""
import pathlib
import shutil
import tarfile

import patch_runtime

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORK = ROOT / '.tmp/stm32-1253-compat-20261023'
BASE = ROOT / 'FirmwareReleases/RaspberryPi/2026-10-23-1332/BMI30-RPi-2026-10-23-1332.tar.gz'


def prepare():
    destination = WORK / 'candidate'
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(BASE) as archive:
        for item in archive.getmembers():
            prefix = '2026-10-23-1332/project/'
            if not item.isfile() or not item.name.startswith(prefix):
                continue
            path = destination / item.name[len(prefix):]
            if not path.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Path outside candidate')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(item).read())
    changes = {'host/usb_vendor/usb_stream.py': patch_runtime.usb,
               'host/usb_vendor/tx_phase.py': patch_runtime.tx_phase,
               'host/usb_vendor/relay_control.py': patch_runtime.relay,
               'host/BMI30.001.py.2026-10-23-1317': patch_runtime.core,
               'hotspot_info_server.py': patch_runtime.portal}
    for name, transform in changes.items():
        path = destination / name
        path.write_text(patch_runtime.validate(transform(path.read_text(encoding='utf-8'))),
                        encoding='utf-8', newline='\n')
    shutil.copy2(pathlib.Path(__file__).with_name('capabilities.py'), destination / 'host/usb_vendor/capabilities.py')
    return destination


if __name__ == '__main__':
    print(prepare())
