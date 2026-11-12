"""Build a source-reviewed, portal-only identity update; no device mutations."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

from patch_rpi_identity import group_javascript, patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_SOURCE = ROOT / 'FirmwareReleases/RaspberryPi/2026-10-21-1819/review/portal.py'
DEFAULT_OUTPUT = ROOT / 'FirmwareReleases/RaspberryPi/2026-10-23-rpi-identity'


def build(source: Path, output: Path) -> dict:
    original = source.read_text(encoding='utf-8')
    candidate = patch(original)
    review = output / 'review'
    payload = output / 'bmi30-rpi-identity'
    review.mkdir(parents=True, exist_ok=True)
    payload.mkdir(parents=True, exist_ok=True)
    (review / 'portal.py').write_text(candidate, encoding='utf-8', newline='\n')
    (review / 'group-rendered.js').write_text(group_javascript(candidate), encoding='utf-8', newline='\n')
    diff = ''.join(difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                                      fromfile='portal-before.py', tofile='portal-after.py'))
    (review / 'portal.diff').write_text(diff, encoding='utf-8', newline='\n')
    for name in ('patch_rpi_identity.py', 'install_rpi_identity.py', 'group_ui.js'):
        shutil.copy2(HERE / name, payload / name)
    shutil.copy2(HERE / 'RPI_IDENTITY_FIX_RU.md', payload / 'README_RU.md')
    manifest = {'version': '2026-10-23', 'scope': 'Raspberry Pi portal hardware identity',
                'deployed': False, 'base_source': str(source.resolve()),
                'base_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
                'files': {f.relative_to(output).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                          for f in sorted([*payload.iterdir(), *review.iterdir()]) if f.is_file()}}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8', newline='\n')
    archive = output / 'BMI30-RPi-identity-2026-10-23.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        for item in sorted(payload.iterdir()):
            if item.is_file():
                bundle.add(item, arcname='bmi30-rpi-identity/' + item.name)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + '.sha256').write_text(checksum + '  ' + archive.name + '\n', encoding='ascii')
    return {'archive': str(archive), 'sha256': checksum, 'review': str(review), 'deployed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), indent=2))
