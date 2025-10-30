#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADC Stream Oscilloscope with Qt GUI
Real-time visualization of dual-channel ADC data from STM32 USB device.

Usage:
    python plot_adc_qt.py
    python plot_adc_qt.py --rate-hz 200 --buffer-size 1000

Requirements:
    - PyQt5 or PyQt6
    - pyqtgraph
    - pyusb
    - numpy

Install:
    pip install PyQt5 pyqtgraph pyusb numpy
"""

import sys
import usb.core
import usb.util
import struct
import time
import argparse
import numpy as np
from collections import deque

# Try PyQt6 first, fallback to PyQt5
try:
    from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QLabel
    from PyQt6.QtCore import QTimer, Qt
    PYQT_VERSION = 6
except ImportError:
    try:
        from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QLabel
        from PyQt5.QtCore import QTimer, Qt
        PYQT_VERSION = 5
    except ImportError:
        print("ERROR: PyQt5 or PyQt6 not found. Install with: pip install PyQt5")
        sys.exit(1)

import pyqtgraph as pg

# USB Device identifiers
VID = 0xCAFE
PID = 0x4001
VENDOR_IF = 2
VENDOR_EP_OUT = 0x03
VENDOR_EP_IN = 0x83
ALT_SETTING = 1

# Frame structure
FRAME_HEADER = [0x5A, 0xA5, 0x01]
FRAME_HEADER_SIZE = 32  # Full header is 32 bytes
FRAME_SIZE = 1856
SAMPLES_PER_FRAME = 912  # (FRAME_SIZE - 32) // 2 = 912 samples

# Commands
CMD_SET_WINDOWS = 0x10
CMD_SET_RATE = 0x11
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14
CMD_START = 0x20
CMD_STOP = 0x21
CMD_GET_STATUS = 0x30


class USBDevice:
    """USB Device communication handler"""
    
    def __init__(self):
        self.dev = None
        self.ep_in = None
        self.ep_out = None
        
    def connect(self):
        """Find and configure USB device"""
        print(f"[USB] Searching for device VID={VID:#06x} PID={PID:#06x}...")
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        
        if self.dev is None:
            raise RuntimeError(f"Device not found: VID={VID:#06x} PID={PID:#06x}")
        
        print(f"[USB] Device found: {self.dev}")
        
        # Detach kernel driver if needed (Linux only, skip on Windows)
        try:
            if self.dev.is_kernel_driver_active(VENDOR_IF):
                print(f"[USB] Detaching kernel driver from interface {VENDOR_IF}")
                self.dev.detach_kernel_driver(VENDOR_IF)
        except NotImplementedError:
            print("[USB] Kernel driver detach not needed on Windows")
        except Exception as e:
            print(f"[USB] Warning: Could not detach kernel driver: {e}")
        
        # Claim interface
        usb.util.claim_interface(self.dev, VENDOR_IF)
        print(f"[USB] Claimed interface {VENDOR_IF}")
        
        # Set alternate setting to activate endpoints
        self.dev.set_interface_altsetting(VENDOR_IF, ALT_SETTING)
        print(f"[USB] Set alternate setting {ALT_SETTING}")
        
        # Get endpoints
        cfg = self.dev.get_active_configuration()
        intf = cfg[(VENDOR_IF, ALT_SETTING)]
        
        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        
        self.ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )
        
        if not self.ep_out or not self.ep_in:
            raise RuntimeError("Endpoints not found")
        
        print(f"[USB] OUT endpoint: {self.ep_out.bEndpointAddress:#04x}")
        print(f"[USB] IN endpoint: {self.ep_in.bEndpointAddress:#04x}")
        
    def configure_streaming(self, win1_start=100, win1_len=300, win2_start=700, win2_len=300, rate_hz=200):
        """Configure streaming parameters"""
        print(f"[CONFIG] Windows: ({win1_start}, {win1_len}), ({win2_start}, {win2_len})")
        print(f"[CONFIG] Rate: {rate_hz} Hz")
        
        # SET_WINDOWS: cmd(1) + win1_start(2) + win1_len(2) + win2_start(2) + win2_len(2)
        win_data = struct.pack('<BHHHH', CMD_SET_WINDOWS, win1_start, win1_len, win2_start, win2_len)
        n = self.ep_out.write(win_data)
        print(f"[CONFIG] SET_WINDOWS written: {n} bytes")
        
        # SET_RATE: cmd(1) + rate_hz(2)
        rate_data = struct.pack('<BH', CMD_SET_RATE, rate_hz)
        n = self.ep_out.write(rate_data)
        print(f"[CONFIG] SET_RATE written: {n} bytes")
        
        # SET_FULL_MODE: cmd(1) + full(1), full=1 for real ADC data
        n = self.ep_out.write(bytes([CMD_SET_FULL_MODE, 0x01]))
        print(f"[CONFIG] SET_FULL_MODE(1) written: {n} bytes")
        
        # SET_PROFILE: cmd(1) + profile(1)
        n = self.ep_out.write(struct.pack('<BB', CMD_SET_PROFILE, 2))
        print(f"[CONFIG] SET_PROFILE(2) written: {n} bytes")
        
        print("[CONFIG] Streaming configured")
        
    def start_streaming(self):
        """Send START command"""
        n = self.ep_out.write(bytes([CMD_START]))
        print(f"[STREAM] Started - CMD_START written: {n} bytes")
        # Send GET_STATUS to trigger device (like vendor_usb does)
        try:
            self.ep_out.write(bytes([CMD_GET_STATUS]))
            print(f"[STREAM] GET_STATUS queued")
        except Exception as e:
            print(f"[WARN] GET_STATUS failed: {e}")
        
    def stop_streaming(self):
        """Send STOP command"""
        n = self.ep_out.write(bytes([CMD_STOP]))
        print(f"[STREAM] Stopped - CMD_STOP written: {n} bytes")
        
    def read_frame(self, timeout=1000):
        """Read one complete frame from IN endpoint (may require multiple USB reads)"""
        # Frame format: [5A A5 01 CH] + 926*2 bytes = 1856 bytes total
        # USB maxPacketSize=64, so need multiple reads
        EXPECTED_FRAME_SIZE = 1856
        buffer = bytearray()
        
        try:
            # Read until we have complete frame
            while len(buffer) < EXPECTED_FRAME_SIZE:
                chunk_size = min(512, EXPECTED_FRAME_SIZE - len(buffer))
                chunk = self.ep_in.read(chunk_size, timeout=timeout)
                buffer.extend(chunk)
                
                # Safety check - if we got header, validate expected size
                if len(buffer) >= 4:
                    if buffer[0] != 0x5A or buffer[1] != 0xA5 or buffer[2] != 0x01:
                        print(f"[WARN] Invalid frame header: {buffer[:4].hex()}")
                        return None
                        
            return bytes(buffer)
            
        except usb.core.USBError as e:
            if e.errno == 110 or e.errno == 10060:  # Timeout (Linux/Windows)
                return None
            raise
        
    def disconnect(self):
        """Release USB device"""
        if self.dev:
            try:
                self.stop_streaming()
            except:
                pass
            try:
                usb.util.release_interface(self.dev, VENDOR_IF)
                print("[USB] Released interface")
            except:
                pass


class FrameParser:
    """Parse ADC frames from USB stream"""
    
    def __init__(self):
        self.buffer = bytearray()
        
    def add_data(self, data):
        """Add received data to buffer"""
        if data:
            self.buffer.extend(data)
            
    def get_frame(self):
        """Extract one complete frame from buffer, returns (channel, samples) or None"""
        # Search for frame header
        while len(self.buffer) >= FRAME_SIZE:
            if (self.buffer[0] == FRAME_HEADER[0] and 
                self.buffer[1] == FRAME_HEADER[1] and 
                self.buffer[2] == FRAME_HEADER[2]):
                
                # Extract frame
                frame = bytes(self.buffer[:FRAME_SIZE])
                self.buffer = self.buffer[FRAME_SIZE:]
                
                # Parse channel from flags (byte 3)
                flags = frame[3]
                if flags & 0x01:
                    channel = 0x01  # ADC0 (A)
                elif flags & 0x02:
                    channel = 0x02  # ADC1 (B)
                else:
                    continue  # Invalid channel, search for next frame
                    
                # Parse samples (16-bit little-endian, starting from byte 32)
                samples = []
                for i in range(FRAME_HEADER_SIZE, FRAME_SIZE, 2):
                    if i + 1 < len(frame):
                        sample = struct.unpack('<h', frame[i:i+2])[0]
                        samples.append(sample)
                
                return (channel, np.array(samples, dtype=np.int16))
            else:
                # Skip one byte and search again
                self.buffer = self.buffer[1:]
                
        return None  # Not enough data for complete frame


class ADCOscilloscope(QMainWindow):
    """Main oscilloscope window"""
    
    def __init__(self, rate_hz=200, buffer_size=1000):
        super().__init__()
        
        self.rate_hz = rate_hz
        self.buffer_size = buffer_size
        
        # Data buffers - now store latest complete frame per channel
        self.ch_a_samples = None  # Latest Channel A frame
        self.ch_b_samples = None  # Latest Channel B frame
        self.ch_a_time = None     # Time axis for Channel A
        self.ch_b_time = None     # Time axis for Channel B
        
        self.sample_counter = 0
        self.start_time = time.time()
        self.frames_received = 0
        self.frames_displayed = 0
        self.display_decimation = 1  # Display every Nth frame
        
        # USB device
        self.usb_dev = USBDevice()
        self.frame_parser = FrameParser()
        self.streaming = False
        
        # Setup UI
        self.init_ui()
        
        # Timer for reading USB data
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_data)
        
    def init_ui(self):
        """Initialize user interface"""
        self.setWindowTitle('ADC Stream Oscilloscope - STM32 USB')
        self.setGeometry(100, 100, 1200, 800)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Layout
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        # Control panel
        control_layout = QHBoxLayout()
        
        self.btn_connect = QPushButton('Connect')
        self.btn_connect.clicked.connect(self.connect_device)
        control_layout.addWidget(self.btn_connect)
        
        self.btn_start = QPushButton('Start')
        self.btn_start.clicked.connect(self.start_streaming)
        self.btn_start.setEnabled(False)
        control_layout.addWidget(self.btn_start)
        
        self.btn_stop = QPushButton('Stop')
        self.btn_stop.clicked.connect(self.stop_streaming)
        self.btn_stop.setEnabled(False)
        control_layout.addWidget(self.btn_stop)
        
        self.btn_clear = QPushButton('Clear')
        self.btn_clear.clicked.connect(self.clear_data)
        control_layout.addWidget(self.btn_clear)
        
        # Display decimation control
        self.btn_decim_down = QPushButton('Display ↓')
        self.btn_decim_down.clicked.connect(self.decrease_display_rate)
        control_layout.addWidget(self.btn_decim_down)
        
        self.btn_decim_up = QPushButton('Display ↑')
        self.btn_decim_up.clicked.connect(self.increase_display_rate)
        control_layout.addWidget(self.btn_decim_up)
        
        self.status_label = QLabel('Status: Disconnected')
        control_layout.addWidget(self.status_label)
        
        control_layout.addStretch()
        
        self.info_label = QLabel('Frames: 0 | Samples: 0')
        control_layout.addWidget(self.info_label)
        
        layout.addLayout(control_layout)
        
        # Plot widgets
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        
        # Channel A plot
        self.plot_a = pg.PlotWidget(title="Channel A (ADC) - Non-zero samples only")
        self.plot_a.setLabel('left', 'ADC Value', units='LSB')
        self.plot_a.setLabel('bottom', 'Sample Index')
        self.plot_a.showGrid(x=True, y=True, alpha=0.3)
        self.plot_a.setYRange(-32768, 32767)  # 16-bit signed range
        self.curve_a = self.plot_a.plot(pen=pg.mkPen('r', width=2), symbol='o', symbolSize=3)
        layout.addWidget(self.plot_a)
        
        # Channel B plot
        self.plot_b = pg.PlotWidget(title="Channel B (ADC) - Non-zero samples only")
        self.plot_b.setLabel('left', 'ADC Value', units='LSB')
        self.plot_b.setLabel('bottom', 'Sample Index')
        self.plot_b.showGrid(x=True, y=True, alpha=0.3)
        self.plot_b.setYRange(-32768, 32767)  # 16-bit signed range
        self.curve_b = self.plot_b.plot(pen=pg.mkPen('b', width=2), symbol='o', symbolSize=3)
        layout.addWidget(self.plot_b)
        
    def connect_device(self):
        """Connect to USB device"""
        try:
            self.usb_dev.connect()
            self.usb_dev.configure_streaming(rate_hz=self.rate_hz)
            
            self.status_label.setText('Status: Connected')
            self.btn_connect.setEnabled(False)
            self.btn_start.setEnabled(True)
            
        except Exception as e:
            self.status_label.setText(f'Status: Error - {e}')
            print(f"[ERROR] Connection failed: {e}")
            
    def start_streaming(self):
        """Start data streaming"""
        try:
            self.usb_dev.start_streaming()
            self.streaming = True
            self.start_time = time.time()
            self.sample_counter = 0
            self.frames_received = 0
            
            # Give device time to start sending data before first read
            time.sleep(0.2)  # 200ms delay (increased from 50ms)
            
            self.timer.start(10)  # 10ms polling interval
            
            self.status_label.setText('Status: Streaming')
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            
        except Exception as e:
            self.status_label.setText(f'Status: Error - {e}')
            print(f"[ERROR] Start failed: {e}")
            
    def stop_streaming(self):
        """Stop data streaming"""
        try:
            self.streaming = False
            self.timer.stop()
            self.usb_dev.stop_streaming()
            
            self.status_label.setText('Status: Stopped')
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            
        except Exception as e:
            self.status_label.setText(f'Status: Error - {e}')
            print(f"[ERROR] Stop failed: {e}")
            
    def clear_data(self):
        """Clear plot data"""
        self.ch_a_samples = None
        self.ch_b_samples = None
        self.ch_a_time = None
        self.ch_b_time = None
        self.sample_counter = 0
        self.frames_received = 0
        self.frames_displayed = 0
        
        self.curve_a.setData([], [])
        self.curve_b.setData([], [])
        self.info_label.setText('Frames: 0 | Displayed: 0 | Samples: 0')
        
    def decrease_display_rate(self):
        """Decrease display update rate (show fewer frames)"""
        self.display_decimation = min(self.display_decimation * 2, 64)
        self.status_label.setText(f'Status: Streaming (Display 1/{self.display_decimation})')
        print(f"[DISPLAY] Decimation: 1/{self.display_decimation}")
        
    def increase_display_rate(self):
        """Increase display update rate (show more frames)"""
        self.display_decimation = max(self.display_decimation // 2, 1)
        self.status_label.setText(f'Status: Streaming (Display 1/{self.display_decimation})')
        print(f"[DISPLAY] Decimation: 1/{self.display_decimation}")
        
    def update_data(self):
        """Read USB data and update plots"""
        if not self.streaming:
            return
            
        try:
            # Read multiple USB packets per update to keep up with data rate
            for _ in range(10):  # Read up to 10 packets per timer tick
                try:
                    # Read raw USB packet (up to 512 bytes)
                    chunk = self.usb_dev.ep_in.read(2048, timeout=10)
                    if chunk:
                        self.frame_parser.add_data(chunk)
                except usb.core.USBError as e:
                    if e.errno == 110 or e.errno == 10060:  # Timeout
                        break
                    raise
                    
            # Process frames
            updated_a = False
            updated_b = False
            
            while True:
                result = self.frame_parser.get_frame()
                if result is None:
                    break
                    
                channel, samples = result
                self.frames_received += 1
                self.sample_counter += len(samples)
                
                # Diagnostic: Check for zero data
                non_zero = np.count_nonzero(samples)
                sample_min = np.min(samples)
                sample_max = np.max(samples)
                sample_mean = np.mean(samples)
                
                # Find non-zero regions
                non_zero_indices = np.nonzero(samples)[0]
                if len(non_zero_indices) > 0:
                    first_non_zero = non_zero_indices[0]
                    last_non_zero = non_zero_indices[-1]
                    non_zero_span = last_non_zero - first_non_zero + 1
                else:
                    first_non_zero = last_non_zero = non_zero_span = 0
                
                ch_name = "A" if channel == 0x01 else "B"
                
                # Print detailed diagnostic every 100 frames
                if self.frames_received % 100 == 0:
                    print(f"[FRAME {self.frames_received}] CH={ch_name} len={len(samples)} "
                          f"non_zero={non_zero}/{len(samples)} "
                          f"min={sample_min} max={sample_max} mean={sample_mean:.1f} "
                          f"region=[{first_non_zero}:{last_non_zero}] span={non_zero_span}")
                
                # Store latest frame for each channel
                if channel == 0x01:  # Channel A
                    # Display ALL samples (including zeros - they are valid ADC readings)
                    # but use continuous time axis for scrolling display
                    self.ch_a_time = np.arange(len(samples), dtype=float)
                    self.ch_a_samples = samples
                    updated_a = True
                    
                else:  # Channel B (0x02)
                    # Display ALL samples (including zeros - they are valid ADC readings)
                    self.ch_b_time = np.arange(len(samples), dtype=float)
                    self.ch_b_samples = samples
                    updated_b = True
                    
            # Update plots only if new data arrived and decimation allows
            should_display = (self.frames_received % self.display_decimation) == 0
            
            if should_display:
                if updated_a and self.ch_a_samples is not None:
                    self.curve_a.setData(self.ch_a_time, self.ch_a_samples)
                    self.frames_displayed += 1
                    
                if updated_b and self.ch_b_samples is not None:
                    self.curve_b.setData(self.ch_b_time, self.ch_b_samples)
                    
            # Update info label
            elapsed = time.time() - self.start_time
            fps = self.frames_received / elapsed if elapsed > 0 else 0
            self.info_label.setText(
                f'Frames: {self.frames_received} | Displayed: {self.frames_displayed} | '
                f'Samples: {self.sample_counter} | Rate: {fps:.1f} fps'
            )
            
        except Exception as e:
            print(f"[ERROR] Update failed: {e}")
            import traceback
            traceback.print_exc()
            self.stop_streaming()
            self.status_label.setText(f'Status: Error - {e}')
            
    def closeEvent(self, event):
        """Handle window close"""
        if self.streaming:
            self.stop_streaming()
        self.usb_dev.disconnect()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description='ADC Stream Oscilloscope with Qt GUI')
    parser.add_argument('--rate-hz', type=int, default=200, help='Sampling rate in Hz (default: 200)')
    parser.add_argument('--buffer-size', type=int, default=2000, help='Plot buffer size (default: 2000)')
    parser.add_argument('--auto-start', action='store_true', help='Auto-connect and start streaming on launch')
    
    args = parser.parse_args()
    
    print(f"=== ADC Stream Oscilloscope ===")
    print(f"PyQt Version: {PYQT_VERSION}")
    print(f"Rate: {args.rate_hz} Hz")
    print(f"Buffer: {args.buffer_size} samples")
    print(f"Auto-start: {args.auto_start}")
    
    app = QApplication(sys.argv)
    window = ADCOscilloscope(rate_hz=args.rate_hz, buffer_size=args.buffer_size)
    window.show()
    
    # Auto-connect and start if requested
    if args.auto_start:
        print("[AUTO] Connecting to device...")
        window.connect_device()
        print("[AUTO] Starting streaming...")
        window.start_streaming()
    
    sys.exit(app.exec() if PYQT_VERSION == 6 else app.exec_())


if __name__ == '__main__':
    main()
