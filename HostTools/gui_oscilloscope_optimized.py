#!/usr/bin/env python3
"""
Оптимизированный осциллограф для BMI30 с раздельными потоками:
- USB поток (высокий приоритет): чтение @ 200 Hz
- GUI поток (низкий приоритет): отрисовка @ естественная скорость Qt
- Показывает метрики: RX rate и Display FPS
"""

import sys, time, struct, threading, queue, argparse
import usb.core, usb.util
from typing import Optional

# USB параметры
VENDOR = 0xCAFE
PRODUCT = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83
HDR_SIZE = 32

# Команды
CMD_START = 0x20
CMD_STOP = 0x21
CMD_SET_ASYNC_MODE = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14

def find_dev():
    """Поиск и инициализация устройства BMI30."""
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
    
    # Пробуем altsetting 1, затем 0
    for alt in (1, 0):
        try:
            dev.set_interface_altsetting(INTERFACE, alt)
            time.sleep(0.05)
            print(f"[USB] Using altsetting={alt}")
            return dev
        except:
            continue
    
    return dev

def send_cmd(dev, cmd_byte, data=None):
    """Отправка команды через EP_OUT."""
    pkt = bytearray([cmd_byte])
    if data:
        pkt.extend(data)
    try:
        dev.write(EP_OUT, pkt, timeout=500)
        return True
    except Exception as e:
        print(f"[ERROR] send_cmd({cmd_byte:02X}): {e}")
        return False

def parse_hdr(b: bytes):
    """Парсинг заголовка кадра."""
    if len(b) < HDR_SIZE:
        return None
    magic, ver, flags, seq, ts, ns, zc = struct.unpack_from('<HBBIIHH', b, 0)
    total_samples = b[12] | (b[13] << 8)
    return {
        'magic': magic,
        'ver': ver,
        'seq': seq,
        'ts': ts,
        'ns': ns,
        'total_samples': total_samples,
    }

class USBReader:
    """Поток чтения USB с высоким приоритетом."""
    
    def __init__(self, dev, stop_event):
        self.dev = dev
        self.stop_event = stop_event
        
        # Последние принятые кадры A и B (shared между потоками)
        self.lock = threading.Lock()
        self.last_frame_a = None  # (header, payload)
        self.last_frame_b = None  # (header, payload)
        self.frame_seq = 0
        
        # Статистика приёма
        self.rx_count = 0
        self.rx_bytes = 0
        self.start_time = time.time()
        
    def get_latest_frame(self):
        """Получить последние кадры A и B (thread-safe)."""
        with self.lock:
            return self.last_frame_a, self.last_frame_b, self.frame_seq
    
    def get_stats(self):
        """Получить статистику приёма."""
        elapsed = time.time() - self.start_time
        return {
            'rx_count': self.rx_count,
            'rx_bytes': self.rx_bytes,
            'rx_rate': self.rx_count / elapsed if elapsed > 0 else 0,
            'throughput': self.rx_bytes / elapsed / 1024 if elapsed > 0 else 0,
            'elapsed': elapsed,
        }
    
    def run(self):
        """Основной цикл чтения USB."""
        print("[USB] Reader thread started (high priority)")
        
        # Попытка установить высокий приоритет потока
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), 2  # THREAD_PRIORITY_HIGHEST
            )
            print("[USB] Thread priority set to HIGHEST")
        except:
            pass
        
        rx_buffer = bytearray()
        
        while not self.stop_event.is_set():
            try:
                # Чтение по 4096 байт (оптимизировано для 200 Hz)
                chunk = self.dev.read(EP_IN, 4096, timeout=100)
                self.rx_bytes += len(chunk)
                rx_buffer += bytes(chunk)
                
            except usb.core.USBError as e:
                if getattr(e, 'errno', None) in (110, 10060) or 'timed out' in str(e).lower():
                    continue
                else:
                    print(f"[USB] Error: {e}")
                    continue
            
            # Парсинг кадров из буфера
            while True:
                if len(rx_buffer) < 4:
                    break
                
                # Пропускаем STAT-кадры
                if rx_buffer[0:4] == b'STAT':
                    if len(rx_buffer) >= 84:
                        rx_buffer = rx_buffer[84:]
                        continue
                    elif len(rx_buffer) >= 64:
                        rx_buffer = rx_buffer[64:]
                        continue
                    elif len(rx_buffer) >= 52:
                        rx_buffer = rx_buffer[52:]
                        continue
                    else:
                        break
                
                # Проверяем заголовок ADC кадра (magic: 0x5A 0xA5 0x01)
                if rx_buffer[0] == 0x5A and rx_buffer[1] == 0xA5 and rx_buffer[2] == 0x01 and len(rx_buffer) >= 16:
                    total_samples = rx_buffer[12] | (rx_buffer[13] << 8)
                    frame_len = 32 + total_samples * 2
                    
                    if len(rx_buffer) < frame_len:
                        break  # Ждём добор данных
                    
                    # Полный кадр получен
                    frame = bytes(rx_buffer[:frame_len])
                    rx_buffer = rx_buffer[frame_len:]
                    
                    # Парсим заголовок
                    h = parse_hdr(frame)
                    if h and h.get('magic') == 0xA55A:
                        ns = h['ns']
                        payload = frame[HDR_SIZE:HDR_SIZE + ns * 2]
                        
                        # Конвертируем payload в список uint16 (НЕ interleaved!)
                        # Каждый кадр содержит только один канал (A или B)
                        samples = struct.unpack(f'<{ns}H', payload)
                        samples_list = list(samples)
                        
                        # Отладка: печатаем размеры первых кадров
                        flags = h.get('flags', 0)
                        if self.rx_count < 4:
                            ch_name = 'A' if flags == 0x01 else ('B' if flags == 0x02 else 'Both/Single')
                            print(f"[DEBUG] Frame #{self.rx_count}: flags=0x{flags:02X} ({ch_name}), ns={ns}, samples={len(samples_list)}")
                        
                        # Сохраняем последний кадр (thread-safe)
                        # Каждый кадр содержит 1100 семплов ОДНОГО канала
                        # Независимое обновление: каждый канал обновляется когда приходит его кадр
                        with self.lock:
                            if flags == 0x01:
                                # Явно канал A
                                self.last_frame_a = (h, samples_list)
                                self.frame_seq += 1
                            elif flags == 0x02:
                                # Явно канал B
                                self.last_frame_b = (h, samples_list)
                                self.frame_seq += 1
                            else:
                                # flags=0x00: чередуются A и B
                                # Используем seq для определения (чётный=A, нечётный=B)
                                seq = h.get('seq', 0)
                                if seq % 2 == 0:
                                    self.last_frame_a = (h, samples_list)
                                else:
                                    self.last_frame_b = (h, samples_list)
                                self.frame_seq += 1
                            self.frame_seq += 1
                        
                        self.rx_count += 1
                    else:
                        # Неизвестные данные - пропускаем байт
                        rx_buffer = rx_buffer[1:]
                else:
                    # Неизвестные данные - пропускаем байт
                    rx_buffer = rx_buffer[1:]
        
        print("[USB] Reader thread stopped")

class GUIDisplay:
    """GUI отображение с низким приоритетом."""
    
    def __init__(self, reader, stop_event):
        self.reader = reader
        self.stop_event = stop_event
        
        # Статистика отрисовки
        self.display_count = 0
        self.start_time = time.time()
        self.last_seq = -1
        
        # Matplotlib setup
        import matplotlib
        matplotlib.use('TkAgg')
        import matplotlib.pyplot as plt
        self.plt = plt
        
        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(12, 8))
        self.fig.suptitle('BMI30 Oscilloscope (Optimized)', fontsize=14)
        
        # Настройка осей
        self.ax1.set_title('Channel A')
        self.ax1.set_xlabel('Sample #')
        self.ax1.set_ylabel('ADC Value')
        self.ax1.grid(True, alpha=0.3)
        
        self.ax2.set_title('Channel B')
        self.ax2.set_xlabel('Sample #')
        self.ax2.set_ylabel('ADC Value')
        self.ax2.grid(True, alpha=0.3)
        
        # Линии графиков
        self.line_a, = self.ax1.plot([], [], 'b-', linewidth=0.5)
        self.line_b, = self.ax2.plot([], [], 'r-', linewidth=0.5)
        
        # Текст для метрик
        self.text_metrics = self.fig.text(0.02, 0.98, '', 
                                          verticalalignment='top',
                                          fontfamily='monospace',
                                          fontsize=10)
        
        # Таймер для обновления (естественная скорость Qt)
        self.timer = self.fig.canvas.new_timer(interval=50)  # ~20 Hz максимум
        self.timer.add_callback(self.update_display)
        
    def update_display(self):
        """Обновление отображения (вызывается таймером Qt с фиксированной частотой ~20 Hz)."""
        if self.stop_event.is_set():
            self.plt.close('all')
            return False
        
        # Получаем последние кадры A и B (независимо обновляемые)
        frame_a, frame_b, frame_seq = self.reader.get_latest_frame()
        
        if frame_a is None and frame_b is None:
            return True  # Ещё нет данных
        
        # Рисуем ВСЕГДА с частотой таймера (20 Hz), независимо от обновлений
        # Каждый канал показывает свой последний буфер
        
        # Извлекаем данные каналов
        h_a, payload_a = frame_a if frame_a else (None, [])
        h_b, payload_b = frame_b if frame_b else (None, [])
        
        # Обновляем графики
        if len(payload_a) > 0:
            x = list(range(len(payload_a)))
            self.line_a.set_data(x, payload_a)
            self.ax1.relim()
            self.ax1.autoscale_view()
        
        # Канал B показываем только если есть данные
        if len(payload_b) > 0:
            x_b = list(range(len(payload_b)))
            self.line_b.set_data(x_b, payload_b)
            self.ax2.relim()
            self.ax2.autoscale_view()
        else:
            # Очищаем график B если нет данных (single-channel mode)
            self.line_b.set_data([], [])
            self.ax2.set_title('Channel B (no data)')
            self.ax2.relim()
            self.ax2.autoscale_view()
        
        # Обновляем метрики
        usb_stats = self.reader.get_stats()
        elapsed = time.time() - self.start_time
        display_fps = self.display_count / elapsed if elapsed > 0 else 0
        
        # Используем заголовок канала A для метрик (или B если A нет)
        h = h_a if h_a else h_b
        seq = h['seq'] if h else 0
        ts = h['ts'] if h else 0
        
        metrics_text = (
            f"USB RX:   {usb_stats['rx_rate']:.1f} frames/s  |  {usb_stats['throughput']:.1f} KB/s\n"
            f"Display:  {display_fps:.1f} FPS  |  Frames: {self.display_count}\n"
            f"Latest:   seq={seq}  ts={ts}  A:{len(payload_a)} B:{len(payload_b)} samples"
        )
        self.text_metrics.set_text(metrics_text)
        
        self.display_count += 1
        
        # Перерисовка
        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()
        
        return True
    
    def run(self):
        """Запуск GUI (блокирует до закрытия окна)."""
        print("[GUI] Display thread started (normal priority)")
        
        self.timer.start()
        self.plt.show()
        
        print("[GUI] Display thread stopped")

def main():
    parser = argparse.ArgumentParser(description="Optimized BMI30 Oscilloscope")
    parser.add_argument('--profile', type=int, default=2, help='Profile ID (default: 2)')
    parser.add_argument('--no-start', action='store_true', help='Do not send START command')
    args = parser.parse_args()
    
    print("="*60)
    print("BMI30 Oscilloscope - Optimized Multi-threaded Version")
    print("="*60)
    print("Architecture:")
    print("  • USB Thread:     High priority, 200 Hz RX")
    print("  • Display Thread: Normal priority, natural Qt FPS")
    print("  • Metrics:        Shows RX rate and Display FPS")
    print("="*60)
    
    # Подключение к устройству
    dev = find_dev()
    
    if not args.no_start:
        print("[CMD] Stopping previous stream...")
        send_cmd(dev, CMD_STOP)
        time.sleep(0.2)
        
        print("[CMD] Configuring device...")
        send_cmd(dev, CMD_SET_ASYNC_MODE, [0x01])
        time.sleep(0.05)
        send_cmd(dev, CMD_SET_CHMODE, [0x02])  # Both channels (A+B)
        time.sleep(0.05)
        send_cmd(dev, CMD_SET_FULL_MODE, [0x01])
        time.sleep(0.05)
        send_cmd(dev, CMD_SET_PROFILE, [args.profile])
        time.sleep(0.05)
        print("[CMD] Sending START...")
        send_cmd(dev, CMD_START)
        time.sleep(0.2)
    
    # Создание потоков
    stop_event = threading.Event()
    
    reader = USBReader(dev, stop_event)
    gui = GUIDisplay(reader, stop_event)
    
    # Запуск USB потока
    usb_thread = threading.Thread(target=reader.run, daemon=True, name="USB-Reader")
    usb_thread.start()
    
    # Небольшая пауза для накопления первых данных
    time.sleep(0.5)
    
    try:
        # Запуск GUI (блокирует главный поток)
        gui.run()
    except KeyboardInterrupt:
        print("\n[MAIN] Interrupted by user")
    finally:
        print("[MAIN] Shutting down...")
        stop_event.set()
        
        # Остановка потока
        if not args.no_start:
            print("[CMD] Sending STOP...")
            send_cmd(dev, CMD_STOP)
        
        # Ждём завершения USB потока
        usb_thread.join(timeout=2.0)
        
        # Финальная статистика
        stats = reader.get_stats()
        print("\n" + "="*60)
        print("Final Statistics:")
        print("="*60)
        print(f"USB RX:      {stats['rx_count']} frames in {stats['elapsed']:.1f}s")
        print(f"RX Rate:     {stats['rx_rate']:.2f} frames/s")
        print(f"Throughput:  {stats['throughput']:.2f} KB/s")
        print(f"Display:     {gui.display_count} updates")
        elapsed_gui = time.time() - gui.start_time
        print(f"Display FPS: {gui.display_count / elapsed_gui:.2f}")
        print("="*60)
        
        # Освобождение устройства
        try:
            usb.util.release_interface(dev, INTERFACE)
            usb.util.dispose_resources(dev)
        except:
            pass

if __name__ == '__main__':
    main()
