#!/usr/bin/env python3
"""Проверка реального размера фреймов"""
import usb.core
import time
import struct

VID = 0xCAFE
PID = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83

CMD_STOP = 0x21
CMD_START = 0x20
CMD_SET_WINDOWS = 0x10
CMD_SET_FRAME_SAMPLES = 0x17
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14
CMD_SET_CHMODE = 0x19

def le16(v):
    return [v & 0xFF, (v >> 8) & 0xFF]

def send_cmd(dev, data):
    dev.write(EP_OUT, data, timeout=1000)

def parse_hdr(b):
    if len(b) < 32:
        return None
    magic, ver, flags, seq, ts, ns = struct.unpack('<HBBHII', b[:14])
    if magic != 0xA55A:
        return None
    return {'magic': magic, 'ver': ver, 'flags': flags, 'seq': seq, 'ts': ts, 'ns': ns}

dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev:
    print("Устройство не найдено!")
    exit(1)

try:
    if dev.is_kernel_driver_active(INTERFACE):
        dev.detach_kernel_driver(INTERFACE)
except NotImplementedError:
    pass

dev.set_configuration()
usb.util.claim_interface(dev, INTERFACE)

# Конфигурируем для 1360 samples
print("Отправляем SET_FRAME_SAMPLES(1360)...")
send_cmd(dev, bytes([CMD_SET_FRAME_SAMPLES] + le16(1360)))
time.sleep(0.1)

print("Отправляем SET_FULL_MODE(1)...")
send_cmd(dev, bytes([CMD_SET_FULL_MODE, 1]))
time.sleep(0.1)

print("Отправляем SET_PROFILE(0)...")
send_cmd(dev, bytes([CMD_SET_PROFILE, 0]))
time.sleep(0.1)

print("Отправляем SET_CHMODE(0) - A-only...")
send_cmd(dev, bytes([CMD_SET_CHMODE, 0x00]))
time.sleep(0.1)

print("Отправляем STOP...")
send_cmd(dev, bytes([CMD_STOP]))
time.sleep(0.3)

print("Отправляем START...")
send_cmd(dev, bytes([CMD_START]))
time.sleep(0.5)

print("\nЧитаем первые 10 пакетов...\n")
for i in range(10):
    try:
        data = dev.read(EP_IN, 4096, timeout=2000)
        h = parse_hdr(bytes(data))
        if h:
            frame_size = len(data)
            print(f"Пакет #{i+1}: seq={h['seq']} flags=0x{h['flags']:02X} ns={h['ns']} len={frame_size} bytes")
        else:
            print(f"Пакет #{i+1}: INVALID HEADER (len={len(data)})")
    except usb.core.USBError as e:
        print(f"USB Error: {e}")
        break

print("\nОтправляем STOP...")
send_cmd(dev, bytes([CMD_STOP]))

usb.util.release_interface(dev, INTERFACE)
print("\nГотово!")
