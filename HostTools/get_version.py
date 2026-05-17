#!/usr/bin/env python3
"""
Запрос версии прошивки у устройства BMI30 через USB Vendor Interface.

Использование:
    python get_version.py

или с опциями:
    python get_version.py --vid 0xCAFE --pid 0x4001 --intf 2
"""

import sys
import usb.core
import usb.util
import argparse
import time


def main():
    parser = argparse.ArgumentParser(
        description='Запрос версии прошивки BMI30 через USB Vendor Interface'
    )
    parser.add_argument('--vid', type=lambda x: int(x, 16), default=0xCAFE,
                        help='USB Vendor ID (default: 0xCAFE)')
    parser.add_argument('--pid', type=lambda x: int(x, 16), default=0x4001,
                        help='USB Product ID (default: 0x4001)')
    parser.add_argument('--intf', type=int, default=2,
                        help='USB Interface number (default: 2)')
    parser.add_argument('--timeout', type=int, default=1000,
                        help='USB read timeout in ms (default: 1000)')
    parser.add_argument('--repeat', type=int, default=1,
                        help='Количество повторений запроса (default: 1)')
    parser.add_argument('--delay', type=float, default=0.5,
                        help='Задержка между повторениями в сек (default: 0.5)')
    
    args = parser.parse_args()
    
    # Поиск устройства
    dev = usb.core.find(idVendor=args.vid, idProduct=args.pid)
    if not dev:
        print(f"Устройство не найдено (VID={args.vid:04X}, PID={args.pid:04X})")
        return 1
    
    print(f"Найдено устройство: {dev.manufacturer} {dev.product} (S/N: {dev.serial_number})")
    print(f"VID={dev.idVendor:04X}, PID={dev.idProduct:04X}")
    
    # Установка интерфейса (alt setting = 1 для активных endpoints)
    try:
        dev.set_interface_altsetting(interface_number=args.intf, alternate_setting=1)
        print(f"Interface {args.intf} set to alternate setting 1")
    except Exception as e:
        print(f"Ошибка при установке интерфейса: {e}")
        return 1
    
    # Параметры endpoint'ов для Vendor Interface #2
    ep_out = 0x03
    ep_in = 0x83
    
    # Команда: 0x32 (CMD_GET_VERSION)
    cmd = bytes([0x32])
    
    print(f"\nОтправка команды 0x32 (CMD_GET_VERSION) {args.repeat} раз...")
    print("-" * 60)
    
    for attempt in range(args.repeat):
        try:
            # Отправка команды
            written = dev.write(ep_out, cmd)
            print(f"[{attempt + 1}] Отправлено {written} байт")
            
            # Чтение ответа (6 байт)
            response = dev.read(ep_in, 6, timeout=args.timeout)
            print(f"[{attempt + 1}] Получено {len(response)} байт: {' '.join(f'{b:02X}' for b in response)}")
            
            # Парсинг ответа
            if len(response) >= 6:
                ack = response[0]
                cmd_echo = response[1]
                major = response[2]
                minor = response[3]
                patch = response[4]
                build = response[5]
                
                if ack == 0x80 and cmd_echo == 0x32:
                    version_str = f"{major}.{minor}.{patch}"
                    print(f"[{attempt + 1}] ✓ Версия прошивки: {version_str} (build {build})")
                else:
                    print(f"[{attempt + 1}] ✗ Неверный формат ответа (ack=0x{ack:02X}, cmd=0x{cmd_echo:02X})")
            else:
                print(f"[{attempt + 1}] ✗ Слишком короткий ответ ({len(response)} байт)")
        
        except usb.core.USBError as e:
            print(f"[{attempt + 1}] ✗ USB ошибка: {e}")
            return 1
        except Exception as e:
            print(f"[{attempt + 1}] ✗ Ошибка: {e}")
            return 1
        
        # Задержка перед следующим запросом
        if attempt < args.repeat - 1:
            time.sleep(args.delay)
    
    print("-" * 60)
    print("Готово!")
    return 0


if __name__ == '__main__':
    sys.exit(main())
