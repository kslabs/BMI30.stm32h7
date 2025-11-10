#!/usr/bin/env python3
"""
Упрощённый запуск GUI осциллографа (по образцу test_3x5min_stress.py)
Использует простую последовательность:
1. Поиск устройства
2. STOP + очистка буфера
3. Простая команда START (0x20)
4. Запуск GUI для 2 каналов
"""

import usb.core
import usb.util
import subprocess
import sys
import time

VID = 0xCAFE
PID = 0x4001

CMD_START = 0x20
CMD_STOP = 0x21

def find_device():
    """Поиск USB устройства и получение endpoints"""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"❌ Устройство VID={VID:04X} PID={PID:04X} не найдено!")
        sys.exit(1)
    
    try:
        dev.set_configuration()
    except:
        pass
    
    cfg = dev.get_active_configuration()
    vendor_intf = None
    for intf in cfg:
        if intf.bInterfaceClass == 0xFF and intf.bInterfaceNumber == 2:
            if intf.bAlternateSetting == 1:
                vendor_intf = intf
                break
    
    if vendor_intf is None:
        print("❌ Vendor интерфейс не найден!")
        sys.exit(1)
    
    try:
        dev.set_interface_altsetting(2, 1)
    except:
        pass
    
    ep_in = None
    ep_out = None
    for ep in vendor_intf:
        if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
            ep_in = ep.bEndpointAddress
        else:
            ep_out = ep.bEndpointAddress
    
    if not ep_in or not ep_out:
        print("❌ Endpoints не найдены!")
        sys.exit(1)
    
    return dev, ep_in, ep_out

def send_command(dev, ep_out, cmd):
    """Отправка команды"""
    try:
        dev.write(ep_out, bytes([cmd]), timeout=1000)
        return True
    except Exception as e:
        print(f"⚠️  Ошибка отправки команды 0x{cmd:02X}: {e}")
        return False

def read_data(dev, ep_in, timeout_ms=1000):
    """Чтение данных"""
    try:
        data = dev.read(ep_in, 16384, timeout=timeout_ms)
        return bytes(data)
    except:
        return None

def main():
    print("\n" + "="*70)
    print("  ЗАПУСК GUI ОСЦИЛЛОГРАФА (упрощённая версия)")
    print("="*70)
    
    # Шаг 1: Поиск устройства
    print("\n[1/4] Поиск устройства...")
    dev, ep_in, ep_out = find_device()
    print(f"   ✅ EP_IN=0x{ep_in:02X}, EP_OUT=0x{ep_out:02X}")
    
    # Шаг 2: STOP + очистка
    print("[2/4] Сброс состояния (STOP + очистка буфера)...")
    send_command(dev, ep_out, CMD_STOP)
    time.sleep(0.5)
    
    # Очистка буфера
    cleared = 0
    while read_data(dev, ep_in, timeout_ms=100):
        cleared += 1
    if cleared > 0:
        print(f"   Очищено {cleared} буферов")
    
    # Шаг 3: START
    print("[3/4] Отправка START...")
    if not send_command(dev, ep_out, CMD_START):
        print("❌ Не удалось отправить START!")
        sys.exit(1)
    print("   ✅ START отправлен")
    
    # Небольшая пауза для стабилизации
    time.sleep(0.3)
    
    # Шаг 4: Запуск GUI
    print("[4/4] Запуск GUI осциллографа...")
    print("   Параметры: 2 канала (без --single), profile=1, 300 отсчётов")
    
    # Запуск gui_oscilloscope.py БЕЗ --single для двух каналов
    cmd = ["py", "-3", "HostTools/gui_oscilloscope.py", "--ns", "300", "--profile", "1"]
    
    print(f"\n{'='*70}")
    print("  GUI ЗАПУЩЕН")
    print("="*70)
    print("   Команда:", " ".join(cmd))
    print()
    
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ GUI завершился с ошибкой: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️  Прервано пользователем")
        sys.exit(0)

if __name__ == "__main__":
    main()
