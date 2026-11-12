"""M02-only complete release adding a disabled-by-default impulse guard switch."""
import json
from pathlib import Path
import sys

import deploy_sample_diversity as previous

ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'.tmp/impulse-guard-toggle-20261016'
LABEL='Optional original-packet impulse protection with averaged detector bypass'
ORIGIN='Make original-packet protection selectable and bypass its calculations when off; default off; preserve averaging, confirmations and thresholds'
BASE=previous.BASE
_script=previous.script


def once(source,old,new):
    assert source.count(old)==1,(old[:100],source.count(old))
    return source.replace(old,new)


def script(name):
    source=(_script(name).replace('.tmp/sample-diversity-20261016','.tmp/impulse-guard-toggle-20261016')
        .replace(previous.LABEL,LABEL).replace(previous.ORIGIN,ORIGIN)
        .replace('validated-M02-sample-diversity-update','validated-M02-impulse-guard-toggle-update'))
    if name=='verify':
        source=once(source,"dict(i['status']['detector']['settings'], confirm_sample_diversity=0)",
            "dict(i['status']['detector']['settings'], impulse_guard_enabled=False)")
        source=source.replace('averaged_sensitive_branch_with_raw_leave_one_out_guard','averaged_sensitive_branch_only')
        source=once(source,"assert status['detector']['dual']['mode'] == 'averaged_plus_raw_leave_one_out'",
            "assert status['detector']['dual']['mode'] == 'averaged_only' and not status['detector']['dual']['enabled']")
        source=once(source,"assert evidence['reason'] not in ('missing_raw_window','invalid_raw_window'), evidence\n        assert evidence['depth'] == status['mode']['rpi_avg_n'], evidence",
            "assert evidence['reason'] == 'disabled' and evidence['enabled'] is False and evidence['allowed'], evidence")
        source=once(source,"assert all(b>a for a,b in zip(before['detector']['dual']['checks'],after['detector']['dual']['checks']))",
            "assert after['detector']['dual']['checks'] == before['detector']['dual']['checks'] == [0,0]")
        source=once(source,"assert after['detector']['settings']['confirm_sample_diversity'] == 0\nassert all(after['detector']['diversity_rejected'+str(ch)] == 0 for ch in (0,1))",
            "assert after['detector']['settings']['impulse_guard_enabled'] is False")
    return source


BASE.WORK,BASE.script=WORK,script
call,remote=BASE.call,previous.remote


if __name__=='__main__':
    print(json.dumps(call(sys.argv[1],json.loads(Path(sys.argv[2]).read_text(encoding='utf8'))),ensure_ascii=False,indent=2))
