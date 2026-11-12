"""Analyze sampled API noise and exact saved confirmation chains offline.

Alternative estimates do not replay the complete live detector and do not prove
tag sensitivity or a counterfactual count of confirmed events.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
import datetime as dt
import json
from pathlib import Path

import numpy as np


def quantiles(values):
    a=np.asarray(values,dtype=float)
    if not a.size:return {}
    return {key:float(value) for key,value in zip(['min','p05','p50','p90','p95','p99','max'],np.quantile(a,[0,.05,.5,.9,.95,.99,1]))}


def load(path):
    samples=[];events=[];boundaries={};errors=[]
    with path.open(encoding='utf8') as file:
        for line in file:
            try:r=json.loads(line)
            except json.JSONDecodeError:continue
            kind=r.get('kind')
            if kind=='sample':samples.append(r)
            elif kind=='event_frame':events.append(r)
            elif kind in ('start','end'):boundaries[kind]=r
            elif kind in ('error','event_error'):errors.append(r)
    return samples,events,boundaries,errors


def summarize(folder):
    samples,events,bounds,errors=load(folder/'M02.jsonl')
    assert samples
    first=bounds.get('start',samples[0]);last=bounds.get('end',samples[-1])
    start_epoch=first['epoch'];a,b=first['status'],last['status']
    usable=[r for r in samples if r['status'].get('ok') and not r['status'].get('stale') and not r['status'].get('busy')]
    result={'complete':'end' in bounds,'duration_s':last['epoch']-start_epoch,'samples':len(samples),
            'usable_samples':len(usable),'request_errors':len(errors),'start_epoch':start_epoch,
            'start_local':dt.datetime.fromtimestamp(start_epoch,dt.timezone(dt.timedelta(hours=2))).isoformat(),
            'end_local':dt.datetime.fromtimestamp(last['epoch'],dt.timezone(dt.timedelta(hours=2))).isoformat(),
            'mode':a['mode'],'settings':a['detector']['settings'],'channels':a['detector']['channels'],
            'request_ms':quantiles([r['request_ms'] for r in samples]),
            'fire_delta':{str(ch):b['detector'][f'fire_seq{ch}']-a['detector'][f'fire_seq{ch}'] for ch in (0,1)},
            'stream_delta':{key:b['stream_stats'][key]-a['stream_stats'][key] for key in ['rx_cnt_ch0','rx_cnt_ch1','seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail']},
            'worker_delta':{key:b['detector']['worker'][key]-a['detector']['worker'][key] for key in ['results','overflows']},
            'over_100ms_delta':b['detector']['worker']['latency']['over_100ms']-a['detector']['worker']['latency']['over_100ms'],
            'same_core_pid':first.get('core_pid')==last.get('core_pid') if 'end' in bounds else None,
            'config_unchanged':first.get('config_sha256')==last.get('config_sha256') if 'end' in bounds else None,
            'active_env_unchanged':first.get('env_sha256')==last.get('env_sha256') if 'end' in bounds else None,
            'worker_alive_all':all(r['status']['detector']['worker']['alive'] for r in usable),
            'player_inactive_all':all(not r['status']['player_recorder']['recording'] and not r['status']['player_recorder']['playback_active'] for r in usable),
            'mode_unchanged':all(r['status']['mode']==a['mode'] for r in usable),
            'settings_unchanged':all(r['status']['detector']['settings']==a['detector']['settings'] for r in usable),
            'sound_active_samples':sum(bool(r['status'].get('sound',{}).get('output_active')) for r in usable),
            'optic_blocked_samples':sum(r['status'].get('optic',{}).get('reaction_gate_allowed') is False for r in usable),
            'events':[],'noise_statistics':{},'alternatives':{}}
    models=['live_mean_5s','instant_only','mean_1s','median_5s','p90_1s','p95_1s','p90_5s','p95_5s',
            'mean_plus_p90_5s','mean_plus_p95_5s',
            'max_1s','max_5s','attack_release_0.5s','attack_release_1s','attack_release_2s']
    measurements={0:{},1:{}}
    for r in usable:
        s=r['status'];d=s['detector']
        for ch,name in [(0,'upper'),(1,'lower')]:
            seq=d['worker'].get(f'source_seq{ch}')
            if seq is None:continue
            measurements[ch].setdefault(seq,{'seq':seq,'t':r['epoch'],'noise':float(d.get(f'noise_ref{ch}',0)),
                'mean':float(d.get(f'noise_avg{ch}',0)),'level':float(d.get(f'lvl{ch}',0)),
                'trigger':float(d.get(f'trigger{ch}',0)),'k':float(d['channels'][name]['effective_threshold']),
                'position_ok':bool(d.get(f'peak_position_ok{ch}')),'width_ok':bool((d.get(f'base_width{ch}') or {}).get('ok',True)),
                'peak':int(d.get(f'peak_index{ch}',-1)),'exact':False})
    exact_steps={}
    for event in events:
        ch=event['channel'];frame=event['frame'];snapshot=frame.get('event_snapshot') or {}
        steps=[s for s in frame.get('confirmation_steps',[]) if isinstance(s,dict)]
        detail={'elapsed_s':event['elapsed_s'],'channel':ch,'fire_seq':event['fire_seq'],
                'exact_frame':bool(frame.get('exact_event_frame')),'steps':len(steps),
                'instant_snr':snapshot.get('instant_snr'),'level':snapshot.get('level'),
                'noise_ref':snapshot.get('noise_ref'),'noise_avg':snapshot.get('noise_avg'),
                'peak':snapshot.get('peak_index'),'base_width':snapshot.get('base_width',{}).get('width'),
                'chain_snr_min':min((s['level']/s['noise_ref'] for s in steps if s['noise_ref']>0),default=None),
                'chain_duration_s':steps[-1]['timestamp']-steps[0]['timestamp'] if steps else None,
                'all_instant_pass':all(s.get('instant_noise_ok') for s in steps),
                'instant_only_reject_steps':sum(s['level']<=s['noise_ref']*s['instant_ratio_required'] for s in steps),
                'models_break_saved_chain':{}}
        near=[s for s in usable if event['epoch']-.15 <= s['epoch'] <= event['epoch']+.3]
        detail['sound_active_near_event']=any(s['status'].get('sound',{}).get('output_active') for s in near)
        detail['optic_allowed_near_event']=any(s['status'].get('optic',{}).get('reaction_gate_allowed') for s in near)
        result['events'].append(detail)
        for step in steps:
            seq=step['source_seq']
            m={'seq':seq,'t':step['timestamp'],'noise':step['noise_ref'],'mean':step['noise_avg'],
               'level':step['level'],'trigger':step['trigger'],'k':step['instant_ratio_required'],
               'position_ok':step['peak_position_ok'],'width_ok':step['base_width']['ok'],
               'peak':step['peak_index'],'exact':True}
            measurements[ch][seq]=m;exact_steps[(ch,seq)]=m
    plot_data={}
    for ch in (0,1):
        points=sorted(measurements[ch].values(),key=lambda m:m['seq'])
        noise_hist=deque();peak_hold={tau:0. for tau in (.5,1.,2.)};previous_t=None
        counts=Counter();values={model:[] for model in models};thresholds={model:[] for model in models}
        for m in points:
            t,n=m['t'],m['noise'];noise_hist.append((t,n))
            while noise_hist and noise_hist[0][0]<t-5:noise_hist.popleft()
            past5=np.asarray([x[1] for x in noise_hist if t-5<=x[0]<=t]);past1=np.asarray([x[1] for x in noise_hist if t-1<=x[0]<=t])
            elapsed=max(0,t-previous_t) if previous_t is not None else 0
            estimates={'live_mean_5s':m['mean'],'instant_only':n,'mean_1s':float(past1.mean()),
                'median_5s':float(np.median(past5)), 'p90_1s':float(np.quantile(past1,.9)),
                'p95_1s':float(np.quantile(past1,.95)),'p90_5s':float(np.quantile(past5,.9)),
                'p95_5s':float(np.quantile(past5,.95)), 'max_1s':float(past1.max()),'max_5s':float(past5.max())}
            for tau in peak_hold:
                peak_hold[tau]=max(n,peak_hold[tau]*np.exp(-elapsed/tau))
                estimates[f'attack_release_{tau:g}s']=max(m['mean'],peak_hold[tau])
            estimates['mean_plus_p90_5s']=max(m['mean'],estimates['p90_5s'])
            estimates['mean_plus_p95_5s']=max(m['mean'],estimates['p95_5s'])
            previous_t=t;m['estimates']=estimates;m['pass']={}
            for model,estimate in estimates.items():
                # Hold the coefficient recorded in this decision constant to
                # isolate the choice of temporal noise estimate.
                threshold=m['trigger'] if model=='live_mean_5s' else max(1,estimate*m['k'])
                allowed=m['level']>=threshold and m['level']>n*m['k'] and m['position_ok'] and m['width_ok']
                m['pass'][model]=bool(allowed)
                if m['t']-start_epoch>=5:counts[model]+=int(allowed)
                values[model].append(estimate);thresholds[model].append(threshold)
        result['noise_statistics'][str(ch)]={'unique_measurements':len(points),'exact_confirmation_steps':sum(m['exact'] for m in points),
            'source_sequence_span':points[-1]['seq']-points[0]['seq']+1,
            'observed_source_fraction':len(points)/(points[-1]['seq']-points[0]['seq']+1),
            'noise_ref':quantiles([m['noise'] for m in points]),'live_mean':quantiles([m['mean'] for m in points]),
            'noise_over_mean':quantiles([m['noise']/m['mean'] for m in points if m['mean']>0]),
            'instant_snr':quantiles([m['level']/m['noise'] for m in points if m['noise']>0]),
            'lag1_noise_correlation':float(np.corrcoef([m['noise'] for m in points[:-1]],[m['noise'] for m in points[1:]])[0,1]),
            'effective_k':quantiles([m['k'] for m in points]),'position_ok_fraction':float(np.mean([m['position_ok'] for m in points])),
            'p95_5s_to_live_mean':quantiles([m['estimates']['mean_plus_p95_5s']/m['mean'] for m in points if m['mean']>0]),
            'width_ok_fraction':float(np.mean([m['width_ok'] for m in points]))}
        result['alternatives'][str(ch)]={model:{'sampled_amplitude_passes_after_5s':counts[model],
            'estimate':quantiles(values[model]),'threshold':quantiles(thresholds[model])} for model in models}
        plot_data[str(ch)]={'t':[m['t']-start_epoch for m in points],'noise':[m['noise'] for m in points],
                           'level':[m['level'] for m in points],'mean':[m['mean'] for m in points],
                           'k':[m['k'] for m in points],
                           'model_estimates':values,'model_thresholds':thresholds}
    for event,detail in zip(events,result['events']):
        steps=[s for s in event['frame'].get('confirmation_steps',[]) if isinstance(s,dict)]
        ch=event['channel']
        for model in models:
            detail['models_break_saved_chain'][model]=any(not exact_steps[(ch,s['source_seq'])]['pass'][model] for s in steps)
    result['saved_chain_comparison']={model:{'broken':sum(e['models_break_saved_chain'][model] for e in result['events']),
        'total':len(result['events'])} for model in models}
    result['limitations']=[
        'No-tag condition is supplied by the user; there is no independent physical tag witness.',
        'API snapshots do not include every detector decision; noise estimator comparisons use sampled history.',
        'Saved event confirmation chains are exact, but their preceding alternative-estimator noise history is sampled.',
        'Breaking an already saved confirmation chain does not prove that the whole interference episode would not fire later.',
        'Alternative comparisons keep each recorded coefficient constant; existing low/high interpolation would need a separate calibrated replay.',
        'No positive tag passes were recorded; loss of tag sensitivity cannot be measured.']
    (folder/'analysis.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    (folder/'plot_data.json').write_text(json.dumps(plot_data),encoding='utf8')
    return result


def plot(folder):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    r=json.loads((folder/'analysis.json').read_text(encoding='utf8'))
    data=json.loads((folder/'plot_data.json').read_text(encoding='utf8'))
    fig,axes=plt.subplots(3,1,figsize=(14,10),gridspec_kw={'height_ratios':[1,1,1.1]})
    for ch,ax in enumerate(axes[:2]):
        d=data[str(ch)];t=np.array(d['t'])/60
        ax.plot(t,np.array(d['noise'])/1000,color='#3896c8',lw=.7,alpha=.7,label='Шум текущего пакета')
        ax.plot(t,np.array(d['mean'])/1000,color='#e89b16',lw=1.4,label='Действующее среднее, 5 с')
        ax.plot(t,np.array(d['model_estimates']['mean_plus_p95_5s'])/1000,color='#8c50ba',lw=1.2,label='Кандидат: max(среднее, P95 за 5 с)')
        for event in r['events']:
            if event['channel']==ch:ax.axvline(event['elapsed_s']/60,color='#c32932',alpha=.6,lw=.7)
        ax.set_title(('Верхний' if ch==0 else 'Нижний')+' канал: красные линии — подтверждённые события без меток',loc='left',fontsize=11)
        ax.set_ylabel('Шум, тыс. ед.');ax.grid(alpha=.2);ax.set_xlim(0,r['duration_s']/60)
    axes[0].legend(loc='upper right',fontsize=9,ncol=1)
    axes[1].set_xlabel('Время наблюдения, мин')
    ax=axes[2]
    event=next((e for e in r['events'] if e['models_break_saved_chain']['mean_plus_p95_5s']),r['events'][0] if r['events'] else None)
    if event:
        ch=event['channel'];d=data[str(ch)];t=np.array(d['t']);mask=(t>=event['elapsed_s']-2)&(t<=event['elapsed_s']+1)
        x=t[mask]-event['elapsed_s'];k=np.array(d['k'])
        old=np.maximum(np.array(d['model_thresholds']['live_mean_5s']),np.array(d['noise'])*k)
        new=np.maximum(np.array(d['model_thresholds']['mean_plus_p95_5s']),np.array(d['noise'])*k)
        ax.plot(x,np.array(d['level'])[mask]/1000,color='#333b42',lw=1.3,label='Пиковый сигнал')
        ax.plot(x,old[mask]/1000,color='#e89b16',lw=1.2,label='Сейчас: среднее + мгновенная проверка S/N')
        ax.plot(x,new[mask]/1000,color='#8c50ba',lw=1.2,label='Кандидат P95 + та же мгновенная проверка')
        ax.axvline(0,color='#c32932',ls='--',lw=1);ax.set_xlabel('Время относительно зарегистрированного события, с')
        ax.set_title('Пример события; сравнение оценок офлайн по выборке API',loc='left',fontsize=11)
        ax.set_ylabel('Уровень, тыс. ед.');ax.legend(loc='upper left',fontsize=8);ax.grid(alpha=.2)
    fig.suptitle('M02: 10 минут без меток — измерение шума и временная оценка',fontsize=15,fontweight='bold')
    fig.text(.01,.01,'Настройки устройства не менялись. Более высокий порог может ухудшить обнаружение настоящих меток.',fontsize=9)
    fig.tight_layout(rect=(0,.025,1,.96));fig.savefig(folder/'noise_comparison.png',dpi=160);plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('folder',type=Path);args=parser.parse_args()
    r=summarize(args.folder)
    if r['complete']:plot(args.folder)
    print(json.dumps({key:r[key] for key in ['complete','duration_s','samples','fire_delta','stream_delta','worker_delta','saved_chain_comparison','noise_statistics']},indent=2))
