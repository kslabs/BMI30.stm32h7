#!/usr/bin/env python3
"""Читаем CDC лог из COM4 во время работы устройства."""
import serial
import time
import sys

COM_PORT = "COM4"
BAUD = 115200

def main():
    print(f"Открываю {COM_PORT} @ {BAUD}...")
    try:
        ser = serial.Serial(COM_PORT, BAUD, timeout=0.1)
    except Exception as e:
        print(f"ERROR: Не могу открыть {COM_PORT}: {e}")
        sys.exit(1)
    
    print("Читаю логи 15 секунд...")
    print("-" * 70)
    
    start_time = time.time()
    line_count = 0
    
    while time.time() - start_time < 15.0:
        try:
            line = ser.readline()
            if line:
                try:
                    text = line.decode('utf-8', errors='ignore').strip()
                    if text:
                        print(text)
                        line_count += 1
                except:
                    pass
        except:
            pass
    
    ser.close()
    print("-" * 70)
    print(f"Всего строк: {line_count}")

if __name__ == '__main__':
    main()
