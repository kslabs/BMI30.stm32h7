#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Циклический тест Vendor-протокола: включает DIAG, затем чередует START/STOP
примерно раз в 1.0 секунду. Параллельно можно запустить com9_reader.py,
чтобы видеть CDC-события START/STOP и периодическую статистику.
"""
import sys
import time
import usb.core
import usb.util

VID = 0xCAFE
PID = 0x4001

# Команды
CMD_SET_FULL_MODE = 0x13
CMD_SET_BLOCK_HZ  = 0x11
CMD_START         = 0x20
CMD_STOP          = 0x21

# Интерфейс Vendor: по описанию IF#2 с EP OUT 0x03, IN 0x83
IF_NUM = 2
EP_OUT = 0x03
EP_IN  = 0x83


def find_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Device not found: VID=0x%04X PID=0x%04X" % (VID, PID))
    # Windows + libusb/WinUSB backend: is_kernel_driver_active() may be unimplemented
    try:
        if dev.is_kernel_driver_active(IF_NUM):
            try:
                dev.detach_kernel_driver(IF_NUM)
            except Exception:
                pass
    except NotImplementedError:
        # Not supported on this platform/backend — ignore
        pass
    # Ensure configuration is set; ignore if already set
    try:
        dev.set_configuration()
    except Exception:
        pass
    # Claim interface, but on Windows/WinUSB this may be a no-op; ignore errors
    try:
        usb.util.claim_interface(dev, IF_NUM)
    except Exception:
        pass
    return dev


def write_cmd(dev, data: bytes, timeout=200):
    # PyUSB on Windows expects write(ep, data, timeout=None)
    return dev.write(EP_OUT, data, timeout)


def read_in(dev, size=64, timeout=50):
    try:
        # PyUSB on Windows expects read(ep, size, timeout=None)
        return dev.read(EP_IN, size, timeout)
    except usb.core.USBError as e:
        if e.errno is None:  # timeout
            return None
        raise


def main():
    dev = find_device()
    print("Found device, claimed IF#2")
    # Включим DIAG (full_mode=0) и частоту 60 Гц
    write_cmd(dev, bytes([CMD_SET_FULL_MODE, 0x00]))
    write_cmd(dev, bytes([CMD_SET_BLOCK_HZ, 60, 0x00]))
    print("Configured DIAG 60 Hz")

    for i in range(10):
        print(f"Cycle {i+1}/10: START")
        write_cmd(dev, bytes([CMD_START]))
        t0 = time.time()
        # Немного читаем IN, чтобы выловить хотя бы один ответ/STAT
        for _ in range(20):
            data = read_in(dev, 64, timeout=50)
            if data is not None:
                # просто отметим факт приема
                print(f"  IN {len(data)} bytes: {bytes(data[:8]).hex()} ...")
                break
        dt = time.time() - t0
        # Ждем остаток до ~1.0 секунды
        if dt < 0.6:
            time.sleep(0.6 - dt)
        print("  -> STOP")
        write_cmd(dev, bytes([CMD_STOP]))
        # После STOP тоже попробуем прочитать на всякий случай STAT
        for _ in range(6):
            data = read_in(dev, 64, timeout=50)
            if data is not None:
                print(f"  IN after STOP {len(data)} bytes: {bytes(data[:8]).hex()} ...")
                break
        time.sleep(0.4)

    print("Done")
    try:
        usb.util.release_interface(dev, IF_NUM)
    except Exception:
        pass


if __name__ == '__main__':
    main()
