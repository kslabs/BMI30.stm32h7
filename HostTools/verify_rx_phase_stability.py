#!/usr/bin/env python3
"""Combined non-halting RX/RS485/ADC/USB observation on the four bench boards.

The optional --reset-sync-peaks only clears diagnostic accumulators, never the
timers or ADC. Supply an ELF/BIN matching every board. RX acceptance can exclude
a board whose analog signal is being investigated; transport is still checked.
"""
import argparse
import concurrent.futures
import json
from pathlib import Path
import time
from types import SimpleNamespace

import monitor_rx_phase_swd as monitor
from monitor_rs485_swd import summarize


def rx_summary(rows):
    output={'readings':len(rows),'channels':[],'failures':[]}
    for ch in range(2):
        values=[row['rx']['channels'][ch] for row in rows]
        valid=[v for v in values if v['valid']]
        changes=values[-1]['changes']-values[0]['changes']
        phases=sorted(set(v['phase_deg'] for v in valid))
        output['channels'].append(dict(channel='AB'[ch],valid_readings=len(valid),
            valid_fraction=len(valid)/len(values),phases=phases,changes=changes,
            minimum_quality=min(v['quality'] for v in valid) if valid else None,
            maximum_age_ms=max((v['age_ms'] or 0) for v in values)))
        if changes!=0 or len(phases)>1:output['failures'].append('unexpected_phase_change:'+str(ch))
        if not valid:output['failures'].append('no_valid_rx_phase:'+str(ch))
        if any(v['phase_deg'] is not None and v['age_ms']>3000 for v in values):
            output['failures'].append('rx_phase_missing_for_over_3s:'+str(ch))
    for a,b in zip(rows,rows[1:]):
        if b['rx']['frames']<=a['rx']['frames']:output['failures'].append('rx_frames_not_advancing')
        if b['rx']['capture_generation']!=a['rx']['capture_generation']:
            output['failures'].append('capture_generation_changed')
    if any(not row['rx']['enabled'] for row in rows):output['failures'].append('detector_disabled')
    output['failures']=sorted(set(output['failures']))
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf',type=Path,required=True)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--duration',type=float,default=1800)
    parser.add_argument('--interval',type=float,default=5)
    parser.add_argument('--rx-device',action='append',choices=list(monitor.swd.DEVICES),required=True)
    parser.add_argument('--reset-sync-peaks',action='store_true')
    args=parser.parse_args()
    if args.duration<=0 or args.interval<=0:parser.error('timing must be positive')
    args.output.mkdir(parents=True,exist_ok=False)
    sym=monitor.symbols(args.elf)
    rows={label:[] for label in monitor.swd.DEVICES}
    for label in rows:
        folder=args.output/label
        monitor.snapshot(label,folder,'verified',sym,args.binary)
        if args.reset_sync_peaks:
            monitor.swd.call(label,folder,'clear_sync_statistics',
                ['-w32',hex(sym['g_rs485_stability_reset'][0]),'0x1','-nv'])
    time.sleep(1)
    started=time.monotonic();index=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            futures={label:pool.submit(monitor.snapshot,label,args.output/label,f'{index:05d}',sym) for label in rows}
            for label,future in futures.items():rows[label].append(future.result())
            covered=min((v[-1]['sync']['uptime_ms']-v[0]['sync']['uptime_ms'])/1000 for v in rows.values())
            brief=dict(elapsed_s=round(covered,1),devices=[monitor.brief(v[-1]) for v in rows.values()])
            (args.output/'progress.json').write_text(json.dumps(brief,indent=2))
            if index%6==0 or covered>=args.duration:print(json.dumps(brief),flush=True)
            if covered>=args.duration:break
            index+=1
            time.sleep(max(0,started+index*args.interval-time.monotonic()))
    criteria=SimpleNamespace(duration=args.duration,expected_nodes=4,expected_mask=0x3802,
        limit_us=3.125,require_raw_phase=True,require_usb_active=True,
        min_adc_hz=390,device_min_usb_hz=1,max_adc_age_ms=10,max_usb_age_ms=1000)
    result={'duration_requested_s':args.duration,'rx_devices':args.rx_device,'devices':{}}
    passed=True
    for label,values in rows.items():
        sync=summarize([v['sync'] for v in values],criteria)
        rx=rx_summary(values)
        result['devices'][label]=dict(sync=sync,rx=rx,rx_in_acceptance=label in args.rx_device)
        if sync['failures'] or sync['incomplete']:passed=False
        if label in args.rx_device and rx['failures']:passed=False
    result['passed']=passed
    (args.output/'summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(dict(passed=passed,summary=str(args.output/'summary.json'))),flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
