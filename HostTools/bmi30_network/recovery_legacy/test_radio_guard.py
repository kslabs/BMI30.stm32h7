"""Independent radio guardian: restore only its own still-current operation."""
import hashlib,json,pathlib,subprocess,sys,types,unittest
from unittest.mock import patch
from test_recovery import DONOR
from active_recovery import RADIO_GUARD


class GuardTests(unittest.TestCase):
    def run_guard(self, *, changed_service=False, foreign_busy=False, wrong_device=False):
        state={'id':DONOR,'ap':'wlan0ap','sta':'wlan0','station_uuid':'original-station',
               'ap_uuid':'original-ap','temporary_id':'temporary-station','token':'owned-operation',
               'service_sha':hashlib.sha256(b'original-service').hexdigest()}
        store={'/trial/state.json':json.dumps(state).encode(),
               '/proc/device-tree/serial-number':(('0000000000000000' if wrong_device else '0000000'+DONOR[6:])+'\0').encode(),
               '/run/bmi30-access/busy':b'foreign-operation' if foreign_busy else b'owned-operation',
               '/opt/bmi30-network/service.py':b'new-service' if changed_service else b'original-service'}
        class MemoryPath:
            def __init__(self,value):self.value=str(value)
            def __truediv__(self,value):return MemoryPath(self.value+'/'+str(value))
            def read_text(self):return store[self.value].decode()
            def read_bytes(self):return store[self.value]
            def is_file(self):return self.value in store
            def unlink(self,missing_ok=False):store.pop(self.value,None)
            def write_text(self,value):store[self.value]=value.encode()
        active={'wlan0':'temporary-station','wlan0ap':'--'};calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if tuple(args[:4])==('nmcli','-g','GENERAL.CON-UUID','dev'):
                return types.SimpleNamespace(stdout=active[args[-1]],returncode=0)
            if 'up' in args:
                ident=args[-1];active['wlan0ap' if ident=='original-ap' else 'wlan0']=ident
            return types.SimpleNamespace(stdout='',returncode=0)
        with patch.object(pathlib,'Path',MemoryPath),patch.object(subprocess,'run',side_effect=run),patch.object(sys,'argv',['guard','/trial']):
            try:exec(compile(RADIO_GUARD,'independent-radio-guard','exec'),{})
            except SystemExit:pass
        return store,active,calls

    def test_dead_service_restores_both_connections_and_releases_busy(self):
        store,active,calls=self.run_guard()
        self.assertEqual(active,{'wlan0':'original-station','wlan0ap':'original-ap'})
        self.assertNotIn('/run/bmi30-access/busy',store)
        self.assertEqual(json.loads(store['/trial/guard-result.json']),{'restored':True})
        self.assertTrue(any('delete' in command for command in calls))
        self.assertFalse(any('bmi30-core' in command for command in calls))

    def test_later_service_is_not_replaced_or_reconfigured(self):
        store,active,calls=self.run_guard(changed_service=True)
        self.assertFalse(calls)
        self.assertEqual(store['/opt/bmi30-network/service.py'],b'new-service')

    def test_another_operations_marker_is_left_untouched(self):
        store,active,calls=self.run_guard(foreign_busy=True)
        self.assertFalse(calls)
        self.assertEqual(store['/run/bmi30-access/busy'],b'foreign-operation')

    def test_wrong_physical_device_fails_closed(self):
        with self.assertRaises(AssertionError):self.run_guard(wrong_device=True)


if __name__=='__main__':unittest.main()
