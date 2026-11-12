"""Actual-FIRE base-width support diagnostic, NOT worker or physical replay.

Saved FIRE snapshots supply their original peak/confirmation source IDs,
noise_avg and gate settings. Where frozen confirmation products are absent,
explicit --reconstruct-wire builds phase-separated production rolling means
from recorded CRC-verified STM packets. The final frame is checked against the
saved old width/peak value. Intermediate confirmations use the event's FINAL
noise_avg only as a labelled diagnostic; their actual noise decisions are
unknown. No capture metadata is rewritten and no hardware is accessed.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter
import hashlib
import inspect
import json
import math
from pathlib import Path

import numpy as np

from analyze_raw_continuity import FrameDecoder
from analyze_raw_pass_features import DSP, ENGINE_NAME, extract_definitions, records, sha256

ROOT=Path(__file__).resolve().parents[2]
OLD_HOST=ROOT/'FirmwareReleases/RaspberryPi/2026-11-04-175044/review/bundle/project/host'
OLD_ENGINE_SHA='02c8b7ceb354464b04f5bf3f8437a27be7b19c411f91a3477d0da22f12637bfd'
OLD_HELPER_SHA='36763d82afe2d11629844f4a6f96b9b94a869b0147d0a47451b6c4a6ef1a0d95'

def finite(value):
    return isinstance(value,(float,int))and not isinstance(value,bool)and math.isfinite(value)

def load_decision(path,candidate=False):
    ns={'isfinite':math.isfinite}
    extract_definitions(path,{'base_width_decision'},ns)
    decision=ns['base_width_decision']
    if candidate and 'noise_level'not in inspect.signature(decision).parameters:
        raise ValueError('Candidate is not ready: noise_level API is missing')
    return decision

def metadata(path):
    rows=[row for row in records(path)if row.get('kind')!='transfer']
    ready=[r for r in rows if r.get('kind')=='ready'];end=[r for r in rows if r.get('kind')=='end']
    if len(ready)!=1 or len(end)!=1:raise ValueError('One complete ready/end pair required')
    ready,end=ready[0],end[0]
    if ready['metadata']['engine_sha256']!=OLD_ENGINE_SHA:raise ValueError('Captured engine is not the pinned original')
    mode=ready['status']['mode'];settings=ready['status']['detector']['settings']
    original=(mode['selected'],mode['stream_mode'],mode['avg_n'],mode['rpi_avg_n'],mode['base_buf_len'])
    if original[0:2]!=(6,2)or original[-1]!=200:raise ValueError('Only mode6 AVG_ROI200 is supported')
    if any(settings.get(k)for k in ('filter_casino','filter_barkhausen','filter_microwire','filter_paper','tag_filter_slot1','tag_filter_slot2','tag_filter_slot3')):
        raise ValueError('Named filters require another reconstruction contract')
    events={};previous=[None,None];deltas=[0,0];resets=[0,0];frozen_arrays=0
    for row in rows:
        status=row.get('status')
        if not isinstance(status,dict):continue
        m=status['mode'];cfg=status['detector']['settings']
        current=(m['selected'],m['stream_mode'],m['avg_n'],m['rpi_avg_n'],m['base_buf_len'])
        if current!=original or cfg['smooth_mode']!=settings['smooth_mode']:
            raise ValueError('Input averaging/smoothing profile changed')
        d=status['detector']
        if d.get('review_snapshot'):frozen_arrays+=1
        for ch in (0,1):
            count=int(d['fire_seq'+str(ch)])
            if previous[ch]is not None:
                if count<previous[ch]:resets[ch]+=1;deltas[ch]+=count
                else:deltas[ch]+=count-previous[ch]
            previous[ch]=count
            snap=d.get('fire_snapshot'+str(ch))
            if not isinstance(snap,dict)or not finite(snap.get('timestamp')):continue
            if not ready['at']<=snap['timestamp']<=end['at']:continue
            key=(ch,int(snap['seq']),float(snap['timestamp']))
            events.setdefault(key,{'channel':ch,'fire_seq':int(snap['seq']),
                'event_epoch':float(snap['timestamp']),'first_observed_epoch':row['at'],'snapshot':snap})
    return ready,end,sorted(events.values(),key=lambda x:x['event_epoch']),{
        'reset_aware_api_deltas':deltas,'counter_resets':resets,
        'status_review_snapshots_present':frozen_arrays,
        'unobserved_intermediate_events_by_channel':[max(0,deltas[ch]-sum(e['channel']==ch for e in events.values()))for ch in (0,1)]}

def geometry_same(left,right):
    return isinstance(right,dict)and all(left.get(k)==right.get(k)for k in
        ('valid','left','right','width','left_kind','right_kind','ok','limit','enabled'))

def compare_product(product,peak,shift,saved,old,new):
    base=saved['base_width'];limit=base['limit'];enabled=base['enabled']
    noise=saved.get('noise_avg')
    before=old(product,peak,limit,enabled,shift)
    after=new(product,peak,limit,enabled,shift,noise_level=noise,noise_source='saved_final_FIRE_noise_avg')
    def boundary_values(decision):
        return {side:float(product[decision[side]])if isinstance(decision.get(side),int)and 0<=decision[side]<len(product)else None for side in ('left','right')}
    return {'old':before,'new':after,'old_boundary_values':boundary_values(before),
        'new_boundary_values':boundary_values(after),'noise_level':noise,
        'noise_source':'saved final FIRE snapshot.noise_avg; never current/re-estimated noise'}

def analyze(path,old_host,candidate_helper,fixed_shifts,reconstruct):
    path=Path(path);ready,end,events,observation=metadata(path)
    if sha256(old_host/ENGINE_NAME)!=OLD_ENGINE_SHA or sha256(old_host/'bmi30_common_mode.py')!=OLD_HELPER_SHA:
        raise ValueError('Original production source hash mismatch')
    old=load_decision(old_host/'bmi30_common_mode.py');new=load_decision(candidate_helper,True)
    dsp=DSP(old_host,ready['status']['detector']['settings'],OLD_ENGINE_SHA)
    depth=ready['status']['mode']['rpi_avg_n'];stm=ready['status']['mode']['avg_n']
    targets=set();references=[]
    for event in events:
        snap=event['snapshot'];seqs=snap.get('confirm_source_sequences')or[];peaks=snap.get('confirm_peaks')or[]
        event['confirmation_checks']=[]
        for index in range(max(len(seqs),int(snap.get('confirm_required',0)))):
            seq=seqs[index]if index<len(seqs)else None
            peak=peaks[index]if index<len(peaks)else None
            ref={'confirmation_index':index,'source_seq':seq,'saved_peak':peak,
                'is_final':index==len(seqs)-1,'actual_confirmation_noise_known':index==len(seqs)-1,
                'actual_frozen_product_available':False,'status':'UNKNOWN','reason':'wire reconstruction not requested'}
            event['confirmation_checks'].append(ref)
            if type(seq)is int and type(peak)is int:
                targets.add((event['channel'],seq&0xffffffff));references.append((event,ref))
            else:ref['reason']='missing confirmation source sequence/peak'
    decoded={};problems=[];counts=Counter();last_dma={};dma_gaps=[];source_windows={}
    if reconstruct:
        def problem(kind,detail):
            problems.append({'kind':kind,**detail});dsp.history.__init__()
        def frame(frame,payload):
            ch,seq,phase=frame['channel'],frame['seq'],frame['phase'];counts[str(ch)]+=1
            if frame['samples']!=200:raise ValueError('Non-ROI200 packet')
            slot=(ch,phase);previous=last_dma.get(slot)
            if previous is not None and((frame['reserved']-previous)&0xffffffff)!=2*stm:
                dma_gaps.append({'channel':ch,'phase':phase,'source_seq':seq,'previous_dma':previous,'dma':frame['reserved']})
            last_dma[slot]=frame['reserved']
            raw=np.frombuffer(payload,dtype='<u2').astype(np.int32)
            stamp=(1,seq,frame['timestamp_ticks'],frame['reserved2'],200)
            full=dsp.history.feed(ch,raw,phase,stamp,0,depth)
            if(ch,seq)not in targets:return
            if not full:
                decoded.setdefault((ch,seq),[]).append({'error':'Incomplete phase-separated history'})
                return
            smooth=[dsp.ns['_rt_detector_smooth_array'](x,dsp.settings['smooth_mode'])for x in dsp.history.pairs[ch]]
            made=dsp.ns['_rt_detector_product_current_prev'](*smooth,dsp.settings['phase_max_shift'],
                'prod',16384.250003814755,fixed_shift=fixed_shifts[ch])
            if made is None:
                decoded.setdefault((ch,seq),[]).append({'error':'Product helper returned no product'});return
            level,shift,peak,value,product=made
            window=dsp.history.raw_windows([ch])[ch]
            decoded.setdefault((ch,seq),[]).append({'product':np.array(product,copy=True),'global_peak':int(peak),
                'level':int(level),'shift':int(shift),'completion_epoch':frame['completion_timing']['host_epoch'],
                'phase_window_sequences':[list(w)for w in window['sequences']],
                'phase_window_counts':[len(w)for w in window['sequences']]})
        decoder=FrameDecoder(frame,problem)
        for row in records(path):
            if row.get('kind')!='transfer':continue
            data=base64.b64decode(row['data'],validate=True)
            if row.get('result')or row.get('truncated')or len(data)!=row.get('captured'):
                problem('invalid_transfer',{'at':row['at']});decoder.break_stream();continue
            decoder.feed(data,{'host_monotonic':row['monotonic'],'host_epoch':row['at'],'usb_at':row['usb_at']})
        decoder_report=decoder.summary()
    else:decoder_report=None
    for event,ref in references:
        if not reconstruct:continue
        snap=event['snapshot'];key=(event['channel'],ref['source_seq']&0xffffffff);matches=decoded.get(key,[])
        if len(matches)!=1:
            ref['reason']='missing/ambiguous source packet';continue
        made=matches[0]
        if 'error'in made:ref['reason']=made['error'];continue
        if not isinstance(snap.get('base_width'),dict)or not finite(snap.get('noise_avg'))or snap['noise_avg']<=0:
            ref['reason']='missing valid saved noise_avg/base-width decision';continue
        comparison=compare_product(made['product'],ref['saved_peak'],made['shift'],snap,old,new)
        ref.update(comparison,status='RECONSTRUCTED_DIAGNOSTIC',reason='wire-derived waveform; not a frozen worker product',
            phase_window_counts=made['phase_window_counts'],phase_window_sequences=made['phase_window_sequences'],
            reconstructed_global_peak=made['global_peak'],reconstructed_level=made['level'],
            frame_completion_epoch=made['completion_epoch'],shift=made['shift'],
            per_confirmation_noise_limit=None if ref['is_final']else'Actual intermediate noise_avg unavailable; final event noise used only as a diagnostic')
        if ref['is_final']:
            match=(made['global_peak']==snap['peak_index']and made['level']==snap['level']
                   and geometry_same(comparison['old'],snap['base_width'])
                   and made['completion_epoch']<=event['event_epoch']+.002)
            ref['matches_saved_final_peak_level_old_geometry']=match
            ref['new_reject_on_matching_final_frame']=bool(match and comparison['old']['ok']and not comparison['new']['ok'])
        else:ref['matches_saved_final_peak_level_old_geometry']=None
    summary=[]
    for ch in(0,1):
        rows=[e for e in events if e['channel']==ch];finals=[r for e in rows for r in e['confirmation_checks']if r['is_final']]
        matched=[r for r in finals if r.get('matches_saved_final_peak_level_old_geometry')is True]
        summary.append({'channel':ch,'actual_FIRE_snapshots':len(rows),
            'final_frame_reconstructed':sum(r['status']=='RECONSTRUCTED_DIAGNOSTIC'for r in finals),
            'final_frame_matches_saved_old_decision':len(matched),
            'new_reject_at_matching_final_frame':sum(r['new_reject_on_matching_final_frame']for r in matched),
            'new_pass_at_matching_final_frame':sum(bool(r['new']['ok'])for r in matched),
            'final_frame_unknown_or_nonmatching':len(rows)-len(matched),
            'intermediate_confirmations_without_actual_saved_noise':sum(not r['actual_confirmation_noise_known']for e in rows for r in e['confirmation_checks'])})
    for event in events:
        snap=event.pop('snapshot');event['saved_FIRE']={k:snap.get(k)for k in('level','noise_avg','noise_ref','peak_index','base_width','confirm_required')}
    return {'capture':str(path),'archive_sha256':sha256(path),'recorded_mode':ready['status']['mode'],
        'recording_complete':end.get('summary',{}).get('capture_complete'),
        'observation':observation,'summary_by_channel':summary,'events':events,
        'decoder':decoder_report,'decoder_problems':problems,'source_DMA_gap_observations':dma_gaps,
        'source_hashes':dsp.source_hashes,'scope':{
            'actual_frozen_worker_products_available':False,'wire_reconstruction_requested':reconstruct,
            'history':'Exact production DualWindowHistory on recorded delivered STM means; np.rint mean per channel/phase, spatial smoothing once',
            'worker_queue_coalescing_and_original_history_unknown':True,
            'noise':'Saved final FIRE noise_avg, never current noise. Intermediate confirmation floor is not known.',
            'DMA_gap_policy':'Reported, not hidden; production DualWindowHistory resets on USB-seq gaps, not DMA-only skips.',
            'risk_counts_are_final_frame_support_diagnostics_not_predicted_misses':True}}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',type=Path,action='append',required=True)
    p.add_argument('--old-host',type=Path,default=OLD_HOST)
    p.add_argument('--candidate-helper',type=Path,required=True)
    p.add_argument('--fixed-shifts',nargs=2,type=int,required=True)
    p.add_argument('--reconstruct-wire',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.fixed_shifts!=[0,0]:p.error('Only independently confirmed fixed RX0/0 is supported')
    if args.output.exists():p.error('Output must be new')
    candidate_sha=sha256(args.candidate_helper);load_decision(args.candidate_helper,True)
    result={'format':'bmi30-base-width-actual-FIRE-support-diagnostic-v1','exact_replay':False,
        'physical_detection_prediction':False,'old_helper_sha256':OLD_HELPER_SHA,
        'candidate_helper':str(args.candidate_helper),'candidate_helper_sha256':candidate_sha,
        'fixed_shifts':args.fixed_shifts,'fixed_shift_provenance':'Explicit operator/root-provided; wire does not encode host RX policy',
        'tool_sha256':sha256(Path(__file__)),'recordings':[
            analyze(path,args.old_host,args.candidate_helper,args.fixed_shifts,args.reconstruct_wire)for path in args.capture],
        'limitations':['No independently labelled hit/miss probabilities are inferred.',
            'A rejected matching final support frame can have other detection opportunities later in the pass.',
            'Intermediate per-confirmation noise_avg was not recorded. Their new widths use final event noise only as an explicit diagnostic.',
            'Raw-product reconstruction is not proof of the actual live worker input/history; matching original peak, value and base geometry is a conformance check.']}
    if sha256(args.candidate_helper)!=candidate_sha:raise ValueError('Candidate helper changed during analysis; rerun on a frozen copy')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf8')as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'output':str(args.output),'candidate_helper_sha256':candidate_sha,
        'recordings':[{'capture':r['capture'],'summary':r['summary_by_channel']}for r in result['recordings']]},indent=2))

if __name__=='__main__':main()
