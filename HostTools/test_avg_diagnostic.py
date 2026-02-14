#!/usr/bin/env python3
"""
Диагностика Mode 2: проверка, что устройство отправляет правильно усреднённые кадры.
"""
import struct
import time
import usb.core
import usb.util

VID = 0xCAFE
PID = 0x4001
INTERFACE = 2
EP_IN = 0x83
EP_OUT = 0x03

VND_CMD_SET_WINDOWS = 0x10
VND_CMD_SET_PROFILE = 0x14
VND_CMD_SET_BLOCK_HZ = 0x11
VND_CMD_FULL_MODE = 0x13
VND_CMD_SET_STREAM_MODE = 0x1A
VND_CMD_SET_ASYNC = 0x18
VND_CMD_SET_CHMODE = 0x19
VND_CMD_START_STREAM = 0x20
VND_CMD_STOP_STREAM = 0x21

def le16(v):
    return bytes((v & 0xFF, (v >> 8) & 0xFF))

def send_cmd(dev, ep_out, payload):
    try:
        dev.write(ep_out, payload, timeout=1000)
    except Exception as e:
        print(f"Ошибка отправки команды: {e}")

dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev:
    print("Устройство не найдено!")
    exit(1)

try:
    dev.set_configuration()
    dev.set_interface_altsetting(INTERFACE, 1)
except:
    pass

print("Настройка устройства для Mode 2 (AVG_ROI), avg_n=8...")

# 1. Устанавливаем ROI 280..480 (длина 200)
payload = struct.pack('<BHHHH', VND_CMD_SET_WINDOWS, 280, 200, 0, 0)
send_cmd(dev, EP_OUT, payload)
print("✓ SET_WINDOWS: start=280, len=200")
time.sleep(0.1)

# 2. Другие настройки
send_cmd(dev, EP_OUT, bytes([VND_CMD_SET_PROFILE, 1]))
send_cmd(dev, EP_OUT, bytes([VND_CMD_SET_BLOCK_HZ]) + le16(200))
send_cmd(dev, EP_OUT, bytes([VND_CMD_FULL_MODE, 1]))
send_cmd(dev, EP_OUT, bytes([VND_CMD_SET_CHMODE, 2]))  # both A+B
send_cmd(dev, EP_OUT, bytes([VND_CMD_SET_ASYNC, 1]))

# 3. SET_STREAM_MODE = 2 (AVG_ROI), avg_n=8
send_cmd(dev, EP_OUT, bytes([VND_CMD_SET_STREAM_MODE, 2, 8]))
print("✓ SET_STREAM_MODE: mode=2, avg_n=8")
time.sleep(0.2)

# 4. STOP + START
send_cmd(dev, EP_OUT, bytes([VND_CMD_STOP_STREAM]))
time.sleep(0.2)
send_cmd(dev, EP_OUT, bytes([VND_CMD_START_STREAM]))
print("✓ START отправлен")
time.sleep(0.5)

# 5. Читаем кадры и анализируем
print("\nЧтение кадров в течение 10 секунд...")
t_start = time.time()
frame_count = {0x01: 0, 0x02: 0, 0x80: 0}  # A, B, TEST
last_seq = {0x01: -1, 0x02: -1}
seq_gaps = {0x01: 0, 0x02: 0}

while time.time() - t_start < 10.0:
    try:
        data = bytes(dev.read(EP_IN, 16384, timeout=1000))
    except usb.core.USBError:
        continue
    
    off = 0
    n = len(data)
    while off + 32 <= n:
        if data[off:off+2] != b'\x5A\xA5':
            off += 1
            continue
        
        # Парсим заголовок
        magic, ver, flags, seq = struct.unpack_from('<HBBI', data, off)
        total_samples = struct.unpack_from('<H', data, off + 12)[0]
        zone_cnt = struct.unpack_from('<H', data, off + 14)[0]
        zone1_offset = struct.unpack_from('<H', data, off + 16)[0]
        zone1_len = struct.unpack_from('<H', data, off + 18)[0]
        
        frame_len = 32 + total_samples * 2
        if off + frame_len > n:
            break
        
        # Классифицируем кадр
        base_flags = flags & 0x83  # биты 0-1 (A/B) + бит 7 (TEST)
        if base_flags in frame_count:
            frame_count[base_flags] += 1
        
        # Проверка последовательности
        if base_flags in [0x01, 0x02]:
            if last_seq[base_flags] >= 0:
                expected = last_seq[base_flags] + 1
                if seq != expected:
                    seq_gaps[base_flags] += 1
            last_seq[base_flags] = seq
        
        off += frame_len

t_end = time.time()
elapsed = t_end - t_start

# Результаты
send_cmd(dev, EP_OUT, bytes([VND_CMD_STOP_STREAM]))

print("\n" + "="*60)
print("РЕЗУЛЬТАТЫ")
print("="*60)
print(f"Продолжительность: {elapsed:.2f} сек")
print(f"Кадров A (0x01): {frame_count[0x01]} ({frame_count[0x01]/elapsed:.1f} FPS)")
print(f"Кадров B (0x02): {frame_count[0x02]} ({frame_count[0x02]/elapsed:.1f} FPS)")
print(f"Тестовых (0x80): {frame_count[0x80]}")
print(f"Пропусков seq A: {seq_gaps[0x01]}")
print(f"Пропусков seq B: {seq_gaps[0x02]}")

pairs = min(frame_count[0x01], frame_count[0x02])
pairs_fps = pairs / elapsed
expected_fps = 400.0 / 8.0  # avg_n=8

print(f"\nПары (min(A, B)): {pairs}")
print(f"FPS пар: {pairs_fps:.2f}")
print(f"Ожидалось: {expected_fps:.2f} FPS")
print(f"Соотношение: {pairs_fps/expected_fps:.2f}x")

if abs(pairs_fps - expected_fps) < 2.0:
    print("\n✅ Скорость ПРАВИЛЬНАЯ!")
else:
    print(f"\n❌ Скорость НЕПРАВИЛЬНАЯ (отклонение {abs(pairs_fps - expected_fps):.1f} FPS)")
print("="*60)
