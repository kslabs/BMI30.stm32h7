"""M02-only sample-diversity setting; reuse the guarded complete-bundle transaction."""
import json
from pathlib import Path
import sys

import deploy_dual_detector as previous

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT/'.tmp/sample-diversity-20261016'
LABEL = 'Configurable diversity of peak sample numbers in confirmations'
ORIGIN = 'Add minimum distinct peak positions in the last confirmation window; preserve sensitive averaging, impulse guard and settings; default disabled'
BASE = previous.BASE
_script = previous.script


def script(name):
    source = (_script(name)
        .replace('.tmp/dual-detector-20261016', '.tmp/sample-diversity-20261016')
        .replace(previous.LABEL, LABEL).replace(previous.ORIGIN, ORIGIN)
        .replace('validated-M02-dual-detector-update', 'validated-M02-sample-diversity-update'))
    if name == 'inspect':
        source = previous.once(source, "helper='host/bmi30_averaging.py')",
            "helper='host/bmi30_averaging.py', dual='host/bmi30_dual_detector.py')")
    if name == 'verify':
        source = previous.once(source,
            "assert status['detector']['settings'] == i['status']['detector']['settings']",
            "assert status['detector']['settings'] == dict(i['status']['detector']['settings'], confirm_sample_diversity=0)")
        source = previous.once(source, "html = urllib.request.urlopen(",
            "assert after['detector']['settings']['confirm_sample_diversity'] == 0\n"
            "assert all(after['detector']['diversity_rejected'+str(ch)] == 0 for ch in (0,1))\n"
            "html = urllib.request.urlopen(")
    return source


BASE.WORK, BASE.script = WORK, script
call, remote = BASE.call, previous.remote


if __name__ == '__main__':
    print(json.dumps(call(sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding='utf8'))), ensure_ascii=False, indent=2))
