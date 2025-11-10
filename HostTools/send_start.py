#!/usr/bin/env python3
"""
Простая настройка устройства: STOP -> настройка -> START
"""
import usb.core
import time

VID = 0xCAFE
PID = 0x4001

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("❌ Устройство не найдено")
        return 1
    
    try:
        dev.set_configuration()
        dev.set_interface_altsetting(2, 1)
    except:
        pass
    
    ep_out = 0x03
    
    print("Отправка команд...")
    # STOP
    dev.write(ep_out, bytes([0x21]), timeout=1000)
    time.sleep(0.3)
    
    # Настройка: ASYNC=1, CHMODE=2 (both channels), PROFILE=1
    dev.write(ep_out, bytes([0x18, 0x01]), timeout=1000)  # ASYNC=1
    time.sleep(0.1)
    dev.write(ep_out, bytes([0x19, 0x02]), timeout=1000)  # CHMODE=2 (both A and B)
    time.sleep(0.1)
    dev.write(ep_out, bytes([0x14, 0x01]), timeout=1000)  # PROFILE=1
    time.sleep(0.1)
    dev.write(ep_out, bytes([0x13, 0x01]), timeout=1000)  # FULL_MODE=1
    time.sleep(0.1)
    
    # START
    dev.write(ep_out, bytes([0x20]), timeout=1000)
    print("✅ START отправлен. Устройство настроено:")
    print("   ASYNC=1, CHMODE=2 (both channels A+B), PROFILE=1, FULL_MODE=1")
    print("   Теперь запустите GUI без флага --single")
    print("   Команда: py -3 HostTools\\gui_oscilloscope.py --ns 300 --profile 1")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
