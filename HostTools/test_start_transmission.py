#!/usr/bin/env python3
"""
Тест передачи данных после команды START
"""
import usb.core
import usb.util
import time
import sys

VID = 0xCAFE
PID = 0x4001

# Команды
CMD_START = 0x20
CMD_STOP = 0x21

def find_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"❌ Устройство VID={VID:04X} PID={PID:04X} не найдено!")
        sys.exit(1)
    
    # Конфигурация
    try:
        dev.set_configuration()
    except:
        pass
    
    # Ищем Vendor интерфейс (IF#2, alt=1)
    cfg = dev.get_active_configuration()
    vendor_intf = None
    for intf in cfg:
        if intf.bInterfaceClass == 0xFF and intf.bInterfaceNumber == 2:
            if intf.bAlternateSetting == 1:
                vendor_intf = intf
                break
    
    if vendor_intf is None:
        print("❌ Vendor интерфейс (IF#2 alt=1) не найден!")
        sys.exit(1)
    
    # Устанавливаем alt setting
    try:
        dev.set_interface_altsetting(2, 1)
    except:
        pass
    
    # Находим endpoints
    ep_in = None
    ep_out = None
    for ep in vendor_intf:
        if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
            ep_in = ep.bEndpointAddress
        else:
            ep_out = ep.bEndpointAddress
    
    print(f"✅ Устройство найдено: VID={VID:04X} PID={PID:04X}")
    print(f"   Vendor IF#2 alt=1, EP IN=0x{ep_in:02X}, EP OUT=0x{ep_out:02X}")
    
    return dev, ep_in, ep_out

def send_command(dev, ep_out, cmd):
    """Отправить команду через EP OUT"""
    try:
        dev.write(ep_out, bytes([cmd]), timeout=1000)
        return True
    except Exception as e:
        print(f"⚠️  Ошибка отправки команды 0x{cmd:02X}: {e}")
        return False

def read_data(dev, ep_in, timeout_ms=1000):
    """Прочитать данные из EP IN"""
    try:
        data = dev.read(ep_in, 16384, timeout=timeout_ms)
        return bytes(data)
    except usb.core.USBTimeoutError:
        return None
    except Exception as e:
        print(f"⚠️  Ошибка чтения: {e}")
        return None

def main():
    print("=" * 50)
    print("Тест передачи данных после START")
    print("=" * 50)
    
    dev, ep_in, ep_out = find_device()
    
    # Отправляем STOP (на всякий случай)
    print("\n[1] Отправка STOP (сброс состояния)...")
    send_command(dev, ep_out, CMD_STOP)
    time.sleep(0.5)
    
    # Очищаем буфер приёма (читаем всё что осталось)
    print("[2] Очистка буфера...")
    while True:
        data = read_data(dev, ep_in, timeout_ms=100)
        if data is None:
            break
        print(f"    Очищено {len(data)} байт")
    
    # Отправляем START
    print("\n[3] Отправка START...")
    if not send_command(dev, ep_out, CMD_START):
        print("❌ Не удалось отправить START!")
        return 1
    
    print("[4] Ожидание данных (5 секунд)...")
    start_time = time.time()
    packets_received = 0
    total_bytes = 0
    
    while time.time() - start_time < 5.0:
        data = read_data(dev, ep_in, timeout_ms=100)
        if data:
            packets_received += 1
            total_bytes += len(data)
            
            # Анализ первого пакета
            if packets_received == 1:
                print(f"\n✅ ПЕРВЫЙ ПАКЕТ получен!")
                print(f"   Размер: {len(data)} байт")
                print(f"   Первые 32 байта: {data[:32].hex(' ')}")
                
                # Пытаемся декодировать заголовок (если это фрейм)
                if len(data) >= 16:
                    magic = data[0]
                    flags = data[1]
                    seq = int.from_bytes(data[2:6], 'little')
                    timestamp = int.from_bytes(data[6:10], 'little')
                    samples = int.from_bytes(data[10:12], 'little')
                    print(f"   Заголовок: magic=0x{magic:02X} flags=0x{flags:02X} seq={seq} ts={timestamp} samples={samples}")
            
            # Периодический отчёт
            if packets_received % 10 == 0:
                elapsed = time.time() - start_time
                rate = total_bytes / elapsed / 1024
                print(f"   [{elapsed:.1f}s] Пакетов: {packets_received}, Байт: {total_bytes}, Скорость: {rate:.1f} КБ/с")
    
    # Итоги
    print("\n" + "=" * 50)
    print("ИТОГИ:")
    print(f"  Пакетов получено: {packets_received}")
    print(f"  Всего байт: {total_bytes}")
    
    if packets_received > 0:
        elapsed = time.time() - start_time
        avg_rate = total_bytes / elapsed / 1024
        print(f"  Средняя скорость: {avg_rate:.2f} КБ/с")
        print(f"  Средний размер пакета: {total_bytes / packets_received:.0f} байт")
        print("\n✅ ПЕРЕДАЧА РАБОТАЕТ!")
    else:
        print("\n❌ ДАННЫЕ НЕ ПОЛУЧЕНЫ!")
    
    # Отправляем STOP
    print("\n[5] Отправка STOP...")
    send_command(dev, ep_out, CMD_STOP)
    
    print("=" * 50)
    return 0 if packets_received > 0 else 1

if __name__ == "__main__":
    sys.exit(main())
