#!/usr/bin/env python3
"""
Простой тест: START -> READ -> NO STOP
Для измерения максимальной производительности
"""
import usb.core
import usb.util
import time

VID = 0xCAFE
PID = 0x4001
INTF = 2
EP_IN = 0x83
EP_OUT = 0x03

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    raise ValueError("Device not found")

print(f"[HOST] Found device VID={VID:04X} PID={PID:04X}")

# Detach kernel driver if needed (Linux only)
try:
    if dev.is_kernel_driver_active(INTF):
        dev.detach_kernel_driver(INTF)
        print(f"[HOST] Detached kernel driver from interface {INTF}")
except (NotImplementedError, usb.core.USBError):
    pass  # Windows doesn't need this

# Claim interface
usb.util.claim_interface(dev, INTF)
print(f"[HOST] Claimed interface {INTF}")

# Set alternate setting 1 (activate endpoints)
dev.set_interface_altsetting(INTF, 1)
print("[HOST] SetInterface(IF#2, alt=1) OK")

# Send START command (0x20)
dev.write(EP_OUT, b'\x20')
print("[HOST] START sent (0x20)")

print("\n[HOST] Reading data continuously... Press Ctrl+C to stop\n")

pair_count = 0
start_time = time.time()
last_report = start_time

try:
    while True:
        try:
            data = dev.read(EP_IN, 2048, timeout=1000)
            if len(data) > 0:
                pair_count += 1
                
                # Report every second
                now = time.time()
                if now - last_report >= 1.0:
                    elapsed = now - start_time
                    rate = pair_count / elapsed
                    print(f"[HOST] Pairs: {pair_count}, Rate: {rate:.1f}/s, Elapsed: {elapsed:.1f}s")
                    last_report = now
                    
        except usb.core.USBError as e:
            if e.errno == 110:  # Timeout
                continue
            else:
                print(f"[HOST] USB Error: {e}")
                break
                
except KeyboardInterrupt:
    print("\n[HOST] Interrupted by user")

# Calculate final stats
elapsed = time.time() - start_time
rate = pair_count / elapsed if elapsed > 0 else 0
print(f"\n[HOST] FINAL: Pairs={pair_count}, Time={elapsed:.1f}s, Rate={rate:.1f}/s")

# Release interface
usb.util.release_interface(dev, INTF)
print("[HOST] Released interface")

# NOTE: Deliberately NOT sending STOP to keep streaming active
print("[HOST] NOT sending STOP - device will keep streaming")
