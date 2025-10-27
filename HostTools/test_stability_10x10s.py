#!/usr/bin/env python3
"""
Тест на стабильность: 10 запусков по 10 секунд каждый.
Проверка надёжности переподключения и повторных запусков.
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
            raise Exception(f"USB error: {e}")
    
    # Проверка заголовка
    if len(buffer) >= 4:
        if buffer[0] != 0x5A or buffer[1] != 0xA5 or buffer[2] != 0x01:
            raise ValueError(f"Bad header: {buffer[0]:02X} {buffer[1]:02X} {buffer[2]:02X} {buffer[3]:02X}")
    
    return bytes(buffer)

def run_single_test(run_number):
    """Один тест на 10 секунд"""
    print(f"\n{'='*60}")
    print(f"[RUN {run_number}/10] Начинаем тест...")
    
    try:
        dev, ep_in, ep_out = find_device()
    except Exception as e:
        print(f"[RUN {run_number}/10] ✗ ОШИБКА подключения: {e}")
        return False, 0, 0
    
    # Отправляем START
    try:
        dev.write(EP_OUT, bytes([CMD_START]), timeout=1000)
        time.sleep(0.1)
    except Exception as e:
        print(f"[RUN {run_number}/10] ✗ ОШИБКА отправки START: {e}")
        return False, 0, 0
    
    start_time = time.time()
    test_duration = 10.0
    frame_count = 0
    byte_count = 0
    error_count = 0
    
    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= test_duration:
                break
            
            try:
                frame = read_frame(ep_in, timeout=1000)
                frame_count += 1
                byte_count += len(frame)
            except Exception as e:
                error_count += 1
                if error_count > 20:
                    print(f"[RUN {run_number}/10] ✗ Слишком много ошибок!")
                    break
    
    except KeyboardInterrupt:
        print(f"\n[RUN {run_number}/10] Прервано пользователем")
        return False, 0, 0
    
    finally:
        # Останавливаем передачу
        try:
            dev.write(EP_OUT, bytes([CMD_STOP]), timeout=1000)
            time.sleep(0.2)  # Пауза перед следующим запуском
        except:
            pass
    
    duration = time.time() - start_time
    fps = frame_count / duration if duration > 0 else 0
    mbps = (byte_count / duration / (1024*1024)) if duration > 0 else 0
    
    print(f"[RUN {run_number}/10] Результат:")
    print(f"  Кадров: {frame_count} | Скорость: {mbps:.2f} MB/s | FPS: {fps:.1f} | Ошибок: {error_count}")
    
    # Успех если скорость >= 0.5 MB/s и ошибок < 5
    success = (mbps >= 0.5 and error_count < 5)
    status = "✓ OK" if success else "✗ FAIL"
    print(f"[RUN {run_number}/10] {status}")
    
    return success, mbps, error_count

def main():
    print("[STABILITY TEST] Запуск теста стабильности: 10 запусков по 10 секунд")
    print("[STABILITY TEST] Критерий успеха: >= 0.5 MB/s, < 5 ошибок в каждом запуске\n")
    
    results = []
    total_success = 0
    
    for run_num in range(1, 11):
        success, mbps, errors = run_single_test(run_num)
        results.append((success, mbps, errors))
        if success:
            total_success += 1
        
        # Пауза между запусками
        if run_num < 10:
            print(f"[PAUSE] Пауза 1 секунда перед следующим запуском...")
            time.sleep(1.0)
    
    # Итоговый отчёт
    print(f"\n{'='*60}")
    print("[ИТОГОВЫЙ РЕЗУЛЬТАТ]")
    print(f"{'='*60}")
    
    for i, (success, mbps, errors) in enumerate(results, 1):
        status = "✓" if success else "✗"
        print(f"  {status} Run {i:2d}: {mbps:5.2f} MB/s, {errors:2d} errors")
    
    print(f"{'='*60}")
    print(f"Успешных запусков: {total_success}/10")
    
    if total_success == 10:
        print("[SUCCESS] ✓✓✓ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        return 0
    elif total_success >= 8:
        print("[WARNING] ⚠ Большинство тестов успешно, но есть проблемы")
        return 1
    else:
        print("[FAIL] ✗ Недостаточная стабильность")
        return 1

if __name__ == "__main__":
    sys.exit(main())
