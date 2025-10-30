#!/usr/bin/env python3
"""Quick FPS test - minimal output"""
import usb.core
import time

# Find device
dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
if not dev:
    print("Device not found!")
    exit(1)

dev.set_configuration()

# Send START
dev.write(0x03, b'\x20')
print("START sent, reading for 10 seconds...")
time.sleep(0.2)

# Read packets for 10 seconds
t_start = time.time()
count = 0
timeout_count = 0

while time.time() - t_start < 10.0:
    try:
        data = dev.read(0x83, 2048, timeout=1000)
        if len(data) > 0:
            count += 1
    except usb.core.USBTimeoutError:
        timeout_count += 1
    except Exception as e:
        print(f"Error: {e}")
        break

t_end = time.time()
elapsed = t_end - t_start

# Send STOP
try:
    dev.write(0x03, b'\x21')
except:
    pass

print(f"\n{'='*50}")
print(f"Packets received: {count}")
print(f"Time elapsed: {elapsed:.2f} seconds")
print(f"FPS: {count/elapsed:.1f}")
print(f"Timeouts: {timeout_count}")
print(f"{'='*50}")
