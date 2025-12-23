#!/usr/bin/env python3
# Simple USB oscilloscope for BMI30 vendor stream
# Dependencies: pyusb, matplotlib (tkinter backend)

import sys, time, struct, threading, queue, argparse, os
from typing import Optional
import usb.core, usb.util
from usb.core import Device  # type hint

def _mk_logger(log_path: str):
    state = {'f': None, 'path': log_path}
    if log_path:
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            state['f'] = open(log_path, 'a', encoding='utf-8')
        except Exception:
            state['f'] = None
    def _log(*args):
        msg = ' '.join(str(a) for a in args)
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{ts}] {msg}"
        print(line)
        f = state.get('f')
        if f:
            try:
                f.write(line + "\n")
                f.flush()
            except Exception:
                pass
    def _close():
        f = state.get('f')
        if f:
            try:
                f.close()
            except Exception:
                pass
    return _log, _close

_log = None  # type: ignore
_log_close = None  # type: ignore

def _init_matplotlib():
    global plt, animation
    try:
        import matplotlib  # type: ignore
        matplotlib.use('TkAgg')  # safest default on Windows
        import matplotlib.pyplot as plt_mod  # type: ignore
        import matplotlib.animation as animation_mod  # type: ignore
        plt = plt_mod
        animation = animation_mod
        return True
    except Exception as e:
        if _log:
            _log("[ERROR] matplotlib not available:", e)
        else:
            print("[ERROR] matplotlib not available:", e)
        return False

VENDOR=0xCAFE
PRODUCT=0x4001
INTERFACE=2  # vendor интерфейс (ожидается 3-й, нумерация с 0)
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
CMD_DEVICE_RESET=0x22
CMD_SOFT_RESET = 0x7E  # control OUT (no data)
CMD_DEEP_RESET = 0x7F  # control OUT (no data)
CMD_GET_STATUS=0x30
CMD_GET_STATUS_IMM=0x31

HDR_SIZE=16  # Реальный размер заголовка: magic(2) ver(1) flags(1) seq(4) ts(4) ns(2) zc(2)

def le16(x:int):
    return [x & 0xFF, (x >> 8) & 0xFF]

def _has_vendor_endpoints(dev: Device, alt: int) -> bool:
    """Проверка наличия EP_IN/EP_OUT в конкретном altsetting (cfg[(iface, alt)])."""
    try:
        cfg = dev.get_active_configuration()  # type: ignore[attr-defined]
        try:
            intf = cfg[(INTERFACE, alt)]  # type: ignore[index]
        except KeyError:
            return False
        eps = [ep.bEndpointAddress for ep in intf]  # type: ignore
        return (EP_IN in eps) and (EP_OUT in eps)
    except Exception:
        return False

def find_dev() -> Device:
    dev: Device = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)  # type: ignore
    if dev is None:
        raise SystemExit("Device not found")
    # WinUSB: безопасно вызвать set_configuration; драйвер ядра обычно уже отсоединён
    try:
        dev.set_configuration()  # type: ignore[attr-defined]
    except Exception:
        pass
    # Интерфейс может быть уже занят — игнорируем ошибки claim
    try:
        usb.util.claim_interface(dev, INTERFACE)
    except Exception:
        pass

    # Некоторые прошивки активируют поток в alt=0, другие в alt=1. Раньше был жёсткий alt=1.
    # Сделаем адаптивно: сначала пытаемся alt=1, проверяем endpoints; если отсутствуют — пробуем alt=0.
    alt_tried = []
    for alt in (1, 0):  # ожидаем stream в alt=1, fallback alt=0
        try:
            dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=alt)  # type: ignore[attr-defined]
            alt_tried.append(alt)
            time.sleep(0.05)
            if _has_vendor_endpoints(dev, alt):
                if _log:
                    _log(f"[USB] Using altsetting={alt} (endpoints OK)")
                return dev
            else:
                if _log:
                    _log(f"[USB] altsetting={alt} has no vendor endpoints -> fallback")
        except Exception as e:
            if _log:
                _log(f"[USB] set_interface_altsetting({alt}) failed: {e}")
            continue

    # Если оба не дали endpoints, всё равно возвращаем dev (ридер будет получать таймауты) и сообщаем.
    if _log:
        _log(f"[USB] Vendor endpoints not found after trying alt={alt_tried}; continuing with current configuration")
    return dev

class DevHandle:
    """Простой контейнер для совместного владения дескриптором устройства между потоками.
    Позволяет вотчдогу переоткрывать устройство, а ридеру — использовать актуальный handle.
    """
    def __init__(self, dev: Device):
        self.dev: Device = dev

def send_cmd(dev: Device, data: bytes, timeout_ms: int = 500):
    """Send command with short timeout, ignore Windows USB timeout errors"""
    try:
        dev.write(EP_OUT, data, timeout=timeout_ms)  # type: ignore[attr-defined]
    except usb.core.USBError as e:
        # Windows часто даёт timeout на control/bulk commands, но данные работают
        # Игнорируем timeout - команда может быть обработана даже если ack не пришел
        pass
    except Exception as e:
        # Ignore all send errors on Windows - bulk read may still work
        pass

def _wait_until_gone(timeout: float = 3.0, poll: float = 0.2) -> bool:
    """Ждём, пока устройство исчезнет с шины (реальный reset/reenum)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if usb.core.find(idVendor=VENDOR, idProduct=PRODUCT) is None:
            return True
        time.sleep(poll)
    return False

def _wait_until_present(timeout: float = 8.0, poll: float = 0.3) -> bool:
    """Ждём, пока устройство появится на шине после reset."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if usb.core.find(idVendor=VENDOR, idProduct=PRODUCT) is not None:
            return True
        time.sleep(poll)
    return False

def reset_device_and_reopen(no_reset: bool=False, retry_s: float=6.0) -> Device:
    """Отправляет DEVICE_RESET и переоткрывает устройство после подтверждённой ре-энумерации.

    Эскалация:
      1) SOFT (EP0, 0x7E) ➜ ждём исчезновения ➜ ждём появления
      2) DEEP (EP0, 0x7F) при отсутствии исчезновения
      3) Bulk DEVICE_RESET (0x22) как запасной вариант
      4) USB port reset (libusb dev.reset())
    """
    if no_reset:
        print("[RESET] Skipping reset (--no-reset-first)")
        return find_dev()
    try:
        dev = find_dev()
        # 1) SOFT reset через EP0
        print("[RESET] Sending SOFT_RESET (0x7E) via control OUT…")
        soft_ok = False
        try:
            dev.ctrl_transfer(0x40, CMD_SOFT_RESET, 0, 0, None, timeout=300)  # type: ignore[attr-defined]
            soft_ok = True
        except usb.core.USBError as e:
            # Windows: ignore timeout, может не поддерживаться
            if 'timeout' not in str(e).lower():
                print(f"[RESET] SOFT_RESET failed: {e}")
        except Exception as e:
            print(f"[RESET] SOFT_RESET failed: {e}")
        time.sleep(0.1)
        if soft_ok and _wait_until_gone(timeout=2.5):
            print("[RESET] Device disappeared (SOFT); waiting to reappear…")
            if not _wait_until_present(timeout=8.0):
                raise SystemExit("Device did not re-appear after SOFT reset")
            print("[RESET] Device re-appeared")
            return find_dev()

        # 2) DEEP reset через EP0
        print("[RESET] Escalate to DEEP_RESET (0x7F) via control OUT…")
        deep_ok = False
        try:
            dev.ctrl_transfer(0x40, CMD_DEEP_RESET, 0, 0, None, timeout=400)  # type: ignore[attr-defined]
            deep_ok = True
        except usb.core.USBError as e:
            if 'timeout' not in str(e).lower():
                print(f"[RESET] DEEP_RESET failed: {e}")
        except Exception as e:
            print(f"[RESET] DEEP_RESET failed: {e}")
        time.sleep(0.15)
        if deep_ok and _wait_until_gone(timeout=3.0):
            print("[RESET] Device disappeared (DEEP); waiting to reappear…")
            if not _wait_until_present(timeout=10.0):
                raise SystemExit("Device did not re-appear after DEEP reset")
            print("[RESET] Device re-appeared")
            return find_dev()

        # 3) Bulk DEVICE_RESET (если EP0 не сработал и устройство 'не пропало')
        print("[RESET] Try Bulk DEVICE_RESET (0x22)…")
        try:
            dev.write(EP_OUT, bytes([CMD_DEVICE_RESET]), timeout=600)  # type: ignore[attr-defined]
        except usb.core.USBError as e:
            if 'timeout' not in str(e).lower():
                print(f"[RESET] Bulk DEVICE_RESET failed: {e}")
        except Exception as e:
            print(f"[RESET] Bulk DEVICE_RESET failed: {e}")
        time.sleep(0.2)
        if _wait_until_gone(timeout=2.5):
            print("[RESET] Device disappeared (BULK); waiting to reappear…")
            if not _wait_until_present(timeout=8.0):
                raise SystemExit("Device did not re-appear after BULK reset")
            print("[RESET] Device re-appeared")
            return find_dev()

        # 4) Последняя мера — USB port reset (libusb)
        print("[RESET] Escalate to USB port reset (libusb dev.reset)…")
        try:
            usb.util.release_interface(dev, INTERFACE)
        except Exception:
            pass
        try:
            dev.reset()  # type: ignore[attr-defined]
        except Exception as e:
            print(f"[RESET] Port reset failed: {e}")
        # После порт-ресета тоже ожидаем исчезновение/появление, но некоторые драйверы не отдают устройство как 'gone'
        # Поэтому просто ждём появления до retry_s
        if not _wait_until_present(timeout=retry_s):
            raise SystemExit("Device did not re-appear after USB port reset")
        print("[RESET] Device present after port reset")
        return find_dev()
    except Exception as e:
        print(f"[WARN] reset_device_and_reopen failed: {e}")
        # Попробуем хотя бы работать с текущим состоянием
        return find_dev()

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

def reader_thread(handle: 'DevHandle', out_q: queue.Queue, stop_ev: threading.Event, pkt_log_interval_sec: float = 5.0):
    """
    Ридер с многопакетной буферизацией: ADC фрейм может занимать несколько USB пакетов.
    Собираем в rx буфер, ищем magic number, извлекаем полные фреймы.
    """
    global g_status
    pkt_count = 0
    last_pkt_log_ts = 0.0
    rx = bytearray()  # Накопительный буфер для сборки фреймов
    
    while not stop_ev.is_set():
        try:
            # Читаем большими блоками для производительности
            chunk = handle.dev.read(EP_IN, 4096, timeout=100)
            pkt_count += 1
            now = time.time()
            
            if pkt_log_interval_sec and pkt_log_interval_sec > 0:
                if (now - last_pkt_log_ts) >= pkt_log_interval_sec:
                    print(f"[PKT] #{pkt_count} len={len(chunk)} bytes")
                    last_pkt_log_ts = now
            
            if g_status is not None:
                g_status.on_read_ok()
            
            # Добавляем в буфер
            rx.extend(chunk)
            
            # Парсим все полные фреймы из rx
            while True:
                # Пропускаем мусор до magic number
                idx = -1
                for i in range(len(rx) - 1):
                    if rx[i] == 0x5A and rx[i+1] == 0xA5:
                        idx = i
                        break
                
                if idx > 0:
                    # Отбрасываем мусор перед magic
                    rx = rx[idx:]
                elif idx < 0:
                    # Нет magic number в буфере - оставляем последние 2 байта на случай разрыва
                    if len(rx) > 2:
                        rx = rx[-2:]
                    break
                
                # Пропускаем STAT frames (начинаются с 'STAT')
                if len(rx) >= 4 and rx[:4] == b'STAT':
                    # Ищем конец STAT фрейма (обычно фиксированного размера, но пропустим до следующего magic)
                    next_magic = -1
                    for i in range(4, len(rx)-1):
                        if rx[i] == 0x5A and rx[i+1] == 0xA5:
                            next_magic = i
                            break
                    if next_magic > 0:
                        rx = rx[next_magic:]
                        continue
                    else:
                        # STAT не завершён, ждём ещё данных
                        break
                
                # Проверяем что есть хотя бы заголовок
                if len(rx) < HDR_SIZE:
                    break
                
                # Парсим заголовок
                h = parse_hdr(bytes(rx))
                if not h or h.get('magic') != 0xA55A:
                    # Неверный magic - отбрасываем первый байт и ищем дальше
                    rx = rx[1:]
                    continue
                
                # Проверяем флаги TEST (0x80) - пропускаем
                flags = h.get('flags', 0)
                if flags & 0x80:
                    # TEST frame - ищем следующий magic
                    next_magic = -1
                    for i in range(2, len(rx)-1):
                        if rx[i] == 0x5A and rx[i+1] == 0xA5:
                            next_magic = i
                            break
                    if next_magic > 0:
                        rx = rx[next_magic:]
                        continue
                    else:
                        break
                
                # Вычисляем полный размер фрейма
                ns = h.get('ns', 0)
                payload_size = ns * 2
                full_size = HDR_SIZE + payload_size
                
                # Ждём пока весь фрейм накопится
                if len(rx) < full_size:
                    break
                
                # Извлекаем payload
                payload = bytes(rx[HDR_SIZE:full_size])
                
                # Debug: первые 5 фреймов
                if pkt_count < 2000:
                    ch = 'A' if (flags & 0x01) else ('B' if (flags & 0x02) else '?')
                    print(f"[FRAME] {ch} seq={h['seq']} ns={ns} payload_len={len(payload)} full_size={full_size}")
                
                # Отправляем в очередь
                if g_status is not None:
                    g_status.on_frame_header(h)
                
                try:
                    out_q.put_nowait((h, payload))
                except queue.Full:
                    # Queue full → drop oldest
                    try:
                        out_q.get_nowait()
                        out_q.put_nowait((h, payload))
                    except (queue.Empty, queue.Full):
                        pass
                
                # Удаляем обработанный фрейм из буфера
                rx = rx[full_size:]
                            
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
        # Display rate tracking
        self.display_count = 0
        self.display_start_ts = time.time()

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
    
    def on_display_frame(self):
        """Called each time GUI actually displays a frame (not every RX frame)"""
        with self._lock:
            self.display_count += 1

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            display_elapsed = now - self.display_start_ts
            display_rate = self.display_count / display_elapsed if display_elapsed > 0 else 0.0
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
                'display_rate': display_rate,
                'ns': self.ns,
            }

def status_logger_thread(stop_ev: threading.Event, interval_sec: float = 10.0):
    # Печатаем состояние раз в interval_sec секунд (уменьшаем нагрузку на терминал)
    global g_status
    while not stop_ev.is_set():
        time.sleep(max(0.2, interval_sec))
        if g_status is None:
            continue
        snap = g_status.snapshot()
        uptime = int(snap['uptime'])
        seq = snap['last_seq']
        pairs = snap['pairs']
        rate = snap['rate_med']
        disp_rate = snap['display_rate']
        qsize = snap['qsize']
        qmax = snap['qmax']
        tott = snap['timeouts']
        consec = snap['timeouts_consec']
        slp = snap['since_last_pair']
        err = snap['last_error']
        st = f"[GUI] t={uptime}s pairs={pairs} seq={seq} RX≈{rate:.1f}Hz Display≈{disp_rate:.1f}FPS q={qsize}/{qmax} TO={tott} consec={consec}"
        if slp is not None:
            st += f" idle_pair={slp:.1f}s"
        if err:
            st += f" last_err={err}"
        print(st)

def _reconfigure_and_start(handle: 'DevHandle', args):
    """Отправить минимальную конфигурацию и START после (пере)открытия."""
    # Базовый STOP для чистого состояния
    try:
        handle.dev.write(EP_OUT, bytes([CMD_STOP]), timeout=800)  # type: ignore[attr-defined]
        time.sleep(0.2)
    except Exception as e:
        if _log: _log("[WDG] STOP before reconfig failed:", e)
    # Полная конфигурация как при старте
    try:
        handle.dev.write(EP_OUT, bytes([CMD_SET_FULL_MODE, 1]), timeout=600)  # type: ignore[attr-defined]
    except Exception as e:
        if _log: _log("[WDG] SET_FULL_MODE failed:", e)
    try:
        handle.dev.write(EP_OUT, bytes([CMD_SET_PROFILE, args.profile]), timeout=600)  # type: ignore[attr-defined]
        time.sleep(0.3)
    except Exception as e:
        if _log: _log("[WDG] SET_PROFILE failed:", e)
    try:
        chmode = 0x00 if getattr(args, 'single', False) else 0x02
        handle.dev.write(EP_OUT, bytes([CMD_SET_CHMODE, chmode]), timeout=600)  # type: ignore[attr-defined]
    except Exception as e:
        if _log: _log("[WDG] SET_CHMODE failed:", e)
    try:
        handle.dev.write(EP_OUT, bytes([CMD_SET_ASYNC_MODE, 0x01]), timeout=600)  # type: ignore[attr-defined]
    except Exception as e:
        if _log: _log("[WDG] SET_ASYNC_MODE failed:", e)
    try:
        bhz = (200 if args.profile == 1 else 300)
        handle.dev.write(EP_OUT, bytes([CMD_SET_BLOCK_HZ] + le16(bhz)), timeout=600)  # type: ignore[attr-defined]
    except Exception as e:
        if _log: _log("[WDG] SET_BLOCK_HZ failed:", e)
    # ТОЛЬКО если пользователь явно указал ns>0
    try:
        if getattr(args, 'ns', 0) and args.ns > 0:
            handle.dev.write(EP_OUT, bytes([CMD_SET_FRAME_SAMPLES] + le16(args.ns)), timeout=600)  # type: ignore[attr-defined]
    except Exception as e:
        if _log: _log("[WDG] SET_FRAME_SAMPLES failed:", e)
    time.sleep(0.1)
    handle.dev.write(EP_OUT, bytes([CMD_START]), timeout=800)  # type: ignore[attr-defined]


def status_watchdog_thread(handle: 'DevHandle', stop_ev: threading.Event, idle_sec: float = 2.5, max_consec_timeouts: int = 8, args=None):
    """Простой вотчдог: если нет пар > idle_sec или подряд таймаутов слишком много —
    пробуем мягкий рестарт (STOP+START). При повторных срывах эскалируем до мягкого
    ресета по bulk (DEVICE_RESET 0x22) с последующим START.
    """
    last_restart = 0.0
    restart_count = 0
    hard_reset_cycles = 0
    while not stop_ev.is_set():
        time.sleep(0.5)
        if g_status is None:
            continue
        snap = g_status.snapshot()
        now = time.time()
        since_pair = snap.get('since_last_pair')
        consec = snap.get('timeouts_consec', 0)
        # Гистерезис: не чаще одного рестарта в 5 секунд
        if (since_pair is not None and since_pair > idle_sec) or (consec is not None and consec >= max_consec_timeouts):
            if (now - last_restart) < 5.0:
                continue
            try:
                restart_count += 1
                msg = f"[WDG] Restart stream: idle_pair={since_pair} consec_timeouts={consec}"
                if _log:
                    _log(msg)
                else:
                    print(msg)
                # Базовый перезапуск
                handle.dev.write(EP_OUT, bytes([CMD_STOP]), timeout=800)  # type: ignore[attr-defined]
                time.sleep(0.25)
                # Эскалация: раз в два срыва попробуем мягкий ресет прошивки (bulk 0x22)
                if restart_count >= 2:
                    if _log: _log("[WDG] Escalate: send DEVICE_RESET (0x22) before START…")
                    else: print("[WDG] Escalate: send DEVICE_RESET (0x22) before START…")
                    try:
                        handle.dev.write(EP_OUT, bytes([CMD_DEVICE_RESET]), timeout=800)  # type: ignore[attr-defined]
                        # дать прошивке восстановить USB стек
                        time.sleep(0.6)
                        # попытаться переоткрыть устройство после мягкого ресета
                        try:
                            try:
                                usb.util.release_interface(handle.dev, INTERFACE)
                            except Exception:
                                pass
                            if not _wait_until_present(timeout=8.0):
                                if _log: _log("[WDG] Wait present timeout after DEVICE_RESET")
                            handle.dev = find_dev()
                            if _log: _log("[WDG] Reopened after DEVICE_RESET")
                        except Exception as e:
                            if _log: _log(f"[WDG] Reopen after DEVICE_RESET failed: {e}")
                    except Exception as e:
                        if _log: _log(f"[WDG] DEVICE_RESET failed: {e}")
                    # сбросить счётчик, чтобы не спамить ресетами
                    restart_count = 0
                # После любой перезапуск-конфигурируем и стартуем
                try:
                    _reconfigure_and_start(handle, args)
                except Exception as e:
                    if _log: _log("[WDG] reconfigure/start failed:", e)
                last_restart = now
            except Exception as e:
                if _log: _log(f"[WDG] Restart failed: {e}")
                last_restart = now

def status_probe_thread(handle: 'DevHandle', stop_ev: threading.Event, base_interval: float = 1.0):
    """Пробник статуса: бережно дёргаем GET_STATUS (EP0), чтобы прошивка печатала STALL_WARN
    и мы могли классифицировать зависание. Усиливаем частоту опроса, когда видим простои.
    """
    while not stop_ev.is_set():
        time.sleep(base_interval)
        if g_status is None:
            continue
        snap = g_status.snapshot()
        slp = snap.get('since_last_pair') or 0.0
        consec = snap.get('timeouts_consec') or 0
        try:
            # Условия для запроса статуса: явный простой >0.5с или хотя бы один таймаут подряд
            if (slp and slp > 0.5) or (consec and consec >= 1):
                # Сначала пробуем через bulk OUT — это активирует классификацию STALL в прошивке
                try:
                    handle.dev.write(EP_OUT, bytes([CMD_GET_STATUS]), timeout=200)  # type: ignore[attr-defined]
                    if _log:
                        _log("[DIAG] GET_STATUS queued via BULK (idle/timeout)")
                except Exception:
                    # Fallback на EP0 (ctrl IN), если bulk недоступен
                    try:
                        handle.dev.ctrl_transfer(0xC0, CMD_GET_STATUS, 0, 0, 64, timeout=300)  # type: ignore[attr-defined]
                        if _log:
                            _log("[DIAG] GET_STATUS requested via CTRL (bulk failed)")
                    except Exception:
                        pass
        except Exception:
            pass

class LivePlot:
    def __init__(self, ns, single_channel: bool, ns_auto: bool = False, trigger_level: int = 0):
        self.ns = ns
        self.single = single_channel
        self.ns_auto = ns_auto
        self.trigger_level = trigger_level  # 0 = disabled, >0 = ADC threshold for rising edge
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

    def _find_trigger_edge(self, vals):
        """Find rising edge position in data. Returns index or -1 if not found."""
        if self.trigger_level <= 0 or len(vals) < 2:
            return -1
        for i in range(len(vals) - 1):
            if vals[i] < self.trigger_level and vals[i+1] >= self.trigger_level:
                return i
        return -1

    def update(self, frame):
        # frame contains (rate_med, seq, a_vals, b_vals or None)
        rate_med, seq, a_vals, b_vals = frame
        
        # Apply trigger if enabled
        trigger_offset = 0
        if self.trigger_level > 0 and len(a_vals) > 0:
            edge_idx = self._find_trigger_edge(a_vals)
            if edge_idx >= 0:
                trigger_offset = edge_idx
                # Shift data so edge is at position 0
                a_vals = a_vals[trigger_offset:]
                if b_vals is not None and len(b_vals) > trigger_offset:
                    b_vals = b_vals[trigger_offset:]
        
        # Handle empty A-channel (dual mode: B arrived without A)
        if len(a_vals) == 0 and b_vals is not None and len(b_vals) > 0:
            # Show B data on both channels (or skip A update)
            xs_b = list(range(len(b_vals)))
            self.line0.set_data([], [])  # Clear A
            if self.line1 is not None:
                self.line1.set_data(xs_b, b_vals)
            n = len(b_vals)
        else:
            # Normal path: A has data
            xs = list(range(len(a_vals)))
            self.line0.set_data(xs, a_vals)
            if self.line1 is not None and b_vals is not None:
                xs_b = list(range(len(b_vals)))
                self.line1.set_data(xs_b, b_vals)
            n = len(a_vals)
        # Авто-ось X по фактическому числу сэмплов, если включён авто-режим
        if self.ns_auto and n > 0:
            for ax in (self.ax0,) if self.ax1 is None else (self.ax0, self.ax1):
                xmin, xmax = ax.get_xlim()
                if int(xmax) != n:
                    ax.set_xlim(0, n)
        # Оверлей с ns и каналами
        ch = 'A' if self.single else ('A+B' if b_vals is not None else 'A+…')
        # Show both RX rate (from device) and Display rate (GUI FPS)
        disp_rate = g_status.snapshot()['display_rate'] if g_status else 0.0
        self.rate_text.set_text(f"seq={seq} | RX≈{rate_med:.1f} Hz | Display≈{disp_rate:.1f} FPS")
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
    ap.add_argument('--no-reset-first', action='store_true', help='Skip initial device reset')
    ap.add_argument('--watchdog', action='store_true', help='Enable simple stream watchdog (STOP+START on idle)')
    ap.add_argument('--wdg-idle-sec', type=float, default=2.5, help='Watchdog idle seconds before restart')
    ap.add_argument('--wdg-timeouts', type=int, default=8, help='Consecutive timeouts threshold for restart')
    ap.add_argument('--log', type=str, default=os.path.join(os.path.dirname(__file__), 'gui_oscilloscope.log'), help='Path to log file')
    ap.add_argument('--status-log-interval', type=float, default=10.0, help='Seconds between GUI status lines')
    ap.add_argument('--pkt-log-interval', type=float, default=0.0, help='Seconds between [PKT] log lines (<=0 to disable)')
    ap.add_argument('--fps', type=int, default=20, help='Target GUI refresh FPS (display only, device RX @ 200Hz)')
    ap.add_argument('--trigger', type=int, default=0, help='Trigger level for edge sync (0=disabled, >0=threshold ADC value)')
    args = ap.parse_args()

    # init logger early
    global _log, _log_close
    _log, _log_close = _mk_logger(args.log)
    _log("[START] gui_oscilloscope.py", sys.executable, sys.version.split()[0], "args:", vars(args))

    # init matplotlib backend
    if not _init_matplotlib():
        if _log_close:
            _log_close()
        sys.exit(1)

    # Начальный сброс устройства для повышения стабильности
    _log("[INIT] reset_device_and_reopen(no_reset=", args.no_reset_first, ")")
    dev = reset_device_and_reopen(no_reset=args.no_reset_first)
    _log("[INIT] device opened")
    handle = DevHandle(dev)

    # CRITICAL: STOP first to clear any locked cur_samples_per_frame
    try:
        send_cmd(handle.dev, bytes([CMD_STOP]))
        time.sleep(0.2)
    except Exception as e:
        _log("[WARN] initial STOP failed:", e)
    
    # Configure stream settings AFTER STOP to ensure clean state
    # NOTE: In FULL mode, SET_WINDOWS is NOT used - ADC profile determines size
    # Отправляем SET_FRAME_SAMPLES только если явно задано ns>0; иначе не ограничиваем устройство
    if args.ns and args.ns > 0:
        _log("[CFG] SET_FRAME_SAMPLES:", args.ns)
        send_cmd(handle.dev, bytes([CMD_SET_FRAME_SAMPLES] + le16(args.ns)))
    _log("[CFG] SET_FULL_MODE=1")
    send_cmd(handle.dev, bytes([CMD_SET_FULL_MODE, 1]))
    # Безопасное сопоставление profile: 0 трактуем как 1 (A @200 Гц)
    prof = args.profile
    if prof == 0:
        _log("[CFG] profile 0 mapped to 1 (A @200Hz)")
        prof = 1
    _log("[CFG] SET_PROFILE=", prof)
    send_cmd(handle.dev, bytes([CMD_SET_PROFILE, prof]))
    
    # CRITICAL: Wait for profile switch to complete and ADC to reconfigure
    time.sleep(0.3)
    
    # Выбор режимов каналов: A-only при --single, иначе оба канала
    try:
        _log("[CFG] SET_CHMODE=", (0x00 if args.single else 0x02))
        send_cmd(handle.dev, bytes([CMD_SET_CHMODE, 0x00 if args.single else 0x02]))
    except Exception as e:
        _log("[WARN] SET_CHMODE failed:", e)
    # Включим асинхронный режим A/B (независимые потоки) — устойчивее для визуализации
    try:
        _log("[CFG] SET_ASYNC_MODE=1")
        send_cmd(handle.dev, bytes([CMD_SET_ASYNC_MODE, 0x01]))
    except Exception as e:
        _log("[WARN] SET_ASYNC_MODE failed:", e)
    # Подскажем устройству целевую частоту блоков (для LCD/диагностики), фактическая задаётся профилем
    try:
        # Поддержим ожидаемое 200 Гц, если профиль A (1) или пользователь указал 0
        bhz = 200 if prof == 1 else (300 if prof in (2,3) else 400)
        _log("[CFG] SET_BLOCK_HZ=", bhz)
        send_cmd(handle.dev, bytes([CMD_SET_BLOCK_HZ] + le16(bhz)))
    except Exception as e:
        _log("[WARN] SET_BLOCK_HZ failed:", e)
    
    # Small delay before START to ensure config is applied
    time.sleep(0.1)
    _log("[RUN] START stream (bulk + ctrl fallback + GET_STATUS)")
    send_cmd(handle.dev, bytes([CMD_START]))  # bulk попытка
    # Дадим прошивке время поднять стрим до первого TX перед контролем статуса
    time.sleep(0.15)
    # Первым делом аккуратно поставим запрос STAT через bulk (это только ставит флаг на устройстве)
    try:
        handle.dev.write(EP_OUT, bytes([CMD_GET_STATUS]), timeout=300)  # type: ignore[attr-defined]
        _log("[DIAG] GET_STATUS queued via BULK after START")
    except Exception as e:
        _log("[WARN] BULK GET_STATUS after START failed:", e)
    # Затем ctrl GET_STATUS с бэкоффом: одна повторная попытка при ошибке канала
    def _try_ctrl_status_once(timeout_ms: int = 400) -> bool:
        try:
            handle.dev.ctrl_transfer(0xC0, CMD_GET_STATUS, 0, 0, 64, timeout=timeout_ms)  # type: ignore[attr-defined]
            _log("[DIAG] GET_STATUS after START (CTRL) OK")
            return True
        except Exception as e:
            _log("[WARN] GET_STATUS after START (CTRL) failed:", e)
            return False
    if not _try_ctrl_status_once(400):
        time.sleep(0.30)
        _try_ctrl_status_once(600)

    # CRITICAL: Queue size=1 for real-time display (always show LATEST frame)
    # GUI displays at ~20 FPS, device sends at 200 Hz → drop intermediate frames
    q = queue.Queue(maxsize=1)
    stop_ev = threading.Event()
    # Глобальный статус и логгер
    global g_status
    g_status = GuiStatus(q, args.ns)
    t = threading.Thread(target=reader_thread, args=(handle,q,stop_ev, args.pkt_log_interval), daemon=True)
    t.start()
    tlog = threading.Thread(target=status_logger_thread, args=(stop_ev, args.status_log_interval), daemon=True); tlog.start()
    if args.watchdog:
        twdg = threading.Thread(target=status_watchdog_thread, args=(handle, stop_ev, args.wdg_idle_sec, args.wdg_timeouts, args), daemon=True)
        twdg.start()
    # Лёгкий пробник статуса — безопасно дёргает GET_STATUS при видимых простоях
    tprobe = threading.Thread(target=status_probe_thread, args=(handle, stop_ev, 1.5), daemon=True)
    tprobe.start()

    plot = LivePlot(args.ns if args.ns>0 else 1360, args.single, ns_auto=(args.ns<=0), trigger_level=args.trigger)

    # DEBUG: Глобальная переменная для проверки лестницы
    _last_ladder_check = [0.0]  # используем список для mutable state в closure

    # generator of frames for animation
    def gen():
        ts_list = []
        last_seq = None
        a_buf = None
        a_seq = None
        last_a_ts = None
        a_buf_t0 = 0.0
        # Сильно уменьшаем ожидание пары, чтобы отрисовывать "по буферу" без пауз
        # Привязываем к целевому FPS: ~1.5 кадра ожидания
        target_fps = getattr(args, 'fps', 60)
        pair_timeout = max(0.005, 1.5/float(target_fps))
        while True:
            # collect until we get A then B of same seq
            try:
                while True:
                    # Короче таймаут очереди, чтобы не стопорить анимацию
                    h, p = q.get(timeout=0.05)
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
                            
                            # DEBUG: Проверка лестницы отключена — маркеры убраны
                            if False:  # ОТКЛЮЧЕНО
                                now = time.time()
                                if now - _last_ladder_check[0] >= 1.0:
                                    _last_ladder_check[0] = now
                                    # Проверяем первые 5 ступеней: [0]=0, [100]=500, [200]=1000, [300]=1500, [400]=2000
                                    s0 = a_vals[0] if len(a_vals) > 0 else -1
                                    s100 = a_vals[100] if len(a_vals) > 100 else -1
                                    s200 = a_vals[200] if len(a_vals) > 200 else -1
                                    s300 = a_vals[300] if len(a_vals) > 300 else -1
                                    s400 = a_vals[400] if len(a_vals) > 400 else -1
                                    print(f"[HOST_RX] Ladder A (ns={len(a_vals)}): [0]={s0} [100]={s100} [200]={s200} [300]={s300} [400]={s400}")
                            
                            if g_status is not None:
                                g_status.on_pair_done(rate_med, h['seq'])
                                g_status.on_display_frame()
                            yield (rate_med, h['seq'], a_vals, None)
                            break
                        else:
                            last_seq = h['seq']
                            a_seq = h['seq']
                            a_buf = p
                            a_buf_t0 = time.time()
                    elif not args.single and h['flags'] == 0x02:
                        # B-канал: обрабатываем независимо от A (асинхронная отрисовка)
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
                        # unpack B as u16 LE
                        hdr_ns = h['ns']
                        ns_limit = args.ns if args.ns and args.ns>0 else hdr_ns
                        ns_b = min(ns_limit, len(p)//2)
                        b_vals = [p[2*i] | (p[2*i+1]<<8) for i in range(ns_b)]
                        # Если есть последний A с совместимым размером — выводим пару, иначе только B
                        if a_buf is not None:
                            ns_a = min(ns_limit, len(a_buf)//2)
                            a_vals = [a_buf[2*i] | (a_buf[2*i+1]<<8) for i in range(ns_a)]
                            ns = min(ns_a, ns_b)
                            # DEBUG: Проверка лестницы B отключена
                            if False:  # ОТКЛЮЧЕНО
                                now_b = time.time()
                                if now_b - _last_ladder_check[0] < 1.0:  # В течение 1 сек после A
                                    s0_b = b_vals[0] if len(b_vals) > 0 else -1
                                    s100_b = b_vals[100] if len(b_vals) > 100 else -1
                                    s200_b = b_vals[200] if len(b_vals) > 200 else -1
                                    s300_b = b_vals[300] if len(b_vals) > 300 else -1
                                    s400_b = b_vals[400] if len(b_vals) > 400 else -1
                                    print(f"[HOST_RX] Ladder B (ns={len(b_vals)}): [0]={s0_b} [100]={s100_b} [200]={s200_b} [300]={s300_b} [400]={s400_b}")
                            
                            # обновим статус по завершённой паре
                            if g_status is not None:
                                g_status.on_pair_done(rate_med, h['seq'])
                                g_status.on_display_frame()
                            yield (rate_med, h['seq'], a_vals[:ns], b_vals[:ns])
                            a_buf = None
                            a_seq = None
                        else:
                            # B пришёл без A — показываем только B (второй график пустой)
                            if g_status is not None:
                                g_status.on_pair_done(rate_med, h['seq'])
                                g_status.on_display_frame()
                            yield (rate_med, h['seq'], [], b_vals)
                        break
                    # Фолбэк: если получили A, но B не пришёл быстро — выводим A-кадр одиночно (асинхронная отрисовка)
                    if not args.single and a_buf is not None and (time.time() - a_buf_t0) > pair_timeout:
                        # оценка частоты по A-меткам, если доступно
                        if last_a_ts is not None:
                            ts_list.append(last_a_ts)
                            if len(ts_list) > args.pairs:
                                ts_list.pop(0)
                        dts = [ts_list[i+1]-ts_list[i] for i in range(len(ts_list)-1)]
                        dts = [(dt + (1<<32)) if dt < 0 else dt for dt in dts]
                        rate_med = 0.0
                        if dts:
                            md = sorted(dts)[len(dts)//2]
                            rate_med = 1000.0/md if md>0 else 0.0
                        hdr_ns = h['ns'] if 'ns' in h else (len(a_buf)//2)
                        ns_limit = args.ns if args.ns and args.ns>0 else hdr_ns
                        ns = min(ns_limit, len(a_buf)//2)
                        a_vals = [a_buf[2*i] | (a_buf[2*i+1]<<8) for i in range(ns)]
                        if g_status is not None:
                            g_status.on_pair_done(rate_med, a_seq if a_seq is not None else (h['seq'] if 'seq' in h else 0))
                            g_status.on_display_frame()
                        yield (rate_med, a_seq if a_seq is not None else (h['seq'] if 'seq' in h else 0), a_vals, None)
                        a_buf = None
                        a_seq = None
                        break
            except queue.Empty:
                # Нет новых кадров – всё равно yield последний пустой апдейт, чтобы не блокировать GUI
                # Передадим пустые данные: update() просто ничего не перерисует кроме оверлея
                yield (g_status.rate_med if g_status else 0.0, g_status.last_seq if g_status else 0, [], None)
                continue
    # Управляем целевой частотой отрисовки через аргумент --fps (по умолчанию 60)
    fps = getattr(args, 'fps', 60)
    try:
        fps = int(fps)
    except Exception:
        fps = 60
    interval_ms = max(5, int(1000/max(1, fps)))
    # В TkAgg + fig.text объекты (rate/ns) не поддерживают blit корректно (axes=None) → отключаем blit
    ani = animation.FuncAnimation(plot.fig, plot.update, gen(), interval=interval_ms, blit=False, cache_frame_data=False)

    def on_close(evt):
        stop_ev.set()
        try:
            send_cmd(handle.dev, bytes([CMD_STOP]))
        except Exception:
            pass
        try:
            usb.util.release_interface(handle.dev, INTERFACE)
        except Exception as e:
            if _log:
                _log("[WARN] release_interface failed:", e)
            else:
                print("[WARN] release_interface failed:", e)
        if _log_close:
            _log_close()
        # Удалён вызов attach_kernel_driver: на целевой Windows среде WinUSB не поддерживает повторное прикрепление.

    plot.fig.canvas.mpl_connect('close_event', on_close)
    plt.tight_layout()
    plt.show()

if __name__ == '__main__':
    main()
