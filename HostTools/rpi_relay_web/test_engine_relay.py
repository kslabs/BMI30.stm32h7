"""Exercise the real staged engine methods with an isolated detector/USB fixture."""
import ast
import os
from pathlib import Path
import time
import tarfile
from types import MethodType
import unittest
import test_relay_control as fixture


class EngineRelayTests(unittest.TestCase):
    command = fixture.RelayTests.command
    advance = fixture.RelayTests.advance

    @classmethod
    def setUpClass(cls):
        configured=os.getenv('RELAY_TEST_ENGINE')
        if configured:
            path=Path(configured)
            source=path.read_text(encoding='utf-8')
        else:
            version='2026-09-29-1720'
            archive=Path(__file__).resolve().parents[2]/'FirmwareReleases/RaspberryPi'/version/('BMI30-RPi-'+version+'.tar.gz')
            path=Path(version)/'project/host'/('BMI30.200.py.'+version)
            with tarfile.open(archive) as bundle:
                source=bundle.extractfile(path.as_posix()).read().decode('utf-8')
        tree=ast.parse(source)
        names={'_rt_apply_detection_outputs','_rt_stop_detection_outputs','_mark_web_detection_fire'}
        nodes=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in names]
        assert len(nodes)==3
        module=ast.Module(body=nodes,type_ignores=[])
        cls.methods={'time':time}
        exec(compile(module,str(path),'exec'),cls.methods)

    def setUp(self):
        fixture.RelayTests.setUp(self)
        s=self.scope
        s._relay_service=self.service
        s._det_hold0=s._det_hold1=False
        s._det_output_active=lambda channel,now:True  # Sound/LED still held for 10 s.
        s._optic_detection_gate_allowed=lambda:True
        s._current_mode_idx=lambda:6
        s._det_output_min_s=lambda:10.0
        for name in ['_group_led_apply_detection','_group_led_poll_sync_status','_send_det_adc_status',
                     '_sync_non_addressable_led_to_sound','_stop_sound_now','_beep_hold_filter_step']:
            setattr(s,name,lambda *a,**k:None)
        for name,method in self.methods.items():
            if name.startswith('_') and callable(method):setattr(s,name,MethodType(method,s))
        self.command(enabled=True,duration_ms=200)

    def test_sound_hold_does_not_extend_relay_minimum(self):
        self.scope._det_hold0=True
        self.scope._rt_apply_detection_outputs()
        self.service.step()
        self.assertEqual(self.stream.mode,1)
        self.scope._det_hold0=False
        self.scope._rt_apply_detection_outputs()
        self.advance(.21)
        self.assertEqual(self.stream.mode,0)

    def test_one_shot_without_hold_is_captured(self):
        self.scope._mark_web_detection_fire(True,False)
        self.scope._rt_apply_detection_outputs()
        self.service.step()
        self.assertEqual(self.stream.mode,1)
        self.advance(.21)
        self.assertEqual(self.stream.mode,0)

    def test_raw_detection_longer_than_minimum_stays_on(self):
        self.scope._det_hold1=True
        for _ in range(10):
            self.scope._rt_apply_detection_outputs()
            self.advance(.1)
            self.assertEqual(self.stream.mode,1)
        self.scope._det_hold1=False
        self.scope._rt_apply_detection_outputs()
        self.service.step()
        self.assertEqual(self.stream.mode,0)

    def test_selected_optic_gate_blocks_both_paths(self):
        self.scope._optic_detection_gate_allowed=lambda:False
        self.scope._det_hold0=True
        self.scope._mark_web_detection_fire(True,False)
        self.scope._rt_apply_detection_outputs()
        self.service.step()
        self.assertEqual(self.stream.mode,0)

    def test_scope_mode_does_not_energize_relay(self):
        self.scope._current_mode_idx=lambda:3
        self.scope._det_hold0=True
        self.scope._mark_web_detection_fire(True,False)
        self.scope._rt_apply_detection_outputs()
        self.service.step()
        self.assertEqual(self.stream.mode,0)

    def test_disabled_led_and_muted_sound_do_not_block_relay(self):
        self.scope._beep_enabled=False
        self.scope._non_addressable_led_enabled=False
        self.scope._det_hold1=True
        self.scope._rt_apply_detection_outputs()
        self.service.step()
        self.assertEqual(self.stream.mode,1)


if __name__=='__main__':unittest.main()
