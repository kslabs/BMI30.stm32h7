#!/usr/bin/env python3
"""
Интерактивная сессия для тестирования тонкой настройки частоты буферов.
Запустите этот скрипт для пошагового тестирования функционала.

Usage:
    python interactive_fine_freq_test.py
"""

import sys
import os
import time

# Добавляем путь к usb_vendor модулю
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'usb_vendor'))

try:
    from usb_stream import BMI30Stream
except ImportError as e:
    print(f"[ERROR] Cannot import usb_stream: {e}")
    sys.exit(1)


def main():
    print("=" * 70)
    print(" BMI30 Fine Frequency Tuning - Interactive Test")
    print("=" * 70)
    print()
    
    # Подключение к устройству
    print("[1/5] Connecting to BMI30...")
    try:
        stream = BMI30Stream(profile=0, full=True)
        print("      ✓ Connected successfully (profile=0, full mode)")
    except Exception as e:
        print(f"      ✗ Failed to connect: {e}")
        return 1
    
    print()
    
    # Тест 1: Установка базовой частоты
    print("[2/5] Testing baseline frequency (200 Hz)...")
    try:
        stream.set_buf_rate_fine(200)
        print("      ✓ Set to 200 Hz")
        time.sleep(0.5)
    except Exception as e:
        print(f"      ✗ Failed: {e}")
        stream.close()
        return 1
    
    print()
    
    # Тест 2: Проверка средней частоты
    print("[3/5] Testing middle frequency (205 Hz)...")
    try:
        stream.set_buf_rate_fine(205)
        print("      ✓ Set to 205 Hz")
        time.sleep(0.5)
    except Exception as e:
        print(f"      ✗ Failed: {e}")
        stream.close()
        return 1
    
    print()
    
    # Тест 3: Проверка верхней границы
    print("[4/5] Testing upper boundary (210 Hz)...")
    try:
        stream.set_buf_rate_fine(210)
        print("      ✓ Set to 210 Hz")
        time.sleep(0.5)
    except Exception as e:
        print(f"      ✗ Failed: {e}")
        stream.close()
        return 1
    
    print()
    
    # Тест 4: Быстрое переключение
    print("[5/5] Testing rapid frequency switching...")
    try:
        for freq in [200, 202, 204, 206, 208, 210]:
            stream.set_buf_rate_fine(freq)
            print(f"      ✓ Switched to {freq} Hz")
            time.sleep(0.2)
    except Exception as e:
        print(f"      ✗ Failed: {e}")
        stream.close()
        return 1
    
    print()
    print("=" * 70)
    print(" All tests passed! ✓")
    print("=" * 70)
    print()
    
    # Интерактивный режим
    print("Interactive mode: Enter frequency (200-210) or 'q' to quit")
    print("-" * 70)
    
    while True:
        try:
            user_input = input("Frequency [Hz]: ").strip()
            
            if user_input.lower() in ['q', 'quit', 'exit']:
                break
            
            try:
                freq = int(user_input)
                if freq < 200 or freq > 210:
                    print(f"  ✗ Frequency {freq} out of range 200-210 Hz")
                    continue
                
                stream.set_buf_rate_fine(freq)
                print(f"  ✓ Set to {freq} Hz")
                
            except ValueError:
                print("  ✗ Invalid input, enter a number between 200-210")
                
        except KeyboardInterrupt:
            print("\n")
            break
        except EOFError:
            print()
            break
    
    # Закрытие соединения
    print()
    print("[CLEANUP] Closing connection...")
    try:
        stream.close()
        print("          ✓ Connection closed")
    except Exception:
        pass
    
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
