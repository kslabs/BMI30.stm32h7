#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Детальная диагностика DMA: почему разное количество прерываний A и B?
"""
import sys
import usb.core
import usb.util
import time
import struct

VID = 0xCAFE
PID = 0x4001
CMD_START = 0x20
CMD_STOP = 0x21
CMD_GET_STATUS = 0x30

def read_detailed_status(dev, intf_num):
    """Чтение STATUS с детальным разбором"""
    try:
        ret = dev.ctrl_transfer(
            bmRequestType=0xC1,
            bRequest=CMD_GET_STATUS,
            wValue=0,
            wIndex=intf_num,
            data_or_wLength=128,
            timeout=1000
        )
        
        if len(ret) < 64:
            return None
        
        # Парсинг полей
        sig = bytes(ret[0:4]).decode('ascii', errors='ignore')
        version = ret[4]
        cur_samples = struct.unpack('<H', bytes(ret[6:8]))[0]
        sent_A = struct.unpack('<I', bytes(ret[16:20]))[0]
        sent_B = struct.unpack('<I', bytes(ret[20:24]))[0]
        tx_cplt = struct.unpack('<I', bytes(ret[24:28]))[0]
        partial_abort = struct.unpack('<I', bytes(ret[28:32]))[0]
        size_mismatch = struct.unpack('<I', bytes(ret[32:36]))[0]
        dma_done_A = struct.unpack('<I', bytes(ret[36:40]))[0]
        dma_done_B = struct.unpack('<I', bytes(ret[40:44]))[0]
        frame_wr_seq = struct.unpack('<I', bytes(ret[44:48]))[0]
        flags_runtime = struct.unpack('<H', bytes(ret[48:50]))[0]
        flags2 = struct.unpack('<H', bytes(ret[50:52]))[0]
        
        # v3 расширение
        zero_A = 0
        zero_B = 0
        if len(ret) >= 84:
            zero_A = struct.unpack('<I', bytes(ret[76:80]))[0]
            zero_B = struct.unpack('<I', bytes(ret[80:84]))[0]
        
        return {
            'sig': sig,
            'version': version,
            'cur_samples': cur_samples,
            'sent_A': sent_A,
            'sent_B': sent_B,
            'tx_cplt': tx_cplt,
            'partial_abort': partial_abort,
            'size_mismatch': size_mismatch,
            'dma_done_A': dma_done_A,
            'dma_done_B': dma_done_B,
            'frame_wr_seq': frame_wr_seq,
            'flags_runtime': flags_runtime,
            'flags2': flags2,
            'zero_A': zero_A,
            'zero_B': zero_B,
            'status_len': len(ret)
        }
    except Exception as e:
        print(f"[ERR] {e}")
        return None

def send_cmd(dev, intf_num, cmd, wValue=0):
    """Отправка команды"""
    try:
        dev.ctrl_transfer(0x41, cmd, wValue, intf_num, None, 1000)
        return True
    except:
        return False

def main():
    print("=== Детальная диагностика DMA синхронизации ===\n")
    
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("[ERR] Device not found")
        return
    
    try:
        dev.set_configuration()
    except:
        pass
    
    cfg = dev.get_active_configuration()
    intf = None
    for i in cfg:
        eps = [ep.bEndpointAddress for ep in i]
        if 0x03 in eps and 0x83 in eps:
            intf = i
            break
    
    if not intf:
        print("[ERR] Interface not found")
        return
    
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except:
        pass
    
    usb.util.claim_interface(dev, intf)
    dev.set_interface_altsetting(intf.bInterfaceNumber, 1)
    
    print("[1] Чтение базового статуса...")
    s0 = read_detailed_status(dev, intf.bInterfaceNumber)
    if s0:
        print(f"    DMA A: {s0['dma_done_A']:,} | DMA B: {s0['dma_done_B']:,}")
        print(f"    Разница: {abs(s0['dma_done_A'] - s0['dma_done_B']):,}")
    
    print("\n[2] Отправка START (profile=0, async=1, chmode=0)...")
    wValue = 1 | (1 << 1) | (0 << 8) | (0 << 10)
    if not send_cmd(dev, intf.bInterfaceNumber, CMD_START, wValue):
        print("[ERR] START failed")
        return
    
    print("[OK] START sent")
    
    # Периодический мониторинг
    print("\n[3] Мониторинг DMA в течение 5 секунд...\n")
    print(f"{'Time':>6} {'DMA_A':>10} {'DMA_B':>10} {'Diff':>8} {'ΔA':>8} {'ΔB':>8} {'frame_wr':>10}")
    print("-" * 70)
    
    start_time = time.time()
    prev_a = s0['dma_done_A']
    prev_b = s0['dma_done_B']
    
    for i in range(10):
        time.sleep(0.5)
        s = read_detailed_status(dev, intf.bInterfaceNumber)
        if not s:
            print("[WARN] Failed to read status")
            continue
        
        elapsed = time.time() - start_time
        diff = abs(s['dma_done_A'] - s['dma_done_B'])
        delta_a = s['dma_done_A'] - prev_a
        delta_b = s['dma_done_B'] - prev_b
        prev_a = s['dma_done_A']
        prev_b = s['dma_done_B']
        
        print(f"{elapsed:5.1f}s {s['dma_done_A']:10,} {s['dma_done_B']:10,} {diff:8,} {delta_a:8,} {delta_b:8,} {s['frame_wr_seq']:10,}")
    
    print("\n[4] Отправка STOP...")
    send_cmd(dev, intf.bInterfaceNumber, CMD_STOP)
    time.sleep(0.2)
    
    print("\n[5] Финальный статус:")
    sf = read_detailed_status(dev, intf.bInterfaceNumber)
    if sf:
        print(f"    DMA завершений A: {sf['dma_done_A']:,}")
        print(f"    DMA завершений B: {sf['dma_done_B']:,}")
        print(f"    Разница A-B: {sf['dma_done_A'] - sf['dma_done_B']:+,}")
        print(f"    frame_wr_seq: {sf['frame_wr_seq']:,}")
        print(f"    Отправлено A: {sf['sent_A']}")
        print(f"    Отправлено B: {sf['sent_B']}")
        print(f"    Нулевых A: {sf['zero_A']}")
        print(f"    Нулевых B: {sf['zero_B']}")
        
        print("\n=== АНАЛИЗ ===")
        diff_final = abs(sf['dma_done_A'] - sf['dma_done_B'])
        if diff_final <= 2:
            print(f"✅ Разница {diff_final} буфера - НОРМАЛЬНО (синхронная работа)")
        elif diff_final <= 100:
            print(f"⚠️  Разница {diff_final} буферов - небольшая десинхронизация")
        else:
            print(f"❌ Разница {diff_final} буферов - ПРОБЛЕМА!")
            if sf['dma_done_B'] > sf['dma_done_A']:
                print("   ADC2 (канал B) обгоняет ADC1 (канал A)")
                print("   Возможные причины:")
                print("   - ADC2 стартовал раньше")
                print("   - Разные настройки DMA приоритета")
                print("   - ADC1 пропускает срабатывания")
            else:
                print("   ADC1 (канал A) обгоняет ADC2 (канал B)")
                print("   Возможные причины:")
                print("   - ADC1 стартовал раньше")
                print("   - Прерывания ADC2 заблокированы")

if __name__ == '__main__':
    main()
