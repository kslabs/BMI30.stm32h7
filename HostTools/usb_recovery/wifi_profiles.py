"""Read saved station profiles. Secrets are requested only by an authenticated POST.

No activation, disk export, remote upload or logging is performed here.
"""
import time
import uuid


class Unavailable(RuntimeError):
    pass


class NetworkManager:
    def __init__(self):
        import dbus
        self.dbus = dbus
        self.bus = dbus.SystemBus()

    def interface(self, path, name):
        return self.dbus.Interface(self.bus.get_object('org.freedesktop.NetworkManager', path), name)

    def connections(self):
        return self.interface('/org/freedesktop/NetworkManager/Settings',
                              'org.freedesktop.NetworkManager.Settings').ListConnections(timeout=5)

    def settings(self, path):
        return self.interface(path, 'org.freedesktop.NetworkManager.Settings.Connection').GetSettings(timeout=3)

    def secrets(self, path):
        return self.interface(path, 'org.freedesktop.NetworkManager.Settings.Connection').GetSecrets(
            '802-11-wireless-security', timeout=5)

    def active(self):
        props = self.interface('/org/freedesktop/NetworkManager', 'org.freedesktop.DBus.Properties')
        paths = props.Get('org.freedesktop.NetworkManager', 'ActiveConnections', timeout=3)
        return {str(self.interface(path, 'org.freedesktop.DBus.Properties').Get(
            'org.freedesktop.NetworkManager.Connection.Active', 'Uuid', timeout=3)) for path in paths}


def saved_profiles(manager, *, reveal=None):
    if reveal is not None:
        try:
            if str(uuid.UUID(reveal)) != reveal: raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise Unavailable('Invalid network selection.') from None
    active = manager.active()
    result = []
    deadline = time.monotonic() + 12
    paths = manager.connections()
    if len(paths) > 256: raise Unavailable('Too many saved networks.')
    for path in paths:
        if time.monotonic() > deadline: raise Unavailable('Saved networks are temporarily unavailable.')
        settings = manager.settings(path)
        connection = settings.get('connection', {})
        wifi = settings.get('802-11-wireless', {})
        if (connection.get('type') != '802-11-wireless'
                or wifi.get('mode', 'infrastructure') != 'infrastructure'):
            continue
        identity = str(connection.get('uuid', ''))
        ssid = bytes(wifi.get('ssid', [])).decode('utf-8', errors='replace')
        if not identity or not ssid: continue
        security = settings.get('802-11-wireless-security', {}).get('key-mgmt', '')
        security = str(security)
        item = {'uuid': identity, 'ssid': ssid, 'name': str(connection.get('id', ssid)),
                'security': security or 'open', 'active': identity in active,
                'autoconnect': bool(connection.get('autoconnect', True)),
                'password_supported': security in ('wpa-psk', 'sae')}
        if reveal is not None:
            if identity != reveal: continue
            if not item['password_supported']:
                return {'ok': True, **item, 'available': False, 'password': ''}
            secrets = manager.secrets(path).get('802-11-wireless-security', {})
            password = str(secrets.get('psk', ''))
            return {'ok': True, **item, 'available': bool(password), 'password': password}
        result.append(item)
    if reveal is not None: raise Unavailable('The saved network no longer exists.')
    result.sort(key=lambda item: (not item['active'], item['ssid'].casefold(), item['name']))
    return {'ok': True, 'networks': result}


def dispatch(action, data=None):
    try:
        if action not in ('list', 'password'): raise Unavailable('Invalid network action.')
        identity = data.get('uuid') if isinstance(data, dict) else None
        if action == 'password' and not isinstance(identity, str):
            raise Unavailable('Invalid network selection.')
        return saved_profiles(NetworkManager(), reveal=identity if action == 'password' else None)
    except Unavailable:
        raise
    except Exception:
        # D-Bus exceptions may include profile details. Do not forward or log them.
        raise Unavailable('Saved networks are temporarily unavailable.') from None
