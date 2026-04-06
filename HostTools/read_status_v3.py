#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Чтение статуса v3 с отображением счётчиков нулевых буферов ADC
"""
import sys
import usb.core
import struct

VID = 0xCAFE
PID = 0x4001
OUT_EP = 0x03
IN_EP = 0x83

CMD_GET_STATUS = 0x30
VND_STFLAG_TX_ENABLED = 0x0010

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"[ERR] Device not found VID=0x{VID:04X} PID=0x{PID:04X}")
        sys.exit(1)
    
    try:
        dev.set_configuration()
    except:
        pass
    
    # Найти интерфейс с нужными endpoints
    cfg = dev.get_active_configuration()
    intf = None
    for i in cfg:
        eps = [ep.bEndpointAddress for ep in i]
        if OUT_EP in eps and IN_EP in eps:
            intf = i
            break
    
    if intf is None:
        print("[ERR] Interface not found")
        sys.exit(2)
    
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except:
        pass
    
    usb.util.claim_interface(dev, intf)
    
    # Установить alt=1
    dev.set_interface_altsetting(intf.bInterfaceNumber, 1)
    
    # Отправить команду GET_STATUS через control transfer
    try:
        ret = dev.ctrl_transfer(
            bmRequestType=0xC1,  # device-to-host, class, interface
            bRequest=CMD_GET_STATUS,
            wValue=0,
            wIndex=intf.bInterfaceNumber,
            data_or_wLength=128,
            timeout=1000
        )
        
        if len(ret) < 64:
            print(f"[ERR] Status too short: {len(ret)} bytes")
            sys.exit(3)
        
        # Парсинг v1 (64 bytes)
        sig = bytes(ret[0:4]).decode('ascii', errors='ignore')
        version = ret[4]
        reserved0 = ret[5]
        cur_samples = struct.unpack('<H', bytes(ret[6:8]))[0]
        frame_bytes = struct.unpack('<H', bytes(ret[8:10]))[0]
        test_frames = struct.unpack('<H', bytes(ret[10:12]))[0]
        produced_seq = struct.unpack('<I', bytes(ret[12:16]))[0]
        sent0 = struct.unpack('<I', bytes(ret[16:20]))[0]
        sent1 = struct.unpack('<I', bytes(ret[20:24]))[0]
        dbg_tx_cplt = struct.unpack('<I', bytes(ret[24:28]))[0]
        dbg_partial = struct.unpack('<I', bytes(ret[28:32]))[0]
        dbg_size_mismatch = struct.unpack('<I', bytes(ret[32:36]))[0]
        dma_done0 = struct.unpack('<I', bytes(ret[36:40]))[0]
        dma_done1 = struct.unpack('<I', bytes(ret[40:44]))[0]
        frame_wr_seq = struct.unpack('<I', bytes(ret[44:48]))[0]
        flags_runtime = struct.unpack('<H', bytes(ret[48:50]))[0]
        flags2 = struct.unpack('<H', bytes(ret[50:52]))[0]
        sending_ch = ret[52]
        reserved2 = ret[53]
        pair_idx = struct.unpack('<H', bytes(ret[54:56]))[0]
        last_tx_len = struct.unpack('<H', bytes(ret[56:58]))[0]
        cur_stream_seq = struct.unpack('<I', bytes(ret[58:62]))[0]
        reserved3 = struct.unpack('<H', bytes(ret[62:64]))[0]
        
        print(f"=== STATUS v{version} ===")
        print(f"Signature: {sig}")
        print(f"cur_samples: {cur_samples}, frame_bytes: {frame_bytes}")
        print(f"test_frames: {test_frames}, produced_seq: {produced_seq}")
        print(f"sent A={sent0}, B={sent1}")
        print(f"TxCplt: {dbg_tx_cplt}, partial_abort: {dbg_partial}, size_mismatch: {dbg_size_mismatch}")
        print(f"DMA done A={dma_done0}, B={dma_done1}")
        print(f"frame_wr_seq: {frame_wr_seq}")
        print(f"flags_runtime: 0x{flags_runtime:04X}, flags2: 0x{flags2:04X}")
        print(f"tx_enabled: {'yes' if (flags_runtime & VND_STFLAG_TX_ENABLED) else 'no'}")
        print(f"sending_ch: {sending_ch} (0=A,1=B,255=none)")
        print(f"last_tx_len: {last_tx_len}, cur_stream_seq: {cur_stream_seq}")
        
        # v2 extension (76 bytes)
        if len(ret) >= 76:
            stage_alt1_ms = struct.unpack('<I', bytes(ret[64:68]))[0]
            stage_start_ms = struct.unpack('<I', bytes(ret[68:72]))[0]
            stage_first_frame_ms = struct.unpack('<I', bytes(ret[72:76]))[0]
            print(f"\n=== v2 STAGE ===")
            print(f"stage_alt1_ms: {stage_alt1_ms}")
            print(f"stage_start_ms: {stage_start_ms}")
            print(f"stage_first_frame_ms: {stage_first_frame_ms}")
        
        # v3 extension (84 bytes) - ДИАГНОСТИКА НУЛЕВЫХ БУФЕРОВ
        if len(ret) >= 84:
            ch_zero_buffers_A = struct.unpack('<I', bytes(ret[76:80]))[0]
            ch_zero_buffers_B = struct.unpack('<I', bytes(ret[80:84]))[0]
            print(f"\n=== v3 ДИАГНОСТИКА ===")
            print(f"ch_zero_buffers A: {ch_zero_buffers_A}")
            print(f"ch_zero_buffers B: {ch_zero_buffers_B}")
            print(f"Процент нулевых буферов A: {100*ch_zero_buffers_A/(sent0+1e-9):.1f}%")
            print(f"Процент нулевых буферов B: {100*ch_zero_buffers_B/(sent1+1e-9):.1f}%")
        
    except Exception as e:
        print(f"[ERR] Control transfer failed: {e}")
        sys.exit(4)
    
    print("\n[OK] Status read successfully")

if __name__ == '__main__':
    main()
