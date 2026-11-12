"""Extract M02 no-tag event evidence and short contexts from verified archive bytes.

Can follow an ongoing local import. Full archives remain untouched. Event counts
come from recorder counters; sampled fire snapshots supply waveform metadata.
"""
import argparse
import collections
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import time

JOB='night-20261019-b51-110cm-01'
EPOCH=re.compile(rb'"epoch":([0-9.eE+\-]+)')
SEQ=re.compile(rb'"fire_seq0":(\d+)')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def write(path,data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)


def extract(path,output,relative):
    recent=collections.deque()
    active_until=0;last_written=0;seen_seq=set();events=[]
    last_seq=None;initial_seq=None;start=None;end=None;observations=0;sampled_sequences_skipped=0
    with gzip.open(output/'event-contexts.jsonl.gz','wb',compresslevel=1) as snippets:
        with gzip.open(path,'rb') as source:
            for line in source:
                if not line.endswith(b'\n'):
                    raise ValueError('Incomplete JSONL line in a closed archive')
                match=EPOCH.search(line[:300]);epoch=float(match.group(1)) if match else None
                if epoch is None:
                    raise ValueError('Missing observer timestamp')
                while recent and recent[0][0]<epoch-2:
                    recent.popleft()
                recent.append((epoch,line))
                if b'"kind":"start"' in line[:100]:
                    start=json.loads(line)
                    last_seq=initial_seq=start['initial']['status']['detector']['fire_seq0']
                    snippets.write(line);last_written=epoch
                elif b'"kind":"sample"' in line[:100]:
                    seq_match=SEQ.search(line)
                    seq=int(seq_match.group(1)) if seq_match else None
                    if seq is not None and last_seq is not None and seq!=last_seq:
                        sampled_sequences_skipped+=max(0,seq-last_seq-1)
                        last_seq=seq
                        row=json.loads(line);d=row['status']['detector'];event=d.get('fire_snapshot0') or {}
                        event_seq=event.get('seq')
                        if event_seq is not None and event_seq>initial_seq and event_seq not in seen_seq:
                            seen_seq.add(event_seq)
                            event={**event,'source_archive':relative,'observed_epoch':epoch,
                                'profile_confirm0':start['initial']['status']['detector']['channels']['upper']['confirm_count'],
                                'sound_at_observation':row['status'].get('sound'),
                                'optic_at_observation':row['status'].get('optic')}
                            events.append(event)
                            event_time=event.get('timestamp',epoch)
                            active_until=max(active_until,event_time+.75)
                            snippets.write((json.dumps({'kind':'false_event_context','event':event,
                                'requested_context_start':event_time-.75,'requested_context_end':event_time+.75},
                                ensure_ascii=True,separators=(',',':'))+'\n').encode())
                            for t,raw in recent:
                                if t>=event_time-.75 and t>last_written:
                                    snippets.write(raw);last_written=t;observations+=1
                elif b'"kind":"end"' in line[:100]:
                    end=json.loads(line)
                if epoch<=active_until and epoch>last_written:
                    snippets.write(line);last_written=epoch;observations+=1
        assert start and end,'Archive has no normal end record'
        assert end.get('observer_complete') and not end.get('queue_drops'),end
        snippets.write((json.dumps({'kind':'context_extract_end','source_archive':relative,
            'event_snapshots':len(events),'context_observations':observations},separators=(',',':'))+'\n').encode())
    result={'archive':relative,'profile_confirm0':start['initial']['status']['detector']['channels']['upper']['confirm_count'],
        'event_snapshots':len(events),'sampled_sequences_skipped':sampled_sequences_skipped,
        'initial_upper_fire_seq':initial_seq,'final_upper_fire_seq':end['final']['status']['detector']['fire_seq0'],
        'source_recording_complete':True,'context_observations':observations,
        'source_started_epoch':start['epoch'],'source_ended_epoch':end['epoch'],
        'events':events,'context_archive_bytes':(output/'event-contexts.jsonl.gz').stat().st_size}
    write(output/'events.json',result)
    return result


def aggregate(output,entries,expected):
    events=[];seen=set();per_block=[]
    for entry in entries:
        p=output/'blocks'/Path(entry['relative']).parent.name/'events.json'
        if not p.exists():continue
        block=json.loads(p.read_text(encoding='utf-8'));per_block.append({k:v for k,v in block.items() if k!='events'})
        for e in block['events']:
            if e['seq'] not in seen:seen.add(e['seq']);events.append(e)
    profiles=[]
    for count in [3,4,6]:
        group=[e for e in events if e['profile_confirm0']==count]
        peaks=[e.get('peak_index') for e in group]
        profiles.append({'confirm0':count,'extracted_event_snapshots':len(group),
            'peak_positions':dict(collections.Counter(peaks).most_common()),
            'events_peak_86_to_104':sum(isinstance(v,int) and 86<=v<=104 for v in peaks),
            'events_peak_80_to_115':sum(isinstance(v,int) and 80<=v<=115 for v in peaks),
            'width_counts':dict(collections.Counter((e.get('base_width') or {}).get('width') for e in group)),
            'phase_span_counts':dict(collections.Counter(e.get('confirm_phase_span') for e in group))})
    result={'processed_blocks':len(per_block),'expected_blocks':expected,'complete':len(per_block)==expected,
        'unique_upper_event_snapshots':len(events),'profiles':profiles,'blocks':per_block,
        'context_archives_bytes':sum(b['context_archive_bytes'] for b in per_block),
        'limitations':['Counter events are software detections, not distinct acoustic alarm episodes.',
            'Tag-associated positive events require separate marked passage recordings.',
            'Waveform snapshots are processed API observations, not exact raw ADC replay.']}
    write(output/'event-summary.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--import-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--follow',action='store_true')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((args.import_root/'source-manifest.json').read_text(encoding='utf-8'))
    prefix='tag_statistics/'+JOB+'/blocks/'
    entries=[r for r in manifest['files'] if r['relative'].startswith(prefix) and r['relative'].endswith('/capture.jsonl.gz')]
    assert len(entries)==31,len(entries)
    done=set()
    for entry in entries:
        output=args.output/'blocks'/Path(entry['relative']).parent.name
        if (output/'events.json').exists():done.add(entry['relative'])
    while len(done)<len(entries):
        advanced=False
        for entry in entries:
            if entry['relative'] in done:continue
            final=args.import_root.joinpath(*entry['relative'].split('/'))
            candidate=final if final.exists() else final.with_name(final.name+'.part')
            if not candidate.exists() or candidate.stat().st_size!=entry['size']:continue
            try:
                if digest(candidate)!=entry['sha256']:continue
                output=args.output/'blocks'/final.parent.name;output.mkdir(parents=True,exist_ok=True)
                block=extract(candidate,output,entry['relative'])
            except (FileNotFoundError,PermissionError):
                # SFTP may still hold the final-sized partial file open, or the
                # importer may be renaming it after verification. Retry later.
                continue
            done.add(entry['relative']);advanced=True
            result=aggregate(args.output,entries,len(entries))
            print(json.dumps({'processed_blocks':len(done),'total_blocks':len(entries),
                'upper_event_snapshots':result['unique_upper_event_snapshots'],
                'context_archive_bytes':result['context_archives_bytes']}),flush=True)
        if len(done)==len(entries) or not args.follow:break
        if not advanced:time.sleep(5)
    result=aggregate(args.output,entries,len(entries))
    print(json.dumps({'complete':result['complete'],'processed_blocks':result['processed_blocks'],
        'unique_upper_event_snapshots':result['unique_upper_event_snapshots'],'profiles':result['profiles'],
        'context_archives_bytes':result['context_archives_bytes']}),flush=True)


if __name__=='__main__':main()
