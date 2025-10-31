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
    # Перенесём логику блоков в статус
    global g_status
    last_seq = None
    while not stop_ev.is_set():
        try:
            data = dev.read(EP_IN, 4096, timeout=1000)
            # Успешное чтение — сбросим счётчик подряд идущих таймаутов
            if g_status is not None:
                g_status.on_read_ok()
        except usb.core.USBError as e:
            # errno 110 = timeout
            if getattr(e, 'errno', None) == 110:
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
        b = bytes(data)
        h = parse_hdr(b)
        if not h or h['magic'] != 0xA55A:
            continue
        payload = b[HDR_SIZE:HDR_SIZE + h['ns']*2]
        # Обновим статус и поставим в очередь
        if g_status is not None:
            g_status.on_frame_header(h)
        out_q.put((h, payload))

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
    def __init__(self, ns, single_channel: bool):
        self.ns = ns
        self.single = single_channel
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

    def update(self, frame):
        # frame contains (rate_med, seq, a_vals, b_vals or None)
        rate_med, seq, a_vals, b_vals = frame
        xs = list(range(len(a_vals)))
        self.line0.set_data(xs, a_vals)
        if self.line1 is not None and b_vals is not None:
            self.line1.set_data(xs, b_vals)
        self.rate_text.set_text(f"seq={seq} | block rate≈{rate_med:.2f} Hz (median)")
        if self.line1 is not None:
            return self.line0, self.line1, self.rate_text
        else:
            return self.line0, self.rate_text

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ns', type=int, default=300)
    ap.add_argument('--profile', type=int, default=1)
    ap.add_argument('--pairs', type=int, default=400, help='pairs for rate median window')
    ap.add_argument('--single', action='store_true', help='Single-channel mode (A-only)')
    args = ap.parse_args()

    dev = find_dev()

    # Configure stream
    win_payload = [CMD_SET_WINDOWS] + le16(100) + le16(args.ns) + le16(700) + le16(args.ns)
    send_cmd(dev, bytes(win_payload))
    send_cmd(dev, bytes([CMD_SET_FRAME_SAMPLES] + le16(args.ns)))
    send_cmd(dev, bytes([CMD_SET_FULL_MODE, 1]))
    send_cmd(dev, bytes([CMD_SET_PROFILE, args.profile]))
    # Выбор режимов каналов: по умолчанию — один канал A для стабилизации
    try:
        send_cmd(dev, bytes([CMD_SET_CHMODE, 0x00 if args.single else 0x02]))
    except Exception:
        pass
    # Подскажем устройству целевую частоту блоков (для LCD/диагностики), фактическая задаётся профилем
    try:
        send_cmd(dev, bytes([CMD_SET_BLOCK_HZ] + le16(200 if args.profile == 1 else 300)))
    except Exception:
        pass
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

    plot = LivePlot(args.ns, args.single)

    # generator of frames for animation
    def gen():
        ts_list = []
        last_seq = None
        a_buf = None
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
                            ns = min(args.ns, len(p)//2)
                            a_vals = [p[2*i] | (p[2*i+1]<<8) for i in range(ns)]
                            if g_status is not None:
                                g_status.on_pair_done(rate_med, h['seq'])
                            yield (rate_med, h['seq'], a_vals, None)
                            break
                        else:
                            last_seq = h['seq']
                            a_buf = p
                    elif not args.single and h['flags'] == 0x02 and last_seq is not None and h['seq'] == last_seq:
                        # got B for same seq
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
                        ns = min(args.ns, len(a_buf)//2, len(p)//2)
                        a_vals = [a_buf[2*i] | (a_buf[2*i+1]<<8) for i in range(ns)]
                        b_vals = [p[2*i] | (p[2*i+1]<<8) for i in range(ns)]
                        # обновим статус по завершённой паре
                        if g_status is not None:
                            g_status.on_pair_done(rate_med, last_seq)
                        yield (rate_med, last_seq, a_vals, b_vals)
                        a_buf = None
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
        try:
            dev.attach_kernel_driver(INTERFACE)
        except Exception:
            pass

    plot.fig.canvas.mpl_connect('close_event', on_close)
    plt.tight_layout()
    plt.show()

if __name__ == '__main__':
    main()
