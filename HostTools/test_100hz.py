#!/usr/bin/env python3
"""
Test 100Hz rate to verify firmware works
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
RATE_HZ = 100  # Lower rate
TEST_DURATION = 10  # shorter test

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
    dev.set_interface_altsetting(VND_IF, 1)
    
    # Configure
    win_cmd = struct.pack('<BHHHH', CMD_SET_WINDOWS, 100, 300, 700, 300)
    dev.write(EP_OUT, win_cmd, timeout=1000)
    
    rate_cmd = struct.pack('<BH', CMD_SET_BLOCK_HZ, RATE_HZ)
    dev.write(EP_OUT, rate_cmd, timeout=1000)
    
    dev.write(EP_OUT, bytes([CMD_SET_FULL_MODE, 1]), timeout=1000)
    dev.write(EP_OUT, bytes([CMD_SET_PROFILE, 2]), timeout=1000)
    
    print(f"[OK] Configured for {RATE_HZ}Hz, reading {TEST_DURATION}s...")
    
    dev.write(EP_OUT, bytes([CMD_START]), timeout=1000)
    
    start_time = time.time()
    count_a = 0
    count_b = 0
    
    try:
        while time.time() - start_time < TEST_DURATION:
            try:
                data = dev.read(EP_IN, FRAME_SIZE, timeout=1000)
                if len(data) >= 4:
                    if data[3] == 1:
                        count_a += 1
                    elif data[3] == 2:
                        count_b += 1
            except usb.core.USBTimeoutError:
                pass
                
    except KeyboardInterrupt:
        pass
    finally:
        try:
            dev.write(EP_OUT, bytes([CMD_STOP]), timeout=1000)
        except:
            pass
            
        elapsed = time.time() - start_time
        pairs = min(count_a, count_b)
        rate = pairs / elapsed if elapsed > 0 else 0
        
        print(f"\nA={count_a}, B={count_b}, pairs={pairs}")
        print(f"Duration: {elapsed:.1f}s, rate: {rate:.1f} pairs/sec")
        print(f"Target: {RATE_HZ}, efficiency: {100*rate/RATE_HZ:.1f}%")
        
        usb.util.release_interface(dev, VND_IF)
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
