#!/usr/bin/env python3
import serial
import sys
import time

port = sys.argv[1] if len(sys.argv) > 1 else 'COM9'
logfile = sys.argv[2] if len(sys.argv) > 2 else 'serial_log.txt'

ser = serial.Serial(port, 115200, timeout=0.5)
print(f"Logging {port} to {logfile}...", file=sys.stderr)

with open(logfile, 'w', encoding='utf-8') as f:
    f.write(f"=== Serial log from {port} at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    f.flush()
    try:
        while True:
            line = ser.readline()
            if line:
                decoded = line.decode('utf-8', errors='replace').rstrip()
                print(decoded, file=sys.stderr)  # Also print to console
                f.write(decoded + '\n')
                f.flush()
    except KeyboardInterrupt:
        pass

ser.close()
