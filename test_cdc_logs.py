#!/usr/bin/env python3
"""Простое чтение CDC логов с COM4"""
import serial
import time

ser = serial.Serial('COM4', 115200, timeout=0.1)
print("Listening on COM4...")
print("=" * 60)

while True:
    try:
        line = ser.readline()
        if line:
            try:
                print(line.decode('ascii', errors='ignore').strip())
            except:
                print(f"[RAW] {line.hex()}")
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"ERROR: {e}")
        time.sleep(0.5)

ser.close()
