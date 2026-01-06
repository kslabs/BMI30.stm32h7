#!/usr/bin/env python3
"""
Читает BOOT логи через COM4 после сброса устройства
"""
import serial
import sys

PORT = 'COM4'
BAUD = 115200

print(f"[INFO] Opening {PORT} @ {BAUD}")
print("Press RESET button on device NOW!")
print("="*70)

try:
    ser = serial.Serial(PORT, BAUD, timeout=0.5)
except Exception as e:
    print(f"[ERROR] Cannot open {PORT}: {e}")
    sys.exit(1)

# Читаем первые 300 строк логов после reset
for i in range(300):
    try:
        line = ser.readline().decode('ascii', errors='ignore').strip()
        if line:
            print(f"[{i:03d}] {line}")
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"[ERROR] {e}")
        break

ser.close()
print("\n" + "="*70)
print("Done. Check for [ADC][CALIB], [TIM15], [DMA] messages above")
