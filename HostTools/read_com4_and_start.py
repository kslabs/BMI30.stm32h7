#!/usr/bin/env python3
"""
Чтение диагностики с COM4 + запуск устройства через USB
"""

import sys, time, threading
import serial
import usb.core, usb.util

VENDOR = 0xCAFE
PRODUCT = 0x4001
INTERFACE = 2
EP_OUT = 0x03

CMD_START = 0x20
CMD_STOP = 0x21
CMD_SET_ASYNC_MODE = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14

def find_dev():
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
    if dev is None:
        raise SystemExit(f"Device {VENDOR:04X}:{PRODUCT:04X} not found")
    
    try:
        dev.set_configuration()
    except:
        pass
    
    try:
        usb.util.claim_interface(dev, INTERFACE)
    except:
        pass
    
    try:
        dev.set_interface_altsetting(INTERFACE, 1)
    except:
        pass
    
    return dev

def send_cmd(dev, cmd_byte, data=None):
    pkt = bytearray([cmd_byte])
    if data:
        pkt.extend(data)
    try:
        dev.write(EP_OUT, pkt, timeout=500)
        return True
    except Exception as e:
        print(f"[ERROR] send_cmd({cmd_byte:02X}): {e}")
        return False

def read_com4(stop_event, duration=10):
    """Чтение COM4 в отдельном потоке"""
    print("[COM4] Opening COM4 @ 115200...")
    try:
        ser = serial.Serial('COM4', 115200, timeout=1)
    except Exception as e:
        print(f"[COM4] Error opening: {e}")
        return
    
    print(f"[COM4] Reading for {duration}s...")
    start = time.time()
    
    while not stop_event.is_set() and (time.time() - start < duration):
        try:
            line = ser.readline()
            if line:
                try:
                    decoded = line.decode('utf-8', errors='ignore').strip()
                    if decoded:
                        print(f"[COM4] {decoded}")
                except:
                    pass
        except Exception as e:
            print(f"[COM4] Read error: {e}")
            break
    
    ser.close()
    print("[COM4] Closed")

def main():
    print("="*60)
    print("COM4 Diagnostic Reader + USB Start")
    print("="*60)
    
    # Подключение к устройству
    dev = find_dev()
    
    # Запуск потока чтения COM4
    stop_event = threading.Event()
    com_thread = threading.Thread(target=lambda: read_com4(stop_event, duration=15), daemon=True)
    com_thread.start()
    
    # Небольшая пауза чтобы поток стартовал
    time.sleep(0.5)
    
    print("\n[USB] Configuring device...")
    send_cmd(dev, CMD_STOP)
    time.sleep(0.2)
    
    send_cmd(dev, CMD_SET_ASYNC_MODE, [0x80])  # paired mode
    time.sleep(0.05)
    send_cmd(dev, CMD_SET_CHMODE, [0x02])  # both channels
    time.sleep(0.05)
    send_cmd(dev, CMD_SET_FULL_MODE, [0x01])
    time.sleep(0.05)
    send_cmd(dev, CMD_SET_PROFILE, [0])
    time.sleep(0.05)
    
    print("[USB] Sending START...")
    send_cmd(dev, CMD_START)
    
    print("\n[MAIN] Waiting for diagnostics from COM4...\n")
    
    # Ждём завершения чтения COM4
    com_thread.join(timeout=20)
    stop_event.set()
    
    print("\n[USB] Sending STOP...")
    send_cmd(dev, CMD_STOP)
    
    print("\n" + "="*60)
    print("Done! Check output above for DMA buffer diagnostics")
    print("="*60)
    
    try:
        usb.util.release_interface(dev, INTERFACE)
        usb.util.dispose_resources(dev)
    except:
        pass

if __name__ == '__main__':
    main()
