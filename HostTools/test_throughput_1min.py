#!/usr/bin/env python3
"""
Тест на стабильность передачи данных 1 минута непрерывно.
Цель: > 1 MB/s без ошибок.
"""

import usb.core
import usb.util
import sys
import time

VID = 0xCAFE
PID = 0x4001
EP_IN = 0x83
EP_OUT = 0x03
FRAME_SIZE = 1856

CMD_START = 0x20
CMD_STOP = 0x21

def find_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise ValueError(f"Device {VID:04x}:{PID:04x} not found")
    
    # Найти интерфейс с нужными endpoints
    cfg = None
    intf = None
    for c in dev:
        for i in c:
            eps = [ep.bEndpointAddress for ep in i]
            if EP_OUT in eps and EP_IN in eps:
                cfg = c
                intf = i
                break
        if cfg:
            break
    
    if not cfg or not intf:
        raise ValueError("Interface with EP 0x03/0x83 not found")
    
    # Отключаем kernel driver если нужно
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except:
        pass
    
    dev.set_configuration(cfg.bConfigurationValue)
    usb.util.claim_interface(dev, intf)
    
    # Активируем altsetting=1 для включения endpoints!
    dev.set_interface_altsetting(intf.bInterfaceNumber, 1)
    
    # Находим endpoints
    ep_in = usb.util.find_descriptor(intf, bEndpointAddress=EP_IN)
    ep_out = usb.util.find_descriptor(intf, bEndpointAddress=EP_OUT)
    
    if not ep_in or not ep_out:
        raise ValueError("Endpoints not found")
    
    return dev, ep_in, ep_out

def read_frame(ep_in, timeout=500):
    """Читаем один полный кадр (1856 байт)"""
    buffer = bytearray()
    
    while len(buffer) < FRAME_SIZE:
        chunk_size = min(512, FRAME_SIZE - len(buffer))
        try:
            chunk = ep_in.read(chunk_size, timeout=timeout)
            buffer.extend(chunk)
        except usb.core.USBError as e:
            raise Exception(f"USB error at {len(buffer)} bytes: {e}")
    
    # Проверка заголовка
    if len(buffer) >= 4:
        if buffer[0] != 0x5A or buffer[1] != 0xA5 or buffer[2] != 0x01:
            raise ValueError(f"Bad header: {buffer[0]:02X} {buffer[1]:02X} {buffer[2]:02X} {buffer[3]:02X}")
    
    return bytes(buffer)

def main():
    print("[TEST] Поиск устройства...")
    dev, ep_in, ep_out = find_device()
    print(f"[TEST] Устройство найдено: {dev.bus}/{dev.address}")
    
    # Отправляем START
    print("[TEST] Отправка START...")
    dev.write(EP_OUT, bytes([CMD_START]), timeout=1000)
    time.sleep(0.1)  # Даём устройству время на инициализацию
    
    print("[TEST] Начинаем чтение на 60 секунд...")
    print("[TEST] Цель: > 1 MB/s стабильно\n")
    
    start_time = time.time()
    test_duration = 60.0
    frame_count = 0
    byte_count = 0
    error_count = 0
    last_report = start_time
    
    try:
        while True:
            now = time.time()
            elapsed = now - start_time
            
            if elapsed >= test_duration:
                break
            
            try:
                frame = read_frame(ep_in, timeout=1000)
                frame_count += 1
                byte_count += len(frame)
                
                # Отчёт каждые 5 секунд
                if now - last_report >= 5.0:
                    duration = now - start_time
                    fps = frame_count / duration if duration > 0 else 0
                    mbps = (byte_count / duration / (1024*1024)) if duration > 0 else 0
                    print(f"[{int(elapsed):3d}s] Frames: {frame_count:5d} | Speed: {mbps:.2f} MB/s | FPS: {fps:.1f} | Errors: {error_count}")
                    last_report = now
                
            except Exception as e:
                error_count += 1
                if error_count > 50:
                    print(f"\n[ABORT] Слишком много ошибок ({error_count})!")
                    break
                # Не выводим каждую ошибку, только счётчик
    
    except KeyboardInterrupt:
        print("\n[TEST] Прервано пользователем")
    
    finally:
        # Останавливаем передачу
        try:
            dev.write(EP_OUT, bytes([CMD_STOP]), timeout=1000)
        except:
            pass
    
    # Финальный отчёт
    duration = time.time() - start_time
    fps = frame_count / duration if duration > 0 else 0
    mbps = (byte_count / duration / (1024*1024)) if duration > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"[РЕЗУЛЬТАТ] Тест завершён:")
    print(f"  Длительность: {duration:.1f} секунд")
    print(f"  Кадров получено: {frame_count}")
    print(f"  Байт получено: {byte_count:,}")
    print(f"  Средняя скорость: {mbps:.2f} MB/s")
    print(f"  Средний FPS: {fps:.1f}")
    print(f"  Ошибок: {error_count}")
    print(f"{'='*60}")
    
    if mbps >= 1.0 and error_count == 0:
        print("[SUCCESS] ✓ Тест пройден: > 1 MB/s без ошибок!")
        return 0
    elif mbps >= 1.0:
        print(f"[WARNING] ⚠ Скорость достигнута, но были ошибки: {error_count}")
        return 1
    else:
        print(f"[FAIL] ✗ Скорость недостаточна: {mbps:.2f} MB/s < 1.0 MB/s")
        return 1

if __name__ == "__main__":
    sys.exit(main())
