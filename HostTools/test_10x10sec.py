#!/usr/bin/env python3
"""Тест: 10 раз по 10 секунд с остановкой между сессиями"""
import usb.core
import usb.util
import time

VID, PID = 0xCAFE, 0x4001
IN_EP, OUT_EP = 0x83, 0x03
CMD_START, CMD_STOP = 0x20, 0x21

def setup_device():
    """Найти и настроить устройство"""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        return None
    
    try:
        dev.set_configuration()
        usb.util.claim_interface(dev, 2)
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        
        # Setup sequence
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x64\x00\x2C\x01\xBC\x02\x2C\x01\x00')  # SET_WINDOWS
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\xC8\x00\x00')  # SET_BLOCK_RATE 200Hz
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x01\x00')  # SET_FULL_MODE(1)
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x02\x00')  # SET_PROFILE(2)
        return dev
    except Exception as e:
        print(f"[ERR] Setup: {e}")
        return None

def run_session(dev, session_num, duration=10):
    """Запустить одну сессию чтения"""
    print(f"\n[Session {session_num}/10] Starting {duration}sec read...")
    
    # START + GET_STATUS
    try:
        dev.write(OUT_EP, bytes([CMD_START]), timeout=500)
        dev.write(OUT_EP, bytes([0x30]), timeout=500)  # GET_STATUS
    except Exception as e:
        print(f"[ERR] START: {e}")
        return None
    
    time.sleep(0.2)
    
    t0 = time.time()
    cnt_a = cnt_b = cnt_stat = errors = 0
    bytes_total = 0
    
    while time.time() - t0 < duration:
        try:
            buf = dev.read(IN_EP, 2048, timeout=2000)
            bytes_total += len(buf)
            
            if len(buf) >= 4:
                if buf[0] == 0x5A and buf[1] == 0xA5 and buf[2] == 0x01:
                    if buf[3] == 0x01:
                        cnt_a += 1
                    elif buf[3] == 0x02:
                        cnt_b += 1
                elif buf[0:4] == b'STAT':
                    cnt_stat += 1
                    
        except usb.core.USBError as e:
            errors += 1
            if errors > 20:
                print(f"  [FATAL] Too many errors")
                break
    
    elapsed = time.time() - t0
    
    # STOP
    try:
        dev.write(OUT_EP, bytes([CMD_STOP]), timeout=500)
        time.sleep(0.1)
        # Try to read final STAT
        for _ in range(3):
            try:
                buf = dev.read(IN_EP, 2048, timeout=500)
                if len(buf) >= 4 and buf[0:4] == b'STAT':
                    cnt_stat += 1
                    break
            except:
                break
    except Exception as e:
        print(f"  [WARN] STOP: {e}")
    
    pairs = min(cnt_a, cnt_b)
    rate = pairs / elapsed if elapsed > 0 else 0
    throughput = (bytes_total / elapsed / 1024) if elapsed > 0 else 0
    
    result = {
        'session': session_num,
        'duration': elapsed,
        'pairs': pairs,
        'cnt_a': cnt_a,
        'cnt_b': cnt_b,
        'cnt_stat': cnt_stat,
        'errors': errors,
        'bytes': bytes_total,
        'rate': rate,
        'throughput_kb': throughput,
        'success': errors < 10 and pairs >= duration * 0.5  # Expect at least 0.5 pairs/sec
    }
    
    status = "✅" if result['success'] else "❌"
    print(f"  {status} {elapsed:.1f}s | Pairs={pairs:3d} | A/B={cnt_a:3d}/{cnt_b:3d} | STAT={cnt_stat:2d} | err={errors:2d} | {rate:.1f} p/s | {throughput:.0f} KB/s")
    
    return result

def main():
    print("=" * 80)
    print("10×10 SECOND STREAMING TEST")
    print("=" * 80)
    
    results = []
    
    for i in range(1, 11):
        # Setup device for each session
        dev = setup_device()
        if not dev:
            print(f"[ERR] Failed to setup device for session {i}")
            break
        
        print(f"[Session {i}] Device ready")
        
        # Run session
        result = run_session(dev, i, duration=10)
        if result:
            results.append(result)
        
        # Пауза между сессиями
        time.sleep(2)
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    if not results:
        print("❌ No successful sessions")
        return
    
    total_pairs = sum(r['pairs'] for r in results)
    total_errors = sum(r['errors'] for r in results)
    total_duration = sum(r['duration'] for r in results)
    total_bytes = sum(r['bytes'] for r in results)
    success_count = sum(1 for r in results if r['success'])
    
    avg_rate = total_pairs / total_duration if total_duration > 0 else 0
    avg_throughput = (total_bytes / total_duration / 1024) if total_duration > 0 else 0
    
    print(f"Sessions completed: {len(results)}/10")
    print(f"Successful:         {success_count}/{len(results)}")
    print(f"Total duration:     {total_duration:.1f} sec")
    print(f"Total pairs:        {total_pairs}")
    print(f"Total errors:       {total_errors}")
    print(f"Total bytes:        {total_bytes:,} ({total_bytes/1024/1024:.2f} MB)")
    print(f"Avg rate:           {avg_rate:.2f} pairs/sec")
    print(f"Avg throughput:     {avg_throughput:.1f} KB/s")
    
    print("\nPer-session results:")
    for r in results:
        status = "✅" if r['success'] else "❌"
        print(f"  {status} Session {r['session']:2d}: {r['duration']:5.1f}s | {r['pairs']:3d} pairs | {r['errors']:2d} err | {r['rate']:5.2f} p/s")
    
    if success_count == len(results) and len(results) == 10:
        print("\n✅ ALL 10 SESSIONS PASSED!")
    elif success_count >= 8:
        print(f"\n⚠️  {success_count}/10 sessions passed (acceptable)")
    else:
        print(f"\n❌ Only {success_count}/10 sessions passed (FAILED)")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[INT] Interrupted by user")
    except Exception as e:
        print(f"\n[ERR] {e}")
        import traceback
        traceback.print_exc()
