"""Build a two-file donor hotfix from the authentic shipped network archive."""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

import patch_recovery
from install_recovery import DONOR_IDS, FILES, TARGET_ID

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / 'FirmwareReleases/RaspberryPi/2026-10-05-network-13'
OUTPUT = ROOT / 'FirmwareReleases/RaspberryPi' / patch_recovery.RELEASE


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(output=OUTPUT):
    archive = BASE / 'BMI30-Network-2026-10-05-network-13.tar.gz'
    checksum = (BASE / 'SHA256SUMS').read_text().split()[0]
    if digest(archive.read_bytes()) != checksum:
        raise RuntimeError('Shipped archive checksum mismatch')
    with tarfile.open(archive) as source:
        original = source.extractfile('2026-10-05-network-13/service.py').read()
    candidate = patch_recovery.patch(original.decode()).encode()
    review, payload = output / 'review', output / 'bmi30-legacy-recovery'
    review.mkdir(parents=True, exist_ok=True)
    payload.mkdir(parents=True, exist_ok=True)
    (payload / 'service.py').write_bytes(candidate)
    for name in ('legacy_recovery.py', 'install_recovery.py', 'README_RU.md'):
        shutil.copy2(HERE / name, payload / name)
    (review / 'service.diff').write_text(''.join(difflib.unified_diff(
        original.decode().splitlines(keepends=True), candidate.decode().splitlines(keepends=True),
        fromfile='network-0.1.13/service.py', tofile='network-' + patch_recovery.VERSION + '/service.py')),
        encoding='utf-8', newline='\n')
    manifest = {'format': 1, 'release': patch_recovery.RELEASE, 'network_version': patch_recovery.VERSION,
                'target_id': TARGET_ID, 'donor_ids': list(DONOR_IDS), 'changed_runtime_files': list(FILES),
                'wire_protocol': 1, 'requires_target_update': False, 'deployed': False,
                'changes_system_time': False, 'changes_wifi_profiles': False, 'changes_stm32': False,
                'base_archive_sha256': checksum, 'base_service_sha256': digest(original),
                'unchanged_agent_methods': patch_recovery.unchanged_methods(original.decode(), candidate.decode()),
                'payload_sha256': {p.name: digest(p.read_bytes()) for p in payload.iterdir()
                                   if p.is_file() and p.name != 'manifest.json'}}
    for path in payload.glob('*.py'):
        ast.parse(path.read_bytes(), filename=str(path))
    (payload / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    result = output / ('BMI30-Network-' + patch_recovery.RELEASE + '.tar.gz')
    with tarfile.open(result, 'w:gz') as bundle:
        for path in sorted(payload.iterdir()):
            if path.is_file():
                bundle.add(path, arcname='bmi30-legacy-recovery/' + path.name)
    result.with_suffix('.gz.sha256').write_text(digest(result.read_bytes()) + '  ' + result.name + '\n', encoding='ascii')
    return {'archive': str(result), 'sha256': digest(result.read_bytes()), 'deployed': False,
            'network_version': patch_recovery.VERSION, 'changed_runtime_files': list(FILES)}


if __name__ == '__main__':
    print(json.dumps(build(), indent=2))
