#!/usr/bin/env python3
"""
Оптимизированный осциллограф для BMI30 с раздельными потоками:
- USB поток (высокий приоритет): чтение @ 200 Hz
- GUI поток (низкий приоритет): отрисовка @ естественная скорость Qt
- Показывает метрики: RX rate и Display FPS
"""

import sys, time, struct, threading, queue, argparse
from pathlib import Path
import usb.core, usb.util
from typing import Optional, cast

# USB параметры
VENDOR = 0xCAFE
PRODUCT = 0x4001
INTERFACE = 2
EP_OUT = 0x03
EP_IN = 0x83
HDR_SIZE = 32
MAX_SAMPLES = 4096  # здравый предел для кадров; всё большее считаем повреждённым

# CRC16-CCITT (0x1021, init 0xFFFF) по байтам payload, как в прошивке
def crc16_ccitt(data: bytes, poly=0x1021, init=0xFFFF):
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc

# Команды
CMD_SET_WINDOWS = 0x10
CMD_SET_TRUNC_SAMPLES = 0x16  # payload: u16 (0=disable)
CMD_SET_FRAME_SAMPLES = 0x17  # payload: u16 (0=use profile/default)
CMD_START = 0x20
CMD_STOP = 0x21
CMD_SET_ASYNC_MODE = 0x18
CMD_SET_CHMODE = 0x19
CMD_SET_FULL_MODE = 0x13
CMD_SET_PROFILE = 0x14
CMD_SET_STREAM_MODE = 0x1A  # 0=LATEST (как сейчас), 1=LOSSLESS_ROI (280..480, 200)

CMD_NAMES = {
    CMD_SET_WINDOWS: 'SET_WINDOWS',
    CMD_SET_TRUNC_SAMPLES: 'SET_TRUNC_SAMPLES',
    CMD_SET_FRAME_SAMPLES: 'SET_FRAME_SAMPLES',
    CMD_START: 'START',
    CMD_STOP: 'STOP',
    CMD_SET_ASYNC_MODE: 'SET_ASYNC_MODE',
    CMD_SET_CHMODE: 'SET_CHMODE',
    CMD_SET_FULL_MODE: 'SET_FULL_MODE',
    CMD_SET_PROFILE: 'SET_PROFILE',
    CMD_SET_STREAM_MODE: 'SET_STREAM_MODE',
}
CMD_TRACE_PATH = Path(__file__).with_name('rpi_usb_cmd_trace.log')

def find_dev():
    """Поиск и инициализация устройства BMI30.

    Пробуем аккуратно выставить конфигурацию и altsetting, не падая на уже выбранной/занятой конфигурации.
    """
    # PyUSB typing stubs иногда считают usb.core.find() генератором.
    # Явно фиксируем find_all=False и приводим тип после проверки на None.
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT, find_all=False)
    if dev is None:
        raise SystemExit(f"Device {VENDOR:04X}:{PRODUCT:04X} not found")

    dev = cast(usb.core.Device, dev)

    def log_once(label, err):
        print(f"[USB][WARN] {label}: {err}")

    def _check_eps() -> bool:
        try:
            cfg = dev.get_active_configuration()
            intf = cfg[(INTERFACE, 1)]  # type: ignore[index]
            eps = [ep.bEndpointAddress for ep in intf]
            ok = (EP_IN in eps) and (EP_OUT in eps)
            if not ok:
                log_once("endpoint_check", f"expected EP_OUT=0x{EP_OUT:02X}, EP_IN=0x{EP_IN:02X}, got {eps}")
            return ok
        except Exception as e:
            log_once("endpoint_check", e)
            return False

    def _apply_cfg_alt_once(tag: str):
        # Минимальные действия с одноразовыми предупреждениями (Windows-драйверы часто уже выбрали конфигурацию)
        try:
            dev.set_configuration()  # default cfg=1
        except Exception as e:
            log_once(f"{tag}:set_configuration", e)

        try:
            usb.util.claim_interface(dev, INTERFACE)
        except Exception as e:
            log_once(f"{tag}:claim_interface", e)

        # Активный поток работает только на alt=1 (alt=0 без эндпоинтов)
        try:
            dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
        except Exception as e:
            log_once(f"{tag}:set_interface_altsetting(alt=1)", e)

        # Иногда полезно снять halt на случай, если прошлый процесс оставил STALL
        try:
            dev.clear_halt(EP_IN)
        except Exception:
            pass
        try:
            dev.clear_halt(EP_OUT)
        except Exception:
            pass

    _apply_cfg_alt_once("init")
    if not _check_eps():
        # Иногда get_active_configuration/altsetting не применяются с первого раза
        time.sleep(0.05)
        _apply_cfg_alt_once("retry")
        _check_eps()

    return dev


def try_open_dev(timeout_s: float = 6.0, poll_s: float = 0.25):
    """Мягко открыть устройство с ретраями (без SystemExit).

    Используется watchdog-ом для ре-открытия после dev.reset()/ошибок libusb.
    """
    deadline = time.time() + float(timeout_s)
    last_err = None
    while time.time() < deadline:
        try:
            dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT, find_all=False)
            if dev is None:
                time.sleep(poll_s)
                continue
            dev = cast(usb.core.Device, dev)
            try:
                dev.set_configuration()
            except Exception:
                pass
            try:
                usb.util.claim_interface(dev, INTERFACE)
            except Exception:
                pass
            try:
                dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
            except Exception:
                pass
            try:
                dev.clear_halt(EP_IN)
            except Exception:
                pass
            try:
                dev.clear_halt(EP_OUT)
            except Exception:
                pass
            return dev
        except Exception as e:
            last_err = e
            time.sleep(poll_s)
    if last_err is not None:
        print(f"[USB][WARN] try_open_dev failed: {last_err}")
    return None

def send_cmd(dev, cmd_byte, data=None, lock: Optional[threading.Lock] = None):
    """Отправка команды через EP_OUT.

    lock (опционально) нужен, чтобы не конфликтовать с параллельным dev.read() в USB потоке.
    """
    pkt = bytearray([cmd_byte])
    if data:
        pkt.extend(data)
    try:
        ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        frac_ms = int((time.time() % 1.0) * 1000.0)
        name = CMD_NAMES.get(int(cmd_byte) & 0xFF, 'UNKNOWN')
        payload = bytes(pkt[1:]).hex()
        with CMD_TRACE_PATH.open('a', encoding='ascii', errors='ignore') as f:
            f.write(f"{ts}.{frac_ms:03d} cmd=0x{int(cmd_byte) & 0xFF:02X} name={name} len={len(pkt) - 1} payload={payload}\n")
    except Exception:
        pass
    try:
        if lock is None:
            dev.write(EP_OUT, pkt, timeout=500)
        else:
            with lock:
                dev.write(EP_OUT, pkt, timeout=500)
        return True
    except Exception as e:
        print(f"[ERROR] send_cmd({cmd_byte:02X}): {e}")
        return False


def _pack_windows(win0_start: int, win0_len: int, win1_start: int = 0, win1_len: int = 0) -> bytes:
    return struct.pack('<HHHH', int(win0_start) & 0xFFFF, int(win0_len) & 0xFFFF, int(win1_start) & 0xFFFF, int(win1_len) & 0xFFFF)

def parse_hdr(b: bytes):
    """Парсинг заголовка кадра."""
    if len(b) < HDR_SIZE:
        return None
    # Структура 32 байта (см. прошивку):
    # magic,u8 ver,u8 flags,u32 seq,u32 ts,u16 total_samples,u16 zone_cnt,
    # u32 zone_off,u32 zone_len,u32 reserved,u16 reserved2,u16 crc16
    magic, ver, flags, seq, ts, total_samples, zone_cnt, zone_off, zone_len, reserved, reserved2, crc16 = struct.unpack_from(
        '<HBBIIHHIIIHH', b, 0
    )
    # Прошивка использует:
    # - reserved (u32) как DMA/frame sequence
    # - reserved2 (u16) как buffer_index (0..7)
    dma_seq = reserved
    buf_idx = reserved2 & 0x07
    # Чёт/неч должен быть стабильным и привязанным к buffer_index, который даёт устройство
    parity = buf_idx & 0x01  # 0=even, 1=odd
    frame_seq = reserved  # DEBUG
    parity_res2 = parity  # DEBUG/legacy
    return {
        'magic': magic,
        'ver': ver,
        'flags': flags,
        'seq': seq,
        'dma_seq': dma_seq,
        'frame_seq': frame_seq,  # DEBUG: добавлено
        'parity': parity,  # Parity из seq
        'parity_res2': parity_res2,  # DEBUG: parity из reserved2
        'ts': ts,
        'ns': total_samples,
        'zone_cnt': zone_cnt,
        'zone_off': zone_off,
        'zone_len': zone_len,
        'reserved': reserved,
        'reserved2': reserved2,
        'buf_idx': buf_idx,
        'crc16': crc16,
    }

class USBReader:
    """Поток чтения USB с высоким приоритетом."""
    
    def __init__(self, dev, stop_event, watchdog: bool = False, wdg_restart: bool = False, rx_timeout_ms: int = 100):
        self.dev = dev
        self.stop_event = stop_event
        self.watchdog = watchdog
        self.wdg_restart = wdg_restart
        self.rx_timeout_ms = int(rx_timeout_ms)

        # Общий lock на операции с PyUSB (read/write), чтобы кнопки режима не ломали чтение
        self.io_lock = threading.Lock()
        
        # Простые буферы: последний кадр для каждого типа
        self.lock = threading.Lock()
        # Храним (samples_list, x0), где x0 берётся из заголовка (zone_off).
        # Для полного кадра: zone_cnt=0 => x0=0. Для ROI: x0=280 => ось X = 280..479.
        self.frame_a_even = None
        self.frame_a_odd = None
        self.frame_b_even = None
        self.frame_b_odd = None
        
        # Статистика
        self.last_dma_seq_a = -1
        self.last_dma_seq_b = -1
        self.gap_a = 0
        self.gap_b = 0
        self.last_bad_log_ts = 0.0
        self.spike_log_path = Path("host_spike_log.txt")

        # Чтобы явно видеть, что режим (LATEST/ROI) реально поменял размер кадра
        self.last_ns_a: Optional[int] = None
        self.last_ns_b: Optional[int] = None

        # Одноразовый лог, чтобы подтвердить что ROI идёт с нужного смещения (например 280)
        self._logged_roi_zone = False

        # При переключении в ROI полезно кратковременно не публиковать кадры,
        # пока не увидим корректные zone_off/zone_len (иначе на экране может мелькнуть X=0..199).
        self._expected_roi = None  # (start, length) | None
        self._expected_roi_seen_mask = 0  # bit0=A, bit1=B

        # Instant-rate (за последний интервал), чтобы не путать со средним с начала сессии
        self._inst_last_t = time.time()
        self._inst_last_rx = 0
        self._inst_last_pairs = 0
        self._inst_last_bytes = 0
        self._inst_rx_rate = 0.0
        self._inst_pair_rate = 0.0
        self._inst_kb_s = 0.0
        
        # Статистика приёма
        self.rx_count = 0
        # Счётчики по каналам (нужны для метрик в async режиме)
        self.rx_a_count = 0
        self.rx_b_count = 0
        self.rx_pair_count = 0  # legacy поле; фактически отдаём min(rx_a, rx_b) через get_stats()
        self.rx_bytes = 0
        self.start_time = time.time()
        self.timeout_count = 0  # Счётчик таймаутов
        self.last_rx_time = time.time()  # Время последнего успешного приёма

        # Watchdog по умолчанию только наблюдает. Авто STOP/START/reopen включается
        # только явным --wdg-restart, иначе host сам создаёт сбросы потока.
        self._wdg_last_action_t = 0.0
        self._wdg_stage = 0  # 0=START, 1=STOP+START
        self._wdg_last_observe_log_t = 0.0

        # Наблюдение за повторяющимися PIPE errors (когда clear_halt+alt не помогает)
        self._pipe_err_streak = 0

    def _log_watchdog_observe(self, reason: str, detail: str = ""):
        now = time.time()
        if (now - self._wdg_last_observe_log_t) < 5.0:
            return
        self._wdg_last_observe_log_t = now
        suffix = f" {detail}" if detail else ""
        print(f"[WDG] Observe only: {reason}{suffix}; no STOP/START/reopen (--wdg-restart disabled)")
        
    def get_latest_buffers(self):
        """Получить копии последних кадров для отображения."""
        with self.lock:
            return (self.frame_a_even, self.frame_a_odd, 
                    self.frame_b_even, self.frame_b_odd)

    def get_dma_stats(self):
        with self.lock:
            return {
                'last_a': self.last_dma_seq_a,
                'last_b': self.last_dma_seq_b,
                'gap_a': self.gap_a,
                'gap_b': self.gap_b,
            }
    
    def get_stats(self):
        """Получить статистику приёма."""
        now = time.time()
        elapsed = now - self.start_time
        with self.lock:
            pair_total = min(self.rx_a_count, self.rx_b_count)
            # Обновим instant-rate на основе дельт (вызов идёт из GUI ~10 Гц)
            dt = now - self._inst_last_t
            if dt > 1e-3:
                drx = self.rx_count - self._inst_last_rx
                dpairs = pair_total - self._inst_last_pairs
                dbytes = self.rx_bytes - self._inst_last_bytes
                self._inst_rx_rate = drx / dt
                self._inst_pair_rate = dpairs / dt
                self._inst_kb_s = (dbytes / dt) / 1024.0
                self._inst_last_t = now
                self._inst_last_rx = self.rx_count
                self._inst_last_pairs = pair_total
                self._inst_last_bytes = self.rx_bytes

            return {
                'rx_count': self.rx_count,
                'rx_pair_count': pair_total,
                'rx_bytes': self.rx_bytes,
                'rx_rate': self.rx_count / elapsed if elapsed > 0 else 0,
                'pair_rate': pair_total / elapsed if elapsed > 0 else 0,
                'throughput': self.rx_bytes / elapsed / 1024 if elapsed > 0 else 0,
                'rx_rate_inst': self._inst_rx_rate,
                'pair_rate_inst': self._inst_pair_rate,
                'throughput_inst': self._inst_kb_s,
                'elapsed': elapsed,
            }

    def prepare_for_mode_switch(self, expect_roi: Optional[tuple[int, int]]):
        """Сбросить display-буферы и (опционально) включить ожидание ROI метаданных."""
        with self.lock:
            self.frame_a_even = None
            self.frame_a_odd = None
            self.frame_b_even = None
            self.frame_b_odd = None
            self.last_ns_a = None
            self.last_ns_b = None
            self._logged_roi_zone = False
            self._expected_roi = expect_roi
            self._expected_roi_seen_mask = 0
    
    def run(self):
        """Основной цикл чтения USB."""
        print("[USB] Reader thread started (high priority)")

        # Обозначаем старт сессии в файле логов выбросов
        try:
            with self.spike_log_path.open("a", encoding="ascii", errors="ignore") as f:
                f.write(f"\n# session start {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        except Exception as e:
            print(f"[WARN] spike log file not writable: {e}")
        
        # Попытка установить высокий приоритет потока
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), 2  # THREAD_PRIORITY_HIGHEST
            )
            print("[USB] Thread priority set to HIGHEST")
        except:
            pass
        
        rx_buffer = bytearray()

        def _ensure_vendor_ready_locked(tag: str = "") -> None:
            """Убедиться, что IF#2 alt=1 активен и endpoints не в STALL.

            Должно вызываться ТОЛЬКО под self.io_lock.
            """
            if tag:
                tag = f"[{tag}] "
            try:
                usb.util.claim_interface(self.dev, INTERFACE)
            except Exception:
                pass
            try:
                self.dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
            except Exception:
                pass
            try:
                self.dev.clear_halt(EP_IN)
            except Exception:
                pass
            try:
                self.dev.clear_halt(EP_OUT)
            except Exception:
                pass
            # Лёгкая проверка EP наличия в активной конфигурации
            try:
                cfg = self.dev.get_active_configuration()
                intf = cfg[(INTERFACE, 1)]  # type: ignore[index]
                eps = [ep.bEndpointAddress for ep in intf]
                if EP_IN not in eps or EP_OUT not in eps:
                    print(f"{tag}[USB][WARN] alt=1 endpoints missing: {eps}")
            except Exception:
                pass

        def _reopen_device(reason: str) -> bool:
            """Попробовать reset + reopen устройства (последняя линия обороны)."""
            try:
                print(f"[USB][RECOVER] Reopen device: {reason}")
                with self.io_lock:
                    old = self.dev
                    try:
                        # Иногда помогает снять halt до reset
                        try:
                            old.clear_halt(EP_IN)
                        except Exception:
                            pass
                        try:
                            old.clear_halt(EP_OUT)
                        except Exception:
                            pass
                        try:
                            old.reset()
                        except Exception as e:
                            print(f"[USB][RECOVER] dev.reset() failed: {e}")
                    finally:
                        try:
                            usb.util.release_interface(old, INTERFACE)
                        except Exception:
                            pass
                        try:
                            usb.util.dispose_resources(old)
                        except Exception:
                            pass

                    time.sleep(0.35)
                    new_dev = try_open_dev(timeout_s=8.0)
                    if new_dev is None:
                        print("[USB][RECOVER] reopen failed: device not found")
                        return False
                    self.dev = new_dev

                    # После reopen обязательно переутвердить alt=1 и снова запустить поток.
                    _ensure_vendor_ready_locked("reopen")
                    send_cmd(self.dev, CMD_SET_CHMODE, [0x02], lock=None)
                    time.sleep(0.02)
                    send_cmd(self.dev, CMD_SET_FULL_MODE, [0x01], lock=None)
                    time.sleep(0.02)
                    send_cmd(self.dev, CMD_START, lock=None)
                return True
            except Exception as e:
                print(f"[USB][RECOVER] reopen exception: {e}")
                return False

        def _recover_in_pipe_error(err: Exception) -> bool:
            """Попытка восстановиться после STALL/PIPE на bulk IN (часто бывает при STOP/START)."""
            try:
                msg = str(err).lower()
                errno = getattr(err, 'errno', None)
                if errno != 32 and 'pipe' not in msg and 'stall' not in msg:
                    return False
                with self.io_lock:
                    # Снимаем halt на IN/OUT и переутверждаем alt=1
                    try:
                        self.dev.clear_halt(EP_IN)
                    except Exception:
                        pass
                    try:
                        self.dev.clear_halt(EP_OUT)
                    except Exception:
                        pass
                    try:
                        usb.util.claim_interface(self.dev, INTERFACE)
                    except Exception:
                        pass
                    try:
                        self.dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
                    except Exception:
                        pass
                return True
            except Exception:
                return False
        
        while not self.stop_event.is_set():
            try:
                # Чтение по 4096 байт (оптимизировано для 200 Hz)
                with self.io_lock:
                    chunk = self.dev.read(EP_IN, 4096, timeout=self.rx_timeout_ms)
                self._pipe_err_streak = 0
                self.rx_bytes += len(chunk)
                self.last_rx_time = time.time()
                rx_buffer += bytes(chunk)
                
            except usb.core.USBError as e:
                if _recover_in_pipe_error(e):
                    self._pipe_err_streak += 1
                    if self.watchdog and self._pipe_err_streak >= 6:
                        if not self.wdg_restart:
                            self._log_watchdog_observe("pipe_err_streak", f"streak={self._pipe_err_streak}")
                            time.sleep(0.15)
                            continue
                        # Похоже, handle/altsetting залипли — пробуем reopen/reset
                        self._pipe_err_streak = 0
                        _reopen_device("pipe_err_streak")
                        time.sleep(0.15)
                        continue
                    time.sleep(0.05)
                    continue
                if getattr(e, 'errno', None) in (110, 10060) or 'timed out' in str(e).lower():
                    self.timeout_count += 1

                    # Watchdog: по умолчанию только логирует простой. Перезапуск потока
                    # сохраняем только для явного диагностического режима --wdg-restart.
                    if self.watchdog:
                        now = time.time()
                        silence = now - self.last_rx_time
                        if silence >= 3.0 and (now - self._wdg_last_action_t) >= 3.0:
                            self._wdg_last_action_t = now
                            if not self.wdg_restart:
                                self._log_watchdog_observe("no_rx", f"silence={silence:.1f}s timeouts={self.timeout_count}")
                                continue
                            if self._wdg_stage == 0:
                                print(f"[WDG] No RX for {silence:.1f}s -> clear_halt + START")
                                with self.io_lock:
                                    _ensure_vendor_ready_locked("wdg0")
                                # На всякий случай переутвердим базовую конфигурацию потока
                                send_cmd(self.dev, CMD_SET_CHMODE, [0x02], lock=self.io_lock)
                                time.sleep(0.02)
                                send_cmd(self.dev, CMD_SET_FULL_MODE, [0x01], lock=self.io_lock)
                                time.sleep(0.02)
                                send_cmd(self.dev, CMD_START, lock=self.io_lock)
                                self._wdg_stage = 1
                            else:
                                print(f"[WDG] No RX for {silence:.1f}s -> STOP + START")
                                send_cmd(self.dev, CMD_STOP, lock=self.io_lock)
                                time.sleep(0.15)
                                # После STOP часто полезно снять halt и переутвердить alt=1
                                with self.io_lock:
                                    _ensure_vendor_ready_locked("wdg1")
                                send_cmd(self.dev, CMD_SET_CHMODE, [0x02], lock=self.io_lock)
                                time.sleep(0.02)
                                send_cmd(self.dev, CMD_SET_FULL_MODE, [0x01], lock=self.io_lock)
                                time.sleep(0.02)
                                send_cmd(self.dev, CMD_START, lock=self.io_lock)
                                self._wdg_stage = 0

                            # Если на старте вообще нет RX — ускоряем восстановление.
                            # Практика: после неудачного STOP/SET_* иногда помогает только reopen.
                            if self.rx_count == 0 and silence >= 3.5:
                                _reopen_device(f"startup_no_rx_{silence:.1f}s")
                            # Если давно нет RX в середине работы — тоже пробуем reopen/reset устройства
                            if silence >= 7.0:
                                _reopen_device(f"no_rx_{silence:.1f}s")

                    # Каждые 50 таймаутов (5 секунд) печатаем предупреждение
                    if self.timeout_count % 50 == 0:
                        elapsed = time.time() - self.last_rx_time
                        print(f"[USB] WARNING: {self.timeout_count} timeouts, no data for {elapsed:.1f}s (rx_count={self.rx_count})")
                    continue
                else:
                    # Для «жёстких» USB ошибок старое поведение делало reopen/reset.
                    # В UDP-like режиме не перезапускаем поток автоматически.
                    print(f"[USB] Error: {e}")
                    if self.wdg_restart:
                        _reopen_device(f"usb_error_{getattr(e, 'errno', None)}")
                    else:
                        self._log_watchdog_observe("usb_error", f"errno={getattr(e, 'errno', None)}")
                        time.sleep(0.2)
                    continue
            
            # Парсинг кадров из буфера
            while True:
                ch_mask = 0  # default to avoid unbound when logging spikes
                if len(rx_buffer) < 4:
                    break
                
                # Пропускаем STAT-кадры
                if rx_buffer[0:4] == b'STAT':
                    if len(rx_buffer) >= 84:
                        rx_buffer = rx_buffer[84:]
                        continue
                    elif len(rx_buffer) >= 64:
                        rx_buffer = rx_buffer[64:]
                        continue
                    elif len(rx_buffer) >= 52:
                        rx_buffer = rx_buffer[52:]
                        continue
                    else:
                        break
                
                # Проверяем заголовок ADC кадра (magic: 0x5A 0xA5 0x01)
                if rx_buffer[0] == 0x5A and rx_buffer[1] == 0xA5 and rx_buffer[2] == 0x01:
                    if len(rx_buffer) < HDR_SIZE:
                        break
                    total_samples = rx_buffer[12] | (rx_buffer[13] << 8)
                    # Отбрасываем явно некорректные размеры, чтобы не потерять синхронизацию
                    if total_samples == 0 or total_samples > MAX_SAMPLES:
                        rx_buffer = rx_buffer[1:]
                        continue
                    frame_len = HDR_SIZE + total_samples * 2
                    if len(rx_buffer) < frame_len:
                        break  # ждём пока буфер наполнится

                    frame = bytes(rx_buffer[:frame_len])
                    rx_buffer = rx_buffer[frame_len:]

                    h = parse_hdr(frame)
                    if not h or h.get('magic') != 0xA55A:
                        continue

                    # Проверяем CRC, если выставлен флаг (bit2=CRC)
                    flags = h.get('flags', 0)
                    ns = h.get('ns', total_samples)
                    payload = frame[HDR_SIZE:HDR_SIZE + ns * 2]
                    ch_mask = flags & 0x03  # 0x01=A, 0x02=B

                    # Для ROI прошивка заполняет zone_off/zone_len (например 280/200),
                    # чтобы хост мог отображать X в исходных индексах.
                    # Делаем устойчиво: если zone_off задан и zone_len совпадает с ns, считаем это ROI.
                    zc = int(h.get('zone_cnt', 0))
                    zo = int(h.get('zone_off', 0))
                    zl = int(h.get('zone_len', 0))
                    x0 = zo if (zo != 0 and zl == ns) or (zc > 0 and zo != 0) else 0

                    # При ожидании ROI: не публикуем кадры в display-буферы,
                    # пока не увидим корректные zone_off/zone_len для нужного окна.
                    expected_roi = None
                    with self.lock:
                        expected_roi = self._expected_roi
                    accept_for_display = True
                    if expected_roi is not None:
                        exp_start, exp_len = expected_roi
                        # Важно: прошивка иногда присылает ns=200, но zone_off/zone_len остаются 0.
                        # Для отображения считаем валидным сам факт ns==exp_len (данные уже ROI-срез).
                        # zone_* используем только как подтверждение (когда они не нулевые).
                        meta_ok = (zo == exp_start and zl == exp_len)
                        meta_unknown = (zo == 0 and zl == 0)
                        if not (ns == exp_len and (meta_ok or meta_unknown)):
                            accept_for_display = False
                        else:
                            # пометить что по этому каналу мы уже видели корректный ROI
                            ch_bit = 0x01 if ch_mask == 0x01 else (0x02 if ch_mask == 0x02 else 0x00)
                            if ch_bit:
                                with self.lock:
                                    self._expected_roi_seen_mask |= ch_bit
                                    # Как только увидели корректный ROI и для A, и для B — отключаем гейт
                                    if (self._expected_roi_seen_mask & 0x03) == 0x03:
                                        self._expected_roi = None
                                        self._expected_roi_seen_mask = 0

                    if (not self._logged_roi_zone) and (zo != 0 and zl == ns):
                        self._logged_roi_zone = True
                        print(f"[ROI] zone_off={zo} zone_len={zl} ns={ns}")

                    # Пары/производительность считаем в get_stats() как min(rx_a, rx_b),
                    # чтобы метрика была осмысленной и в async режиме.

                    if flags & 0x04:
                        crc_calc = crc16_ccitt(payload)
                        if crc_calc != h.get('crc16', 0):
                            # повреждённый кадр — пропускаем и продолжаем сдвигаться вперёд
                            rx_buffer = rx_buffer[1:]
                            continue

                    samples = struct.unpack(f'<{ns}H', payload)
                    samples_list = list(samples)

                    # Логируем смену размера кадра по каждому каналу (обычно 600 ↔ 200)
                    if ch_mask == 0x01:
                        if self.last_ns_a is None:
                            self.last_ns_a = ns
                        elif self.last_ns_a != ns:
                            print(
                                f"[MODE] Channel A frame size changed: {self.last_ns_a} -> {ns} "
                                f"(zone={int(h.get('zone_cnt', 0))}:{int(h.get('zone_off', 0))}+{int(h.get('zone_len', 0))})"
                            )
                            self.last_ns_a = ns
                    elif ch_mask == 0x02:
                        if self.last_ns_b is None:
                            self.last_ns_b = ns
                        elif self.last_ns_b != ns:
                            print(
                                f"[MODE] Channel B frame size changed: {self.last_ns_b} -> {ns} "
                                f"(zone={int(h.get('zone_cnt', 0))}:{int(h.get('zone_off', 0))}+{int(h.get('zone_len', 0))})"
                            )
                            self.last_ns_b = ns

                    # Детектор утечки guard pattern (0x0000) в payload - ОТКЛЮЧЕН для производительности
                    # Гвард-паттерн работает правильно, утечки фиксируются, но проверка тормозит USB поток
                    # if samples_list:
                    #     vmin = min(samples_list)
                    #     if vmin == 0:
                    #         now = time.time()
                    #         if now - self.last_bad_log_ts > 1.0:  # не чаще 1 Гц
                    #             self.last_bad_log_ts = now
                    #             imin = samples_list.index(0)
                    #             w0 = max(imin - 11, 0); w1 = min(imin + 11 + 1, len(samples_list))
                    #             win = samples_list[w0:w1]  # 22 значения: [imin-11 : imin+11] включительно
                    #             seq = h.get('seq', 0)
                    #             dma_seq = h.get('dma_seq', -1)
                    #             log_line = f"[HOST_GUARD_LEAK] ch_mask=0x{ch_mask:02X} seq={seq} dma_seq={dma_seq} zero @ {imin} window[{w0}:{w1}]={win}"
                    #             try:
                    #                 with self.spike_log_path.open("a", encoding="ascii", errors="ignore") as f:
                    #                     f.write(log_line + "\n")
                    #             except Exception:
                    #                 pass

                    if self.rx_count < 4:
                        ch_name = 'A' if ch_mask == 0x01 else ('B' if ch_mask == 0x02 else 'Both/Single')
                        parity = h.get('parity', 0)
                        parity_str = 'even' if parity == 0 else 'odd'
                        zc = int(h.get('zone_cnt', 0))
                        zo = int(h.get('zone_off', 0))
                        zl = int(h.get('zone_len', 0))
                        print(f"[DEBUG] Frame #{self.rx_count}: flags=0x{flags:02X} ({ch_name}), ns={ns}, parity={parity_str}, zone={zc}:{zo}+{zl}")

                    with self.lock:
                        dma_seq = h.get('dma_seq', -1)
                        parity = h.get('parity', 0)  # 0=even, 1=odd

                        # Всегда считаем RX, но в display-буферы пишем только если кадр проходит гейт.
                        self.rx_count += 1
                        if ch_mask == 0x01:
                            self.rx_a_count += 1
                        elif ch_mask == 0x02:
                            self.rx_b_count += 1

                        if accept_for_display:
                            frame_tuple = (samples_list, x0)

                            if ch_mask == 0x01:  # Channel A
                                if parity == 0:
                                    self.frame_a_even = frame_tuple  # Заменяем последний кадр
                                else:
                                    self.frame_a_odd = frame_tuple

                                if dma_seq >= 0 and self.last_dma_seq_a >= 0 and dma_seq > self.last_dma_seq_a + 1:
                                    self.gap_a += dma_seq - self.last_dma_seq_a - 1
                                self.last_dma_seq_a = dma_seq

                            elif ch_mask == 0x02:  # Channel B
                                if parity == 0:
                                    self.frame_b_even = frame_tuple
                                else:
                                    self.frame_b_odd = frame_tuple

                                if dma_seq >= 0 and self.last_dma_seq_b >= 0 and dma_seq > self.last_dma_seq_b + 1:
                                    self.gap_b += dma_seq - self.last_dma_seq_b - 1
                                self.last_dma_seq_b = dma_seq
                else:
                    # Неизвестные данные - пропускаем байт
                    rx_buffer = rx_buffer[1:]
        
        print("[USB] Reader thread stopped")

class GUIDisplay:
    """GUI отображение с низким приоритетом."""
    
    def __init__(self, reader, stop_event):
        self.reader = reader
        self.stop_event = stop_event
        
        # Статистика отрисовки
        self.display_count = 0
        self.start_time = time.time()
        self.last_seq = -1

        # Debounce кнопок режимов, чтобы случайные/повторные клики не делали STOP/START в шторм
        self._last_mode_switch_t = 0.0
        self._last_mode_switch_mode: Optional[int] = None
        
        # Matplotlib setup
        import matplotlib
        matplotlib.use('TkAgg')
        import matplotlib.pyplot as plt
        self.plt = plt
        
        # Создаём 2 отдельных графика: один для Channel A, другой для Channel B
        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(14, 10))
        self.fig.suptitle('BMI30 Oscilloscope - Dual Channel ADC @ 400Hz', fontsize=14)
        
        # График 1: Channel A - показываем Even и Odd отдельными линиями
        self.ax1.set_title('Channel A', fontsize=12)
        self.ax1.set_xlabel('Sample #')
        self.ax1.set_ylabel('ADC Value')
        self.ax1.grid(True, alpha=0.3)
        
        # График 2: Channel B - показываем Even и Odd отдельными линиями
        self.ax2.set_title('Channel B', fontsize=12)
        self.ax2.set_xlabel('Sample #')
        self.ax2.set_ylabel('ADC Value')
        self.ax2.grid(True, alpha=0.3)
        
        # Линии для Channel A: Even (синяя) и Odd (голубая)
        self.line_a_even, = self.ax1.plot([], [], 'b-', linewidth=1.2, label='A Even', alpha=0.8)
        self.line_a_odd, = self.ax1.plot([], [], 'c-', linewidth=1.2, label='A Odd', alpha=0.8)
        
        # Линии для Channel B: Even (красная) и Odd (оранжевая)
        self.line_b_even, = self.ax2.plot([], [], 'r-', linewidth=1.2, label='B Even', alpha=0.8)
        self.line_b_odd, = self.ax2.plot([], [], 'orange', linewidth=1.2, label='B Odd', alpha=0.8)
        
        self.ax1.legend(loc='upper right')
        self.ax2.legend(loc='upper right')
        
        # Текст для метрик
        self.text_metrics = self.fig.text(0.02, 0.98, '', 
                                          verticalalignment='top',
                                          fontfamily='monospace',
                                          fontsize=10)

        # Кнопки переключения режима (минимально инвазивно, без отдельного GUI-фреймворка)
        from matplotlib.widgets import Button
        ax_btn_latest = self.fig.add_axes((0.78, 0.93, 0.20, 0.05))
        ax_btn_roi = self.fig.add_axes((0.78, 0.87, 0.20, 0.05))
        self.btn_latest = Button(ax_btn_latest, 'MODE: LATEST')
        self.btn_roi = Button(ax_btn_roi, 'MODE: ROI 280-480')
        self.btn_latest.on_clicked(lambda _evt: self._switch_stream_mode(0))
        self.btn_roi.on_clicked(lambda _evt: self._switch_stream_mode(1))
        
        # Таймер для обновления экрана (~10 Hz как просил пользователь)
        self.timer = self.fig.canvas.new_timer(interval=100)  # 100ms = 10 Hz
        self.timer.add_callback(self.update_display)

    def _switch_stream_mode(self, mode: int):
        """Переключает режим стриминга на устройстве.

        mode:
          0 = как сейчас (последний буфер, возможны пропуски)
          1 = lossless ROI (280..480, 200 семплов)
        """
        mode = 1 if mode else 0

        # Простая защита от дребезга/повторов
        now = time.time()
        if self._last_mode_switch_mode == mode and (now - self._last_mode_switch_t) < 0.7:
            return
        if (now - self._last_mode_switch_t) < 0.35:
            return
        self._last_mode_switch_t = now
        self._last_mode_switch_mode = mode

        print(f"[CMD] Switching stream mode -> {mode}")

        # Смена режима делается через короткий STOP/START, чтобы не ловить рассинхронизацию формата.
        # Также сбрасываем любые прежние ограничения размера (TRUNC/FRAME_SAMPLES),
        # чтобы LATEST всегда был «полным», а ROI определялся только окнами.

        # Подготовка reader: очистить буферы и (для ROI) включить ожидание корректных zone_* метаданных.
        self.reader.prepare_for_mode_switch((280, 200) if mode == 1 else None)

        send_cmd(self.reader.dev, CMD_STOP, lock=self.reader.io_lock)
        time.sleep(0.15)

        # Сброс ограничений размера (на всякий случай, если ранее запускались другие host-скрипты)
        send_cmd(self.reader.dev, CMD_SET_TRUNC_SAMPLES, struct.pack('<H', 0), lock=self.reader.io_lock)
        time.sleep(0.01)
        send_cmd(self.reader.dev, CMD_SET_FRAME_SAMPLES, struct.pack('<H', 0), lock=self.reader.io_lock)
        time.sleep(0.01)

        if mode == 1:
            # Явно задаём окно (win0=280,len=200; win1=0)
            send_cmd(self.reader.dev, CMD_SET_WINDOWS, _pack_windows(280, 200), lock=self.reader.io_lock)
            time.sleep(0.02)
        else:
            # На LATEST сбрасываем окна, чтобы точно не остаться в ROI-конфигурации
            send_cmd(self.reader.dev, CMD_SET_WINDOWS, _pack_windows(0, 0), lock=self.reader.io_lock)
            time.sleep(0.02)

        send_cmd(self.reader.dev, CMD_SET_STREAM_MODE, bytes([mode]), lock=self.reader.io_lock)
        time.sleep(0.02)

        send_cmd(self.reader.dev, CMD_START, lock=self.reader.io_lock)
        time.sleep(0.10)
        
    def update_display(self):
        """Обновление отображения (вызывается таймером Qt с фиксированной частотой ~20 Hz)."""
        if self.stop_event.is_set():
            self.plt.close('all')
            return False
        
        # Берём копии накопительных буферов
        buf_a_even, buf_a_odd, buf_b_even, buf_b_odd = self.reader.get_latest_buffers()
        
        # Обновляем Channel A: Even и Odd отдельно
        if buf_a_even:
            y, x0 = buf_a_even
            # Ось X нормализуем: 0..N-1. Важно, что сами данные уже из ROI (например с 280).
            x = list(range(len(y)))
            self.line_a_even.set_data(x, y)
        
        if buf_a_odd:
            y, x0 = buf_a_odd
            x = list(range(len(y)))
            self.line_a_odd.set_data(x, y)
        
        # Обновляем Channel B: Even и Odd отдельно
        if buf_b_even:
            y, x0 = buf_b_even
            x = list(range(len(y)))
            self.line_b_even.set_data(x, y)
        
        if buf_b_odd:
            y, x0 = buf_b_odd
            x = list(range(len(y)))
            self.line_b_odd.set_data(x, y)
        
        # Автомасштабирование для обоих графиков
        self.ax1.relim()
        self.ax1.autoscale_view()
        self.ax2.relim()
        self.ax2.autoscale_view()
        
        # Обновляем метрики
        usb_stats = self.reader.get_stats()
        dma_stats = self.reader.get_dma_stats()
        elapsed = time.time() - self.start_time
        display_fps = self.display_count / elapsed if elapsed > 0 else 0

        metrics_text = (
            f"USB RX(avg): {usb_stats['rx_rate']:.1f} frames/s  |  {usb_stats['pair_rate']:.1f} pairs/s  |  {usb_stats['throughput']:.1f} KB/s\n"
            f"USB RX(inst): {usb_stats['rx_rate_inst']:.1f} frames/s  |  {usb_stats['pair_rate_inst']:.1f} pairs/s  |  {usb_stats['throughput_inst']:.1f} KB/s\n"
            f"Display:  {display_fps:.1f} FPS  |  Updates: {self.display_count}\n"
            f"Frames:   A_even={len(buf_a_even[0]) if buf_a_even else 0}, A_odd={len(buf_a_odd[0]) if buf_a_odd else 0}, "
            f"B_even={len(buf_b_even[0]) if buf_b_even else 0}, B_odd={len(buf_b_odd[0]) if buf_b_odd else 0}\n"
            f"Gaps:     A={dma_stats['gap_a']} (last={dma_stats['last_a']}), "
            f"B={dma_stats['gap_b']} (last={dma_stats['last_b']})"
        )
        self.text_metrics.set_text(metrics_text)
        
        self.display_count += 1
        
        # Перерисовка
        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()
        
        return True
    
    def run(self):
        """Запуск GUI (блокирует до закрытия окна)."""
        print("[GUI] Display thread started (normal priority)")
        
        self.timer.start()
        self.plt.show()
        
        print("[GUI] Display thread stopped")

def main():
    parser = argparse.ArgumentParser(description="BMI30 Oscilloscope - 400Hz Even/Odd Mode")
    parser.add_argument('--profile', type=int, default=0, help='Profile ID (default: 0 = 600 samples @ 400Hz)')
    parser.add_argument('--no-start', action='store_true', help='Do not send START command')
    parser.add_argument('--rx-timeout', type=int, default=100, help='USB read timeout, ms (default 100)')
    # В прошивке по умолчанию async=1 (если хост не фиксировал режим через SET_ASYNC_MODE).
    # Ранее GUI принудительно ставил async=0, и это значение сохранялось в рантайме MCU (async_mode_host_set=1),
    # из-за чего последующие START могли почти не давать кадров. Делаем async=1 дефолтом.
    parser.add_argument(
        '--async',
        dest='async_mode',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Use async mode (default: true). Use --no-async to force paired mode.'
    )
    # Допущенные параметры совместимости (игнорируются, но не ломают запуск)
    parser.add_argument('--ns', type=int, default=0, help='(compat) ignored')
    parser.add_argument('--watchdog', action='store_true', help='Log RX stalls; no stream restart unless --wdg-restart is set')
    parser.add_argument('--wdg-restart', action='store_true', help='Allow watchdog STOP+START/reopen recovery')
    parser.add_argument('--headless-secs', type=float, default=0.0, help='Run without GUI for N seconds, print RX stats and exit')
    args = parser.parse_args()
    
    print("="*60)
    print("BMI30 Oscilloscope - 400Hz Even/Odd Mode")
    print("="*60)
    print("Architecture:")
    print("  • USB Thread:     High priority, 400 Hz RX (even/odd)")
    print("  • Display Thread: Normal priority, natural Qt FPS")
    print("  • Metrics:        Shows RX rate and Display FPS")
    print("  • Visualization:  4 lines (A_even, A_odd, B_even, B_odd)")
    print("="*60)
    
    # Подключение к устройству
    dev = find_dev()
    
    if not args.no_start:
        print("[CMD] Stopping previous stream...")
        send_cmd(dev, CMD_STOP)
        time.sleep(0.2)
        
        print("[CMD] Configuring device...")
        # Mode byte: bit0=async, bit7=strict_pairing (см. прошивку VND_CMD_SET_ASYNC_MODE).
        # Для paired режима используем strict_pairing=1, чтобы гарантировать синхронную пару A+B.
        mode_byte = 0x01 if args.async_mode else 0x80
        send_cmd(dev, CMD_SET_ASYNC_MODE, [mode_byte])
        time.sleep(0.05)
        send_cmd(dev, CMD_SET_CHMODE, [0x02])  # Both channels (A+B)
        time.sleep(0.05)
        send_cmd(dev, CMD_SET_FULL_MODE, [0x01])
        time.sleep(0.05)
        send_cmd(dev, CMD_SET_PROFILE, [args.profile])
        # Переключение профиля может занимать заметное время (ADC/DMA re-init)
        time.sleep(0.25)

        # На старте иногда помогает принудительно переутвердить alt=1 и снять halt
        try:
            usb.util.claim_interface(dev, INTERFACE)
        except Exception:
            pass
        try:
            dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
        except Exception:
            pass
        try:
            dev.clear_halt(EP_IN)
        except Exception:
            pass
        try:
            dev.clear_halt(EP_OUT)
        except Exception:
            pass

        # Стабилизируем стартовое состояние: убираем прошлые ограничения размера и явно ставим базовый режим.
        send_cmd(dev, CMD_SET_TRUNC_SAMPLES, struct.pack('<H', 0))
        time.sleep(0.01)
        send_cmd(dev, CMD_SET_FRAME_SAMPLES, struct.pack('<H', 0))
        time.sleep(0.01)
        send_cmd(dev, CMD_SET_WINDOWS, _pack_windows(0, 0))
        time.sleep(0.01)
        send_cmd(dev, CMD_SET_STREAM_MODE, [0x00])  # LATEST
        time.sleep(0.02)

        print("[CMD] Sending START...")
        send_cmd(dev, CMD_START)
        time.sleep(0.2)
    
    # Создание потоков
    stop_event = threading.Event()
    
    reader = USBReader(dev, stop_event, watchdog=args.watchdog, wdg_restart=args.wdg_restart, rx_timeout_ms=args.rx_timeout)
    gui = GUIDisplay(reader, stop_event)
    
    # Запуск USB потока
    # Передаём пользовательский таймаут через lambda, чтобы не ломать сигнатуру run
    usb_thread = threading.Thread(target=lambda: reader.run(), daemon=True, name="USB-Reader")
    usb_thread.start()
    
    # Небольшая пауза для накопления первых данных
    time.sleep(0.5)
    
    try:
        if args.headless_secs and args.headless_secs > 0:
            # Без GUI: просто подождать N секунд и напечатать статистику
            time.sleep(float(args.headless_secs))
        else:
            # Запуск GUI (блокирует главный поток)
            gui.run()
    except KeyboardInterrupt:
        print("\n[MAIN] Interrupted by user")
    finally:
        print("[MAIN] Shutting down...")
        stop_event.set()
        
        # Остановка потока
        if not args.no_start:
            print("[CMD] Sending STOP...")
            # Важно: reader может сделать reopen() и заменить device handle.
            try:
                with reader.io_lock:
                    try:
                        usb.util.claim_interface(reader.dev, INTERFACE)
                    except Exception:
                        pass
                    try:
                        reader.dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)
                    except Exception:
                        pass
                    try:
                        reader.dev.clear_halt(EP_IN)
                    except Exception:
                        pass
                    try:
                        reader.dev.clear_halt(EP_OUT)
                    except Exception:
                        pass
                send_cmd(reader.dev, CMD_STOP, lock=reader.io_lock)
            except Exception as e:
                print(f"[WARN] STOP failed during shutdown: {e}")
        
        # Ждём завершения USB потока
        usb_thread.join(timeout=2.0)
        
        # Финальная статистика
        stats = reader.get_stats()
        print("\n" + "="*60)
        print("Final Statistics:")
        print("="*60)
        print(f"USB RX:      {stats['rx_count']} frames / {stats['rx_pair_count']} pairs in {stats['elapsed']:.1f}s")
        print(f"RX Rate:     {stats['rx_rate']:.2f} frames/s  |  {stats['pair_rate']:.2f} pairs/s")
        print(f"Throughput:  {stats['throughput']:.2f} KB/s")
        print(f"Display:     {gui.display_count} updates")
        elapsed_gui = time.time() - gui.start_time
        print(f"Display FPS: {gui.display_count / elapsed_gui:.2f}")
        print("="*60)
        
        # Освобождение устройства
        try:
            usb.util.release_interface(reader.dev, INTERFACE)
            usb.util.dispose_resources(reader.dev)
        except Exception:
            pass

if __name__ == '__main__':
    main()
