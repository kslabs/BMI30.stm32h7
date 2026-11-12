#!/usr/bin/env python3
from __future__ import annotations
import sys, time, struct

try:
    import usb.core, usb.util  # type: ignore
except Exception as e:
    print(f"[ERR] PyUSB not installed: {e}\n  pip install pyusb")
    sys.exit(2)

VID=0xCAFE; PID=0x4001; EP_OUT=0x03; EP_IN=0x83
CMD_START=0x20; CMD_STOP=0x21
MAGIC=0xA55A; HDR_FMT='<H B B I I H H I I I H H'; HDR_SIZE=32; MAGIC_LE=b"\x5A\xA5"

def open_vendor():
    d = usb.core.find(idVendor=VID, idProduct=PID)
    if d is None:
        raise SystemExit(f"device {VID:04X}:{PID:04X} not found")
    try:
        _ = d.get_active_configuration()
    except usb.core.USBError:
        d.set_configuration()
    cfg = d.get_active_configuration()
    chosen=None
    for it in cfg:
        addrs=[e.bEndpointAddress for e in it.endpoints()]
        if EP_OUT in addrs and EP_IN in addrs:
            chosen=it; break
    if chosen is None:
        raise SystemExit('vendor interface with EP 0x%02X/0x%02X not found' % (EP_OUT, EP_IN))
    try:
        if d.is_kernel_driver_active(chosen.bInterfaceNumber):
            try: d.detach_kernel_driver(chosen.bInterfaceNumber)
            except Exception: pass
    except Exception:
        pass
    usb.util.claim_interface(d, chosen.bInterfaceNumber)
    return d, chosen


def hexb(b:bytes, n:int=32) -> str:
    return ' '.join(f"{x:02X}" for x in b[:n]) + (" …" if len(b)>n else '')


def main():
    d,intf = open_vendor()
    print(f"[open] {VID:04X}:{PID:04X} IF#{intf.bInterfaceNumber} OUT=0x{EP_OUT:02X} IN=0x{EP_IN:02X}")
    buf=bytearray(); got_test=False; seen_a=False; seen_b=False
    def rd(n:int, to:int):
        return bytes(d.read(EP_IN, n, timeout=to))
    def wr(b:bytes):
        return d.write(EP_OUT, b, timeout=600)
    try:
        wr(bytes([CMD_START])); print(f"[TX] START (0x{CMD_START:02X})")
        t_end=time.time()+5.0
        while time.time()<t_end and not (seen_a and seen_b):
            try:
                data=rd(4096, 800)
            except usb.core.USBError as e:
                if getattr(e,'errno',None)==110:
                    continue
                raise
            if not data:
                continue
            if data[:4]==b'STAT':
                print(f"[RX] STAT len={len(data)} head={hexb(data,16)}")
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
                payload = bytes(buf[HDR_SIZE:frame_len])
                if (flags & 0x80) and total_samples==8 and not got_test:
                    print(f"[RX] TEST len={frame_len} ver={ver} seq={seq}")
                    got_test=True
                    del buf[:frame_len]
                    continue
                ch=None
                if flags & 0x01:
                    ch='A'
                elif flags & 0x02:
                    ch='B'
                else:
                    del buf[:frame_len]
                    continue
                # первые 32 байта payload в hex и как int16
                vhex = hexb(payload, 32)
                n=min(16, len(payload)//2)
                vals = struct.unpack('<'+'h'*n, payload[:n*2]) if n>0 else []
                print(f"[RX] {ch} seq={seq & 0xFFFF} total={total_samples} payload[0:32]={vhex}")
                print(f"     int16[0:16]={list(vals)}")
                if ch=='A': seen_a=True
                if ch=='B': seen_b=True
                del buf[:frame_len]
        wr(bytes([CMD_STOP])); print(f"[TX] STOP (0x{CMD_STOP:02X})")
        return 0 if (seen_a or seen_b) else 1
    finally:
        try: usb.util.release_interface(d, intf.bInterfaceNumber)
        except Exception: pass
        try: usb.util.dispose_resources(d)
        except Exception: pass

if __name__=='__main__':
    raise SystemExit(main())
