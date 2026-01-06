#!/usr/bin/env python3
"""
Запускает устройство через USB и сразу читает диагностику из COM4.
Версия с более агрессивным таймингом для захвата загрузочных сообщений.
"""
import sys
import serial
import time
import usb.core
import usb.util
from pathlib import Path

# USB параметры
VENDOR = 0xCAFE
PRODUCT = 0x4001
INTERFACE = 2
EP_OUT = 0x03

# COM параметры
COM_PORT = "COM4"
COM_BAUD = 115200

# Команды
CMD_STOP = 0x21
CMD_SET_ASYNC_MODE = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14
CMD_START = 0x20

OUTPUT_FILE = Path(__file__).parent.parent / ".vscode" / "com4_start_diag_log.txt"

def find_dev():
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
    if dev is None:
        raise SystemExit(f"Device {VENDOR:04X}:{PRODUCT:04X} not found")
    try:
        dev.set_configuration()
        usb.util.claim_interface(dev, INTERFACE)
        dev.set_interface_altsetting(INTERFACE, 1)
    except Exception as e:
        pass  # Ignore configuration errors
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

def main():
    print("="*60)
    print("COM4 Diagnostic Capture with USB START")
    print("="*60)
    
    # Открываем COM4 ПЕРВЫМ делом
    print(f"[1/3] Opening {COM_PORT} @ {COM_BAUD}...")
    try:
        ser = serial.Serial(COM_PORT, COM_BAUD, timeout=0.05)
        ser.reset_input_buffer()
        print(f"[COM4] Opened")
    except Exception as e:
        print(f"[ERROR] Cannot open {COM_PORT}: {e}")
        return 1
    
    # Подключаемся к USB
    print(f"[2/3] Connecting to USB device...")
    try:
        dev = find_dev()
        print("[USB] Connected")
    except Exception as e:
        print(f"[ERROR] USB connection failed: {e}")
        ser.close()
        return 1
    
    # Отправляем команды
    print(f"[3/3] Configuring and starting device...")
    send_cmd(dev, CMD_STOP)
    time.sleep(0.1)
    send_cmd(dev, CMD_SET_ASYNC_MODE, [0x80])  # paired mode
    time.sleep(0.05)
    send_cmd(dev, CMD_SET_CHMODE, [0x02])  # both channels
    time.sleep(0.05)
    send_cmd(dev, CMD_SET_FULL_MODE, [0x01])
    time.sleep(0.05)
    send_cmd(dev, CMD_SET_PROFILE, [0x00])  # profile 0
    time.sleep(0.1)
    
    print("\n[USB] Sending START command...")
    send_cmd(dev, CMD_START)
    
    print("[COM4] Reading diagnostics for 15 seconds...")
    print("="*60)
    
    start_t = time.time()
    lines = []
    
    try:
        while (time.time() - start_t) < 15.0:
            try:
                data = ser.read(4096)
                if data:
                    text = data.decode('ascii', errors='ignore')
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    lines.append(text)
            except Exception as e:
                print(f"\n[ERROR] Read error: {e}")
                break
    
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted")
    
    finally:
        ser.close()
        print(f"\n[COM4] Closed")
        
        # Сохраняем
        full_text = ''.join(lines)
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(full_text, encoding='utf-8')
        print(f"[INFO] Saved {len(full_text)} bytes to {OUTPUT_FILE}")
        print("="*60)
        
        # Останавливаем устройство
        try:
            send_cmd(dev, CMD_STOP)
            usb.util.release_interface(dev, INTERFACE)
            usb.util.dispose_resources(dev)
        except:
            pass
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
