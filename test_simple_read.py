#!/usr/bin/env python3
"""Простой тест чтения кадров USB (без GUI) - аналог plot_adc_qt"""
import usb.core
import usb.util
import time
import struct

VID = 0xCAFE
PID = 0x4001
VENDOR_IF = 2
ALT_SETTING = 1

# Commands
CMD_SET_WINDOWS = 0x10
CMD_SET_RATE = 0x11
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14
CMD_START = 0x20
CMD_STOP = 0x21
CMD_GET_STATUS = 0x30

def main():
    print(f"[TEST] Searching for device VID={VID:#06x} PID={PID:#06x}...")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    
    if not dev:
        print("[ERROR] Device not found!")
        return
    
    print(f"[TEST] Device found: {dev}")
    
    # Claim interface
    usb.util.claim_interface(dev, VENDOR_IF)
    print(f"[TEST] Claimed interface {VENDOR_IF}")
    
    # Set alt setting
    dev.set_interface_altsetting(VENDOR_IF, ALT_SETTING)
    print(f"[TEST] Set alt={ALT_SETTING}")
    
    # Get endpoints
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
    
    print(f"[TEST] EP OUT={ep_out.bEndpointAddress:#04x} IN={ep_in.bEndpointAddress:#04x}")
    
    # Configure
    print("[TEST] Sending configuration...")
    win_data = struct.pack('<BHHHH', CMD_SET_WINDOWS, 100, 300, 700, 300)
    ep_out.write(win_data)
    print(f"[TEST] SET_WINDOWS sent: {len(win_data)} bytes")
    
    rate_data = struct.pack('<BH', CMD_SET_RATE, 200)
    ep_out.write(rate_data)
    print(f"[TEST] SET_RATE sent: {len(rate_data)} bytes")
    
    ep_out.write(bytes([CMD_SET_FULL_MODE, 0x01]))
    print(f"[TEST] SET_FULL_MODE sent")
    
    ep_out.write(struct.pack('<BB', CMD_SET_PROFILE, 2))
    print(f"[TEST] SET_PROFILE sent")
    
    # Start streaming
    print("[TEST] Sending START...")
    ep_out.write(bytes([CMD_START]))
    print(f"[TEST] START sent")
    
    # Send GET_STATUS (like vendor_usb)
    ep_out.write(bytes([CMD_GET_STATUS]))
    print(f"[TEST] GET_STATUS sent")
    
    # Wait a bit
    time.sleep(0.1)
    
    # Try to read frames
    print("[TEST] Reading frames...")
    FRAME_SIZE = 1856
    frames_read = 0
    
    for i in range(5):  # Try to read 5 frames
        print(f"[TEST] Reading frame {i+1}...")
        buffer = bytearray()
        
        try:
            # Read frame in chunks
            while len(buffer) < FRAME_SIZE:
                chunk_size = min(512, FRAME_SIZE - len(buffer))
                chunk = ep_in.read(chunk_size, timeout=5000)  # 5 second timeout
                buffer.extend(chunk)
                print(f"  [TEST] Got {len(chunk)} bytes, total={len(buffer)}")
                
                if len(buffer) >= 4:
                    # Check header
                    if buffer[0] == 0x5A and buffer[1] == 0xA5 and buffer[2] == 0x01:
                        ch = buffer[3]
                        print(f"  [TEST] Valid header, channel={'A' if ch==1 else 'B' if ch==2 else '?'}")
                    else:
                        print(f"  [WARN] Invalid header: {buffer[:4].hex()}")
                        break
            
            if len(buffer) == FRAME_SIZE:
                frames_read += 1
                print(f"[TEST] Frame {i+1} complete: {FRAME_SIZE} bytes")
            else:
                print(f"[WARN] Frame {i+1} incomplete: {len(buffer)} bytes")
                break
                
        except usb.core.USBError as e:
            print(f"[ERROR] USB read error on frame {i+1}: {e}")
            break
    
    # Stop
    print(f"\n[TEST] Stopping... (read {frames_read} frames)")
    ep_out.write(bytes([CMD_STOP]))
    time.sleep(0.1)
    
    # Release
    usb.util.release_interface(dev, VENDOR_IF)
    print("[TEST] Done!")

if __name__ == '__main__':
    main()
