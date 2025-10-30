#!/usr/bin/env python3
"""
Дамп первых пакетов для диагностики формата данных.
"""
import usb.core
import sys

VID, PID = 0xCAFE, 0x4001
IN_EP, OUT_EP = 0x83, 0x03
CMD_START = 0x20

dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev:
    print("[ERROR] Device not found")
    sys.exit(1)

dev.set_configuration()
dev.set_interface_altsetting(interface=2, alternate_setting=1)
print("[OK] Device configured")

dev.write(OUT_EP, bytes([CMD_START]))
print("[OK] START sent, reading first 5 packets...\n")

for i in range(5):
    try:
        data = dev.read(IN_EP, 512, timeout=2000)
        print(f"\n--- Packet {i+1}, len={len(data)} ---")
        
        # First 32 bytes
        print("Hex: ", end="")
        for j in range(min(32, len(data))):
            print(f"{data[j]:02X} ", end="")
        print()
        
        # ASCII
        print("ASCII: ", end="")
        for j in range(min(32, len(data))):
            ch = chr(data[j]) if 32 <= data[j] < 127 else '.'
            print(ch, end="")
        print()
        
        # Try to parse as frame
        if len(data) >= 8:
            ch = chr(data[0]) if 32 <= data[0] < 127 else '?'
            seq = data[1]
            ns = int.from_bytes(data[2:4], 'little')
            ts = int.from_bytes(data[4:8], 'little')
            print(f"Parsed: ch='{ch}' seq={seq} ns={ns} ts={ts}")
            
    except Exception as e:
        print(f"[ERROR] {e}")
        break

print("\nDone")
