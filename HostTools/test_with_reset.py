#!/usr/bin/env python3
"""
Тест многократных циклов START/STOP с DEVICE RESET перед каждым START.
Для временного решения проблемы накопительного зависания.
"""

import os, sys, time, argparse, usb.core, usb.util

VID = int(os.getenv('VND_VID', '0xCAFE'), 16)
PID = int(os.getenv('VND_PID', '0x4001'), 16)
IFACE_INDEX = int(os.getenv('VND_INTF', '2'))
IN_EP = 0x83

VND_CMD_DEVICE_RESET    = 0x22
VND_CMD_START           = 0x20
VND_CMD_STOP            = 0x21


def ctrl_out(dev, bRequest, wValue=0, timeout=500):
    bm = usb.util.build_request_type(usb.util.CTRL_OUT, usb.util.CTRL_TYPE_VENDOR, 
                                       usb.util.CTRL_RECIPIENT_DEVICE)
    return dev.ctrl_transfer(bm, bRequest, wValue, 0, 0, timeout=timeout)


def find_and_setup_device():
    """Найти устройство и настроить интерфейс"""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        return None
    try:
        dev.set_configuration()
    except:
        pass
    try:
        usb.util.claim_interface(dev, IFACE_INDEX)
    except:
        pass
    try:
        dev.set_interface_altsetting(interface=IFACE_INDEX, alternate_setting=1)
    except:
        pass
    return dev


def main():
    ap = argparse.ArgumentParser(description='Multi-cycle test with DEVICE RESET before each START')
    ap.add_argument('--cycles', type=int, default=10, help='Number of START/STOP cycles')
    ap.add_argument('--secs', type=int, default=5, help='Duration of each cycle in seconds')
    ap.add_argument('--reset-wait', type=float, default=3.0, help='Wait after RESET (sec)')
    ap.add_argument('--timeout', type=int, default=500, help='Read timeout (ms)')
    args = ap.parse_args()

    print(f"\n{'='*70}")
    print(f"  MULTI-CYCLE TEST WITH DEVICE RESET")
    print(f"  Cycles: {args.cycles}, Duration: {args.secs}s each")
    print(f"  RESET wait: {args.reset_wait}s")
    print(f"{'='*70}\n")

    results = []
    
    for cycle in range(1, args.cycles + 1):
        print(f"\n--- CYCLE {cycle}/{args.cycles} ---")
        
        # 1. Найти устройство
        print(f"[1] Finding device...")
        dev = find_and_setup_device()
        if dev is None:
            print(f"[ERROR] Device not found in cycle {cycle}")
            results.append((cycle, 0, 0, "DEVICE_NOT_FOUND"))
            continue
        
        # 2. DEVICE RESET (пропускаем для первого цикла, если устройство свежее)
        if cycle > 1:
            print(f"[2] Sending DEVICE RESET...")
            try:
                ctrl_out(dev, VND_CMD_DEVICE_RESET)
            except Exception as e:
                print(f"    (Reset exception expected: {e})")
            
            # Ждём перезагрузки устройства
            print(f"[3] Waiting {args.reset_wait}s for device reboot...")
            time.sleep(args.reset_wait)
            
            # Переподключаемся к устройству
            print(f"[4] Re-connecting to device...")
            for retry in range(5):
                dev = find_and_setup_device()
                if dev:
                    break
                time.sleep(0.5)
            
            if dev is None:
                print(f"[ERROR] Device not found after reset in cycle {cycle}")
                results.append((cycle, 0, 0, "DEVICE_NOT_FOUND_AFTER_RESET"))
                continue
        
        # 3. START
        print(f"[5] Sending START...")
        try:
            ctrl_out(dev, VND_CMD_START)
        except Exception as e:
            print(f"[ERROR] START failed: {e}")
            results.append((cycle, 0, 0, "START_FAILED"))
            continue
        
        # 4. Читаем данные
        print(f"[6] Reading for {args.secs}s...")
        deadline = time.time() + args.secs
        pkts = 0
        timeouts = 0
        
        while time.time() < deadline:
            try:
                _ = dev.read(IN_EP, 512, timeout=args.timeout)
                pkts += 1
            except usb.core.USBTimeoutError:
                timeouts += 1
            except Exception as e:
                print(f"    Read error: {e}")
                break
        
        # 5. STOP
        print(f"[7] Sending STOP...")
        try:
            ctrl_out(dev, VND_CMD_STOP)
        except Exception as e:
            print(f"    STOP warning: {e}")
        
        # Результат цикла
        status = "OK" if timeouts < pkts/2 else "DEGRADED" if pkts > 0 else "FAILED"
        results.append((cycle, pkts, timeouts, status))
        print(f"    Result: pkts={pkts}, timeouts={timeouts}, status={status}")
    
    # Итоговая статистика
    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    for cycle, pkts, timeouts, status in results:
        print(f"  Cycle {cycle:2d}: pkts={pkts:4d}, timeouts={timeouts:3d}, status={status}")
    
    ok_count = sum(1 for _, _, _, s in results if s == "OK")
    print(f"\n  Total cycles: {args.cycles}")
    print(f"  OK cycles: {ok_count}")
    print(f"  Success rate: {ok_count/args.cycles*100:.1f}%")
    print(f"{'='*70}\n")
    
    return 0 if ok_count == args.cycles else 1


if __name__ == '__main__':
    sys.exit(main())
