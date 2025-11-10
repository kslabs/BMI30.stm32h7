#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверка непрерывности ADC/DMA:
- Отправим START
- Мониторим DMA счётчики каждые 0.5 сек в течение 10 сек
- Должны видеть непрерывный рост dma_done_A и dma_done_B

Цель: понять останавливается ли DMA после 1 прерывания
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

def read_status_v3(dev, intf_num):
    """Чтение STATUS v3 (84 байта)"""
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
        
        # Парсим основные поля (v2 совместимость)
        sig = bytes(ret[0:4]).decode('ascii', errors='ignore')
        version = ret[4]
        sent_A = struct.unpack('<I', bytes(ret[16:20]))[0]
        sent_B = struct.unpack('<I', bytes(ret[20:24]))[0]
        dma_done_A = struct.unpack('<I', bytes(ret[36:40]))[0]
        dma_done_B = struct.unpack('<I', bytes(ret[40:44]))[0]
        frame_wr_seq = struct.unpack('<I', bytes(ret[44:48]))[0]
        
        # v3 расширенные поля (zero_buffers)
        zero_A = 0
        zero_B = 0
        if len(ret) >= 84:
            # Поля в STATUS v3:
            # 48..51: zero_buffers_A (4 байта)
            # 52..55: zero_buffers_A high (4 байта)
            # 56..59: zero_buffers_B (4 байта)
            # 60..63: zero_buffers_B high (4 байта)
            # Но скорее всего uint32_t, берём младшие 4 байта
            zero_A = struct.unpack('<I', bytes(ret[76:80]))[0] if len(ret) >= 80 else 0
            zero_B = struct.unpack('<I', bytes(ret[80:84]))[0] if len(ret) >= 84 else 0
        
        return {
            'sig': sig,
            'version': version,
            'sent_A': sent_A,
            'sent_B': sent_B,
            'dma_done_A': dma_done_A,
            'dma_done_B': dma_done_B,
            'frame_wr_seq': frame_wr_seq,
            'zero_A': zero_A,
            'zero_B': zero_B,
        }
    except usb.core.USBError as e:
        print(f"USB Error: {e}")
        return None

def send_cmd(dev, intf_num, cmd_byte):
    """Отправка команды через CTRL OUT"""
    try:
        dev.ctrl_transfer(
            bmRequestType=0x41,
            bRequest=cmd_byte,
            wValue=0,
            wIndex=intf_num,
            data_or_wLength=b'',
            timeout=1000
        )
        return True
    except usb.core.USBError as e:
        print(f"Command {cmd_byte:#x} failed: {e}")
        return False

def main():
    print("=== Тест непрерывности ADC/DMA ===")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"Устройство {VID:#06x}:{PID:#06x} не найдено")
        sys.exit(1)
    
    print(f"Найдено: {dev}")
    
    # Интерфейс 2 - vendor
    intf_num = 2
    
    # Чтение базового статуса
    print("\n--- Базовый статус ---")
    st = read_status_v3(dev, intf_num)
    if st:
        print(f"SIG={st['sig']!r} ver={st['version']}")
        print(f"DMA A={st['dma_done_A']}, B={st['dma_done_B']} (diff={st['dma_done_B']-st['dma_done_A']})")
        print(f"frame_wr_seq={st['frame_wr_seq']}, sent A={st['sent_A']}, B={st['sent_B']}")
        print(f"zero_A={st['zero_A']}, zero_B={st['zero_B']}")
        base_A = st['dma_done_A']
        base_B = st['dma_done_B']
    else:
        print("Не удалось прочитать STATUS!")
        sys.exit(1)
    
    # Отправка START
    print("\n--- Отправка START ---")
    if not send_cmd(dev, intf_num, CMD_START):
        sys.exit(1)
    
    time.sleep(0.5)  # Даём время на запуск
    
    # Мониторинг DMA в течение 10 секунд с интервалом 0.5 сек
    print("\n--- Мониторинг DMA (10 сек, интервал 0.5с) ---")
    print(f"{'Time':<6} {'DMA_A':<8} {'ΔA':<6} {'DMA_B':<8} {'ΔB':<6} {'diff':<6} {'sent_A':<7} {'sent_B':<7}")
    print("-" * 75)
    
    prev_A = base_A
    prev_B = base_B
    start_time = time.time()
    
    for i in range(20):  # 20 проверок по 0.5с = 10 секунд
        time.sleep(0.5)
        elapsed = time.time() - start_time
        
        st = read_status_v3(dev, intf_num)
        if not st:
            print("Ошибка чтения STATUS!")
            break
        
        delta_A = st['dma_done_A'] - prev_A
        delta_B = st['dma_done_B'] - prev_B
        diff = st['dma_done_B'] - st['dma_done_A']
        
        print(f"{elapsed:5.1f}s {st['dma_done_A']:<8} {delta_A:<6} {st['dma_done_B']:<8} {delta_B:<6} {diff:<6} {st['sent_A']:<7} {st['sent_B']:<7}")
        
        prev_A = st['dma_done_A']
        prev_B = st['dma_done_B']
        
        # Если DMA не растёт в течение 2 циклов - остановились!
        if i > 2 and delta_A == 0 and delta_B == 0:
            print("\n❌ DMA STOPPED! Нет новых прерываний!")
            break
    
    # Финальный статус
    print("\n--- Финальный статус ---")
    st = read_status_v3(dev, intf_num)
    if st:
        print(f"DMA A={st['dma_done_A']}, B={st['dma_done_B']} (diff={st['dma_done_B']-st['dma_done_A']})")
        print(f"frame_wr_seq={st['frame_wr_seq']}, sent A={st['sent_A']}, B={st['sent_B']}")
        print(f"zero_A={st['zero_A']}, zero_B={st['zero_B']}")
        
        total_A = st['dma_done_A'] - base_A
        total_B = st['dma_done_B'] - base_B
        print(f"\nВсего новых DMA прерываний: A=+{total_A}, B=+{total_B}")
        
        if total_A > 10 and total_B > 10:
            print("✅ DMA работает непрерывно!")
        else:
            print("❌ DMA остановился после первых прерываний!")
    
    # STOP
    print("\n--- STOP ---")
    send_cmd(dev, intf_num, CMD_STOP)
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
