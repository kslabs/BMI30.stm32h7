#!/usr/bin/env python3
"""
Тест скорости приёма USB данных без GUI.
Только USB чтение и подсчёт пакетов для измерения реальной пропускной способности.
"""

import sys, time, struct
import usb.core, usb.util

VENDOR = 0xCAFE
PRODUCT = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83

CMD_START = 0x20
CMD_STOP = 0x21
CMD_SET_ASYNC_MODE = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14

HDR_SIZE = 32

def find_dev():
    """Поиск устройства BMI30."""
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
    if dev is None:
        raise ValueError(f"Device {VENDOR:04X}:{PRODUCT:04X} not found")
    
    # Отцепляем драйвер если нужно (только для Linux)
    try:
        if dev.is_kernel_driver_active(INTERFACE):
            dev.detach_kernel_driver(INTERFACE)
    except (usb.core.USBError, NotImplementedError):
        pass  # Windows не поддерживает is_kernel_driver_active
    
    # Пробуем altsetting 1 (должен иметь vendor endpoints)
    cfg = dev.get_active_configuration()
    try:
        intf = cfg[(INTERFACE, 1)]
        usb.util.claim_interface(dev, INTERFACE)
        dev.set_interface_altsetting(INTERFACE, 1)
        print(f"[USB] Connected, using altsetting 1")
        return dev
    except (usb.core.USBError, KeyError):
        pass
    
    # Fallback на altsetting 0
    try:
        intf = cfg[(INTERFACE, 0)]
        usb.util.claim_interface(dev, INTERFACE)
        dev.set_interface_altsetting(INTERFACE, 0)
        print(f"[USB] Connected, using altsetting 0")
        return dev
    except (usb.core.USBError, KeyError):
        raise ValueError("No valid altsetting found for vendor interface")

def send_cmd(dev, cmd_byte, data=None):
    """Отправка команды через EP_OUT."""
    pkt = bytearray([cmd_byte])
    if data:
        pkt.extend(data)
    try:
        dev.write(EP_OUT, pkt, timeout=500)
        return True
    except usb.core.USBError as e:
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

def main():
    import argparse
    parser = argparse.ArgumentParser(description="USB RX speed test (no GUI)")
    parser.add_argument('--duration', type=int, default=30, help='Test duration in seconds (default: 30)')
    parser.add_argument('--start', action='store_true', help='Send START command before test')
    args = parser.parse_args()
    
    print(f"[TEST] USB RX Speed Test (duration: {args.duration}s)")
    print(f"[TEST] No GUI - pure USB reading benchmark")
    
    # Подключаемся
    dev = find_dev()
    
    if args.start:
        print("[CMD] Sending configuration commands...")
        send_cmd(dev, CMD_SET_ASYNC_MODE, [0x01])  # async mode
        time.sleep(0.05)
        send_cmd(dev, CMD_SET_CHMODE, [0x00])       # A-only
        time.sleep(0.05)
        send_cmd(dev, CMD_SET_FULL_MODE, [0x01])    # full buffer
        time.sleep(0.05)
        send_cmd(dev, CMD_SET_PROFILE, [0x02])      # profile 2
        time.sleep(0.05)
        print("[CMD] Sending START...")
        send_cmd(dev, CMD_START)
        time.sleep(0.2)
    
    # Статистика
    rx_buffer = bytearray()
    frames_received = 0
    bytes_received = 0
    timeouts = 0
    errors = 0
    stat_frames = 0  # Количество STAT-кадров
    unknown_frames = 0  # Неизвестные данные
    
    start_time = time.time()
    last_report_time = start_time
    last_frames = 0
    last_bytes = 0
    
    print(f"[TEST] Reading data for {args.duration} seconds...")
    print(f"[TEST] Press Ctrl+C to stop early")
    
    try:
        while (time.time() - start_time) < args.duration:
            try:
                # Читаем по 4096 байт (как в оптимизированной GUI)
                chunk = dev.read(EP_IN, 4096, timeout=100)
                bytes_received += len(chunk)
                rx_buffer += bytes(chunk)
                
                # Парсим кадры из буфера
                while True:
                    if len(rx_buffer) < 4:
                        break
                    
                    # Пропускаем STAT-кадры
                    if rx_buffer[0:4] == b'STAT':
                        stat_frames += 1  # Считаем STAT-кадры
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
                            break  # ждём добор байтов
                    
                    # Проверяем заголовок ADC кадра (magic: 0x5A 0xA5 0x01)
                    if rx_buffer[0] == 0x5A and rx_buffer[1] == 0xA5 and rx_buffer[2] == 0x01 and len(rx_buffer) >= 16:
                        total_samples = rx_buffer[12] | (rx_buffer[13] << 8)
                        frame_len = 32 + total_samples * 2
                        
                        if len(rx_buffer) < frame_len:
                            break  # Ждём добор данных
                        
                        # Полный кадр получен
                        frames_received += 1
                        rx_buffer = rx_buffer[frame_len:]
                    else:
                        # Неизвестный формат - пропускаем 1 байт
                        unknown_frames += 1
                        rx_buffer = rx_buffer[1:]
                
            except usb.core.USBError as e:
                if getattr(e, 'errno', None) in (110, 10060) or 'timed out' in str(e).lower():
                    timeouts += 1
                else:
                    errors += 1
                    print(f"[ERROR] USB error: {e}")
            
            # Отчёт каждую секунду
            now = time.time()
            if (now - last_report_time) >= 1.0:
                elapsed = now - start_time
                interval = now - last_report_time
                
                frames_delta = frames_received - last_frames
                bytes_delta = bytes_received - last_bytes
                
                frames_rate = frames_delta / interval
                bytes_rate = bytes_delta / interval / 1024  # KB/s
                
                avg_frames_rate = frames_received / elapsed
                avg_bytes_rate = bytes_received / elapsed / 1024  # KB/s
                
                print(f"[{elapsed:.1f}s] RX: {frames_rate:.1f} frames/s | "
                      f"{bytes_rate:.1f} KB/s | "
                      f"Total: {frames_received} frames | "
                      f"{bytes_received/1024:.1f} KB | "
                      f"TO: {timeouts}")
                
                last_report_time = now
                last_frames = frames_received
                last_bytes = bytes_received
    
    except KeyboardInterrupt:
        print("\n[TEST] Interrupted by user")
    
    # Финальная статистика
    total_time = time.time() - start_time
    
    print(f"\n{'='*60}")
    print(f"[RESULT] USB RX Speed Test Results:")
    print(f"{'='*60}")
    print(f"Duration:        {total_time:.2f} seconds")
    print(f"ADC frames:      {frames_received} (full buffers)")
    print(f"STAT frames:     {stat_frames}")
    print(f"Unknown bytes:   {unknown_frames}")
    print(f"Bytes received:  {bytes_received} ({bytes_received/1024/1024:.2f} MB)")
    print(f"Average rate:    {frames_received/total_time:.2f} frames/s")
    print(f"Throughput:      {bytes_received/total_time/1024:.2f} KB/s")
    print(f"Timeouts:        {timeouts}")
    print(f"Errors:          {errors}")
    print(f"{'='*60}")
    
    # Сравнение с целевой частотой 200 Hz
    target_rate = 200.0
    actual_rate = frames_received / total_time
    efficiency = (actual_rate / target_rate) * 100 if target_rate > 0 else 0
    
    print(f"\nTarget rate:     {target_rate:.0f} Hz")
    print(f"Actual rate:     {actual_rate:.2f} Hz")
    print(f"Efficiency:      {efficiency:.1f}%")
    
    if efficiency >= 95:
        print(f"✓ EXCELLENT: USB RX at {efficiency:.1f}% of target")
    elif efficiency >= 80:
        print(f"⚠ GOOD: USB RX at {efficiency:.1f}% of target")
    elif efficiency >= 50:
        print(f"⚠ MODERATE: USB RX at {efficiency:.1f}% of target")
    else:
        print(f"✗ LOW: USB RX at only {efficiency:.1f}% of target")
    
    if args.start:
        print("\n[CMD] Sending STOP...")
        send_cmd(dev, CMD_STOP)
    
    # Освобождаем устройство
    try:
        usb.util.release_interface(dev, INTERFACE)
        usb.util.dispose_resources(dev)
    except:
        pass

if __name__ == '__main__':
    main()
