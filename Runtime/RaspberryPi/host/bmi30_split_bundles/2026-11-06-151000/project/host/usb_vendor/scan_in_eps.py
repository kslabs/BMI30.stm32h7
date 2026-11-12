#!/usr/bin/env python3
import usb.core, usb.util, struct, time

MAGIC=0xA55A; HDR_SIZE=32
HDR_FMT='<H B B I I H H I I I H H'

def main():
    devs=list(usb.core.find(find_all=True))
    if not devs:
        print('no usb devices visible')
        return 2
    for d in devs:
        try:
            cfg=d.get_active_configuration()
        except usb.core.USBError:
            try:
                d.set_configuration()
                cfg=d.get_active_configuration()
            except Exception:
                continue
        for intf in cfg:
            try:
                cls=getattr(intf,'bInterfaceClass',None)
                if cls!=0xFF: continue
                eps=list(intf.endpoints())
                in_eps=[e.bEndpointAddress for e in eps if (e.bEndpointAddress & 0x80) and (e.bmAttributes & 0x03)==2]
                out_eps=[e.bEndpointAddress for e in eps if (e.bEndpointAddress & 0x80)==0 and (e.bmAttributes & 0x03)==2]
                if not in_eps: continue
                try:
                    if d.is_kernel_driver_active(intf.bInterfaceNumber):
                        try: d.detach_kernel_driver(intf.bInterfaceNumber)
                        except Exception: pass
                except Exception: pass
                try:
                    usb.util.claim_interface(d, intf.bInterfaceNumber)
                except Exception:
                    pass
                print(f"probe {d.idVendor:04X}:{d.idProduct:04X} IF#{intf.bInterfaceNumber} IN={list(map(hex,in_eps))} OUT={list(map(hex,out_eps))}")
                # send START on first out if available
                if out_eps:
                    try:
                        d.write(out_eps[0], bytes([0x20]), timeout=300)
                    except Exception:
                        pass
                t0=time.time(); buf=bytearray(); MAGIC_LE=b"\x5A\xA5"
                # poll few times
                for ep in in_eps:
                    for _ in range(4):
                        try:
                            data=bytes(d.read(ep, 2048, timeout=500))
                        except usb.core.USBError as e:
                            if getattr(e,'errno',None)==110:
                                continue
                            break
                        if not data:
                            continue
                        if data[:4]==b'STAT':
                            print(f"  EP {ep:#04x}: STAT {data[:16].hex()}")
                            if len(data)>64:
                                data=data[64:]
                            else:
                                continue
                        buf.extend(data)
                        # deframe one
                        if len(buf)>=HDR_SIZE:
                            if not (buf[0]==0x5A and buf[1]==0xA5):
                                idx=buf.find(MAGIC_LE)
                                if idx!=-1:
                                    del buf[:idx]
                            if len(buf)>=HDR_SIZE and buf[0]==0x5A and buf[1]==0xA5:
                                try:
                                    (magic,ver,flags,seq,ts,total_samples,zc,z1o,z1l,r1,r2,crc)=struct.unpack(HDR_FMT, bytes(buf[:HDR_SIZE]))
                                except struct.error:
                                    continue
                                if magic==MAGIC:
                                    print(f"  EP {ep:#04x}: HDR ok flags=0x{flags:02X} seq={seq} total={total_samples}")
                                    return 0
                try:
                    usb.util.release_interface(d, intf.bInterfaceNumber)
                except Exception:
                    pass
                try:
                    usb.util.dispose_resources(d)
                except Exception:
                    pass
            except Exception:
                continue
    print('no working frames observed on any vendor bulk IN EP')
    return 1

if __name__=='__main__':
    raise SystemExit(main())
