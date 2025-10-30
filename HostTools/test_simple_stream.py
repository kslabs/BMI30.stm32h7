#!/usr/bin/env python3
"""
Простой тест потоковой передачи данных.
Просто START -> читаем данные -> считаем пары.
"""
import usb.core
import time
import sys

VID, PID = 0xCAFE, 0x4001
IN_EP, OUT_EP = 0x83, 0x03
CMD_START, CMD_STOP = 0x20, 0x21

def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    
    print("="*80)
    print(f"SIMPLE STREAMING TEST: {duration} seconds")
    print("="*80)
    
    # Find device
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("[ERROR] Device not found")
        return 1
    
    # Configure
    try:
        dev.set_configuration()
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        print("[OK] Device configured, IF#2 altsetting 1")
    except Exception as e:
        print(f"[WARN] Config: {e}")
    
    # Send START
    try:
        dev.write(OUT_EP, bytes([CMD_START]))
        print("[OK] START command sent")
    except Exception as e:
        print(f"[ERROR] Failed to send START: {e}")
        return 1
    
    # Read data
    count_a = 0
    count_b = 0
    total_bytes = 0
    errors = 0
    
    t_start = time.time()
    t_end = t_start + duration
    t_last_print = t_start
    
    print("\nReceiving data...")
    print(f"{'Time':>6} | {'A pairs':>8} {'B pairs':>8} | {'Rate':>8} | {'Bytes':>10} | {'Errors':>6}")
    print("-" * 80)
    
    try:
        while time.time() < t_end:
            try:
                data = dev.read(IN_EP, 512, timeout=1000)
                total_bytes += len(data)
                
                # Parse header - check for valid frame magic 0xA55A (little endian = 5A A5)
                if len(data) >= 8 and data[0] == 0x5A and data[1] == 0xA5:
                    # Valid frame header
                    ver = data[2]
                    flags = data[3]
                    
                    # flags: bit0=ADC0 (A channel), bit1=ADC1 (B channel)
                    if flags & 0x01:  # ADC0 = channel A
                        count_a += 1
                    elif flags & 0x02:  # ADC1 = channel B
                        count_b += 1
                
                # Print stats every second
                now = time.time()
                if now - t_last_print >= 1.0:
                    elapsed = now - t_start
                    rate = (count_a + count_b) / elapsed if elapsed > 0 else 0
                    print(f"{elapsed:6.1f} | {count_a:8d} {count_b:8d} | {rate:8.1f} | {total_bytes:10d} | {errors:6d}")
                    t_last_print = now
                    
            except usb.core.USBTimeoutError:
                errors += 1
                continue
            except Exception as e:
                print(f"\n[ERROR] Read failed: {e}")
                errors += 1
                continue
                
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED]")
    
    # Send STOP
    try:
        dev.write(OUT_EP, bytes([CMD_STOP]))
        print("\n[OK] STOP command sent")
    except:
        pass
    
    # Final stats
    elapsed = time.time() - t_start
    total_pairs = count_a + count_b
    rate = total_pairs / elapsed if elapsed > 0 else 0
    
    print("\n" + "="*80)
    print("RESULTS:")
    print("="*80)
    print(f"Duration:      {elapsed:.1f} sec")
    print(f"A pairs:       {count_a}")
    print(f"B pairs:       {count_b}")
    print(f"Total pairs:   {total_pairs}")
    print(f"Total bytes:   {total_bytes:,}")
    print(f"Rate:          {rate:.1f} pairs/sec")
    print(f"Errors:        {errors}")
    print("="*80)
    
    if rate < 50:
        print("\n[WARNING] Rate very low! Expected 200+ pairs/sec")
        return 1
    elif rate < 150:
        print("\n[WARNING] Rate below target (200 pairs/sec)")
        return 0
    else:
        print("\n[SUCCESS] Good streaming performance!")
        return 0

if __name__ == '__main__':
    sys.exit(main())
