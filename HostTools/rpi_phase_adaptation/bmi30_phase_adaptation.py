"""Manual RX alignment policy, independent of transmitter phase commands."""
import json
import os
from pathlib import Path
import tempfile
import threading

try:
    from usb_vendor.tx_phase import _CONFIG_LOCK
except ImportError:
    _CONFIG_LOCK = threading.RLock()


def config_path():
    return Path(os.environ.get('BMI30_CONFIG_JSON') or '/home/techaid/Documents/host/bmi30_config.json')


def initialize(scope):
    with _CONFIG_LOCK:
        path = config_path()
        data = json.loads(path.read_text()) if path.exists() else {}
        policy = data.get('rx_phase_adaptation', {})
        enabled = policy.get('enabled', True)
        shifts = policy.get('fixed_shifts', [0, 0])
        if type(enabled) is not bool or not isinstance(shifts, list) or len(shifts) != 2:
            raise ValueError('Invalid RX phase adaptation settings')
        if any(type(x) is not int or abs(x) > 599 for x in shifts):
            raise ValueError('Invalid fixed RX phase shifts')
        scope._rx_phase_auto_adapt = enabled
        scope._rx_phase_fixed_shifts = tuple(shifts)


def fixed_shift(source, channel):
    shifts = (source or {}).get('rx_phase_fixed_shifts') if isinstance(source, dict) or source is None else (
        None if getattr(source, '_rx_phase_auto_adapt', True) else source._rx_phase_fixed_shifts)
    return None if shifts is None else int(shifts[int(channel)])


def state(scope):
    enabled = bool(getattr(scope, '_rx_phase_auto_adapt', True))
    current = [int(getattr(scope, '_det_last_shift' + str(ch), 0)) for ch in (0, 1)]
    fixed = list(getattr(scope, '_rx_phase_fixed_shifts', (0, 0)))
    return {'enabled': enabled, 'locked': not enabled, 'current_shifts': current,
            'fixed_shifts': None if enabled else fixed}


def set_enabled(scope, enabled, persist=True):
    if type(enabled) is not bool:
        raise ValueError('Auto phase adaptation must be true or false')
    with _CONFIG_LOCK:
        previous = state(scope)
        if previous['enabled'] == enabled:
            return previous
        shifts = previous['current_shifts'] if not enabled else list(scope._rx_phase_fixed_shifts)
        if persist:
            path = config_path()
            data = json.loads(path.read_text()) if path.exists() else {}
            data['rx_phase_adaptation'] = {'enabled': enabled, 'fixed_shifts': shifts}
            fd, name = tempfile.mkstemp(prefix='.rx-phase-', dir=path.parent)
            try:
                if path.exists():
                    os.chmod(name, path.stat().st_mode & 0o777)
                with os.fdopen(fd, 'w', encoding='utf8') as stream:
                    json.dump(data, stream, ensure_ascii=False, indent=2)
                    stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
                os.replace(name, path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        # The core command queue owns this transition. Retire old worker results
        # before returning success, including results computed during the change.
        scope._rx_phase_fixed_shifts = tuple(shifts)
        scope._rx_phase_auto_adapt = enabled
        scope._rt_detector_worker_reset_for_stream(reason='rx_phase_adaptation')
        return state(scope)
