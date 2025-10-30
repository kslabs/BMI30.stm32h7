#!/usr/bin/env python3
"""
Простой тест парсера кадров - без GUI
"""

import usb.core
import usb.util
import struct
import sys

VID = 0xcafe
PID = 0x4001
EP_OUT = 0x03
EP_IN = 0x83

CMD_START = 0x20
CMD_STOP = 0x21

def parse_frame(data):
    """Разбор одного кадра
    Заголовок 32 байта:
      [0..1]  magic = 0xA55A
      [2]     ver = 0x01
      [3]     flags: 0x01=ADC0(A), 0x02=ADC1(B), 0x80=TEST
      [4..7]  seq (u32 LE)
      [8..11] timestamp (u32 LE)
      [12..13] total_samples (u16 LE)
      [14..31] reserved/zones/crc
    Данные начинаются с байта 32
    """
    if len(data) < 32:
        print(f"[WARN] Frame too short: {len(data)} bytes")
        return None
    
    magic = struct.unpack('<H', bytes(data[0:2]))[0]
    if magic != 0xA55A:
        print(f"[WARN] Invalid magic: 0x{magic:04X} (expected 0xA55A)")
        # Вывести первые 16 байт для диагностики
        print(f"       First 16 bytes: {data[:16].hex() if len(data) >= 16 else data.hex()}")
        return None
    
    ver = data[2]
    flags = data[3]
    seq = struct.unpack('<I', bytes(data[4:8]))[0]
    ts = struct.unpack('<I', bytes(data[8:12]))[0]
    ns = struct.unpack('<H', bytes(data[12:14]))[0]
    
    # Определить канал по флагам
    if flags & 0x01:
        channel = 'A'
    elif flags & 0x02:
        channel = 'B'
    else:
        channel = '?'
    
    # Распаковать сэмплы (начинаются с байта 32)
    samples = []
    for i in range(32, min(32 + ns*2, len(data)), 2):
        val = struct.unpack('<h', bytes(data[i:i+2]))[0]  # signed 16-bit
        samples.append(val)
    
    return {
        'channel': channel,
        'seq': seq,
        'ns': ns,
        'ts': ts,
        'samples': samples,
        'flags': flags
    }

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("[ERROR] Device not found!")
        return 1
    
    dev.set_configuration()
    usb.util.claim_interface(dev, 2)
    dev.set_interface_altsetting(2, 1)
    
    print("="*80)
    print("TESTING FRAME PARSER")
    print("="*80)
    
    # Послать START
    dev.write(EP_OUT, bytes([CMD_START]))
    print("[OK] START sent")
    
    frames_captured = 0
    buffer = bytearray()
    
    try:
        # Захватить 5 кадров для теста
        while frames_captured < 5:
            try:
                # Читаем USB пакет
                chunk = dev.read(EP_IN, 2048, timeout=2000)
                print(f"\n[USB] Read {len(chunk)} bytes")
                buffer.extend(chunk)
                
                # Парсим все полные кадры из буфера
                while len(buffer) >= 1856:
                    data = bytes(buffer[:1856])
                    buffer = buffer[1856:]
                    
                    frame = parse_frame(data)
                    if frame:
                        frames_captured += 1
                        print(f"\n[FRAME #{frames_captured}]")
                        print(f"  Channel:  {frame['channel']}")
                        print(f"  Seq:      {frame['seq']}")
                        print(f"  Flags:    0x{frame['flags']:02X}")
                        print(f"  Samples:  {frame['ns']} (expected: {len(frame['samples'])})")
                        print(f"  Timestamp: {frame['ts']} ms")
                        if len(frame['samples']) > 0:
                            print(f"  First 10:  {frame['samples'][:10]}")
                            print(f"  Last 10:   {frame['samples'][-10:]}")
                        print(f"  Frame len: {len(data)} bytes")
                    
                    if frames_captured >= 5:
                        break
                        
            except usb.core.USBError as e:
                if e.errno == 110:  # Timeout
                    print("[WARN] USB timeout")
                    break
                raise
    
    finally:
        # STOP
        dev.write(EP_OUT, bytes([CMD_STOP]))
        print("\n[OK] STOP sent")
        print(f"\nBuffer remaining: {len(buffer)} bytes")
    
    print("="*80)
    print(f"Captured {frames_captured} frames successfully")
    print("="*80)
    return 0

if __name__ == '__main__':
    sys.exit(main())
