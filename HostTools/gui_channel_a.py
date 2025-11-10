#!/usr/bin/env python3
"""
Запуск GUI для канала A (одноканальный режим)
После hardware reset, без CDC RESET
"""
import subprocess
import sys

def main():
    print("=" * 70)
    print("ЗАПУСК GUI - КАНАЛ A (PA6)")
    print("=" * 70)
    
    cmd = ["py", "-3", "HostTools/gui_oscilloscope.py", "--ns", "300", "--profile", "1", "--single"]
    
    print(f"\nКоманда: {' '.join(cmd)}")
    print("Параметры: --single (A-only), profile=1, 300 samples\n")
    
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\n⚠️  GUI закрыт")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
