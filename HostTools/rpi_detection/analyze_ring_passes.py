"""Correlate human T33 pass marks with sampled detector evidence.

Snapshots are not every detector frame. Human labels remain independent of
fire counters and sound snapshots; pending/invalid marks are reported.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
import math
from pathlib import Path


def labelled_rate(hits, misses):
    """Human-reported rate with a two-sided 95% Wilson interval."""
    n = hits + misses
    if not n:
        return {'hits': hits, 'misses': misses, 'labelled_passes': 0,
                'rate': None, 'wilson_95': None}
    p, z = hits / n, 1.959963984540054
    scale = 1 + z*z/n
    center = (p + z*z/(2*n)) / scale
    radius = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / scale
    return {'hits': hits, 'misses': misses, 'labelled_passes': n, 'rate': p,
            'wilson_95': [max(0.0, center-radius), min(1.0, center+radius)]}


def normalize_records(records, channel=None, tag=None, distance_cm=None):
    """Adapt passive-wire observations without inventing antenna or test labels."""
    records = [dict(row) for row in records]
    starts = [r for r in records if r.get('kind') == 'start']
    ready = [r for r in records if r.get('kind') == 'ready']
    raw = bool(ready)
    if len(starts) + len(ready) != 1:
        raise ValueError('Expected exactly one recording start')
    start = dict((starts or ready)[0])
    if channel is not None and start.get('channel') not in (None, channel):
        raise ValueError('Explicit channel conflicts with recorded channel')
    channel = channel or start.get('channel')
    if channel not in ('upper', 'lower'):
        raise ValueError('An explicit upper/lower channel is required for a both-channel raw recording')
    start['channel'] = channel
    for key, value in (('tag', tag), ('distance_cm', distance_cm)):
        if value is not None and start.get(key) not in (None, value):
            raise ValueError('Explicit ' + key + ' conflicts with recorded label')
        if value is not None:
            start[key] = value
    if distance_cm is not None and (not math.isfinite(distance_cm) or distance_cm <= 0):
        raise ValueError('distance_cm must be positive and finite')
    normalized = []
    for row in records:
        if 'observed_mono' not in row and 'monotonic' in row:
            row['observed_mono'] = row['monotonic']
        if 'epoch' not in row and 'at' in row:
            row['epoch'] = row['at']
        if raw and row.get('kind') in ('ready', 'status', 'end') and isinstance(row.get('status'), dict):
            normalized.append({**row, 'kind': 'sample'})
        if raw and row.get('kind') == 'ready':
            normalized.append({**row, **start, 'kind': 'start'})
        else:
            normalized.append(row)
    return normalized, start, raw


def eligible_passes(records):
    """Only unique, explicitly started, well-formed nonoverlapping marks qualify."""
    starts = [r for r in records if r.get('kind') == 'pass_start']
    ends = [r for r in records if r.get('kind') == 'pass_end']
    def mark_key(row):
        pid = row.get('pass_id')
        return type(pid).__name__, repr(pid)

    start_ids = Counter(mark_key(r) for r in starts)
    end_ids = Counter(mark_key(r) for r in ends)
    valid, invalid = [], []
    for mark in ends:
        pid = mark.get('pass_id')
        reasons = []
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            reasons.append('invalid_pass_id')
        if start_ids[mark_key(mark)] != 1:
            reasons.append('missing_or_reused_start')
        if end_ids[mark_key(mark)] != 1:
            reasons.append('reused_end')
        if mark.get('human_result') not in ('hit', 'miss', 'unlabelled'):
            reasons.append('invalid_human_result')
        times = [mark.get(key) for key in ('start_mono', 'end_mono', 'epoch', 'observed_mono')]
        if (not all(isinstance(t, (int, float)) and math.isfinite(t) for t in times) or
                times[1] <= times[0]):
            reasons.append('invalid_pass_times')
        matching = next((r for r in starts if mark_key(r) == mark_key(mark)), None)
        if matching is not None:
            if matching.get('start_mono') != mark.get('start_mono'):
                reasons.append('start_time_mismatch')
            if records.index(matching) > records.index(mark):
                reasons.append('end_before_start')
        if reasons:
            invalid.append({'pass_id': pid, 'reasons': reasons, 'mark': mark})
        else:
            valid.append(mark)
    overlapping = set()
    ordered = sorted(valid, key=lambda r: r['start_mono'])
    for index, mark in enumerate(ordered):
        for following in ordered[index+1:]:
            if following['start_mono'] >= mark['end_mono']:
                break
            overlapping.update((mark['pass_id'], following['pass_id']))
    for mark in valid:
        if mark['pass_id'] in overlapping:
            invalid.append({'pass_id': mark['pass_id'], 'reasons': ['overlapping_pass_windows'], 'mark': mark})
    pending = [r.get('pass_id') for r in starts if not end_ids[mark_key(r)]]
    return [r for r in valid if r['pass_id'] not in overlapping], invalid, pending


def analyze(records, channel=None, tag=None, distance_cm=None):
    records, start, raw = normalize_records(records, channel, tag, distance_cm)
    channel = start['channel']
    ch = 0 if channel == 'upper' else 1
    samples = sorted((r for r in records if r.get('kind') == 'sample'), key=lambda r: r['observed_mono'])
    ends = [r for r in records if r.get('kind') == 'pass_end']
    eligible, invalid, pending = eligible_passes(records)
    events = {}
    for sample in samples:
        detector = sample['status'].get('detector', {})
        for number in (0, 1):
            snapshot = detector.get(f'fire_snapshot{number}') or {}
            if snapshot.get('seq') is not None and isinstance(snapshot.get('timestamp'), (int, float)):
                events[(number, snapshot['seq'])] = {'channel': number, **snapshot}
    recording_ends = [r for r in records if r.get('kind') == 'end']
    errors = [r for r in records if r.get('kind') == 'fatal' or r.get('kind', '').endswith('_error')]
    end = recording_ends[0] if len(recording_ends) == 1 else {}
    ignored = [r for r in records if r.get('kind') in ('ignored_mark', 'orphan_pass_end')]
    if raw:
        summary = end.get('summary', {})
        errors.extend({'kind': 'raw_capture_error', 'error': error} for error in summary.get('errors', []))
        capture_complete = summary.get('capture_complete') is True and not errors
        ground_truth_complete = summary.get('ground_truth_complete') is True
    else:
        capture_complete = bool(end and not errors and end.get('observer_complete', True))
        ground_truth_complete = bool(eligible)
    ground_truth_complete = bool(ground_truth_complete and eligible and not invalid and not pending and not ignored and
                                 not any(r['human_result'] == 'unlabelled' for r in eligible))
    result = {'tag': start.get('tag'), 'distance_cm': start.get('distance_cm'), 'channel': channel,
              'source_format': 'passive_raw_wire' if raw else 'api_observation',
              'capture_complete': capture_complete, 'ground_truth_complete': ground_truth_complete,
              'recording_complete': bool(capture_complete and (ground_truth_complete if raw else True)),
              'human_counts': dict(Counter(r.get('human_result', 'unknown') for r in ends)),
              'eligible_human_counts': dict(Counter(r['human_result'] for r in eligible)),
              'excluded_human_counts': dict(Counter(r['mark'].get('human_result', 'unknown') for r in invalid)),
              'invalid_marks': invalid, 'ignored_marks': ignored,
              'errors': errors,
              'pending_pass_ids': pending,
              'passes': [],
              'limitations': 'Human outcomes are not inferred from fire_seq. Sampled gate counts are not frame counts or proven miss causes. Fire snapshots may omit intervening events.'}
    for mark in eligible:
        lo, hi = mark['start_mono'], mark['end_mono']
        offset = mark['epoch'] - mark['observed_mono']
        chosen = [r for r in samples if lo <= r['observed_mono'] <= hi]
        detectors = [r['status'].get('detector', {}) for r in chosen]
        healthy = [r for r in chosen if r['status'].get('ok') and not r['status'].get('stale')
                   and r['status'].get('connection', {}).get('connected')
                   and r['status'].get('detector', {}).get('active')]
        strong = [d for d in detectors if d.get(f'peak_position_ok{ch}') is True
                  and isinstance(d.get(f'lvl{ch}'), (float, int))
                  and isinstance(d.get(f'thr{ch}'), (float, int))
                  and d[f'lvl{ch}'] >= d[f'thr{ch}']]
        fires = [e for e in events.values() if lo + offset <= e['timestamp'] < hi + offset]
        counters = [d.get(f'fire_seq{ch}') for d in detectors]
        counter_delta = None
        if (len(counters) >= 2 and len(healthy) == len(chosen) and
                all(isinstance(value, int) for value in counters) and
                all(b >= a for a, b in zip(counters, counters[1:]))):
            counter_delta = counters[-1] - counters[0]
        top = sorted(detectors, key=lambda d: d.get(f'lvl{ch}') or 0, reverse=True)[:3]
        row = {**{k: mark[k] for k in ('pass_id', 'human_result', 'start_mono', 'end_mono')},
               'duration_s': hi-lo, 'sample_count': len(chosen), 'healthy_samples': len(healthy),
               'selected_channel_fire_snapshots': [e for e in fires if e['channel'] == ch],
               'other_channel_fire_snapshots': [e for e in fires if e['channel'] != ch],
               'selected_counter_delta_in_sampled_interval': counter_delta,
               'selected_channel_event_observed': bool(any(e['channel'] == ch for e in fires) or
                                                       counter_delta is not None and counter_delta > 0),
               'sound_active_samples': sum(r['status'].get('sound', {}).get('output_active') is True for r in chosen),
               'optic_blocked_samples': sum(r['status'].get('optic', {}).get('reaction_gate_allowed') is False for r in chosen),
               'candidate_samples': sum(d.get(f'candidate{ch}') is True for d in detectors),
               'max_confirmation_seen': max((d.get(f'confirm_seen{ch}') or 0 for d in detectors), default=0),
               'confirmation_reasons': dict(Counter(d.get(f'confirm_step_reason{ch}') for d in detectors)),
               'above_threshold_and_peak_position_ok_samples': len(strong),
               'strong_width_rejected_samples': sum((d.get(f'base_width{ch}') or {}).get('ok') is False for d in strong),
               'strong_common_mode_blocked_samples': sum(d.get('common_mode', {}).get(channel, {}).get('blocked') is True for d in strong),
               'strong_peak_positions': dict(Counter(d.get(f'peak_index{ch}') for d in strong)),
               'top_signal_samples': [{k: d.get(k) for k in (f'lvl{ch}', f'thr{ch}', f'peak_index{ch}',
                   f'peak_position_ok{ch}', f'base_width{ch}', f'candidate{ch}', f'confirm_seen{ch}',
                   f'confirm_step_reason{ch}', 'common_mode')} for d in top]}
        result['passes'].append(row)
    counts = result['eligible_human_counts']
    result['human_reported_detection'] = labelled_rate(counts.get('hit', 0), counts.get('miss', 0))
    result['human_event_evidence'] = dict(Counter(
        (row['human_result'] + ('_with_event_evidence' if row['selected_channel_event_observed']
                                else '_without_event_evidence')) for row in result['passes']))
    result['event_evidence_note'] = ('Counters cover only the sampled interval inside each marked pass. '
        'A missing snapshot/counter increase is unknown evidence, not a proven software miss. '
        'Human rate excludes unlabelled/pending/orphan marks and is not independently verified ground truth.')
    return result


def read_complete_lines(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if not line.endswith('\n'):
                return  # Recording may still be writing its final line.
            if line.strip():
                yield json.loads(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--channel', choices=('upper', 'lower'),
                        help='Required for passive raw recordings that contain both antennas')
    parser.add_argument('--tag', help='Explicit test label when absent from the recording')
    parser.add_argument('--distance-cm', type=float, help='Explicit distance when absent from the recording')
    args = parser.parse_args()
    result = analyze(read_complete_lines(args.recording), args.channel, args.tag, args.distance_cm)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'passes'}, ensure_ascii=False))
    for row in result['passes']:
        print(json.dumps({k:row[k] for k in ('pass_id','human_result','duration_s','sample_count',
                         'sound_active_samples','optic_blocked_samples','candidate_samples','max_confirmation_seen',
                         'above_threshold_and_peak_position_ok_samples','strong_width_rejected_samples',
                         'strong_common_mode_blocked_samples')} |
                         {'selected_fires':len(row['selected_channel_fire_snapshots']),
                          'other_fires':len(row['other_channel_fire_snapshots'])}))


if __name__ == '__main__':
    main()
