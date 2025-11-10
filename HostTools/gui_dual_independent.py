#!/usr/bin/env python3
"""
GUI осциллограф для 2 НЕЗАВИСИМЫХ каналов
Показывает каналы A и B без ожидания синхронизации по seq
"""

import usb.core
import usb.util
import struct
import queue
import threading
import time
from collections import deque
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from typing import Optional

VID = 0xCAFE
PID = 0x4001
EP_IN = 0x83
HDR_SIZE = 16

def parse_hdr(b: bytes):
    if len(b) < HDR_SIZE:
        return None
    magic, ver, flags, seq, ts, ns, zc = struct.unpack_from('<HBBIIHH', b, 0)
    return {
        'magic': magic,
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'ts': ts,
        'ns': ns,
    }

class DataReader(threading.Thread):
    def __init__(self, dev, q_a: queue.Queue, q_b: queue.Queue):
        super().__init__(daemon=True)
        self.dev = dev
        self.q_a = q_a
        self.q_b = q_b
        self.stop_ev = threading.Event()
        
    def run(self):
        while not self.stop_ev.is_set():
            try:
                data = self.dev.read(EP_IN, 4096, timeout=1000)
                b = bytes(data)
                h = parse_hdr(b)
                if not h or h['magic'] != 0xA55A:
                    continue
                
                payload = b[HDR_SIZE:HDR_SIZE + h['ns']*2]
                ns = len(payload)//2
                vals = [payload[2*i] | (payload[2*i+1]<<8) for i in range(ns)]
                
                # Разделяем по каналам
                if h['flags'] == 0x01:  # A
                    try:
                        self.q_a.put_nowait((h['seq'], vals))
                    except queue.Full:
                        pass
                elif h['flags'] == 0x02:  # B
                    try:
                        self.q_b.put_nowait((h['seq'], vals))
                    except queue.Full:
                        pass
            except usb.core.USBError:
                continue
            except Exception as e:
                print(f"Read error: {e}")
                continue
    
    def stop(self):
        self.stop_ev.set()

class DualPlot:
    def __init__(self, ns=300):
        self.fig, (self.ax_a, self.ax_b) = plt.subplots(2, 1, figsize=(12, 8))
        self.ns = ns
        
        # Буферы для отображения
        self.buf_a = deque(maxlen=ns)
        self.buf_b = deque(maxlen=ns)
        
        # Инициализация линий
        self.line_a, = self.ax_a.plot([], [], 'b-', lw=1)
        self.line_b, = self.ax_b.plot([], [], 'r-', lw=1)
        
        self.ax_a.set_ylim(0, 65535)
        self.ax_a.set_xlim(0, ns)
        self.ax_a.set_title('Channel A (PA6)', fontsize=12)
        self.ax_a.set_ylabel('ADC value', fontsize=10)
        self.ax_a.grid(True, alpha=0.3)
        
        self.ax_b.set_ylim(0, 65535)
        self.ax_b.set_xlim(0, ns)
        self.ax_b.set_title('Channel B (PC4)', fontsize=12)
        self.ax_b.set_ylabel('ADC value', fontsize=10)
        self.ax_b.set_xlabel('Sample', fontsize=10)
        self.ax_b.grid(True, alpha=0.3)
        
        self.fig.tight_layout()
        
        # Статистика
        self.count_a = 0
        self.count_b = 0
        self.last_update = time.time()
        
    def update(self, frame_data):
        seq_a, vals_a, seq_b, vals_b = frame_data
        
        if vals_a:
            self.buf_a.extend(vals_a)
            self.count_a += len(vals_a)
        if vals_b:
            self.buf_b.extend(vals_b)
            self.count_b += len(vals_b)
        
        # Обновление графиков
        if self.buf_a:
            x_a = np.arange(len(self.buf_a))
            y_a = np.array(list(self.buf_a))
            self.line_a.set_data(x_a, y_a)
            self.ax_a.set_xlim(0, max(self.ns, len(self.buf_a)))
            
        if self.buf_b:
            x_b = np.arange(len(self.buf_b))
            y_b = np.array(list(self.buf_b))
            self.line_b.set_data(x_b, y_b)
            self.ax_b.set_xlim(0, max(self.ns, len(self.buf_b)))
        
        # Статус каждые 2 секунды
        now = time.time()
        if now - self.last_update >= 2.0:
            print(f"[GUI] A: {self.count_a} samples | B: {self.count_b} samples")
            self.last_update = now
        
        return self.line_a, self.line_b

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--ns', type=int, default=300, help='Samples to display')
    args = ap.parse_args()
    
    print("=" * 70)
    print("GUI ОСЦИЛЛОГРАФ - 2 НЕЗАВИСИМЫХ КАНАЛА")
    print("=" * 70)
    
    # Поиск устройства
    print("\n[1/3] Поиск USB устройства...")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"❌ Устройство не найдено (VID={VID:04X} PID={PID:04X})")
        return 1
    
    try:
        dev.set_configuration()
        dev.set_interface_altsetting(2, 1)
    except:
        pass
    
    print(f"✅ Устройство найдено")
    
    # Очереди для каналов A и B
    q_a = queue.Queue(maxsize=100)
    q_b = queue.Queue(maxsize=100)
    
    print("\n[2/3] Запуск потока чтения...")
    reader = DataReader(dev, q_a, q_b)
    reader.start()
    
    print("\n[3/3] Запуск GUI...")
    print("=" * 70)
    
    plot = DualPlot(ns=args.ns)
    
    def gen():
        while True:
            seq_a, vals_a = None, []
            seq_b, vals_b = None, []
            
            # Читаем из очереди A (неблокирующее)
            try:
                while not q_a.empty():
                    seq_a, vals_a = q_a.get_nowait()
            except queue.Empty:
                pass
            
            # Читаем из очереди B (неблокирующее)
            try:
                while not q_b.empty():
                    seq_b, vals_b = q_b.get_nowait()
            except queue.Empty:
                pass
            
            yield (seq_a, vals_a, seq_b, vals_b)
            time.sleep(0.05)  # 20 Hz refresh
    
    ani = animation.FuncAnimation(plot.fig, plot.update, gen(), interval=50, blit=False, cache_frame_data=False)
    
    def on_close(evt):
        reader.stop()
    
    plot.fig.canvas.mpl_connect('close_event', on_close)
    
    try:
        plt.show()
    except KeyboardInterrupt:
        reader.stop()
    
    print("\n✅ GUI закрыт")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
