#!/usr/bin/env python3
"""
Быстрая диагностика статуса устройства через bulk GET_STATUS
"""
import usb.core
import usb.util
import time

VID = 0xCAFE
PID = 0x4001
CMD_GET_STATUS = 0x30

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("❌ Устройство не найдено")
        return
    
    try:
        dev.set_configuration()
        dev.set_interface_altsetting(2, 1)
    except:
        pass
    
    ep_out = 0x03
    ep_in = 0x83
    
    print("Отправка GET_STATUS...")
    try:
        dev.write(ep_out, bytes([CMD_GET_STATUS]), timeout=1000)
        time.sleep(0.2)
        
        # Читаем ответ
        data = dev.read(ep_in, 16384, timeout=2000)
        print(f"✅ Получено {len(data)} байт:")
        print(data[:100].hex(' '))
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    main()
