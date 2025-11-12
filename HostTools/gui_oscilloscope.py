#!/usr/bin/env python3
# Simple USB oscilloscope for BMI30 vendor stream
# Dependencies: pyusb, matplotlib (tkinter backend)

import sys, time, struct, threading, queue, argparse
from typing import Optional
import usb.core, usb.util

try:
    import matplotlib
    matplotlib.use('TkAgg')  # safest default on Windows
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
except Exception as e:
    print("[ERROR] matplotlib not available:", e)
    sys.exit(1)

VENDOR=0xCAFE
PRODUCT=0x4001
INTERFACE=2
EP_OUT=0x03
EP_IN=0x83
g_status = None  # глобальный статус GUI для логгера/ридера

# Commands
CMD_START=0x20
CMD_STOP=0x21
CMD_SET_WINDOWS=0x10
CMD_SET_BLOCK_HZ=0x11
CMD_SET_TRUNC_SAMPLES=0x16
CMD_SET_FRAME_SAMPLES=0x17
CMD_SET_FULL_MODE=0x13
CMD_SET_PROFILE=0x14
CMD_SET_CHMODE=0x19  # 0=A-only, 1=B-only, 2=both
CMD_SET_ASYNC_MODE=0x18

HDR_SIZE=32

def le16(x:int):
    return [x & 0xFF, (x >> 8) & 0xFF]

def find_dev():
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
    if dev is None:
        raise SystemExit("Device not found")
    # Windows safe guards
    try:
        if hasattr(dev, 'is_kernel_driver_active') and dev.is_kernel_driver_active(INTERFACE):
            try:
                dev.detach_kernel_driver(INTERFACE)
            except Exception:
                pass
    except Exception:
        pass
    dev.set_configuration()
    try:
        usb.util.claim_interface(dev, INTERFACE)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
    except Exception:
        pass
    return dev

def send_cmd(dev, data: bytes):
    dev.write(EP_OUT, data, timeout=1000)

def parse_hdr(b: bytes):
    if len(b) < HDR_SIZE:
        return None
    magic, ver, flags, seq, ts, ns, zc = struct.unpack_from('<HBBIIHH', b, 0)
    return {
        'magic': magic,
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'ts': ts,
        'ns': ns,
    }

def reader_thread(dev, out_q: queue.Queue, stop_ev: threading.Event):
    """
    Надёжный ридер: собираем полные кадры из 512-байтных кусочков (WinUSB/libusb).
    Выдаём в очередь только полностью собранные кадры A/B (h, payload_full).
    """
    global g_status
    rx = bytearray()
    last_pkt_len = None
    pkt_count = 0
    while not stop_ev.is_set():
        try:
            # Чтение «сырых» кусков, обычно 512B; размер 512 даёт предсказуемое поведение на WinUSB
            chunk = dev.read(EP_IN, 512, timeout=1000)
            pkt_count += 1
            if pkt_count % 10 == 0 or len(chunk) != last_pkt_len:
                print(f"[PKT] #{pkt_count} len={len(chunk)} bytes (frame: 32+ns*2; ns=1360 => 2752)")
            last_pkt_len = len(chunk)
            rx += bytes(chunk)
            if g_status is not None:
                g_status.on_read_ok()
        except usb.core.USBError as e:
            if getattr(e, 'errno', None) in (110, 10060) or 'timed out' in str(e).lower():
                if g_status is not None:
                    g_status.on_timeout()
                continue
            else:
                if g_status is not None:
                    g_status.on_error(repr(e))
                continue
        except Exception as e:
            if g_status is not None:
                g_status.on_error(repr(e))
            continue

        # Пытаемся извлечь из буфера одно или несколько полных кадров
        while True:
            if len(rx) < 4:
                break
            # Пропускаем STAT-кадры (64B или 52B), они GUI не нужны
            if rx[0:4] == b'STAT':
                if len(rx) >= 64:
                    rx = rx[64:]
                    continue
                elif len(rx) >= 52:
                    rx = rx[52:]
                    continue
                else:
                    break  # ждём добор байтов
            # Заголовок кадра?
            if rx[0] == 0x5A and rx[1] == 0xA5 and rx[2] == 0x01 and len(rx) >= 16:
                total_samples = rx[12] | (rx[13] << 8)
                frame_len = 32 + total_samples * 2
                if len(rx) < frame_len:
                    break  # ждём остаток кадра
                frame = bytes(rx[:frame_len])
                rx = rx[frame_len:]
                # Распарсим заголовок и положим полный payload
                h = parse_hdr(frame)
                if h and h.get('magic') == 0xA55A:
                    payload = frame[HDR_SIZE:HDR_SIZE + h['ns']*2]
                    if g_status is not None:
                        g_status.on_frame_header(h)
                    out_q.put((h, payload))
                continue
            # Ресинхронизация — ищем ближайший STAT или хедер
            idx_stat = rx.find(b'STAT')
            idx_hdr = rx.find(b"\x5A\xA5\x01")
            idx = -1
            if idx_stat != -1 and (idx_hdr == -1 or idx_stat < idx_hdr):
                idx = idx_stat
            elif idx_hdr != -1:
                idx = idx_hdr
            if idx > 0:
                rx = rx[idx:]
                continue
            break

class GuiStatus:
    def __init__(self, q: queue.Queue, ns: int):
        self._lock = threading.Lock()
        self.start_ts = time.time()
        self.last_hdr_ts: float = 0.0
        self.last_pair_ts: float = 0.0
        self.last_seq: Optional[int] = None
        self.a_count = 0
        self.b_count = 0
        self.pairs = 0
        self.q = q
        self.q_max = q.maxsize if hasattr(q, 'maxsize') else 0
        self.timeouts_total = 0
        self.timeouts_consec = 0
        self.last_error: str = ''
        self.rate_med: float = 0.0
        self.ns = ns

    def on_timeout(self):
        with self._lock:
            self.timeouts_total += 1
            self.timeouts_consec += 1

    def on_read_ok(self):
        with self._lock:
            self.timeouts_consec = 0

    def on_error(self, msg: str):
        with self._lock:
            self.last_error = msg

    def on_frame_header(self, h: dict):
        with self._lock:
            self.last_hdr_ts = time.time()
            self.last_seq = h.get('seq', self.last_seq)
            fl = h.get('flags', 0)
            if fl == 0x01:
                self.a_count += 1
            elif fl == 0x02:
                self.b_count += 1

    def on_pair_done(self, rate_med: float, seq: int):
        with self._lock:
            self.pairs += 1
            self.last_pair_ts = time.time()
            self.rate_med = rate_med
            self.last_seq = seq

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            return {
                'uptime': now - self.start_ts,
                'since_last_hdr': (now - self.last_hdr_ts) if self.last_hdr_ts else None,
                'since_last_pair': (now - self.last_pair_ts) if self.last_pair_ts else None,
                'last_seq': self.last_seq,
                'pairs': self.pairs,
                'a_count': self.a_count,
                'b_count': self.b_count,
                'qsize': self.q.qsize() if self.q else 0,
                'qmax': self.q_max,
                'timeouts': self.timeouts_total,
                'timeouts_consec': self.timeouts_consec,
                'last_error': self.last_error,
                'rate_med': self.rate_med,
                'ns': self.ns,
            }

def status_logger_thread(stop_ev: threading.Event):
    # Печатаем состояние раз в 1 секунду
    global g_status
    while not stop_ev.is_set():
        time.sleep(1.0)
        if g_status is None:
            continue
        snap = g_status.snapshot()
        uptime = int(snap['uptime'])
        seq = snap['last_seq']
        pairs = snap['pairs']
        rate = snap['rate_med']
        qsize = snap['qsize']
        qmax = snap['qmax']
        tott = snap['timeouts']
        consec = snap['timeouts_consec']
        slp = snap['since_last_pair']
        err = snap['last_error']
        st = f"[GUI] t={uptime}s pairs={pairs} seq={seq} rate≈{rate:.2f}Hz q={qsize}/{qmax} timeouts={tott} consec={consec}"
        if slp is not None:
            st += f" idle_pair={slp:.1f}s"
        if err:
            st += f" last_err={err}"
        print(st)

class LivePlot:
    def __init__(self, ns, single_channel: bool, ns_auto: bool = False):
        self.ns = ns
        self.single = single_channel
        self.ns_auto = ns_auto
        if self.single:
            self.fig, ax = plt.subplots(1, 1, figsize=(9,4))
            self.ax0 = ax
            self.ax1 = None
            self.line0, = self.ax0.plot([], [], lw=1, color='tab:blue')
            self.line1 = None
            self.ax0.set_title('Channel A')
            self.ax0.set_xlabel('Sample index')
            axes = (self.ax0,)
        else:
            self.fig, (self.ax0, self.ax1) = plt.subplots(2, 1, figsize=(9,6), sharex=True)
            self.line0, = self.ax0.plot([], [], lw=1, color='tab:blue')
            self.line1, = self.ax1.plot([], [], lw=1, color='tab:orange')
            self.ax0.set_title('Channel A')
            self.ax1.set_title('Channel B')
            self.ax1.set_xlabel('Sample index')
            axes = (self.ax0, self.ax1)
        for ax in axes:
            ax.set_xlim(0, ns)
            ax.set_ylim(0, 4095)
            ax.grid(True, alpha=0.3)
        self.last_pairs = 0
        self.last_ts = None
        self.rate_text = self.fig.text(0.02, 0.95, '', fontsize=10)
        self.ns_text = self.fig.text(0.70, 0.95, '', fontsize=10)

    def update(self, frame):
        # frame contains (rate_med, seq, a_vals, b_vals or None)
        rate_med, seq, a_vals, b_vals = frame
        xs = list(range(len(a_vals)))
        self.line0.set_data(xs, a_vals)
        if self.line1 is not None and b_vals is not None:
            self.line1.set_data(xs, b_vals)
        n = len(a_vals)
        # Авто-ось X по фактическому числу сэмплов, если включён авто-режим
        if self.ns_auto and n > 0:
            for ax in (self.ax0,) if self.ax1 is None else (self.ax0, self.ax1):
                xmin, xmax = ax.get_xlim()
                if int(xmax) != n:
                    ax.set_xlim(0, n)
        # Оверлей с ns и каналами
        ch = 'A' if self.single else ('A+B' if b_vals is not None else 'A+…')
        self.rate_text.set_text(f"seq={seq} | rate≈{rate_med:.2f} Hz")
        self.ns_text.set_text(f"ns={n} ({'auto' if self.ns_auto else 'fixed'}) | ch={ch}")
        if self.line1 is not None:
            return self.line0, self.line1, self.rate_text, self.ns_text
        else:
            return self.line0, self.rate_text, self.ns_text

def main():
    ap = argparse.ArgumentParser()
    # ns=0 => не ограничивать и не отправлять CMD_SET_FRAME_SAMPLES (использовать размер кадра устройства)
    ap.add_argument('--ns', type=int, default=0)
    ap.add_argument('--profile', type=int, default=1)
    ap.add_argument('--pairs', type=int, default=400, help='pairs for rate median window')
    ap.add_argument('--single', action='store_true', help='Single-channel mode (A-only)')
    args = ap.parse_args()

    dev = find_dev()

    # CRITICAL: STOP first to clear any locked cur_samples_per_frame
    try:
        send_cmd(dev, bytes([CMD_STOP]))
        time.sleep(0.2)
    except Exception:
        pass
    
    # Configure stream settings AFTER STOP to ensure clean state
    # NOTE: In FULL mode, SET_WINDOWS is NOT used - ADC profile determines size
    # Отправляем SET_FRAME_SAMPLES только если явно задано ns>0; иначе не ограничиваем устройство
    if args.ns and args.ns > 0:
        send_cmd(dev, bytes([CMD_SET_FRAME_SAMPLES] + le16(args.ns)))
    send_cmd(dev, bytes([CMD_SET_FULL_MODE, 1]))
    send_cmd(dev, bytes([CMD_SET_PROFILE, args.profile]))
    
    # CRITICAL: Wait for profile switch to complete and ADC to reconfigure
    time.sleep(0.3)
    
    # Выбор режимов каналов: A-only при --single, иначе оба канала
    try:
        send_cmd(dev, bytes([CMD_SET_CHMODE, 0x00 if args.single else 0x02]))
    except Exception:
        pass
    # Включим асинхронный режим A/B (независимые потоки) — устойчивее для визуализации
    try:
        send_cmd(dev, bytes([CMD_SET_ASYNC_MODE, 0x01]))
    except Exception:
        pass
    # Подскажем устройству целевую частоту блоков (для LCD/диагностики), фактическая задаётся профилем
    try:
        send_cmd(dev, bytes([CMD_SET_BLOCK_HZ] + le16(200 if args.profile == 1 else 300)))
    except Exception:
        pass
    
    # Small delay before START to ensure config is applied
    time.sleep(0.1)
    send_cmd(dev, bytes([CMD_START]))

    q = queue.Queue(maxsize=1000)
    stop_ev = threading.Event()
    # Глобальный статус и логгер
    global g_status
    g_status = GuiStatus(q, args.ns)
    t = threading.Thread(target=reader_thread, args=(dev,q,stop_ev), daemon=True)
    t.start()
    tlog = threading.Thread(target=status_logger_thread, args=(stop_ev,), daemon=True)
    tlog.start()

    plot = LivePlot(args.ns if args.ns>0 else 1360, args.single, ns_auto=(args.ns<=0))

    # generator of frames for animation
    def gen():
        ts_list = []
        last_seq = None
        a_buf = None
        a_seq = None
        last_a_ts = None
        while True:
            # collect until we get A then B of same seq
            try:
                while True:
                    h, p = q.get(timeout=1.0)
                    if h['flags'] == 0x01:  # A
                        if args.single:
                            # Оцениваем частоту по разнице меток времени A→A
                            if last_a_ts is not None:
                                ts_list.append(h['ts'])
                                if len(ts_list) > args.pairs:
                                    ts_list.pop(0)
                            last_a_ts = h['ts']
                            # median rate
                            if len(ts_list) >= 2:
                                dts = [ts_list[i+1]-ts_list[i] for i in range(len(ts_list)-1)]
                                dts = [(dt + (1<<32)) if dt < 0 else dt for dt in dts]
                                md = sorted(dts)[len(dts)//2]
                                rate_med = 1000.0/md if md>0 else 0.0
                            else:
                                rate_med = 0.0
                            # Показываем min(запрошенный ns, hdr ns); если ns<=0 — используем hdr ns
                            hdr_ns = h['ns']
                            ns_limit = args.ns if args.ns and args.ns>0 else hdr_ns
                            ns = min(ns_limit, len(p)//2)
                            a_vals = [p[2*i] | (p[2*i+1]<<8) for i in range(ns)]
                            if g_status is not None:
                                g_status.on_pair_done(rate_med, h['seq'])
                            yield (rate_med, h['seq'], a_vals, None)
                            break
                        else:
                            last_seq = h['seq']
                            a_seq = h['seq']
                            a_buf = p
                    elif not args.single and h['flags'] == 0x02 and a_buf is not None:
                        # got B (pair with the latest A regardless of exact seq; device seq often alternates A/B)
                        # rate measurement by device timestamp (A-only)
                        ts_list.append(h['ts'])
                        if len(ts_list) > args.pairs:
                            ts_list.pop(0)
                        # median rate
                        dts = [ts_list[i+1]-ts_list[i] for i in range(len(ts_list)-1)]
                        dts = [(dt + (1<<32)) if dt < 0 else dt for dt in dts]
                        if dts:
                            md = sorted(dts)[len(dts)//2]
                            rate_med = 1000.0/md if md>0 else 0.0
                        else:
                            rate_med = 0.0
                        # unpack a,b as u16 LE
                        hdr_ns = h['ns']
                        ns_limit = args.ns if args.ns and args.ns>0 else hdr_ns
                        ns = min(ns_limit, (len(a_buf)//2) if a_buf is not None else 0, len(p)//2)
                        a_vals = [a_buf[2*i] | (a_buf[2*i+1]<<8) for i in range(ns)] if a_buf is not None else []
                        b_vals = [p[2*i] | (p[2*i+1]<<8) for i in range(ns)]
                        # обновим статус по завершённой паре
                        if g_status is not None:
                            g_status.on_pair_done(rate_med, a_seq if a_seq is not None else h['seq'])
                        yield (rate_med, a_seq if a_seq is not None else h['seq'], a_vals, b_vals)
                        a_buf = None
                        a_seq = None
                        break
            except queue.Empty:
                continue

    ani = animation.FuncAnimation(plot.fig, plot.update, gen(), interval=50, blit=False)

    def on_close(evt):
        stop_ev.set()
        try:
            send_cmd(dev, bytes([CMD_STOP]))
        except Exception:
            pass
        usb.util.release_interface(dev, INTERFACE)
        # Удалён вызов attach_kernel_driver: на целевой Windows среде WinUSB не поддерживает повторное прикрепление.

    plot.fig.canvas.mpl_connect('close_event', on_close)
    plt.tight_layout()
    plt.show()

if __name__ == '__main__':
    main()
