#!/usr/bin/env python3
"""
Быстрая проверка ADC данных без GUI.
Читает 10 кадров и показывает статистику значений.
"""

import sys, time, struct
import usb.core, usb.util

VENDOR = 0xCAFE
PRODUCT = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83
HDR_SIZE = 32

CMD_START = 0x20
CMD_STOP = 0x21
CMD_SET_ASYNC_MODE = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14

def find_dev():
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
    
    try:
        dev.set_interface_altsetting(INTERFACE, 1)
    except:
        pass
    
    return dev

def send_cmd(dev, cmd_byte, data=None):
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
    if len(b) < HDR_SIZE:
        return None
    magic, ver, flags, seq, ts, total_samples, zone_cnt, zone_off, zone_len, reserved, reserved2, crc16 = struct.unpack_from(
        '<HBBIIHHIIIHH', b, 0
    )
    return {
        'magic': magic,
        'flags': flags,
        'ns': total_samples,
        'seq': seq,
    }

def main():
    print("="*60)
    print("ADC Raw Data Check - reading 10 frames")
    print("="*60)
    
    dev = find_dev()
    
    # Настройка устройства
    print("[CMD] Stopping previous stream...")
    send_cmd(dev, CMD_STOP)
    time.sleep(0.2)
    
    print("[CMD] Configuring: paired mode, both channels, profile 0...")
    send_cmd(dev, CMD_SET_ASYNC_MODE, [0x80])  # paired mode
    time.sleep(0.05)
    send_cmd(dev, CMD_SET_CHMODE, [0x02])  # both channels
    time.sleep(0.05)
    send_cmd(dev, CMD_SET_FULL_MODE, [0x01])
    time.sleep(0.05)
    send_cmd(dev, CMD_SET_PROFILE, [0])
    time.sleep(0.05)
    
    print("[CMD] Starting stream...")
    send_cmd(dev, CMD_START)
    time.sleep(0.3)
    
    rx_buffer = bytearray()
    frame_count = 0
    target_frames = 10
    
    print("\nReading frames...\n")
    
    start_time = time.time()
    timeout_sec = 5.0
    
    while frame_count < target_frames:
        if time.time() - start_time > timeout_sec:
            print(f"\n[TIMEOUT] Got only {frame_count} frames in {timeout_sec}s")
            break
        
        try:
            chunk = dev.read(EP_IN, 4096, timeout=200)
            rx_buffer += bytes(chunk)
        except usb.core.USBError as e:
            if getattr(e, 'errno', None) in (110, 10060) or 'timed out' in str(e).lower():
                continue
            else:
                print(f"[USB ERROR] {e}")
                break
        
        # Парсинг кадров
        while len(rx_buffer) >= 4:
            # Пропуск STAT
            if rx_buffer[0:4] == b'STAT':
                stat_len = 84 if len(rx_buffer) >= 84 else (64 if len(rx_buffer) >= 64 else 52)
                if len(rx_buffer) >= stat_len:
                    rx_buffer = rx_buffer[stat_len:]
                    continue
                else:
                    break
            
            # Проверка ADC magic
            if rx_buffer[0] == 0x5A and rx_buffer[1] == 0xA5 and rx_buffer[2] == 0x01:
                if len(rx_buffer) < HDR_SIZE:
                    break
                
                h = parse_hdr(rx_buffer[:HDR_SIZE])
                if not h or h['magic'] != 0xA55A:
                    rx_buffer = rx_buffer[1:]
                    continue
                
                ns = h['ns']
                if ns == 0 or ns > 4096:
                    rx_buffer = rx_buffer[1:]
                    continue
                
                frame_len = HDR_SIZE + ns * 2
                if len(rx_buffer) < frame_len:
                    break
                
                frame = bytes(rx_buffer[:frame_len])
                rx_buffer = rx_buffer[frame_len:]
                
                # Парсинг payload
                payload = frame[HDR_SIZE:HDR_SIZE + ns * 2]
                samples = struct.unpack(f'<{ns}H', payload)
                
                # Статистика
                ch_mask = h['flags'] & 0x03
                ch_name = 'A' if ch_mask == 0x01 else ('B' if ch_mask == 0x02 else '?')
                parity = 'odd' if (h['flags'] & 0x80) else 'even'
                
                vmin = min(samples)
                vmax = max(samples)
                vavg = sum(samples) / len(samples)
                
                zeros = sum(1 for s in samples if s == 0)
                nonzeros = len(samples) - zeros
                
                print(f"Frame #{frame_count+1}: CH={ch_name} {parity:4s} | "
                      f"samples={ns:4d} | "
                      f"min={vmin:5d} max={vmax:5d} avg={vavg:7.1f} | "
                      f"zeros={zeros:4d} nonzero={nonzeros:4d}")
                
                # Если есть ненулевые значения, покажем первые 20
                if nonzeros > 0:
                    first_20 = list(samples[:20])
                    print(f"  First 20: {first_20}")
                
                frame_count += 1
                
                if frame_count >= target_frames:
                    break
            else:
                rx_buffer = rx_buffer[1:]
    
    # Остановка
    print("\n[CMD] Stopping stream...")
    send_cmd(dev, CMD_STOP)
    
    print("\n" + "="*60)
    print(f"Received {frame_count} frames")
    print("="*60)
    
    if frame_count > 0:
        print("\nExpected values: 1000-2000 (if ADC working)")
        print("Actual: see statistics above")
        print("\nIf all zeros → ADC not converting (TIM15 TRGO not triggering ADC)")
        print("If noise (random values) → ADC working but no input signal")
        print("If constant ~1000-2000 → ADC working with real signal")
    
    try:
        usb.util.release_interface(dev, INTERFACE)
        usb.util.dispose_resources(dev)
    except:
        pass

if __name__ == '__main__':
    main()
