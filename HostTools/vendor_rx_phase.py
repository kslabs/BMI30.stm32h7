#!/usr/bin/env python3
"""Read BMI30 measured RX polarity via EP0; does not change stream or TX."""
import argparse
import json
import struct
import time

CMD_GET_RX_PHASE = 0x4E
RXP1_SIZE = 112
FLAGS = {1:'collecting',2:'weak',4:'ambiguous',8:'clipped',16:'stale',32:'unsupported'}


def decode_rx_phase(raw):
    if len(raw)!=RXP1_SIZE or raw[:6]!=b'RXP1\x01\x02' or struct.unpack_from('<H',raw,6)[0]!=RXP1_SIZE:
        raise ValueError('Invalid RXP1 response; firmware 1.2.53+ required')
    seq=struct.unpack_from('<I',raw,8)[0]
    if seq&1 or seq!=struct.unpack_from('<I',raw,108)[0]:
        raise ValueError('Inconsistent RXP1 snapshot')
    values=struct.unpack_from('<7I',raw,12)
    result=dict(zip(['uptime_ms','capture_generation','frames','drops','max_service_cycles',
                     'total_service_cycles','last_frame_ms'],values))
    result.update(sequence=seq,enabled=bool(struct.unpack_from('<I',raw,104)[0]),channels=[])
    for ch in (0,1):
        phase,valid,quality,flags,angle,coherence,amplitude,even,odd,clip,last,changes,roi_clip,lo,hi,span=struct.unpack_from('<BBBBh5HII4H',raw,40+ch*32)
        if phase not in (0,1,255) or valid>1 or quality>100 or (valid and phase==255):
            raise ValueError('Invalid RXP1 channel state')
        result['channels'].append(dict(channel='AB'[ch],phase_deg=None if phase==255 else phase*180,
            valid=bool(valid),quality=quality,flags=[name for bit,name in FLAGS.items() if flags&bit],
            angle_deg=angle/100,coherence=coherence/1000,amplitude=amplitude,
            even_count=even,odd_count=odd,clipped_fraction=clip/1000,
            last_valid_ms=last,age_ms=None if phase==255 else (result['uptime_ms']-last)&0xFFFFFFFF,
            changes=changes,roi_clipped_fraction=roi_clip/1000,roi_min=lo,roi_max=hi,roi_span=span))
    return result


def read_phase(dev):
    return decode_rx_phase(bytes(dev.ctrl_transfer(0xC0,CMD_GET_RX_PHASE,0,0,RXP1_SIZE,timeout=500)))


def main():
    from vendor_tx_phase import choose_device
    import usb.util
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=int)
    parser.add_argument('--serial')
    parser.add_argument('--vid',type=lambda v:int(v,0),default=0xCAFE)
    parser.add_argument('--pid',type=lambda v:int(v,0),default=0x4001)
    parser.add_argument('--watch',action='store_true')
    args=parser.parse_args()
    dev=choose_device(args.vid,args.pid,args.serial,args.index)
    try:
        while True:
            print(json.dumps(read_phase(dev),ensure_ascii=False),flush=True)
            if not args.watch: break
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        usb.util.dispose_resources(dev)


if __name__=='__main__':
    main()
