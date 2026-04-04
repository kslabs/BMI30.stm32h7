#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Test payload format for SET_WINDOWS command"""

import struct

VND_CMD_SET_WINDOWS = 0x10

roi_start = 280
roi_len = 200

# Формат согласно тесту
payload = struct.pack('<BHHHH', VND_CMD_SET_WINDOWS, roi_start, roi_len, 0, 0)
print(f"Payload length: {len(payload)} bytes")
print(f"Payload hex: {payload.hex()}")
print(f"Payload bytes: {list(payload)}")

# Декодируем как делает прошивка
data = payload
if len(data) >= 9:
    win_start0 = (data[1] | (data[2] << 8))
    win_len0   = (data[3] | (data[4] << 8))
    win_start1 = (data[5] | (data[6] << 8))
    win_len1   = (data[7] | (data[8] << 8))
    print(f"\nДекодировано:")
    print(f"  win_start0 = {win_start0}")
    print(f"  win_len0   = {win_len0}")
    print(f"  win_start1 = {win_start1}")
    print(f"  win_len1   = {win_len1}")
else:
    print(f"\n❌ Payload слишком короткий: {len(data)} < 9")
