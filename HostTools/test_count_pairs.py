#!/usr/bin/env python3
"""Тест для сравнения отправленных и полученных пар"""
import usb.core
import usb.util
import time
import struct

VID, PID = 0xCAFE, 0x4001
IN_EP, OUT_EP = 0x83, 0x03
CMD_START, CMD_STOP, CMD_GET_STATUS = 0x20, 0x21, 0x30

def parse_stat_frame(data):
    """Парсинг STAT frame"""
    if len(data) < 52:
        return None
    try:
        # Базовая структура (52 байта)
        parts = struct.unpack('<4sBBHHHIIIIIIIIIHH', data[:52])
        if parts[0] != b'STAT':
            return None
        
        stat = {
            'version': parts[1],
            'flags': parts[2],
            'cur_samples': parts[3],
            'frame_bytes': parts[4],
            'test_frames': parts[5],
            'produced_seq': parts[6],  # Сколько пар создано firmware
            'sent0': parts[7],         # Сколько A frames отправлено
            'sent1': parts[8],         # Сколько B frames отправлено
            'dbg_tx_cplt': parts[9],   # TxCplt callbacks
            'dma_done0': parts[12],
            'dma_done1': parts[13],
            'frame_wr_seq': parts[14],
            'flags_runtime': parts[15],
        }
        
        # Расширенная структура (если есть)
        if len(data) >= 64:
            ext = struct.unpack('<HBBHBBBB', data[52:64])
            stat['flags2'] = ext[0]
            stat['sending_ch'] = ext[1]
            stat['pair_fill'] = ext[2]
            stat['pair_send'] = ext[3]
            stat['last_tx_len'] = ext[4]
            stat['cur_stream_seq'] = ext[5]
            stat['prep_calls'] = ext[6]
            stat['prep_ok'] = ext[7]
        
        return stat
    except:
        return None

def setup_device():
    """Найти и настроить устройство"""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("[ERR] Device not found")
        return None
    
    try:
        dev.set_configuration()
        usb.util.claim_interface(dev, 2)
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        
        # Setup sequence
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x64\x00\x2C\x01\xBC\x02\x2C\x01\x00')
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\xC8\x00\x00')
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x01\x00')
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x02\x00')
        print("[HOST] Device configured")
        return dev
    except Exception as e:
        print(f"[ERR] Setup: {e}")
        return None

def read_with_stat_monitoring(dev, duration_sec):
    """Читать с периодическим опросом STAT"""
    print(f"\n[TEST] Reading for {duration_sec} seconds with STAT monitoring...")
    print("Time | Host_A | Host_B | FW_Pairs | FW_SentA | FW_SentB | TxCplt | Errors | Delta")
    print("-" * 100)
    
    # START + GET_STATUS
    dev.write(OUT_EP, bytes([CMD_START]), timeout=500)
    dev.write(OUT_EP, bytes([CMD_GET_STATUS]), timeout=500)
    time.sleep(0.2)
    
    t0 = time.time()
    host_cnt_a = host_cnt_b = host_cnt_stat = errors = 0
    last_print = t0
    last_fw_pairs = 0
    last_fw_sent_a = 0
    last_fw_sent_b = 0
    
    # Для хранения последних значений firmware
    fw_stats = []
    
    # Buffer для реассемблирования (как в vendor_usb_start_and_read.py)
    rx_buffer = bytearray()
    
    while time.time() - t0 < duration_sec:
        try:
            # Читаем пакеты по 512 байт (как делает базовый скрипт)
            chunk = bytes(dev.read(IN_EP, 512, timeout=3000))
            rx_buffer += chunk
            # Читаем пакеты по 512 байт (как делает базовый скрипт)
            chunk = bytes(dev.read(IN_EP, 512, timeout=3000))
            rx_buffer += chunk
            
            # Извлекаем полные фреймы из буфера
            while True:
                if len(rx_buffer) < 4:
                    break
                
                # STAT frame?
                if rx_buffer[0:4] == b'STAT':
                    flen = 64 if len(rx_buffer) >= 64 else (52 if len(rx_buffer) >= 52 else 0)
                    if flen == 0 or len(rx_buffer) < flen:
                        break
                    
                    frame = bytes(rx_buffer[:flen])
                    rx_buffer = rx_buffer[flen:]
                    host_cnt_stat += 1
                    
                    stat = parse_stat_frame(frame)
                    if stat:
                        fw_stats.append({
                            'time': time.time() - t0,
                            'produced_seq': stat['produced_seq'],
                            'sent0': stat['sent0'],
                            'sent1': stat['sent1'],
                            'tx_cplt': stat['dbg_tx_cplt']
                        })
                        
                        # Вычисляем дельту
                        host_pairs = min(host_cnt_a, host_cnt_b)
                        fw_pairs = stat['produced_seq']
                        delta = fw_pairs - host_pairs
                        
                        elapsed = time.time() - t0
                        print(f"{int(elapsed):4d}s | {host_cnt_a:6d} | {host_cnt_b:6d} | {fw_pairs:8d} | {stat['sent0']:8d} | {stat['sent1']:8d} | {stat['dbg_tx_cplt']:6d} | {errors:6d} | {delta:+5d}")
                        
                        last_fw_pairs = fw_pairs
                        last_fw_sent_a = stat['sent0']
                        last_fw_sent_b = stat['sent1']
                    continue
                
                # Data frame (A/B)?
                if rx_buffer[0] == 0x5A and rx_buffer[1] == 0xA5 and rx_buffer[2] == 0x01 and len(rx_buffer) >= 16:
                    # Get total samples from header
                    total_samples = rx_buffer[12] | (rx_buffer[13] << 8)
                    flen = 32 + total_samples * 2
                    
                    if len(rx_buffer) < flen:
                        break  # Ждём больше данных
                    
                    frame = bytes(rx_buffer[:flen])
                    rx_buffer = rx_buffer[flen:]
                    
                    flags = frame[3]
                    if flags == 0x01:
                        host_cnt_a += 1
                    elif flags == 0x02:
                        host_cnt_b += 1
                    continue
                
                # Unknown/bad data - skip one byte
                rx_buffer = rx_buffer[1:]
            
            # Периодически запрашиваем STAT
            now = time.time()
            if now - last_print >= 5.0:
                dev.write(OUT_EP, bytes([CMD_GET_STATUS]), timeout=500)
                last_print = now
                
        except usb.core.USBError as e:
            errors += 1
            if errors <= 3:
                print(f"\n[USB_ERR] {e}")
            if errors > 50:
                print("\n[FATAL] Too many errors")
                break
    
    elapsed = time.time() - t0
    
    # STOP and get final STAT
    print(f"\n[TEST] Stopping after {elapsed:.1f}s...")
    try:
        dev.write(OUT_EP, bytes([CMD_STOP]), timeout=500)
        time.sleep(0.2)
        
        # Request final STAT
        dev.write(OUT_EP, bytes([CMD_GET_STATUS]), timeout=500)
        time.sleep(0.1)
        
        # Read final frames
        for _ in range(10):
            try:
                chunk = bytes(dev.read(IN_EP, 512, timeout=1000))
                rx_buffer += chunk
                
                # Process any remaining frames
                while len(rx_buffer) >= 4:
                    if rx_buffer[0:4] == b'STAT':
                        flen = 64 if len(rx_buffer) >= 64 else 52
                        if len(rx_buffer) < flen:
                            break
                        frame = bytes(rx_buffer[:flen])
                        rx_buffer = rx_buffer[flen:]
                        
                        stat = parse_stat_frame(frame)
                        if stat:
                            fw_stats.append({
                                'time': time.time() - t0,
                                'produced_seq': stat['produced_seq'],
                                'sent0': stat['sent0'],
                                'sent1': stat['sent1'],
                                'tx_cplt': stat['dbg_tx_cplt']
                            })
                            last_fw_pairs = stat['produced_seq']
                            last_fw_sent_a = stat['sent0']
                            last_fw_sent_b = stat['sent1']
                            print(f"[FINAL_STAT] FW produced={stat['produced_seq']}, sent A/B={stat['sent0']}/{stat['sent1']}, TxCplt={stat['dbg_tx_cplt']}")
                        break
                    elif rx_buffer[0] == 0x5A and rx_buffer[1] == 0xA5:
                        if len(rx_buffer) >= 16:
                            total_samples = rx_buffer[12] | (rx_buffer[13] << 8)
                            flen = 32 + total_samples * 2
                            if len(rx_buffer) < flen:
                                break
                            frame = bytes(rx_buffer[:flen])
                            rx_buffer = rx_buffer[flen:]
                            if frame[3] == 0x01:
                                host_cnt_a += 1
                            elif frame[3] == 0x02:
                                host_cnt_b += 1
                        else:
                            break
                    else:
                        rx_buffer = rx_buffer[1:]
            except:
                break
    except Exception as e:
        print(f"[WARN] STOP: {e}")
    
    # Final summary
    host_pairs = min(host_cnt_a, host_cnt_b)
    
    print("\n" + "=" * 100)
    print("FINAL COMPARISON:")
    print("=" * 100)
    print(f"{'':20s} | Pairs | A frames | B frames")
    print("-" * 100)
    print(f"{'HOST received':20s} | {host_pairs:5d} | {host_cnt_a:8d} | {host_cnt_b:8d}")
    print(f"{'FIRMWARE produced':20s} | {last_fw_pairs:5d} | {last_fw_sent_a:8d} | {last_fw_sent_b:8d}")
    print("-" * 100)
    print(f"{'DELTA (FW - Host)':20s} | {last_fw_pairs - host_pairs:+5d} | {last_fw_sent_a - host_cnt_a:+8d} | {last_fw_sent_b - host_cnt_b:+8d}")
    print("=" * 100)
    
    # Analysis
    if last_fw_pairs - host_pairs > 10:
        print("\n⚠️  WARNING: Firmware produced significantly more pairs than host received!")
        print("   → Possible causes:")
        print("     1. USB transmission failures (check TxCplt vs sent counts)")
        print("     2. Host read timeouts (USB bandwidth issue)")
        print("     3. Data lost in USB stack")
    elif last_fw_pairs - host_pairs > 0:
        print(f"\n✓ Small delta ({last_fw_pairs - host_pairs} pairs) - likely in-flight data or timing")
    else:
        print("\n✅ Host received all produced pairs!")
    
    print(f"\nHost errors: {errors}")
    print(f"STAT frames received: {host_cnt_stat}")
    print(f"Duration: {elapsed:.1f} sec")
    
    return {
        'host_pairs': host_pairs,
        'host_a': host_cnt_a,
        'host_b': host_cnt_b,
        'fw_pairs': last_fw_pairs,
        'fw_sent_a': last_fw_sent_a,
        'fw_sent_b': last_fw_sent_b,
        'errors': errors,
        'duration': elapsed,
        'fw_stats': fw_stats
    }

def main():
    print("=" * 100)
    print("PAIR COUNT COMPARISON TEST")
    print("Compare firmware produced pairs vs host received pairs")
    print("=" * 100)
    
    dev = setup_device()
    if not dev:
        return
    
    # Test 1: 30 seconds
    result = read_with_stat_monitoring(dev, 30)
    
    print("\n[TEST] Complete. Analysis saved.")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[INT] Interrupted by user")
    except Exception as e:
        print(f"\n[ERR] {e}")
        import traceback
        traceback.print_exc()
