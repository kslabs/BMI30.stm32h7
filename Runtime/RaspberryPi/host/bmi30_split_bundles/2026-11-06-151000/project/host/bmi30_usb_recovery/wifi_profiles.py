"""One exact-SSID saved-network row; UUID variants remain operational.

GET never reads secrets. Explicit authenticated password/connect POST callers
may request secrets. No credential file, log, upload or profile deletion here.
"""
import copy
import time
import threading
import uuid

_CONNECT_LOCK = threading.Lock()


class Unavailable(RuntimeError):
    pass


def _uuid(value):
    try:
        result = str(value)
        if str(uuid.UUID(result)) != result:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise Unavailable('Invalid network selection.') from None


class NetworkManager:
    def __init__(self):
        import dbus
        self.dbus = dbus
        self.bus = dbus.SystemBus()

    def interface(self, path, name):
        return self.dbus.Interface(self.bus.get_object('org.freedesktop.NetworkManager', path), name)

    def connection(self, path):
        return self.interface(path, 'org.freedesktop.NetworkManager.Settings.Connection')

    def connections(self):
        return self.interface('/org/freedesktop/NetworkManager/Settings',
                              'org.freedesktop.NetworkManager.Settings').ListConnections(timeout=5)

    def settings(self, path):
        return self.connection(path).GetSettings(timeout=3)

    def secrets(self, path):
        return self.connection(path).GetSecrets('802-11-wireless-security', timeout=5)

    def active(self):
        props = self.interface('/org/freedesktop/NetworkManager', 'org.freedesktop.DBus.Properties')
        paths = props.Get('org.freedesktop.NetworkManager', 'ActiveConnections', timeout=3)
        return {str(self.interface(path, 'org.freedesktop.DBus.Properties').Get(
            'org.freedesktop.NetworkManager.Connection.Active', 'Uuid', timeout=3)) for path in paths}

    def device(self, interface):
        path = self.interface('/org/freedesktop/NetworkManager',
                              'org.freedesktop.NetworkManager').GetDeviceByIpIface(interface, timeout=5)
        props = self.interface(path, 'org.freedesktop.DBus.Properties')
        hardware = str(props.Get('org.freedesktop.NetworkManager.Device', 'HwAddress', timeout=3))
        try:
            permanent = str(props.Get('org.freedesktop.NetworkManager.Device.Wireless', 'PermHwAddress', timeout=3))
        except Exception:
            permanent = hardware
        active = str(props.Get('org.freedesktop.NetworkManager.Device', 'ActiveConnection', timeout=3))
        return {'path': path, 'interface': interface, 'hardware_address': hardware.lower(),
                'permanent_hardware_address': permanent.lower(), 'active_path': active}

    def update_memory(self, path, settings):
        # NM_SETTINGS_UPDATE2_FLAG_IN_MEMORY; never persist a trial password.
        self.connection(path).Update2(settings, self.dbus.UInt32(2), self.dbus.Dictionary({}, signature='sv'), timeout=5)

    def activate(self, path, device):
        return str(self.interface('/org/freedesktop/NetworkManager', 'org.freedesktop.NetworkManager').ActivateConnection(
            path, device['path'], self.dbus.ObjectPath('/'), timeout=10))

    def wait_activated(self, active_path, identity, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                values = self.interface(active_path, 'org.freedesktop.DBus.Properties').GetAll(
                    'org.freedesktop.NetworkManager.Connection.Active', timeout=3)
                if int(values.get('State', 0)) == 2:
                    return str(values.get('Uuid', '')) == identity
                if int(values.get('State', 0)) == 4:
                    return False
            except Exception:
                return False
            time.sleep(.25)
        return False

    def save(self, path):
        self.connection(path).Save(timeout=5)


def _timestamp(value):
    try:
        stamp = int(value)
        return stamp if 0 < stamp <= time.time() + 300 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def _mac(value):
    if isinstance(value, str):
        return value.lower()
    try:
        return ':'.join(f'{item:02x}' for item in bytes(value))
    except (ValueError, TypeError):
        return ''


def _rows(manager, deadline):
    active = manager.active()
    paths = manager.connections()
    if len(paths) > 256:
        raise Unavailable('Too many saved networks.')
    result = []
    for path in paths:
        if time.monotonic() > deadline:
            raise Unavailable('Saved networks are temporarily unavailable.')
        settings = manager.settings(path)
        connection, wifi = settings.get('connection', {}), settings.get('802-11-wireless', {})
        if connection.get('type') != '802-11-wireless' or wifi.get('mode', 'infrastructure') != 'infrastructure':
            continue
        try:
            identity = _uuid(connection.get('uuid', ''))
            raw = bytes(wifi.get('ssid', []))
        except (Unavailable, TypeError, ValueError):
            continue
        if not 1 <= len(raw) <= 32:
            continue
        security = str(settings.get('802-11-wireless-security', {}).get('key-mgmt', '')) or 'open'
        try:
            display = raw.decode('utf-8')
            network_key = 'wifi:' + display
        except UnicodeDecodeError:
            display = raw.decode('utf-8', errors='replace') + ' [bytes:' + raw.hex() + ']'
            network_key = None
        mac = wifi.get('mac-address', [])
        hardware = _mac(mac)
        result.append({'uuid': identity, 'ssid': display, 'ssid_hex': raw.hex(), 'network_key': network_key,
            'name': str(connection.get('id', display)), 'security': security, 'active': identity in active,
            'autoconnect': bool(connection.get('autoconnect', True)), 'password_supported': security in ('wpa-psk', 'sae'),
            'last_successful_at': _timestamp(connection.get('timestamp', 0)),
            'interface': str(connection.get('interface-name', '')), 'bssid': _mac(wifi.get('bssid', [])),
            '_hardware_address': hardware, '_path': path, '_settings': settings, '_ssid': raw})
    return result


def _public(row):
    return {key: value for key, value in row.items() if not key.startswith('_')}


def _preferred(rows):
    ordered = sorted(rows, key=lambda row: (not row['active'], -row['last_successful_at'], row['uuid']))
    chosen = ordered[0]
    active = [row for row in ordered if row['active']]
    if len(active) == 1:
        return chosen, 'active-connection', True
    if not active and chosen['last_successful_at'] and sum(row['last_successful_at'] == chosen['last_successful_at'] for row in ordered) == 1:
        return chosen, 'last-successful-activation', True
    return chosen, 'unverified-saved-profile', False


def _password(manager, row):
    if not row['password_supported']:
        return ''
    try:
        return str(manager.secrets(row['_path']).get('802-11-wireless-security', {}).get('psk', ''))
    except Exception:
        raise Unavailable('The saved password could not be read.') from None


def _owns_trial(manager, row, trial, allowed_passwords):
    current, expected = manager.settings(row['_path']), copy.deepcopy(trial)
    for values in (current, expected):
        values.get('connection', {}).pop('timestamp', None)
        values.get('802-11-wireless-security', {}).pop('psk', None)
    return current == expected and _password(manager, row) in allowed_passwords


def saved_profiles(manager, *, reveal=None, variant=None):
    if reveal is not None:
        reveal = _uuid(reveal)
    if variant is not None:
        variant = _uuid(variant)
    deadline = time.monotonic() + 12
    rows = _rows(manager, deadline)
    groups = {}
    for row in rows:
        groups.setdefault(row['ssid_hex'], []).append(row)
    if reveal is None:
        result = []
        for members in groups.values():
            chosen, reason, trusted = _preferred(members)
            item = _public(chosen)
            item.update(active=any(row['active'] for row in members),
                        password_supported=any(row['password_supported'] for row in members),
                        profile_count=len(members), variants=[_public(row) for row in sorted(members, key=lambda row: row['uuid'])],
                        selection_reason=reason, selection_has_success_evidence=trusted)
            result.append(item)
        result.sort(key=lambda item: (not item['active'], item['ssid'].casefold(), item['ssid_hex']))
        return {'ok': True, 'networks': result}
    requested = next((row for row in rows if row['uuid'] == reveal), None)
    if requested is None:
        raise Unavailable('The saved network no longer exists.')
    members = groups[requested['ssid_hex']]
    chosen, reason, trusted = _preferred(members)
    if variant is not None:
        chosen = next((row for row in members if row['uuid'] == variant), None)
        if chosen is None:
            raise Unavailable('The selected profile does not belong to this network.')
        reason, trusted = 'explicit-saved-profile', False
    elif chosen['uuid'] != requested['uuid']:
        return {'ok': True, 'available': False, 'password': '', 'refresh_required': True,
                'message': 'The selected network changed. Refresh the list.'}
    if not chosen['password_supported']:
        return {'ok': True, **_public(chosen), 'available': False, 'password': '', 'source_reason': reason}
    if trusted or variant is not None or len(members) == 1:
        password = _password(manager, chosen)
    else:
        if len(members) > 16 or len({row['security'] for row in members}) > 1:
            return {'ok': True, 'available': False, 'password': '', 'selection_required': True,
                    'message': 'Choose a saved profile to view its password.'}
        passwords = set()
        for row in members:
            if time.monotonic() > deadline:
                raise Unavailable('Saved networks are temporarily unavailable.')
            value = _password(manager, row)
            if value:
                passwords.add(value)
        if len(passwords) > 1:
            return {'ok': True, 'available': False, 'password': '', 'password_conflict': True, 'selection_required': True,
                    'message': 'Saved profiles contain different passwords. Choose a profile.'}
        password = next(iter(passwords), '')
        reason = 'matching-saved-profiles'
    return {'ok': True, **_public(chosen), 'available': bool(password), 'password': password,
            'source_reason': reason, 'password_confirmed': False}


def connect_saved(manager, ssid, password='', interface='wlan0'):
    """Reuse an eligible existing UUID. Return None only when no eligible row.

    Trial secrets stay in NM memory until a new successful activation. A failed
    or unchanged activation restores original settings; no new UUID is created.
    """
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode('utf-8')) <= 32:
        raise Unavailable('Wi-Fi SSID must be 1-32 bytes long.')
    if not isinstance(password, str):
        raise Unavailable('Invalid Wi-Fi password.')
    device = manager.device(interface)
    rows = [row for row in _rows(manager, time.monotonic() + 12) if row['_ssid'] == ssid.encode('utf-8')
            and row['interface'] in ('', interface)
            and (not row['_hardware_address'] or row['_hardware_address'] in
                 (device['hardware_address'], device.get('permanent_hardware_address', device['hardware_address'])))
            and (not password or row['password_supported'])]
    if not rows:
        return None
    chosen, _reason, _trusted = _preferred(rows)
    original = copy.deepcopy(chosen['_settings'])
    old_password = _password(manager, chosen) if chosen['password_supported'] else ''
    changed = bool(password and password != old_password)
    if changed:
        try:
            secret_flags = int(original.get('802-11-wireless-security', {}).get('psk-flags', 0))
        except (ValueError, TypeError):
            secret_flags = -1
        # IN_MEMORY prevents the profile file write; an agent-owned secret can
        # still be sent to its agent. Do not trial-edit that external store.
        if secret_flags != 0 or not old_password:
            raise Unavailable('This profile requires its credential manager to change the saved password.')
    if chosen['password_supported'] and old_password:
        original.setdefault('802-11-wireless-security', {})['psk'] = old_password
    trial = copy.deepcopy(original)
    if changed:
        trial.setdefault('802-11-wireless-security', {})['psk'] = password
    saving = False
    try:
        if changed:
            manager.update_memory(chosen['_path'], trial)
        active_path = manager.activate(chosen['_path'], device)
        if changed and active_path == device.get('active_path'):
            raise Unavailable('The connection was not reactivated. The saved password was not changed.')
        if not manager.wait_activated(active_path, chosen['uuid']):
            raise Unavailable('Unable to connect with the selected saved profile.')
        if changed:
            if not _owns_trial(manager, chosen, trial, (password,)):
                raise Unavailable('The profile changed during connection.')
            saving = True
            manager.save(chosen['_path'])
            saving = False
        return {'ok': True, 'uuid': chosen['uuid'], 'ssid': ssid, 'reused': True,
                'message': 'Connected using the existing saved network.'}
    except Exception:
        if changed:
            try:
                if not _owns_trial(manager, chosen, trial, (old_password, password)):
                    raise Unavailable('The profile changed during connection.')
                manager.update_memory(chosen['_path'], original)
                if saving:
                    # Save may have reached NM before a D-Bus timeout. Restore
                    # the prior persistent secret too; never claim certainty
                    # if that second commit cannot be acknowledged.
                    manager.save(chosen['_path'])
            except Exception:
                raise Unavailable('Connection failed. The saved profile could not be verified; review this network before retrying.') from None
        raise Unavailable('Unable to connect. The saved password was not changed.') from None


def connect_existing(ssid, password='', interface='wlan0'):
    if not _CONNECT_LOCK.acquire(blocking=False):
        raise Unavailable('Another saved-network connection is in progress.')
    try:
        return connect_saved(NetworkManager(), ssid, password, interface)
    except Unavailable:
        raise
    except Exception:
        raise Unavailable('Saved networks are temporarily unavailable.') from None
    finally:
        _CONNECT_LOCK.release()


def dispatch(action, data=None):
    try:
        if action not in ('list', 'password'):
            raise Unavailable('Invalid network action.')
        identity = data.get('uuid') if isinstance(data, dict) else None
        variant = data.get('variant_uuid') if isinstance(data, dict) else None
        if action == 'password' and not isinstance(identity, str):
            raise Unavailable('Invalid network selection.')
        return saved_profiles(NetworkManager(), reveal=identity if action == 'password' else None,
                              variant=variant if action == 'password' else None)
    except Unavailable:
        raise
    except Exception:
        raise Unavailable('Saved networks are temporarily unavailable.') from None
