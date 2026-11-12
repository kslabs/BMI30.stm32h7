"""Use the existing guarded M02 transaction for the annotation-only change."""
import json
from pathlib import Path
import sys

import deploy_autofreeze_annotations as previous

BASE = previous.previous.previous
WORK = BASE.ROOT/'.tmp/scope-autofreeze-status-20261016'
LABEL = 'AutoFreeze readout without redundant firing label and with full header width'
ORIGIN = 'Remove only FIRE from accepted-event parameter readouts; retain event time in triggered heading; use free header width'
_script = previous.script


def script(name):
    source = (_script(name)
        .replace('.tmp/scope-autofreeze-annotations-20261016', '.tmp/scope-autofreeze-status-20261016')
        .replace(previous.LABEL, LABEL).replace(previous.ORIGIN, ORIGIN)
        .replace('validated-M02-autofreeze-annotations-update', 'validated-M02-autofreeze-status-update'))
    if name == 'verify':
        source = source.replace("assert 'reviewReason(cm)' not in html",
            "assert 'reviewReason(cm)' not in html\n"
            "assert 'if(!snap.fired)parts.push({text:kind,' in html\n"
            "assert '.scope-norm.decision{position:static;transform:none;flex:1 1 0;' in html")
    return source


BASE.WORK, BASE.script = WORK, script
call, remote = BASE.call, BASE.remote


if __name__ == '__main__':
    print(json.dumps(call(sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding='utf8'))), indent=2))
