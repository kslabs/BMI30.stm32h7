#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест надёжности приёма команд: отправляем START N раз со случайными задержками
"""
import sys
import usb.core
import time
import random

VID = 0xCAFE
PID = 0x4001
OUT_EP = 0x03
IN_EP = 0x83

CMD_START = 0x20
CMD_GET_STATUS = 0x30

def send_start(dev, intf_num):
    """Отправка START команды через control transfer"""
    try:
        ret = dev.ctrl_transfer(
            bmRequestType=0x41,  # host-to-device, class, interface
            bRequest=CMD_START,
            wValue=0,
            wIndex=intf_num,
            data_or_wLength=None,
            timeout=500
        )
        return True
    except Exception as e:
        print(f"[ERR] START failed: {e}")
        return False

def read_start_counter(dev, intf_num):
    """Чтение STATUS и извлечение счётчика START команд из flags2"""
    try:
        ret = dev.ctrl_transfer(
            bmRequestType=0xC1,  # device-to-host, class, interface
            bRequest=CMD_GET_STATUS,
            wValue=0,
            wIndex=intf_num,
            data_or_wLength=128,
            timeout=1000
        )
        
        if len(ret) < 64:
            print(f"[ERR] Status too short: {len(ret)} bytes")
            return None
        
        # flags2 находится на смещении 50-51 (uint16_t)
        # Но счётчик команд в наших переменных cmd_start_count - нужно добавить в STATUS
        # Пока просто показываем что получили ответ
        return len(ret)
        
    except Exception as e:
        print(f"[ERR] Get status failed: {e}")
        return None

def main():
    # Параметры теста
    num_commands = 100
    min_delay_ms = 5
    max_delay_ms = 50
    
    print(f"=== Тест надёжности приёма команд ===")
    print(f"Команд: {num_commands}")
    print(f"Задержка: {min_delay_ms}-{max_delay_ms} мс (случайная)\n")
    
    # Найти устройство
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"[ERR] Device not found VID=0x{VID:04X} PID=0x{PID:04X}")
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
    
    # Установить alt=1
    dev.set_interface_altsetting(intf.bInterfaceNumber, 1)
    print(f"[OK] Device configured, interface {intf.bInterfaceNumber}\n")
    
    # Счётчики
    sent_count = 0
    success_count = 0
    
    # Отправка команд
    print("Отправка START команд...")
    start_time = time.time()
    
    for i in range(num_commands):
        if send_start(dev, intf.bInterfaceNumber):
            success_count += 1
        sent_count += 1
        
        # Прогресс каждые 10 команд
        if (i + 1) % 10 == 0:
            print(f"  Отправлено: {i+1}/{num_commands}")
        
        # Случайная задержка
        delay = random.uniform(min_delay_ms/1000.0, max_delay_ms/1000.0)
        time.sleep(delay)
    
    elapsed = time.time() - start_time
    
    # Небольшая пауза перед чтением статуса
    time.sleep(0.1)
    
    # Прочитать статус
    print(f"\n[OK] Отправлено команд: {sent_count}")
    print(f"[OK] Успешных отправок: {success_count}")
    print(f"[OK] Время выполнения: {elapsed:.2f} сек")
    print(f"\n*** Теперь проверьте LCD дисплей - должно быть показано ST:#N ***")
    print(f"*** где N = количество принятых START команд (ожидается ~{num_commands}) ***")
    
    # Попытка прочитать статус
    status_len = read_start_counter(dev, intf.bInterfaceNumber)
    if status_len:
        print(f"\n[INFO] STATUS прочитан ({status_len} байт)")
        print(f"[INFO] Для подробностей запустите: py -3 HostTools/read_status_v3.py")
    
    print("\n=== Тест завершён ===")

if __name__ == '__main__':
    main()
