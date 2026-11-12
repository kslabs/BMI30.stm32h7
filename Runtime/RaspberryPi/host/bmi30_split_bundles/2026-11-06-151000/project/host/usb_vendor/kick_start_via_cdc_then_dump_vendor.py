#!/usr/bin/env python3
from __future__ import annotations
import sys, time, glob, struct
import usb.core, usb.util  # type: ignore
import serial  # type: ignore

VID=0xCAFE; PID=0x4001; EP_IN=0x83; EP_OUT=0x03

# CDC protocol (from USB_proto)
CMD_SET_WINDOWS  = 0x10
CMD_SET_BLOCK_HZ = 0x11
CMD_START        = 0x20
CMD_STOP         = 0x21
CMD_GET_STATUS   = 0x30

MAGIC=0xA55A; HDR_SIZE=32
HDR_FMT='<H B B I I H H I I I H H'

def find_cdc_port() -> str:
    # simple scan
    ports = sorted(glob.glob('/dev/ttyACM*'))
    if not ports:
        raise SystemExit('CDC порт не найден (/dev/ttyACM*)')
    return ports[0]

def poke_cdc(cfg_block_hz:int=300, windows=(0,0,0,0), start=True):
    port = find_cdc_port()
    ser = serial.Serial(port=port, baudrate=115200, timeout=0.6, write_timeout=0.6)
    try:
        # SET_WINDOWS (optional)
        try:
            payload = struct.pack('<HHHH', *windows)
            ser.write(bytes([CMD_SET_WINDOWS]) + payload)
            ser.flush()
            time.sleep(0.05)
        except Exception:
            pass
        # SET_BLOCK_HZ (optional)
        try:
            payload = struct.pack('<H', cfg_block_hz & 0xFFFF)
            ser.write(bytes([CMD_SET_BLOCK_HZ]) + payload)
            ser.flush()
            time.sleep(0.05)
        except Exception:
            pass
        # START
        if start:
            ser.write(bytes([CMD_START]))
            ser.flush()
            time.sleep(0.08)
    finally:
        try: ser.close()
        except Exception: pass

def poke_cdc_via_pyusb(cfg_block_hz:int=300, windows=(0,0,0,0), start=True):
    """Отправить CDC-команды через PyUSB на CDC Data интерфейс (bulk OUT),
    если нет прав на /dev/ttyACM* или он отсутствует."""
    d = usb.core.find(idVendor=VID, idProduct=PID)
    if d is None:
        raise SystemExit('device not found (usb)')
    try:
        cfg = d.get_active_configuration()
    except usb.core.USBError:
        d.set_configuration(); cfg = d.get_active_configuration()
    # найдём CDC Data интерфейс (bInterfaceClass==0x0A) с bulk OUT
    cdc_intf = None; cdc_out = None
    for it in cfg:
        try:
            if getattr(it, 'bInterfaceClass', None) == 0x0A:
                for e in it.endpoints():
                    if (e.bEndpointAddress & 0x80) == 0 and (e.bmAttributes & 0x03) == 2:
                        cdc_intf = it; cdc_out = e.bEndpointAddress; break
        except Exception:
            continue
        if cdc_intf and cdc_out is not None:
            break
    if cdc_intf is None or cdc_out is None:
        raise SystemExit('CDC Data interface not found for PyUSB poke')
    try:
        if d.is_kernel_driver_active(cdc_intf.bInterfaceNumber):
            try: d.detach_kernel_driver(cdc_intf.bInterfaceNumber)
            except Exception: pass
    except Exception:
        pass
    try:
        usb.util.claim_interface(d, cdc_intf.bInterfaceNumber)
    except Exception:
        pass
    # Сформируем и отправим команды так же, как по CDC: однобайтный код + payload
    try:
        # SET_WINDOWS
        try:
            payload = struct.pack('<HHHH', *windows)
            d.write(cdc_out, bytes([CMD_SET_WINDOWS]) + payload, timeout=600)
            time.sleep(0.02)
        except Exception:
            pass
        # SET_BLOCK_HZ
        try:
            payload = struct.pack('<H', cfg_block_hz & 0xFFFF)
            d.write(cdc_out, bytes([CMD_SET_BLOCK_HZ]) + payload, timeout=600)
            time.sleep(0.02)
        except Exception:
            pass
        if start:
            d.write(cdc_out, bytes([CMD_START]), timeout=600)
            time.sleep(0.05)
    finally:
        try:
            usb.util.release_interface(d, cdc_intf.bInterfaceNumber)
        except Exception:
            pass

def open_vendor():
    d=usb.core.find(idVendor=VID, idProduct=PID)
    if d is None: raise SystemExit('device not found (vendor)')
    try:
        cfg=d.get_active_configuration()
    except usb.core.USBError:
        d.set_configuration(); cfg=d.get_active_configuration()
    intf=None
    for it in cfg:
        eps=[e.bEndpointAddress for e in it.endpoints()]
        if EP_IN in eps and EP_OUT in eps:
            intf=it; break
    if intf is None: raise SystemExit('vendor intf 0x83/0x03 not found')
    try:
        if d.is_kernel_driver_active(intf.bInterfaceNumber):
            try: d.detach_kernel_driver(intf.bInterfaceNumber)
            except Exception: pass
    except Exception: pass
    usb.util.claim_interface(d, intf.bInterfaceNumber)
    return d, intf

def main():
    # 1) Пнул CDC на START
    # Сначала пробуем нормальный CDC через /dev/ttyACM*,
    # при ошибке прав/отсутствии — пробуем PyUSB CDC (bulk OUT)
    poked = False
    try:
        poke_cdc(cfg_block_hz=300, windows=(0,0,0,0), start=True)
        print('[cdc] START via CDC (tty) sent')
        poked = True
    except Exception as e:
        print(f"[cdc] tty failed: {e}; try PyUSB")
        try:
            poke_cdc_via_pyusb(cfg_block_hz=300, windows=(0,0,0,0), start=True)
            print('[cdc] START via CDC (PyUSB) sent')
            poked = True
        except Exception as e2:
            print(f"[cdc] PyUSB CDC failed: {e2}")

    # 2) Читаем Vendor и печатаем первые 100 int16 из кадров
    d,intf = open_vendor()
    try:
        # Also send Vendor START (harmless if already started)
        try: d.write(EP_OUT, bytes([0x20]), timeout=500)
        except Exception: pass
        MAGIC_LE=b"\x5A\xA5"; buf=bytearray(); printed=0; t0=time.time()
        while printed<4 and time.time()-t0<6.0:
            try:
                data=bytes(d.read(EP_IN, 4096, timeout=700))
            except usb.core.USBError as e:
                if getattr(e,'errno',None)==110: continue
                raise
            if not data: continue
            if data[:4]==b'STAT':
                print('STAT:', data[:16].hex())
                if len(data)>64:
                    data=data[64:]
                else:
                    continue
            buf.extend(data)
            while True:
                if len(buf)<HDR_SIZE: break
                if not (buf[0]==0x5A and buf[1]==0xA5):
                    idx=buf.find(MAGIC_LE)
                    if idx==-1:
                        del buf[:max(0, len(buf)-1)]; break
                    else:
                        del buf[:idx]
                        if len(buf)<HDR_SIZE: break
                try:
                    (magic,ver,flags,seq,ts,total_samples,zc,z1o,z1l,r1,r2,crc)=struct.unpack(HDR_FMT, bytes(buf[:HDR_SIZE]))
                except struct.error:
                    break
                if magic!=MAGIC:
                    del buf[0]; continue
                frame_len = HDR_SIZE + int(total_samples)*2
                if len(buf)<frame_len: break
                payload = bytes(buf[HDR_SIZE:frame_len]); del buf[:frame_len]
                if (flags & 0x80) and total_samples==8:
                    print(f"[TEST] seq={seq} total=8")
                    continue
                ch = 0 if (flags & 0x01) else (1 if (flags & 0x02) else -1)
                if ch<0: continue
                n=min(100, len(payload)//2)
                vals=list(struct.unpack('<'+'h'*n, payload[:n*2]))
                print(f"FRAME seq={seq} ch={ch} total={total_samples} n_print={n}")
                print(' '.join(str(x) for x in vals))
                printed+=1
        if printed==0:
            print('[WARN] no frames read after CDC+Vendor START')
            # Попробуем мягкий USB reset и повторим
            try:
                print('[reset] resetting USB device...')
                d.reset()
            except Exception as e:
                print(f'[reset] err: {e}')
            # дать устройству время переинициализироваться
            time.sleep(2.5)
            # повторный CDC-пинок через PyUSB (без tty, чтобы не упереться в права)
            try:
                poke_cdc_via_pyusb(cfg_block_hz=300, windows=(0,0,0,0), start=True)
                print('[cdc] re-START via CDC (PyUSB) sent')
            except Exception as e:
                print(f'[cdc] re-poke failed: {e}')
            # переоткроем vendor и попробуем ещё раз коротко
            try:
                d2,intf2 = open_vendor()
                try:
                    try: d2.write(EP_OUT, bytes([0x20]), timeout=500)
                    except Exception: pass
                    MAGIC_LE=b"\x5A\xA5"; buf=bytearray(); t1=time.time(); printed2=0
                    while printed2<2 and time.time()-t1<4.0:
                        try:
                            data=bytes(d2.read(EP_IN, 4096, timeout=700))
                        except usb.core.USBError as e:
                            if getattr(e,'errno',None)==110: continue
                            raise
                        if not data: continue
                        if data[:4]==b'STAT':
                            if len(data)>64: data=data[64:]
                            else: continue
                        buf.extend(data)
                        if len(buf)>=HDR_SIZE and buf[0]==0x5A and buf[1]==0xA5:
                            try:
                                (magic,ver,flags,seq,ts,total_samples,zc,z1o,z1l,r1,r2,crc)=struct.unpack(HDR_FMT, bytes(buf[:HDR_SIZE]))
                            except struct.error:
                                break
                            if magic==MAGIC:
                                frame_len = HDR_SIZE + int(total_samples)*2
                                if len(buf) >= frame_len:
                                    payload = bytes(buf[HDR_SIZE:frame_len]); del buf[:frame_len]
                                    if (flags & 0x80) and total_samples==8: continue
                                    ch = 0 if (flags & 0x01) else (1 if (flags & 0x02) else -1)
                                    if ch<0: continue
                                    n=min(100, len(payload)//2)
                                    vals=list(struct.unpack('<'+'h'*n, payload[:n*2]))
                                    print(f"FRAME seq={seq} ch={ch} total={total_samples} n_print={n}")
                                    print(' '.join(str(x) for x in vals))
                                    printed2 += 1
                    if printed2==0:
                        return 1
                    else:
                        return 0
                finally:
                    try: usb.util.release_interface(d2, intf2.bInterfaceNumber)
                    except Exception: pass
                    try: usb.util.dispose_resources(d2)
                    except Exception: pass
            except Exception:
                return 1
        return 0
    finally:
        try: usb.util.release_interface(d, intf.bInterfaceNumber)
        except Exception: pass
        try: usb.util.dispose_resources(d)
        except Exception: pass

if __name__=='__main__':
    sys.exit(main())
