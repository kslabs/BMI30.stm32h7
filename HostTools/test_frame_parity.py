#!/usr/bin/env python3
"""
Test script to verify all 4 frame types (A_even, A_odd, B_even, B_odd) are received.
Runs for 10 seconds and reports frame distribution.
"""

import usb.core
import usb.util
import struct
import time
import os
import sys
from collections import defaultdict

_this_dir = os.path.dirname(__file__)
_proj_root = os.path.abspath(os.path.join(_this_dir, os.pardir))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from usb_vendor.usb_stream import (
    CMD_ASYNC,
    CMD_CHMODE,
    CMD_FULL_MODE,
    CMD_SET_PROFILE,
    CMD_START_STREAM,
    CMD_STOP_STREAM,
)

VID = 0xCAFE
PID = 0x4001
EP_OUT = 0x03  # Vendor OUT endpoint
EP_IN = 0x83   # Vendor IN endpoint
TIMEOUT_MS = 1000

def send_command(dev, cmd_bytes):
    """Send command via EP_OUT"""
    try:
        dev.write(EP_OUT, cmd_bytes, timeout=TIMEOUT_MS)
        return True
    except usb.core.USBError as e:
        print(f"[ERROR] Failed to send command: {e}")
        return False

def main():
    print("=" * 60)
    print("BMI30 Frame Parity Test")
    print("=" * 60)
    
    # Find device
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("[ERROR] Device not found (VID=0x{:04X}, PID=0x{:04X})".format(VID, PID))
        return 1
    
    print(f"[OK] Device found: VID=0x{VID:04X}, PID=0x{PID:04X}")
    
    # Detach kernel driver if necessary (Linux only)
    import platform
    if platform.system() == 'Linux':
        if dev.is_kernel_driver_active(0):
            try:
                dev.detach_kernel_driver(0)
                print("[OK] Kernel driver detached")
            except usb.core.USBError as e:
                print(f"[WARN] Could not detach kernel driver: {e}")
    
    # Set configuration
    try:
        dev.set_configuration()
        print("[OK] Configuration set")
    except usb.core.USBError as e:
        print(f"[ERROR] Failed to set configuration: {e}")
        return 1
    
    # Claim vendor interface and set alternate setting
    try:
        usb.util.claim_interface(dev, 2)  # Interface 2 = Vendor
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        print("[OK] Vendor interface activated (alt setting 1)")
    except usb.core.USBError as e:
        print(f"[ERROR] Failed to claim vendor interface: {e}")
        return 1
    
    # Send initialization sequence
    print("\n[CMD] Sending STOP...")
    send_command(dev, bytes([CMD_STOP_STREAM]))  # STOP
    time.sleep(0.1)
    
    print("[CMD] Configuring device...")
    send_command(dev, bytes([CMD_ASYNC, 0x80]))      # SET_ASYNC_MODE (paired + strict)
    send_command(dev, bytes([CMD_CHMODE, 0x02]))     # SET_CHMODE (dual channel)
    send_command(dev, bytes([CMD_FULL_MODE, 0x01]))  # SET_FULL_MODE (full stream)
    send_command(dev, bytes([CMD_SET_PROFILE, 0x00]))  # SET_PROFILE (profile 0)
    time.sleep(0.2)
    
    print("[CMD] Sending START...")
    send_command(dev, bytes([CMD_START_STREAM]))  # START
    time.sleep(0.5)
    
    # Read frames for 10 seconds
    frame_stats = defaultdict(int)
    total_frames = 0
    start_time = time.time()
    test_duration = 10.0
    
    print(f"\n[TEST] Reading frames for {test_duration} seconds...")
    print("-" * 60)
    
    while time.time() - start_time < test_duration:
        try:
            data = dev.read(EP_IN, 4096, timeout=500)
            if len(data) < 32:
                continue
            
            # Parse header (v1, 32 bytes)
            magic, ver, flags, seq, ts, ns, zone_cnt, zone_off, zone_len, reserved, reserved2, crc16 = struct.unpack_from(
                '<HBBIIHHIIIHH', data, 0
            )
            
            if magic != 0xA55A:
                print(f"[WARN] Invalid magic: 0x{magic:04X}")
                continue
            
            # Determine channel
            ch_mask = flags & 0x03
            ch_name = 'A' if ch_mask == 0x01 else 'B'
            
            # Determine parity from reserved2 (buffer_index & 1).
            # bit7 flags is TEST marker in current firmware.
            parity_bit = reserved2 & 0x01
            parity_name = 'odd' if parity_bit else 'even'
            
            # Frame type
            frame_type = f"{ch_name}_{parity_name}"
            frame_stats[frame_type] += 1
            total_frames += 1
            
            # Print first occurrence of each type
            if frame_stats[frame_type] == 1:
                print(f"[FIRST] {frame_type}: flags=0x{flags:02X}, seq={seq}, ns={ns}")
            
        except usb.core.USBTimeoutError:
            continue
        except usb.core.USBError as e:
            print(f"[ERROR] USB read error: {e}")
            break
    
    elapsed = time.time() - start_time
    
    # Send STOP
    print("\n[CMD] Sending STOP...")
    send_command(dev, bytes([CMD_STOP_STREAM]))
    
    # Print statistics
    print("\n" + "=" * 60)
    print("Frame Statistics")
    print("=" * 60)
    print(f"Total Frames:  {total_frames}")
    print(f"Duration:      {elapsed:.1f} seconds")
    print(f"Frame Rate:    {total_frames/elapsed:.2f} frames/s")
    print()
    print("Frame Type Distribution:")
    print("-" * 60)
    for frame_type in ['A_even', 'A_odd', 'B_even', 'B_odd']:
        count = frame_stats[frame_type]
        percent = (count / total_frames * 100) if total_frames > 0 else 0
        print(f"  {frame_type:<10} {count:>6} frames  ({percent:>5.1f}%)")
    print("=" * 60)
    
    # Verify all types present
    missing_types = [t for t in ['A_even', 'A_odd', 'B_even', 'B_odd'] if frame_stats[t] == 0]
    if missing_types:
        print(f"\n[FAIL] Missing frame types: {', '.join(missing_types)}")
        return 1
    else:
        print("\n[PASS] All 4 frame types received!")
        return 0

if __name__ == "__main__":
    exit(main())
