#!/usr/bin/env python3
"""
Проверка конфигурации TIM15 и ADC после загрузки прошивки.
Ищет в логах COM4:
- [TIM15][CFG] CR2, MMS, PSC, ARR
- [ADC1][CFG] CFGR, EXTSEL, EXTEN
- [ADC][CB] колбэки DMA
"""
import serial
import time
import sys

PORT = 'COM4'
BAUD = 115200
TIMEOUT_SEC = 15

def main():
    print(f"[CHECK] Connecting to {PORT} @ {BAUD}...")
    
    try:
        with serial.Serial(PORT, BAUD, timeout=1) as ser:
            print("[CHECK] Waiting for boot logs (reset device if needed)...")
            print("=" * 70)
            
            found_tim15 = False
            found_adc1 = False
            found_callbacks = False
            callback_count = 0
            
            start_time = time.time()
            
            while time.time() - start_time < TIMEOUT_SEC:
                if ser.in_waiting:
                    try:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if not line:
                            continue
                        
                        # TIM15 конфигурация
                        if '[TIM15][CFG]' in line:
                            print(f"✅ {line}")
                            found_tim15 = True
                        
                        # ADC1 конфигурация
                        if '[ADC1][CFG]' in line or '[ADC][CFG]' in line:
                            print(f"✅ {line}")
                            found_adc1 = True
                        
                        # DMA колбэки
                        if '[ADC][CB]' in line:
                            print(f"✅ {line}")
                            found_callbacks = True
                            callback_count += 1
                            if callback_count >= 5:
                                print("[CHECK] Got 5 callbacks ✅ ADC DMA working!")
                                break
                        
                        # Ошибки
                        if 'ERROR' in line or 'Error' in line:
                            print(f"❌ {line}")
                    
                    except UnicodeDecodeError:
                        pass
            
            print("=" * 70)
            print("\n[SUMMARY]")
            print(f"  TIM15 config: {'✅ Found' if found_tim15 else '❌ NOT found'}")
            print(f"  ADC1 config:  {'✅ Found' if found_adc1 else '❌ NOT found'}")
            print(f"  DMA callbacks: {'✅ Found (' + str(callback_count) + ')' if found_callbacks else '❌ NOT found'}")
            
            if not found_callbacks:
                print("\n⚠️  NO DMA CALLBACKS = ADC not receiving TIM15 triggers!")
                print("    Check:")
                print("    1. TIM15->CR2 MMS = 2 (UPDATE mode)")
                print("    2. ADC1->CFGR EXTSEL = 0x0D (TIM15_TRGO)")
                print("    3. ADC1->CFGR EXTEN = 0x1 (rising edge)")
            
            return 0 if found_callbacks else 1
    
    except serial.SerialException as e:
        print(f"❌ Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n[CHECK] Interrupted by user")
        return 1

if __name__ == '__main__':
    sys.exit(main())
