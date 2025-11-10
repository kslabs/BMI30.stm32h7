#!/usr/bin/env python3
"""
Автоматический запуск GUI осциллографа с предварительной инициализацией
1. CDC RESET устройства (через COM4)
2. Находит устройство
3. Отправляет STOP (очистка)
4. Настраивает режим (ASYNC=1, CHMODE=2, PROFILE=1)
5. Отправляет START
6. Запускает gui_oscilloscope.py
"""

import usb.core
import usb.util
import serial
import time
import subprocess
import sys

VID = 0xCAFE
PID = 0x4001

CMD_START = 0x20
CMD_STOP = 0x21
CMD_SET_ASYNC = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_PROFILE = 0x14

def device_reset_cdc(cdc_port="COM4", wait_time=3.0):
    """Отправить команду RESET через CDC порт и дождаться перезагрузки"""
    print(f"🔄 DEVICE RESET через {cdc_port}...")
    
    try:
        ser = serial.Serial(cdc_port, 115200, timeout=2)
        time.sleep(0.3)
        ser.write(b"RESET\r\n")
        ser.flush()
        time.sleep(0.2)
        ser.close()
        print(f"✅ RESET отправлен")
    except Exception as e:
        print(f"❌ Ошибка CDC RESET: {e}")
        print(f"⚠️  Продолжаем без RESET...")
        return False
    
    print(f"⏳ Ожидание перезагрузки устройства ({wait_time:.1f}s)...")
    time.sleep(wait_time)
    return True

def find_device():
    """Поиск USB устройства"""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError(f"USB устройство VID={VID:04X} PID={PID:04X} не найдено")
    
    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        print(f"⚠️  set_configuration error (игнорируем): {e}")
    
    cfg = dev.get_active_configuration()
    vendor_intf = None
    for intf in cfg:
        if intf.bInterfaceClass == 0xFF and intf.bInterfaceSubClass == 0x00:
            vendor_intf = intf
            break
    
    if vendor_intf is None:
        raise RuntimeError("Vendor interface не найден")
    
    # Установить alt setting 1
    try:
        dev.set_interface_altsetting(2, 1)
    except:
        pass
    
    # Получить vendor_intf заново после установки alt setting
    cfg = dev.get_active_configuration()
    ep_in = None
    ep_out = None
    
    for intf in cfg:
        if intf.bInterfaceClass == 0xFF and intf.bInterfaceSubClass == 0x00:
            # Проверяем endpoints в этом интерфейсе
            for ep in intf:
                if ep.bEndpointAddress & 0x80:
                    ep_in = ep.bEndpointAddress
                else:
                    ep_out = ep.bEndpointAddress
            
            if ep_in and ep_out:
                break
    
    if not ep_in or not ep_out:
        raise RuntimeError("Endpoints не найдены")
    
    return dev, ep_in, ep_out

def send_command(dev, ep_out, cmd):
    """Отправка команды (только одиночный байт, как в test_3x5min_stress.py)"""
    try:
        dev.write(ep_out, bytes([cmd]), timeout=1000)
        return True
    except Exception as e:
        print(f"⚠️  Команда 0x{cmd:02X} ошибка: {e}")
        return False

def main():
    print("=" * 70)
    print("АВТОЗАПУСК GUI ОСЦИЛЛОГРАФА")
    print("=" * 70)
    
    print("\n[1/6] CDC RESET устройства...")
    device_reset_cdc("COM4", wait_time=3.5)
    
    print("\n[2/6] Поиск устройства...")
    try:
        dev, ep_in, ep_out = find_device()
        print(f"✅ Устройство найдено: EP_IN=0x{ep_in:02X}, EP_OUT=0x{ep_out:02X}")
    except Exception as e:
        print(f"❌ {e}")
        return 1
    
    print("\n[3/6] Очистка состояния (STOP)...")
    send_command(dev, ep_out, CMD_STOP)
    time.sleep(0.5)
    
    # Очистка буфера
    print("[4/6] Очистка буфера...")
    try:
        while True:
            data = dev.read(ep_in, 16384, timeout=100)
            if len(data) == 0:
                break
    except:
        pass
    
    print("\n[5/6] Отправка START...")
    if not send_command(dev, ep_out, CMD_START):
        print("❌ Не удалось отправить START")
        return 1
    print("✅ START отправлен")
    time.sleep(1.0)
    
    print("\n[6/6] Запуск GUI осциллографа (2 канала)...")
    print("=" * 70)
    
    # Запуск gui_oscilloscope.py БЕЗ --single для двух каналов
    cmd = ["py", "-3", "HostTools/gui_oscilloscope.py", "--ns", "300", "--profile", "1"]
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\n\n⚠️  GUI закрыт пользователем")
    except Exception as e:
        print(f"\n❌ Ошибка запуска GUI: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
