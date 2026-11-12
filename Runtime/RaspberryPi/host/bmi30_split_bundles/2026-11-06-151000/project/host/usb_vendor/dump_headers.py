#!/usr/bin/env python3
import usb.core, usb.util, struct, time
VID=0xCAFE; PID=0x4001; EP_IN=0x83; EP_OUT=0x03
MAGIC=0xA55A; HDR_SIZE=32
HDR_FMT='<H B B I I H H I I I H H'

def open_dev():
    d=usb.core.find(idVendor=VID, idProduct=PID)
    if d is None: raise SystemExit('device not found')
    cfg=d.get_active_configuration()
    intf=None
    for i in cfg:
        eps=[e.bEndpointAddress for e in i.endpoints()]
        if EP_IN in eps and EP_OUT in eps:
            intf=i; break
    if intf is None: raise SystemExit('intf not found')
    try:
        if d.is_kernel_driver_active(intf.bInterfaceNumber):
            try: d.detach_kernel_driver(intf.bInterfaceNumber)
            except Exception: pass
    except Exception: pass
    usb.util.claim_interface(d, intf.bInterfaceNumber)
    return d

def main():
    d=open_dev()
    # configure profile/full and START
    try:
        d.write(EP_OUT, bytes([0x14, 0x02]), timeout=500)  # SET_PROFILE=2
    except Exception: pass
    try:
        d.write(EP_OUT, bytes([0x13, 0x01]), timeout=500)  # SET_FULL_MODE=1
    except Exception: pass
    try: d.write(EP_OUT, bytes([0x20]), timeout=1000)
    except Exception: pass
    t0=time.time(); n=0; maxn=24
    while n<maxn and time.time()-t0<8.0:
        try:
            data=bytes(d.read(EP_IN, 2048, timeout=1000))
        except usb.core.USBError as e:
            if getattr(e,'errno',None)==110: continue
            raise
        if data[:4]==b'STAT':
            print('STAT:', data[:16].hex())
            continue
        if len(data) < 16:
            print('CHUNK:', len(data), data[:16].hex())
            continue
        if len(data)<HDR_SIZE:
            print('SHORT:', len(data), data[:16].hex())
            continue
        (magic,ver,flags,seq,ts,total_samples,zc,z1o,z1l,rsv1,rsv2,crc)=struct.unpack(HDR_FMT, data[:HDR_SIZE])
        print(f"HDR n={n} magic=0x{magic:04X} ver={ver} flags=0x{flags:02X} seq={seq} total={total_samples} len={len(data)}")
        n+=1
    # stop
    try: d.write(EP_OUT, bytes([0x21]), timeout=1000)
    except Exception: pass

if __name__=='__main__':
    main()
