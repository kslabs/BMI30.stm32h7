#!/usr/bin/env python3
from __future__ import annotations
import sys, time, struct

try:
    import usb.core, usb.util  # type: ignore
except Exception as e:
    print(f"[ERR] PyUSB not installed: {e}\n  pip install pyusb")
    sys.exit(2)

VID=0xCAFE; PID=0x4001; EP_OUT=0x03; EP_IN=0x83
CMD_START=0x20; CMD_STOP=0x21; CMD_GET_STATUS=0x30
MAGIC=0xA55A; HDR_FMT='<H B B I I H H I I I H H'; HDR_SIZE=32; MAGIC_LE=b"\x5A\xA5"

def open_vendor(vid:int, pid:int, ep_out:int, ep_in:int):
    d = usb.core.find(idVendor=vid, idProduct=pid)
    if d is None:
        raise SystemExit(f"device {vid:04X}:{pid:04X} not found")
    try:
        _ = d.get_active_configuration()
    except usb.core.USBError:
        d.set_configuration()
    cfg = d.get_active_configuration()
    chosen=None
    for it in cfg:
        addrs=[e.bEndpointAddress for e in it.endpoints()]
        if ep_out in addrs and ep_in in addrs:
            chosen=it; break
    if chosen is None:
        raise SystemExit('vendor interface with EP 0x%02X/0x%02X not found' % (ep_out, ep_in))
    try:
        if d.is_kernel_driver_active(chosen.bInterfaceNumber):
            try: d.detach_kernel_driver(chosen.bInterfaceNumber)
            except Exception: pass
    except Exception:
        pass
    usb.util.claim_interface(d, chosen.bInterfaceNumber)
    return d, chosen


def main():
    d,intf = open_vendor(VID, PID, EP_OUT, EP_IN)
    print(f"[open] {VID:04X}:{PID:04X} IF#{intf.bInterfaceNumber} OUT=0x{EP_OUT:02X} IN=0x{EP_IN:02X}")
    buf=bytearray(); got_test=False; pairs=0; last_a_seq=None; frames_seen=0; seen_a=False; seen_b=False
    def rd(n:int, to:int):
        return bytes(d.read(EP_IN, n, timeout=to))
    def wr(b:bytes):
        return d.write(EP_OUT, b, timeout=600)
    try:
        wr(bytes([CMD_START])); print(f"[TX] START (0x{CMD_START:02X})")
        # коротко подождём STAT
        try:
            st=rd(64, 800)
            if st[:4]==b'STAT':
                print(f"[RX] STAT len={len(st)} head={' '.join(f'{x:02X}' for x in st[:16])} …")
            else:
                buf.extend(st)
        except usb.core.USBError:
            pass
        # читаем до первых 4 кадров или 8 секунд (достаточно, чтобы показать поток)
        deadline=time.time()+8.0
        while frames_seen<4 and time.time()<deadline:
            try:
                data=rd(4096, 700)
            except usb.core.USBError as e:
                if getattr(e,'errno',None)==110:
                    continue
                raise
            if not data:
                continue
            if data[:4]==b'STAT':
                print(f"[RX] STAT (mid) len={len(data)} head={' '.join(f'{x:02X}' for x in data[:16])} …")
                continue
            buf.extend(data)
            # deframe
            while True:
                if len(buf)<HDR_SIZE:
                    break
                if not (buf[0]==0x5A and buf[1]==0xA5):
                    idx=buf.find(MAGIC_LE)
                    if idx==-1:
                        del buf[:max(0,len(buf)-1)]
                        break
                    else:
                        del buf[:idx]
                        if len(buf)<HDR_SIZE:
                            break
                try:
                    (magic, ver, flags, seq, ts, total_samples, zc, z1o, z1l, r1, r2, crc) = struct.unpack(HDR_FMT, bytes(buf[:HDR_SIZE]))
                except struct.error:
                    break
                if magic!=MAGIC:
                    del buf[0]; continue
                frame_len = HDR_SIZE + int(total_samples)*2
                if len(buf)<frame_len: break
                # рабочий канал или TEST
                if (flags & 0x80) and total_samples==8 and not got_test:
                    print(f"[RX] TEST len={frame_len} ver={ver} flags=0x{flags:02X} seq={seq}")
                    got_test=True
                    del buf[:frame_len]
                    continue
                ch=None
                if flags & 0x01:
                    ch='A'; last_a_seq=seq
                elif flags & 0x02:
                    ch='B'
                else:
                    del buf[:frame_len]
                    continue
                # Выведем первые 16 значений для наглядности
                payload = bytes(buf[HDR_SIZE:frame_len])
                n=min(16, len(payload)//2)
                if n>0:
                    vals = struct.unpack('<'+'h'*n, payload[:n*2])
                    print(f"[RX] {ch} len={frame_len} seq={seq & 0xFFFF} total={total_samples} first={list(vals)}")
                else:
                    print(f"[RX] {ch} len={frame_len} seq={seq & 0xFFFF} total={total_samples}")
                frames_seen += 1
                if ch=='A':
                    seen_a=True; last_a_seq=seq
                elif ch=='B':
                    seen_b=True
                if ch=='B' and last_a_seq is not None and last_a_seq==seq:
                    pairs+=1
                del buf[:frame_len]
        # STOP
        wr(bytes([CMD_STOP])); print(f"[TX] STOP (0x{CMD_STOP:02X})")
        # ждём STAT после STOP (не критично)
        try:
            st2=rd(64, 800)
            if st2[:4]==b'STAT':
                print(f"[RX] STAT (STOP) len={len(st2)} head={' '.join(f'{x:02X}' for x in st2[:16])} …")
        except Exception:
            pass
        print(f"[sum] frames={frames_seen} pairs={pairs} seenA={seen_a} seenB={seen_b} test={got_test}")
        return 0 if frames_seen>0 else 1
    finally:
        try:
            usb.util.release_interface(d, intf.bInterfaceNumber)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(d)
        except Exception:
            pass

if __name__=='__main__':
    raise SystemExit(main())
