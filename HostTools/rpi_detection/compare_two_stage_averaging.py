"""Bounded synthetic STM8/RPi12 versus STM16/RPi8 comparison; NOT exact replay.

Original verified STM4 packets are grouped nonoverlapping per ADC/phase. All
unknown block offsets are retained. Published-event proxy executes the original
engine web-latch method and recorded status-poll clearing; no live I/O occurs.
"""
from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
from types import MethodType

from analyze_raw_continuity import FrameDecoder
from analyze_raw_pass_features import DEFAULT_HOST, ENGINE_NAME, extract_definitions, profile, records, sha256
from analyze_ring_passes import eligible_passes, normalize_records
from bmi30_cohort_averaging import CohortAverager
from model_raw_detector_events import EventModel, activity_summary, validate_baseline

PROFILES = ({'id':'STM16_RPi8','stm32':16,'rpi':8,'group_n':4},
            {'id':'STM8_RPi12','stm32':8,'rpi':12,'group_n':2})


def source_metadata(path):
    meta=[r for r in records(path) if r.get('kind')!='transfer']
    starts=[r for r in meta if r.get('kind')=='ready'];ends=[r for r in meta if r.get('kind')=='end']
    if len(starts)!=1 or len(ends)!=1:
        raise ValueError('Exactly one closed ready/end required')
    ready,end=starts[0],ends[0]
    original=profile(ready['status']);m=original['mode']
    if (m['selected'],m['stream_mode'],m['avg_n'],m['base_buf_len'])!=(6,2,4,200):
        raise ValueError('Synthetic input must be original mode6/STM4/ROI200')
    if m['rpi_avg_n'] not in (1,12) or original['depth']!=m['rpi_avg_n']:
        raise ValueError('Unexpected recorded Raspberry depth')
    for row in meta:
        if isinstance(row.get('status'),dict):
            if profile(row['status'])!=original:raise ValueError('Source profile changed')
            if row['status'].get('dc_guard',{}).get('settle_ms')!=0:raise ValueError('Frozen source DC0 required')
    normalized,_,_=normalize_records(meta,channel='upper')
    windows,invalid,pending=eligible_passes(normalized)
    if invalid or pending:raise ValueError('Malformed/incomplete start windows need a separate review')
    for window in windows:
        window['recorded_end_mono']=window['end_mono']
        window['end_mono']=min(window['end_mono'],window['start_mono']+5.)
    return ready,end,windows


def current_policy(path):
    obj=json.loads(Path(path).read_text(encoding='utf8'))
    status=obj.get('before_status',obj)
    p=profile(status);s=p['settings'];c=p['channels']
    expected=(s['noise_area_percent'],s['base_width_max'],s['confirm_phase_gate'],s['smooth_mode'],s['peak_index_min'],s['peak_index_max'])
    if expected!=(60,30,25,4,20,66):raise ValueError('Unexpected current policy; this is not a free parameter sweep')
    for name,ratio in (('upper',(1.6,1.3)),('lower',(3.,2.))):
        cc=c[name]
        if (cc['threshold'],cc['threshold_high'],cc['confirm_count'],cc['auto_threshold'])!=(*ratio,3,False):
            raise ValueError('Unexpected current channel thresholds/confirmations')
    if any(s.get(k) for k in ('common_mode_enabled','impulse_guard_enabled','confirm_growth_enabled','confirm_sample_diversity')):
        raise ValueError('Unsupported additional current gate')
    return status


class PublishedCounter:
    """Original production mark method; status clears only the output web latch."""
    def __init__(self,model):
        self.model=model;self.events=[]
        ns=model.ns
        extract_definitions(model.dsp.engine,{'_rt_detector_noise_guard_radius','_rt_detector_noise_skip_parts'},ns)
        extract_definitions(Path(DEFAULT_HOST)/'bmi30_common_mode.py',{'review_window'},ns)
        tree=ast.parse(model.dsp.engine.read_text(encoding='utf8'))
        nodes=[next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
               for name in ('_mark_web_detection_fire','_det_break_confirmation_chains')]
        future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future,*nodes],type_ignores=[])),str(model.dsp.engine),'exec'),ns)
        model.scope._det_break_confirmation_chains=MethodType(ns['_det_break_confirmation_chains'],model.scope)
        self.production_mark=MethodType(ns['_mark_web_detection_fire'],model.scope)
        accepted=model.scope._mark_web_detection_fire
        def mark(upper,lower):
            accepted(upper,lower)
            previous=[int(getattr(model.scope,'_web_det_flash_seq'+str(ch),0)) for ch in (0,1)]
            was_active=[bool(getattr(model.scope,'_web_det_flash_active'+str(ch),False)) for ch in (0,1)]
            self.production_mark(upper,lower)
            for ch,fired in enumerate((upper,lower)):
                seq=int(getattr(model.scope,'_web_det_flash_seq'+str(ch),0))
                if seq>previous[ch]:
                    self.events.append({'channel':ch,'monotonic':model.clock.mono,'epoch':model.clock.epoch,
                                        'published_seq':seq,'synthetic_source_seq':model.previous_seq[ch],
                                        'source_group':deepcopy(getattr(model,'input_provenance',{}).get(ch))})
                elif fired and not was_active[ch]:
                    raise RuntimeError('Production published-event method failed')
        model.scope._mark_web_detection_fire=mark

    def poll(self,epoch):
        # Exact source status side effect: Core1720552337..2346. Other API work,
        # devices and physical sound/relay/optic are deliberately not executed.
        for ch in (0,1):
            if not self.model.scope._det_output_active(ch,epoch):
                setattr(self.model.scope,'_web_det_flash_active'+str(ch),False)


def event_windows(windows,events):
    out=[]
    for w in windows:
        selected=[e for e in events if e['channel']==0 and w['start_mono']<=e['monotonic']<w['end_mono']]
        out.append({'pass_id':w['pass_id'],'start_mono':w['start_mono'],'end_mono':w['end_mono'],
                    'recorded_end_mono':w.get('recorded_end_mono'),'human_result_original':w['human_result'],
                    'new_upper_events':len(selected),'has_new_upper_event':bool(selected),
                    'first_latency_s':selected[0]['monotonic']-w['start_mono'] if selected else None})
    return out


def make_case(source_status,policy,engine_hash,host,config,offset):
    assumed=deepcopy(source_status)
    assumed['detector']['settings']=deepcopy(policy['detector']['settings'])
    assumed['detector']['channels']=deepcopy(policy['detector']['channels'])
    assumed['sound']=deepcopy(policy['sound'])
    assumed['mode']=dict(assumed['mode'],avg_n=config['stm32'],rpi_avg_n=config['rpi'])
    assumed['averaging']=dict(assumed['averaging'],depth=config['rpi'])
    model=EventModel(assumed,engine_hash,host,depth=config['rpi'],method='mean',loss_seconds=0.,peak_search='global')
    model.input_provenance={}
    published=PublishedCounter(model)
    resets=[]
    def reset(reason,detail):
        model.dsp.history.__init__()
        model.previous_seq=[None,None]
        model.scope._det_break_confirmation_chains('synthetic_source_'+reason)
        resets.append({'reason':reason,'detail':detail})
    grouper=CohortAverager(config['group_n'],offset,on_reset=reset)
    return {'config':config,'offset':offset,'model':model,'published':published,'grouper':grouper,'reset_events':resets}


def analyze_source(path,label,policy,host,output):
    ready,end,windows=source_metadata(path)
    cases=[make_case(ready['status'],policy,ready['metadata']['engine_sha256'],host,config,offset)
           for config in PROFILES for offset in range(config['group_n'])]
    problems=[]
    def problem(kind,detail):
        problems.append({'kind':kind,**detail})
        for case in cases:case['grouper'].reset('decoder_'+kind,detail)
    def frame(f,payload):
        for case in cases:
            made=case['grouper'].feed(f,payload)
            if made is not None:
                synthetic,data=made
                case['model'].input_provenance[synthetic['channel']]=synthetic['provenance']
                case['model'].frame(synthetic,data)
    decoder=FrameDecoder(frame,problem)
    status_polls=0
    for row in records(path):
        if row['kind']=='transfer':
            data=base64.b64decode(row['data'],validate=True)
            if row.get('result') or row.get('truncated') or len(data)!=row.get('captured') or row.get('captured')!=row.get('length'):
                problem('transfer_error',{})
                decoder.break_stream()
                continue
            decoder.feed(data,{'host_monotonic':row['monotonic'],'host_epoch':row['at'],'usb_at':row['usb_at']})
        elif row['kind'] in ('ready','status','end'):
            status_polls+=1
            for case in cases:case['published'].poll(row['at'])
    expected=end['summary']['wire']['crc_verified_frames']
    if decoder.summary().get('crc_verified_frames')!=sum(expected):raise ValueError('Decoded input count differs from recorder')
    if problems:raise ValueError('Corrupt/incomplete monitored input; groups were reset but event comparison is not qualified')
    results=[]
    for case in cases:
        model=case['model'];diag=case['grouper'].diagnostics()
        if not diag.get('anchors'):
            raise ValueError('A coherent shared source anchor could not be established')
        accepted=event_windows(windows,model.events);published=event_windows(windows,case['published'].events)
        result={'profile_id':case['config']['id'],'stm32':case['config']['stm32'],'rpi':case['config']['rpi'],
                'base_STM4_group_size':case['config']['group_n'],'offset':case['offset'],
                'accepted_new_events_by_channel':[sum(e['channel']==ch for e in model.events) for ch in (0,1)],
                'published_new_events_by_channel':[sum(e['channel']==ch for e in case['published'].events) for ch in (0,1)],
                'accepted_upper_first5s_positive_windows':sum(w['has_new_upper_event'] for w in accepted),
                'published_upper_first5s_positive_windows':sum(w['has_new_upper_event'] for w in published),
                'marked_windows':len(windows),'accepted_windows':accepted,'published_windows':published,
                'accepted_events':model.events,'published_events':case['published'].events,
                'source_group_diagnostics':diag,'resets':case['reset_events'],
                'processed':dict(model.counts),'final_mean_history':model.dsp.history.diagnostics(),
                'activity':activity_summary(model,ready['monotonic'],ready['monotonic']+end['summary']['duration_s'])}
        results.append(result)
    report={'label':label,'source_archive':str(Path(path).resolve()),'archive_sha256':sha256(path),
            'source_metadata':ready['metadata'],'source_profile':profile(ready['status']),
            'source_frozen_DC0':True,'recorded_RPi_is_after_USB_and_not_reapplied':True,
            'decoder':decoder.summary(),'wire_counts':expected,'status_polls':status_polls,
            'capture_complete_reported':end['summary']['capture_complete'],'cases':results}
    (output/(label+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'finished':label,'cases':[{'profile':r['profile_id'],'offset':r['offset'],
        'published_upper_windows':r['published_upper_first5s_positive_windows'],
        'published_events':r['published_new_events_by_channel']} for r in results]}),flush=True)
    return report


def summarize(sources):
    profiles=[]
    for config in PROFILES:
        per_source=[]
        for source in sources:
            cases=[c for c in source['cases'] if c['profile_id']==config['id']]
            per_source.append({'source':source['label'],'offsets':[{'offset':c['offset'],
                'published_upper_windows':c['published_upper_first5s_positive_windows'],
                'accepted_upper_windows':c['accepted_upper_first5s_positive_windows'],
                'published_events':c['published_new_events_by_channel'],'accepted_events':c['accepted_new_events_by_channel']} for c in cases],
                'published_upper_window_range':[min(c['published_upper_first5s_positive_windows'] for c in cases),max(c['published_upper_first5s_positive_windows'] for c in cases)],
                'accepted_upper_window_range':[min(c['accepted_upper_first5s_positive_windows'] for c in cases),max(c['accepted_upper_first5s_positive_windows'] for c in cases)],
                'published_BG_by_channel_range':[[min(c['published_new_events_by_channel'][ch] for c in cases),max(c['published_new_events_by_channel'][ch] for c in cases)] for ch in (0,1)],
                'accepted_BG_by_channel_range':[[min(c['accepted_new_events_by_channel'][ch] for c in cases),max(c['accepted_new_events_by_channel'][ch] for c in cases)] for ch in (0,1)]})
        main=next(s for s in per_source if s['source']=='marked')
        backgrounds=[s for s in per_source if s['source']!='marked']
        fp=[[sum(s['published_BG_by_channel_range'][ch][bound] for s in backgrounds) for bound in (0,1)] for ch in (0,1)]
        profiles.append({'profile':config,'published_upper_first5s_window_range':main['published_upper_window_range'],
                         'accepted_upper_first5s_window_range':main['accepted_upper_window_range'],
                         'confirmed_BG_published_event_ranges_by_channel':fp,
                         'confirmed_BG_accepted_event_ranges_by_channel':[[sum(s['accepted_BG_by_channel_range'][ch][b] for s in backgrounds) for b in (0,1)] for ch in (0,1)],
                         'sources':per_source})
    a,b=profiles
    def worst(p):return (p['published_upper_first5s_window_range'][0],-p['confirmed_BG_published_event_ranges_by_channel'][0][1])
    def best(p):return (p['published_upper_first5s_window_range'][1],-p['confirmed_BG_published_event_ranges_by_channel'][0][0])
    winner=a['profile']['id'] if worst(a)>best(b) else b['profile']['id'] if worst(b)>best(a) else None
    if worst(a)==best(a)==worst(b)==best(b):resolution='equal_within_evaluated_counts'
    else:resolution='robust_over_all_alignment_bounds' if winner else 'unresolved_by_unknown_alignment'
    return profiles,{'resolution':resolution,'robust_winner':winner,
        'objective':['maximize upper windows with NEW published event within first5s','only among equal TP minimize published UPPER events on ALL confirmed backgrounds'],
        'not_an_offset_optimization':True,'conservative_minTP_policy_applied':False,
        'bounds_policy':'Each source alignment unknown independently; background total bounds sum per-control minima/maxima. No best offset selected.',
        'lower_and_combined_channels_are_diagnostics_not_a_changed_primary_objective':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path)
    parser.add_argument('--background',action='append',type=Path,required=True)
    parser.add_argument('--background-confirmed',action='store_true')
    parser.add_argument('--current-policy',type=Path,required=True)
    parser.add_argument('--baseline-validation',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if len(args.background)!=3 or not args.background_confirmed:parser.error('Exactly3 explicitly confirmed no-tag controls required, including cal05')
    baseline=json.loads(args.baseline_validation.read_text(encoding='utf8'))
    validate_baseline(baseline,sha256(args.capture),sha256(DEFAULT_HOST/ENGINE_NAME))
    policy=current_policy(args.current_policy)
    args.output.mkdir(parents=True,exist_ok=False)
    sources=[]
    for label,path in [('marked',args.capture)]+list(zip(('background_before','background_after','late_cal05_stress'),args.background)):
        print(json.dumps({'processing':label,'profiles':PROFILES}),flush=True)
        sources.append(analyze_source(path,label,policy,DEFAULT_HOST,args.output))
    summaries,ranking=summarize(sources)
    result={'format':'bmi30-synthetic-two-stage-comparison-v1','evaluated_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'exact_replay':False,'synthetic_STM_not_real_ADC_recovery':True,'current_detection_policy':profile(policy),
        'current_policy_source_sha256':sha256(args.current_policy),'baseline_validation_sha256':sha256(args.baseline_validation),
        'profiles':summaries,'ranking':ranking,
        'code_sha256':{name:sha256(Path(__file__).parent/name) for name in ('compare_two_stage_averaging.py','bmi30_cohort_averaging.py','model_raw_detector_events.py')},
        'source_reports':[{'label':s['label'],'archive_sha256':s['archive_sha256']} for s in sources],
        'limitations':['Original STM4 rounding/DC correction and possible pre-mean clipping cannot be undone. Grouped half-up means approximate STM8/16.',
            'Synthetic sequence is virtual; sample payload CRC is not a wire CRC. Original source IDs, end-DMA and completion timestamps remain in group provenance.',
            'Synthetic readiness uses last constituent USB completion, not the unknown hypothetical STM8/16 DMA/USB timing.',
            'Legacy actual STM8/16 LatestWins, FIFO pressure, live reader/worker drops, scheduling and normal-DC feedback are not simulated.',
            'Source cohort/USB/CRC gaps reset grouped means and confirmation histories, never bridge raw groups; time-based noise history follows existing production behavior.',
            'Initial detector/noise/HOLD state is cold. All confirmed-background events, including startup events, remain in the ranking.',
            'Published output is an engine web-latch proxy with original minimum timer and recorded API poll clearing; physical sound/relay/optic and actual worker timing are absent.',
            'Each new published event belongs to at most one first5s marker window; an already-held state is not a new event.',
            'CENTER windows are unlabelled attempts, not independently verified physical passes or sensitivity qualification.',
            'All current detector knobs are explicit overrides; original source metadata and sealed archives are never rewritten.']}
    (args.output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'profiles':summaries,'ranking':ranking},ensure_ascii=False))


if __name__=='__main__':main()
