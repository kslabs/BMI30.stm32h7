#!/usr/bin/env python3
"""
Пример использования команды CMD_SET_DC_ADAPT для управления DC-адаптацией.

Сценарий:
1. При детекции сигнала на RPI - заморозить адаптацию (сохранить текущие DC значения)
2. Когда сигнал пропал - возобновить обучение DC

Использование:
    python test_dc_adapt.py
"""

import sys
import time

try:
    from usb_vendor.usb_stream import USBStream
except ImportError:
    print("Ошибка: не найден модуль usb_vendor")
    print("Убедитесь что вы запускаете из корня проекта")
    sys.exit(1)


def freeze_dc_adaptation(stream: USBStream):
    """Заморозить DC-адаптацию (прекратить обучение, продолжить вычитание)"""
    print("[DC] Замораживаем адаптацию...")
    stream.set_dc_adapt(False)  # 0x00 = FREEZE
    print("[DC] Адаптация заморожена (FREEZE)")


def resume_dc_adaptation(stream: USBStream):
    """Возобновить DC-адаптацию (продолжить обучение)"""
    print("[DC] Возобновляем адаптацию...")
    stream.set_dc_adapt(True)  # 0x01 = ACTIVE
    print("[DC] Адаптация активна (ACTIVE)")


def main():
    print("=== Тест команды CMD_SET_DC_ADAPT ===\n")
    
    # Подключение к устройству
    try:
        stream = USBStream()
        print(f"Устройство подключено: VID={stream.dev.idVendor:04x} PID={stream.dev.idProduct:04x}\n")
    except Exception as e:
        print(f"Ошибка подключения к устройству: {e}")
        print("Убедитесь что устройство подключено и не используется другим процессом")
        return 1
    
    try:
        # Пример 1: Заморозить адаптацию на 5 секунд
        print("Тест 1: Замораживаем адаптацию на 5 секунд...")
        print("Смотрите на LCD экран - полоса прогресса должна стать СИНЕЙ")
        freeze_dc_adaptation(stream)
        time.sleep(5)
        
        # Возобновить адаптацию
        print("\nВозобновляем адаптацию...")
        print("Смотрите на LCD экран - полоса должна вернуть свой цвет (WHITE/GREEN/RED)")
        resume_dc_adaptation(stream)
        time.sleep(2)
        
        # Пример 2: Симуляция детекции сигнала
        print("\nТест 2: Симуляция детекции/потери сигнала...")
        for cycle in range(3):
            print(f"\n--- Цикл {cycle + 1}/3 ---")
            
            # Симуляция: сигнал обнаружен - замораживаем
            print("Сигнал обнаружен!")
            freeze_dc_adaptation(stream)
            time.sleep(2)
            
            # Симуляция: сигнал пропал - возобновляем
            print("Сигнал пропал")
            resume_dc_adaptation(stream)
            time.sleep(2)
        
        print("\n=== Тест завершен успешно! ===")
        print("\n📺 LCD ИНДИКАЦИЯ:")
        print("  • БЕЛЫЙ  - нормальная адаптация")
        print("  • СИНИЙ  - адаптация ЗАМОРОЖЕНА (FREEZE)")
        print("  • ЗЕЛЕНЫЙ - успешная запись DC")
        print("  • КРАСНЫЙ - ошибка записи DC")
        print("\nИнтеграция в код детекции сигнала:")
        print("""
# В вашем коде детекции на RPI:
from usb_vendor.usb_stream import USBStream

stream = USBStream()

# При детекции сигнала
if signal_detected():
    stream.set_dc_adapt(False)  # FREEZE
    
# Когда сигнал пропал
if signal_lost():
    stream.set_dc_adapt(True)  # ACTIVE
        """)
        
        return 0
        
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
        return 0
    except Exception as e:
        print(f"\nОшибка: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        try:
            stream.close()
        except:
            pass


if __name__ == '__main__':
    sys.exit(main())
