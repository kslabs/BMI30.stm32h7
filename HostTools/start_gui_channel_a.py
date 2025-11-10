#!/usr/bin/env python3
"""
Запуск GUI для ОДНОГО канала (Channel A)
Простая последовательность: STOP → очистка → START → GUI
"""
import usb.core
import usb.util
import time
import subprocess
import sys

VID = 0xCAFE
PID = 0x4001
CMD_START = 0x20
CMD_STOP = 0x21

def main():
    print("=" * 70)
    print("ЗАПУСК GUI ДЛЯ КАНАЛА A")
    print("=" * 70)
    
    # [1] Поиск устройства
    print("\n[1/4] Поиск USB устройства...")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"❌ Устройство не найдено (VID={VID:04X} PID={PID:04X})")
        return 1
    
    try:
        dev.set_configuration()
        dev.set_interface_altsetting(2, 1)
    except:
        pass
    
    print("✅ Устройство найдено")
    
    # [2] STOP + очистка
    print("\n[2/4] Остановка передачи (STOP)...")
    try:
        dev.write(0x03, bytes([CMD_STOP]), timeout=1000)
        print("✅ STOP отправлен")
    except Exception as e:
        print(f"⚠️  STOP ошибка: {e}")
    
    time.sleep(0.5)
    
    # Очистка буфера
    print("[3/4] Очистка буфера...")
    try:
        while True:
            data = dev.read(0x83, 16384, timeout=100)
            if len(data) == 0:
                break
    except:
        pass
    print("✅ Буфер очищен")
    
    # [3] START
    print("\n[4/4] Отправка START...")
    try:
        dev.write(0x03, bytes([CMD_START]), timeout=1000)
        print("✅ START отправлен")
    except Exception as e:
        print(f"❌ START ошибка: {e}")
        return 1
    
    time.sleep(1.0)
    
    # [4] Запуск GUI
    print("\n" + "=" * 70)
    print("ЗАПУСК GUI ОСЦИЛЛОГРАФА (КАНАЛ A)")
    print("=" * 70)
    
    cmd = ["py", "-3", "HostTools/gui_oscilloscope.py", "--ns", "300", "--profile", "1", "--single"]
    print(f"Команда: {' '.join(cmd)}\n")
    
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\n⚠️  GUI закрыт")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
