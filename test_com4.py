import serial
import time

PORT = 'COM4'
BAUD = 115200

print(f"[TEST] Opening {PORT} @ {BAUD}...")
ser = serial.Serial(PORT, BAUD, timeout=1)
print("[TEST] Waiting for data (15s)...")

start = time.time()
lines = 0
while time.time() - start < 15:
    if ser.in_waiting:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(f"  {line}")
                lines += 1
                if lines >= 50:
                    break
        except:
            pass

print(f"\n[TEST] Got {lines} lines in {time.time()-start:.1f}s")
ser.close()
