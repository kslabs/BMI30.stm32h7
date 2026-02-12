import serial
import time
import sys

try:
    s = serial.Serial('COM4', 115200, timeout=0.2)
    print("[READER] Opened COM4")
    lines = []
    end = time.time() + 3
    
    while time.time() < end:
        try:
            line = s.readline()
            if line:
                lines.append(line.decode('utf-8', errors='ignore').strip())
        except:
            break
    
    s.close()
    print(f"[READER] Got {len(lines)} lines")
    
    # Фильтр
    for line in lines:
        if 'COARSE' in line or 'idx=' in line:
            print(line)
            
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)
