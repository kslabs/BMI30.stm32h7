"""Apply the tested preset, calibrate each installation, and verify persistence.

This does not copy another module's background distance or change firmware.
"""
from pathlib import Path
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / 'HostTools/rpi_radar'))
from radar_gate import CONFIG_KEYS, RadarClient, RadarError
from flash import RELEASE, verified_image

PRESET = ROOT / 'presets/early_tag_0.1.2_180s.json'


def check_state(state, preset):
    for key in ('device', 'firmware', 'gpio0_mode'):
        if state.get(key) != preset[key]:
            raise RadarError('Unexpected ' + key + ': ' + str(state.get(key)))


def check_config(state, preset):
    check_state(state, preset)
    if any(state['config'].get(k) != v for k, v in preset['config'].items()):
        raise RadarError('Preset configuration readback mismatch')


def empty(state, preset):
    return (state.get('armed') == 1 and state.get('calibrating') == 0 and
            state.get('fault') == 0 and state.get('occupied') == 0 and state.get('gpio0') == 0 and
            preset['config']['gap_min_mm'] <= state.get('gap_mm', 0) <= preset['config']['gap_max_mm'])


def provision(client, preset, report, timeout=30, clock=time.monotonic, sleep=time.sleep):
    before = client.status()
    report['before'] = before
    check_state(before, preset)
    # CONFIG changes eleven atomic controls. The remaining scan/calibration
    # limits must already match the release's defaults; refuse surprises.
    for key, value in preset['config'].items():
        if key not in CONFIG_KEYS and before['config'].get(key) != value:
            raise RadarError('Restore release default before provisioning: ' + key)
    report['applied'] = client.apply({k: preset['config'][k] for k in CONFIG_KEYS})
    check_config(report['applied'], preset)
    client.request('CALIBRATE')
    deadline = clock() + timeout
    clear_since = None
    first_frame = None
    while clock() < deadline:
        state = client.status()
        report['last_calibration_state'] = state
        check_config(state, preset)
        if empty(state, preset):
            if clear_since is None:
                clear_since, first_frame = clock(), state.get('frame_count')
            if clock() - clear_since >= 0.5 and state.get('frame_count') != first_frame:
                break
        else:
            clear_since = None
        sleep(0.1)
    else:
        raise RadarError('No stable empty-passage calibration; settings were not saved')
    gap = state['gap_mm']
    client.request('SAVE')
    report['saved'] = True
    client.request('LOAD')
    loaded = client.status()
    report['after_load'] = loaded
    check_config(loaded, preset)
    if not empty(loaded, preset) or abs(loaded['gap_mm'] - gap) > 1:
        raise RadarError('Saved background/readback mismatch')
    report['verified'] = True
    return loaded


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', required=True, help='Explicit USB/UART port, 115200 8N1')
    p.add_argument('--label', required=True, help='Label of the module/installation in the report')
    p.add_argument('--empty-passage', action='store_true', help='Confirm final installation is empty')
    p.add_argument('--check', action='store_true', help='Read back preset without changing settings')
    p.add_argument('--report', type=Path, help='New JSON report file, required when provisioning')
    p.add_argument('--timeout', type=int, default=30)
    args = p.parse_args()
    if not 5 <= args.timeout <= 120:
        p.error('--timeout must be 5..120 seconds')
    if not args.check and (not args.empty_passage or not args.report):
        p.error('Provisioning requires --empty-passage and a new --report file')
    preset = json.loads(PRESET.read_text(encoding='utf-8'))
    _, manifest = verified_image(RELEASE)
    if manifest['bin_sha256'] != preset['bin_sha256']:
        raise RadarError('Preset/release hash mismatch')
    report = {'label': args.label, 'port': args.port, 'preset': preset,
              'saved': False, 'verified': False, 'cold_power_cycle_verified': False}
    output = None
    client = None
    try:
        if not args.check:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            output = args.report.open('x', encoding='utf-8')
        client = RadarClient(args.port)
        if args.check:
            state = client.status()
            check_config(state, preset)
            print(json.dumps(state, indent=2))
        else:
            state = provision(client, preset, report, args.timeout)
            print(json.dumps({'verified': True, 'gap_mm': state['gap_mm'], 'report': str(args.report)}))
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        if client:
            client.close()
        if output:
            json.dump(report, output, indent=2)
            output.write('\n')
            output.close()


if __name__ == '__main__':
    main()
