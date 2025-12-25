#!/usr/bin/env python3
"""
Оптимизированный осциллограф для BMI30 с раздельными потоками:
- USB поток (высокий приоритет): чтение @ 200 Hz
- GUI поток (низкий приоритет): отрисовка @ естественная скорость Qt
- Показывает метрики: RX rate и Display FPS
"""

import sys, time, struct, threading, queue, argparse
from pathlib import Path
import usb.core, usb.util
from typing import Optional

# USB параметры
VENDOR = 0xCAFE
PRODUCT = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83
HDR_SIZE = 32
MAX_SAMPLES = 4096  # здравый предел для кадров; всё большее считаем повреждённым

# CRC16-CCITT (0x1021, init 0xFFFF) по байтам payload, как в прошивке
def crc16_ccitt(data: bytes, poly=0x1021, init=0xFFFF):
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc

# Команды
CMD_START = 0x20
CMD_STOP = 0x21
CMD_SET_ASYNC_MODE = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14

def find_dev():
    """Поиск и инициализация устройства BMI30.

    Пробуем аккуратно выставить конфигурацию и altsetting, не падая на уже выбранной/занятой конфигурации.
    """
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
    if dev is None:
        raise SystemExit(f"Device {VENDOR:04X}:{PRODUCT:04X} not found")

    def log_once(label, err):
        print(f"[USB][WARN] {label}: {err}")

    # Минимальные действия с одноразовыми предупреждениями (Windows-драйверы часто уже выбрали конфигурацию)
    try:
        dev.set_configuration()  # default cfg=1
    except Exception as e:
        log_once("set_configuration", e)

    try:
        usb.util.claim_interface(dev, INTERFACE)
    except Exception as e:
        log_once("claim_interface", e)

    # Активный поток работает только на alt=1 (alt=0 без эндпоинтов → Invalid endpoint address)
    try:
        dev.set_interface_altsetting(INTERFACE, 1)
    except Exception as e:
        log_once("set_interface_altsetting(alt=1)", e)

    # Быстрая проверка, что выбран alt=1 с нужными EP
    try:
        cfg = dev.get_active_configuration()
        intf = cfg[(INTERFACE, 1)]
        eps = [ep.bEndpointAddress for ep in intf]
        if EP_IN not in eps or EP_OUT not in eps:
            log_once("endpoint_check", f"expected EP_OUT=0x{EP_OUT:02X}, EP_IN=0x{EP_IN:02X}, got {eps}")
    except Exception as e:
        log_once("endpoint_check", e)

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
    # Структура 32 байта (см. прошивку):
    # magic,u8 ver,u8 flags,u32 seq,u32 ts,u16 total_samples,u16 zone_cnt,
    # u32 zone_off,u32 zone_len,u32 reserved,u16 reserved2,u16 crc16
    magic, ver, flags, seq, ts, total_samples, zone_cnt, zone_off, zone_len, reserved, reserved2, crc16 = struct.unpack_from(
        '<HBBIIHHIIIHH', b, 0
    )
    # Прошивка использует:
    # - reserved (u32) как DMA/frame sequence
    # - reserved2 (u16) как buffer_index (0..7)
    dma_seq = reserved
    buf_idx = reserved2 & 0x07
    # Чёт/неч должен быть стабильным и привязанным к buffer_index, который даёт устройство
    parity = buf_idx & 0x01  # 0=even, 1=odd
    frame_seq = reserved  # DEBUG
    parity_res2 = parity  # DEBUG/legacy
    return {
        'magic': magic,
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'dma_seq': dma_seq,
        'frame_seq': frame_seq,  # DEBUG: добавлено
        'parity': parity,  # Parity из seq
        'parity_res2': parity_res2,  # DEBUG: parity из reserved2
        'ts': ts,
        'ns': total_samples,
        'zone_cnt': zone_cnt,
        'zone_off': zone_off,
        'zone_len': zone_len,
        'reserved': reserved,
        'reserved2': reserved2,
        'buf_idx': buf_idx,
        'crc16': crc16,
    }

class USBReader:
    """Поток чтения USB с высоким приоритетом."""
    
    def __init__(self, dev, stop_event):
        self.dev = dev
        self.stop_event = stop_event
        
        # Простые буферы: последний кадр для каждого типа
        self.lock = threading.Lock()
        self.frame_a_even = None  # (samples) для A even
        self.frame_a_odd = None   # (samples) для A odd
        self.frame_b_even = None  # (samples) для B even
        self.frame_b_odd = None   # (samples) для B odd
        
        # Статистика
        self.last_dma_seq_a = -1
        self.last_dma_seq_b = -1
        self.gap_a = 0
        self.gap_b = 0
        self.last_bad_log_ts = 0.0
        self.spike_log_path = Path("host_spike_log.txt")
        
        # Статистика приёма
        self.rx_count = 0
        self.rx_bytes = 0
        self.start_time = time.time()
        self.timeout_count = 0  # Счётчик таймаутов
        self.last_rx_time = time.time()  # Время последнего успешного приёма
        
    def get_latest_buffers(self):
        """Получить копии последних кадров для отображения."""
        with self.lock:
            return (self.frame_a_even, self.frame_a_odd, 
                    self.frame_b_even, self.frame_b_odd)

    def get_dma_stats(self):
        with self.lock:
            return {
                'last_a': self.last_dma_seq_a,
                'last_b': self.last_dma_seq_b,
                'gap_a': self.gap_a,
                'gap_b': self.gap_b,
            }
    
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

        # Обозначаем старт сессии в файле логов выбросов
        try:
            with self.spike_log_path.open("a", encoding="ascii", errors="ignore") as f:
                f.write(f"\n# session start {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        except Exception as e:
            print(f"[WARN] spike log file not writable: {e}")
        
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
                self.last_rx_time = time.time()
                rx_buffer += bytes(chunk)
                
            except usb.core.USBError as e:
                if getattr(e, 'errno', None) in (110, 10060) or 'timed out' in str(e).lower():
                    self.timeout_count += 1
                    # Каждые 50 таймаутов (5 секунд) печатаем предупреждение
                    if self.timeout_count % 50 == 0:
                        elapsed = time.time() - self.last_rx_time
                        print(f"[USB] WARNING: {self.timeout_count} timeouts, no data for {elapsed:.1f}s (rx_count={self.rx_count})")
                    continue
                else:
                    print(f"[USB] Error: {e}")
                    continue
            
            # Парсинг кадров из буфера
            while True:
                ch_mask = 0  # default to avoid unbound when logging spikes
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
                if rx_buffer[0] == 0x5A and rx_buffer[1] == 0xA5 and rx_buffer[2] == 0x01:
                    if len(rx_buffer) < HDR_SIZE:
                        break
                    total_samples = rx_buffer[12] | (rx_buffer[13] << 8)
                    # Отбрасываем явно некорректные размеры, чтобы не потерять синхронизацию
                    if total_samples == 0 or total_samples > MAX_SAMPLES:
                        rx_buffer = rx_buffer[1:]
                        continue
                    frame_len = HDR_SIZE + total_samples * 2
                    if len(rx_buffer) < frame_len:
                        break  # ждём пока буфер наполнится

                    frame = bytes(rx_buffer[:frame_len])
                    rx_buffer = rx_buffer[frame_len:]

                    h = parse_hdr(frame)
                    if not h or h.get('magic') != 0xA55A:
                        continue

                    # Проверяем CRC, если выставлен флаг (bit2=CRC)
                    flags = h.get('flags', 0)
                    ns = h.get('ns', total_samples)
                    payload = frame[HDR_SIZE:HDR_SIZE + ns * 2]
                    ch_mask = flags & 0x03  # 0x01=A, 0x02=B
                    if flags & 0x04:
                        crc_calc = crc16_ccitt(payload)
                        if crc_calc != h.get('crc16', 0):
                            # повреждённый кадр — пропускаем и продолжаем сдвигаться вперёд
                            rx_buffer = rx_buffer[1:]
                            continue

                    samples = struct.unpack(f'<{ns}H', payload)
                    samples_list = list(samples)

                    # Детектор утечки guard pattern (0x0000) в payload - ОТКЛЮЧЕН для производительности
                    # Гвард-паттерн работает правильно, утечки фиксируются, но проверка тормозит USB поток
                    # if samples_list:
                    #     vmin = min(samples_list)
                    #     if vmin == 0:
                    #         now = time.time()
                    #         if now - self.last_bad_log_ts > 1.0:  # не чаще 1 Гц
                    #             self.last_bad_log_ts = now
                    #             imin = samples_list.index(0)
                    #             w0 = max(imin - 11, 0); w1 = min(imin + 11 + 1, len(samples_list))
                    #             win = samples_list[w0:w1]  # 22 значения: [imin-11 : imin+11] включительно
                    #             seq = h.get('seq', 0)
                    #             dma_seq = h.get('dma_seq', -1)
                    #             log_line = f"[HOST_GUARD_LEAK] ch_mask=0x{ch_mask:02X} seq={seq} dma_seq={dma_seq} zero @ {imin} window[{w0}:{w1}]={win}"
                    #             try:
                    #                 with self.spike_log_path.open("a", encoding="ascii", errors="ignore") as f:
                    #                     f.write(log_line + "\n")
                    #             except Exception:
                    #                 pass

                    if self.rx_count < 4:
                        ch_name = 'A' if ch_mask == 0x01 else ('B' if ch_mask == 0x02 else 'Both/Single')
                        frame_seq = h.get('frame_seq', 0)
                        parity = h.get('parity', 0)
                        parity_res2 = h.get('parity_res2', 0)
                        parity_str = 'even' if parity == 0 else 'odd'
                        print(f"[DEBUG] Frame #{self.rx_count}: flags=0x{flags:02X} ({ch_name}), ns={ns}, frame_seq={frame_seq}, parity={parity_str}({parity}), res2={parity_res2}, samples={len(samples_list)}")
                    elif self.rx_count == 50:
                        print(f"[DEBUG] Frame #{self.rx_count}: Still receiving data... (total frames={self.rx_count})")
                    elif self.rx_count % 100 == 0:
                        print(f"[DEBUG] Frame #{self.rx_count}: rx_count={self.rx_count}, timeouts={self.timeout_count}")

                    with self.lock:
                        dma_seq = h.get('dma_seq', -1)
                        parity = h.get('parity', 0)  # 0=even, 1=odd
                        
                        if ch_mask == 0x01:  # Channel A
                            if parity == 0:
                                self.frame_a_even = samples_list  # Заменяем последний кадр
                            else:
                                self.frame_a_odd = samples_list
                            
                            if dma_seq >= 0 and self.last_dma_seq_a >= 0 and dma_seq > self.last_dma_seq_a + 1:
                                self.gap_a += dma_seq - self.last_dma_seq_a - 1
                            self.last_dma_seq_a = dma_seq
                            
                        elif ch_mask == 0x02:  # Channel B
                            if parity == 0:
                                self.frame_b_even = samples_list
                            else:
                                self.frame_b_odd = samples_list
                            
                            if dma_seq >= 0 and self.last_dma_seq_b >= 0 and dma_seq > self.last_dma_seq_b + 1:
                                self.gap_b += dma_seq - self.last_dma_seq_b - 1
                            self.last_dma_seq_b = dma_seq

                    self.rx_count += 1
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
        
        # Создаём 2 отдельных графика: один для Channel A, другой для Channel B
        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(14, 10))
        self.fig.suptitle('BMI30 Oscilloscope - Dual Channel ADC @ 400Hz', fontsize=14)
        
        # График 1: Channel A - показываем Even и Odd отдельными линиями
        self.ax1.set_title('Channel A', fontsize=12)
        self.ax1.set_xlabel('Sample #')
        self.ax1.set_ylabel('ADC Value')
        self.ax1.grid(True, alpha=0.3)
        
        # График 2: Channel B - показываем Even и Odd отдельными линиями
        self.ax2.set_title('Channel B', fontsize=12)
        self.ax2.set_xlabel('Sample #')
        self.ax2.set_ylabel('ADC Value')
        self.ax2.grid(True, alpha=0.3)
        
        # Линии для Channel A: Even (синяя) и Odd (голубая)
        self.line_a_even, = self.ax1.plot([], [], 'b-', linewidth=1.2, label='A Even', alpha=0.8)
        self.line_a_odd, = self.ax1.plot([], [], 'c-', linewidth=1.2, label='A Odd', alpha=0.8)
        
        # Линии для Channel B: Even (красная) и Odd (оранжевая)
        self.line_b_even, = self.ax2.plot([], [], 'r-', linewidth=1.2, label='B Even', alpha=0.8)
        self.line_b_odd, = self.ax2.plot([], [], 'orange', linewidth=1.2, label='B Odd', alpha=0.8)
        
        self.ax1.legend(loc='upper right')
        self.ax2.legend(loc='upper right')
        
        # Текст для метрик
        self.text_metrics = self.fig.text(0.02, 0.98, '', 
                                          verticalalignment='top',
                                          fontfamily='monospace',
                                          fontsize=10)
        
        # Таймер для обновления экрана (~10 Hz как просил пользователь)
        self.timer = self.fig.canvas.new_timer(interval=100)  # 100ms = 10 Hz
        self.timer.add_callback(self.update_display)
        
    def update_display(self):
        """Обновление отображения (вызывается таймером Qt с фиксированной частотой ~20 Hz)."""
        if self.stop_event.is_set():
            self.plt.close('all')
            return False
        
        # Берём копии накопительных буферов
        buf_a_even, buf_a_odd, buf_b_even, buf_b_odd = self.reader.get_latest_buffers()
        
        # Обновляем Channel A: Even и Odd отдельно
        if buf_a_even:
            x = list(range(len(buf_a_even)))
            self.line_a_even.set_data(x, buf_a_even)
        
        if buf_a_odd:
            x = list(range(len(buf_a_odd)))
            self.line_a_odd.set_data(x, buf_a_odd)
        
        # Обновляем Channel B: Even и Odd отдельно
        if buf_b_even:
            x = list(range(len(buf_b_even)))
            self.line_b_even.set_data(x, buf_b_even)
        
        if buf_b_odd:
            x = list(range(len(buf_b_odd)))
            self.line_b_odd.set_data(x, buf_b_odd)
        
        # Автомасштабирование для обоих графиков
        self.ax1.relim()
        self.ax1.autoscale_view()
        self.ax2.relim()
        self.ax2.autoscale_view()
        
        # Обновляем метрики
        usb_stats = self.reader.get_stats()
        dma_stats = self.reader.get_dma_stats()
        elapsed = time.time() - self.start_time
        display_fps = self.display_count / elapsed if elapsed > 0 else 0

        metrics_text = (
            f"USB RX:   {usb_stats['rx_rate']:.1f} frames/s  |  {usb_stats['throughput']:.1f} KB/s\n"
            f"Display:  {display_fps:.1f} FPS  |  Updates: {self.display_count}\n"
            f"Frames:   A_even={len(buf_a_even) if buf_a_even else 0}, A_odd={len(buf_a_odd) if buf_a_odd else 0}, "
            f"B_even={len(buf_b_even) if buf_b_even else 0}, B_odd={len(buf_b_odd) if buf_b_odd else 0}\n"
            f"Gaps:     A={dma_stats['gap_a']} (last={dma_stats['last_a']}), "
            f"B={dma_stats['gap_b']} (last={dma_stats['last_b']})"
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
    parser = argparse.ArgumentParser(description="BMI30 Oscilloscope - 400Hz Even/Odd Mode")
    parser.add_argument('--profile', type=int, default=0, help='Profile ID (default: 0 = 600 samples @ 400Hz)')
    parser.add_argument('--no-start', action='store_true', help='Do not send START command')
    parser.add_argument('--rx-timeout', type=int, default=100, help='USB read timeout, ms (default 100)')
    parser.add_argument('--async', dest='async_mode', action='store_true', help='Enable async mode (default OFF, paired A/B)')
    # Допущенные параметры совместимости (игнорируются, но не ломают запуск)
    parser.add_argument('--ns', type=int, default=0, help='(compat) ignored')
    parser.add_argument('--watchdog', action='store_true', help='(compat) ignored')
    args = parser.parse_args()
    
    print("="*60)
    print("BMI30 Oscilloscope - 400Hz Even/Odd Mode")
    print("="*60)
    print("Architecture:")
    print("  • USB Thread:     High priority, 400 Hz RX (even/odd)")
    print("  • Display Thread: Normal priority, natural Qt FPS")
    print("  • Metrics:        Shows RX rate and Display FPS")
    print("  • Visualization:  4 lines (A_even, A_odd, B_even, B_odd)")
    print("="*60)
    
    # Подключение к устройству
    dev = find_dev()
    
    if not args.no_start:
        print("[CMD] Stopping previous stream...")
        send_cmd(dev, CMD_STOP)
        time.sleep(0.2)
        
        print("[CMD] Configuring device...")
        # Mode byte: bit0=async(0=paired,1=async), bit7=strict_pairing(1=strict,0=independent)
        # For 400Hz even/odd mode we need: async=0 (paired), strict_pairing=1 → mode=0x80
        mode_byte = 0x80 if not args.async_mode else 0x01
        send_cmd(dev, CMD_SET_ASYNC_MODE, [mode_byte])
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
    # Передаём пользовательский таймаут через lambda, чтобы не ломать сигнатуру run
    usb_thread = threading.Thread(target=lambda: reader.run(), daemon=True, name="USB-Reader")
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
