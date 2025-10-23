#!/usr/bin/env python3
# Pull a tiny session over COM (CDC): START -> read TEST + A + B -> STOP
# Requires: pip install pyserial

import sys
import time
import struct
import serial

CMD_START = 0x20
CMD_STOP  = 0x21
CMD_GET_STATUS = 0x30

MAG0 = 0x5A
MAG1 = 0xA5


def open_port(dev='COM4', baud=115200, timeout=2.0):
    ser = serial.Serial(dev, baudrate=baud, timeout=timeout)
    ser.reset_input_buffer(); ser.reset_output_buffer()
    return ser


def send_cmd(ser, *bytes_list):
    ser.write(bytes(bytes_list)); ser.flush()


def read_exact(ser, n):
    data = bytearray()
    while len(data) < n:
        chunk = ser.read(n - len(data))
        if not chunk:
            raise TimeoutError(f"Timeout reading {n} bytes (got {len(data)})")
        data += chunk
    return bytes(data)


def read_frame(ser):
    # Read fixed 32-byte header
    hdr = read_exact(ser, 32)
    if hdr[0] != MAG0 or hdr[1] != MAG1:
        raise RuntimeError(f"Bad magic: {hdr[:4].hex()}")
    ver   = hdr[2]
    flags = hdr[3]
    (seq, ts, total_samples) = struct.unpack_from('<IIH', hdr, 4)
    # If CRC flag (bit 0x04) is set, CRC16 is in bytes 30..31; calc over first 30+payload
    payload = read_exact(ser, total_samples * 2)
    # Align to 64-byte boundary (CDC path pads to 64)
    total = 32 + len(payload)
    pad = (64 - (total & 63)) & 63
    if pad:
        _ = read_exact(ser, pad)
    return {
        'ver': ver, 'flags': flags, 'seq': seq, 'ts': ts,
        'samples': total_samples, 'payload_len': len(payload)
    }


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else 'COM4'
    ser = open_port(port)
    print(f"Opened {port}")
    # START
    send_cmd(ser, CMD_START)
    # Expect TEST (8 samples)
    test = read_frame(ser)
    print('TEST:', test)
    # Expect A (ADC0) and B (ADC1)
    a = read_frame(ser)
    b = read_frame(ser)
    print('A:', a)
    print('B:', b)
    # STOP
    send_cmd(ser, CMD_STOP)
    ser.close()

if __name__ == '__main__':
    main()
