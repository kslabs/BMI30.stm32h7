#!/usr/bin/env python3
"""10-секундный тест непрерывного чтения USB"""
import usb.core
import time

VID, PID = 0xCAFE, 0x4001
IN_EP, OUT_EP = 0x83, 0x03
CMD_START, CMD_STOP = 0x20, 0x21

dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev:
    print("[ERR] Device not found")
    exit(1)

try:
    dev.set_configuration()
    usb.util.claim_interface(dev, 2)
    dev.set_interface_altsetting(interface=2, alternate_setting=1)
except Exception as e:
    print(f"[WARN] Setup: {e}")

print("[HOST] Sending START...")
dev.write(OUT_EP, bytes([CMD_START]), timeout=500)

print("[HOST] Reading for 10 seconds...")
t0 = time.time()
cnt_a = cnt_b = cnt_stat = cnt_other = 0
errors = 0

while time.time() - t0 < 10.0:
    try:
        buf = dev.read(IN_EP, 2048, timeout=500)
        if len(buf) >= 4:
            if buf[0:4] == b'STAT':
                cnt_stat += 1
            elif buf[0:2] == b'\x5A\xA5' and buf[3] == 1:
                cnt_a += 1
            elif buf[0:2] == b'\x5A\xA5' and buf[3] == 2:
                cnt_b += 1
            else:
                cnt_other += 1
    except Exception as e:
        errors += 1
        if errors <= 3:
            print(f"[ERR] {e}")
        if errors > 10:
            break

elapsed = time.time() - t0
print(f"\n[RESULT] {elapsed:.1f} sec: A={cnt_a} B={cnt_b} STAT={cnt_stat} other={cnt_other} errors={errors}")
print(f"[RESULT] Total packets: {cnt_a + cnt_b + cnt_stat + cnt_other}")
print(f"[RESULT] Pairs: {min(cnt_a, cnt_b)}")

dev.write(OUT_EP, bytes([CMD_STOP]), timeout=500)
print("[HOST] STOP sent")
