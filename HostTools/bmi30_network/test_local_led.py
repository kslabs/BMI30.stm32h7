"""Run the real LED control methods without USB, GPIO, sound or detector hardware."""
import ast,os,time,threading,unittest
from pathlib import Path
from types import MethodType,SimpleNamespace
from unittest.mock import Mock,patch

class LocalLEDTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(os.getenv('BMI30_TEST_ENGINE',str(Path(__file__).resolve().parents[2]/'.tmp/led-pattern-20261005/engine-local-led.py')))
        names={'_local_led_detection_active','_set_det_gpio_locked','_non_addressable_led_snapshot',
               '_group_led_desired_output_event','_group_led_apply_current_state_locked','_led_pattern_value','_group_led_event_key'}
        nodes=[n for n in ast.walk(ast.parse(path.read_text(encoding='utf-8'))) if isinstance(n,ast.FunctionDef) and n.name in names]
        assert len(nodes)==len(names)
        cls.methods={'time':time};exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),cls.methods)
    def setUp(self):
        self.local=[False,False]
        self.scope=SimpleNamespace(_recovery_outputs_inhibited=False,_non_addressable_led_enabled=True,
            _non_addressable_led_test_enabled=False,_sound_indicator_active=True,_sound_test_enabled=False,
            _det_gpio_backend='rpi',_det_gpio_pin_b=22,_det_gpio_last_b=False,_det_gpio_chip=Mock(HIGH=1,LOW=0),
            _group_led_manual_test_pattern=0,_group_led_neighbor_detection_enabled=True,_sound_test_upper_enabled=True,
            _group_led_local_upper_active=False,_group_led_local_lower_active=False,
            _group_led_neighbor_upper_active=True,_group_led_neighbor_lower_active=False,
            _group_led_base_held_for_optic_gate=False,_group_led_active_event='neighbor_upper_detection',_group_led_active_pattern=4)
        s=self.scope;s._det_output_active=lambda ch:self.local[ch]
        s._group_led_detection_allowed=lambda:True
        s._group_led_current_event=lambda:'neighbor_upper_detection'
        s._group_led_clear_neighbor_state_if_no_peer=lambda:False
        s._group_led_clear_detection_state=Mock();s._group_led_patterns_snapshot=lambda:{'upper_detection':8,'lower_detection':9,'both_detection':12,'fault':7}
        s._send_led_pattern=Mock(return_value=True)
        for name in ('_local_led_detection_active','_set_det_gpio_locked','_non_addressable_led_snapshot','_group_led_desired_output_event','_group_led_apply_current_state_locked','_group_led_event_key'):
            setattr(s,name,MethodType(self.methods[name],s))
    def test_neighbor_sound_never_lights_gpio_or_strip(self):
        s=self.scope;s._set_det_gpio_locked(True,True)
        self.assertFalse(s._det_gpio_last_b);s._det_gpio_chip.output.assert_not_called()
        self.assertEqual(s._non_addressable_led_snapshot()['level'],0)
        self.assertTrue(s._non_addressable_led_snapshot()['sound_output_active'])
        self.assertEqual(s._group_led_desired_output_event(),'')
        self.assertTrue(s._group_led_apply_current_state_locked())
        s._send_led_pattern.assert_called_with(0,update_ui=False)
        self.assertEqual(s._group_led_active_pattern,0)
    def test_either_local_antenna_drives_both_outputs(self):
        for flags,event in [([True,False],'upper_detection'),([False,True],'lower_detection'),([True,True],'both_detection')]:
            with self.subTest(event=event):
                self.local[:]=flags;s=self.scope;s._group_led_current_event=lambda:event
                s._set_det_gpio_locked(True,True)
                self.assertTrue(s._det_gpio_last_b);self.assertEqual(s._non_addressable_led_snapshot()['level'],1)
                self.assertEqual(s._group_led_desired_output_event(),event)
                s._group_led_apply_current_state_locked()
                self.assertEqual(s._group_led_active_pattern,{'upper_detection':8,'lower_detection':9,'both_detection':12}[event])
    def test_expired_local_flags_cannot_renew_led_output(self):
        s=self.scope;s._group_led_current_event=lambda:'upper_detection';s._det_gpio_last_b=True
        s._set_det_gpio_locked(True,True)
        s._det_gpio_chip.output.assert_called_with(22,0)
        self.assertEqual(s._group_led_desired_output_event(),'')
    def test_optic_gate_and_permission_are_preserved(self):
        self.local[0]=True;s=self.scope;s._group_led_current_event=lambda:'upper_detection'
        s._group_led_detection_allowed=lambda:False
        self.assertFalse(s._local_led_detection_active());s._set_det_gpio_locked(True,True)
        self.assertEqual(s._non_addressable_led_snapshot()['level'],0)
        s._group_led_detection_allowed=lambda:True;s._non_addressable_led_enabled=False
        self.assertEqual(s._group_led_desired_output_event(),'');self.assertEqual(s._non_addressable_led_snapshot()['level'],0)
    def test_explicit_tests_and_fault_pattern_remain_available(self):
        s=self.scope;s._non_addressable_led_test_enabled=True;s._set_det_gpio_locked(False,False)
        self.assertEqual(s._non_addressable_led_snapshot()['level'],1)
        s._group_led_manual_test_pattern=15
        self.assertEqual(s._group_led_desired_output_event(),'manual_test')
        s._group_led_current_event=lambda:'fault'
        self.assertEqual(s._group_led_desired_output_event(),'fault')
        s._group_led_manual_test_pattern=0;s._group_led_current_event=lambda:'';s._sound_test_enabled=True
        self.assertEqual(s._group_led_desired_output_event(),'upper_detection')
    def test_recovery_always_turns_off_gpio(self):
        s=self.scope;s._non_addressable_led_test_enabled=True;s._recovery_outputs_inhibited=True;s._det_gpio_last_b=True
        self.local[0]=True;s._set_det_gpio_locked(True,True)
        s._det_gpio_chip.output.assert_called_with(22,0)
        self.assertEqual(s._non_addressable_led_snapshot()['level'],0)

if __name__=='__main__':unittest.main()
