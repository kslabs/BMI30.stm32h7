#!/usr/bin/env python3
"""Читает UART для просмотра отладочных сообщений parity"""
import serial
import time

try:
    s = serial.Serial('COM4', 115200, timeout=0.3)
    time.sleep(0.3)
    
    lines = []
    for i in range(150):
        line = s.readline().decode('utf-8', 'ignore').rstrip()
        if line:
            lines.append(line)
        if len(lines) >= 60:
            break
    
    print('\n'.join(lines[:60]))
    
finally:
    try:
        s.close()
    except:
        pass
