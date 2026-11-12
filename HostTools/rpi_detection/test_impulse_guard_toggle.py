"""Optional original-packet protection: real engine gates, bypass and persistence."""
import copy
import json
import os
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import test_base_width as base
import test_noise_area as config


class ToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.EngineTests.setUpClass()
        cls.ns = base.EngineTests.ns

    def setUp(self):
        base.EngineTests.setUp(self)
        self.scope._det_impulse_guard_enabled = False

    def feed(self, channel=0, **kwargs):
        self.scope._update_realtime_prevbuf_detection(channel, 100, peak_idx=80,
            prod_arr=base.pulse(24), **kwargs)

    def test_off_ignores_missing_and_vetoed_evidence_for_both_channels(self):
        for ch in (0,1):
            self.feed(ch, dual_guard_required_in=True, source_seq_in=1)
            self.feed(ch, dual_guard_required_in=True, source_seq_in=2,
                common_mode_meta_in={'timing':{'impulse_guard':{
                    'allowed':False,'enabled':True,'source_seq':2,'reason':'single_packet_dominance'}}})
            self.assertTrue(any(event[ch] for event in self.fired))
            self.assertEqual(getattr(self.scope,f'_dual_last{ch}')['reason'],'disabled')
            self.assertEqual(getattr(self.scope,f'_dual_checks{ch}',0),0)

    def test_on_requires_valid_enabled_proof_even_if_caller_does_not_require_it(self):
        self.scope._det_impulse_guard_enabled = True
        self.feed(source_seq_in=1)
        self.assertFalse(self.fired)
        self.assertEqual(self.scope._det_exceed0,0)
        for seq in (2,3):
            self.feed(source_seq_in=seq,common_mode_meta_in={'timing':{'impulse_guard':{
                'enabled':True,'allowed':True,'source_seq':seq,'reason':'distributed_support'}}})
        self.assertEqual(self.fired,[(True,False)])

    def test_on_rejects_disabled_evidence_even_when_allowed(self):
        self.scope._det_impulse_guard_enabled = True
        for seq in (1,2):
            self.feed(source_seq_in=seq,common_mode_meta_in={'timing':{'impulse_guard':{
                'enabled':False,'allowed':True,'source_seq':seq,'reason':'disabled'}}})
        self.assertFalse(self.fired)
        self.assertEqual(self.scope._dual_rejected0,2)

    def test_off_does_not_bypass_the_primary_amplitude_or_base_width_gates(self):
        for _ in range(6):
            self.scope._update_realtime_prevbuf_detection(0,0,peak_idx=80,prod_arr=base.pulse(24))
        for _ in range(6):
            self.scope._update_realtime_prevbuf_detection(0,100,peak_idx=80,prod_arr=base.pulse(25))
        self.assertFalse(self.fired)

    def test_toggle_clears_both_chains_and_fences_worker_without_changing_noise(self):
        s=self.scope
        for name in ('_det_break_confirmation_chains','_det_note_confirmation_step'):
            setattr(s,name,MethodType(self.ns[name],s))
        self.ns['common_mode_settings']=lambda settings:{k:v for k,v in settings.items() if k.startswith('common_mode')}
        s._detector_settings_snapshot=lambda:{'impulse_guard_enabled':s._det_impulse_guard_enabled}
        resets=[]
        s._rt_detector_worker_reset_for_stream=lambda reason:resets.append(reason)
        self.feed(0);self.feed(1)
        s._det_review_history=None
        s._det_noise_avg0=12345
        s._set_detector_channel_settings({'impulse_guard_enabled':'1'},persist=False)
        self.assertTrue(s._det_impulse_guard_enabled)
        for ch in (0,1):
            self.assertEqual(getattr(s,f'_det_exceed{ch}'),0)
            self.assertEqual(getattr(s,f'_det_confirm_peaks{ch}'),())
            self.assertEqual(getattr(s,f'_det_confirm_source_seqs{ch}'),())
        self.assertEqual(s._det_noise_avg0,12345)
        self.assertEqual(len(resets),1)
        s._set_detector_channel_settings({'impulse_guard_enabled':True},persist=False)
        self.assertEqual(len(resets),1)
        s._set_detector_channel_settings({'impulse_guard_enabled':'false'},persist=False)
        self.assertFalse(s._det_impulse_guard_enabled)
        self.assertEqual(s._dual_last0['reason'],'disabled')
        self.assertEqual(len(resets),2)

    def test_disabled_wrapper_executes_no_second_path(self):
        calls=[]
        self.ns['evaluate_impulse_guard']=lambda *a,**k:calls.append(1)
        fn=self.ns['_rt_detector_optional_impulse_guard']
        for value in (False,'false','0',0):
            result=fn({'impulse_guard_enabled':value},0,123,None,None)
            self.assertEqual(result,{'enabled':False,'allowed':True,'reason':'disabled','source_seq':123})
        self.assertEqual(calls,[])

    def test_enabled_wrapper_calls_original_guard_and_preserves_its_decision(self):
        calls=[]
        self.ns['_rt_detector_product_display']=object()
        self.ns['_rt_detector_relative_noise_reference']=object()
        self.ns['evaluate_impulse_guard']=lambda *a,**k: calls.append(a) or {'allowed':False,'reason':'single_packet_dominance','source_seq':123}
        result=self.ns['_rt_detector_optional_impulse_guard'](
            {'impulse_guard_enabled':True,'dual_raw_windows':{0:'raw'}},0,123,('even','odd'),[0,0,100,0,'product'])
        self.assertEqual(len(calls),1)
        self.assertFalse(result['allowed'])
        self.assertTrue(result['enabled'])


class PersistenceTests(unittest.TestCase):
    def test_form_checkbox_save_and_core_reload_match_for_on_and_off(self):
        ns=config.portal();stored={'gain':17,'operation':{'rpi_avg_n':8}}
        ns['_load_config_json']=lambda:copy.deepcopy(stored)
        ns['_save_config_json']=lambda cfg:stored.update(cfg)
        ns['load_tag_detection_config_from_core']=lambda:None
        engine=config.engine()
        with config.test_directory() as directory:
            file=directory/'config.json';engine['BMI30_CONFIG_FILE']=str(file)
            for form,value in [({},False),({'impulse_guard_enabled':'1'},True),({'impulse_guard_enabled':'0'},False)]:
                cfg=ns['tag_detection_config_from_form'](form)
                self.assertIs(cfg['impulse_guard_enabled'],value)
                ns['save_tag_detection_config'](cfg)
                self.assertIs(ns['load_tag_detection_config']()['impulse_guard_enabled'],value)
                file.write_text(json.dumps(stored))
                self.assertIs(engine['load_det_channel_settings']()['impulse_guard_enabled'],value)
                engine['save_det_channel_settings']({'impulse_guard_enabled':not value})
                persisted=json.loads(file.read_text())
                self.assertIs(persisted['tag_detection']['impulse_guard_enabled'],not value)
                self.assertIs(persisted['impulse_guard_enabled'],not value)
            self.assertEqual(stored['gain'],17)
            self.assertEqual(stored['operation']['rpi_avg_n'],8)

    def test_portal_live_apply_passes_explicit_false(self):
        ns=config.portal();requests=[]
        class Reply:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self):return b'{"ok":true}'
        ns['urlopen']=lambda request,**kw:requests.append(json.loads(request.data)) or Reply()
        cfg=ns['tag_detection_config_from_form']({'impulse_guard_enabled':'0'})
        self.assertTrue(ns['apply_tag_detection_config_to_core'](cfg)[0])
        self.assertIs(requests[0]['params']['impulse_guard_enabled'],False)


class CompatibilityTests(unittest.TestCase):
    def test_diversity_zero_and_guard_off_match_pre_diversity_primary_decisions(self):
        baseline=Path(__file__).resolve().parents[2]/'FirmwareReleases/RaspberryPi/2026-10-16-1605/review/engine.py'
        if not baseline.exists():self.skipTest('Optional pre-diversity source snapshot unavailable')
        base.EngineTests.setUpClass();current=dict(base.EngineTests.ns)
        with patch.dict(os.environ,BASE_WIDTH_TEST_ENGINE=str(baseline)):
            old=base.engine_functions()
        for key,value in current.items():old.setdefault(key,value)
        instances=[]
        for ns in (old,current):
            instance=SimpleNamespace(ns=ns)
            base.EngineTests.setUp(instance)
            s=instance.scope;s._det_confirm_sample_diversity=0;s._det_impulse_guard_enabled=False
            s._det_confirm_phase_gate=22;s._det_channel_confirm_count=lambda ch:4
            s._det_note_confirmation_step=MethodType(ns['_det_note_confirmation_step'],s)
            instances.append(instance)
        rng=np.random.default_rng(434)
        fixed=[(100,80),(100,80),(100,80),(100,80),(0,80),(100,80),(100,81),(100,82),(100,83),(0,83)]
        frames=fixed+[(100 if rng.random()>.25 else 0,int(rng.integers(65,140))) for _ in range(600)]
        for seq,(level,peak) in enumerate(frames):
            ch=seq%2 if seq>=len(fixed) else 0
            contiguous=seq<20 or seq%71!=0
            for instance in instances:
                instance.scope._update_realtime_prevbuf_detection(ch,level,peak_idx=peak,
                    prod_arr=base.pulse(24,center=peak),source_seq_in=seq,
                    confirmation_contiguous_in=contiguous)
            a,b=instances
            self.assertEqual(a.fired,b.fired)
            for channel in (0,1):
                for key in ('exceed','hold','confirm_peaks'):
                    self.assertEqual(getattr(a.scope,f'_det_{key}{channel}',None),getattr(b.scope,f'_det_{key}{channel}',None))
        self.assertTrue(instances[0].fired)


if __name__=='__main__':unittest.main()
