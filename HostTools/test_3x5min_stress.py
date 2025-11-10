#!/usr/bin/env python3
"""
Стресс-тест: 3 прогона по 5 минут
Проверка стабильности передачи данных
"""
import usb.core
import usb.util
import time
import sys
import serial
from datetime import datetime

VID = 0xCAFE
PID = 0x4001

CMD_START = 0x20
CMD_STOP = 0x21
CMD_DEVICE_RESET = 0x22  # ПОЛНЫЙ RESET MCU

def find_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"❌ Устройство VID={VID:04X} PID={PID:04X} не найдено!")
        sys.exit(1)
    
    try:
        dev.set_configuration()
    except:
        pass
    
    cfg = dev.get_active_configuration()
    vendor_intf = None
    for intf in cfg:
        if intf.bInterfaceClass == 0xFF and intf.bInterfaceNumber == 2:
            if intf.bAlternateSetting == 1:
                vendor_intf = intf
                break
    
    if vendor_intf is None:
        print("❌ Vendor интерфейс не найден!")
        sys.exit(1)
    
    try:
        dev.set_interface_altsetting(2, 1)
    except:
        pass
    
    ep_in = None
    ep_out = None
    for ep in vendor_intf:
        if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
            ep_in = ep.bEndpointAddress
        else:
            ep_out = ep.bEndpointAddress
    
    return dev, ep_in, ep_out

def send_command(dev, ep_out, cmd):
    try:
        dev.write(ep_out, bytes([cmd]), timeout=1000)
        return True
    except Exception as e:
        print(f"⚠️  Ошибка отправки команды 0x{cmd:02X}: {e}")
        return False

def device_reset_and_reconnect(cdc_port="COM4", wait_time=3.0):
    """Отправить команду RESET через CDC порт и переподключиться к устройству"""
    print(f"\n🔄 DEVICE RESET через {cdc_port}...")
    
    # Отправка команды RESET через CDC порт
    reset_sent = False
    try:
        print(f"    📤 Отправка 'RESET' через {cdc_port}...")
        ser = serial.Serial(cdc_port, 115200, timeout=2)
        time.sleep(0.3)
        ser.write(b"RESET\r\n")
        ser.flush()
        time.sleep(0.2)
        ser.close()
        print(f"    ✅ RESET отправлен через CDC")
        reset_sent = True
    except Exception as e:
        print(f"    ❌ CDC RESET ошибка: {e}")
        print(f"    ⚠️  ПРОДОЛЖАЕМ БЕЗ RESET - используем обычный STOP")
        return True  # Продолжаем тест без reset
    
    print(f"⏳ Ожидание перезагрузки устройства ({wait_time:.1f}s)...")
    time.sleep(wait_time)
    
    # Переподключение к USB Vendor устройству с несколькими попытками
    print("🔌 Переподключение к USB Vendor устройству...")
    for attempt in range(1, 8):
        try:
            dev, ep_in, ep_out = find_device()
            # Дополнительная пауза для стабилизации USB после reset
            time.sleep(1.5)
            # Тестовая очистка endpoints
            try:
                dev.read(ep_in, 16384, timeout=100)
            except:
                pass  # игнорируем таймаут
            print(f"✅ Устройство готово (попытка {attempt})")
            return True
        except Exception as e:
            if attempt < 7:
                print(f"    Попытка {attempt}/7 не удалась, повтор...")
                time.sleep(1.0)
            else:
                print(f"❌ Не удалось переподключиться после {attempt} попыток: {e}")
                return False
    return False

def read_data(dev, ep_in, timeout_ms=1000):
    try:
        data = dev.read(ep_in, 16384, timeout=timeout_ms)
        return bytes(data)
    except usb.core.USBTimeoutError:
        return None
    except Exception as e:
        return None

def run_5min_test(run_number):
    """Один прогон на 5 минут"""
    print("\n" + "=" * 70)
    print(f"ПРОГОН #{run_number} - 5 МИНУТ")
    print(f"Начало: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 70)
    
    dev, ep_in, ep_out = find_device()
    
    # STOP + очистка
    print("[1] Сброс состояния (STOP)...")
    send_command(dev, ep_out, CMD_STOP)
    time.sleep(0.5)
    
    print("[2] Очистка буфера...")
    while read_data(dev, ep_in, timeout_ms=100):
        pass
    
    # START
    print(f"[3] START в {datetime.now().strftime('%H:%M:%S')}")
    if not send_command(dev, ep_out, CMD_START):
        print("❌ Не удалось отправить START!")
        return None
    
    # Сбор данных 5 минут
    print("[4] Сбор данных 5 минут...")
    print("    Формат: [время] Пакеты | Байты | Скорость | Ошибки")
    print("-" * 70)
    
    start_time = time.time()
    target_duration = 5 * 60  # 5 минут
    
    packets_total = 0
    bytes_total = 0
    errors_total = 0
    timeouts_total = 0
    last_report_time = start_time
    last_report_packets = 0
    last_report_bytes = 0
    
    max_gap = 0.0
    last_packet_time = start_time
    
    while True:
        elapsed = time.time() - start_time
        if elapsed >= target_duration:
            break
        
        data = read_data(dev, ep_in, timeout_ms=500)
        
        if data:
            now = time.time()
            gap = now - last_packet_time
            if gap > max_gap:
                max_gap = gap
            last_packet_time = now
            
            packets_total += 1
            bytes_total += len(data)
            
            # Проверка заголовка (простая валидация)
            if len(data) >= 16:
                magic = data[0]
                if magic != 0x5A:
                    errors_total += 1
        else:
            timeouts_total += 1
        
        # Отчёт каждые 10 секунд
        if time.time() - last_report_time >= 10.0:
            report_elapsed = time.time() - start_time
            interval = time.time() - last_report_time
            
            packets_interval = packets_total - last_report_packets
            bytes_interval = bytes_total - last_report_bytes
            
            rate_kbps = (bytes_interval / interval) / 1024
            
            mins = int(report_elapsed // 60)
            secs = int(report_elapsed % 60)
            
            print(f"    [{mins:02d}:{secs:02d}] {packets_total:6d} | "
                  f"{bytes_total/1024/1024:7.2f} МБ | {rate_kbps:6.1f} КБ/с | "
                  f"Err:{errors_total} TO:{timeouts_total}")
            
            last_report_time = time.time()
            last_report_packets = packets_total
            last_report_bytes = bytes_total
    
    # Финальный отчёт
    total_elapsed = time.time() - start_time
    print("-" * 70)
    
    # STOP
    print(f"[5] STOP в {datetime.now().strftime('%H:%M:%S')}")
    send_command(dev, ep_out, CMD_STOP)
    
    # Итоги прогона
    avg_rate = bytes_total / total_elapsed / 1024
    avg_packet_size = bytes_total / packets_total if packets_total > 0 else 0
    
    result = {
        'run': run_number,
        'duration': total_elapsed,
        'packets': packets_total,
        'bytes': bytes_total,
        'errors': errors_total,
        'timeouts': timeouts_total,
        'avg_rate_kbps': avg_rate,
        'avg_packet_size': avg_packet_size,
        'max_gap': max_gap
    }
    
    print("\n" + "=" * 70)
    print(f"ИТОГИ ПРОГОНА #{run_number}:")
    print(f"  Длительность:     {total_elapsed/60:.2f} мин ({total_elapsed:.1f} сек)")
    print(f"  Пакетов:          {packets_total}")
    print(f"  Байт:             {bytes_total:,} ({bytes_total/1024/1024:.2f} МБ)")
    print(f"  Средняя скорость: {avg_rate:.2f} КБ/с")
    print(f"  Средний пакет:    {avg_packet_size:.0f} байт")
    print(f"  Ошибок данных:    {errors_total}")
    print(f"  Таймауты:         {timeouts_total}")
    print(f"  Макс. пауза:      {max_gap*1000:.1f} мс")
    
    if packets_total > 0:
        print(f"\n  ✅ Прогон #{run_number} УСПЕШЕН")
    else:
        print(f"\n  ❌ Прогон #{run_number} ПРОВАЛЕН (нет данных)")
    
    print("=" * 70)
    
    return result

def main():
    print("\n" + "#" * 70)
    print("# СТРЕСС-ТЕСТ: 3 ПРОГОНА ПО 5 МИНУТ")
    print("# Общая длительность: ~15 минут")
    print("#" * 70)
    print(f"\nСтарт теста: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    results = []
    
    for run_num in range(1, 4):
        # RESET устройства перед КАЖДЫМ прогоном (включая первый)
        print(f"\n{'='*70}")
        print(f"ПЕРЕЗАГРУЗКА УСТРОЙСТВА перед прогоном #{run_num}")
        print(f"{'='*70}")
        if not device_reset_and_reconnect(cdc_port="COM4", wait_time=3.0):
            print(f"❌ Не удалось выполнить RESET перед прогоном #{run_num}")
            print("⚠️  Продолжаем без RESET...")
        print(f"\n⏸  Дополнительная пауза 2 секунды...")
        time.sleep(2)
        
        result = run_5min_test(run_num)
        if result:
            results.append(result)
    
    # ФИНАЛЬНЫЙ ОТЧЁТ
    print("\n" + "#" * 70)
    print("# ФИНАЛЬНЫЙ ОТЧЁТ")
    print("#" * 70)
    print(f"Завершение: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    if not results:
        print("❌ НЕТ УСПЕШНЫХ ПРОГОНОВ!")
        return 1
    
    print("Сводка по прогонам:")
    print("-" * 70)
    print(f"{'Прогон':^8} | {'Пакеты':>8} | {'МБ':>8} | {'КБ/с':>8} | {'Ошибки':>8} | {'TO':>6}")
    print("-" * 70)
    
    total_packets = 0
    total_bytes = 0
    total_errors = 0
    total_timeouts = 0
    
    for r in results:
        print(f"  #{r['run']:d}     | {r['packets']:8d} | {r['bytes']/1024/1024:8.2f} | "
              f"{r['avg_rate_kbps']:8.1f} | {r['errors']:8d} | {r['timeouts']:6d}")
        total_packets += r['packets']
        total_bytes += r['bytes']
        total_errors += r['errors']
        total_timeouts += r['timeouts']
    
    print("-" * 70)
    print(f"  ИТОГО  | {total_packets:8d} | {total_bytes/1024/1024:8.2f} | "
          f"         | {total_errors:8d} | {total_timeouts:6d}")
    print("-" * 70)
    
    # Оценка стабильности
    print("\n📊 ОЦЕНКА СТАБИЛЬНОСТИ:")
    
    if len(results) == 3:
        print(f"  ✅ Все 3 прогона завершены успешно")
    else:
        print(f"  ⚠️  Завершено {len(results)}/3 прогонов")
    
    if total_errors == 0:
        print(f"  ✅ Ошибок данных: 0")
    else:
        print(f"  ⚠️  Ошибок данных: {total_errors}")
    
    if total_packets > 0:
        avg_total_rate = total_bytes / (15 * 60) / 1024
        print(f"  📈 Средняя скорость за 15 минут: {avg_total_rate:.2f} КБ/с")
        print(f"  📦 Всего данных: {total_bytes/1024/1024:.2f} МБ")
    
    # Вариация скорости
    if len(results) >= 2:
        rates = [r['avg_rate_kbps'] for r in results]
        min_rate = min(rates)
        max_rate = max(rates)
        variation = ((max_rate - min_rate) / min_rate * 100) if min_rate > 0 else 0
        print(f"  📊 Вариация скорости: {variation:.1f}% (min={min_rate:.1f}, max={max_rate:.1f} КБ/с)")
    
    print("\n" + "#" * 70)
    
    if len(results) == 3 and total_errors == 0:
        print("✅ ТЕСТ ПРОЙДЕН УСПЕШНО!")
        return 0
    else:
        print("⚠️  ТЕСТ ЗАВЕРШЁН С ЗАМЕЧАНИЯМИ")
        return 1

if __name__ == "__main__":
    sys.exit(main())
