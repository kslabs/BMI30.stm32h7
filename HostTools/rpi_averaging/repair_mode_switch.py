"""Guarded repair of averaging after raw/latest mode on approved S07/M02 media."""
import base64
import concurrent.futures
import datetime
import hashlib
import json
from pathlib import Path
import sys

import expand12 as base
from measure_boards import REMOTE as TEST_REMOTE

W = base.ROOT / '.tmp/m02-stop-20261013'
HERE = Path(__file__).parent
base.W = W


def remote_source(name):
    source = (HERE / name).read_text(encoding='utf8')
    for old, new in [
        ('rpi-averaging12-20261013', 'averaging-mode-fix-20261013'),
        ('bmi30-average12-emmc', 'bmi30-average-mode-fix-emmc'),
        ('bmi30-averaging12', 'bmi30-averaging-mode-fix'),
        ('BMI30_SPLIT_VERSION=2026-10-13-1534', 'BMI30_SPLIT_VERSION=2026-10-13-1638'),
        ('Raspberry averaging 1-12', 'Raspberry averaging mode-switch fix'),
        ('Extend Raspberry rolling averaging to 12 packets; preserve centered readout',
         'Fix false averaging sequence gaps after raw mode; preserve settings and 1-12 range'),
        ('Raspberry rolling averaging 1-12; centered readout',
         'AVG_ROI consecutive stream sequence; ignore legacy raw sequence stride'),
        ('validated-averaging12-update', 'validated-averaging-mode-fix'),
    ]:
        source = source.replace(old, new)
    return source


def inspect(board):
    script = base.INSPECT.replace("=='2026-10-13-1534'", "=='2026-10-13-1638'")
    result = base.call(board, script, 'before', timeout=35)
    folder = W / board / 'source'
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in result['sources'].items():
        (folder / (name+'.py')).write_bytes(base64.b64decode(data))
    print(json.dumps({'board': board, 'mode': result['mode'], 'hashes': result['hashes']}), flush=True)


def test(board):
    source = W / board / 'source'
    files = {name: base64.b64encode((HERE/name).read_bytes()).decode()
             for name in ['bmi30_averaging.py', 'test_averaging.py', 'benchmark.py']}
    files.update({name+'.py': base64.b64encode((source/(name+'.py')).read_bytes()).decode()
                  for name in ['engine', 'portal']})
    result = base.call(board, TEST_REMOTE.replace('rpi-averaging-20261013/check',
                       'averaging-mode-fix-20261013/check'), 'tests', {'files': files}, timeout=65)
    assert 'OK' in result['tests'], result['tests']
    print(json.dumps({'board': board, 'tests': result['tests']}), flush=True)


def build():
    before = json.loads((W/'M02-before.json').read_text())
    version = datetime.datetime.now().strftime('%Y-%m-%d-%H%M')
    files = {name: base64.b64encode((W/'M02/source'/(name+'.py')).read_bytes()).decode()
             for name in ['core', 'engine', 'portal']}
    files['helper'] = base64.b64encode((HERE/'bmi30_averaging.py').read_bytes()).decode()
    result = base.call('M02', remote_source('expand12_build_remote.py'), 'build',
                       {'before': before, 'version': version, 'files': files}, timeout=210)
    archive = base64.b64decode(result.pop('archive'))
    assert hashlib.sha256(archive).hexdigest() == result['archive_sha256']
    folder = base.ROOT/'FirmwareReleases/RaspberryPi'/version
    folder.mkdir(exist_ok=False)
    filename = 'BMI30-RPi-'+version+'.tar.gz'
    (folder/filename).write_bytes(archive)
    (folder/(filename+'.sha256')).write_text(result['archive_sha256']+'  '+filename+'\n')
    result['archive_path'] = str(folder/filename)
    (W/'release.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


def install(board):
    release = json.loads((W/'release.json').read_text())
    payload = {'before': json.loads((W/(board+'-before.json')).read_text()), 'release': release,
               'archive': base64.b64encode(Path(release['archive_path']).read_bytes()).decode()}
    result = base.call(board, remote_source('expand12_install_remote.py'), 'installed', payload, timeout=130)
    print(json.dumps({'board': board, **result}), flush=True)


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'build':
        build()
    elif action in ('inspect', 'test'):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(globals()[action], sys.argv[2:] or ['M02', 'S07']))
    elif action == 'install':
        install(sys.argv[2])
    elif action == 'audit':
        base.audit(sys.argv[2])
