#!/usr/bin/env python3
import usb.core, usb.util, time, argparse

VID=0xCAFE; PID=0x4001; IF_NUM=2; EP_OUT=0x03; EP_IN=0x83
CMD_GET_STATUS=0x30; CMD_START=0x20; CMD_STOP=0x21
STAT_LEN_V5 = 136

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

VND_STFLAG_OPTIC_ACTIVE = 0x0020

def find_dev():
    d=usb.core.find(idVendor=VID,idProduct=PID)
    if d is None: raise SystemExit('device not found')
    try:
        d.set_configuration()
    except Exception:
        pass

    # ВАЖНО: bulk-endpoints 0x03/0x83 доступны только на alt=1 для IF#2.
    try:
        if d.is_kernel_driver_active(IF_NUM):
            d.detach_kernel_driver(IF_NUM)
    except Exception:
        pass

    try:
        d.set_interface_altsetting(interface=IF_NUM, alternate_setting=1)
    except Exception:
        # На некоторых системах set_interface_altsetting может бросать, если интерфейс уже в alt=1.
        pass

    try:
        usb.util.claim_interface(d, IF_NUM)
    except Exception:
        pass
    return d

def read_pkt(dev, timeout=300):
    try:
        return bytes(dev.read(EP_IN, 512, timeout))
    except usb.core.USBError as e:
        if getattr(e,'errno',None) in (110,10060):
            return None
        raise

def ctrl_get_status(dev, timeout=300):
    # Vendor IN (device->host). В прошивке GET_STATUS по EP0 разрешён всегда.
    try:
        data = dev.ctrl_transfer(0xC0, CMD_GET_STATUS, 0, 0, STAT_LEN_V5, timeout=timeout)
        return bytes(data)
    except usb.core.USBError as e:
        if getattr(e,'errno',None) in (110,10060):
            return None
        raise

def ctrl_send_nodata(dev, bRequest: int, timeout=300):
    # Vendor OUT (host->device) без data stage.
    dev.ctrl_transfer(0x40, bRequest, 0, 0, None, timeout=timeout)

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
    # reserved3 packed optic info:
    # [15:8]=optic_power, [7:2]=optic_hold_seconds, [0]=optic_active, [1]=tx_enable
    rs3 = out.get('reserved3', 0)
    out['optic_power'] = (rs3 >> 8) & 0xFF
    out['optic_hold_seconds'] = (rs3 >> 2) & 0x3F
    out['optic_active_packed'] = bool(rs3 & 0x01)
    out['tx_enable_packed'] = bool(rs3 & 0x02)
    out['optic_active_flag'] = bool(out.get('flags_runtime', 0) & VND_STFLAG_OPTIC_ACTIVE)
    if len(buf) >= STAT_LEN_V5 and out.get('version', 0) >= 5:
        out['optic_hold_ds'] = int.from_bytes(buf[96:98], 'little')
        out['led_pattern'] = buf[98]
        out['sync_local_status'] = buf[99]
        out['sync_seen_mask'] = int.from_bytes(buf[100:104], 'little')
        out['sync_node_count'] = buf[104]
        out['sync_status_bytes'] = list(buf[105:136])
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repeat',type=int,default=5)
    ap.add_argument('--interval',type=float,default=0.4)
    ap.add_argument('--start',action='store_true')
    ap.add_argument('--stop',action='store_true')
    ap.add_argument('--ctrl',action='store_true', help='Read STAT via EP0 control (reliable; ignores bulk gating)')
    ap.add_argument('--bulk',action='store_true', help='Force bulk GET_STATUS (requires IF#2 alt=1; may be gated)')
    args=ap.parse_args()
    dev=find_dev()

    use_bulk = bool(args.bulk)
    use_ctrl = bool(args.ctrl) or not use_bulk

    if args.stop:
        # START/STOP по EP0 поддерживаются прошивкой, безопаснее bulk.
        ctrl_send_nodata(dev, CMD_STOP)
        print('STOP sent (CTRL)')
    if args.start:
        ctrl_send_nodata(dev, CMD_START)
        print('START sent (CTRL)')

    for i in range(args.repeat):
        pkt=None
        if use_bulk:
            dev.write(EP_OUT, bytes([CMD_GET_STATUS]))
            t0=time.time()
            while time.time()-t0 < 0.6:
                p=read_pkt(dev, timeout=150)
                if p is None: continue
                if p[:4]==b'STAT':
                    pkt=p
                    break

        if pkt is None and use_ctrl:
            pkt = ctrl_get_status(dev, timeout=300)
        if pkt is None:
            print(f'[{i}] STAT timeout')
        else:
            st=parse_status(pkt)
            if st is None:
                print(f'[{i}] BAD len={len(pkt)} hex={pkt.hex()}')
            else:
                print(
                    f'[{i}] len={len(pkt)} cur_samples={st["cur_samples"]} produced_seq={st["produced_seq"]} '
                    f'sent0={st["sent0"]} sent1={st["sent1"]} tx_cplt={st["dbg_tx_cplt"]} '
                    f'test_frames={st["test_frames"]} flags2={hex(st["flags2"])} bits={st["flags2_bits"]} '
                    f'optic_power={st["optic_power"]} optic_hold_s={st["optic_hold_seconds"]} '
                    f'optic_active={int(st["optic_active_packed"] or st["optic_active_flag"])} '
                    f'tx_enable={int(st["tx_enable_packed"])} '
                    f'optic_hold_ds={st.get("optic_hold_ds", st["optic_hold_seconds"]*10)} '
                    f'led_pattern={st.get("led_pattern", "-")} '
                    f'sync_count={st.get("sync_node_count", "-")} sync_mask=0x{st.get("sync_seen_mask", 0):08X}'
                )
        time.sleep(args.interval)

    try:
        usb.util.release_interface(dev, IF_NUM)
        usb.util.dispose_resources(dev)
    except Exception:
        pass

if __name__=='__main__':
    main()
