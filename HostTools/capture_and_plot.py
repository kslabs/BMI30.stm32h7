#!/usr/bin/env python3
"""
Захват нескольких кадров ADC и построение графика
"""

import usb.core
import usb.util
import struct
import sys
import matplotlib.pyplot as plt
import numpy as np

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
        return None
    
    magic = struct.unpack('<H', bytes(data[0:2]))[0]
    if magic != 0xA55A:
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
        'samples': samples
    }

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("Device not found!")
        return 1
    
    dev.set_configuration()
    usb.util.claim_interface(dev, 2)
    dev.set_interface_altsetting(2, 1)
    
    print("=" * 80)
    print("CAPTURING ADC DATA FOR OSCILLOSCOPE")
    print("=" * 80)
    
    # Послать START
    dev.write(EP_OUT, bytes([CMD_START]))
    print("[OK] START sent, capturing 10 frames...")
    
    frames_a = []
    frames_b = []
    
    try:
        # Захватить 10 пар кадров
        # Один кадр = 1856 байт = 4 USB пакета (512+512+512+320)
        buffer = bytearray()  # Буфер для накопления данных
        
        while len(frames_a) < 10 or len(frames_b) < 10:
            try:
                # Читаем USB пакет и добавляем в буфер
                chunk = dev.read(EP_IN, 2048, timeout=2000)
                buffer.extend(chunk)
                
                # Если накопили достаточно для кадра, парсим
                while len(buffer) >= 1856:
                    # Берём ровно 1856 байт для одного кадра
                    data = bytes(buffer[:1856])
                    buffer = buffer[1856:]  # Удаляем обработанные байты
                    
                    frame = parse_frame(data)
                    if frame:
                        if frame['channel'] == 'A' and len(frames_a) < 10:
                            frames_a.append(frame)
                            print(f"  Captured A frame {len(frames_a)}/10 ({len(frame['samples'])} samples)")
                        elif frame['channel'] == 'B' and len(frames_b) < 10:
                            frames_b.append(frame)
                            print(f"  Captured B frame {len(frames_b)}/10 ({len(frame['samples'])} samples)")
                    
                    # Если собрали достаточно, выходим
                    if len(frames_a) >= 10 and len(frames_b) >= 10:
                        break
                        
            except usb.core.USBError as e:
                if e.errno == 110:  # Timeout
                    break  # Выходим при таймауте
                raise
    
    finally:
        # STOP
        dev.write(EP_OUT, bytes([CMD_STOP]))
        print("[OK] STOP sent")
    
    print(f"\n[OK] Captured {len(frames_a)} A frames, {len(frames_b)} B frames")
    
    # Построить график
    if frames_a and frames_b:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Канал A - первые 3 кадра
        ax1.set_title('ADC Channel A - First 3 Frames', fontsize=14, fontweight='bold')
        ax1.set_xlabel('Sample Index')
        ax1.set_ylabel('ADC Value')
        ax1.grid(True, alpha=0.3)
        
        colors_a = ['blue', 'green', 'red']
        for i, frame in enumerate(frames_a[:3]):
            offset = i * len(frame['samples'])
            x = np.arange(offset, offset + len(frame['samples']))
            ax1.plot(x, frame['samples'], color=colors_a[i], 
                    label=f"Frame {i+1} (seq={frame['seq']}, ts={frame['ts']})", linewidth=0.8)
        ax1.legend(loc='upper right')
        
        # Канал B - первые 3 кадра
        ax2.set_title('ADC Channel B - First 3 Frames', fontsize=14, fontweight='bold')
        ax2.set_xlabel('Sample Index')
        ax2.set_ylabel('ADC Value')
        ax2.grid(True, alpha=0.3)
        
        colors_b = ['cyan', 'magenta', 'orange']
        for i, frame in enumerate(frames_b[:3]):
            offset = i * len(frame['samples'])
            x = np.arange(offset, offset + len(frame['samples']))
            ax2.plot(x, frame['samples'], color=colors_b[i],
                    label=f"Frame {i+1} (seq={frame['seq']}, ts={frame['ts']})", linewidth=0.8)
        ax2.legend(loc='upper right')
        
        plt.tight_layout()
        
        # Сохранить в файл
        filename = 'adc_waveforms.png'
        plt.savefig(filename, dpi=150)
        print(f"\n[OK] Waveform saved to: {filename}")
        
        # Показать окно
        plt.show()
    
    print("=" * 80)
    return 0

if __name__ == '__main__':
    sys.exit(main())
