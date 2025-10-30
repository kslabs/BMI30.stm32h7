#!/usr/bin/env python3
"""
Проверка реальных значений ADC в одном кадре
Показывает статистику ADC1 и ADC2 отдельно
"""

import usb.core
import usb.util
import struct
import sys

VID = 0xcafe
PID = 0x4001
EP_OUT = 0x03
EP_IN = 0x83

CMD_START = 0x20
CMD_STOP = 0x21

dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev:
    print("Device not found!")
    sys.exit(1)

dev.set_configuration()
cfg = dev.get_active_configuration()
intf = cfg[(2,1)]

# Claim interface
usb.util.claim_interface(dev, 2)
dev.set_interface_altsetting(2, 1)

print("=" * 80)
print("ADC VALUES CHECK")
print("=" * 80)

# Послать START
dev.write(EP_OUT, bytes([CMD_START]))
print("[OK] START sent")

# Прочитать ОДИН кадр (1856 байт)
try:
    data = bytearray()
    while len(data) < 1856:
        chunk = dev.read(EP_IN, 2048, timeout=2000)
        data.extend(chunk)
        if len(data) >= 1856:
            break
    
    # Разобрать заголовок
    magic = (data[1] << 8) | data[0]
    channel = data[2]
    seq = data[3]
    ns = struct.unpack('<H', bytes(data[4:6]))[0]
    ts = struct.unpack('<I', bytes(data[6:10]))[0]
    reserved = struct.unpack('<H', bytes(data[10:12]))[0]
    
    print(f"\nFrame header:")
    print(f"  Magic: 0x{magic:04X} {'OK' if magic == 0xA55A else 'INVALID!'}")
    print(f"  Channel: {channel} ({'A' if channel == 1 else 'B' if channel == 2 else '?'})")
    print(f"  Seq: {seq}")
    print(f"  Samples: {ns}")
    print(f"  Timestamp: {ts}")
    
    # Распаковать сэмплы (912 штук × 2 байта)
    samples = []
    for i in range(12, min(12 + ns*2, len(data)), 2):
        val = struct.unpack('<H', bytes(data[i:i+2]))[0]
        samples.append(val)
    
    print(f"\n  Total samples parsed: {len(samples)}")
    
    # Статистика
    if samples:
        non_zero = [s for s in samples if s != 0]
        min_val = min(samples)
        max_val = max(samples)
        avg_val = sum(samples) / len(samples)
        
        print(f"\n  Non-zero samples: {len(non_zero)}/{len(samples)} ({100.0*len(non_zero)/len(samples):.1f}%)")
        print(f"  Min: {min_val}")
        print(f"  Max: {max_val}")
        print(f"  Average: {avg_val:.1f}")
        
        # Первые 20 значений
        print(f"\n  First 20 values:")
        print(f"    {samples[:20]}")
        
        # Проверка - все ли нули?
        if len(non_zero) == 0:
            print(f"\n  ⚠️  WARNING: ALL SAMPLES ARE ZERO!")
        elif len(non_zero) < 100:
            print(f"\n  ⚠️  WARNING: Very few non-zero samples! ({len(non_zero)})")
        else:
            print(f"\n  ✓ ADC data looks OK ({len(non_zero)} non-zero samples)")
    
except Exception as e:
    print(f"Error: {e}")
finally:
    # STOP
    dev.write(EP_OUT, bytes([CMD_STOP]))
    print("\n[OK] STOP sent")
    print("=" * 80)
