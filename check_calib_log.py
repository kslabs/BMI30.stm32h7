#!/usr/bin/env python3
"""
Читает логи через COM4 @ 115200 в поисках сообщений о калибровке ADC
"""
import serial
import sys
import time

PORT = 'COM4'
BAUD = 115200
TIMEOUT = 5.0  # сек

print(f"[INFO] Opening {PORT} @ {BAUD}")
try:
    ser = serial.Serial(PORT, BAUD, timeout=1.0)
except Exception as e:
    print(f"[ERROR] Cannot open {PORT}: {e}")
    sys.exit(1)

print(f"[INFO] Waiting for ADC calibration logs (timeout={TIMEOUT}s)...")
start_time = time.time()
found_calib = False

while time.time() - start_time < TIMEOUT:
    try:
        line = ser.readline().decode('ascii', errors='ignore').strip()
        if line:
            print(f"[LOG] {line}")
            if 'CALIB' in line or 'ADC' in line or 'TIM15' in line or 'TIM2' in line:
                found_calib = True
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
        break
    except Exception as e:
        print(f"[ERROR] Read error: {e}")
        break

ser.close()

if found_calib:
    print("\n[OK] Found relevant logs (check output above)")
else:
    print("\n[WARN] No ADC/CALIB logs found - device may not be sending debug output")

sys.exit(0 if found_calib else 1)
