"""Compare ring-marked passage neighborhoods with a confirmed no-tag control.

Works on compressed API observations, including a growing archive. This is
feature screening, not raw replay or a validated detection-rate measurement.
"""
import argparse
import base64
from bisect import bisect_left, bisect_right
from collections import Counter
import gzip
import json
from pathlib import Path
import statistics
import struct

from remote_pass_logger import detector_profile


def quantiles(values):
    values = sorted(x for x in values if isinstance(x, (float, int)))
    if not values:
        return None
    def at(q):
        index = q * (len(values)-1)
        lo = int(index)
        hi = min(len(values)-1, lo+1)
        return values[lo] + (values[hi]-values[lo]) * (index-lo)
    return dict(count=len(values), min=values[0], p10=at(.1), median=at(.5),
                p90=at(.9), p95=at(.95), p99=at(.99), max=values[-1])


def read_session(path):
    result = dict(name=path.name, start=None, end=None, marks=[], samples=[], frames=[],
                  errors=[], counts=Counter(), last_mono=None, archive_complete=False)
    try:
        with gzip.open(path/'capture.jsonl.gz', 'rt', encoding='utf-8') as stream:
            for line in stream:
                if not line.endswith('\n'):
                    break
                row = json.loads(line)
                kind = row['kind']
                result['counts'][kind] += 1
                result['last_mono'] = row['observed_mono']
                if kind == 'start':
                    result['start'] = row
                elif kind == 'end':
                    result['end'] = row
                elif kind == 'pass_start':
                    result['marks'].append(row)
                elif kind.endswith('_error') or kind == 'fatal':
                    result['errors'].append(row)
                elif kind == 'sample':
                    status = row['status']
                    d = status.get('detector', {})
                    c = d.get('common_mode', {}).get('upper', {})
                    width = d.get('base_width0') or {}
                    healthy = bool(status.get('ok') and not status.get('stale') and not status.get('busy')
                                   and status.get('connection', {}).get('connected') and d.get('active'))
                    threshold = d.get('thr0') or 0
                    result['samples'].append(dict(
                        mono=row['observed_mono'], epoch=row['epoch'], healthy=healthy,
                        level=d.get('lvl0'), threshold=threshold,
                        ratio=d.get('lvl0', 0)/threshold if threshold > 0 else None,
                        peak=d.get('peak_index0'), position_ok=d.get('peak_position_ok0'),
                        width=width.get('width'), width_valid=width.get('valid'), width_ok=width.get('ok'),
                        candidate=d.get('candidate0'), confirm=d.get('confirm_seen0'),
                        confirm_reason=d.get('confirm_step_reason0'), common_blocked=c.get('blocked'),
                        common_reason=c.get('reason'), common=c,
                        fire_seq=d.get('fire_seq0'), fire_snapshot=d.get('fire_snapshot0'),
                        lower_fire_seq=d.get('fire_seq1'), noise=d.get('noise_avg0'),
                        stats=status.get('stream_stats'), worker=d.get('worker'),
                        channels=d.get('channels'), sound=status.get('sound')))
                elif kind == 'waveform':
                    raw = base64.b64decode(row['data'], validate=True)
                    size = struct.unpack_from('<I', raw, 4)[0]
                    header = json.loads(raw[8:8+size])
                    assert header['step'] == 1 and header['count'] == header['base_buf_len']
                    n = header['count']
                    assert len(raw) == ((8+size+1)&~1) + 2*n*len(header['arrays'])
                    peak = header.get('prod_peak0') or {}
                    width = peak.get('base_width') or {}
                    source_epoch = header.get('last_frame_t')
                    source_mono = row['observed_mono']-(row['epoch']-source_epoch)
                    result['frames'].append(dict(mono=source_mono, epoch=source_epoch,
                        peak=peak.get('sample'), level=peak.get('raw_amplitude'), width=width.get('width'),
                        width_valid=width.get('valid'), width_ok=width.get('ok'),
                        lower_peak=(header.get('prod_peak1') or {}).get('sample'),
                        lower_level=(header.get('prod_peak1') or {}).get('raw_amplitude')))
        result['archive_complete'] = True
    except EOFError:
        pass  # A live gzip file is expected to have no final trailer yet.
    assert result['start'] and result['samples'] and result['frames'], path
    return result


def select(rows, lo, hi):
    times = [r['mono'] for r in rows]
    return rows[bisect_left(times, lo):bisect_right(times, hi)]


def features(samples, frames):
    samples = [r for r in samples if r['healthy']]
    events = {}
    if samples:
        lo, hi = samples[0]['epoch'], samples[-1]['epoch']
        for row in samples:
            event = row.get('fire_snapshot') or {}
            if isinstance(event.get('timestamp'), (int, float)) and lo <= event['timestamp'] <= hi:
                events[event.get('seq')] = event
    strong = [r for r in samples if r['ratio'] is not None and r['ratio'] >= 1 and r['position_ok']]
    return dict(samples=len(samples), frames=len(frames),
        ratio=quantiles(r['ratio'] for r in samples), level=quantiles(r['level'] for r in samples),
        waveform_peak=quantiles(r['level'] for r in frames),
        waveform_width=quantiles(r['width'] for r in frames if r['width_valid']),
        waveform_peak_positions=dict(Counter(r['peak'] for r in frames).most_common(12)),
        candidate_samples=sum(bool(r['candidate']) for r in samples),
        max_confirmation=max((r['confirm'] or 0 for r in samples), default=0),
        above_threshold_and_position_ok_samples=len(strong),
        strong_width_rejected=sum(r['width_ok'] is False for r in strong),
        strong_common_mode_blocked=sum(r['common_blocked'] is True for r in strong),
        common_mode_reasons=dict(Counter(r['common_reason'] for r in samples)),
        fire_snapshots=list(events.values()))


def passage_report(session, horizon):
    report = dict(session=session['name'], archive_complete=session['archive_complete'],
                  recording_ended=session['end'] is not None, counts=dict(session['counts']),
                  errors=session['errors'], profile=profile(session), passes=[])
    events = {r['fire_snapshot']['seq']:r['fire_snapshot'] for r in session['samples']
              if isinstance(r.get('fire_snapshot'), dict) and r['fire_snapshot'].get('seq') is not None}
    for index, mark in enumerate(session['marks']):
        lo = mark['start_mono']
        next_start = session['marks'][index+1]['start_mono'] if index+1 < len(session['marks']) else session['last_mono']
        hi = min(lo+horizon, next_start, session['last_mono'])
        samples = select(session['samples'], lo, hi)
        frames = select(session['frames'], lo, hi)
        measured = features(samples, frames)
        offset = mark['epoch']-mark['observed_mono']
        measured['fire_snapshots'] = [event for event in events.values()
            if isinstance(event.get('timestamp'), (int, float)) and lo+offset <= event['timestamp'] < hi+offset]
        strongest = max((r for r in samples if r['healthy']), key=lambda r: r['ratio'] or 0, default=None)
        report['passes'].append(dict(pass_id=mark['pass_id'], start_mono=lo,
            observed_s=hi-lo, window_complete=hi-lo >= horizon-.05, **measured,
            strongest_sample=strongest))
    return report


def profile(session):
    status = session['start']['initial']['status']
    detector = status['detector']
    upper = detector['channels']['upper']
    settings = detector['settings']
    return dict(stm32_average=status['mode']['avg_n'], raspberry_average=status['mode']['rpi_avg_n'],
        upper_confirm_count=upper['confirm_count'], upper_threshold=upper['threshold'],
        upper_threshold_high=upper['threshold_high'], peak_index_min=settings['peak_index_min'],
        peak_index_max=settings['peak_index_max'], base_width_max=settings['base_width_max'],
        common_mode_enabled=settings['common_mode_enabled'], impulse_guard_enabled=settings['impulse_guard_enabled'])


def comparison_profile(session):
    return detector_profile(session['start']['initial']['status'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--passages', nargs='+', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--horizon', type=float, default=4)
    args = parser.parse_args()
    positives = [read_session(p) for p in args.passages]
    control = read_session(args.control)
    conditions = json.loads((args.control/'test-conditions.json').read_text(encoding='utf-8'))
    assert conditions['tag_present'] is False and conditions['tag_removed_confirmed_by_user'] is True
    start_mono = control['start']['observed_mono']
    lo = start_mono+conditions['warmup_exclude_s']
    hi = min(start_mono+conditions['warmup_exclude_s']+conditions['evaluation_duration_s'], control['last_mono'])
    negative_samples = select(control['samples'], lo, hi)
    negative_frames = select(control['frames'], lo, hi)
    reports = [passage_report(s, args.horizon) for s in positives]
    all_positive_samples, all_positive_frames = [], []
    for session in positives:
        for index, mark in enumerate(session['marks']):
            a = mark['start_mono']
            b = min(a+args.horizon, session['marks'][index+1]['start_mono'] if index+1 < len(session['marks']) else session['last_mono'])
            all_positive_samples.extend(select(session['samples'], a, b))
            all_positive_frames.extend(select(session['frames'], a, b))
    control_report = dict(session=control['name'], archive_complete=control['archive_complete'],
        recording_ended=control['end'] is not None, errors=control['errors'], duration_s=hi-lo,
        evaluated_start_mono=lo, evaluated_end_mono=hi, profile=profile(control),
        **features(negative_samples, negative_frames))
    if negative_samples:
        a, b = negative_samples[0], negative_samples[-1]
        keys = ('seq_gap_ch0', 'seq_gap_ch1', 'crc_bad', 'magic_bad', 'host_rx_ack_fail')
        control_report['transport_deltas'] = {k:b['stats'][k]-a['stats'][k] for k in keys}
        control_report['upper_fire_seq_delta'] = b['fire_seq']-a['fire_seq']
        control_report['lower_fire_seq_delta'] = b['lower_fire_seq']-a['lower_fire_seq']
        control_report['worker_overflow_delta'] = b['worker']['overflows']-a['worker']['overflows']
        control_report['long_latency_delta'] = b['worker']['latency']['over_100ms']-a['worker']['latency']['over_100ms']
    # Changing a threshold affects other dynamic gates. This grid only screens
    # whether observed amplitude and width alone can separate the two classes.
    screening = []
    for scale in (.25, .375, .5, .625, .75, .875, 1, 1.25, 1.5, 2):
        for max_width in (12, 15, 18, 23, 30, 40, 200):
            def qualifies(row):
                return (row['healthy'] and row['ratio'] is not None and row['ratio'] >= scale
                        and row['position_ok'] and row['width_valid'] and row['width'] <= max_width)
            passage_counts = []
            for session in positives:
                for index, mark in enumerate(session['marks']):
                    a = mark['start_mono']
                    b = min(a+args.horizon, session['marks'][index+1]['start_mono'] if index+1<len(session['marks']) else session['last_mono'])
                    if b-a < args.horizon-.05:
                        continue
                    passage_counts.append(sum(qualifies(r) for r in select(session['samples'], a, b)))
            screening.append(dict(relative_threshold=scale, max_width=max_width,
                marked_windows_with_matching_sample=sum(n>0 for n in passage_counts),
                full_marked_windows=len(passage_counts),
                matching_no_tag_samples=sum(qualifies(r) for r in negative_samples),
                note='Samples, not confirmed detector decisions. No common-mode or confirmation replay.'))
    result = dict(horizon_s=args.horizon, passages=reports,
        marked_neighborhood_features=features(all_positive_samples, all_positive_frames),
        no_tag_control=control_report, screening=screening,
        settings_match=all(comparison_profile(s) == comparison_profile(control) for s in positives),
        mode_match=all(comparison_profile(s)['mode'] == comparison_profile(control)['mode'] for s in positives),
        limitations=[
            'Ring marks establish approximate passage starts, not exact tag waveform labels or human hit/miss outcomes.',
            'Unmarked intervals in passage recordings are not labelled as no-tag data.',
            'The four-second neighborhood is an explicit analysis assumption; compare other horizons.',
            'API waveforms and detector snapshots have independent update times and are not exact raw replay.',
            'Feature screening cannot establish a reliable new detector profile without live validation.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    compact = {k:v for k,v in result.items() if k not in ('passages','screening')}
    compact['marked_neighborhood_features'] = {k:v for k,v in compact['marked_neighborhood_features'].items() if k != 'fire_snapshots'}
    compact['no_tag_control'] = {k:v for k,v in compact['no_tag_control'].items() if k != 'fire_snapshots'}
    print(json.dumps(compact,ensure_ascii=False))
    for report in reports:
        print(json.dumps({'session':report['session'],'marks':len(report['passes']),
            'windows_with_fire_snapshot':sum(bool(p['fire_snapshots']) for p in report['passes']),
            'windows_with_candidate':sum(p['candidate_samples']>0 for p in report['passes']),
            'max_confirmation_by_pass':[p['max_confirmation'] for p in report['passes']],
            'max_ratio_by_pass':[round(p['ratio']['max'],2) if p['ratio'] else None for p in report['passes']]},ensure_ascii=False))
    print(json.dumps({'screening_best':sorted(screening,key=lambda r:(r['matching_no_tag_samples']>0,-r['marked_windows_with_matching_sample'],r['matching_no_tag_samples']))[:12]},ensure_ascii=False))


if __name__ == '__main__':
    main()
