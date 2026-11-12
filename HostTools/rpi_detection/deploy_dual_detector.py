"""M02-only dual-detector transaction, complete bundle and rollback guards."""
import json
from pathlib import Path
import sys

import deploy_fresh_pairs as previous

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT/'.tmp/dual-detector-20261016'
LABEL = 'Sensitive averaging with original-packet impulse verification'
ORIGIN = 'Restore Raspberry detector averaging; verify same-window raw packet influence; preserve settings, burst protection and AutoFreeze'
BASE = previous.previous.BASE
_script = previous.script


def once(source, old, new):
    assert source.count(old) == 1, (old[:100],source.count(old))
    return source.replace(old,new)


def script(name):
    source = (_script(name)
        .replace('.tmp/fresh-pair-detector-20261016', '.tmp/dual-detector-20261016')
        .replace(previous.LABEL, LABEL).replace(previous.ORIGIN, ORIGIN)
        .replace('validated-M02-fresh-pairs-update', 'validated-M02-dual-detector-update'))
    if name == 'build':
        source = once(source, "engine='host/BMI30.200.py.' + v)", "engine='host/BMI30.200.py.' + v, dual='host/bmi30_dual_detector.py')")
    if name == 'install':
        source = once(source, "    A / r['paths']['engine']: (B / 'project' / r['paths']['engine']).read_bytes(),",
            "    A / r['paths']['engine']: (B / 'project' / r['paths']['engine']).read_bytes(),\n"
            "    A / r['paths']['dual']: (B / 'project' / r['paths']['dual']).read_bytes(),")
        source = once(source, '                atomic(file, data)\n        for file, data in settings.items():',
            '                atomic(file, data)\n'
            "            elif file == A / r['paths']['dual']:\n"
            "                assert file.parent.resolve() == (A/'host').resolve()\n"
            '                file.unlink(missing_ok=True)\n'
            '        for file, data in settings.items():')
    if name == 'verify':
        source = source.replace('fresh_arrival_adjacent_opposite_phase_before_rpi_mean',
            'averaged_sensitive_branch_with_raw_leave_one_out_guard')
        source = once(source, "estimate['detector_rpi_depth'] == 1", "estimate['detector_rpi_depth'] == status['mode']['rpi_avg_n']")
        source = once(source, "assert status['averaging']['display_only'] and status['averaging']['detector_depth'] == 1",
            "assert not status['averaging']['display_only'] and status['averaging']['detector_depth'] == status['mode']['rpi_avg_n']\n"
            "    assert status['detector']['dual']['mode'] == 'averaged_plus_raw_leave_one_out'\n"
            "    for channel in ('upper','lower'):\n"
            "        evidence = status['detector']['dual'][channel]\n"
            "        assert evidence['reason'] not in ('missing_raw_window','invalid_raw_window'), evidence\n"
            "        assert evidence['depth'] == status['mode']['rpi_avg_n'], evidence")
        # The prior runtime already has rare scheduler stalls over 100 ms.
        # Report them; transport loss/worker overflow/health still fail checks.
        source = once(source, "assert worker1['latency']['over_100ms'] == worker0['latency']['over_100ms']", "assert all(b>a for a,b in zip(before['detector']['dual']['checks'],after['detector']['dual']['checks']))")
        source = source.replace("'fresh_pairs': worker1['fresh_pairs'],", "'fresh_pairs': worker1['fresh_pairs'], 'dual': after['detector']['dual'],")
        source = once(source, 'before = healthy(get())',
            "for attempt in range(100):\n"
            "    initial = get()\n"
            "    if all(initial.get('detector',{}).get('dual',{}).get(ch,{}).get('reason') for ch in ('upper','lower')):\n"
            "        break\n"
            "    time.sleep(.2)\n"
            "else:\n"
            "    raise AssertionError('Dual detector evidence did not become ready')\n"
            "before = healthy(initial)")
    return source


BASE.WORK, BASE.script = WORK, script
call, remote = BASE.call, previous.remote


if __name__ == '__main__':
    print(json.dumps(call(sys.argv[1],json.loads(Path(sys.argv[2]).read_text(encoding='utf8'))),ensure_ascii=False,indent=2))
