#!/usr/bin/env python3
import serial
import time

s = serial.Serial('COM9', 115200, timeout=1)
print("Sending RESET...")
s.write(b'RESET\n')
s.flush()
time.sleep(1)

print("=== Boot logs ===")
for _ in range(100):
    line = s.readline()
    if not line:
        continue
    print(line.decode('utf-8', errors='replace').rstrip())

s.close()
