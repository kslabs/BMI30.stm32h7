#!/usr/bin/env python3
"""
Read firmware version from COM9 (USART1)
"""
import serial
import time
import sys

PORT = "COM9"
BAUD = 115200

def main():
    print(f"[INFO] Opening {PORT} @ {BAUD} baud...")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
        print(f"[OK] Port opened. Reading for 5 seconds...")
        print("=" * 70)
        
        start = time.time()
        while time.time() - start < 5:
            if ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(line)
                    
        print("=" * 70)
        print("[INFO] Done")
        ser.close()
        return 0
        
    except serial.SerialException as e:
        print(f"[ERROR] Cannot open {PORT}: {e}")
        print("[HINT] Check if port exists and is not in use")
        return 1
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted")
        return 0

if __name__ == "__main__":
    sys.exit(main())
