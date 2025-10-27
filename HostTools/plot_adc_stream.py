#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Считывает поток данных двух АЦП по USB Vendor интерфейсу и строит осциллограмму.
Использует vendor_usb_start_and_read.py для получения данных и matplotlib для визуализации.
"""
import os
import sys
import struct
import time
import argparse
import usb.core
import usb.util
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

def _parse_args():
    p = argparse.ArgumentParser(description="ADC stream plotter: визуализация данных двух АЦП")
    p.add_argument('--vid', type=lambda x: int(x,16), default=int(os.getenv('VND_VID','0xCAFE'),16))
    p.add_argument('--pid', type=lambda x: int(x,16), default=int(os.getenv('VND_PID','0x4001'),16))
    p.add_argument('--intf', type=int, default=int(os.getenv('VND_INTF','2')))
    p.add_argument('--ep-in', dest='ep_in', type=lambda x: int(x,16), default=int(os.getenv('VND_EP_IN','0x83'),16))
    p.add_argument('--ep-out', dest='ep_out', type=lambda x: int(x,16), default=int(os.getenv('VND_EP_OUT','0x03'),16))
    p.add_argument('--timeout-ms', type=int, default=3000, help='USB read timeout (ms)')
    p.add_argument('--samples', type=int, default=1000, help='Количество отсчётов для отображения')
    p.add_argument('--rate-hz', type=int, default=200, help='Частота дискретизации (Hz)')
    p.add_argument('--save-png', help='Сохранить график в PNG файл вместо показа')
    p.add_argument('--duration-sec', type=float, default=5.0, help='Длительность захвата (секунд)')
    return p.parse_args()

args = _parse_args()

def find_device():
    """Находит USB устройство с заданными VID/PID"""
    dev = usb.core.find(idVendor=args.vid, idProduct=args.pid)
    if dev is None:
        print(f"[ERR] Устройство VID=0x{args.vid:04X} PID=0x{args.pid:04X} не найдено")
        sys.exit(1)
    return dev

def setup_device(dev):
    """Настраивает устройство для работы"""
    # Найдём интерфейс с нужными endpoint'ами
    cfg = None
    intf = None
    for c in dev:
        for i in c:
            eps = [ep.bEndpointAddress for ep in i]
            if args.ep_out in eps and args.ep_in in eps:
                cfg = c
                intf = i
                break
        if cfg:
            break
    
    if not cfg or not intf:
        print(f"[ERR] Не найден интерфейс с EP OUT=0x{args.ep_out:02X} IN=0x{args.ep_in:02X}")
        sys.exit(2)
    
    # Отключаем kernel driver если активен
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except:
        pass
    
    # Claim interface
    try:
        usb.util.claim_interface(dev, args.intf)
        print(f"[USB] Claimed interface #{args.intf}")
    except Exception as e:
        print(f"[WARN] Claim interface failed: {e}, продолжаем...")
    
    # Переключаем на altsetting=1
    try:
        dev.set_interface_altsetting(interface=args.intf, alternate_setting=1)
        print(f"[USB] SetInterface(IF#{args.intf}, alt=1) OK")
    except Exception as e:
        print(f"[WARN] SetInterface failed: {e}")
    
    return dev

def configure_streaming(dev):
    """Конфигурирует параметры стриминга"""
    # SET_WINDOWS: win0=(100,300), win1=(700,300)
    try:
        payload = struct.pack('<BHHHH', 0x10, 100, 300, 700, 300)
        dev.write(args.ep_out, payload, timeout=1000)
        print(f"[USB] SET_WINDOWS: (100,300) (700,300)")
    except Exception as e:
        print(f"[WARN] SET_WINDOWS failed: {e}")
    
    # SET_BLOCK_RATE
    try:
        payload = struct.pack('<BH', 0x11, args.rate_hz)
        dev.write(args.ep_out, payload, timeout=1000)
        print(f"[USB] SET_BLOCK_RATE: {args.rate_hz} Hz")
    except Exception as e:
        print(f"[WARN] SET_BLOCK_RATE failed: {e}")
    
    # SET_FULL_MODE (1 = ADC mode)
    try:
        dev.write(args.ep_out, bytes([0x13, 0x01]), timeout=1000)
        print(f"[USB] SET_FULL_MODE: ADC")
    except Exception as e:
        print(f"[WARN] SET_FULL_MODE failed: {e}")
    
    # SET_PROFILE (2 = default)
    try:
        dev.write(args.ep_out, bytes([0x14, 0x02]), timeout=1000)
        print(f"[USB] SET_PROFILE: 2")
    except Exception as e:
        print(f"[WARN] SET_PROFILE failed: {e}")

def start_streaming(dev):
    """Отправляет команду START"""
    dev.write(args.ep_out, bytes([0x20]), timeout=1000)
    print(f"[USB] START отправлен")

def stop_streaming(dev):
    """Отправляет команду STOP"""
    try:
        dev.write(args.ep_out, bytes([0x21]), timeout=1000)
        print(f"[USB] STOP отправлен")
    except:
        pass

def parse_frame(data):
    """
    Парсит фрейм данных АЦП.
    Формат: заголовок [5A A5 01 CH] где CH=01 (канал A) или 02 (канал B)
    После заголовка идут 16-битные отсчёты АЦП
    """
    if len(data) < 4:
        return None, []
    
    if data[0] != 0x5A or data[1] != 0xA5 or data[2] != 0x01:
        return None, []
    
    channel = 'A' if data[3] == 0x01 else 'B' if data[3] == 0x02 else None
    if not channel:
        return None, []
    
    # Парсим 16-битные отсчёты (little-endian)
    samples = []
    for i in range(4, len(data)-1, 2):
        if i+1 < len(data):
            sample = struct.unpack('<H', data[i:i+2])[0]
            samples.append(sample)
    
    return channel, samples

def collect_data(dev, duration_sec):
    """Собирает данные в течение заданного времени"""
    print(f"[DATA] Сбор данных в течение {duration_sec} секунд...")
    
    adc_a = []
    adc_b = []
    start_time = time.time()
    rx_buffer = bytearray()
    
    while (time.time() - start_time) < duration_sec:
        try:
            chunk = bytes(dev.read(args.ep_in, 2048, timeout=args.timeout_ms))
            rx_buffer += chunk
            
            # Ищем фреймы в буфере
            while len(rx_buffer) >= 1856:  # Ожидаемый размер фрейма
                # Ищем заголовок
                idx = -1
                for i in range(len(rx_buffer) - 3):
                    if rx_buffer[i] == 0x5A and rx_buffer[i+1] == 0xA5 and rx_buffer[i+2] == 0x01:
                        idx = i
                        break
                
                if idx == -1:
                    # Заголовок не найден, очищаем старые данные
                    if len(rx_buffer) > 2000:
                        rx_buffer = rx_buffer[-1000:]
                    break
                
                if idx > 0:
                    rx_buffer = rx_buffer[idx:]
                
                if len(rx_buffer) < 1856:
                    break
                
                frame_data = bytes(rx_buffer[:1856])
                rx_buffer = rx_buffer[1856:]
                
                channel, samples = parse_frame(frame_data)
                if channel == 'A':
                    adc_a.extend(samples)
                elif channel == 'B':
                    adc_b.extend(samples)
                
                # Выводим прогресс
                if len(adc_a) % 1000 < 50:
                    print(f"[DATA] ADC_A: {len(adc_a)}, ADC_B: {len(adc_b)} отсчётов", end='\r')
        
        except usb.core.USBError as e:
            if e.errno == 110:  # Timeout
                continue
            else:
                print(f"\n[ERR] USB ошибка: {e}")
                break
        except KeyboardInterrupt:
            print("\n[INFO] Прервано пользователем")
            break
    
    print(f"\n[DATA] Собрано: ADC_A={len(adc_a)}, ADC_B={len(adc_b)} отсчётов")
    return np.array(adc_a), np.array(adc_b)

def plot_data(adc_a, adc_b, save_path=None):
    """Строит осциллограмму"""
    # Ограничиваем количество отображаемых отсчётов
    max_samples = args.samples
    adc_a = adc_a[:max_samples]
    adc_b = adc_b[:max_samples]
    
    # Создаём временную шкалу
    time_a = np.arange(len(adc_a)) / args.rate_hz
    time_b = np.arange(len(adc_b)) / args.rate_hz
    
    # Создаём график
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
    fig.suptitle(f'Данные АЦП (VID=0x{args.vid:04X} PID=0x{args.pid:04X})', fontsize=14)
    
    # График канала A
    ax1.plot(time_a * 1000, adc_a, 'b-', linewidth=0.5, label='ADC A')
    ax1.set_ylabel('Значение АЦП')
    ax1.set_title(f'Канал A ({len(adc_a)} отсчётов)')
    ax1.grid(True, alpha=0.3)
    ax1.legend()
    
    # График канала B
    ax2.plot(time_b * 1000, adc_b, 'r-', linewidth=0.5, label='ADC B')
    ax2.set_xlabel('Время (мс)')
    ax2.set_ylabel('Значение АЦП')
    ax2.set_title(f'Канал B ({len(adc_b)} отсчётов)')
    ax2.grid(True, alpha=0.3)
    ax2.legend()
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=150)
        print(f"[PLOT] График сохранён: {save_path}")
    else:
        plt.show()

def main():
    print(f"=== ADC Stream Plotter ===")
    print(f"VID=0x{args.vid:04X} PID=0x{args.pid:04X} Rate={args.rate_hz}Hz Duration={args.duration_sec}s")
    
    try:
        # Находим и настраиваем устройство
        dev = find_device()
        dev = setup_device(dev)
        
        # Конфигурируем стриминг
        configure_streaming(dev)
        
        # Запускаем стриминг
        start_streaming(dev)
        
        # Собираем данные
        adc_a, adc_b = collect_data(dev, args.duration_sec)
        
        # Останавливаем стриминг
        stop_streaming(dev)
        
        # Строим график
        if len(adc_a) > 0 or len(adc_b) > 0:
            plot_data(adc_a, adc_b, args.save_png)
        else:
            print("[ERR] Данные не получены")
            sys.exit(3)
        
    except KeyboardInterrupt:
        print("\n[INFO] Прервано пользователем")
        sys.exit(130)
    except Exception as e:
        print(f"[ERR] Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
