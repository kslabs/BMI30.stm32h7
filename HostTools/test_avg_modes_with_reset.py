#!/usr/bin/env python3
"""
Тест скорости потока в Mode 2 (AVG_ROI) для разных значений усреднения.
Между каждым тестом выполняется перезагрузка устройства.
"""
import subprocess
import sys
import time
import os

# Значения усреднения для тестирования
AVG_VALUES = [8, 16, 32, 64]

# Длительность теста для каждого значения (секунды)
TEST_DURATION = 10

def reset_device():
    """Перезагрузка устройства через device_reset_sequence.py"""
    print("\n" + "="*60)
    print("🔄 ПЕРЕЗАГРУЗКА УСТРОЙСТВА...")
    print("="*60)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    reset_script = os.path.join(script_dir, "device_reset_sequence.py")
    
    try:
        result = subprocess.run(
            [sys.executable, reset_script],
            capture_output=True,
            text=True,
            timeout=15
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        
        # Дополнительная пауза для стабилизации
        print("⏳ Ожидание стабилизации устройства (3 сек)...")
        time.sleep(3)
        
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("⚠️  Таймаут при перезагрузке устройства")
        return False
    except Exception as e:
        print(f"❌ Ошибка при перезагрузке: {e}")
        return False


def run_test(avg_n):
    """Запуск теста для заданного значения усреднения"""
    print("\n" + "="*60)
    print(f"🧪 ТЕСТ: avg_n = {avg_n}, длительность = {TEST_DURATION} сек")
    print("="*60)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    test_script = os.path.join(script_dir, "test_avg_switch_live.py")
    
    try:
        result = subprocess.run(
            [sys.executable, test_script, 
             "--avg-list", str(avg_n),
             "--secs", str(TEST_DURATION)],
            capture_output=True,
            text=True,
            timeout=TEST_DURATION + 10
        )
        
        # Вывод результата
        output = result.stdout
        print(output)
        
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        
        # Парсинг результата
        lines = output.strip().split('\n')
        if len(lines) >= 2:
            # Последняя строка содержит результат
            result_line = lines[-1]
            return result_line
        
        return None
        
    except subprocess.TimeoutExpired:
        print(f"⚠️  Таймаут при тесте avg_n={avg_n}")
        return None
    except Exception as e:
        print(f"❌ Ошибка при тесте: {e}")
        return None


def main():
    print("="*60)
    print("ТЕСТ СКОРОСТИ ПОТОКА В MODE 2 (AVG_ROI)")
    print("="*60)
    print(f"Значения усреднения: {AVG_VALUES}")
    print(f"Длительность каждого теста: {TEST_DURATION} сек")
    print("="*60)
    
    results = []
    
    for i, avg_n in enumerate(AVG_VALUES):
        # Перезагрузка устройства перед каждым тестом
        if not reset_device():
            print(f"⚠️  Не удалось перезагрузить устройство перед тестом avg_n={avg_n}")
            print("   Продолжаю без перезагрузки...")
        
        # Запуск теста
        result = run_test(avg_n)
        if result:
            results.append(result)
    
    # Итоговая сводка
    print("\n" + "="*60)
    print("📊 ИТОГОВЫЕ РЕЗУЛЬТАТЫ")
    print("="*60)
    print("avg_n,actual_pairs_fps,expected_400_div_N,ratio_to_400N,in_errors")
    for r in results:
        print(r)
    print("="*60)


if __name__ == "__main__":
    main()
