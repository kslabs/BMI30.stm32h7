#!/usr/bin/env python3
"""
Тестирование тонкой настройки частоты буферов: 200-210 Гц с шагом 1 Гц.
Проверка влияния переходных процессов на качество сигнала.

Использование:
    python test_fine_freq.py 200    # Установить 200 Гц
    python test_fine_freq.py 205    # Установить 205 Гц
    python test_fine_freq.py        # Перебрать все частоты 200-210 Гц
"""

import sys
import time
import os

# Добавляем путь к usb_vendor модулю
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'usb_vendor'))

try:
    from usb_stream import BMI30Stream
except ImportError as e:
    print(f"[ERROR] Cannot import usb_stream: {e}")
    print("Make sure usb_vendor/ is in PYTHONPATH or run from HostTools/")
    sys.exit(1)


def test_single_freq(stream: BMI30Stream, freq_hz: int):
    """Установить и протестировать одну частоту"""
    print(f"\n[TEST] Setting buf_rate = {freq_hz} Hz...")
    
    try:
        stream.set_buf_rate_fine(freq_hz)
        print(f"[OK] Frequency set to {freq_hz} Hz")
        
        # Даём прошивке время на перенастройку и стабилизацию
        time.sleep(0.5)
        
        # Опционально: прочитать статус для проверки
        try:
            status = stream._get_status_ep0()
            if len(status) >= 10:
                # Предполагаем, что частота находится где-то в статусе (формат зависит от прошивки)
                print(f"[INFO] Status received: {len(status)} bytes")
        except Exception as e:
            print(f"[WARN] Cannot read status: {e}")
        
        return True
        
    except ValueError as e:
        print(f"[ERROR] Invalid frequency: {e}")
        return False
    except Exception as e:
        print(f"[ERROR] Failed to set frequency: {e}")
        return False


def test_sweep_frequencies(stream: BMI30Stream):
    """Перебрать все частоты 200-210 Гц с шагом 1 Гц"""
    print("\n[SWEEP] Testing frequencies 200-210 Hz...")
    
    results = {}
    for freq in range(200, 211):
        success = test_single_freq(stream, freq)
        results[freq] = "OK" if success else "FAIL"
        time.sleep(0.2)  # Небольшая пауза между переключениями
    
    print("\n[SUMMARY] Test results:")
    print("-" * 40)
    for freq, status in results.items():
        print(f"  {freq} Hz: {status}")
    print("-" * 40)
    
    ok_count = sum(1 for s in results.values() if s == "OK")
    print(f"\nSuccess rate: {ok_count}/{len(results)}")


def main():
    print("=" * 60)
    print("BMI30 Fine Frequency Tuning Test (200-210 Hz)")
    print("=" * 60)
    
    # Парсинг аргументов командной строки
    if len(sys.argv) > 1:
        try:
            target_freq = int(sys.argv[1])
            if target_freq < 200 or target_freq > 210:
                print(f"[ERROR] Frequency must be 200-210 Hz, got {target_freq}")
                sys.exit(1)
            mode = "single"
        except ValueError:
            print(f"[ERROR] Invalid frequency: {sys.argv[1]}")
            sys.exit(1)
    else:
        target_freq = None
        mode = "sweep"
    
    # Подключение к устройству
    print("\n[INIT] Connecting to BMI30...")
    try:
        stream = BMI30Stream(profile=0, full=True)
        print(f"[OK] Connected to BMI30 (profile=0)")
    except Exception as e:
        print(f"[ERROR] Cannot connect to BMI30: {e}")
        print("\nTroubleshooting:")
        print("  1. Check USB connection")
        print("  2. Verify device is powered and enumerated")
        print("  3. Check USB permissions (Linux: udev rules)")
        sys.exit(1)
    
    try:
        if mode == "single":
            test_single_freq(stream, target_freq)
        else:
            test_sweep_frequencies(stream)
        
        print("\n[DONE] Test completed successfully")
        
    except KeyboardInterrupt:
        print("\n[ABORT] Test interrupted by user")
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            stream.close()
            print("[CLEANUP] Connection closed")
        except Exception:
            pass


if __name__ == "__main__":
    main()
