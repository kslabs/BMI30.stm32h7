#!/usr/bin/env python3
"""Проверка поступления всех 4 типов пакетов (A_even, A_odd, B_even, B_odd)."""
import usb.core
import struct
import time

VID = 0xCAFE
PID = 0x4001
EP_IN = 0x81
HDR_SIZE = 32

def parse_hdr(b: bytes):
    if len(b) < HDR_SIZE:
        return None
    magic, ver, flags, seq, ts, total_samples, zone_cnt, zone_off, zone_len, reserved, reserved2, crc16 = struct.unpack_from(
        '<HBBIIHHIIIHH', b, 0
    )
    parity = 1 if (flags & 0x80) else 0
    ch_mask = flags & 0x03
    return {
        'magic': magic,
        'flags': flags,
        'ch_mask': ch_mask,
        'parity': parity,
        'ns': total_samples,
        'seq': seq,
    }

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print(f"ERROR: Device {VID:04X}:{PID:04X} not found")
        return
    
    print("Устройство найдено. Настраиваю и отправляю START...")
    # STOP (0x21)
    try:
        dev.ctrl_transfer(0x40, 0, 0, 0, bytes([0x21]), timeout=1000)
        time.sleep(0.2)
    except:
        pass
    
    # SET_ASYNC_MODE (0x18) = 0x80 (paired + strict)
    try:
        dev.ctrl_transfer(0x40, 0, 0, 0, bytes([0x18, 0x80]), timeout=1000)
        time.sleep(0.05)
    except:
        pass
    
    # SET_CHMODE (0x19) = 0x02 (Both A+B)
    try:
        dev.ctrl_transfer(0x40, 0, 0, 0, bytes([0x19, 0x02]), timeout=1000)
        time.sleep(0.05)
    except:
        pass
    
    # SET_FULL_MODE (0x1B) = 0x01
    try:
        dev.ctrl_transfer(0x40, 0, 0, 0, bytes([0x1B, 0x01]), timeout=1000)
        time.sleep(0.05)
    except:
        pass
    
    # SET_PROFILE (0x16) = 0x00
    try:
        dev.ctrl_transfer(0x40, 0, 0, 0, bytes([0x16, 0x00]), timeout=1000)
        time.sleep(0.05)
    except:
        pass
    
    # START (0x20)
    try:
        dev.ctrl_transfer(0x40, 0, 0, 0, bytes([0x20]), timeout=1000)
        time.sleep(0.5)
    except:
        pass
    
    counters = {
        'A_even': 0,
        'A_odd': 0,
        'B_even': 0,
        'B_odd': 0,
    }
    
    zero_frames = {
        'A_even': 0,
        'A_odd': 0,
        'B_even': 0,
        'B_odd': 0,
    }
    
    start_time = time.time()
    rx_buffer = bytearray()
    
    print("Читаю данные 10 секунд...")
    print("-" * 70)
    
    read_count = 0
    timeout_count = 0
    error_count = 0
    
    while time.time() - start_time < 10.0:
        try:
            chunk = dev.read(EP_IN, 4096, timeout=100)
            rx_buffer += bytes(chunk)
            read_count += 1
            if read_count <= 5:
                print(f"[READ {read_count}] Получено {len(chunk)} байт")
        except usb.core.USBError as e:
            if 'timed out' in str(e).lower():
                timeout_count += 1
                continue
            else:
                error_count += 1
                if error_count <= 3:
                    print(f"USB Error: {e}")
                continue
        
        # Парсинг кадров
        while True:
            if len(rx_buffer) < 4:
                break
            
            # Пропуск STAT
            if rx_buffer[0:4] == b'STAT':
                if len(rx_buffer) >= 84:
                    rx_buffer = rx_buffer[84:]
                    continue
                else:
                    break
            
            # Проверка ADC кадра
            if rx_buffer[0] == 0x5A and rx_buffer[1] == 0xA5 and rx_buffer[2] == 0x01:
                if len(rx_buffer) < HDR_SIZE:
                    break
                total_samples = rx_buffer[12] | (rx_buffer[13] << 8)
                if total_samples == 0 or total_samples > 1200:
                    rx_buffer = rx_buffer[1:]
                    continue
                frame_len = HDR_SIZE + total_samples * 2
                if len(rx_buffer) < frame_len:
                    break
                
                frame = bytes(rx_buffer[:frame_len])
                rx_buffer = rx_buffer[frame_len:]
                
                h = parse_hdr(frame)
                if not h or h['magic'] != 0xA55A:
                    continue
                
                ns = h['ns']
                payload = frame[HDR_SIZE:HDR_SIZE + ns * 2]
                samples = struct.unpack(f'<{ns}H', payload)
                
                ch_mask = h['ch_mask']
                parity = h['parity']
                
                # Определение типа
                if ch_mask == 0x01:  # A
                    key = 'A_even' if parity == 0 else 'A_odd'
                elif ch_mask == 0x02:  # B
                    key = 'B_even' if parity == 0 else 'B_odd'
                else:
                    continue
                
                counters[key] += 1
                
                # Проверка на нули
                if all(s == 0 for s in samples):
                    zero_frames[key] += 1
            else:
                rx_buffer = rx_buffer[1:]
    
    print("\n" + "=" * 70)
    print("РЕЗУЛЬТАТЫ (10 секунд):")
    print("=" * 70)
    print(f"USB чтений: {read_count}, таймаутов: {timeout_count}, ошибок: {error_count}")
    print(f"Получено байт: {len(rx_buffer)}")
    total = sum(counters.values())
    print(f"Всего пакетов: {total}")
    print(f"  A_even: {counters['A_even']:4d}  (нули: {zero_frames['A_even']})")
    print(f"  A_odd:  {counters['A_odd']:4d}  (нули: {zero_frames['A_odd']})")
    print(f"  B_even: {counters['B_even']:4d}  (нули: {zero_frames['B_even']})")
    print(f"  B_odd:  {counters['B_odd']:4d}  (нули: {zero_frames['B_odd']})")
    print("=" * 70)
    
    # Анализ
    missing = [k for k, v in counters.items() if v == 0]
    if missing:
        print(f"⚠️  ОТСУТСТВУЮТ: {', '.join(missing)}")
    else:
        print("✅ Все 4 типа пакетов получены!")
    
    # Отправка STOP
    try:
        dev.ctrl_transfer(0x40, 0, 0, 0, bytes([0x21]), timeout=1000)
    except:
        pass

if __name__ == '__main__':
    main()
