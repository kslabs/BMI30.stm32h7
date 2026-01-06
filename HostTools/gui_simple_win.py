#!/usr/bin/env python3
"""
Минимальный GUI осциллограф для Windows
Только читает bulk данные без control transfers (stream должен быть уже запущен)
"""

import usb.core
import usb.util
import struct
import time
from collections import deque
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation

VID = 0xCAFE
PID = 0x4001
EP_IN = 0x83
EP_OUT = 0x03
HDR_SIZE = 16
CMD_START = 0x20

def find_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev:
        raise RuntimeError("Device not found")
    
    # Set altsetting 1 for vendor interface
    cfg = dev.get_active_configuration()
    intf = None
    for cfg_intf in cfg:
        if cfg_intf.bInterfaceClass == 0xFF:  # Vendor
            intf = cfg_intf
            break
    
    if not intf:
        raise RuntimeError("Vendor interface not found")
    
    # Try altsetting 1
    try:
        usb.util.claim_interface(dev, intf.bInterfaceNumber)
        dev.set_interface_altsetting(intf.bInterfaceNumber, 1)
        print(f"[OK] Device ready, IF#{intf.bInterfaceNumber} alt=1")
    except Exception as e:
        print(f"[WARN] Interface setup: {e}")
    
    # Send START command via bulk OUT
    try:
        dev.write(EP_OUT, bytes([CMD_START]), timeout=1000)
        print("[OK] START command sent via bulk")
    except Exception as e:
        print(f"[WARN] START command failed: {e}")
    
    return dev

def parse_packet(data):
    """Parse packet header and payload"""
    b = bytes(data)
    if len(b) < HDR_SIZE:
        return None, None
    
    try:
        # Header: magic(2), ver(1), flags(1), seq(4), ts(4), ns(2), zc(2) = 16 bytes
        magic, ver, flags, seq, ts, ns, zc = struct.unpack_from('<HBBIIHH', b, 0)
        
        if magic != 0xA55A:
            return None, None
        
        # Extract payload (may be multi-chunk - we get what we can)
        payload_start = HDR_SIZE
        payload_end = min(len(b), HDR_SIZE + ns * 2)
        payload = b[payload_start:payload_end]
        
        # Decode samples (little-endian 16-bit)
        num_samples = len(payload) // 2
        vals = [payload[2*i] | (payload[2*i+1] << 8) for i in range(num_samples)]
        
        return flags, vals
    except Exception as e:
        return None, None

class Oscilloscope:
    def __init__(self, dev, maxlen=600):
        self.dev = dev
        self.maxlen = maxlen
        
        # Data buffers
        self.data_a = deque(maxlen=maxlen)
        self.data_b = deque(maxlen=maxlen)
        
        # Stats
        self.count_a = 0
        self.count_b = 0
        self.last_seq_a = None
        self.last_seq_b = None
        
        # Setup plot
        self.fig, (self.ax_a, self.ax_b) = plt.subplots(2, 1, figsize=(12, 8))
        self.fig.suptitle('BMI30 Oscilloscope (Windows - Bulk Read Only)', fontsize=14)
        
        self.line_a, = self.ax_a.plot([], [], 'b-', linewidth=0.8)
        self.ax_a.set_xlim(0, maxlen)
        self.ax_a.set_ylim(0, 65535)
        self.ax_a.set_title('Channel A')
        self.ax_a.grid(True, alpha=0.3)
        
        self.line_b, = self.ax_b.plot([], [], 'r-', linewidth=0.8)
        self.ax_b.set_xlim(0, maxlen)
        self.ax_b.set_ylim(0, 65535)
        self.ax_b.set_title('Channel B')
        self.ax_b.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        self.start_time = time.time()
        self.frame_count = 0
        
    def read_packets(self):
        """Read multiple packets in one call"""
        packets_read = 0
        for _ in range(20):  # Read up to 20 packets per update
            try:
                data = self.dev.read(EP_IN, 4096, timeout=100)
                flags, vals = parse_packet(data)
                
                if vals:
                    if flags == 0x01:  # Channel A
                        self.data_a.extend(vals)
                        self.count_a += 1
                        if self.count_a <= 3:  # Log first few packets
                            print(f"[DATA] Channel A: {len(vals)} samples")
                    elif flags == 0x02:  # Channel B
                        self.data_b.extend(vals)
                        self.count_b += 1
                        if self.count_b <= 3:
                            print(f"[DATA] Channel B: {len(vals)} samples")
                    packets_read += 1
                elif data and len(data) > 0:
                    # Got data but failed to parse
                    if packets_read == 0:  # Only log once per update
                        print(f"[WARN] Packet parse failed, len={len(data)}, first bytes: {bytes(data[:16]).hex()}")
            except usb.core.USBError as e:
                if packets_read == 0 and self.frame_count % 20 == 0:  # Log timeout every 20 frames
                    print(f"[WARN] USB timeout: {e}")
                break
            except Exception as e:
                print(f"[ERR] Read: {e}")
                break
        
        return packets_read
    
    def update(self, frame):
        """Animation update callback"""
        try:
            self.frame_count += 1
            
            # Read new data
            packets = self.read_packets()
            
            # Update plots
            if len(self.data_a) > 0:
                x = np.arange(len(self.data_a))
                y = np.array(list(self.data_a))
                self.line_a.set_data(x, y)
            
            if len(self.data_b) > 0:
                x = np.arange(len(self.data_b))
                y = np.array(list(self.data_b))
                self.line_b.set_data(x, y)
            
            # Update title every 10 frames
            if self.frame_count % 10 == 0:
                elapsed = time.time() - self.start_time
                fps = self.frame_count / elapsed if elapsed > 0 else 0
                self.fig.suptitle(
                    f'BMI30 Oscilloscope (Windows) | A: {self.count_a} frames | B: {self.count_b} frames | Display: {fps:.1f} FPS',
                    fontsize=12
                )
            
            return self.line_a, self.line_b
        except Exception as e:
            print(f"[ERR] Update: {e}")
            return self.line_a, self.line_b
    
    def run(self):
        """Start animation"""
        print("[GUI] Starting oscilloscope...")
        print("[GUI] Reading bulk data without sending commands")
        print("[GUI] If no data appears, device may need reset or START command")
        print("[GUI] Close window to exit")
        
        self.ani = animation.FuncAnimation(
            self.fig,
            self.update,
            interval=50,  # 20 FPS
            blit=False,  # Changed to False for better compatibility
            cache_frame_data=False
        )
        
        try:
            plt.show(block=True)
        except KeyboardInterrupt:
            print("\n[GUI] Interrupted")
        finally:
            print("[GUI] Closing...")

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Simple Windows oscilloscope')
    parser.add_argument('--maxlen', type=int, default=600, help='Max samples to display')
    args = parser.parse_args()
    
    print("="*70)
    print("BMI30 SIMPLE WINDOWS OSCILLOSCOPE")
    print("="*70)
    print("Note: This GUI only reads data, does not send control commands")
    print("      Stream should already be running (check with quick_status.cmd)")
    print("="*70)
    
    try:
        dev = find_device()
        osc = Oscilloscope(dev, maxlen=args.maxlen)
        osc.run()
    except KeyboardInterrupt:
        print("\n[EXIT] Interrupted by user")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
