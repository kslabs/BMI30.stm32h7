#!/usr/bin/env python3
"""
Тест установки ROI (Region of Interest) от хоста в режиме AVG_ROI (Mode 2).
Проверяет, что устройство правильно применяет заданные окна.
"""
import struct
import time
import usb.core
import usb.util
import argparse

VND_CMD_SET_WINDOWS = 0x10
VND_CMD_SET_PROFILE = 0x14
VND_CMD_SET_BLOCK_HZ = 0x11
VND_CMD_SET_FULL_MODE = 0x13
VND_CMD_SET_STREAM_MODE = 0x1A
VND_CMD_SET_ASYNC = 0x18
VND_CMD_SET_CHMODE = 0x19
VND_CMD_START_STREAM = 0x20
VND_CMD_STOP_STREAM = 0x21

MAGIC = 0xA55A


def le16(v: int) -> bytes:
    return bytes((v & 0xFF, (v >> 8) & 0xFF))


def send_cmd(dev, ep_out: int, payload: bytes):
    dev.write(ep_out, payload, timeout=1000)


def claim_vendor_interface(dev, intf: int):
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    try:
        if dev.is_kernel_driver_active(intf):
            dev.detach_kernel_driver(intf)
    except (NotImplementedError, usb.core.USBError, AttributeError):
        pass
    usb.util.claim_interface(dev, intf)
    try:
        dev.set_interface_altsetting(interface=intf, alternate_setting=1)
    except Exception:
        pass


def parse_frame_header(data: bytes):
    """Парсинг заголовка кадра (32 байта)"""
    if len(data) < 32:
        return None
    magic = struct.unpack_from('<H', data, 0)[0]
    if magic != MAGIC:
        return None
    
    ver, flags, seq, ts = struct.unpack_from('<BBII', data, 2)
    total_samples, zone_cnt = struct.unpack_from('<HH', data, 12)
    # ИСПРАВЛЕНО: zone1_offset/zone1_len/zone2_offset/zone2_len - это uint32
    zone1_offset, zone1_len = struct.unpack_from('<II', data, 16)
    zone2_offset, zone2_len = struct.unpack_from('<II', data, 24)
    
    return {
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'ts': ts,
        'total_samples': total_samples,
        'zone_cnt': zone_cnt,
        'zone1_offset': zone1_offset,
        'zone1_len': zone1_len,
        'zone2_offset': zone2_offset,
        'zone2_len': zone2_len,
    }


def read_frames_and_check_roi(dev, ep_in: int, expected_offset: int, expected_len: int, 
                                duration: float, timeout_ms: int):
    """Читает кадры и проверяет, что ROI соответствует ожидаемым значениям"""
    end_t = time.time() + duration
    frames_ok = 0
    frames_bad = 0
    in_errors = 0
    
    print(f"Ожидаемое ROI: offset={expected_offset}, len={expected_len}")
    
    while time.time() < end_t:
        try:
            data = bytes(dev.read(ep_in, 16384, timeout=timeout_ms))
        except usb.core.USBError:
            in_errors += 1
            continue
        
        off = 0
        n = len(data)
        while off + 32 <= n:
            # Ищем заголовок кадра
            if data[off:off+2] != b'\x5A\xA5':
                off += 1
                continue
            
            hdr = parse_frame_header(data[off:off+32])
            if not hdr:
                off += 1
                continue
            
            frame_len = 32 + hdr['total_samples'] * 2
            if off + frame_len > n:
                break
            
            # Проверка ROI (zone1)
            if hdr['zone1_offset'] == expected_offset and hdr['zone1_len'] == expected_len:
                frames_ok += 1
            else:
                frames_bad += 1
                if frames_bad <= 5:  # Печатаем только первые 5 неправильных
                    print(f"❌ Неправильное ROI: offset={hdr['zone1_offset']}, len={hdr['zone1_len']} "
                          f"(seq={hdr['seq']}, flags=0x{hdr['flags']:02X})")
            
            off += frame_len
    
    return frames_ok, frames_bad, in_errors


def test_roi_configuration(vid: int, pid: int, intf: int, ep_in: int, ep_out: int,
                           roi_start: int, roi_len: int, avg_n: int, test_secs: float):
    """Тестирует одну конфигурацию ROI"""
    print("\n" + "="*70)
    print(f"🧪 ТЕСТ ROI: start={roi_start}, len={roi_len}, avg_n={avg_n}")
    print("="*70)
    
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        print("❌ Устройство не найдено")
        return False
    
    claim_vendor_interface(dev, intf)
    
    # Конфигурация
    # 1. SET_WINDOWS (ВАЖНО: до SET_STREAM_MODE!)
    payload = struct.pack('<BHHHH', VND_CMD_SET_WINDOWS, 
                          roi_start, roi_len, 0, 0)
    send_cmd(dev, ep_out, payload)
    print(f"✓ SET_WINDOWS: start0={roi_start}, len0={roi_len}")
    time.sleep(0.1)
    
    # 2. Другие настройки
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_PROFILE, 1]))
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_BLOCK_HZ]) + le16(200))
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_FULL_MODE, 1]))
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_CHMODE, 2]))  # both A+B
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_ASYNC, 1]))
    
    # 3. SET_STREAM_MODE = AVG_ROI (после SET_WINDOWS!)
    send_cmd(dev, ep_out, bytes([VND_CMD_SET_STREAM_MODE, 2, avg_n & 0xFF]))
    print(f"✓ SET_STREAM_MODE: mode=2 (AVG_ROI), avg_n={avg_n}")
    time.sleep(0.2)
    
    # 4. START
    send_cmd(dev, ep_out, bytes([VND_CMD_STOP_STREAM]))
    time.sleep(0.2)
    send_cmd(dev, ep_out, bytes([VND_CMD_START_STREAM]))
    print("✓ START отправлен")
    time.sleep(0.5)
    
    # 5. Чтение и проверка
    frames_ok, frames_bad, in_errors = read_frames_and_check_roi(
        dev, ep_in, roi_start, roi_len, test_secs, 1000)
    
    # 6. STOP
    try:
        send_cmd(dev, ep_out, bytes([VND_CMD_STOP_STREAM]))
    except Exception:
        pass
    
    # Результат
    print("\n" + "-"*70)
    print(f"Правильных кадров: {frames_ok}")
    print(f"Неправильных кадров: {frames_bad}")
    print(f"Ошибок USB: {in_errors}")
    
    success = frames_ok > 0 and frames_bad == 0
    if success:
        print("✅ ТЕСТ ПРОЙДЕН: ROI установлен правильно")
    else:
        print("❌ ТЕСТ ПРОВАЛЕН: ROI установлен неправильно")
    print("-"*70)
    
    return success


def main():
    ap = argparse.ArgumentParser(description="Тест установки ROI от хоста")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=0xCAFE)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=0x4001)
    ap.add_argument("--intf", type=int, default=2)
    ap.add_argument("--ep-in", type=lambda x: int(x, 0), default=0x83)
    ap.add_argument("--ep-out", type=lambda x: int(x, 0), default=0x03)
    ap.add_argument("--secs", type=float, default=5.0, help="Длительность каждого теста")
    args = ap.parse_args()
    
    # Набор тестов с разными ROI конфигурациями
    test_configs = [
        # (roi_start, roi_len, avg_n, описание)
        (280, 200, 16, "Дефолтное ROI 280..480"),
        (100, 200, 16, "Раннее ROI 100..300"),
        (350, 150, 16, "Позднее ROI 350..500"),
        (200, 250, 8, "Широкое ROI 200..450"),
    ]
    
    print("="*70)
    print("ТЕСТ УСТАНОВКИ ROI ОТ ХОСТА (MODE 2 / AVG_ROI)")
    print("="*70)
    print(f"Устройство: VID=0x{args.vid:04X} PID=0x{args.pid:04X}")
    print(f"Длительность каждого теста: {args.secs} сек")
    print("="*70)
    
    results = []
    for roi_start, roi_len, avg_n, desc in test_configs:
        success = test_roi_configuration(
            args.vid, args.pid, args.intf, args.ep_in, args.ep_out,
            roi_start, roi_len, avg_n, args.secs
        )
        results.append((desc, success))
        time.sleep(1)  # Пауза между тестами
    
    # Итоговая сводка
    print("\n" + "="*70)
    print("📊 ИТОГОВЫЕ РЕЗУЛЬТАТЫ")
    print("="*70)
    for desc, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status}: {desc}")
    print("="*70)
    
    all_passed = all(s for _, s in results)
    if all_passed:
        print("\n✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        return 0
    else:
        print("\n❌ НЕКОТОРЫЕ ТЕСТЫ ПРОВАЛЕНЫ")
        return 1


if __name__ == "__main__":
    exit(main())
