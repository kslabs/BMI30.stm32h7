#!/usr/bin/env python3
"""
Простая проверка: читаем 10 кадров и показываем min/max/mean значения АЦП.
"""
import sys, time, struct
import usb.core, usb.util

VENDOR = 0xCAFE
PRODUCT = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83
HDR_SIZE = 32

CMD_STOP = 0x21
CMD_START = 0x20
CMD_SET_ASYNC_MODE = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14

dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
if not dev:
    print("Device not found!")
    sys.exit(1)

try:
    dev.set_configuration()
    usb.util.claim_interface(dev, INTERFACE)
    dev.set_interface_altsetting(INTERFACE, 1)
except:
    pass

def send(cmd, data=None):
    pkt = bytearray([cmd])
    if data: pkt.extend(data)
    dev.write(EP_OUT, pkt, timeout=500)

print("Configuring...")
send(CMD_STOP)
time.sleep(0.1)
send(CMD_SET_ASYNC_MODE, [0x80])
send(CMD_SET_CHMODE, [0x02])
send(CMD_SET_FULL_MODE, [0x01])
send(CMD_SET_PROFILE, [0x00])
time.sleep(0.1)

print("Starting...")
send(CMD_START)
time.sleep(0.5)

print("\nReading 10 frames...")
rx_buf = bytearray()
frame_count = 0

while frame_count < 10:
    try:
        chunk = dev.read(EP_IN, 4096, timeout=1000)
        rx_buf += bytes(chunk)
    except usb.core.USBError as e:
        if 'timed out' not in str(e).lower():
            print(f"USB Error: {e}")
        continue
    
    while len(rx_buf) >= 4:
        # Skip STAT frames
        if rx_buf[0:4] == b'STAT':
            if len(rx_buf) >= 84:
                rx_buf = rx_buf[84:]
                continue
            elif len(rx_buf) >= 64:
                rx_buf = rx_buf[64:]
                continue
            else:
                break
        
        # ADC frame
        if rx_buf[0] == 0x5A and rx_buf[1] == 0xA5 and rx_buf[2] == 0x01:
            if len(rx_buf) < HDR_SIZE:
                break
            
            ns = rx_buf[12] | (rx_buf[13] << 8)
            frame_len = HDR_SIZE + ns * 2
            
            if len(rx_buf) < frame_len:
                break
            
            frame = bytes(rx_buf[:frame_len])
            rx_buf = rx_buf[frame_len:]
            
            # Parse payload
            payload = frame[HDR_SIZE:HDR_SIZE + ns * 2]
            samples = struct.unpack(f'<{ns}H', payload)
            
            vmin = min(samples)
            vmax = max(samples)
            vmean = sum(samples) / len(samples)
            
            flags = frame[3]
            ch = 'A' if (flags & 0x01) else 'B'
            
            print(f"Frame #{frame_count}: CH={ch} samples={len(samples)} "
                  f"min={vmin} max={vmax} mean={vmean:.1f} first5={list(samples[:5])}")
            
            frame_count += 1
            if frame_count >= 10:
                break
        else:
            rx_buf = rx_buf[1:]

print("\nDone!")
send(CMD_STOP)
