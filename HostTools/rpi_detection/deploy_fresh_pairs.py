"""Guarded M02-only fresh-evidence and oscilloscope latency release."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'HostTools/rpi_scope_ui'), str(ROOT/'.tmp/new-device-20261009')]
import deploy_autofreeze_status as previous

WORK = ROOT/'.tmp/fresh-pair-detector-20261016'
LABEL = 'Fresh detector evidence, working burst gate and calculated latency'
ORIGIN = 'Check every fresh opposite-phase arrival before Raspberry display averaging; preserve confirmation settings; show calculated latency and compact FPS'
_script = previous.script


def script(name):
    source = (_script(name)
        .replace('.tmp/scope-autofreeze-status-20261016', '.tmp/fresh-pair-detector-20261016')
        .replace(previous.LABEL, LABEL).replace(previous.ORIGIN, ORIGIN)
        .replace('validated-M02-autofreeze-status-update', 'validated-M02-fresh-pairs-update'))
    if name == 'verify':
        source = source.replace("    return status\n", """    assert status['detector']['worker']['input_policy'] == 'fresh_arrival_adjacent_opposite_phase_before_rpi_mean'
    estimate = status['detector']['latency_estimate']
    counts = [status['detector']['channels'][ch]['confirm_count'] for ch in ['upper','lower']]
    expected = [(count+1)*1000*status['mode']['avg_n']/(2*status['mode']['freq_hz']) for count in counts]
    assert estimate['full_input_ms'] == expected
    assert estimate['hard_limit_ms'] is None and estimate['detector_rpi_depth'] == 1
    assert status['averaging']['display_only'] and status['averaging']['detector_depth'] == 1
    return status
""")
        source = source.replace("'events':events,", "'latency_estimate': after['detector']['latency_estimate'], 'fresh_pairs': worker1['fresh_pairs'], 'events':events,")
    return source


previous.BASE.WORK, previous.BASE.script = WORK, script
call, remote = previous.BASE.call, previous.remote


if __name__ == '__main__':
    value = call(sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding='utf8')))
    print(json.dumps(value, ensure_ascii=False, indent=2))
