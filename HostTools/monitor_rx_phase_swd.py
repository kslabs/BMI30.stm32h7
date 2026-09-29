#!/usr/bin/env python3
"""Non-halting RXP1/SYNC diagnostics for a matching firmware ELF.

Reads only unless an explicitly requested experiment writes phase or diagnostic
enable state through the helper functions. Every CLI operation is retained.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import time

import capture_rx_phase_swd as swd
from monitor_rs485_swd import decode_snapshot, SYMBOL
from vendor_rx_phase import decode_rx_phase, RXP1_SIZE

NM='C:/ST/STM32CubeCLT_1.19.0/GNU-tools-for-STM32/bin/arm-none-eabi-nm.exe'


def symbols(elf):
    data=subprocess.run([NM,'-S','--defined-only',str(elf)],capture_output=True,text=True,check=True).stdout
    result={}
    for line in data.splitlines():
        words=line.split()
        if len(words)==4:result[words[3]]=(int(words[0],16),int(words[1],16))
    if result['g_rx_phase_status'][1]!=RXP1_SIZE:raise ValueError('RXP1 ELF layout mismatch')
    return result


def snapshot(label, folder, name, sym, verify_binary=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    operations=[]
    if verify_binary:
        expected=Path(verify_binary).read_bytes()
        operations+=swd.upload(0x08000000,len(expected),folder/(name+'_flash.bin'))
    for tag,symbol,size in [('rx','g_rx_phase_status',RXP1_SIZE),('sync',SYMBOL,128),
                            ('phase','s_tx_phase_requested_mask',2),('tx','streaming',3)]:
        operations+=swd.upload(sym[symbol][0],size,folder/(name+'_'+tag+'.bin'))
    event=swd.call(label,folder,name,operations)
    if verify_binary:
        actual=(folder/(name+'_flash.bin')).read_bytes()
        if actual!=expected:raise ValueError(f'{label}: flash image mismatch')
    for attempt in range(8):
        try:
            rx=decode_rx_phase((folder/(name+'_rx.bin')).read_bytes())
            sync=decode_snapshot((folder/(name+'_sync.bin')).read_bytes())
            break
        except ValueError:
            if attempt==7:raise
            # A publication can overlap more than one non-halting SWD read.
            # Retry only the diagnostic copies, never halt/reset the target.
            swd.call(label,folder,name+f'_retry{attempt+1}',
                swd.upload(sym['g_rx_phase_status'][0],RXP1_SIZE,folder/(name+'_rx.bin'))+
                swd.upload(sym[SYMBOL][0],128,folder/(name+'_sync.bin')))
    phase=list((folder/(name+'_phase.bin')).read_bytes())
    tx=list((folder/(name+'_tx.bin')).read_bytes())
    result=dict(timestamp=event['timestamp'],label=label,rx=rx,sync=sync,
                phase_requested=phase[0],phase_applied=phase[1],streaming=tx[0],tx_requested=tx[1],tx_enabled=tx[2])
    (folder/(name+'.json')).write_text(json.dumps(result,indent=2))
    return result


def set_phase(label, value, folder, name, sym):
    if not 0<=value<=3:raise ValueError('phase mask must be 0..3')
    swd.call(label,Path(folder),name,['-w8',hex(sym['s_tx_phase_requested_mask'][0]),hex(value),'-nv'])


def set_enabled(label, value, folder, name, sym):
    if value not in (0,1):raise ValueError('enable must be 0/1')
    swd.call(label,Path(folder),name,['-w32',hex(sym['g_rx_phase_enabled'][0]),hex(value),'-nv'])


def brief(s):
    r=s['rx']
    return dict(label=s['label'],uptime_ms=r['uptime_ms'],frames=r['frames'],
        phase=[c['phase_deg'] for c in r['channels']],valid=[c['valid'] for c in r['channels']],
        quality=[c['quality'] for c in r['channels']],
        angle=[c['angle_deg'] for c in r['channels']],
        roi_clip=[c['roi_clipped_fraction'] for c in r['channels']],
        tx=s['phase_applied'],enabled=r['enabled'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',action='append',choices=list(swd.DEVICES))
    parser.add_argument('--duration',type=float,default=30)
    parser.add_argument('--interval',type=float,default=1)
    args=parser.parse_args()
    if args.duration<0 or args.interval<=0:parser.error('invalid timing')
    args.output.mkdir(parents=True,exist_ok=False)
    sym=symbols(args.elf);start=time.monotonic();idx=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            tasks=[pool.submit(snapshot,d,args.output/d,f'{idx:05d}',sym) for d in (args.device or swd.DEVICES)]
            for task in tasks:print(json.dumps(brief(task.result())),flush=True)
            idx+=1
            if time.monotonic()-start>=args.duration:break
            time.sleep(max(0,min(args.interval,start+idx*args.interval-time.monotonic())))


if __name__=='__main__':
    main()
