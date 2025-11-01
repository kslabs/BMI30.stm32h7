#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI осциллограф для Vendor USB потока (интерфейс #2, IN=0x83, OUT=0x03).
- Настраивает окна/скорость, отправляет START и читает кадры A/B в реальном времени
- Строит график каналов A и B (сырые значения) в одном окне matplotlib

Требования:
- PyUSB (pip install pyusb)
- Matplotlib (pip install matplotlib)

Опционально (ускорение):
- NumPy (pip install numpy) — если доступен, парсинг payload быстрее

Подсказки:
- Если окно не появляется или backend не поддержан, установите tkinter или другой backend для matplotlib
"""
import os
import sys
import time
import struct
import argparse

try:
    import usb.core
    import usb.util
except Exception as e:
    print("[ERR] Требуется PyUSB: pip install pyusb")
    raise

# Matplotlib для рендера графика
try:
    import matplotlib.pyplot as plt
except Exception as e:
    print("[ERR] Требуется matplotlib: pip install matplotlib")
    raise

# NumPy опционально
try:
    import numpy as np
except Exception:
    np = None

# --------- Парсер аргументов ---------

def _parse_args():
    p = argparse.ArgumentParser(description="Vendor USB GUI-осциллограф")
    p.add_argument('--vid', type=lambda x: int(x,16), default=int(os.getenv('VND_VID','0xCAFE'),16), help='USB VID (hex)')
    p.add_argument('--pid', type=lambda x: int(x,16), default=int(os.getenv('VND_PID','0x4001'),16), help='USB PID (hex)')
    p.add_argument('--intf', type=int, default=int(os.getenv('VND_INTF','2')), help='Vendor interface index (default 2)')
    p.add_argument('--ep-in', dest='ep_in', type=lambda x: int(x,16), default=int(os.getenv('VND_EP_IN','0x83'),16), help='Bulk IN endpoint (hex)')
    p.add_argument('--ep-out', dest='ep_out', type=lambda x: int(x,16), default=int(os.getenv('VND_EP_OUT','0x03'),16), help='Bulk OUT endpoint (hex)')
    p.add_argument('--rate-hz', type=int, default=int(os.getenv('VND_RATE_HZ','200')), help='Block rate (Hz)')
    p.add_argument('--win0', nargs=2, type=int, metavar=('START','LEN'), default=(int(os.getenv('VND_WIN0_START','0')), int(os.getenv('VND_WIN0_LEN','1000'))), help='Window0 start,len')
    p.add_argument('--win1', nargs=2, type=int, metavar=('START','LEN'), default=(int(os.getenv('VND_WIN1_START','0')), int(os.getenv('VND_WIN1_LEN','0'))), help='Window1 start,len (0,0 = off)')
    p.add_argument('--async-mode', type=int, choices=[0,1], default=int(os.getenv('VND_ASYNC_MODE','0')), help='1=async A/B, 0=strict pairs')
    p.add_argument('--ch-mode', type=int, choices=[0,1,2], default=int(os.getenv('VND_CH_MODE','2')), help='0=A-only, 1=B-only, 2=both')
    p.add_argument('--read-timeout-ms', type=int, default=int(os.getenv('VND_READ_TIMEOUT','3000')), help='Read timeout per transfer (ms)')
    p.add_argument('--buffer-sec', type=float, default=5.0, help='Сколько секунд хранить в буфере графика (скользящее окно)')
    p.add_argument('--no-start', action='store_true', help='Не отправлять START (если уже запущено)')
    return p.parse_args()

args = _parse_args()

VID = args.vid
PID = args.pid
OUT_EP = args.ep_out
IN_EP  = args.ep_in
READ_TIMEOUT_MS = args.read_timeout_ms
WIN0_START, WIN0_LEN = args.win0
WIN1_START, WIN1_LEN = args.win1
RATE_HZ = args.rate_hz
ASYNC_MODE = args.async_mode
CH_MODE = args.ch_mode
IFACE_INDEX = args.intf

VND_CMD_SET_WINDOWS    = 0x10
VND_CMD_SET_BLOCK_RATE = 0x11
VND_CMD_SET_FULL_MODE  = 0x13
VND_CMD_SET_PROFILE    = 0x14
VND_CMD_START          = 0x20
VND_CMD_STOP           = 0x21
VND_CMD_SET_ASYNC_MODE = 0x18
VND_CMD_SET_CHMODE     = 0x19

# --------- Утилиты USB ---------

def log(s):
    print(s, flush=True)

def _is_timeout(e: Exception) -> bool:
    en = getattr(e, 'errno', None)
    if en in (10060, 110, 60):
        return True
    if 'timed out' in str(e).lower():
        return True
    return False

def write_vendor(dev, payload: bytes, timeout_ms: int = 1000, label: str = "CMD") -> int:
    try:
        return dev.write(OUT_EP, payload, timeout=timeout_ms)
    except Exception as e:
        if not _is_timeout(e):
            raise
        log(f"[HOST][WRITE][WARN] {label} timeout: {e}")
        try:
            try:
                dev.clear_halt(OUT_EP)
                log(f"[HOST][RECOVER] clear_halt OUT 0x{OUT_EP:02X} OK")
            except Exception as ce:
                log(f"[HOST][RECOVER][WARN] clear_halt OUT failed: {ce}")
            try:
                dev.set_interface_altsetting(interface=IFACE_INDEX, alternate_setting=0)
            except Exception:
                pass
            time.sleep(0.02)
            try:
                dev.set_interface_altsetting(interface=IFACE_INDEX, alternate_setting=1)
            except Exception as se1:
                log(f"[HOST][RECOVER][WARN] SetInterface alt=1 failed: {se1}")
            time.sleep(0.02)
            w = dev.write(OUT_EP, payload, timeout=timeout_ms)
            log(f"[HOST][WRITE][OK] {label} after recover: {w} bytes")
            return w
        except Exception as e2:
            raise e2

# --------- Парсинг кадров ---------

HDR_MAGIC = b"\x5A\xA5\x01"  # bytes 0..2
HDR_LEN = 32

class Frame:
    __slots__ = ("flags", "seq", "timestamp", "samples", "is_test")
    def __init__(self, flags: int, seq: int, timestamp: int, samples):
        self.flags = flags
        self.seq = seq
        self.timestamp = timestamp
        self.samples = samples  # list[int] or np.ndarray[int]
        self.is_test = bool(flags & 0x80)


def extract_frames(rx: bytearray):
    """Итеративный разбор накопленного буфера на кадры. Возвращает (frames, leftover)."""
    frames = []
    mv = memoryview(rx)
    i = 0
    L = len(rx)
    while True:
        if i + 4 > L:
            break
        # Поиск заголовка или STAT (игнор STAT в GUI)
        if mv[i:i+4].tobytes() == b'STAT':
            # STAT длина 64 или 52 — пропускаем
            if i + 64 <= L:
                i += 64
                continue
            elif i + 52 <= L:
                i += 52
                continue
            else:
                break
        # Поиск кадра
        if mv[i:i+3].tobytes() == HDR_MAGIC and i + 16 <= L:
            flags = mv[i+3]
            total_samples = int(mv[i+12]) | (int(mv[i+13]) << 8)
            flen = HDR_LEN + total_samples * 2
            if i + flen > L:
                break
            # Заголовок
            seq = int.from_bytes(mv[i+4:i+8], 'little')
            timestamp = int.from_bytes(mv[i+8:i+12], 'little')
            payload = mv[i+HDR_LEN:i+flen]
            # Парс payload -> массив 16-бит LE
            if np is not None:
                samples = np.frombuffer(payload.tobytes(), dtype='<i2')
            else:
                # Без NumPy: вручную распаковать
                samples = list(struct.unpack('<' + 'h'*total_samples, payload.tobytes()))
            frames.append(Frame(flags, seq, timestamp, samples))
            i += flen
            continue
        # Ресинхронизация: ищем STAT или HDR_MAGIC
        idx_stat = rx.find(b'STAT', i+1)
        idx_hdr = rx.find(HDR_MAGIC, i+1)
        nxt = -1
        if idx_stat != -1 and (idx_hdr == -1 or idx_stat < idx_hdr):
            nxt = idx_stat
        elif idx_hdr != -1:
            nxt = idx_hdr
        if nxt == -1:
            # Ничего полезного до конца
            i = L
            break
        i = nxt
    # Остаток
    leftover = bytearray(mv[i:].tobytes()) if i < L else bytearray()
    return frames, leftover

# --------- Инициализация устройства ---------

def open_device():
    log(f"[HOST][CFG] VID=0x{VID:04X} PID=0x{PID:04X} IF={IFACE_INDEX} IN=0x{IN_EP:02X} OUT=0x{OUT_EP:02X} rate={RATE_HZ}Hz async={ASYNC_MODE} chmode={CH_MODE}")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        log("[ERR] Устройство не найдено. Проверьте драйвер WinUSB (Zadig) на Interface 2.")
        sys.exit(1)
    try:
        dev.set_configuration()
    except Exception:
        pass
    # Выбираем alt=1
    try:
        usb.util.claim_interface(dev, IFACE_INDEX)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=IFACE_INDEX, alternate_setting=1)
        time.sleep(0.05)
    except Exception as e:
        log(f"[WARN] set_interface_altsetting alt=1: {e}")
    return dev


def configure_stream(dev):
    # Окна
    try:
        payload = struct.pack('<BHHHH', VND_CMD_SET_WINDOWS, WIN0_START, WIN0_LEN, WIN1_START, WIN1_LEN)
        w = write_vendor(dev, payload, timeout_ms=500, label='SET_WINDOWS')
        log(f"[HOST] SET_WINDOWS ({WIN0_START},{WIN0_LEN}) ({WIN1_START},{WIN1_LEN}) -> {w} bytes")
        time.sleep(0.01)
    except Exception as e:
        log(f"[WARN] SET_WINDOWS failed: {e}")
    # Скорость блоков
    try:
        payload = struct.pack('<BH', VND_CMD_SET_BLOCK_RATE, RATE_HZ)
        w = write_vendor(dev, payload, timeout_ms=500, label='SET_BLOCK_RATE')
        log(f"[HOST] SET_BLOCK_RATE {RATE_HZ} Hz -> {w} bytes")
        time.sleep(0.01)
    except Exception as e:
        log(f"[WARN] SET_BLOCK_RATE failed: {e}")
    # FULL mode
    try:
        w = write_vendor(dev, bytes([VND_CMD_SET_FULL_MODE, 0x01]), timeout_ms=500, label='SET_FULL_MODE')
        log(f"[HOST] SET_FULL_MODE -> {w} bytes")
        time.sleep(0.005)
    except Exception as e:
        log(f"[WARN] SET_FULL_MODE failed: {e}")
    # Профиль по умолчанию (2)
    try:
        w = write_vendor(dev, bytes([VND_CMD_SET_PROFILE, 0x02]), timeout_ms=500, label='SET_PROFILE')
        log(f"[HOST] SET_PROFILE(2) -> {w} bytes")
        time.sleep(0.005)
    except Exception as e:
        log(f"[WARN] SET_PROFILE failed: {e}")
    # async
    try:
        w = write_vendor(dev, bytes([VND_CMD_SET_ASYNC_MODE, 0x01 if ASYNC_MODE else 0x00]), timeout_ms=500, label='SET_ASYNC_MODE')
        log(f"[HOST] SET_ASYNC_MODE({ASYNC_MODE}) -> {w} bytes")
        time.sleep(0.005)
    except Exception as e:
        log(f"[WARN] SET_ASYNC_MODE failed: {e}")
    # chmode
    try:
        w = write_vendor(dev, bytes([VND_CMD_SET_CHMODE, CH_MODE & 0xFF]), timeout_ms=500, label='SET_CHMODE')
        log(f"[HOST] SET_CHMODE({CH_MODE}) -> {w} bytes")
        time.sleep(0.005)
    except Exception as e:
        log(f"[WARN] SET_CHMODE failed: {e}")

# --------- GUI и цикл чтения ---------

def run_gui():
    dev = open_device()
    configure_stream(dev)
    # START (если разрешено)
    if not args.no_start:
        try:
            w = write_vendor(dev, bytes([VND_CMD_START]), timeout_ms=500, label='START')
            log(f"[HOST] START -> {w} bytes")
        except Exception as e:
            log(f"[ERR] START failed: {e}")

    # Буферы последних значений
    # Оценим количество сэмплов/с — на канал ~ rate_hz * samples_per_frame; точного нет до фиксации
    # Возьмём запас 100k точек на канал
    max_points = 100_000
    if np is not None:
        bufA = np.zeros(max_points, dtype=np.int16)
        bufB = np.zeros(max_points, dtype=np.int16)
    else:
        bufA = [0]*max_points
        bufB = [0]*max_points
    idxA = 0
    idxB = 0

    plt.figure("Vendor USB Oscilloscope")
    ax = plt.gca()
    ax.set_title("Каналы A (синий) и B (оранжевый)")
    ax.set_xlabel("Индекс выборки (скользящее окно)")
    ax.set_ylabel("Сырой код (int16)")
    # Линии
    if np is not None:
        x = np.arange(max_points)
        lineA, = ax.plot(x, bufA, 'b-', lw=0.8, label='A')
        lineB, = ax.plot(x, bufB, 'C1-', lw=0.8, label='B')
    else:
        lineA, = ax.plot(range(max_points), bufA, 'b-', lw=0.8, label='A')
        lineB, = ax.plot(range(max_points), bufB, 'C1-', lw=0.8, label='B')
    ax.legend(loc='upper right')
    plt.tight_layout()

    # Чтение и обновление графика
    rx = bytearray()
    last_draw = time.time()
    try:
        while plt.fignum_exists(plt.gcf().number):
            # читать куски по 512 байт
            try:
                chunk = bytes(dev.read(IN_EP, 512, timeout=READ_TIMEOUT_MS))
                rx += chunk
            except usb.core.USBError as e:
                if _is_timeout(e):
                    # просто пропуск обновления
                    pass
                else:
                    log(f"[HOST][RX][ERR] {e}")
                    time.sleep(0.01)
                    continue
            # попытка собрать кадры
            frames, rx = extract_frames(rx)
            for fr in frames:
                if fr.is_test:
                    continue
                if fr.flags == 0x01 and CH_MODE in (0,2):
                    # Добавить в буфер A
                    if np is not None:
                        ns = len(fr.samples)
                        # кольцевая запись
                        end = idxA + ns
                        if end <= max_points:
                            bufA[idxA:end] = fr.samples
                        else:
                            k = max_points - idxA
                            bufA[idxA:] = fr.samples[:k]
                            bufA[:ns-k] = fr.samples[k:]
                        idxA = (idxA + ns) % max_points
                    else:
                        for s in fr.samples:
                            bufA[idxA] = int(s)
                            idxA += 1
                            if idxA >= max_points:
                                idxA = 0
                elif fr.flags == 0x02 and CH_MODE in (1,2):
                    # Добавить в буфер B
                    if np is not None:
                        ns = len(fr.samples)
                        end = idxB + ns
                        if end <= max_points:
                            bufB[idxB:end] = fr.samples
                        else:
                            k = max_points - idxB
                            bufB[idxB:] = fr.samples[:k]
                            bufB[:ns-k] = fr.samples[k:]
                        idxB = (idxB + ns) % max_points
                    else:
                        for s in fr.samples:
                            bufB[idxB] = int(s)
                            idxB += 1
                            if idxB >= max_points:
                                idxB = 0
            # периодически перерисовывать (~25 FPS)
            now = time.time()
            if now - last_draw > 0.04:
                if np is not None:
                    lineA.set_ydata(bufA)
                    lineB.set_ydata(bufB)
                else:
                    lineA.set_ydata(bufA)
                    lineB.set_ydata(bufB)
                ax.relim(); ax.autoscale_view()
                plt.pause(0.001)
                last_draw = now
    except KeyboardInterrupt:
        pass
    finally:
        # Отправим STOP для корректного завершения
        try:
            dev.write(OUT_EP, bytes([VND_CMD_STOP]), timeout=500)
            log("[HOST] STOP written")
        except Exception:
            pass
        try:
            usb.util.release_interface(dev, IFACE_INDEX)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass


if __name__ == '__main__':
    run_gui()
