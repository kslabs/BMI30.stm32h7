#!/usr/bin/env python3
"""Стресс-тест USB streaming: 1 минута + 10×10 секунд"""
import usb.core
import time
import sys

VID, PID = 0xCAFE, 0x4001
IN_EP, OUT_EP = 0x83, 0x03
CMD_START, CMD_STOP = 0x20, 0x21

def find_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("[ERR] Device not found")
        return None
    try:
        dev.set_configuration()
        usb.util.claim_interface(dev, 2)
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        # Setup как в vendor_usb_start_and_read.py
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x64\x00\x2C\x01\xBC\x02\x2C\x01\x00')  # SET_WINDOWS
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\xC8\x00\x00')  # SET_BLOCK_RATE 200Hz
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x01\x00')  # SET_FULL_MODE(1)
        dev.ctrl_transfer(0x21, 0x22, 0, 2, b'\x02\x00')  # SET_PROFILE(2)
    except Exception as e:
        print(f"[WARN] Setup: {e}")
    return dev

def test_duration(dev, duration_sec, test_name):
    """Тест на заданное время"""
    print(f"\n[{test_name}] Starting {duration_sec}sec test...")
    
    # START
    dev.write(OUT_EP, bytes([CMD_START]), timeout=500)
    time.sleep(0.2)  # Даём устройству время начать передачу
    
    t0 = time.time()
    cnt_a = cnt_b = cnt_stat = cnt_other = 0
    errors = 0
    last_print = t0
    
    while time.time() - t0 < duration_sec:
        try:
            buf = dev.read(IN_EP, 2048, timeout=2000)  # Увеличен timeout до 2сек
            if len(buf) >= 4:
                if buf[0:4] == b'STAT':
                    cnt_stat += 1
                elif buf[0:2] == b'\x5A\xA5' and buf[3] == 1:
                    cnt_a += 1
                elif buf[0:2] == b'\x5A\xA5' and buf[3] == 2:
                    cnt_b += 1
                else:
                    cnt_other += 1
            
            # Прогресс каждую секунду
            now = time.time()
            if now - last_print >= 1.0:
                elapsed = now - t0
                pairs = min(cnt_a, cnt_b)
                print(f"  [{int(elapsed):3d}s] Pairs={pairs:4d} A={cnt_a:4d} B={cnt_b:4d} STAT={cnt_stat:2d} err={errors:2d}", end='\r')
                last_print = now
                
        except usb.core.USBError as e:
            errors += 1
            if errors <= 5:
                print(f"\n  [ERR] {e}")
            if errors > 20:
                print(f"\n  [FATAL] Too many errors, aborting")
                break
    
    elapsed = time.time() - t0
    pairs = min(cnt_a, cnt_b)
    
    # STOP
    try:
        dev.write(OUT_EP, bytes([CMD_STOP]), timeout=500)
    except:
        pass
    
    print(f"\n[{test_name}] RESULT: {elapsed:.1f}sec, Pairs={pairs}, A={cnt_a}, B={cnt_b}, STAT={cnt_stat}, err={errors}")
    
    return {
        'duration': elapsed,
        'pairs': pairs,
        'cnt_a': cnt_a,
        'cnt_b': cnt_b,
        'cnt_stat': cnt_stat,
        'errors': errors,
        'success': errors < 10 and pairs > duration_sec * 0.8  # Expect ~1 pair/sec min
    }

def main():
    print("=" * 70)
    print("USB STRESS TEST: 1×60sec + 10×10sec")
    print("=" * 70)
    
    results = []
    
    # TEST 1: 1 минута
    dev = find_device()
    if not dev:
        return
    
    result = test_duration(dev, 60, "TEST 1/11 (60sec)")
    results.append(result)
    
    if not result['success']:
        print("\n[FAIL] First test failed, aborting")
        return
    
    time.sleep(2)  # Пауза между тестами
    
    # TEST 2-11: 10 раз по 10 секунд
    for i in range(10):
        time.sleep(1)  # Короткая пауза
        
        # Переподключаемся каждый раз (как в реальном сценарии)
        dev = find_device()
        if not dev:
            print(f"\n[ERR] Device not found for test {i+2}")
            break
        
        result = test_duration(dev, 10, f"TEST {i+2}/11 (10sec)")
        results.append(result)
        
        if not result['success']:
            print(f"\n[WARN] Test {i+2} had issues")
    
    # Итоговая статистика
    print("\n" + "=" * 70)
    print("SUMMARY:")
    print("=" * 70)
    
    total_pairs = sum(r['pairs'] for r in results)
    total_errors = sum(r['errors'] for r in results)
    total_duration = sum(r['duration'] for r in results)
    success_count = sum(1 for r in results if r['success'])
    
    print(f"Total tests:    {len(results)}")
    print(f"Successful:     {success_count}/{len(results)}")
    print(f"Total duration: {total_duration:.1f} sec")
    print(f"Total pairs:    {total_pairs}")
    print(f"Total errors:   {total_errors}")
    print(f"Avg pairs/sec:  {total_pairs/total_duration:.2f}")
    
    if success_count == len(results):
        print("\n✅ ALL TESTS PASSED!")
    else:
        print(f"\n❌ {len(results) - success_count} TESTS FAILED")
    
    # Детали по каждому тесту
    print("\nDetailed results:")
    for i, r in enumerate(results, 1):
        status = "✅" if r['success'] else "❌"
        print(f"  Test {i:2d}: {status} {r['duration']:5.1f}s, pairs={r['pairs']:4d}, err={r['errors']:2d}")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[INT] Interrupted by user")
    except Exception as e:
        print(f"\n[ERR] {e}")
        import traceback
        traceback.print_exc()
