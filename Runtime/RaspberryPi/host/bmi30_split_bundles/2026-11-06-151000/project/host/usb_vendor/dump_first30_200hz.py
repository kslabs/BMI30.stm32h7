#!/usr/bin/env python3
from __future__ import annotations
import usb.core, usb.util, struct, time

VID=0xCAFE; PID=0x4001; EP_IN=0x83; EP_OUT=0x03
CMD_SET_PROFILE=0x14; CMD_SET_FULL=0x13; CMD_START=0x20; CMD_STOP=0x21
MAGIC=0xA55A; HDR_SIZE=32; HDR_FMT='<H B B I I H H I I I H H'

def open_dev():
    d=usb.core.find(idVendor=VID, idProduct=PID)
    if d is None: raise SystemExit('device not found')
    try:
        cfg=d.get_active_configuration()
    except usb.core.USBError:
        d.set_configuration(); cfg=d.get_active_configuration()
    intf=None
    for it in cfg:
        eps=[e.bEndpointAddress for e in it.endpoints()]
        if EP_IN in eps and EP_OUT in eps:
            intf=it; break
    if intf is None: raise SystemExit('vendor intf not found')
    try:
        if d.is_kernel_driver_active(intf.bInterfaceNumber):
            try: d.detach_kernel_driver(intf.bInterfaceNumber)
            except Exception: pass
    except Exception: pass
    try:
        usb.util.claim_interface(d, intf.bInterfaceNumber)
    except Exception:
        pass
    return d, intf

def w(dev, data:bytes, timeout=800):
    return dev.write(EP_OUT, data, timeout=timeout)

def main():
    d,intf=open_dev()
    try:
        # профиль 200 Гц, полный режим, старт
        try:
            w(d, bytes([CMD_SET_PROFILE, 0x01])); time.sleep(0.03)
            w(d, bytes([CMD_SET_FULL, 0x01])); time.sleep(0.03)
        except Exception:
            pass
        w(d, bytes([CMD_START])); time.sleep(0.05)
        buf=bytearray(); MAGIC_LE=b"\x5A\xA5"; frames=0; first_seq=None
        t0=time.time(); first_print=False
        while time.time()-t0<3.0 and frames<3:
            try:
                data=bytes(d.read(EP_IN, 4096, timeout=900))
            except usb.core.USBError as e:
                if getattr(e,'errno',None)==110:
                    continue
                raise
            if not data: continue
            if data[:4]==b'STAT':
                if len(data)>64: data=data[64:]
                else: continue
            buf.extend(data)
            while True:
                if len(buf)<HDR_SIZE: break
                if not (buf[0]==0x5A and buf[1]==0xA5):
                    idx=buf.find(MAGIC_LE)
                    if idx==-1:
                        del buf[:max(0,len(buf)-1)]; break
                    else:
                        del buf[:idx]
                        if len(buf)<HDR_SIZE: break
                try:
                    (magic,ver,flags,seq,ts,total_samples,zc,z1o,z1l,r1,r2,crc)=struct.unpack(HDR_FMT, bytes(buf[:HDR_SIZE]))
                except struct.error:
                    break
                if magic!=MAGIC:
                    del buf[0]; continue
                frame_len=HDR_SIZE+int(total_samples)*2
                if len(buf)<frame_len: break
                payload=bytes(buf[HDR_SIZE:frame_len]); del buf[:frame_len]
                # пропустим возможный тестовый кадр 8 семплов
                if (flags & 0x80) and total_samples==8:
                    continue
                ch = 0 if (flags & 0x01) else (1 if (flags & 0x02) else -1)
                if ch<0:
                    continue
                # печать первых 30 значений int16
                n=min(30, len(payload)//2)
                vals=list(struct.unpack('<'+'h'*n, payload[:n*2]))
                if not first_print:
                    print(f"FRAME seq={seq} total={total_samples} (ожидаем 1360 для 200Гц)")
                    first_seq=seq
                    first_print=True
                print(f"CH{ch}: ", ' '.join(str(x) for x in vals))
                frames+=1
        # вывод о потоке
        if frames>=2:
            print(f"[info] Наблюдается поток (кадров: {frames})")
        elif frames==1:
            print("[info] Получен один рабочий кадр (похоже на одиночный пакет)")
        else:
            print("[warn] Рабочие кадры не получены")
        try:
            w(d, bytes([CMD_STOP]))
        except Exception:
            pass
        return 0 if frames>0 else 1
    finally:
        try: usb.util.release_interface(d, intf.bInterfaceNumber)
        except Exception: pass
        try: usb.util.dispose_resources(d)
        except Exception: pass

if __name__=='__main__':
    raise SystemExit(main())
