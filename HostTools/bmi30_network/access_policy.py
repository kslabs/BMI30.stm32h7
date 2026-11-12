"""Small, shared access policy. Never imports or controls acquisition."""
import json
from pathlib import Path

SETTINGS = Path('/var/lib/bmi30-access/settings.json')

def preferences(path=SETTINGS):
    try:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict):
            raise ValueError('Invalid access preferences')
        return data
    except FileNotFoundError:
        return {}

def offline_mode():
    try:
        return preferences().get('offline_mode', False) is True
    except (ValueError, OSError):
        return True

def forgotten(ssid):
    try:
        return ssid in preferences().get('forgotten_ssids', [])
    except (ValueError, OSError):
        return True

def maintenance():
    try:
        import time
        return time.time() - Path('/run/bmi30-access/busy').stat().st_mtime < 120
    except OSError:
        return False

def ap_clients():
    try:
        import subprocess
        r = subprocess.run(['iw', 'dev', 'wlan0ap', 'station', 'dump'],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.count('Station ') if r.returncode == 0 else 0
    except (OSError, subprocess.TimeoutExpired):
        return 0
