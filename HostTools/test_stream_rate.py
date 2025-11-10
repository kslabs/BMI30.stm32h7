#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест потока: запускаем START и измеряем скорость приёма буферов
Подсчитываем нулевые кадры
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

def send_start(dev, intf_num, profile=0, async_mode=1, chmode=0):
    """Отправка START через control transfer"""
    try:
        # wValue: async_mode(bit0), full_mode(bit1), chmode(bits 8-9), profile(bits 10-11)
        full_mode = 1
        wValue = (async_mode & 1) | ((full_mode & 1) << 1) | ((chmode & 3) << 8) | ((profile & 3) << 10)
        
        ret = dev.ctrl_transfer(
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

def is_buffer_all_zeros(data, start_offset, samples):
    """Проверка буфера на все нули (пропускаем заголовок)"""
    for i in range(samples):
        val = struct.unpack('<H', data[start_offset + i*2 : start_offset + i*2 + 2])[0]
        if val != 0:
            return False
    return True

def main():
    # Параметры теста
    test_duration_sec = 10
    profile = 0  # Profile 0: 1360 samples @ 200 Hz
    read_timeout_ms = 500
    
    print(f"=== Тест скорости потока данных ===")
    print(f"Профиль: {profile} (ожидается ~200 буферов/сек)")
    print(f"Длительность: {test_duration_sec} сек\n")
    
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
    
    # Отправить START
    print(f"Отправка START (profile={profile})...")
    if not send_start(dev, intf.bInterfaceNumber, profile=profile):
        print("[ERR] Failed to start")
        sys.exit(3)
    
    print("[OK] START отправлен, ожидание данных...\n")
    time.sleep(0.5)  # Дать устройству время на запуск
    
    # Счётчики
    total_buffers = 0
    zero_buffers_A = 0
    zero_buffers_B = 0
    total_bytes = 0
    timeout_count = 0
    max_timeouts = 5
    
    start_time = time.time()
    last_report = start_time
    
    print("Чтение данных...")
    print(f"{'Время':<8} {'Буферы':<10} {'Скорость':<15} {'Нули A':<10} {'Нули B':<10} {'% нулей':<10}")
    print("-" * 80)
    
    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed > test_duration_sec:
                break
            
            try:
                # Читаем пакет
                data = dev.read(IN_EP, 8192, timeout=read_timeout_ms)
                
                if len(data) == 0:
                    continue
                
                total_bytes += len(data)
                
                # Простой подсчёт: если пакет >= 600 байт, считаем это одним буфером
                # Более точный анализ требует парсинга заголовка
                if len(data) >= 600:
                    total_buffers += 1
                    
                    # Проверка на нулевые данные (примерная)
                    # Пропускаем первые 32 байта (заголовок) и проверяем данные
                    if len(data) >= 700:
                        # Канал A: начинается после заголовка
                        samples_per_ch = (len(data) - 32) // 4  # 2 байта на семпл, 2 канала
                        if samples_per_ch > 100:
                            samples_per_ch = min(samples_per_ch, 680)  # ограничение
                            
                            # Проверяем канал A (первая половина данных после заголовка)
                            if is_buffer_all_zeros(data, 32, min(samples_per_ch, 300)):
                                zero_buffers_A += 1
                            
                            # Проверяем канал B (вторая половина)
                            offset_b = 32 + samples_per_ch * 2
                            if offset_b + 600 < len(data):
                                if is_buffer_all_zeros(data, offset_b, min(samples_per_ch, 300)):
                                    zero_buffers_B += 1
                
                timeout_count = 0  # Сброс счётчика таймаутов
                
            except usb.core.USBError as e:
                if e.errno == 110 or 'timeout' in str(e).lower():
                    timeout_count += 1
                    if timeout_count >= max_timeouts:
                        print(f"\n[WARN] {max_timeouts} последовательных таймаутов, поток остановился?")
                        break
                else:
                    print(f"\n[ERR] USB error: {e}")
                    break
            
            # Отчёт каждую секунду
            now = time.time()
            if now - last_report >= 1.0:
                elapsed = now - start_time
                rate = total_buffers / elapsed if elapsed > 0 else 0
                zero_total = zero_buffers_A + zero_buffers_B
                zero_percent = 100 * zero_total / (total_buffers * 2) if total_buffers > 0 else 0
                
                print(f"{elapsed:6.1f}с  {total_buffers:<10} {rate:6.1f} буф/с   {zero_buffers_A:<10} {zero_buffers_B:<10} {zero_percent:6.1f}%")
                last_report = now
    
    except KeyboardInterrupt:
        print("\n[INFO] Прервано пользователем")
    
    # Итоговая статистика
    elapsed = time.time() - start_time
    avg_rate = total_buffers / elapsed if elapsed > 0 else 0
    zero_total = zero_buffers_A + zero_buffers_B
    zero_percent = 100 * zero_total / (total_buffers * 2) if total_buffers > 0 else 0
    
    print("\n" + "=" * 80)
    print(f"=== ИТОГИ ===")
    print(f"Длительность:     {elapsed:.1f} сек")
    print(f"Получено буферов: {total_buffers}")
    print(f"Средняя скорость: {avg_rate:.1f} буф/сек (ожидалось ~200)")
    print(f"Всего байт:       {total_bytes}")
    print(f"Нулевых буферов:")
    print(f"  Канал A:        {zero_buffers_A}")
    print(f"  Канал B:        {zero_buffers_B}")
    print(f"  Всего:          {zero_total} из {total_buffers*2} ({zero_percent:.1f}%)")
    print("=" * 80)
    
    # Отправить STOP
    print("\nОтправка STOP...")
    send_stop(dev, intf.bInterfaceNumber)
    print("[OK] Тест завершён")

if __name__ == '__main__':
    main()
