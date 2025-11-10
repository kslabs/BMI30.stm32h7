#!/usr/bin/env python3
"""
Сброс устройства через CDC порт
Использует команду "RESET\r\n" для полного перезапуска MCU
"""
import serial
import time
import sys

def device_reset(cdc_port="COM4", wait_time=3.0):
    """Отправить команду RESET через CDC порт"""
    print(f"🔄 DEVICE RESET через {cdc_port}...")
    
    try:
        print(f"📤 Отправка команды RESET...")
        ser = serial.Serial(cdc_port, 115200, timeout=2)
        time.sleep(0.3)
        ser.write(b"RESET\r\n")
        ser.flush()
        time.sleep(0.2)
        ser.close()
        print(f"✅ RESET отправлен")
    except Exception as e:
        print(f"❌ Ошибка отправки RESET: {e}")
        return False
    
    print(f"⏳ Ожидание перезагрузки устройства ({wait_time:.1f}s)...")
    time.sleep(wait_time)
    print("✅ Устройство готово к работе")
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Device reset через CDC")
    parser.add_argument("--port", default="COM4", help="CDC порт (по умолчанию COM4)")
    parser.add_argument("--wait", type=float, default=3.0, help="Время ожидания перезагрузки в секундах")
    args = parser.parse_args()
    
    success = device_reset(args.port, args.wait)
    sys.exit(0 if success else 1)
