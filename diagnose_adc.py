#!/usr/bin/env python3
"""
Комплексная диагностика ADC/DMA через COM4
Читает boot-логи и ищет критические сообщения
"""
import serial
import sys
import time
from datetime import datetime

PORT = 'COM4'
BAUD = 115200

print("="*70)
print("BMI30 ADC/DMA Diagnostic Tool")
print("="*70)
print(f"Opening {PORT} @ {BAUD}...")
print("\n⚠️  PRESS RESET BUTTON NOW to capture boot sequence!\n")

try:
    ser = serial.Serial(PORT, BAUD, timeout=0.3)
except Exception as e:
    print(f"[ERROR] Cannot open {PORT}: {e}")
    sys.exit(1)

print("Waiting for boot messages (30 seconds)...\n")
print("="*70)

start = time.time()
boot_found = False
calib_adc1 = None
calib_adc2 = None
dma_start = []
tim15_state = []
tim2_state = []
errors = []

try:
    while time.time() - start < 30:
        line = ser.readline()
        if not line:
            continue
            
        try:
            text = line.decode('ascii', errors='ignore').strip()
            if not text:
                continue
            
            # Печатаем важные сообщения
            upper = text.upper()
            is_critical = any(kw in upper for kw in [
                'CALIB', 'TIM15', 'TIM2', 'DMA', 'ADC', 'ERROR', 'FAIL',
                'STAGE', 'START', 'INIT', 'DIAG'
            ])
            
            if 'STM32' in text or 'BMI' in text or 'BOOT' in upper:
                boot_found = True
                print(f"\n🔷 {text}")
            elif is_critical:
                ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                print(f"[{ts}] {text}")
                
                # Сохраняем критические события
                if 'ADC1 calibration OK' in text:
                    calib_adc1 = True
                elif 'ADC1 calibration FAILED' in text:
                    calib_adc1 = False
                elif 'ADC2 calibration OK' in text:
                    calib_adc2 = True
                elif 'ADC2 calibration FAILED' in text:
                    calib_adc2 = False
                elif 'DMA start' in text or 'HAL_ADC_Start_DMA' in text:
                    dma_start.append(text)
                elif 'TIM15' in text:
                    tim15_state.append(text)
                elif 'TIM2' in text and ('Started' in text or 'apply' in text):
                    tim2_state.append(text)
                elif 'ERROR' in upper or 'FAIL' in upper:
                    errors.append(text)
                    
        except Exception as e:
            pass
            
except KeyboardInterrupt:
    print("\n\n[Interrupted]")
finally:
    ser.close()

# Анализ результатов
print("\n" + "="*70)
print("DIAGNOSTIC SUMMARY")
print("="*70)

print("\n1. BOOT SEQUENCE:")
if boot_found:
    print("   ✅ Boot messages detected")
else:
    print("   ❌ No boot messages (device may not have reset)")

print("\n2. ADC CALIBRATION:")
if calib_adc1 is True:
    print("   ✅ ADC1 calibration OK")
elif calib_adc1 is False:
    print("   ❌ ADC1 calibration FAILED")
else:
    print("   ⚠️  ADC1 calibration status unknown")
    
if calib_adc2 is True:
    print("   ✅ ADC2 calibration OK")
elif calib_adc2 is False:
    print("   ❌ ADC2 calibration FAILED")
else:
    print("   ⚠️  ADC2 calibration status unknown")

print("\n3. DMA START:")
if dma_start:
    print(f"   Found {len(dma_start)} DMA start events:")
    for msg in dma_start[:3]:
        print(f"     • {msg[:80]}")
else:
    print("   ❌ No DMA start messages detected")

print("\n4. TIM15 STATE:")
if tim15_state:
    print(f"   Found {len(tim15_state)} TIM15 messages:")
    for msg in tim15_state[:3]:
        print(f"     • {msg[:80]}")
else:
    print("   ❌ No TIM15 messages detected")

print("\n5. TIM2 STATE:")
if tim2_state:
    print(f"   Found {len(tim2_state)} TIM2 messages:")
    for msg in tim2_state[:3]:
        print(f"     • {msg[:80]}")
else:
    print("   ❌ No TIM2 messages detected")

print("\n6. ERRORS:")
if errors:
    print(f"   ❌ {len(errors)} errors found:")
    for err in errors[:5]:
        print(f"     • {err[:80]}")
else:
    print("   ✅ No explicit errors detected")

# Вердикт
print("\n" + "="*70)
print("VERDICT:")
print("="*70)

if calib_adc1 is True and calib_adc2 is True and dma_start:
    print("✅ ADC/DMA initialization appears successful")
    print("   → Problem may be in:")
    print("     • TIM15 not generating triggers (check RESET mode)")
    print("     • ADC not receiving external trigger")
    print("     • DMA not writing to buffers (check cache coherency)")
elif calib_adc1 is False or calib_adc2 is False:
    print("❌ ADC calibration FAILED")
    print("   → This is CRITICAL - ADC cannot work without calibration")
    print("   → Check:")
    print("     • ADC voltage reference (VREF+)")
    print("     • ADC clock configuration")
    print("     • HAL_ADCEx_Calibration_Start() return code")
elif not dma_start:
    print("❌ DMA never started")
    print("   → Check:")
    print("     • HAL_ADC_Start_DMA() is being called")
    print("     • ADC init sequence completes")
    print("     • No early Error_Handler() calls")
else:
    print("⚠️  Inconclusive - need more diagnostic data")
    print("   → Manually check COM4 output for details")

print("="*70)
