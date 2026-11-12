"""Install pinned display-only dependencies from the bundle, without network."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys


def ensure_environment(helpers):
    helpers = Path(helpers).resolve()
    target = Path('/opt/bmi30-scope-rtc')
    assert target.resolve() == target and platform.machine() == 'aarch64'
    assert sys.version_info[:2] == (3, 11), 'Scope RTC wheels require Python 3.11 ARM64'
    manifest = json.loads((helpers / 'scope_dependencies.json').read_text())
    for name, digest in manifest['wheels'].items():
        assert Path(name).name == name
        wheel = helpers / 'wheels' / name
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == digest, name
    if not target.exists():
        subprocess.run(['python3', '-m', 'venv', str(target)], check=True)
        subprocess.run([str(target / 'bin/pip'), 'install', '--no-index', '--require-hashes',
                        '--find-links', str(helpers / 'wheels'), '-r', str(helpers / 'requirements.lock')], check=True)
    code = "import importlib.metadata,json;print(json.dumps({d.metadata['Name'].lower().replace('_','-'):d.version for d in importlib.metadata.distributions()}))"
    installed = json.loads(subprocess.check_output([str(target / 'bin/python'), '-c', code], text=True))
    for name, version in manifest['packages'].items():
        assert installed.get(name.lower().replace('_', '-')) == version, 'Scope dependency differs: ' + name
    return target


if __name__ == '__main__':
    print(ensure_environment(sys.argv[1]))
