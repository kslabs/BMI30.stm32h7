#!/usr/bin/env python3
"""Тест непрерывного чтения 60 секунд"""
import usb.core
import usb.util
import time

VID, PID = 0xCAFE, 0x4001
IN_EP, OUT_EP = 0x83, 0x03
CMD_START, CMD_STOP = 0x20, 0x21

print("=" * 70)
print("USB CONTINUOUS READ TEST: 60 seconds")
print("=" * 70)

# Find device
dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev:
    print("[ERR] Device not found")
    exit(1)

print(f"[HOST] Device found: VID={VID:04X} PID={PID:04X}")

# Configure
try:
    dev.set_configuration()
    usb.util.claim_interface(dev, 2)
    dev.set_interface_altsetting(interface=2, alternate_setting=1)
    print("[HOST] Claimed interface #2, alt=1")
except Exception as e:
    print(f"[ERR] Config: {e}")
    exit(1)

# Setup sequence
try:
    dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x64\x00\x2C\x01\xBC\x02\x2C\x01\x00')  # SET_WINDOWS
    dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\xC8\x00\x00')  # SET_BLOCK_RATE 200Hz
    dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x01\x00')  # SET_FULL_MODE(1)
    dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x02\x00')  # SET_PROFILE(2)
    print("[HOST] Setup complete")
except Exception as e:
    print(f"[ERR] Setup: {e}")
    exit(1)

# START
print("[HOST] Sending START...")
dev.write(OUT_EP, bytes([CMD_START]), timeout=500)
# Request status to kickstart streaming (как в vendor_usb_start_and_read.py)
dev.write(OUT_EP, bytes([0x30]), timeout=500)  # GET_STATUS
time.sleep(0.3)  # Даём устройству время начать

print("[HOST] Reading for 60 seconds...")
print("Time | Pairs | A_cnt | B_cnt | STAT | Errors | Rate")
print("-" * 70)

t0 = time.time()
cnt_a = cnt_b = cnt_stat = cnt_other = 0
errors = 0
last_print = t0
bytes_total = 0

try:
    while time.time() - t0 < 60.0:
        try:
            buf = dev.read(IN_EP, 2048, timeout=2000)
            bytes_total += len(buf)
            
            # Debug первых 10 пакетов
            if bytes_total < 10000:
                print(f"[DBG] len={len(buf)} head={' '.join(f'{b:02X}' for b in buf[:min(8,len(buf))])}")
            
            # Classify frame
            if len(buf) >= 4:
                # Check for frame header first (most common)
                if buf[0] == 0x5A and buf[1] == 0xA5 and buf[2] == 0x01:
                    if buf[3] == 0x01:
                        cnt_a += 1
                    elif buf[3] == 0x02:
                        cnt_b += 1
                    else:
                        cnt_other += 1
                # Then check for STAT
                elif buf[0:4] == b'STAT':
                    cnt_stat += 1
                else:
                    cnt_other += 1
            
            # Print progress every second
            now = time.time()
            if now - last_print >= 1.0:
                elapsed = now - t0
                pairs = min(cnt_a, cnt_b)
                rate = pairs / elapsed if elapsed > 0 else 0
                print(f"{int(elapsed):4d}s | {pairs:5d} | {cnt_a:5d} | {cnt_b:5d} | {cnt_stat:4d} | {errors:6d} | {rate:5.2f} pairs/s")
                last_print = now
                
        except usb.core.USBError as e:
            errors += 1
            if errors <= 3:
                print(f"[USB_ERR] {e}")
            if errors > 50:
                print("[FATAL] Too many errors, aborting")
                break

except KeyboardInterrupt:
    print("\n[INT] Interrupted by user")

# STOP
elapsed = time.time() - t0
print("\n" + "=" * 70)
print("Stopping...")
try:
    dev.write(OUT_EP, bytes([CMD_STOP]), timeout=500)
    time.sleep(0.1)
    # Try to read final STAT
    for _ in range(3):
        try:
            buf = dev.read(IN_EP, 2048, timeout=500)
            if len(buf) >= 4 and buf[0:4] == b'STAT':
                cnt_stat += 1
                print(f"[HOST] Final STAT received ({len(buf)} bytes)")
                break
        except:
            break
except Exception as e:
    print(f"[WARN] STOP: {e}")

# Final statistics
pairs = min(cnt_a, cnt_b)
rate_avg = pairs / elapsed if elapsed > 0 else 0
throughput_mb = (bytes_total / elapsed / 1024 / 1024) if elapsed > 0 else 0

print("=" * 70)
print("FINAL RESULTS:")
print(f"  Duration:    {elapsed:.1f} seconds")
print(f"  Total pairs: {pairs}")
print(f"  A frames:    {cnt_a}")
print(f"  B frames:    {cnt_b}")
print(f"  STAT frames: {cnt_stat}")
print(f"  Errors:      {errors}")
print(f"  Other/bad:   {cnt_other}")
print(f"  Avg rate:    {rate_avg:.2f} pairs/sec")
print(f"  Throughput:  {throughput_mb:.2f} MB/s")
print(f"  Total bytes: {bytes_total:,} bytes")
print("=" * 70)

# Success criteria
if errors < 10 and pairs > 30:  # Expect at least 30 pairs in 60 sec
    print("✅ TEST PASSED")
else:
    print("❌ TEST FAILED")
