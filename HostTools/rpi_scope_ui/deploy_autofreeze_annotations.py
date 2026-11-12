"""Guarded M02 UI-only fix for the full AutoFreeze readout and heading time."""
import json
from pathlib import Path
import sys

import deploy_scope_noise_line as previous

WORK = previous.previous.ROOT/'.tmp/scope-autofreeze-annotations-20261016'
LABEL = 'Restore AutoFreeze annotations and triggered-antenna heading time'
ORIGIN = 'Fix missing common-mode tooltip helper; preserve all decision annotations; show event time only beside the triggered antenna heading'
_script = previous.script


def script(name):
    source = (_script(name).replace('.tmp/scope-noise-line-20261016','.tmp/scope-autofreeze-annotations-20261016')
        .replace(previous.LABEL,LABEL).replace(previous.ORIGIN,ORIGIN)
        .replace('validated-M02-scope-noise-time-update','validated-M02-autofreeze-annotations-update'))
    if name == 'verify':
        source = source.replace("assert 'content:\" AUTO FIRE\";' not in html",
            "assert 'content:attr(data-autofreeze-time)' in html\nassert 'reviewReason(cm)' not in html")
    return source


previous.previous.WORK, previous.previous.script = WORK, script
call = previous.previous.call
remote = previous.previous.remote


if __name__=='__main__':
    print(json.dumps(call(sys.argv[1],json.loads(Path(sys.argv[2]).read_text(encoding='utf8'))),indent=2))
