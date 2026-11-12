"""Add noise/time annotations using the existing M02 deployment transaction."""
import json
from pathlib import Path
import sys

import deploy_autofreeze_pair as previous

WORK = previous.ROOT/'.tmp/scope-noise-line-20261016'
LABEL = 'Scope reference-noise line and frozen event time'
ORIGIN = 'Draw the saved threshold-reference noise on both antennas; show event time without redundant detection captions'
_previous_script = previous.script


def script(name):
    if name == 'verify':
        source = _previous_script(name)
        source = source.replace('assert "snap.synchronized?\'SYNC \':\'REF \'" in html',
            "assert 'function detectorPlotNoise(' in html and 'function detectorEventTimeParts(' in html\n"
            "assert 'content:\" AUTO FIRE\";' not in html")
        return source
    source = (_previous_script(name).replace('.tmp/autofreeze-20261016','.tmp/scope-noise-line-20261016')
            .replace(previous.LABEL,LABEL).replace(previous.ORIGIN,ORIGIN)
            .replace('validated-M02-autofreeze-pair-update','validated-M02-scope-noise-time-update'))
    if name == 'registry':
        old = "rows = [dict(zip(fields, values))]+[row for row in rows if row[fields[1]] != r['version']]"
        assert source.count(old) == 1
        source = source.replace(old,
            "all_values = [values] + p.get('earlier_journal_rows', [])\n"
            "assert all(len(value)==len(fields) for value in all_values)\n"
            "ids = {value[1] for value in all_values}\n"
            "rows = [dict(zip(fields,value)) for value in all_values]+[row for row in rows if row[fields[1]] not in ids]")
    return source


previous.WORK, previous.script = WORK, script
call = previous.call


if __name__=='__main__':
    name = sys.argv[1]
    print(json.dumps(call(name,json.loads(Path(sys.argv[2]).read_text(encoding='utf8'))),indent=2))
