#!/usr/bin/env python3
"""Чтение логов с COM9 (запустить ПЕРЕД нажатием кнопки RESET)"""
import serial
import time

port = 'COM9'
ser = serial.Serial(port, 115200, timeout=0.5)
print(f"[HOST] Opened {port}, waiting for device logs...")
print("[USER] >>> НАЖМИТЕ КНОПКУ RESET НА УСТРОЙСТВЕ <<<")
print("=" * 80)

try:
    while True:
        line = ser.readline()
        if line:
            print(line.decode('utf-8', errors='replace').rstrip())
except KeyboardInterrupt:
    print("\n" + "=" * 80)
    print("[HOST] Stopped by user")
finally:
    ser.close()
