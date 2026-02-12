#!/usr/bin/env python3
"""
Захват нескольких кадров ADC и построение графика
"""

import usb.core
import usb.util
import struct
import sys
import argparse
import matplotlib
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
    ap = argparse.ArgumentParser(description='Capture ADC frames and save plot')
    ap.add_argument('--frames', type=int, default=10, help='Frames per channel to capture')
    ap.add_argument('--channel', choices=['A', 'B', 'AB'], default='AB', help='Channels to expect (default AB)')
    ap.add_argument('--png', default='adc_waveforms.png', help='Path to save PNG')
    ap.add_argument('--no-show', action='store_true', help='Do not open matplotlib window (headless)')
    args = ap.parse_args()

    # Без GUI используем Agg, чтобы писать PNG без дисплея
    if args.no_show:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

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
    print(f"Frames per channel: {args.frames}, channels: {args.channel}")
    
    # Послать START
    dev.write(EP_OUT, bytes([CMD_START]))
    print("[OK] START sent, capturing 10 frames...")
    
    frames_a = []
    frames_b = []
    need_a = 'A' in args.channel
    need_b = 'B' in args.channel
    
    try:
        # Захватить 10 пар кадров
        # Один кадр = 1856 байт = 4 USB пакета (512+512+512+320)
        buffer = bytearray()  # Буфер для накопления данных
        
        target_a = args.frames if need_a else 0
        target_b = args.frames if need_b else 0

        while (len(frames_a) < target_a) or (len(frames_b) < target_b):
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
                        if frame['channel'] == 'A' and need_a and len(frames_a) < target_a:
                            frames_a.append(frame)
                            print(f"  Captured A frame {len(frames_a)}/{target_a} ({len(frame['samples'])} samples)")
                        elif frame['channel'] == 'B' and need_b and len(frames_b) < target_b:
                            frames_b.append(frame)
                            print(f"  Captured B frame {len(frames_b)}/{target_b} ({len(frame['samples'])} samples)")
                    
                    # Если собрали достаточно, выходим
                    if len(frames_a) >= target_a and len(frames_b) >= target_b:
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
    if frames_a or frames_b:
        if need_a and need_b:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        elif need_a:
            fig, ax1 = plt.subplots(1, 1, figsize=(12, 5))
            ax2 = None
        else:
            fig, ax2 = plt.subplots(1, 1, figsize=(12, 5))
            ax1 = None

        # Канал A
        if ax1 is not None and frames_a:
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

        # Канал B
        if ax2 is not None and frames_b:
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
        plt.savefig(args.png, dpi=150)
        print(f"\n[OK] Waveform saved to: {args.png}")

        # Показать окно при необходимости
        if not args.no_show:
            plt.show()
    
    print("=" * 80)
    return 0

if __name__ == '__main__':
    sys.exit(main())
