#!/usr/bin/env python3
"""
Long duration test: measure pairs/sec over extended period
Based on vendor_usb_start_and_read.py - simplified for rate measurement
"""
import sys
import time
import usb.core
import usb.util
import struct

VID = 0xCAFE
PID = 0x4001
VND_IF = 2
EP_IN = 0x83
EP_OUT = 0x03

CMD_START = 0x20
CMD_STOP = 0x21
CMD_SET_FULL_MODE = 0x12
CMD_SET_PROFILE = 0x13
CMD_SET_WINDOWS = 0x10
CMD_SET_BLOCK_HZ = 0x11

FRAME_SIZE = 1856
RATE_HZ = 200
TEST_DURATION = 60  # seconds

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        print("[ERR] Device not found")
        return 1
        
    try:
        if dev.is_kernel_driver_active(VND_IF):
            dev.detach_kernel_driver(VND_IF)
    except:
        pass
        
    dev.set_configuration()
    usb.util.claim_interface(dev, VND_IF)
    
    # Set interface to alt 1
    dev.set_interface_altsetting(VND_IF, 1)
    
    # Configure device
    # SET_WINDOWS
    win_cmd = struct.pack('<BHHHH', CMD_SET_WINDOWS, 100, 300, 700, 300)
    dev.write(EP_OUT, win_cmd, timeout=1000)
    
    # SET_BLOCK_RATE
    rate_cmd = struct.pack('<BH', CMD_SET_BLOCK_HZ, RATE_HZ)
    dev.write(EP_OUT, rate_cmd, timeout=1000)
    
    # SET_FULL_MODE
    dev.write(EP_OUT, bytes([CMD_SET_FULL_MODE, 1]), timeout=1000)
    
    # SET_PROFILE
    dev.write(EP_OUT, bytes([CMD_SET_PROFILE, 2]), timeout=1000)
    
    print(f"[OK] Device configured for {RATE_HZ}Hz")
    print(f"[TEST] Reading for {TEST_DURATION} seconds...")
    print()
    print("Time |  A_count  B_count    Pairs | Rate (1s) | Rate (avg) | Status")
    print("-" * 80)
    
    # START
    dev.write(EP_OUT, bytes([CMD_START]), timeout=1000)
    
    start_time = time.time()
    last_report_time = start_time
    
    count_a = 0
    count_b = 0
    last_count_a = 0
    last_count_b = 0
    
    timeout_ms = 2000
    
    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= TEST_DURATION:
                break
                
            try:
                data = dev.read(EP_IN, FRAME_SIZE, timeout=timeout_ms)
                
                if len(data) >= 4:
                    frame_type = data[3]
                    if frame_type == 1:  # A
                        count_a += 1
                    elif frame_type == 2:  # B
                        count_b += 1
                        
            except usb.core.USBTimeoutError:
                print(f"[TIMEOUT] at {elapsed:.1f}s")
                continue
            except usb.core.USBError as e:
                if e.errno == 32:  # Pipe error
                    print(f"[ERR] Pipe error at {elapsed:.1f}s - device disconnected?")
                    break
                raise
                
            # Report every second
            now = time.time()
            if now - last_report_time >= 1.0:
                pairs = min(count_a, count_b)
                avg_rate = pairs / elapsed if elapsed > 0 else 0
                
                # Rate over last second
                pairs_1s = min(count_a - last_count_a, count_b - last_count_b)
                
                status = "OK"
                if abs(count_a - count_b) > 10:
                    status = "IMBALANCE"
                    
                print(f"{elapsed:5.1f} | {count_a:8d} {count_b:8d} {pairs:8d} | "
                      f"{pairs_1s:9d} | {avg_rate:10.1f} | {status}")
                      
                last_report_time = now
                last_count_a = count_a
                last_count_b = count_b
                
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user")
    finally:
        # STOP
        try:
            dev.write(EP_OUT, bytes([CMD_STOP]), timeout=1000)
        except:
            pass
            
        elapsed = time.time() - start_time
        pairs = min(count_a, count_b)
        avg_rate = pairs / elapsed if elapsed > 0 else 0
        
        print()
        print("=" * 80)
        print(f"Duration:     {elapsed:.1f} sec")
        print(f"A frames:     {count_a}")
        print(f"B frames:     {count_b}")
        print(f"Pairs:        {pairs}")
        print(f"Average rate: {avg_rate:.1f} pairs/sec")
        print(f"Target:       {RATE_HZ} pairs/sec")
        print(f"Efficiency:   {100*avg_rate/RATE_HZ:.1f}%")
        
        if abs(count_a - count_b) > 5:
            print(f"\n[WARN] Imbalance: {abs(count_a - count_b)} frames difference")
            
        usb.util.release_interface(dev, VND_IF)
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
