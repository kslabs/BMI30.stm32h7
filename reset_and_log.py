#!/usr/bin/env python3
"""Сброс устройства через COM9 и чтение логов"""
import serial
import time
import sys

port = sys.argv[1] if len(sys.argv) > 1 else 'COM9'
lines_to_read = int(sys.argv[2]) if len(sys.argv) > 2 else 100

try:
    ser = serial.Serial(port, 115200, timeout=1)
    print(f"[HOST] Opened {port}")
    
    # Отправляем RESET
    print("[HOST] Sending RESET command...")
    ser.write(b'RESET\n')
    ser.flush()
    
    # Ждём перезагрузку
    time.sleep(1.5)
    
    # Читаем логи
    print(f"[HOST] Reading logs (max {lines_to_read} lines)...\n")
    print("=" * 80)
    
    for i in range(lines_to_read):
        line = ser.readline()
        if not line:
            continue
        decoded = line.decode('utf-8', errors='replace').rstrip()
        if decoded:
            print(decoded)
    
    print("=" * 80)
    ser.close()
    print(f"\n[HOST] Done. Port closed.")
    
except serial.SerialException as e:
    print(f"[ERROR] Serial port error: {e}")
    sys.exit(1)
except KeyboardInterrupt:
    print("\n[HOST] Interrupted by user")
    if 'ser' in locals():
        ser.close()
