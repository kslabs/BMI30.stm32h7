#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест заполнения буферов: START → 10 сек → STOP → подсчёт нулевых буферов
Проверяет работает ли DMA и заполняются ли буферы реальными данными
"""
import sys
import usb.core
import usb.util
import time
import struct

VID = 0xCAFE
PID = 0x4001
OUT_EP = 0x03
IN_EP = 0x83

CMD_START = 0x20
CMD_STOP = 0x21
CMD_GET_STATUS = 0x30

def send_start(dev, intf_num, profile=0, async_mode=1, chmode=0):
    """Отправка START через control transfer"""
    try:
        full_mode = 1
        wValue = (async_mode & 1) | ((full_mode & 1) << 1) | ((chmode & 3) << 8) | ((profile & 3) << 10)
        
        dev.ctrl_transfer(
            bmRequestType=0x41,
            bRequest=CMD_START,
            wValue=wValue,
            wIndex=intf_num,
            data_or_wLength=None,
            timeout=1000
        )
        return True
    except Exception as e:
        print(f"[ERR] START failed: {e}")
        return False

def send_stop(dev, intf_num):
    """Отправка STOP"""
    try:
        dev.ctrl_transfer(
            bmRequestType=0x41,
            bRequest=CMD_STOP,
            wValue=0,
            wIndex=intf_num,
            data_or_wLength=None,
            timeout=1000
        )
        return True
    except Exception as e:
        print(f"[ERR] STOP failed: {e}")
        return False

def read_status_v3(dev, intf_num):
    """Чтение STATUS v3 с извлечением счётчиков нулевых буферов"""
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
            print(f"[ERR] Status too short: {len(ret)} bytes")
            return None
        
        # Парсинг базовых полей (v1)
        sent_A = struct.unpack('<I', bytes(ret[16:20]))[0]
        sent_B = struct.unpack('<I', bytes(ret[20:24]))[0]
        dma_done_A = struct.unpack('<I', bytes(ret[36:40]))[0]
        dma_done_B = struct.unpack('<I', bytes(ret[40:44]))[0]
        frame_wr_seq = struct.unpack('<I', bytes(ret[44:48]))[0]
        
        # v3 расширение (84 bytes)
        zero_buffers_A = 0
        zero_buffers_B = 0
        if len(ret) >= 84:
            zero_buffers_A = struct.unpack('<I', bytes(ret[76:80]))[0]
            zero_buffers_B = struct.unpack('<I', bytes(ret[80:84]))[0]
        
        return {
            'sent_A': sent_A,
            'sent_B': sent_B,
            'dma_done_A': dma_done_A,
            'dma_done_B': dma_done_B,
            'frame_wr_seq': frame_wr_seq,
            'zero_buffers_A': zero_buffers_A,
            'zero_buffers_B': zero_buffers_B,
            'status_len': len(ret)
        }
        
    except Exception as e:
        print(f"[ERR] Read status failed: {e}")
        return None

def main():
    test_duration_sec = 10
    profile = 0  # Profile 0: 1360 samples @ 200 Hz
    
    print(f"=== Тест заполнения буферов (DMA диагностика) ===")
    print(f"Профиль: {profile}")
    print(f"Длительность: {test_duration_sec} сек")
    print(f"Цель: проверить заполняются ли буферы ADC ненулевыми данными\n")
    
    # Найти устройство
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"[ERR] Device not found")
        sys.exit(1)
    
    try:
        dev.set_configuration()
    except:
        pass
    
    # Найти vendor интерфейс
    cfg = dev.get_active_configuration()
    intf = None
    for i in cfg:
        eps = [ep.bEndpointAddress for ep in i]
        if OUT_EP in eps and IN_EP in eps:
            intf = i
            break
    
    if intf is None:
        print("[ERR] Vendor interface not found")
        sys.exit(2)
    
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except:
        pass
    
    usb.util.claim_interface(dev, intf)
    dev.set_interface_altsetting(intf.bInterfaceNumber, 1)
    
    print(f"[OK] Device configured\n")
    
    # Прочитать статус ДО START (для базовой линии)
    print("Чтение статуса перед START...")
    status_before = read_status_v3(dev, intf.bInterfaceNumber)
    if status_before:
        print(f"  DMA A/B: {status_before['dma_done_A']}/{status_before['dma_done_B']}")
        print(f"  Sent A/B: {status_before['sent_A']}/{status_before['sent_B']}")
        print(f"  Zero buffers A/B: {status_before['zero_buffers_A']}/{status_before['zero_buffers_B']}")
    
    # Отправить START
    print(f"\n>> Отправка START (profile={profile})...")
    if not send_start(dev, intf.bInterfaceNumber, profile=profile):
        print("[ERR] Failed to start")
        sys.exit(3)
    
    print(f"[OK] START отправлен")
    print(f"[INFO] Ожидание {test_duration_sec} секунд...")
    print(f"[INFO] STM32 должен заполнять буферы через DMA...")
    
    # Отсчёт времени
    for i in range(test_duration_sec):
        time.sleep(1)
        print(f"  {i+1}/{test_duration_sec} сек", end='\r')
    
    print(f"\n\n>> Отправка STOP...")
    if not send_stop(dev, intf.bInterfaceNumber):
        print("[WARN] STOP failed")
    else:
        print("[OK] STOP отправлен")
    
    time.sleep(0.2)  # Пауза для обработки STOP
    
    # Прочитать финальный статус
    print("\nЧтение финального статуса...")
    status_after = read_status_v3(dev, intf.bInterfaceNumber)
    
    if not status_after:
        print("[ERR] Failed to read final status")
        sys.exit(4)
    
    # Анализ результатов
    print("\n" + "=" * 80)
    print("=== РЕЗУЛЬТАТЫ ДИАГНОСТИКИ ===")
    print("=" * 80)
    
    print(f"\n1. DMA АКТИВНОСТЬ:")
    print(f"   DMA завершений A: {status_after['dma_done_A']:,}")
    print(f"   DMA завершений B: {status_after['dma_done_B']:,}")
    print(f"   frame_wr_seq:     {status_after['frame_wr_seq']:,}")
    
    dma_active = (status_after['dma_done_A'] > 100 and status_after['dma_done_B'] > 100)
    print(f"   >>> DMA работает: {'✅ ДА' if dma_active else '❌ НЕТ'}")
    
    print(f"\n2. ОТПРАВКА ДАННЫХ:")
    print(f"   Отправлено A: {status_after['sent_A']}")
    print(f"   Отправлено B: {status_after['sent_B']}")
    
    transmission_works = (status_after['sent_A'] > 0 or status_after['sent_B'] > 0)
    print(f"   >>> Передача работает: {'✅ ДА' if transmission_works else '❌ НЕТ'}")
    
    print(f"\n3. НУЛЕВЫЕ БУФЕРЫ (v3 диагностика):")
    print(f"   Счётчик нулевых буферов A: {status_after['zero_buffers_A']}")
    print(f"   Счётчик нулевых буферов B: {status_after['zero_buffers_B']}")
    
    if status_after['status_len'] < 84:
        print(f"   ⚠️  STATUS только {status_after['status_len']} байт (v3 требует 84)")
        print(f"   ⚠️  Перепрошейте устройство с VND_STATUS_MAX=84")
    else:
        total_zero = status_after['zero_buffers_A'] + status_after['zero_buffers_B']
        total_dma = status_after['dma_done_A'] + status_after['dma_done_B']
        zero_percent = 100 * total_zero / total_dma if total_dma > 0 else 0
        
        print(f"   Всего DMA завершений: {total_dma:,}")
        print(f"   Всего нулевых буферов: {total_zero:,}")
        print(f"   Процент нулевых: {zero_percent:.2f}%")
        
        if total_zero == 0:
            print(f"   >>> ✅ ОТЛИЧНО! Все буферы содержат данные (нулевых нет)")
        elif zero_percent < 5:
            print(f"   >>> ⚠️  Мало нулевых буферов ({zero_percent:.1f}%), возможны единичные глитчи")
        else:
            print(f"   >>> ❌ ПРОБЛЕМА! {zero_percent:.1f}% нулевых буферов")
            print(f"   >>> Вероятные причины:")
            print(f"       - ADC не подключен к реальным входам (PA6/PC4 не подключены)")
            print(f"       - Триггер TIM15 TRGO не генерируется")
            print(f"       - DMA переписывает нулевые области памяти")
    
    print("\n" + "=" * 80)
    print("=== РЕКОМЕНДАЦИИ ===")
    
    if not dma_active:
        print("❌ DMA не работает - проверьте инициализацию ADC/DMA/TIM15")
    elif not transmission_works:
        print("❌ Передача не работает - проверьте vendor stream task и USB EP")
    elif status_after['zero_buffers_A'] > 0 or status_after['zero_buffers_B'] > 0:
        print("⚠️  DMA работает, но буферы нулевые:")
        print("   1. Проверьте подключение входов PA6 (ADC1_INP3) и PC4 (ADC2_INP4)")
        print("   2. Проверьте генерацию триггера TIM15 TRGO (осциллограф)")
        print("   3. Проверьте что ADC реально читает входы, а не внутренний 0V")
    else:
        print("✅ Всё работает корректно!")
    
    print("=" * 80)

if __name__ == '__main__':
    main()
