#!/usr/bin/env python3
"""Test script: send commands, read few packets, then exit to check CDC logs"""
import usb.core
import time

VID = 0x0483
PID = 0x5740
EP_OUT = 0x01
EP_IN = 0x81

CMD_STOP = 0x21
CMD_SET_FRAME_SAMPLES = 0x11
CMD_SET_FULL_MODE = 0x12
CMD_SET_PROFILE = 0x14
CMD_SET_CHMODE = 0x15
CMD_START = 0x20

dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev:
    print("Device not found")
    exit(1)

print("Device found")

# Send configuration
def send_cmd(data):
    dev.write(EP_OUT, bytes(data), timeout=1000)
    time.sleep(0.02)

print("Sending: STOP")
send_cmd([CMD_STOP])
time.sleep(0.2)

print("Sending: SET_FRAME_SAMPLES(1360)")
ns = 1360
send_cmd([CMD_SET_FRAME_SAMPLES, ns & 0xFF, (ns >> 8) & 0xFF])

print("Sending: SET_FULL_MODE(1)")
send_cmd([CMD_SET_FULL_MODE, 1])

print("Sending: SET_PROFILE(0)")  # Profile 0 = 1360 samples @ 200Hz
send_cmd([CMD_SET_PROFILE, 0])
time.sleep(0.3)  # Wait for profile switch

print("Sending: SET_CHMODE(0)")  # A-only
send_cmd([CMD_SET_CHMODE, 0x00])

print("Sending: START")
send_cmd([CMD_START])

print("\nReading first 5 packets...")
for i in range(5):
    try:
        data = dev.read(EP_IN, 4096, timeout=2000)
        print(f"  Packet #{i+1}: {len(data)} bytes")
        if i == 0 and len(data) >= 32:
            # Parse header
            magic = data[0] | (data[1] << 8)
            total_samples = data[10] | (data[11] << 8)
            print(f"    Magic: 0x{magic:04X}, total_samples: {total_samples}")
    except Exception as e:
        print(f"  Error: {e}")
        break

print("\nStopping stream...")
send_cmd([CMD_STOP])
time.sleep(0.1)

print("Done! Now read CDC logs from COM4")
