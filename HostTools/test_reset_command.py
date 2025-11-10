#!/usr/bin/env python3
"""
Тест отправки команды DEVICE RESET разными способами
"""
import usb.core
import usb.util
import time

VID = 0xCAFE
PID = 0x4001

CMD_DEVICE_RESET = 0x22

def test_bulk_reset():
    """Отправка RESET через bulk OUT endpoint"""
    print("\n=== ТЕСТ #1: RESET через BULK OUT ===")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("❌ Устройство не найдено")
        return False
    
    try:
        dev.set_configuration()
        dev.set_interface_altsetting(2, 1)
        
        # Найти EP OUT
        cfg = dev.get_active_configuration()
        intf = cfg[(2, 1)]
        ep_out = None
        for ep in intf:
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
                ep_out = ep.bEndpointAddress
                break
        
        if not ep_out:
            print("❌ EP OUT не найден")
            return False
        
        print(f"📤 Отправка 0x22 через bulk EP {ep_out:#04x}...")
        dev.write(ep_out, bytes([CMD_DEVICE_RESET]), timeout=1000)
        print("✅ Команда отправлена (bulk)")
        
    except Exception as e:
        print(f"⚠️  Exception (ожидаемо при reset): {e}")
    
    return True

def test_control_reset():
    """Отправка RESET через control endpoint (EP0)"""
    print("\n=== ТЕСТ #2: RESET через CONTROL EP0 ===")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("❌ Устройство не найдено")
        return False
    
    try:
        dev.set_configuration()
        
        # Control transfer: bmRequestType=0x40 (vendor, device, out), bRequest=0x22
        print(f"📤 Отправка control transfer bRequest=0x22...")
        bmRequestType = usb.util.build_request_type(
            usb.util.CTRL_OUT, 
            usb.util.CTRL_TYPE_VENDOR, 
            usb.util.CTRL_RECIPIENT_DEVICE
        )
        dev.ctrl_transfer(bmRequestType, CMD_DEVICE_RESET, 0, 0, 0, timeout=1000)
        print("✅ Команда отправлена (control)")
        
    except Exception as e:
        print(f"⚠️  Exception (ожидаемо при reset): {e}")
    
    return True

def main():
    print("\n" + "="*70)
    print("  ТЕСТ КОМАНДЫ DEVICE RESET")
    print("="*70)
    
    # Тест 1: Bulk
    test_bulk_reset()
    print("\n⏳ Ожидание 3 секунды...")
    time.sleep(3)
    
    # Проверка устройства после bulk reset
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev:
        print("📱 Устройство найдено после bulk reset")
    else:
        print("❌ Устройство НЕ найдено после bulk reset - возможно, reset сработал!")
        print("⏳ Ждём ещё 2 секунды...")
        time.sleep(2)
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev:
            print("✅ Устройство появилось - reset сработал!")
        else:
            print("❌ Устройство так и не появилось")
            return 1
    
    # Тест 2: Control
    print("\n⏳ Пауза 2 секунды перед control reset...")
    time.sleep(2)
    
    test_control_reset()
    print("\n⏳ Ожидание 3 секунды...")
    time.sleep(3)
    
    # Проверка устройства после control reset
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev:
        print("📱 Устройство найдено после control reset")
    else:
        print("❌ Устройство НЕ найдено после control reset - возможно, reset сработал!")
        print("⏳ Ждём ещё 2 секунды...")
        time.sleep(2)
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev:
            print("✅ Устройство появилось - reset сработал!")
        else:
            print("❌ Устройство так и не появилось")
            return 1
    
    print("\n" + "="*70)
    print("  ТЕСТЫ ЗАВЕРШЕНЫ")
    print("="*70)
    return 0

if __name__ == '__main__':
    import sys
    sys.exit(main())
