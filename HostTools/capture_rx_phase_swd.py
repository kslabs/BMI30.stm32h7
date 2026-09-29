#!/usr/bin/env python3
"""Capture retained raw ADC frames from a running, verified 1.2.52 image.

HotPlug only: no halt/reset/flash/ADC writes. Phase experiments write ONLY the
one-byte existing TX phase request; the normal marker IRQ applies it. Original
requests are saved before any write and restored in finally. USB settings and
TX enable are never changed. Raw dumps and every CLI command are retained.
"""
import argparse
import concurrent.futures
import hashlib
import json
import struct
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from monitor_rs485_swd import decode_snapshot

DEVICES = {'COM27': '0670FF535548877187163910',
           'COM28': '066DFF514953667287234159',
           'COM11': '066FFF565556857187224249',
           'COM32': '066EFF514953667287242707'}
CLI = r'C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe'
SHA = '2fc61eb5e51a97e60f1c7eacc2ee96e927e6e555c556d3731bbac1512c03e6b9'
META = 0x2404B160
META_SIZE = 320
STRIDE = 1360 * 2

def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')

def call(label, folder, name, operations):
    command = [CLI, '-c', 'port=SWD', 'sn=' + DEVICES[label], 'ap=0',
               'freq=4000', 'mode=HotPlug'] + operations
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True,
                            errors='replace', timeout=25,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    event = dict(timestamp=now(), device=label, serial=DEVICES[label],
                 command=command, elapsed=time.monotonic()-started,
                 returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    (folder / (name + '.command.json')).write_text(json.dumps(event, indent=2))
    if result.returncode:
        raise RuntimeError(f'{label} {name}: CubeCLI exit {result.returncode}')
    if 'Connect mode: Hot Plug' not in result.stdout:
        raise RuntimeError('HotPlug connection not confirmed')
    return event

def upload(address, size, path):
    return ['-u', hex(address), str(size), str(path)]

def state(label, folder, name, verify=False):
    folder.mkdir(parents=True, exist_ok=True)
    operations = []
    if verify:
        operations += upload(0x08000000, 315604, folder / (name + '_flash.bin'))
    for tag, address, size in [('meta', META, META_SIZE), ('cfg', 0x24000240, 128),
                                ('tx', 0x24000D60, 48), ('diag', 0x20001DC0, 128)]:
        operations += upload(address, size, folder / f'{name}_{tag}.bin')
    call(label, folder, name, operations)
    if verify:
        assert hashlib.sha256((folder / (name + '_flash.bin')).read_bytes()).hexdigest() == SHA, label
    meta = (folder / f'{name}_meta.bin').read_bytes()
    cfg = (folder / f'{name}_cfg.bin').read_bytes()
    tx = (folder / f'{name}_tx.bin').read_bytes()
    try:
        diag = decode_snapshot((folder / f'{name}_diag.bin').read_bytes())
    except ValueError:
        call(label, folder, name + '_diag_retry', upload(0x20001DC0, 128, folder / f'{name}_diag_retry.bin'))
        diag = decode_snapshot((folder / f'{name}_diag_retry.bin').read_bytes())
    result = dict(requested=meta[0x129], applied=meta[0x12A],
                  samples=struct.unpack_from('<H', cfg, 0x1A)[0],
                  adc_pointers=struct.unpack_from('<II', cfg, 0x1C),
                  streaming=tx[8], tx_requested=tx[9], tx_enabled=tx[10], diag=diag)
    assert result['samples'] == 600 and result['adc_pointers'] == (0x24020960, 0x24035D60), result
    assert result['requested'] == result['applied'] and result['requested'] <= 3, result
    assert result['streaming'] == 1, result
    (folder / (name + '.state.json')).write_text(json.dumps(result, indent=2))
    return result

def phase(label, value, folder, name):
    assert 0 <= value <= 3
    # This is the existing scalar request, not a GPIO or timer register.
    # CubeCLI 2.20 silently writes zero for a bare decimal token here.
    call(label, folder, name, ['-w8', '0x2404b289', hex(value), '-nv'])

def capture(label, folder, ordinal, expected):
    prefix = f'{ordinal:04d}'
    slot = ordinal & 1
    files = {tag: folder / f'{prefix}_{tag}.bin' for tag in ('gen0', 'meta0', 'a', 'b', 'meta1', 'gen1')}
    operations = []
    for tag, addr, size in [('gen0', 0x20000000, 4), ('meta0', META, META_SIZE),
                          ('a', 0x24020960+slot*STRIDE, 1200), ('b', 0x24035D60+slot*STRIDE, 1200),
                          ('meta1', META, META_SIZE), ('gen1', 0x20000000, 4)]:
        operations += upload(addr, size, files[tag])
    event = call(label, folder, prefix, operations)
    blobs = {tag: path.read_bytes() for tag, path in files.items()}
    m0, m1 = blobs['meta0'], blobs['meta1']
    counters = lambda m: (struct.unpack_from('<I', m)[0], *struct.unpack_from('<II', m, 0x60))
    before, after = counters(m0), counters(m1)
    accepted, rejected = [], []
    if blobs['gen0'] != blobs['gen1'] or min(after) < min(before):
        raise RuntimeError(f'{label}: ADC generation/reset changed during capture')
    if any(m[0x129] != expected or m[0x12A] != expected for m in (m0, m1)):
        raise RuntimeError(f'{label}: TX phase changed outside the experiment')
    # Reserve extra two frames for the non-atomic metadata read itself. Never
    # use the current/next DBM banks or any slot that could have been recycled.
    oldest_next = min(before)
    newest_next = max(after)
    for slot in (slot,):
        seq = oldest_next - 1 - ((oldest_next - 1 - slot) & 31)
        reason = None
        if not 3 <= oldest_next-seq <= 28 or newest_next-seq > 28:
            reason = 'outside_conservative_retained_window'
        elif m0[0xD0+slot] != m1[0xD0+slot]:
            reason = 'parity_metadata_changed'
        if reason:
            rejected.append(dict(slot=slot, seq=seq, reason=reason))
            continue
        parity = m0[0xD0+slot] & 1
        waves = [np.frombuffer(blobs[tag], dtype='<u2', count=600).copy()
                 for tag in ('a', 'b')]
        accepted.append(dict(slot=slot, seq=seq, parity=parity, waves=waves))
    evidence = dict(timestamp=event['timestamp'], counters_before=before, counters_after=after,
                    phase=expected, accepted=[{k:v for k,v in r.items() if k!='waves'} for r in accepted],
                    rejected=rejected)
    (folder / (prefix + '.capture.json')).write_text(json.dumps(evidence, indent=2))
    return accepted

def stage(label, stage_path, count, expected):
    folder = stage_path / label
    folder.mkdir(parents=True, exist_ok=True)
    initial = state(label, folder, 'before')
    assert initial['requested'] == expected
    records = []
    for ordinal in range(count):
        records.extend(capture(label, folder, ordinal, expected))
    final = state(label, folder, 'after')
    if not records:
        raise RuntimeError(f'{label}: no coherent frames')
    np.savez_compressed(folder / 'frames.npz',
                        wave=np.asarray([r['waves'] for r in records]),
                        seq=np.asarray([r['seq'] for r in records]),
                        parity=np.asarray([r['parity'] for r in records]))
    d0, d1 = initial['diag'], final['diag']
    seconds = (d1['uptime_ms']-d0['uptime_ms'])/1000
    summary = dict(frames=len(records), even=sum(r['parity']==0 for r in records),
                   odd=sum(r['parity']==1 for r in records), seconds=seconds,
                   adc_rate=(d1['adc_publish_count']-d0['adc_publish_count'])/seconds,
                   usb_rate=(d1['usb_tx_cplt']-d0['usb_tx_cplt'])/seconds,
                   deltas={k:d1[k]-d0[k] for k in ('adc_fault_count','adc_gap_over10ms','usb_recovery','phase_flip','phase_restart')})
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2))
    assert not any(summary['deltas'].values()), (label,summary)
    return summary

def parallel(fn):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {label:pool.submit(fn,label) for label in DEVICES}
        return {label:f.result() for label,f in futures.items()}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--captures', type=int, default=24)
    parser.add_argument('--experiment', choices=['baseline','global','individual'], default='baseline')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    initial = parallel(lambda label:state(label, args.output/label, 'initial', verify=True))
    (args.output/'original_states.json').write_text(json.dumps(initial, indent=2))
    masks = {label:s['requested'] for label,s in initial.items()}
    configurations = [('baseline', masks.copy())]
    if args.experiment == 'global':
        configurations += [('all_inverted',{label:value ^ 3 for label,value in masks.items()}),
                           ('restored',masks.copy())]
    elif args.experiment == 'individual':
        for label, value in masks.items():
            if initial[label]['tx_enabled']:
                for bit in (1,2):
                    configurations.append((f'{label}_tx{bit}_inverted', {**masks,label:value ^ bit}))
        configurations.append(('restored',masks.copy()))
    applied = masks.copy()
    results = {}
    try:
        for name, configuration in configurations:
            for label, value in configuration.items():
                if applied[label] != value:
                    phase(label,value,args.output/label,name+'_set')
                    applied[label] = value
            time.sleep(0.15)
            results[name] = parallel(lambda label:stage(label,args.output/name,args.captures,configuration[label]))
            print(json.dumps(dict(stage=name,results=results[name])),flush=True)
            (args.output/'results.json').write_text(json.dumps(results,indent=2))
    finally:
        restore_errors = {}
        # Always verify all original requests, including after a failed write.
        for label,value in masks.items():
            try:
                current=state(label,args.output/label,'pre_restore')
                if current['requested'] != value:
                    phase(label,value,args.output/label,'restore')
            except Exception as exc:
                restore_errors[label]=str(exc)
                try:
                    phase(label,value,args.output/label,'restore_retry')
                except Exception as retry:
                    restore_errors[label] += ' / '+str(retry)
        time.sleep(0.1)
        restored=parallel(lambda label:state(label,args.output/label,'restored'))
        assert all(restored[label]['requested']==masks[label] and
                   restored[label]['tx_requested']==initial[label]['tx_requested'] and
                   restored[label]['tx_enabled']==initial[label]['tx_enabled'] for label in masks), restored
        (args.output/'restoration.json').write_text(json.dumps(dict(verified=True,states=restored,retry_notes=restore_errors),indent=2))
        print('Original TX phase and enable states restored and verified.',flush=True)

if __name__ == '__main__':
    main()
