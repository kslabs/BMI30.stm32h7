#!/usr/bin/env python3
"""
Универсальный тест производительности с мониторингом счётчиков firmware.
Цель: 200-300 пар/сек, все пары должны приниматься без потерь.
"""
import usb.core
import usb.util
import time
import struct
import sys

VID, PID = 0xCAFE, 0x4001
IN_EP, OUT_EP = 0x83, 0x03
CMD_START, CMD_STOP, CMD_GET_STATUS = 0x20, 0x21, 0x30

def parse_stat_frame(data):
    """Парсинг STAT frame с полной структурой"""
    if len(data) < 52 or data[0:4] != b'STAT':
        return None
    try:
        parts = struct.unpack('<4sBBHHHIIIIIIIIIHH', data[:52])
        stat = {
            'version': parts[1],
            'flags': parts[2],
            'cur_samples': parts[3],
            'frame_bytes': parts[4],
            'test_frames': parts[5],
            'produced_seq': parts[6],
            'sent0': parts[7],
            'sent1': parts[8],
            'dbg_tx_cplt': parts[9],
            'dbg_partial_abort': parts[10],
            'dbg_size_mismatch': parts[11],
            'dma_done0': parts[12],
            'dma_done1': parts[13],
            'frame_wr_seq': parts[14],
            'flags_runtime': parts[15],
            'flags2': parts[16] if len(data) >= 54 else 0,
        }
        
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
    """Настройка устройства с 200Hz"""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        return None
    
    try:
        dev.set_configuration()
        usb.util.claim_interface(dev, 2)
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        
        # Setup для максимальной производительности
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x64\x00\x2C\x01\xBC\x02\x2C\x01\x00')  # SET_WINDOWS
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x11\xC8\x00')  # SET_BLOCK_HZ 200Hz (cmd=0x11, val=200)
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x01\x00')  # SET_FULL_MODE(1)
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x02\x00')  # SET_PROFILE(2)
        return dev
    except Exception as e:
        print(f"[ERR] Setup: {e}")
        return None

def performance_test(dev, duration_sec, target_rate=200):
    """
    Тест производительности с подробным мониторингом.
    target_rate: ожидаемая скорость в парах/сек (200-300)
    """
    print(f"\n{'='*120}")
    print(f"PERFORMANCE TEST: {duration_sec}sec, target={target_rate} pairs/sec")
    print(f"{'='*120}")
    print(f"{'Time':>5s} | {'Host_A':>7s} {'Host_B':>7s} {'Pairs':>7s} | {'FW_Prod':>8s} {'FW_A':>7s} {'FW_B':>7s} | {'Delta':>6s} | {'Rate':>8s} | {'TxCplt':>7s} {'DMA0':>8s} | Status")
    print("-" * 120)
    
    # START
    dev.write(OUT_EP, bytes([CMD_START]), timeout=500)
    dev.write(OUT_EP, bytes([CMD_GET_STATUS]), timeout=500)
    time.sleep(0.1)
    
    t0 = time.time()
    host_a = host_b = host_stat = errors = 0
    last_stat_req = t0
    
    # Последние данные firmware
    fw_produced = fw_sent_a = fw_sent_b = fw_tx_cplt = fw_dma0 = 0
    stat_count = 0
    
    # Буфер для реассемблирования
    rx_buf = bytearray()
    
    try:
        while time.time() - t0 < duration_sec:
            try:
                # Читаем пакеты
                chunk = bytes(dev.read(IN_EP, 512, timeout=3000))
                rx_buf += chunk
                
                # Обрабатываем полные фреймы
                while True:
                    if len(rx_buf) < 4:
                        break
                    
                    # STAT?
                    if rx_buf[0:4] == b'STAT':
                        flen = 64 if len(rx_buf) >= 64 else (52 if len(rx_buf) >= 52 else 0)
                        if flen == 0 or len(rx_buf) < flen:
                            break
                        
                        frame = bytes(rx_buf[:flen])
                        rx_buf = rx_buf[flen:]
                        host_stat += 1
                        stat_count += 1
                        
                        stat = parse_stat_frame(frame)
                        if stat:
                            fw_produced = stat['produced_seq']
                            fw_sent_a = stat['sent0']
                            fw_sent_b = stat['sent1']
                            fw_tx_cplt = stat['dbg_tx_cplt']
                            fw_dma0 = stat['dma_done0']
                            
                            elapsed = time.time() - t0
                            host_pairs = min(host_a, host_b)
                            delta = fw_produced - host_pairs
                            rate = host_pairs / elapsed if elapsed > 0 else 0
                            
                            status = "OK" if delta < 10 else "LOSS!"
                            if rate < target_rate * 0.5:
                                status = "SLOW!"
                            
                            print(f"{elapsed:5.1f}s | {host_a:7d} {host_b:7d} {host_pairs:7d} | {fw_produced:8d} {fw_sent_a:7d} {fw_sent_b:7d} | {delta:+6d} | {rate:6.1f}/s | {fw_tx_cplt:7d} {fw_dma0:8d} | {status}")
                        continue
                    
                    # Data frame?
                    if rx_buf[0] == 0x5A and rx_buf[1] == 0xA5 and rx_buf[2] == 0x01 and len(rx_buf) >= 16:
                        total_samples = rx_buf[12] | (rx_buf[13] << 8)
                        flen = 32 + total_samples * 2
                        if len(rx_buf) < flen:
                            break
                        
                        frame = bytes(rx_buf[:flen])
                        rx_buf = rx_buf[flen:]
                        
                        if frame[3] == 0x01:
                            host_a += 1
                        elif frame[3] == 0x02:
                            host_b += 1
                        continue
                    
                    # Bad data
                    rx_buf = rx_buf[1:]
                
                # Запрашиваем STAT каждые 2 секунды
                now = time.time()
                if now - last_stat_req >= 2.0:
                    dev.write(OUT_EP, bytes([CMD_GET_STATUS]), timeout=500)
                    last_stat_req = now
                    # После запроса STAT читаем более агрессивно
                    for _ in range(5):
                        try:
                            chunk = bytes(dev.read(IN_EP, 512, timeout=500))
                            rx_buf += chunk
                        except:
                            break
                    
            except usb.core.USBTimeoutError:
                errors += 1
                if errors <= 3:
                    print(f"[TIMEOUT at {time.time()-t0:.1f}s]")
                if errors > 20:
                    print("[FATAL] Too many timeouts")
                    break
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"[ERR] {e}")
                break
    
    except KeyboardInterrupt:
        print("\n[INT] Test interrupted")
    
    elapsed = time.time() - t0
    
    # STOP и финальный STAT
    print(f"\n[STOP] after {elapsed:.1f}s...")
    try:
        dev.write(OUT_EP, bytes([CMD_STOP]), timeout=500)
        time.sleep(0.2)
        dev.write(OUT_EP, bytes([CMD_GET_STATUS]), timeout=500)
        time.sleep(0.1)
        
        # Читаем финальные фреймы
        for _ in range(20):
            try:
                chunk = bytes(dev.read(IN_EP, 512, timeout=1000))
                rx_buf += chunk
                
                while len(rx_buf) >= 4:
                    if rx_buf[0:4] == b'STAT':
                        flen = 64 if len(rx_buf) >= 64 else 52
                        if len(rx_buf) < flen:
                            break
                        frame = bytes(rx_buf[:flen])
                        rx_buf = rx_buf[flen:]
                        
                        stat = parse_stat_frame(frame)
                        if stat:
                            fw_produced = stat['produced_seq']
                            fw_sent_a = stat['sent0']
                            fw_sent_b = stat['sent1']
                            fw_tx_cplt = stat['dbg_tx_cplt']
                            fw_dma0 = stat['dma_done0']
                            print(f"[FINAL_STAT] prod={fw_produced}, sent={fw_sent_a}/{fw_sent_b}, TxCplt={fw_tx_cplt}, DMA={fw_dma0}")
                        break
                    elif rx_buf[0] == 0x5A and rx_buf[1] == 0xA5 and len(rx_buf) >= 16:
                        total_samples = rx_buf[12] | (rx_buf[13] << 8)
                        flen = 32 + total_samples * 2
                        if len(rx_buf) < flen:
                            break
                        frame = bytes(rx_buf[:flen])
                        rx_buf = rx_buf[flen:]
                        if frame[3] == 0x01:
                            host_a += 1
                        elif frame[3] == 0x02:
                            host_b += 1
                    else:
                        rx_buf = rx_buf[1:]
            except:
                break
    except Exception as e:
        print(f"[WARN] STOP: {e}")
    
    # Итоговая статистика
    host_pairs = min(host_a, host_b)
    rate_avg = host_pairs / elapsed if elapsed > 0 else 0
    loss = fw_produced - host_pairs if fw_produced > 0 else 0
    loss_pct = (loss / fw_produced * 100) if fw_produced > 0 else 0
    
    print(f"\n{'='*120}")
    print("RESULTS:")
    print(f"{'='*120}")
    print(f"Duration:          {elapsed:.1f} sec")
    print(f"")
    print(f"HOST received:     {host_pairs:7d} pairs  ({host_a} A + {host_b} B)")
    print(f"FIRMWARE produced: {fw_produced:7d} pairs  ({fw_sent_a} A + {fw_sent_b} B)")
    print(f"LOSS:              {loss:7d} pairs  ({loss_pct:.1f}%)")
    print(f"")
    print(f"Rate (host):       {rate_avg:7.1f} pairs/sec  (target: {target_rate})")
    print(f"Efficiency:        {rate_avg/target_rate*100:.1f}% of target")
    print(f"")
    print(f"STAT frames:       {stat_count:7d}")
    print(f"Errors/timeouts:   {errors:7d}")
    print(f"TxCplt callbacks:  {fw_tx_cplt:7d}")
    print(f"DMA buffers:       {fw_dma0:7d}")
    print(f"{'='*120}")
    
    # Диагностика
    if stat_count == 0:
        print("\nCRITICAL: NO STAT frames received!")
        print("   -> Check: CMD_GET_STATUS handling in firmware")
        print("   -> Check: STAT frame generation logic")
    elif fw_produced == 0:
        print("\nCRITICAL: Firmware produced 0 pairs!")
        print("   -> Check: vnd_prepare_pair() is being called")
        print("   -> Check: Buffer state machine")
    elif loss > host_pairs * 0.05:
        print(f"\nFAIL: High packet loss ({loss_pct:.1f}%)")
        print("   -> Firmware producing faster than host can receive")
        print("   -> Or USB transmission failures")
    elif rate_avg < target_rate * 0.7:
        print(f"\nSLOW: Rate {rate_avg:.1f} pairs/sec < target {target_rate}")
        print("   -> Check: Are we hitting USB bandwidth limits?")
        print("   -> Check: BLOCK_RATE setting in firmware")
    else:
        print(f"\nPASS: Rate {rate_avg:.1f} pairs/sec, loss {loss_pct:.1f}%")
    
    return {
        'duration': elapsed,
        'host_pairs': host_pairs,
        'fw_produced': fw_produced,
        'rate': rate_avg,
        'loss': loss,
        'errors': errors,
        'stat_count': stat_count
    }

def main():
    if len(sys.argv) > 1:
        duration = int(sys.argv[1])
    else:
        duration = 30
    
    print("="*120)
    print("USB STREAMING PERFORMANCE TEST")
    print("="*120)
    print("Target: 200-300 pairs/sec with 0% loss")
    print("="*120)
    
    dev = setup_device()
    if not dev:
        print("[ERR] Device not found")
        return 1
    
    print("[OK] Device configured for 200Hz BLOCK_RATE")
    
    result = performance_test(dev, duration, target_rate=200)
    
    # Return code для автоматизации
    if result['rate'] >= 140 and result['loss'] < result['fw_produced'] * 0.1:
        return 0  # Success
    else:
        return 1  # Fail

if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n[INT] Interrupted")
        sys.exit(2)
    except Exception as e:
        print(f"\n[ERR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(3)
