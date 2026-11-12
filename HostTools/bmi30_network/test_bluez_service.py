"""Check BlueZ API contracts with an explicit fake bus, not a radio claim."""
import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

from bluetooth_transport import SERVICE_UUID, RFCOMM_CHANNEL


class BluezTests(unittest.TestCase):
    def backend(self,powered=True,cached=None,devices=True):
        bus=Mock();bus.get_object.side_effect=lambda service,path,**kwargs:path
        objects=Mock();profiles=Mock();adapter=Mock();props=Mock()
        objects.GetManagedObjects.return_value={
            '/org/bluez/hci0':{'org.bluez.Adapter1':{'Powered':powered}},
            '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01':{'org.bluez.Device1':{
                'Address':'AA:BB:CC:DD:EE:01','Name':'BMI30-old-hostname','UUIDs':[SERVICE_UUID]}},
            '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_02':{'org.bluez.Device1':{
                'Address':'AA:BB:CC:DD:EE:02','Name':'Unrelated headset','UUIDs':['audio-uuid']}}}
        if not devices:
            objects.GetManagedObjects.return_value={k:v for k,v in objects.GetManagedObjects.return_value.items() if '/dev_' not in k}
        props.Get.return_value=powered
        dbus=types.ModuleType('dbus');dbus.service=types.ModuleType('dbus.service')
        class Object:
            def __init__(self,*args):pass
            def remove_from_connection(self):pass
        dbus.service.Object=Object
        dbus.service.method=lambda *args,**kwargs:lambda fn:fn
        dbus.mainloop=types.ModuleType('dbus.mainloop');dbus.mainloop.glib=types.ModuleType('dbus.mainloop.glib')
        dbus.mainloop.glib.DBusGMainLoop=Mock()
        dbus.SystemBus=Mock(return_value=bus);dbus.UInt16=int;dbus.Boolean=bool;dbus.Array=lambda values,**kwargs:values
        mapping={'org.freedesktop.DBus.ObjectManager':objects,'org.bluez.ProfileManager1':profiles,
                 'org.bluez.Adapter1':adapter,'org.freedesktop.DBus.Properties':props}
        dbus.Interface=lambda obj,iface:mapping[iface]
        glib=Mock();callbacks=[]
        glib.timeout_add.side_effect=lambda duration,fn:callbacks.append(fn) or 1
        clock=[1000]
        def loop():
            callbacks[0]();clock[0]+=13;callbacks[0]();manager.enabled.return_value=False;callbacks[0]()
        glib.MainLoop.return_value.run.side_effect=loop
        gi=types.ModuleType('gi');gi.repository=types.ModuleType('gi.repository');gi.repository.GLib=glib
        manager=Mock();manager.agent.config={};manager.enabled.return_value=True
        manager.known_candidates.return_value=cached or {}
        modules={'dbus':dbus,'dbus.service':dbus.service,'dbus.mainloop':dbus.mainloop,
                 'dbus.mainloop.glib':dbus.mainloop.glib,'gi':gi,'gi.repository':gi.repository}
        with patch.dict(sys.modules,modules):
            # The Linux-only module is loaded solely inside the fake-bus scope.
            sys.modules.pop('bluez_service',None)
            backend=importlib.import_module('bluez_service')
            try:
                with patch.object(backend.time,'monotonic',side_effect=lambda:clock[0]),patch.object(
                        backend.random,'uniform',side_effect=lambda a,b:a):
                    backend.run(manager)
            finally:sys.modules.pop('bluez_service',None)
        return manager,bus,profiles,adapter,props

    def test_registers_separate_classic_profile_and_scopes_discovery_to_own_client(self):
        manager,bus,profiles,adapter,props=self.backend()
        args=profiles.RegisterProfile.call_args.args
        self.assertEqual(profiles.RegisterProfile.call_args.kwargs['signature'],'osa{sv}')
        self.assertEqual(args[1],SERVICE_UUID);self.assertEqual(args[2]['Channel'],RFCOMM_CHANNEL)
        self.assertFalse(args[2]['RequireAuthentication']);self.assertFalse(args[2]['RequireAuthorization'])
        options=adapter.SetDiscoveryFilter.call_args.args[0]
        self.assertEqual(options['Transport'],'bredr');self.assertTrue(options['Discoverable'])
        self.assertEqual(options['UUIDs'],[SERVICE_UUID])
        adapter.StartDiscovery.assert_called_once_with();adapter.StopDiscovery.assert_called_once_with()
        props.Set.assert_not_called()  # No power, alias, pairing or global discoverability changes.
        self.assertEqual(manager.connect.call_count,2)
        self.assertTrue(all(call.args==('AA:BB:CC:DD:EE:01',None) for call in manager.connect.call_args_list))
        profiles.UnregisterProfile.assert_called_once_with('/com/techaid/BMI30/ServiceChannel',signature='o')
        bus.close.assert_called_once()

    def test_powered_off_adapter_is_reported_without_enabling_it(self):
        manager,bus,profiles,adapter,props=self.backend(False)
        self.assertEqual(manager.state,'adapter-off')
        profiles.RegisterProfile.assert_not_called();adapter.StartDiscovery.assert_not_called()
        props.Set.assert_not_called();bus.close.assert_called_once()

    def test_cached_authenticated_address_is_redialed_after_bluez_forgets_device(self):
        manager,*_=self.backend(cached={'AA:BB:CC:DD:EE:03':'BMI30-B'},devices=False)
        self.assertEqual(manager.connect.call_count,2)
        self.assertTrue(all(c.args==('AA:BB:CC:DD:EE:03','BMI30-B') for c in manager.connect.call_args_list))


if __name__=='__main__':unittest.main()
