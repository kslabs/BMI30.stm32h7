import serial
import time
import sys

port = sys.argv[1]
duration = 25
start_time = time.time()

print(f"[DIAG] Starting monitor on {port} for {duration}s...")
try:
    ser = serial.Serial(port, 115200, timeout=1.0)
    while time.time() - start_time < duration:
        line = ser.readline()
        if line:
            print(line.decode('utf-8', errors='replace').rstrip())
    ser.close()
except Exception as e:
    print(f"[ERROR] {port}: {e}")
