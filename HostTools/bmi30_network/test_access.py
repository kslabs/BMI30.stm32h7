import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from access_power import PowerTrial
import access_policy

class PowerTests(unittest.TestCase):
    def setUp(self):
        self.now=0; self.operations=[]
        self.p=PowerTrial(lambda dbm:self.operations.append(('limit',dbm)) is None,
                          lambda:self.operations.append(('auto',)) is None,lambda:self.now)
    def sample(self,signal=-25,packets=100,failed=0,uplink=False,count=1,power=20):
        return self.p.sample([dict(mac='aa',signal=signal,packets=packets,failed=failed,connected=self.now)]*count,uplink,current_dbm=power)
    def trial(self):
        self.sample();self.now=15;self.sample(packets=180,failed=20)
        self.now=30;self.sample(packets=260,failed=40)
        self.assertTrue(self.p.active)
    def test_strong_signal_without_errors_never_reduces_power(self):
        for self.now in range(0,120,15):self.sample(packets=100+self.now)
        self.assertEqual(self.operations,[])
    def test_router_link_prevents_phy_power_changes(self):
        for self.now in range(0,120,15):self.sample(packets=100+self.now,failed=self.now,uplink=True)
        self.assertEqual(self.operations,[])
    def test_multiple_clients_prevent_trial(self):
        self.sample(count=2);self.assertEqual(self.operations,[])
    def test_failure_trial_is_only_three_db(self):
        self.trial();self.assertEqual(self.operations,[('limit',17)])
    def test_worse_transmission_restores_default(self):
        self.trial();self.now=45;self.sample(packets=340,failed=65)
        self.assertFalse(self.p.active);self.assertEqual(self.operations[-1],('auto',))
    def test_router_association_immediately_restores_power(self):
        self.trial();self.now=45;self.sample(uplink=True)
        self.assertEqual(self.operations[-1],('auto',))
    def test_client_departure_restores_power(self):
        self.trial();self.sample(count=0);self.assertFalse(self.p.active)
    def test_idle_trial_is_reverted(self):
        self.trial();self.now=45;self.sample(packets=260,failed=40)
        self.assertFalse(self.p.active)
    def test_untrustworthy_driver_readback_is_not_used(self):
        for self.now in range(0,120,15):self.sample(packets=100+self.now,failed=self.now,power=31)
        self.assertEqual(self.operations,[])
    def test_unsupported_driver_disables_trial(self):
        self.p.apply_limit=lambda dbm:False
        self.sample();self.now=15;self.sample(packets=180,failed=20)
        self.now=30;self.sample(packets=260,failed=40)
        self.assertTrue(self.p.disabled);self.assertEqual(self.operations[-1],('auto',))
    def test_counter_reset_restores_power(self):
        self.trial();self.now=45;self.sample(packets=10,failed=0)
        self.assertFalse(self.p.active)

class PolicyTests(unittest.TestCase):
    def test_no_file_means_normal_mode_and_corrupt_file_fails_closed(self):
        with patch.object(Path,'read_text',side_effect=FileNotFoundError):
            self.assertEqual(access_policy.preferences(),{})
        with patch.object(Path,'read_text',return_value='bad'):
            with self.assertRaises(ValueError):access_policy.preferences()
        with patch.object(access_policy,'preferences',side_effect=ValueError):
            self.assertTrue(access_policy.offline_mode())
            self.assertTrue(access_policy.forgotten('router'))

def linux_access():
    # Windows can exercise pure input decoding without faking a physical press.
    if 'fcntl' not in sys.modules and sys.platform=='win32':
        import types
        sys.modules['fcntl']=types.SimpleNamespace()
    spec=importlib.util.spec_from_file_location('bmi30_access_tests',Path(__file__).with_name('access.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m

class ButtonTests(unittest.TestCase):
    def test_short_press_once_long_press_and_repeat_ignored(self):
        m=linux_access();now=[0];events=[]
        b=m.Button(lambda:events.append('recover'),lambda:now[0])
        b.event(1,116,1);now[0]=.2;b.event(1,116,0)
        self.assertEqual(events,['recover'])
        now[0]=20;b.event(1,116,1);now[0]=22;b.event(1,116,0)
        self.assertEqual(events,['recover'])
        now[0]=30;b.event(1,116,1);b.event(1,116,2);now[0]=30.2;b.event(1,116,0)
        self.assertEqual(events,['recover','recover'])
    def test_busy_callback_does_not_break_future_presses(self):
        m=linux_access();now=[0];events=[]
        def callback():
            events.append('call')
            if len(events)==1:raise ValueError('busy')
        b=m.Button(callback,lambda:now[0])
        for t in (0,20):
            now[0]=t;b.event(1,116,1);now[0]=t+.2;b.event(1,116,0)
        self.assertEqual(len(events),2)
    def test_no_poweroff_reboot_core_or_usb_operations(self):
        tree=ast.parse(Path(__file__).with_name('access.py').read_text(encoding='utf-8'))
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run']
        for call in calls:
            literals=[n.value for n in call.args if isinstance(n,ast.Constant) and isinstance(n.value,str)]
            self.assertFalse(set(literals)&{'reboot','poweroff','bmi30-core','NetworkManager','usbreset'})

class ParsingTests(unittest.TestCase):
    def test_signal_and_loss_read_from_actual_station_format(self):
        m=linux_access()
        s=m.station_metrics('Station aa:bb:cc:dd:ee:ff (on wlan0ap)\n\tsignal: -25 [-25] dBm\n\ttx packets: 120\n\ttx failed: 2\n\tconnected time: 30 seconds')
        self.assertEqual(s[0]['signal'],-25);self.assertEqual(s[0]['packets'],120)

if __name__=='__main__':unittest.main()
