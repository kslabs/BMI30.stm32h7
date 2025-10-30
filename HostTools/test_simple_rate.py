#!/usr/bin/env python3
"""Простой тест скорости без STAT - просто считаем пары в секунду"""
import usb.core
import usb.util
import time

VID, PID = 0xCAFE, 0x4001
IN_EP, OUT_EP = 0x83, 0x03

dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev:
    print("[ERR] Device not found")
    exit(1)

dev.set_configuration()
usb.util.claim_interface(dev, 2)
dev.set_interface_altsetting(interface=2, alternate_setting=1)

# Setup для 200Hz
dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x64\x00\x2C\x01\xBC\x02\x2C\x01\x00')
dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x11\xC8\x00')  # 200Hz (cmd=0x11, val=200)
dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x01\x00')
dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x02\x00')

print("[OK] Device configured, starting 30sec test...")
print("Time | Pairs | Rate")
print("-" * 40)

# START
dev.write(OUT_EP, bytes([0x20]), timeout=500)
time.sleep(0.1)

t0 = time.time()
cnt_a = cnt_b = 0
last_print = t0
rx_buf = bytearray()

try:
    while time.time() - t0 < 30:
        try:
            chunk = bytes(dev.read(IN_EP, 512, timeout=3000))
            rx_buf += chunk
            
            # Извлекаем фреймы
            while True:
                if len(rx_buf) < 16:
                    break
                
                # STAT?
                if rx_buf[0:4] == b'STAT':
                    flen = 64 if len(rx_buf) >= 64 else 52
                    if len(rx_buf) < flen:
                        break
                    rx_buf = rx_buf[flen:]
                    continue
                
                # Data frame?
                if rx_buf[0] == 0x5A and rx_buf[1] == 0xA5 and rx_buf[2] == 0x01:
                    total_samples = rx_buf[12] | (rx_buf[13] << 8)
                    flen = 32 + total_samples * 2
                    if len(rx_buf) < flen:
                        break
                    
                    frame = bytes(rx_buf[:flen])
                    rx_buf = rx_buf[flen:]
                    
                    if frame[3] == 0x01:
                        cnt_a += 1
                    elif frame[3] == 0x02:
                        cnt_b += 1
                    continue
                
                # Bad data
                rx_buf = rx_buf[1:]
            
            # Print каждую секунду
            now = time.time()
            if now - last_print >= 1.0:
                elapsed = now - t0
                pairs = min(cnt_a, cnt_b)
                rate = pairs / elapsed
                print(f"{int(elapsed):4d}s | {pairs:5d} | {rate:6.2f}/s")
                last_print = now
                
        except Exception as e:
            print(f"[ERR] {e}")
            break
except KeyboardInterrupt:
    print("\n[INT]")

# STOP
elapsed = time.time() - t0
dev.write(OUT_EP, bytes([0x21]), timeout=500)

pairs = min(cnt_a, cnt_b)
rate = pairs / elapsed

print(f"\n{'='*40}")
print(f"RESULT: {pairs} pairs in {elapsed:.1f}s")
print(f"Average rate: {rate:.2f} pairs/sec")
print(f"Target: 200 pairs/sec")
print(f"Efficiency: {rate/200*100:.1f}%")
print(f"{'='*40}")
