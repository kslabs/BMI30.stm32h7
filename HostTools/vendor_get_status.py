#!/usr/bin/env python3
import usb.core, usb.util, time, argparse

VID=0xCAFE; PID=0x4001; IF_NUM=2; EP_OUT=0x03; EP_IN=0x83
CMD_GET_STATUS=0x30; CMD_START=0x20; CMD_STOP=0x21

LAYOUT = [
  (0,4,'sig'),      # 'STAT'
  (4,1,'version'),
  (5,1,'reserved0'),
  (6,2,'cur_samples'),
  (8,2,'frame_bytes'),
  (10,2,'test_frames'),
  (12,4,'produced_seq'),
  (16,4,'sent0'),
  (20,4,'sent1'),
  (24,4,'dbg_tx_cplt'),
  (28,4,'dbg_partial_abort'),
  (32,4,'dbg_size_mismatch'),
  (36,4,'dma_done0'),
  (40,4,'dma_done1'),
  (44,4,'frame_wr_seq'),
  (48,2,'flags_runtime'),
  (50,2,'flags2'),
  (52,1,'sending_ch'),
  (53,1,'reserved2'),
  (54,2,'pair_idx'),
  (56,2,'last_tx_len'),
  (58,4,'cur_stream_seq'),
  (62,2,'reserved3'),
]

FLAG2_BITS = [
 'ep_busy','tx_ready','pending_B','test_in_flight','start_ack_done','start_stat_inflight','start_stat_planned','pending_status','simple_tx_mode','diag_mode_active'
]

def find_dev():
    d=usb.core.find(idVendor=VID,idProduct=PID)
    if d is None: raise SystemExit('device not found')
    try: d.set_configuration()
    except Exception: pass
    try: usb.util.claim_interface(d, IF_NUM)
    except Exception: pass
    return d

def read_pkt(dev, timeout=300):
    try:
        return bytes(dev.read(EP_IN, 512, timeout))
    except usb.core.USBError as e:
        if getattr(e,'errno',None) in (110,10060):
            return None
        raise

def parse_status(buf: bytes):
    if len(buf)<64 or buf[:4]!=b'STAT':
        return None
    out={}
    for off,size,name in LAYOUT:
        field = buf[off:off+size]
        if name=='sig':
            out[name]=field.decode(errors='ignore')
        elif size==1:
            out[name]=field[0]
        elif size==2:
            out[name]=int.from_bytes(field,'little')
        elif size==4:
            out[name]=int.from_bytes(field,'little')
    # Расшифровка flags2
    f2 = out.get('flags2',0)
    out['flags2_bits'] = {FLAG2_BITS[i]: bool(f2 & (1<<i)) for i in range(min(len(FLAG2_BITS),16))}
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repeat',type=int,default=5)
    ap.add_argument('--interval',type=float,default=0.4)
    ap.add_argument('--start',action='store_true')
    args=ap.parse_args()
    dev=find_dev()
    if args.start:
        dev.write(EP_OUT, bytes([CMD_START]))
        print('START sent')
    for i in range(args.repeat):
        dev.write(EP_OUT, bytes([CMD_GET_STATUS]))
        t0=time.time(); pkt=None
        while time.time()-t0 < 0.6:
            p=read_pkt(dev, timeout=150)
            if p is None: continue
            if p[:4]==b'STAT': pkt=p; break
        if pkt is None:
            print(f'[{i}] STAT timeout')
        else:
            st=parse_status(pkt)
            if st is None:
                print(f'[{i}] BAD len={len(pkt)} hex={pkt.hex()}')
            else:
                print(f'[{i}] len={len(pkt)} cur_samples={st["cur_samples"]} produced_seq={st["produced_seq"]} sent0={st["sent0"]} sent1={st["sent1"]} tx_cplt={st["dbg_tx_cplt"]} test_frames={st["test_frames"]} flags2={hex(st["flags2"])} bits={st["flags2_bits"]}')
        time.sleep(args.interval)

if __name__=='__main__':
    main()