#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точное измерение частоты DMA прерываний
"""
import sys
import usb.core
import struct
import time

VID = 0xCAFE
PID = 0x4001
CMD_GET_STATUS = 0x30

def read_dma_counters(dev, intf_num):
    """Чтение DMA счётчиков из STATUS"""
    ret = dev.ctrl_transfer(0xC1, CMD_GET_STATUS, 0, intf_num, 128, timeout=1000)
    if len(ret) < 44:
        return None, None
    dma_A = struct.unpack('<I', bytes(ret[36:40]))[0]
    dma_B = struct.unpack('<I', bytes(ret[40:44]))[0]
    return dma_A, dma_B

def main():
    print("=== Точное измерение частоты DMA ===\n")
    
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"Устройство не найдено!")
        sys.exit(1)
    
    intf_num = 2
    
    # Измерение на 10 секундах для точности
    print("Базовые счётчики:")
    dma_A_base, dma_B_base = read_dma_counters(dev, intf_num)
    print(f"  DMA A = {dma_A_base}")
    print(f"  DMA B = {dma_B_base}")
    
    t_start = time.time()
    
    print("\n⏳ Точное измерение 10 секунд...\n")
    time.sleep(10.0)
    
    t_end = time.time()
    elapsed = t_end - t_start
    
    dma_A_new, dma_B_new = read_dma_counters(dev, intf_num)
    print(f"После {elapsed:.3f} секунд:")
    print(f"  DMA A = {dma_A_new}  (Δ = +{dma_A_new - dma_A_base})")
    print(f"  DMA B = {dma_B_new}  (Δ = +{dma_B_new - dma_B_base})")
    
    delta_A = dma_A_new - dma_A_base
    delta_B = dma_B_new - dma_B_base
    
    freq_A = delta_A / elapsed
    freq_B = delta_B / elapsed
    
    print(f"\n{'='*50}")
    print(f"Измеренная частота:")
    print(f"  Channel A: {freq_A:.2f} Hz")
    print(f"  Channel B: {freq_B:.2f} Hz")
    print(f"  Синхронизация: diff = {dma_B_new - dma_A_new}")
    
    expected = 200.0
    error_A = abs(freq_A - expected)
    error_B = abs(freq_B - expected)
    
    if error_A < 5 and error_B < 5:
        print(f"\n✅ Частота соответствует ожидаемой 200 Hz (погрешность ±5 Hz)")
    else:
        print(f"\n⚠️  Частота отличается от ожидаемой 200 Hz!")
        print(f"    Ошибка A: {error_A:.2f} Hz ({error_A/expected*100:.1f}%)")
        print(f"    Ошибка B: {error_B:.2f} Hz ({error_B/expected*100:.1f}%)")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
