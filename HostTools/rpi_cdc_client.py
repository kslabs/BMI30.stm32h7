#!/usr/bin/env python3
import sys
import time
import struct
import zlib
import serial

CMD_PING = 0x01
CMD_SET_WINDOWS = 0x10
CMD_SET_BLOCK_RATE = 0x11
CMD_START_STREAM = 0x20
CMD_STOP_STREAM = 0x21
CMD_GET_STATUS = 0x30

RSP_ACK = 0x80
RSP_NACK = 0x81
RSP_STATUS = 0x82

FRAME_MAGIC0 = 0x5A
FRAME_MAGIC1 = 0xA5


def open_port(dev='/dev/ttyACM0', baud=115200, timeout=1.0):
    ser = serial.Serial(dev, baudrate=baud, timeout=timeout)
    # CDC игнорирует скорость, но pyserial требует параметр
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser


def send_cmd(ser, data: bytes):
    ser.write(data)
    ser.flush()


def expect_ack(ser, echo_cmd):
    r = ser.read(2)
    if len(r) != 2 or r[0] != RSP_ACK or r[1] != echo_cmd:
        raise RuntimeError(f"ACK failed, got {r}")


def cmd_ping(ser):
    send_cmd(ser, bytes([CMD_PING]))
    expect_ack(ser, CMD_PING)


def cmd_set_windows(ser, start0, len0, start1, len1):
    payload = struct.pack('<BHHHH', CMD_SET_WINDOWS, start0, len0, start1, len1)
    send_cmd(ser, payload)
    expect_ack(ser, CMD_SET_WINDOWS)


def cmd_set_block_rate(ser, hz):
    payload = struct.pack('<BH', CMD_SET_BLOCK_RATE, hz)
    send_cmd(ser, payload)
    expect_ack(ser, CMD_SET_BLOCK_RATE)


def cmd_start(ser):
    send_cmd(ser, bytes([CMD_START_STREAM]))
    expect_ack(ser, CMD_START_STREAM)


def cmd_stop(ser):
    send_cmd(ser, bytes([CMD_STOP_STREAM]))
    expect_ack(ser, CMD_STOP_STREAM)


def cmd_get_status(ser):
    send_cmd(ser, bytes([CMD_GET_STATUS]))
    # Простой парсинг STATUS: opcode(1) ver(1) streaming(1) rate(2) seq(4) win_count(1) + 2 окна (start,len)*2
    r = ser.read(1)
    if len(r) != 1 or r[0] != RSP_STATUS:
        raise RuntimeError('Bad STATUS header')
    rest = ser.read(1+1+2+4+1 + 16)  # может прийти не весь буфер — это пример
    return r + rest


def read_exact(ser, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = ser.read(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def find_magic(ser):
    # Поиск 0x5A 0xA5 в потоке
    prev = None
    while True:
        b = ser.read(1)
        if not b:
            return False
        if prev == FRAME_MAGIC0 and b[0] == FRAME_MAGIC1:
            return True
        prev = b[0]


def crc32_le(data: bytes) -> int:
    # Совместимо с прошивкой: init=0xFFFFFFFF, финальный XOR
    return zlib.crc32(data, 0xFFFFFFFF) ^ 0xFFFFFFFF


def read_frame(ser):
    if not find_magic(ser):
        return None
    # Уже съели 0x5A 0xA5, дочитаем оставшиеся 14 байт заголовка
    rest = read_exact(ser, 14)
    if len(rest) != 14:
        return None
    hdr = bytes([FRAME_MAGIC0, FRAME_MAGIC1]) + rest
    # Разбор заголовка
    magic0, magic1, ver, flags, seq, msec, win_count, sample_bits, channels, reserved = struct.unpack('<BBBBIIBBBB', hdr)
    # Читаем таблицу окон
    table = read_exact(ser, win_count * 4)
    if len(table) != win_count * 4:
        return None
    wins = []
    total_len = 0
    for i in range(win_count):
        start, length = struct.unpack_from('<HH', table, i*4)
        wins.append((start, length))
        total_len += length
    # Вычисляем размер payload (интерливинг L/R 16 бит)
    payload_len = total_len * 2 * 2
    payload = read_exact(ser, payload_len)
    if len(payload) != payload_len:
        return None
    crc_bytes = read_exact(ser, 4)
    if len(crc_bytes) != 4:
        return None
    (crc_recv,) = struct.unpack('<I', crc_bytes)
    crc_calc = crc32_le(hdr + table + payload)
    if crc_calc != crc_recv:
        raise RuntimeError(f"CRC mismatch: calc=0x{crc_calc:08X} recv=0x{crc_recv:08X}")
    return {
        'ver': ver,
        'seq': seq,
        'msec': msec,
        'win_count': win_count,
        'windows': wins,
        'sample_bits': sample_bits,
        'channels': channels,
        'payload': payload,
    }


def main():
    dev = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM0'
    ser = open_port(dev)
    print(f"Opened {dev}")

    print('PING...')
    cmd_ping(ser)
    print('OK')

    print('SET_WINDOWS (100,300) (700,300)')
    cmd_set_windows(ser, 100, 300, 700, 300)
    print('SET_BLOCK_RATE 200 Hz')
    cmd_set_block_rate(ser, 200)

    print('START_STREAM')
    cmd_start(ser)

    t0 = time.time()
    frames = 0
    try:
        while time.time() - t0 < 5.0:
            f = read_frame(ser)
            if f is None:
                continue
            frames += 1
            # Пример: печать первых 2 сэмплов пары L/R в первом окне
            print(f"seq={f['seq']} win_count={f['win_count']} payload={len(f['payload'])}B")
    finally:
        print('STOP_STREAM')
        try:
            cmd_stop(ser)
        except Exception:
            pass
        ser.close()
        print(f"Frames received: {frames}")

if __name__ == '__main__':
    main()
