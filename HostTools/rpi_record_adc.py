#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Упрощённая версия для Raspberry Pi - сохраняет данные в файл для последующей обработки.
Не требует matplotlib - можно использовать на headless системах.
"""
import os
import sys
import struct
import time
import argparse
import usb.core
import usb.util

def _parse_args():
    p = argparse.ArgumentParser(description="ADC stream recorder для RPI")
    p.add_argument('--vid', type=lambda x: int(x,16), default=0xCAFE)
    p.add_argument('--pid', type=lambda x: int(x,16), default=0x4001)
    p.add_argument('--intf', type=int, default=2)
    p.add_argument('--ep-in', dest='ep_in', type=lambda x: int(x,16), default=0x83)
    p.add_argument('--ep-out', dest='ep_out', type=lambda x: int(x,16), default=0x03)
    p.add_argument('--timeout-ms', type=int, default=3000)
    p.add_argument('--rate-hz', type=int, default=200)
    p.add_argument('--duration-sec', type=float, default=5.0)
    p.add_argument('--output', default='adc_data.csv', help='Выходной CSV файл')
    return p.parse_args()

args = _parse_args()

def find_device():
    dev = usb.core.find(idVendor=args.vid, idProduct=args.pid)
    if dev is None:
        print(f"[ERR] Device not found: VID=0x{args.vid:04X} PID=0x{args.pid:04X}")
        sys.exit(1)
    print(f"[USB] Found device: VID=0x{args.vid:04X} PID=0x{args.pid:04X}")
    return dev

def setup_device(dev):
    cfg = None
    intf = None
    for c in dev:
        for i in c:
            eps = [ep.bEndpointAddress for ep in i]
            if args.ep_out in eps and args.ep_in in eps:
                cfg = c
                intf = i
                break
        if cfg:
            break
    
    if not cfg or not intf:
        print(f"[ERR] Interface not found")
        sys.exit(2)
    
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except:
        pass
    
    try:
        usb.util.claim_interface(dev, args.intf)
        print(f"[USB] Claimed interface #{args.intf}")
    except Exception as e:
        print(f"[WARN] Claim failed: {e}")
    
    try:
        dev.set_interface_altsetting(interface=args.intf, alternate_setting=1)
        print(f"[USB] SetInterface(IF#{args.intf}, alt=1) OK")
    except Exception as e:
        print(f"[WARN] SetInterface failed: {e}")
    
    return dev

def configure_streaming(dev):
    try:
        payload = struct.pack('<BHHHH', 0x10, 100, 300, 700, 300)
        dev.write(args.ep_out, payload, timeout=1000)
        print(f"[CFG] SET_WINDOWS OK")
    except Exception as e:
        print(f"[WARN] SET_WINDOWS failed: {e}")
    
    try:
        payload = struct.pack('<BH', 0x11, args.rate_hz)
        dev.write(args.ep_out, payload, timeout=1000)
        print(f"[CFG] SET_BLOCK_RATE: {args.rate_hz} Hz")
    except Exception as e:
        print(f"[WARN] SET_BLOCK_RATE failed: {e}")
    
    try:
        dev.write(args.ep_out, bytes([0x13, 0x01]), timeout=1000)
        print(f"[CFG] SET_FULL_MODE: ADC")
    except:
        pass
    
    try:
        dev.write(args.ep_out, bytes([0x14, 0x02]), timeout=1000)
        print(f"[CFG] SET_PROFILE: 2")
    except:
        pass

def start_streaming(dev):
    dev.write(args.ep_out, bytes([0x20]), timeout=1000)
    print(f"[CMD] START sent")

def stop_streaming(dev):
    try:
        dev.write(args.ep_out, bytes([0x21]), timeout=1000)
        print(f"[CMD] STOP sent")
    except:
        pass

def parse_frame(data):
    if len(data) < 4:
        return None, []
    
    if data[0] != 0x5A or data[1] != 0xA5 or data[2] != 0x01:
        return None, []
    
    channel = 'A' if data[3] == 0x01 else 'B' if data[3] == 0x02 else None
    if not channel:
        return None, []
    
    samples = []
    for i in range(4, len(data)-1, 2):
        if i+1 < len(data):
            sample = struct.unpack('<H', data[i:i+2])[0]
            samples.append(sample)
    
    return channel, samples

def collect_and_save(dev, duration_sec, output_file):
    print(f"[DATA] Collecting data for {duration_sec} sec -> {output_file}")
    
    with open(output_file, 'w') as f:
        f.write("time_ms,channel,sample_value\n")
        
        start_time = time.time()
        rx_buffer = bytearray()
        sample_count_a = 0
        sample_count_b = 0
        
        while (time.time() - start_time) < duration_sec:
            try:
                chunk = bytes(dev.read(args.ep_in, 2048, timeout=args.timeout_ms))
                rx_buffer += chunk
                
                while len(rx_buffer) >= 1856:
                    idx = -1
                    for i in range(len(rx_buffer) - 3):
                        if rx_buffer[i] == 0x5A and rx_buffer[i+1] == 0xA5 and rx_buffer[i+2] == 0x01:
                            idx = i
                            break
                    
                    if idx == -1:
                        if len(rx_buffer) > 2000:
                            rx_buffer = rx_buffer[-1000:]
                        break
                    
                    if idx > 0:
                        rx_buffer = rx_buffer[idx:]
                    
                    if len(rx_buffer) < 1856:
                        break
                    
                    frame_data = bytes(rx_buffer[:1856])
                    rx_buffer = rx_buffer[1856:]
                    
                    channel, samples = parse_frame(frame_data)
                    if channel:
                        elapsed_ms = (time.time() - start_time) * 1000
                        for sample in samples:
                            f.write(f"{elapsed_ms:.3f},{channel},{sample}\n")
                        
                        if channel == 'A':
                            sample_count_a += len(samples)
                        else:
                            sample_count_b += len(samples)
                        
                        if (sample_count_a + sample_count_b) % 1000 < 50:
                            print(f"[DATA] A={sample_count_a}, B={sample_count_b} samples", end='\r')
            
            except usb.core.USBError as e:
                if e.errno == 110:
                    continue
                else:
                    print(f"\n[ERR] USB error: {e}")
                    break
            except KeyboardInterrupt:
                print("\n[INFO] Interrupted by user")
                break
    
    print(f"\n[DATA] Saved: A={sample_count_a}, B={sample_count_b} samples to {output_file}")
    return sample_count_a, sample_count_b

def main():
    print(f"=== ADC Stream Recorder for RPI ===")
    print(f"VID=0x{args.vid:04X} PID=0x{args.pid:04X} Rate={args.rate_hz}Hz Duration={args.duration_sec}s")
    
    try:
        dev = find_device()
        dev = setup_device(dev)
        configure_streaming(dev)
        start_streaming(dev)
        
        count_a, count_b = collect_and_save(dev, args.duration_sec, args.output)
        
        stop_streaming(dev)
        
        if count_a > 0 or count_b > 0:
            print(f"[OK] Data collection complete!")
            print(f"[INFO] Use: python3 plot_csv.py {args.output}")
        else:
            print("[ERR] No data received")
            sys.exit(3)
        
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted")
        sys.exit(130)
    except Exception as e:
        print(f"[ERR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
