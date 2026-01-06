#!/usr/bin/env python3
"""
Читает весь вывод из COM4 после перезагрузки устройства, записывает в файл.
"""
import sys
import serial
import time
from pathlib import Path

PORT = "COM4"
BAUD = 115200
TIMEOUT_SEC = 20  # Длительность чтения
OUTPUT_FILE = Path(__file__).parent.parent / ".vscode" / "com4_full_boot_log.txt"

def main():
    print("="*60)
    print("COM4 Full Boot Log Capture")
    print("="*60)
    print(f"Port: {PORT} @ {BAUD}")
    print(f"Duration: {TIMEOUT_SEC}s")
    print(f"Output: {OUTPUT_FILE}")
    print("="*60)
    
    try:
        ser = serial.Serial(PORT, BAUD, timeout=0.1)
        print(f"[COM4] Opened")
    except Exception as e:
        print(f"[ERROR] Cannot open {PORT}: {e}")
        return 1
    
    # Очистить буфер приёма
    ser.reset_input_buffer()
    time.sleep(0.1)
    
    print("[INFO] Please RESET the device NOW (press reset button or power cycle)")
    print(f"[INFO] Capturing for {TIMEOUT_SEC}s...")
    
    start_t = time.time()
    lines = []
    
    try:
        while (time.time() - start_t) < TIMEOUT_SEC:
            try:
                data = ser.read(4096)
                if data:
                    # Декодируем и выводим на экран
                    text = data.decode('ascii', errors='ignore')
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    lines.append(text)
            except Exception as e:
                print(f"\n[ERROR] Read error: {e}")
                break
    
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
    
    finally:
        ser.close()
        print(f"\n[COM4] Closed")
        
        # Сохраняем в файл
        full_text = ''.join(lines)
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(full_text, encoding='utf-8')
        print(f"[INFO] Saved {len(full_text)} bytes to {OUTPUT_FILE}")
        print("="*60)
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
