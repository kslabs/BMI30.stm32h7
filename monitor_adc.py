#!/usr/bin/env python3
"""
Мониторинг логов ADC через COM4 @ 115200
Ищет ключевые сообщения: CALIB, DMA, TIM15, TIM2
"""
import serial
import sys
import time
from datetime import datetime

PORT = 'COM4'
BAUD = 115200

print(f"[{datetime.now().strftime('%H:%M:%S')}] Opening {PORT} @ {BAUD}")
print("="*70)

try:
    ser = serial.Serial(PORT, BAUD, timeout=0.5)
except Exception as e:
    print(f"[ERROR] Cannot open {PORT}: {e}")
    print("\nПроверьте:")
    print("  1. Устройство подключено к COM4")
    print("  2. Другие программы не используют порт")
    print("  3. Драйверы USB-CDC установлены")
    sys.exit(1)

print("[OK] Port opened. Waiting for device logs...")
print("Press Ctrl+C to stop\n")

line_count = 0
interesting_count = 0
keywords = ['CALIB', 'DMA', 'TIM15', 'TIM2', 'ADC', 'DIAG', 'START', 'STOP', 'ERROR', 'FAIL']

try:
    while True:
        line = ser.readline()
        if line:
            line_count += 1
            try:
                text = line.decode('ascii', errors='ignore').strip()
                if text:
                    # Проверяем наличие ключевых слов
                    is_interesting = any(kw in text.upper() for kw in keywords)
                    
                    if is_interesting:
                        interesting_count += 1
                        # Подсвечиваем важные сообщения
                        prefix = f"[{datetime.now().strftime('%H:%M:%S')}][{line_count:04d}] "
                        print(f"{prefix}{text}")
                    else:
                        # Обычные сообщения показываем с меньшим акцентом
                        if line_count % 10 == 0:  # Каждое 10-е сообщение
                            print(f"  ...({line_count} lines received, {interesting_count} relevant)")
            except Exception as e:
                print(f"[WARN] Decode error: {e}")
                
except KeyboardInterrupt:
    print(f"\n\n[{datetime.now().strftime('%H:%M:%S')}] Interrupted by user")
finally:
    ser.close()
    print(f"\n{'='*70}")
    print(f"Summary:")
    print(f"  Total lines: {line_count}")
    print(f"  Relevant:    {interesting_count}")
    print(f"{'='*70}")
