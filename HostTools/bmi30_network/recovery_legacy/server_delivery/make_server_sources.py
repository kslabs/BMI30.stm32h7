"""Adapt the two-file patch for old/new network runtimes and stock menu delivery."""
from pathlib import Path
import re
import sys

PARENT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PARENT))
import patch_recovery


def service(source, version):
    if 'VERSION = "0.1.13"' in source:
        source = patch_recovery.patch(source).replace('VERSION = "0.1.13-recovery1"', 'VERSION = "' + version + '"')
        return active_tick(source)
    if 'VERSION = "0.2.4"' not in source:
        raise ValueError('Unsupported network version')
    replace = patch_recovery.replace_once
    source = replace(source, 'VERSION = "0.2.4"', 'from legacy_recovery import LegacyRecovery\nVERSION = "' + version + '"')
    source = replace(source, '        self.bluetooth = BluetoothManager(self)\n',
                     '        self.bluetooth = BluetoothManager(self)\n        self.legacy_recovery = LegacyRecovery(self)\n')
    source = replace(source, "'service_transports':agent.group.transport_status()})",
                     "'service_transports':agent.group.transport_status(), 'legacy_recovery':agent.legacy_recovery.snapshot()})")
    source = replace(source, '''                with agent.lock:
                    data = agent.protocol.open(raw)
                transit=agent.accept_rpc(data,'exchange')
''', '''                try:
                    with agent.lock:
                        agent.legacy_recovery.check_replay(raw)
                        data = agent.protocol.open(raw)
                except Exception:
                    if not allows(agent, 'wifi'):
                        raise ValueError('Wi-Fi service channel disabled')
                    raw = agent.legacy_recovery.respond(raw)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                transit=agent.accept_rpc(data,'exchange')
''')
    compile(source, 'menu-network-service', 'exec')
    return active_tick(source)


def active_tick(source):
    return patch_recovery.replace_once(source, '        if choice and can_switch:\n',
        '        if self.legacy_recovery.active.maybe_visit(local):\n            return\n'
        '        if choice and can_switch:\n')


def installer(donors, modern):
    source = (PARENT / 'install_recovery.py').read_text(encoding='utf-8')
    source = re.sub(r'^DONOR_IDS = .*$', 'DONOR_IDS = ' + repr(tuple(donors)), source, flags=re.M)
    if modern:
        source = source.replace("if json.loads(config_path.read_text()).get('id') != expected:",
                                "if hardware_identity() != expected:")
    source += '''
# A concurrent full firmware transaction has priority over component rollback.
_component_rollback = rollback
def rollback(backup, expected, automatic=False):
    import fcntl
    lock = Path('/home/techaid/Documents/.bmi30_cloud_sync/server_operation.lock')
    with lock.open('rb') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'stage': 'deferred-by-firmware-operation', 'restored': False}
        return _component_rollback(backup, expected, automatic)
'''
    # The CLI must call the guarded implementation, including the saved tool.
    source = source.replace("if __name__ == '__main__':\n    main()\n", '')
    source += "\nif __name__ == '__main__':\n    main()\n"
    compile(source, 'menu-recovery-installer', 'exec')
    return source


def helper(donors, bundle, guard_sha):
    source = (PARENT / 'legacy_recovery.py').read_text(encoding='utf-8')
    source = re.sub(r'^DONOR_IDS = .*$', 'DONOR_IDS = frozenset(' + repr(tuple(donors)) + ')', source, flags=re.M)
    active = (PARENT / 'active_recovery.py').read_text(encoding='utf-8')
    active = re.sub(r'^ACTIVE_DONOR = .*$', 'ACTIVE_DONOR = ' + repr(donors[0]), active, flags=re.M)
    source += '\n' + active
    source += '''
# Resume the independent menu-rollback guard after a board reboot.
_CredentialRecovery = LegacyRecovery
class LegacyRecovery(_CredentialRecovery):
    def __init__(self, agent):
        super().__init__(agent)
        self.active = ActiveRecovery(self)
        self.state['mode'] = 'active-and-passive-v1'
        try:
            import subprocess
            directory = Path('/var/lib/bmi30-legacy-menu') / BOUND_BUNDLE
            guard = directory / 'guard.py'
            unit = 'bmi30-legacy-guard-' + BOUND_BUNDLE
            if (directory / 'state.json').is_file() and guard.is_file() and hashlib.sha256(guard.read_bytes()).hexdigest() == GUARD_SHA:
                status = subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=3)
                if status.stdout.strip() != 'active':
                    if (Path('/etc/systemd/system') / (unit + '.service')).is_file():
                        subprocess.run(['systemctl','start',unit],capture_output=True,timeout=5)
                    else:
                        subprocess.run(['systemd-run','--quiet','--collect','--unit='+unit,
                                        '--property=Restart=on-failure','--property=RestartSec=3',
                                        '--property=StartLimitIntervalSec=0',
                                        '/usr/bin/python3',str(guard),'--state',str(directory/'state.json')],
                                       capture_output=True,timeout=5)
        except Exception:
            pass

    def snapshot(self):
        return {**super().snapshot(), 'active': self.active.snapshot()}
'''
    source += '\nBOUND_BUNDLE = ' + repr(bundle) + '\nGUARD_SHA = ' + repr(guard_sha) + '\n'
    compile(source, 'menu-credential-recovery', 'exec')
    return source


def portal(source):
    marker = 'def main() -> None:\n    global HTTPS_RUNTIME_ENABLED\n'
    hook = '''def read_legacy_service_recovery_diagnostics():
    # Existing Agent uploads /api/status over authenticated HTTPS. No new port,
    # token, shell command, Wi-Fi secret or remote-control endpoint is added.
    try:
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8791/status', timeout=0.5) as response:
            state = json.loads(response.read(1048576))
        local = state.get('local') or {}
        recovery = state.get('legacy_recovery') or {}
        keys = ('target_id', 'enabled', 'stage', 'requests', 'offered_count', 'confirmed_count',
                'future_timestamp_conflicts', 'last_seen_age_s', 'peer_clock', 'ap_offered_count',
                'ap_confirmed_count', 'relay_available', 'relay_reserved', 'active')
        return {'available': True, 'network_version': str(state.get('version', ''))[:64],
                'local_id': str(local.get('id', ''))[:64], 'station_associated': bool(local.get('bssid')),
                'internet_ok': bool((local.get('internet') or {}).get('ok')),
                'frequency_mhz': local.get('freq'), 'signal': local.get('signal'),
                'hotspot_clients': local.get('clients'), 'recovery': {k: recovery.get(k) for k in keys}}
    except Exception:
        return {'available': False}


def install_legacy_service_recovery_menu_component():
    split = detect_bmi30_split_system_version()
    core = pathlib.Path(_resolve_project_path(split['core_path']))
    path = core.parent / 'bmi30_legacy_menu_bootstrap.py'
    spec = importlib.util.spec_from_file_location('bmi30_legacy_menu_bootstrap', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.start()


'''
    source = patch_recovery.replace_once(source, marker, hook + marker + '\n    install_legacy_service_recovery_menu_component()\n')
    source = patch_recovery.replace_once(source,
        '            payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")',
        '            data["service_recovery"] = read_legacy_service_recovery_diagnostics()\n'
        '            payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")')
    compile(source, 'menu-recovery-portal', 'exec')
    return source
