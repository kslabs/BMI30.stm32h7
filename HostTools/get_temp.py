#!/usr/bin/env python3
"""
Скрипт для чтения температуры кристалла STM32H723 через USB Vendor команду 0x31 (CMD_GET_TEMP)

Использование:
    python get_temp.py
    python get_temp.py --repeat 10  # читать 10 раз
    python get_temp.py --repeat 10 --interval 1  # читать каждую секунду
"""

import usb.core
import usb.util
import time
import sys
import argparse
import struct

# USB идентификаторы
VID = 0xCAFE
PID = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83

# Команды
CMD_GET_TEMP = 0x31
RSP_ACK = 0x80


def find_device():
    """Найти USB устройство"""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Устройство не найдено (VID=0x{:04X} PID=0x{:04X})".format(VID, PID))
    return dev


def set_active_interface(dev):
    """Активировать интерфейс с endpoints"""
    try:
        dev.set_interface_altsetting(interface_number=INTERFACE, alternate_setting=1)
        print("[INFO] Активирован интерфейс #2, alt=1 (с endpoints)")
    except usb.core.USBError as e:
        print("[WARN] Ошибка активации интерфейса: {}".format(e))


def read_temperature(dev, timeout_ms=1000):
    """
    Прочитать температуру кристалла через USB
    Возвращает температуру в °C (int16_t)
    """
    try:
        # Отправить команду 0x31 (CMD_GET_TEMP)
        cmd = bytes([CMD_GET_TEMP])
        dev.write(EP_OUT, cmd, timeout=timeout_ms)
        
        # Прочитать ответ (4 байта: RSP_ACK + cmd + 2 байта температуры)
        response = dev.read(EP_IN, 4, timeout=timeout_ms)
        
        if len(response) < 4:
            raise RuntimeError("Неполный ответ (получено {} байт, ожидается 4)".format(len(response)))
        
        # Проверить заголовок ответа
        if response[0] != RSP_ACK:
            raise RuntimeError("Неверный ACK: 0x{:02X}".format(response[0]))
        
        if response[1] != CMD_GET_TEMP:
            raise RuntimeError("Неверная команда в ответе: 0x{:02X}".format(response[1]))
        
        # Распарсить температуру (bytes 2-3, little-endian, signed int16)
        temp_raw = response[2] | (response[3] << 8)
        
        # Преобразовать из unsigned в signed int16
        if temp_raw & 0x8000:
            temp_c = temp_raw - 0x10000
        else:
            temp_c = temp_raw
        
        return temp_c
        
    except usb.core.USBError as e:
        raise RuntimeError("USB ошибка: {}".format(e))
    except Exception as e:
        raise RuntimeError("Ошибка при чтении температуры: {}".format(e))


def main():
    parser = argparse.ArgumentParser(
        description='Читать температуру кристалла STM32H723 через USB'
    )
    parser.add_argument('--repeat', type=int, default=1, 
                        help='Количество повторных чтений (по умолчанию 1)')
    parser.add_argument('--interval', type=float, default=0, 
                        help='Интервал между чтениями в секундах (по умолчанию 0)')
    parser.add_argument('--timeout', type=int, default=1000,
                        help='Таймаут USB в миллисекундах (по умолчанию 1000)')
    
    args = parser.parse_args()
    
    try:
        # Найти устройство
        print("[INFO] Поиск устройства (VID=0x{:04X} PID=0x{:04X})...".format(VID, PID))
        dev = find_device()
        print("[OK] Устройство найдено")
        
        # Активировать интерфейс
        set_active_interface(dev)
        
        # Читать температуру
        print("[INFO] Чтение температуры...\n")
        
        for i in range(args.repeat):
            try:
                temp = read_temperature(dev, timeout_ms=args.timeout)
                print("  [#{:2d}] Температура: {:3d}°C".format(i + 1, temp))
                
                if i < args.repeat - 1 and args.interval > 0:
                    time.sleep(args.interval)
                    
            except RuntimeError as e:
                print("  [#{:2d}] ОШИБКА: {}".format(i + 1, e))
                return 1
        
        print("\n[OK] Успешно")
        return 0
        
    except RuntimeError as e:
        print("[ERROR] {}".format(e))
        return 1
    except KeyboardInterrupt:
        print("\n[CANCEL] Прервано пользователем")
        return 130


if __name__ == '__main__':
    sys.exit(main())
