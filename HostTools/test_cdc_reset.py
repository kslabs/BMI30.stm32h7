#!/usr/bin/env python3
"""
Test CDC RESET command
Отправляет текстовую команду "RESET" через CDC порт (COM4 или указанный)
"""

import serial
import time
import sys

PORT = "COM4"  # Можно изменить на нужный COM порт
BAUD = 115200

def send_cdc_reset(port=PORT, baud=BAUD):
    """Отправить RESET через CDC порт"""
    print(f"🔌 Подключение к {port} @ {baud}...")
    try:
        ser = serial.Serial(port, baud, timeout=2)
        time.sleep(0.5)  # Даем время на подключение
        
        print(f"📤 Отправка команды 'RESET'...")
        ser.write(b"RESET\r\n")
        ser.flush()
        
        print(f"⏳ Ожидание ответа (2s)...")
        time.sleep(0.5)
        
        # Попытка прочитать ответ (если успеем до reset)
        if ser.in_waiting > 0:
            response = ser.read(ser.in_waiting)
            print(f"📥 Ответ: {response.decode('utf-8', errors='ignore')}")
        
        ser.close()
        print(f"✅ Команда отправлена, устройство должно перезагрузиться")
        
        # Ждем перезагрузку
        print(f"⏳ Ожидание перезагрузки (3s)...")
        time.sleep(3)
        
        # Проверка переподключения
        print(f"🔌 Проверка доступности устройства...")
        for attempt in range(1, 6):
            try:
                test_ser = serial.Serial(port, baud, timeout=1)
                test_ser.close()
                print(f"✅ Устройство доступно (попытка {attempt})")
                return True
            except Exception as e:
                if attempt < 5:
                    print(f"    Попытка {attempt}/5 не удалась, повтор...")
                    time.sleep(1)
                else:
                    print(f"❌ Устройство не доступно после {attempt} попыток")
                    return False
        
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        return False

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else PORT
    success = send_cdc_reset(port)
    sys.exit(0 if success else 1)
