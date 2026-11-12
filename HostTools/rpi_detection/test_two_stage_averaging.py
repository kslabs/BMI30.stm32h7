"""Published-latch source execution and unknown-alignment ranking guards."""
from copy import deepcopy
from pathlib import Path
import unittest

from analyze_raw_pass_features import records
from compare_two_stage_averaging import (PublishedCounter, PROFILES, event_windows,
                                         make_case, source_metadata, summarize)
from model_raw_detector_events import EventModel

ROOT=Path(__file__).resolve().parents[2]
CAPTURE=ROOT/'.tmp/casino-s07-35cm-20261104/passes-35cm-4-1-01/capture.jsonl.gz'


class TwoStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ready=next(r for r in records(CAPTURE) if r['kind']=='ready')

    def test_original_production_web_latch_merges_callbacks_until_real_output_expiry(self):
        model=EventModel(self.ready['status'],self.ready['metadata']['engine_sha256'])
        published=PublishedCounter(model)
        def fire(t):
            model.clock.epoch=1000.+t;model.clock.mono=t
            model.scope._mark_web_detection_fire(True,False)
        fire(0.);fire(.2)
        self.assertEqual(len(model.events),2)
        self.assertEqual(len(published.events),1)
        published.poll(1000.8)
        self.assertTrue(model.scope._web_det_flash_active0)
        published.poll(1001.21)
        self.assertFalse(model.scope._web_det_flash_active0)
        fire(1.3)
        self.assertEqual(len(published.events),2)

    def test_native_hold_prevents_web_rearm_after_minimum_timer(self):
        model=EventModel(self.ready['status'],self.ready['metadata']['engine_sha256'])
        published=PublishedCounter(model)
        model.clock.epoch=1000.;model.scope._mark_web_detection_fire(True,False)
        model.scope._det_hold0=True
        published.poll(1005.)
        self.assertTrue(model.scope._web_det_flash_active0)
        model.scope._det_hold0=False
        published.poll(1005.1)
        self.assertFalse(model.scope._web_det_flash_active0)

    def test_one_published_event_never_becomes_a_hit_in_two_marker_windows(self):
        windows=[{'pass_id':1,'start_mono':0.,'end_mono':1.,'human_result':'unlabelled'},
                 {'pass_id':2,'start_mono':1.,'end_mono':2.,'human_result':'unlabelled'}]
        counted=event_windows(windows,[{'channel':0,'monotonic':.9}])
        self.assertEqual([w['has_new_upper_event'] for w in counted],[True,False])

    def test_alignment_uncertainty_does_not_silently_pick_best_or_maximin(self):
        sources=[]
        for label in ('marked','background_before','background_after','late_cal05_stress'):
            cases=[]
            for config in PROFILES:
                for offset in range(config['group_n']):
                    # Both can reach48; STM16 has unknown47/48. FP ranges allow
                    # either winner, so neither best-offset nor minTP is chosen.
                    tp=47+(offset%2) if config['stm32']==16 else 48
                    fp=offset%2 if config['stm32']==16 else 1
                    if label=='late_cal05_stress':fp+=10
                    cases.append({'profile_id':config['id'],'offset':offset,
                      'published_upper_first5s_positive_windows':tp if label=='marked' else 0,
                      'accepted_upper_first5s_positive_windows':tp if label=='marked' else 0,
                      'published_new_events_by_channel':[fp,2], 'accepted_new_events_by_channel':[fp+1,3]})
            sources.append({'label':label,'cases':cases})
        profiles,ranking=summarize(sources)
        self.assertEqual(ranking['resolution'],'unresolved_by_unknown_alignment')
        self.assertIsNone(ranking['robust_winner'])
        self.assertFalse(ranking['conservative_minTP_policy_applied'])
        self.assertEqual(profiles[0]['confirmed_BG_published_event_ranges_by_channel'][0],[10,13])

    def test_actual_recorded_rpi12_input_is_valid_for_pre_rpi_USB_processing(self):
        path=ROOT/'.tmp/casino-s07-35cm-20261104/t33-calibration-05/capture.jsonl.gz'
        ready,_,windows=source_metadata(path)
        self.assertEqual(ready['status']['mode']['rpi_avg_n'],12)
        self.assertEqual(windows,[])


if __name__=='__main__':unittest.main()
