#!/usr/bin/env python3
# Simple USB oscilloscope for BMI30 vendor stream
# Dependencies: pyusb, matplotlib (tkinter backend)

import sys, time, struct, threading, queue, argparse
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

# Commands
CMD_START=0x20
CMD_STOP=0x21
CMD_SET_WINDOWS=0x10
CMD_SET_BLOCK_HZ=0x11
CMD_SET_TRUNC_SAMPLES=0x16
CMD_SET_FRAME_SAMPLES=0x17
CMD_SET_FULL_MODE=0x13
CMD_SET_PROFILE=0x14

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
    last_seq = None
    while not stop_ev.is_set():
        try:
            data = dev.read(EP_IN, 4096, timeout=1000)
        except Exception:
            continue
        b = bytes(data)
        h = parse_hdr(b)
        if not h or h['magic'] != 0xA55A:
            continue
        payload = b[HDR_SIZE:HDR_SIZE + h['ns']*2]
        # push only A/B pairs in order
        out_q.put((h, payload))

class LivePlot:
    def __init__(self, ns):
        self.ns = ns
        self.fig, (self.ax0, self.ax1) = plt.subplots(2, 1, figsize=(9,6), sharex=True)
        self.line0, = self.ax0.plot([], [], lw=1, color='tab:blue')
        self.line1, = self.ax1.plot([], [], lw=1, color='tab:orange')
        self.ax0.set_title('Channel A')
        self.ax1.set_title('Channel B')
        self.ax1.set_xlabel('Sample index')
        for ax in (self.ax0, self.ax1):
            ax.set_xlim(0, ns)
            ax.set_ylim(0, ns+10)
            ax.grid(True, alpha=0.3)
        self.last_pairs = 0
        self.last_ts = None
        self.rate_text = self.fig.text(0.02, 0.95, '', fontsize=10)

    def update(self, frame):
        # frame contains (rate_med, seq, a_vals, b_vals)
        rate_med, seq, a_vals, b_vals = frame
        xs = list(range(len(a_vals)))
        self.line0.set_data(xs, a_vals)
        self.line1.set_data(xs, b_vals)
        self.rate_text.set_text(f"seq={seq} | block rate≈{rate_med:.2f} Hz (median)")
        return self.line0, self.line1, self.rate_text

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ns', type=int, default=300)
    ap.add_argument('--profile', type=int, default=2)
    ap.add_argument('--pairs', type=int, default=400, help='pairs for rate median window')
    args = ap.parse_args()

    dev = find_dev()

    # Configure stream
    win_payload = [CMD_SET_WINDOWS] + le16(100) + le16(args.ns) + le16(700) + le16(args.ns)
    send_cmd(dev, bytes(win_payload))
    send_cmd(dev, bytes([CMD_SET_FRAME_SAMPLES] + le16(args.ns)))
    send_cmd(dev, bytes([CMD_SET_FULL_MODE, 1]))
    send_cmd(dev, bytes([CMD_SET_PROFILE, args.profile]))
    send_cmd(dev, bytes([CMD_START]))

    q = queue.Queue(maxsize=1000)
    stop_ev = threading.Event()
    t = threading.Thread(target=reader_thread, args=(dev,q,stop_ev), daemon=True)
    t.start()

    plot = LivePlot(args.ns)

    # generator of frames for animation
    def gen():
        ts_list = []
        last_seq = None
        a_buf = None
        while True:
            # collect until we get A then B of same seq
            try:
                while True:
                    h, p = q.get(timeout=1.0)
                    if h['flags'] == 0x01:  # A
                        last_seq = h['seq']
                        a_buf = p
                    elif h['flags'] == 0x02 and last_seq is not None and h['seq'] == last_seq:
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
