"""BlueZ profile/discovery adapter; imported only on Linux with BlueZ installed."""
import socket
import time
import random

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

from bluetooth_transport import SERVICE_UUID, RFCOMM_CHANNEL

PROFILE_PATH = '/com/techaid/BMI30/ServiceChannel'
PROFILE_IFACE = 'org.bluez.Profile1'
ADAPTER_IFACE = 'org.bluez.Adapter1'
PROPERTIES = 'org.freedesktop.DBus.Properties'


def run(manager):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus(private=True)
    loop = GLib.MainLoop()
    objects = dbus.Interface(bus.get_object('org.bluez', '/', introspect=False), 'org.freedesktop.DBus.ObjectManager')
    profiles = dbus.Interface(bus.get_object('org.bluez', '/org/bluez', introspect=False), 'org.bluez.ProfileManager1')

    class Profile(dbus.service.Object):
        @dbus.service.method(PROFILE_IFACE, in_signature='', out_signature='')
        def Release(self):
            loop.quit()

        @dbus.service.method(PROFILE_IFACE, in_signature='oha{sv}', out_signature='')
        def NewConnection(self, device, fd, properties):
            raw = fd.take()
            try:
                sock = socket.socket(fileno=raw)
            except Exception:
                import os
                os.close(raw)
                raise
            address = str(device).rsplit('/dev_', 1)[-1].replace('_', ':')
            manager.accept(sock, address)

        @dbus.service.method(PROFILE_IFACE, in_signature='o', out_signature='')
        def RequestDisconnection(self, device):
            address = str(device).rsplit('/dev_', 1)[-1].replace('_', ':')
            with manager.lock:
                for session in list(manager.links.values()):
                    if session.address == address:
                        session.close()

    profile = Profile(bus, PROFILE_PATH)
    registered = False
    adapter = props = None
    scanning = False
    scan_started = last_scan = -100
    next_scan = time.monotonic() + random.uniform(0, 3)
    last_objects = -100
    last_dial = {}
    timer = None
    try:
        all_objects = objects.GetManagedObjects()
        selected = manager.agent.config.get('bluetooth', {}).get('adapter')
        adapters = [(str(path), data[ADAPTER_IFACE]) for path, data in all_objects.items()
                    if ADAPTER_IFACE in data and (not selected or str(path).endswith('/' + selected))]
        if not adapters:
            raise RuntimeError('Bluetooth adapter unavailable')
        path, state = sorted(adapters)[0]
        manager.address = str(state.get('Address','')).upper()
        adapter = dbus.Interface(bus.get_object('org.bluez', path), ADAPTER_IFACE)
        props = dbus.Interface(bus.get_object('org.bluez', path), PROPERTIES)
        if not state.get('Powered'):
            manager.state, manager.error = 'adapter-off', ''
            return
        profiles.RegisterProfile(PROFILE_PATH, SERVICE_UUID, {
            'Name': 'BMI30 Service Channel', 'Role': 'server', 'Channel': dbus.UInt16(RFCOMM_CHANNEL),
            # Fleet HMAC and AES-GCM authenticate the application independently
            # of consumer pairing. No automatic trust or pairing-agent changes.
            'RequireAuthentication': dbus.Boolean(False), 'RequireAuthorization': dbus.Boolean(False)},
            signature='osa{sv}')
        registered = True
        # Discoverability belongs only to this D-Bus discovery session. There
        # are no persistent adapter/Pairable/Powered/alias changes to restore.
        adapter.SetDiscoveryFilter({'Transport': 'bredr', 'UUIDs': dbus.Array([SERVICE_UUID], signature='s'),
                                    'Discoverable': dbus.Boolean(True)})
        manager.state, manager.error = 'listening', ''

        def tick():
            nonlocal scanning, scan_started, last_scan, last_objects, next_scan
            if not manager.enabled():
                loop.quit()
                return False
            try:
                if not props.Get(ADAPTER_IFACE, 'Powered'):
                    loop.quit()
                    return False
                now = time.monotonic()
                if scanning and now - scan_started >= 12:
                    adapter.StopDiscovery()
                    scanning = False
                if not scanning and now >= next_scan:
                    adapter.StartDiscovery()
                    scanning = True
                    scan_started = last_scan = now
                    next_scan = now + random.uniform(30, 40)
                if now - last_objects >= 3:
                    last_objects = now
                    candidates = manager.known_candidates()
                    for item in manager.agent.config.get('bluetooth', {}).get('peers', []):
                        candidates[str(item['address']).upper()] = item.get('id')
                    for device_path, interfaces in objects.GetManagedObjects().items():
                        device = interfaces.get('org.bluez.Device1', {})
                        if not str(device_path).startswith(path + '/dev_'):
                            continue
                        # A name/UUID is only a discovery hint. The CM5 number
                        # is accepted only after the cryptographic handshake.
                        uuids = [str(u).lower() for u in device.get('UUIDs', [])]
                        name = str(device.get('Name', ''))
                        if SERVICE_UUID in uuids or name.upper().startswith('BMI30-'):
                            candidates.setdefault(str(device.get('Address', '')).upper(), None)
                    for address, expected in sorted(candidates.items())[:32]:
                        if now - last_dial.get(address, -100) >= 10:
                            last_dial[address] = now
                            manager.connect(address, expected)
            except Exception:
                manager.error = 'bluez-discovery'
                loop.quit()
                return False
            return True

        timer = GLib.timeout_add(1000, tick)
        loop.run()
    finally:
        if timer is not None:
            context = GLib.MainContext.default()
            if context.find_source_by_id(timer):
                GLib.source_remove(timer)
        if scanning:
            try:
                adapter.StopDiscovery()  # Releases only this client's inquiry.
            except Exception:
                pass
        if registered:
            try:
                profiles.UnregisterProfile(PROFILE_PATH, signature='o')
            except Exception:
                pass
        profile.remove_from_connection()
        bus.close()
