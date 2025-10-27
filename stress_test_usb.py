#!/usr/bin/env python3
"""Длительный тест USB streaming - читать кадры непрерывно"""
import usb.core
import usb.util
import time
import struct
import sys

VID = 0xCAFE
PID = 0x4001
VENDOR_IF = 2
ALT_SETTING = 1

CMD_SET_WINDOWS = 0x10
CMD_SET_RATE = 0x11
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14
CMD_START = 0x20
CMD_STOP = 0x21
CMD_GET_STATUS = 0x30

def main():
    duration_sec = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    
    print(f"[STRESS] Starting {duration_sec}-second continuous read test...")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    
    if not dev:
        print("[ERROR] Device not found!")
        return 1
    
    usb.util.claim_interface(dev, VENDOR_IF)
    dev.set_interface_altsetting(VENDOR_IF, ALT_SETTING)
    
    cfg = dev.get_active_configuration()
    intf = cfg[(VENDOR_IF, ALT_SETTING)]
    
    ep_out = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    
    ep_in = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
    )
    
    # Configure
    win_data = struct.pack('<BHHHH', CMD_SET_WINDOWS, 100, 300, 700, 300)
    ep_out.write(win_data)
    
    rate_data = struct.pack('<BH', CMD_SET_RATE, 200)
    ep_out.write(rate_data)
    
    ep_out.write(bytes([CMD_SET_FULL_MODE, 0x01]))
    ep_out.write(struct.pack('<BB', CMD_SET_PROFILE, 2))
    
    # Start
    ep_out.write(bytes([CMD_START]))
    # DON'T send GET_STATUS - it causes STAT frame to be sent instead of data!
    # ep_out.write(bytes([CMD_GET_STATUS]))
    time.sleep(0.1)
    
    print(f"[STRESS] Reading frames for {duration_sec} seconds...")
    
    FRAME_SIZE = 1856
    frames_read = 0
    bytes_read = 0
    errors = 0
    start_time = time.time()
    last_report = start_time
    
    try:
        while (time.time() - start_time) < duration_sec:
            buffer = bytearray()
            
            try:
                # Read complete frame
                while len(buffer) < FRAME_SIZE:
                    chunk_size = min(512, FRAME_SIZE - len(buffer))
                    chunk = ep_in.read(chunk_size, timeout=2000)
                    buffer.extend(chunk)
                
                # Validate header
                if len(buffer) >= 4:
                    if buffer[0] == 0x5A and buffer[1] == 0xA5 and buffer[2] == 0x01:
                        ch = buffer[3]
                        if ch in [1, 2]:
                            frames_read += 1
                            bytes_read += len(buffer)
                        else:
                            errors += 1
                            print(f"[WARN] Invalid channel: {ch}")
                    else:
                        errors += 1
                        print(f"[ERROR] Bad header: {buffer[:4].hex()}")
                else:
                    errors += 1
                    
                # Report every second
                now = time.time()
                if now - last_report >= 1.0:
                    elapsed = now - start_time
                    rate = frames_read / elapsed if elapsed > 0 else 0
                    mbps = (bytes_read * 8 / 1000000) / elapsed if elapsed > 0 else 0
                    print(f"[{elapsed:.1f}s] Frames={frames_read} Rate={rate:.1f} fps ({mbps:.2f} Mbps) Errors={errors}")
                    last_report = now
                    
            except usb.core.USBError as e:
                errors += 1
                print(f"[ERROR] USB error: {e}")
                if errors > 10:
                    print("[ABORT] Too many errors!")
                    break
                    
    except KeyboardInterrupt:
        print("\n[STRESS] Interrupted by user")
    
    # Stop
    elapsed = time.time() - start_time
    print(f"\n[STRESS] Test complete:")
    print(f"  Duration: {elapsed:.2f} seconds")
    print(f"  Frames: {frames_read}")
    print(f"  Bytes: {bytes_read:,}")
    print(f"  Average rate: {frames_read/elapsed:.1f} fps")
    print(f"  Throughput: {(bytes_read*8/1000000)/elapsed:.2f} Mbps")
    print(f"  Errors: {errors}")
    
    ep_out.write(bytes([CMD_STOP]))
    time.sleep(0.1)
    usb.util.release_interface(dev, VENDOR_IF)
    
    return 0 if errors == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
