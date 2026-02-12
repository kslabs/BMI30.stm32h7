#!/usr/bin/env python3
"""
Читает живые логи после прошивки (без RESET)
Проверяет наличие DMA callbacks
"""
import serial
import sys
import time

PORT = 'COM4'
BAUD = 115200

print("Opening COM4...")
try:
    ser = serial.Serial(PORT, BAUD, timeout=0.5)
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)

print("Reading live logs (30 sec). Looking for [ADC][CB]...\n")

start = time.time()
cb_count = 0
zero_count = 0

try:
    while time.time() - start < 30:
        line = ser.readline().decode('ascii', errors='ignore').strip()
        if line:
            if '[ADC][CB]' in line:
                cb_count += 1
                print(f"✅ {line}")
            elif 'zero buffer detected' in line:
                zero_count += 1
                if zero_count < 5:
                    print(f"⚠️  {line}")
            elif 'TIM15' in line or 'CFGR' in line or 'ExtTrig' in line:
                print(f"🔍 {line}")
except KeyboardInterrupt:
    pass
finally:
    ser.close()

print(f"\n{'='*70}")
print(f"Summary (30 sec):")
print(f"  DMA callbacks: {cb_count}")
print(f"  Zero buffers:  {zero_count}")
if cb_count == 0:
    print("\n❌ NO DMA CALLBACKS - ADC not receiving triggers!")
    print("   Check:")
    print("   • TIM15 TRGO configuration")
    print("   • ADC ExternalTrigConv = TIM15_TRGO")
    print("   • ADC ExternalTrigConvEdge = RISING")
elif zero_count > 10:
    print("\n⚠️  DMA callbacks OK, but buffers empty - ADC not sampling!")
else:
    print("\n✅ System appears to be working")
print(f"{'='*70}")
