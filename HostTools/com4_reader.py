#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Простой читатель CDC (COM-порт): выводит строки, которые прошивка дублирует из Vendor-кадров.
По умолчанию использует COM4; можно переопределить первым аргументом.
"""
import sys
import time
import serial


def open_port(dev='COM4', baud=115200, timeout=1.0):
    ser = serial.Serial(dev, baudrate=baud, timeout=timeout)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser


def main():
    dev = sys.argv[1] if len(sys.argv) > 1 else 'COM4'
    ser = open_port(dev)
    print(f"Opened {dev}")
    # Отправим минимальный набор команд в Vendor через CDC-прокси:
    #  - SET_FULL_MODE(0) -> включить диагностическую пилу
    #  - SET_BLOCK_RATE(60 Гц)
    #  - START_STREAM
    try:
        ser.write(bytes([0x13, 0x00]))  # VND_CMD_SET_FULL_MODE = 0x13, full=0 (DIAG)
        ser.flush(); time.sleep(0.05)
        ser.write(bytes([0x11, 60, 0x00]))  # VND_CMD_SET_BLOCK_RATE = 0x11, 60 Hz (LE)
        ser.flush(); time.sleep(0.05)
        ser.write(bytes([0x20]))  # VND_CMD_START_STREAM = 0x20
        ser.flush()
        print("Sent: SET_FULL_MODE(0), SET_BLOCK_RATE(60), START_STREAM")
    except Exception as e:
        print(f"WARN: failed to send start cmds over CDC: {e}")
    try:
        while True:
            line = ser.readline()
            if not line:
                # таймаут — просто продолжим
                continue
            try:
                print(line.decode('utf-8', errors='replace').rstrip())
            except Exception:
                print(repr(line))
    except KeyboardInterrupt:
        pass
    finally:
        # Попросим остановить поток (необязательно)
        try:
            ser.write(bytes([0x21]))  # VND_CMD_STOP_STREAM
            ser.flush()
        except Exception:
            pass
        ser.close()


if __name__ == '__main__':
    main()
