#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест: DMA счётчики должны расти ВСЕГДА, даже без команды START!
ADC/DMA работают с момента загрузки в main().
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
    try:
        ret = dev.ctrl_transfer(
            bmRequestType=0xC1,
            bRequest=CMD_GET_STATUS,
            wValue=0,
            wIndex=intf_num,
            data_or_wLength=128,
            timeout=1000
        )
        
        if len(ret) < 44:
            return None, None
        
        dma_A = struct.unpack('<I', bytes(ret[36:40]))[0]
        dma_B = struct.unpack('<I', bytes(ret[40:44]))[0]
        return dma_A, dma_B
    except usb.core.USBError as e:
        print(f"USB Error: {e}")
        return None, None

def main():
    print("=== Тест: DMA работает ВСЕГДА (без START) ===\n")
    
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"Устройство {VID:#06x}:{PID:#06x} не найдено!")
        sys.exit(1)
    
    intf_num = 2  # Vendor interface
    
    # Базовые счётчики
    print("Базовые счётчики (без START):")
    dma_A_base, dma_B_base = read_dma_counters(dev, intf_num)
    if dma_A_base is None:
        print("Ошибка чтения STATUS!")
        sys.exit(1)
    
    print(f"  DMA A = {dma_A_base}")
    print(f"  DMA B = {dma_B_base}")
    print(f"  diff  = {dma_B_base - dma_A_base}")
    
    # Ждём 5 секунд
    print("\n⏳ Ожидание 5 секунд...\n")
    time.sleep(5)
    
    # Повторное чтение
    print("После 5 секунд (без START):")
    dma_A_new, dma_B_new = read_dma_counters(dev, intf_num)
    if dma_A_new is None:
        print("Ошибка чтения STATUS!")
        sys.exit(1)
    
    print(f"  DMA A = {dma_A_new}  (Δ = +{dma_A_new - dma_A_base})")
    print(f"  DMA B = {dma_B_new}  (Δ = +{dma_B_new - dma_B_base})")
    print(f"  diff  = {dma_B_new - dma_A_new}")
    
    # Проверка
    delta_A = dma_A_new - dma_A_base
    delta_B = dma_B_new - dma_B_base
    
    print(f"\n{'='*50}")
    if delta_A > 800 and delta_B > 800:  # За 5 сек @ 200Hz ожидаем ~1000 прерываний
        print("✅ SUCCESS! DMA работает непрерывно!")
        print(f"   Скорость: ~{delta_A/5:.0f} Hz (A), ~{delta_B/5:.0f} Hz (B)")
    elif delta_A > 0 or delta_B > 0:
        print("⚠️  WARNING: DMA работает, но медленно!")
        print(f"   Ожидали ~1000, получили A={delta_A}, B={delta_B}")
    else:
        print("❌ FAIL: DMA НЕ работает (счётчики не растут)!")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
