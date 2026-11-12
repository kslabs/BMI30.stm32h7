#!/usr/bin/env python3
import copy
import usb.core, usb.util, struct, time, threading, queue, sys, os, json, math, re
from collections import deque

VID=0xCAFE  # Автопоиск если не найдено
PID=0x4001
EP_IN=0x83  # vendor bulk IN (interface 2)
EP_OUT=0x03 # vendor bulk OUT (interface 2)
DEVICE_STATE_JSON = os.getenv("BMI30_DEVICE_STATE_JSON", "/tmp/bmi30_device_state.json")
EVT1_EVENT_NAMES = {
    0x00: "fw_info",
    0x01: "temp_c",
    0x02: "mcu_adc",
    0x10: "optic_state",
    0x11: "sync_state",
    0x12: "mode_state",
    0x13: "error_state",
    0x14: "sensor_map",
}
EVT1_PAYLOAD_LENGTHS = {
    0x00: 47,
    0x01: 2,
    0x02: 16,
    0x10: 8,
    0x11: 16,
    0x12: 16,
    0x13: 16,
    0x14: 40,
}
STAT_PACKET_MIN_LEN = 64
STAT_PACKET_V4_LEN = 95
STAT_PACKET_V5_LEN = 136
STAT_PACKET_V6_LEN = 137
RS485_IDENT_V1_LEN = 32
RS485_IDENT_V2_LEN = 40
RS485_SENSOR_V1_LEN = 16
RPI_ID_RE = re.compile(r"^[0-9A-F]{9}$")


def parse_communication_recovery(data):
    packet = bytes(data)
    if len(packet) != 24 or packet[:4] != b'COM1' or packet[4] != 1:
        return None
    if packet[5] not in (0, 1) or packet[6] not in (0, 1) or packet[7] != 0:
        return None
    generation, hold_ms = struct.unpack_from('<HH', packet, 8)
    if packet[5] and not generation:
        return None
    return {'version': 1, 'pending': bool(packet[5]), 'durable': bool(packet[6]),
            'generation': generation, 'hold_ms': hold_ms,
            'stm32_uid96': packet[12:24].hex().upper()}


def _stat_packet_len_from_header(data, offset: int = 0) -> int:
    """Return the wire length for the STAT layout advertised by its version byte.

    Firmware v4 emits 95 bytes, v5 emits 136 bytes, and v6 emits 137 bytes.
    The extra v6 byte makes sync_status_bytes directly addressable by every
    persistent RS485 device_id in the complete 0..31 range.
    """
    try:
        version = int(data[int(offset) + 4]) & 0xFF
    except Exception:
        return STAT_PACKET_V6_LEN
    if version >= 6:
        return STAT_PACKET_V6_LEN
    if version == 5:
        return STAT_PACKET_V5_LEN
    if version == 4:
        return STAT_PACKET_V4_LEN
    return STAT_PACKET_MIN_LEN

def _float_env(name: str, dflt: float) -> float:
    try:
        return float(os.getenv(name, str(dflt)))
    except Exception:
        return dflt

SERVICE_HEARTBEAT_S = max(1.0, _float_env("BMI30_SERVICE_HEARTBEAT_S", 30.0))
SERVICE_LAG_S = max(2.0 * SERVICE_HEARTBEAT_S, _float_env("BMI30_SERVICE_LAG_S", 2.0 * SERVICE_HEARTBEAT_S))
SERVICE_LAG_WRITE_S = max(5.0, _float_env("BMI30_SERVICE_LAG_WRITE_S", 10.0))

CMD_SET_PROFILE   = 0x14
CMD_SET_FULL_MODE = 0x13
CMD_SET_ROI_US    = 0x15
CMD_START_STREAM  = 0x20
CMD_STOP_STREAM   = 0x21
CMD_GET_STATUS    = 0x30
CMD_SET_TX_ENABLE = 0x33
CMD_HOST_RX_ACK   = 0x36
CMD_SET_FRAME_SAMPLES = 0x17
CMD_ASYNC         = 0x18  # 0=strict pairs A/B, 1=independent A/B
CMD_SET_WINDOWS    = 0x10  # payload: <HHHH> start0,len0,start1,len1
CMD_SET_STREAM_MODE = 0x1A  # payload: <B> 0=LATEST(600), 1=LOSSLESS_ROI(200)
CMD_SET_DC_SPEED = 0x1F
DC_SETTLE_MAX_MS = 86_400_000
CMD_DEVICE_RESET = 0x22
CMD_SAVE_DC_TO_FLASH = 0x2B
CMD_TOGGLE_TIM2CH3_INV = 0x32
CMD_HOST_RX_CLEAR = 0x37
CMD_SET_DET_ADC = 0x3C
CMD_GET_DC_CONFIG = 0x3A
CMD_SET_RS485_ID = 0x3D
CMD_SET_RS485_IP = 0x3E
CMD_REQUEST_RS485_IDENT = 0x3F
CMD_GET_RS485_IDENT = 0x40
CMD_SET_RPI_INFO = 0x42
CMD_GET_RS485_SENSOR = 0x43
CMD_SET_OPTIC_REACTION_SOURCE = 0x44
CMD_SET_ALT       = 0x31  # optional vendor EP0 control OUT to set alt
CMD_SOFT_RESET   = 0x7E  # EP0 vendor control OUT, no data
CMD_DEEP_RESET   = 0x7F  # EP0 vendor control OUT, no data

MAGIC=0xA55A
HDR_FMT='<HBBI I H H I I I H H'  # manual split
# We'll unpack manually due to spacing: (magic,ver,flags,seq,timestamp,total_samples,zone_count,zone1_offset,zone1_length,reserved,reserved2,crc16)
HDR_SIZE=32

VF_ADC0   =0x01
VF_ADC1   =0x02
VF_CRC    =0x04

def _u8_at(data: bytes, off: int):
    try:
        if len(data) > off:
            return int(data[off]) & 0xFF
    except Exception:
        pass
    return None

def _u16_at(data: bytes, off: int):
    try:
        if len(data) >= off + 2:
            return int.from_bytes(data[off:off + 2], "little")
    except Exception:
        pass
    return None

def _u32_at(data: bytes, off: int):
    try:
        if len(data) >= off + 4:
            return int.from_bytes(data[off:off + 4], "little")
    except Exception:
        pass
    return None

def _i16_at(data: bytes, off: int):
    try:
        if len(data) >= off + 2:
            return int.from_bytes(data[off:off + 2], "little", signed=True)
    except Exception:
        pass
    return None

def _status_byte_fields(value, label: str = "", node_id: int | None = None) -> dict:
    try:
        b = int(value) & 0xFF
    except Exception:
        b = 0
    public_node_id = b & 0x1F
    out = {
        "status_byte": b,
        "status_hex": f"0x{b:02X}",
        "selector": public_node_id,
        "status_node_id": public_node_id,
        "optic_active": bool(b & 0x20),
        "optic_indication_allow": bool(b & 0x20),
        "detadc1": bool(b & 0x40),
        "detadc2": bool(b & 0x80),
    }
    if label:
        out["label"] = label
    if node_id is not None:
        out["node_id"] = int(node_id)
        out["node_id_matches_status"] = (public_node_id == 0 or public_node_id == int(node_id))
    return out

def _parse_ipv4(value) -> tuple[int, int, int, int] | None:
    if isinstance(value, (bytes, bytearray, list, tuple)) and len(value) == 4:
        try:
            parts = tuple(int(x) & 0xFF for x in value)
            return parts if len(parts) == 4 else None
        except Exception:
            return None
    try:
        text = str(value or "").strip()
    except Exception:
        return None
    if not text:
        return None
    parts_s = text.split(".")
    if len(parts_s) != 4:
        return None
    try:
        parts = tuple(int(p) for p in parts_s)
    except Exception:
        return None
    if any(p < 0 or p > 255 for p in parts):
        return None
    return parts  # type: ignore[return-value]

def _normalize_rpi_id(value) -> str:
    text = str(value or "").strip().upper()
    return text if RPI_ID_RE.fullmatch(text) else ""


def _parse_rs485_ident_packet(packet: bytes, requested_node_id: int = 0xFF) -> dict | None:
    bs = bytes(packet or b"")
    if len(bs) < RS485_IDENT_V1_LEN or bs[:4] not in {b"RID1", b"RID2"}:
        return None
    try:
        wire_version = int(bs[4]) & 0xFF
        v2 = len(bs) >= RS485_IDENT_V2_LEN and (bs[:4] == b"RID2" or wire_version >= 2)
        node_id = int(bs[5]) & 0xFF
        if node_id > 31:
            return None
        requested_id = int(requested_node_id) & 0xFFFF
        if 0 <= requested_id <= 31 and node_id != requested_id:
            return None
        flags = int.from_bytes(bs[6:8], "little")
        if (flags & 0x07FF) == 0:
            return None
        short_raw = bytes(bs[8:18]).split(b"\x00", 1)[0]
        short_id = short_raw.decode("ascii", "ignore").strip().upper()
        short_id = short_id.replace("?", "").strip() or ""
        if v2:
            rpi_raw = bytes(bs[18:28]).split(b"\x00", 1)[0]
            rpi_id = _normalize_rpi_id(rpi_raw.decode("ascii", "ignore"))
            ip_parts = [int(x) & 0xFF for x in bs[28:32]]
            seen_page_mask = int.from_bytes(bs[32:36], "little")
            last_ms = int.from_bytes(bs[36:40], "little")
            legacy_rpi_number = None
        else:
            rpi_id = ""
            ip_parts = [int(x) & 0xFF for x in bs[18:22]]
            seen_page_mask = int.from_bytes(bs[22:26], "little")
            last_ms = int.from_bytes(bs[26:30], "little")
            legacy_rpi_number = int.from_bytes(bs[30:32], "little")
        ip = ".".join(str(x) for x in ip_parts)
        ip_valid = bool(flags & 0x0002) and any(ip_parts)
        is_master = bool(flags & 0x0080)
        now = time.time()
        entry = {
            "node_id": node_id,
            "requested_node_id": requested_id,
            "node_id_matches_status": bool(requested_id > 31 or node_id == requested_id),
            "version": wire_version,
            "wire_format": "RID2" if v2 else "RID1",
            "flags": flags,
            "flags_hex": f"0x{flags:04X}",
            "short_id": short_id,
            "host_id": short_id,
            "short_valid": bool(flags & 0x0001),
            "ip": ip if ip_valid else "",
            "ip4": ip_parts,
            "ip_valid": ip_valid,
            "ip_last": str(ip_parts[-1]) if ip_valid else "",
            "rpi_id": rpi_id if (flags & 0x0200) else "",
            "rpi_id_valid": bool(flags & 0x0200) and bool(rpi_id),
            "rpi_number": legacy_rpi_number if (not v2 and (flags & 0x0200)) else None,
            "rpi_number_valid": bool(not v2 and (flags & 0x0200)),
            "device_id_assigned": bool(flags & 0x0400),
            "master": is_master,
            "role": "master" if is_master else "slave",
            "node_conflict": bool(flags & 0x0100),
            "complete": bool(flags & 0x0004),
            "local": bool(flags & 0x0008),
            "recent": bool(flags & 0x0010),
            "scan_active": bool(flags & 0x0020),
            "seen_page_mask": seen_page_mask,
            "last_ms": last_ms,
            "host_updated_at": now,
            "host_updated_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        }
        label_id = str(entry.get("rpi_id") or short_id or "")
        if ip_valid:
            entry["group_label"] = f"{label_id}/{ip_parts[-1]}" if label_id else str(ip_parts[-1])
        elif label_id:
            entry["group_label"] = label_id
        return entry
    except Exception:
        return None

def _parse_rs485_sensor_packet(packet: bytes, requested_node_id: int = 0xFF) -> dict | None:
    """Parse the fixed SNS1 snapshot returned by GET_RS485_SENSOR (0x43)."""
    bs = bytes(packet or b"")
    if len(bs) != RS485_SENSOR_V1_LEN or bs[:4] != b"SNS1":
        return None
    try:
        version = int(bs[4]) & 0xFF
        node_id = int(bs[5]) & 0xFF
        flags = int.from_bytes(bs[6:8], "little")
        sensor_bits = int.from_bytes(bs[8:10], "little")
        requested_id = int(requested_node_id) & 0xFFFF
        if version != 1 or node_id > 31 or not bool(flags & 0x0001):
            return None
        if 0 <= requested_id <= 31 and node_id != requested_id:
            return None
        if 0 <= requested_id <= 31 and bool(flags & 0x0002):
            return None
        master = bool(flags & 0x0008)
        detadc1 = bool(sensor_bits & 0x0002)
        detadc2 = bool(sensor_bits & 0x0004)
        optic_raw = bool(sensor_bits & 0x0001)
        # Firmware 1.2.13 publishes the same processed physical optic state for
        # master and slave rows. DetADC bits are independent controlled sensors.
        optic_physical = optic_raw
        now = time.time()
        return {
            "node_id": node_id,
            "status_node_id": node_id,
            "requested_node_id": requested_id,
            "node_id_matches_status": bool(requested_id > 31 or node_id == requested_id),
            "packet_validated": True,
            "version": version,
            "wire_format": "SNS1",
            "flags": flags,
            "flags_hex": f"0x{flags:04X}",
            "sensor_bits": sensor_bits,
            "sensor_bits_hex": f"0x{sensor_bits:04X}",
            "optic_active_raw": optic_raw,
            "optic_active": optic_physical,
            "optic_active_physical": optic_physical,
            "optic_indication_allow": optic_raw,
            "optic_reject_reason": "",
            "detadc1": detadc1,
            "detadc2": detadc2,
            "last_sensor_index": int(bs[10]) & 0xFF,
            "last_change_ms": int.from_bytes(bs[12:16], "little"),
            "local": bool(flags & 0x0002),
            "recent": bool(flags & 0x0004),
            "seen": bool(flags & 0x0004),
            "master": master,
            "online": bool(flags & 0x0004),
            "sensor_updated_at": now,
            "sensor_updated_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        }
    except Exception:
        return None

def _merge_dict(base: dict, patch: dict) -> dict:
    out = dict(base) if isinstance(base, dict) else {}
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_dict(out[key], value)
        else:
            out[key] = value
    return out

def crc16_ccitt_false(data:bytes, init=0xFFFF):
    crc=init
    for b in data:
        crc ^= b<<8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc<<1) ^ 0x1021
            else:
                crc <<=1
            crc &=0xFFFF
    return crc

class Frame:
    __slots__=("seq","timestamp","adc_id","flags","samples","payload","reserved","reserved2","ver")
    def __init__(self,seq,timestamp,adc_id,flags,samples,payload,reserved=0,reserved2=0,ver=0):
        self.seq=seq; self.timestamp=timestamp; self.adc_id=adc_id; self.flags=flags; self.samples=samples; self.payload=payload
        self.reserved = reserved
        self.reserved2 = reserved2
        self.ver = ver

class StereoAssembler:
    def __init__(self, relaxed: bool | None = None, relaxed_order: bool | None = None, ts_pairing: bool | None = None, ts_tol: float | None = None, independent: bool | None = None):
        self.bufA = {}
        self.bufB = {}
        # Очереди по умолчанию были маленькие (256) и при переполнении silently-drop'али пары.
        # Это может выглядеть как GAP на хосте даже при идеальном USB. Делаем размер настраиваемым и считаем потери.
        def _int_env(name: str, dflt: int) -> int:
            try:
                v = int(os.getenv(name, str(dflt)))
                return max(1, v)
            except Exception:
                return dflt
        self.q = queue.Queue(maxsize=_int_env('BMI30_ASM_Q_MAX', 128))
        self.drop_pairs = 0
        self.drop_a = 0
        self.drop_b = 0
        # Relaxed mode: allow pairing with seq+1/seq-1 if exact seq not present
        # If param is None, respect environment variables (backwards compatibility)
        try:
            if relaxed is None:
                self.relaxed = str(os.getenv('BMI30_RELAXED_PAIRING', '1')).lower() not in ('0', 'false', 'no')
            else:
                self.relaxed = bool(relaxed)
        except Exception:
            self.relaxed = True
        # Дополнительный сверх-терпимый режим: если строгая состыковка по seq не найдена,
        # будем собирать пары по порядку прихода (первый A с первым B), игнорируя seq.
        # Это полезно, если прошивка использует независимый счётчик seq для каналов.
        try:
            if relaxed_order is None:
                self.relaxed_order = str(os.getenv('BMI30_RELAXED_ORDER', '1')).lower() not in ('0', 'false', 'no')
            else:
                self.relaxed_order = bool(relaxed_order)
        except Exception:
            self.relaxed_order = True
        # Timestamp-based pairing fallback: if exact seq matching fails, try pairing A/B by closeness of timestamps
        try:
            if ts_pairing is None:
                self.ts_pairing = str(os.getenv('BMI30_TS_PAIRING', '1')).lower() not in ('0', 'false', 'no')
            else:
                self.ts_pairing = bool(ts_pairing)
        except Exception:
            self.ts_pairing = True
        try:
            if ts_tol is None:
                # default tolerance in seconds (1ms)
                self.ts_tol = float(os.getenv('BMI30_TS_TOL', '0.001'))
            else:
                self.ts_tol = float(ts_tol)
        except Exception:
            self.ts_tol = 0.001
        self._ts_pair_count = 0
        self._seq_neighbor_pairs = 0
        # Independent channels mode: do not attempt to pair A/B, instead expose per-channel queues
        try:
            if independent is None:
                # Channels are independent by default — prefer not to auto-pair.
                self.independent = str(os.getenv('BMI30_INDEPENDENT_CHANNELS', '1')).lower() not in ('0', 'false', 'no')
            else:
                self.independent = bool(independent)
        except Exception:
            self.independent = False
        if self.independent:
            # queues for each channel (A=0,B=1)
            self.qA = queue.Queue(maxsize=_int_env('BMI30_ASM_QA_MAX', 128))
            self.qB = queue.Queue(maxsize=_int_env('BMI30_ASM_QB_MAX', 128))
    def _emit_pair(self, a: 'Frame', b: 'Frame'):
        try:
            self.q.put_nowait((a, b))
        except Exception:
            # queue.Full или другое: попробуем вытеснить один старый элемент и вставить новый
            try:
                self.drop_pairs += 1
                _ = self.q.get_nowait()
                self.q.put_nowait((a, b))
            except Exception:
                pass
    def _emit_frame_a(self, a: 'Frame'):
        try:
            self.qA.put_nowait(a)
        except Exception:
            try:
                self.drop_a += 1
                _ = self.qA.get_nowait()
                self.qA.put_nowait(a)
            except Exception:
                pass
    def _emit_frame_b(self, b: 'Frame'):
        try:
            self.qB.put_nowait(b)
        except Exception:
            try:
                self.drop_b += 1
                _ = self.qB.get_nowait()
                self.qB.put_nowait(b)
            except Exception:
                pass
    def push(self,f:Frame):
        # independent mode: just enqueue frames per-channel
        if self.independent:
            if f.adc_id == 0:
                self._emit_frame_a(f)
            else:
                self._emit_frame_b(f)
            # keep some minimal buffer cleanup
            if len(self.bufA) > 2048:
                self.bufA.clear()
            if len(self.bufB) > 2048:
                self.bufB.clear()
            return

        if f.adc_id==0:
            self.bufA[f.seq]=f
            if f.seq in self.bufB:
                a=self.bufA.pop(f.seq); b=self.bufB.pop(f.seq)
                self._emit_pair(a,b)
            elif ((f.seq-1) in self.bufB or (f.seq+1) in self.bufB) and (self.relaxed or self.ts_pairing):
                # allow neighbor seq pairing (seq-1 or seq+1) when relaxed or when ts close
                if (f.seq-1) in self.bufB:
                    candidate_key = f.seq-1
                else:
                    candidate_key = f.seq+1
                candidate = self.bufB.get(candidate_key)
                if candidate is not None and not self.relaxed:
                    # strict mode requires timestamp closeness
                    if getattr(candidate, 'timestamp', None) is not None and getattr(f, 'timestamp', None) is not None:
                        dt = abs((candidate.timestamp - f.timestamp) / 1_000_000.0)
                        if dt > self.ts_tol:
                            candidate = None
                if candidate is not None:
                    a = self.bufA.pop(f.seq)
                    b = self.bufB.pop(candidate_key)
                    self._emit_pair(a, b)
                    self._seq_neighbor_pairs += 1
                elif self.relaxed and (f.seq+1) in self.bufB:
                    # relaxed fallback: pair with seq+1 if present
                    a = self.bufA.pop(f.seq);
                    b = self.bufB.pop(f.seq+1)
                    self._emit_pair(a,b)
            elif self.relaxed_order and self.bufB:
                # Сверх-терпимый: возьмём ближайший по seq B (или просто первый) и спарим
                try:
                    # Найдём ключ B c минимальной разницей по seq
                    kb = min(self.bufB.keys(), key=lambda k: abs(int(k) - int(f.seq)))
                except Exception:
                    kb = next(iter(self.bufB.keys()))
                a=self.bufA.pop(f.seq)
                b=self.bufB.pop(kb)
                self._emit_pair(a,b)
            elif self.ts_pairing and self.bufB:
                # Last-resort: try to find B whose timestamp is close to this A
                try:
                    cand = None
                    best_dt = None
                    for k,bv in self.bufB.items():
                        if getattr(bv, 'timestamp', None) is None or getattr(f, 'timestamp', None) is None:
                            continue
                        dt = abs((bv.timestamp - f.timestamp) / 1_000_000.0)
                        if dt <= self.ts_tol and (best_dt is None or dt < best_dt):
                            cand = k; best_dt = dt
                    if cand is not None:
                        a = self.bufA.pop(f.seq)
                        b = self.bufB.pop(cand)
                        self._emit_pair(a,b)
                        self._ts_pair_count += 1
                        if self._ts_pair_count % 100 == 0:
                            try:
                                print(f"[ASM] timestamp-paired {self._ts_pair_count} times; last dt={best_dt:.6f}s", flush=True)
                            except Exception:
                                pass
                except Exception:
                    pass
        else:
            self.bufB[f.seq]=f
            if f.seq in self.bufA:
                a=self.bufA.pop(f.seq); b=self.bufB.pop(f.seq)
                self._emit_pair(a,b)
            elif ((f.seq-1) in self.bufA or (f.seq+1) in self.bufA) and (self.relaxed or self.ts_pairing):
                candidate_key = None
                if (f.seq-1) in self.bufA:
                    candidate_key = f.seq-1
                elif (f.seq+1) in self.bufA:
                    candidate_key = f.seq+1
                candidate = self.bufA.get(candidate_key)
                if candidate is not None and not self.relaxed:
                    if getattr(candidate,'timestamp',None) is not None and getattr(f,'timestamp',None) is not None:
                        dt = abs((candidate.timestamp - f.timestamp) / 1_000_000.0)
                        if dt > self.ts_tol:
                            candidate = None
                if candidate is not None:
                    a=self.bufA.pop(candidate_key); b=self.bufB.pop(f.seq)
                    self._emit_pair(a,b)
                    self._seq_neighbor_pairs += 1
                elif self.relaxed and (f.seq-1) in self.bufA:
                    a=self.bufA.pop(f.seq-1); b=self.bufB.pop(f.seq)
                    self._emit_pair(a,b)
            elif self.relaxed_order and self.bufA:
                try:
                    ka = min(self.bufA.keys(), key=lambda k: abs(int(k) - int(f.seq)))
                except Exception:
                    ka = next(iter(self.bufA.keys()))
                a=self.bufA.pop(ka)
                b=self.bufB.pop(f.seq)
                self._emit_pair(a,b)
            elif self.ts_pairing and self.bufA:
                # Last-resort: try to find A whose timestamp is close to this B
                try:
                    cand = None
                    best_dt = None
                    for k,av in self.bufA.items():
                        if getattr(av, 'timestamp', None) is None or getattr(f, 'timestamp', None) is None:
                            continue
                        dt = abs((av.timestamp - f.timestamp) / 1_000_000.0)
                        if dt <= self.ts_tol and (best_dt is None or dt < best_dt):
                            cand = k; best_dt = dt
                    if cand is not None:
                        a = self.bufA.pop(cand)
                        b = self.bufB.pop(f.seq)
                        self._emit_pair(a,b)
                        self._ts_pair_count += 1
                        if self._ts_pair_count % 100 == 0:
                            try:
                                print(f"[ASM] timestamp-paired {self._ts_pair_count} times; last dt={best_dt:.6f}s", flush=True)
                            except Exception:
                                pass
                except Exception:
                    pass
        # Простейшая защита от разрастания при редких несостыковках
        if len(self.bufA) > 2048:
            self.bufA.clear()
        if len(self.bufB) > 2048:
            self.bufB.clear()

class USBStream:
    def __init__(self, profile=1, full=True, vid=VID, pid=PID, interactive=False, allow_any=False, iface_prefer=None, test_as_data: bool=False, frame_samples: int | None = None, fast_mode: bool | None = None, assembler_relaxed: bool | None = None, assembler_relaxed_order: bool | None = None, assembler_ts_pairing: bool | None = None, assembler_ts_tol: float | None = None, assembler_independent: bool | None = None, tx_enabled: bool | None = None, tx_phase_config_path=None):
        self._running = True
        # OUT serialization is needed during constructor-time startup too.
        self._ep_out_lock = threading.Lock()
        self._ep0_lock = threading.RLock()
        self._tx_phase_config_path = tx_phase_config_path
        self._tx_phase_control = None
        self._close_lock = threading.Lock()
        self._rs485_ident_lock = threading.Lock()
        # EP0 status can arrive during constructor-time alt-setting setup, well
        # before the Bulk reader starts.  Initialise the cache publisher here so
        # those early updates are retained, but start its I/O worker only after
        # USB setup succeeds.
        self.device_state_cache = {}
        self._init_device_state_cache_writer()
        self.dev=None
        self.intf=None
        self.profile = profile
        self.full = full
        self.test_as_data = test_as_data
        self.frame_samples = frame_samples
        # Persistent host request supplied by the owning engine.  STOP_STREAM
        # clears the device-side request, so every transport START must restore
        # this value afterwards.
        self.desired_tx_enabled = bool(tx_enabled) if tx_enabled is not None else False
        # Быстрый режим: жёстко задаём FULL/PROFILE/NS перед START и включаем keepalive/restart пороги
        try:
            fm_env = os.getenv('BMI30_FAST_MODE', None)
            fm_env_val = None if fm_env is None else (str(fm_env).lower() not in ('0','false','no'))
        except Exception:
            fm_env_val = None
        # По умолчанию быстрый режим ВКЛЮЧЕН (всегда рабочий режим)
        self.fast_mode = True if fast_mode is None and fm_env_val is None else bool(fast_mode if fast_mode is not None else fm_env_val)
        try:
            # Passive mode: host opens USB and reads status/data, but does not
            # proactively reconfigure or start the STM32 on healthy connect.
            self.passive_connect = str(os.getenv('BMI30_PASSIVE_CONNECT', '1')).lower() not in ('0', 'false', 'no')
        except Exception:
            self.passive_connect = True

        # Save assembler pairing overrides (None -> use env)
        self._asm_relaxed_override = assembler_relaxed
        self._asm_relaxed_order_override = assembler_relaxed_order
        self._asm_ts_pair_override = assembler_ts_pairing
        self._asm_ts_tol_override = assembler_ts_tol
        self._asm_independent_override = assembler_independent
        # Поведение можно ослабить через переменные окружения (уменьшаем «тряску» EP0/ALT/CDC у текущей прошивки)
        try:
            self.ignore_ready_flags = str(os.getenv('BMI30_IGNORE_ALT_READY','1')).lower() not in ('0','false','no')
        except Exception:
            self.ignore_ready_flags = True
        try:
            # Общий флаг рестарта; фактический локальный recovery ниже по умолчанию выключен.
            self.disable_restart = str(os.getenv('BMI30_DISABLE_RESTART','0')).lower() not in ('0','false','no')
        except Exception:
            self.disable_restart = False
        try:
            # Current firmware/host policy: the GUI/upper layer owns recovery because
            # it can take two EP0 STAT snapshots and restore the user's last mode.
            self.local_recovery_enabled = str(os.getenv('BMI30_USBSTREAM_LOCAL_RECOVERY', '0')).lower() not in ('0', 'false', 'no')
        except Exception:
            self.local_recovery_enabled = False
        try:
            self.disable_cdc_kick = str(os.getenv('BMI30_DISABLE_CDC_KICK','1')).lower() not in ('0','false','no')
        except Exception:
            self.disable_cdc_kick = True
        # Пороги keepalive/restart (сек)
        def _float_env(name: str, dflt: float) -> float:
            try:
                return float(os.getenv(name, str(dflt)))
            except Exception:
                return dflt
        # Базовые значения (мягкие). В fast_mode делаем более агрессивные
        self.keepalive_sec = _float_env('BMI30_KEEPALIVE_SEC', 2.0)
        self.restart_after = _float_env('BMI30_RESTART_AFTER', 4.0)
        self.restart_min_interval = _float_env('BMI30_RESTART_MIN_INTERVAL', 3.0)
        if self.fast_mode:
            # Быстрый режим: чаще пингуем и быстрее рестартуем
            self.keepalive_sec = _float_env('BMI30_KEEPALIVE_SEC', 1.0)
            self.restart_after = _float_env('BMI30_RESTART_AFTER', 2.5)
            self.restart_min_interval = _float_env('BMI30_RESTART_MIN_INTERVAL', 2.0)
            if self.local_recovery_enabled:
                self.disable_restart = False
        # функция сканирования всех интерфейсов устройства
        def scan_device(dev):
            infos=[]
            for cfg in dev:
                for intf in cfg:  # Interface
                    # intf сам уже alt setting (PyUSB объединяет)
                    eps=[e.bEndpointAddress for e in intf.endpoints()]
                    if EP_IN in eps and EP_OUT in eps:
                        infos.append((cfg.bConfigurationValue, intf.bInterfaceNumber, eps))
            return infos
        # 1. Ищем только указанный VID/PID (строго). Если нужно любое устройство, передать allow_any=True
        if vid and pid:
            exact_list=list(usb.core.find(find_all=True, idVendor=vid, idProduct=pid))
            if exact_list:
                # выбираем первое; если iface_prefer задан – ищем интерфейс по номеру
                self.dev=exact_list[0]
                matches=scan_device(self.dev)
                if not matches:
                    raise SystemExit(f"Найдено устройство, но нет подходящих интерфейсов с EP {hex(EP_IN)}/{hex(EP_OUT)}")
                if iface_prefer is not None:
                    m2=[m for m in matches if m[1]==iface_prefer]
                    if m2: matches=m2
                cfg_val,intf_num,eps=matches[0]
                self.intf_sel=(cfg_val,intf_num)
                print(f"[open] exact {hex(self.dev.idVendor)}:{hex(self.dev.idProduct)} cfg={cfg_val} intf={intf_num} eps={list(map(hex,eps))}")
        if self.dev is None and not allow_any:
            raise SystemExit(f"Device {hex(vid)}:{hex(pid)} not present. Подключите/прошивка? Либо запустите с allow_any=True для авто-поиска других.")
        # 2. Автопоиск по любому устройству (если разрешено)
        if self.dev is None and allow_any:
            cand=[]
            for d in usb.core.find(find_all=True):
                try: matches=scan_device(d)
                except Exception: continue
                if matches: cand.append((d,matches))
            if not cand:
                raise SystemExit("Нет ни одного устройства с подходящими endpoint'ами")
            if len(cand)>1 and interactive:
                print('[auto-any] Кандидаты:')
                for i,(d,ms) in enumerate(cand):
                    print(f"  {i}: {hex(d.idVendor)}:{hex(d.idProduct)} ifaces={[ (c,i2,[hex(e) for e in eps]) for (c,i2,eps) in ms ]}")
                sel=None
                while sel is None:
                    try:
                        sel=int(input('Выбор: '))
                        if sel<0 or sel>=len(cand): sel=None
                    except Exception: sel=None
                self.dev,matches=cand[sel]
            else:
                self.dev,matches=cand[0]
            self.intf_sel=(matches[0][0], matches[0][1])
            print(f"[auto-any] selected {hex(self.dev.idVendor)}:{hex(self.dev.idProduct)} cfg={self.intf_sel[0]} intf={self.intf_sel[1]}")
        # 3. (опционально) Жёсткий reset USB-устройства по требованию окружения
        try:
            _reset_on_connect = str(os.getenv('BMI30_USB_RESET_ON_CONNECT','0')).lower() not in ('0','false','no')
        except Exception:
            _reset_on_connect = False
        if _reset_on_connect:
            try:
                print('[usb] dev.reset() on connect (BMI30_USB_RESET_ON_CONNECT=1)')
                self.dev.reset()
                time.sleep(0.2)
            except Exception as e:
                print('[usb] dev.reset() failed:', e)

        # 4. Установка конфигурации и отделение драйвера (если занято)
        tried_detach=False
        cfg_val=self.intf_sel[0]
        chosen_intf_num = self.intf_sel[1]
        # Если нужная конфигурация уже активна — не трогаем
        try:
            cfg_active = self.dev.get_active_configuration()
        except usb.core.USBError:
            cfg_active = None
        need_set_config = not (cfg_active and cfg_active.bConfigurationValue == cfg_val)
        if need_set_config:
            while True:
                try:
                    # Мягко отсоединим драйвер только у выбранного vendor-интерфейса
                    try:
                        if self.dev.is_kernel_driver_active(chosen_intf_num):
                            try:
                                self.dev.detach_kernel_driver(chosen_intf_num)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    self.dev.set_configuration(cfg_val)
                    break
                except usb.core.USBError as e:
                    if getattr(e,'errno',None)==16 and not tried_detach: # EBUSY
                        tried_detach=True
                        # Повторим попытку, предварительно отключив драйвер только выбранного интерфейса
                        try:
                            if self.dev.is_kernel_driver_active(chosen_intf_num):
                                try:
                                    self.dev.detach_kernel_driver(chosen_intf_num)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        continue
                    if getattr(e,'errno',None)==13:
                        print('[perm] Недостаточно прав (udev?). Создайте правило:')
                        print(f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{self.dev.idVendor:04x}", ATTR{{idProduct}}=="{self.dev.idProduct:04x}", MODE="0666"')
                    raise
        # Ensure interface is released before claiming
        try:
            usb.util.release_interface(self.dev, chosen_intf_num)
        except Exception:
            pass
        # Claim the interface for bulk transfers
        try:
            usb.util.claim_interface(self.dev, chosen_intf_num)
        except Exception:
            pass
        # Clear halt on endpoints to ensure they are ready
        try:
            usb.util.clear_halt(self.dev, EP_OUT)
            usb.util.clear_halt(self.dev, EP_IN)
        except Exception:
            pass
        # 5. Получаем интерфейс и endpoints
        try:
            cfg=self.dev.get_active_configuration()
        except usb.core.USBError as ge:
            print('[err] cannot get active configuration:', ge)
            raise
        intf_num=self.intf_sel[1]
        self.intf_num = intf_num
                # Явно выбираем altsetting=1 для Vendor IF#2, где находятся bulk EP (новая прошивка)
        self.current_alt = 0
        if not self._ensure_alt(intf_num, desired_alt=1):
            raise RuntimeError(f'unable to set vendor interface alt=1 for bulk endpoints {hex(EP_OUT)}/{hex(EP_IN)}')
        # Найдём дескриптор интерфейса с alt=0
        intf = usb.util.find_descriptor(
            cfg,
            custom_match=lambda i: getattr(i, 'bInterfaceNumber', -1) == intf_num and getattr(i, 'bAlternateSetting', -1) == 0
        )
        if intf is None:
            # Попробуем найти любой alt для указанного интерфейса (fallback)
            intf = usb.util.find_descriptor(cfg, bInterfaceNumber=intf_num)
        if intf is None:
            raise SystemExit('Interface disappeared')
        # But take EP from alt=1
        intf_alt1 = usb.util.find_descriptor(
            cfg,
            custom_match=lambda i: getattr(i, 'bInterfaceNumber', -1) == intf_num and getattr(i, 'bAlternateSetting', -1) == 1
        )
        if intf_alt1:
            intf = intf_alt1
        # Запомним актуальный altsetting выбранного интерфейса
        try:
            self.alt_setting = getattr(intf, 'bAlternateSetting', 0)
        except Exception:
            self.alt_setting = 0
        # вытащим endpoints из текущего altsetting
        self.ep_in=None; self.ep_out=None
        for e in intf.endpoints():
            if e.bEndpointAddress==EP_IN: self.ep_in=e
            if e.bEndpointAddress==EP_OUT: self.ep_out=e
        if not (self.ep_in and self.ep_out):
            # Fallback: if EP not found and we didn't try alt=1 yet, try setting alt=1
            if self.current_alt != 1:
                print(f"[fallback] EP not found in alt={self.alt_setting}, trying alt=1")
                self._ensure_alt(intf_num, desired_alt=1)
                if self.current_alt == 1:
                    intf = usb.util.find_descriptor(
                        cfg,
                        custom_match=lambda i: getattr(i, 'bInterfaceNumber', -1) == intf_num and getattr(i, 'bAlternateSetting', -1) == 1
                    )
                    if intf:
                        self.ep_in=None; self.ep_out=None
                        for e in intf.endpoints():
                            if e.bEndpointAddress==EP_IN: self.ep_in=e
                            if e.bEndpointAddress==EP_OUT: self.ep_out=e
                        if self.ep_in and self.ep_out:
                            self.alt_setting = 1
                            print(f"[fallback] EP found in alt=1")
                            # Try to set alt=1 now
                            try:
                                self.dev.set_interface_altsetting(interface=intf_num, alternate_setting=1)
                                self.current_alt = 1
                                print(f"[alt] set alt=1 after EP found")
                            except Exception as e:
                                print(f"[alt] failed to set alt=1 after EP found: {e}")
                        else:
                            raise SystemExit(f'Endpoints {hex(EP_IN)}/{hex(EP_OUT)} not found even in alt=1')
                    else:
                        raise SystemExit('Interface alt=1 not found')
                else:
                    raise SystemExit(f'Endpoints {hex(EP_IN)}/{hex(EP_OUT)} not found in chosen interface/alt={self.alt_setting}')
            else:
                raise SystemExit(f'Endpoints {hex(EP_IN)}/{hex(EP_OUT)} not found in chosen interface/alt={self.alt_setting}')
        # Попробуем подготовить «чистый старт»: STOP, очистка состояний EP, переустановка altsetting
        try:
            # По умолчанию НЕ делаем «чистый старт» (STOP/alt toggle), чтобы не мешать прошивке
            _disable_clean = str(os.getenv('BMI30_CLEAN_START','0')).lower() in ('0','false','no')
        except Exception:
            _disable_clean = True
        if not _disable_clean:
            try:
                self._prepare_clean_start(stop_first=True)
            except Exception:
                pass

        # инициализация потоков (внутри __init__)
        self.disconnected = False
        self.connected_t = time.time()
        self.last_rx_t = self.connected_t
        self.last_restart_t = 0.0
        self._rx_flush_requested = False  # сигнал _rx_loop очистить буфер после рестарта
        self.last_stat = None
        self.last_stat_t = 0.0
        self.last_evt1 = None
        self.device_state_cache = getattr(self, "device_state_cache", {}) or {}
        self.last_service_t = 0.0
        self.last_evt1_t = 0.0
        self.service_packets = 0
        self.evt1_packets = 0
        self.stat_packets = 0
        self.service_lag_reported = False
        self.service_lag_last_write_t = 0.0
        # Сохраним порт info для power cycle
        self.port_info = self.get_port_path_info()
        if self._tx_phase_config_path is not None:
            try:
                self.tx_phase_control().restore()
            except Exception as exc:
                # Older firmware stays usable; the portal reports capability
                # errors. A saved phase is checked again before every START.
                print(f"[tx-phase] connect read/restore failed: {exc}", flush=True)
        # Старт: отправим профиль/режим/окна/частоту/размер кадра (если заданы), затем START
        try:
            # Небольшая проверка готовности перед первыми bulk OUT
            try:
                self._wait_ready(timeout=1.0)
            except Exception:
                pass
            # Опционально сконфигурируем окна и частоту блока до старта (по умолчанию ВКЛ)
            try:
                # По умолчанию не шлём окна (как в простом рабочем читателе)
                send_windows = str(os.getenv('BMI30_SEND_WINDOWS','0')).lower() not in ('0','false','no')
            except Exception:
                send_windows = False
            if send_windows:
                try:
                    import struct as _st
                    # Значения по умолчанию совпадают с host скриптами: (100,300) и (700,300)
                    w0s = int(os.getenv('BMI30_WIN0_START', '100'))
                    w0l = int(os.getenv('BMI30_WIN0_LEN',   '300'))
                    w1s = int(os.getenv('BMI30_WIN1_START', '700'))
                    w1l = int(os.getenv('BMI30_WIN1_LEN',   '300'))
                    payload = _st.pack('<BHHHH', 0x10, w0s & 0xFFFF, w0l & 0xFFFF, w1s & 0xFFFF, w1l & 0xFFFF)
                    self.dev.write(EP_OUT, payload, timeout=1000)
                    # Небольшая пауза, чтобы устройство приняло конфиг
                    time.sleep(0.02)
                except Exception:
                    pass
            try:
                # По умолчанию не шлём частоту блока
                send_rate = str(os.getenv('BMI30_SEND_BLOCK_RATE','0')).lower() not in ('0','false','no')
            except Exception:
                send_rate = False
            if send_rate:
                try:
                    import struct as _st
                    # По умолчанию 200 Гц для профиля 1, 300 Гц для профиля 2
                    if 'BMI30_BLOCK_RATE' in os.environ:
                        rate_hz = int(os.getenv('BMI30_BLOCK_RATE','200'))
                    else:
                        rate_hz = 200 if int(self.profile or 1) == 1 else 300
                    payload = _st.pack('<BH', 0x11, rate_hz & 0xFFFF)
                    self.dev.write(EP_OUT, payload, timeout=1000)
                    time.sleep(0.02)
                except Exception:
                    pass
            if not self.passive_connect:
                try:
                    send_profile = str(os.getenv('BMI30_SEND_PROFILE','0')).lower() not in ('0','false','no')
                except Exception:
                    send_profile = False
                # Быстрый режим принудительно шлёт профиль и Ns до START
                if self.fast_mode:
                    try:
                        prof = int(self.profile if self.profile is not None else 2)
                        self.send_cmd(CMD_SET_PROFILE, bytes([prof & 0xFF])); time.sleep(0.2 if prof == 1 else 0.01)
                    except Exception:
                        pass
                    try:
                        # SET_FULL_MODE(1/0)
                        self.send_cmd(CMD_SET_FULL_MODE, bytes([1 if self.full else 0])); time.sleep(0.05)
                    except Exception:
                        pass
                    # Важно: SET_FRAME_SAMPLES (0x17) нельзя слать для профиля 1 — ломает поток.
                    # Для профиля 2 можно слать только если хост явно попросил (frame_samples!=None).
                    if self.frame_samples is not None and int(self.profile or 2) == 2:
                        try:
                            ns = max(1, int(self.frame_samples)) & 0xFFFF
                            self.send_cmd(CMD_SET_FRAME_SAMPLES, ns.to_bytes(2, 'little'))
                            time.sleep(0.05)
                        except Exception:
                            pass
                    # В fast-режиме дополнительно задаём частоту блока (200/300 Гц) перед START
                    try:
                        rate_hz = 200 if int(self.profile or 1) == 1 else 300
                        self.set_block_rate(rate_hz)
                        time.sleep(0.05)
                    except Exception:
                        pass
                elif send_profile:
                    try:
                        if self.profile is not None:
                            self.send_cmd(CMD_SET_PROFILE, bytes([int(self.profile) & 0xFF])); time.sleep(0.02)
                    except Exception:
                        pass
                try:
                    self.send_cmd(CMD_SET_FULL_MODE, bytes([1 if self.full else 0])); time.sleep(0.02)
                except Exception:
                    pass
                if self.frame_samples is not None and int(self.profile or 2) == 2:
                    try:
                        # u16 LE (только для профиля 2; профиль 1 ломается при SET_FRAME_SAMPLES)
                        ns = max(1, int(self.frame_samples)) & 0xFFFF
                        self.send_cmd(CMD_SET_FRAME_SAMPLES, ns.to_bytes(2, 'little'))
                        time.sleep(0.02)
                    except Exception:
                        pass
                # Профиль 1 требует больше времени на инициализацию
                delay = 0.3 if (int(self.profile if self.profile is not None else 2) == 1) else 0.05
                time.sleep(delay)
                self._start_stream_with_tx(reason="constructor")
                time.sleep(0.05)
                # EP0 статус-пинг сразу после старта — выключен по умолчанию
                try:
                    if str(os.getenv('BMI30_EP0_AFTER_START','0')).lower() not in ('0','false','no'):
                        self._get_status_ep0()
                except Exception:
                    pass
        except Exception:
            pass
    # Консервативный старт: без дополнительных пинков/GET_STATUS (сделаем только по запросу)
        self.lock = threading.Lock()
        self.frames = 0; self.bytes = 0; self.crc_bad = 0; self.magic_bad = 0
        self.last_crc_bad_info = None
        self._crc_bad_log_count = 0
        self.test_seen = 0
        self.last_stat = getattr(self, "last_stat", None)
        self.last_stat_t = float(getattr(self, "last_stat_t", 0.0) or 0.0)
        self.last_evt1 = getattr(self, "last_evt1", None)
        self.device_state_cache = getattr(self, "device_state_cache", {}) or {}
        self.last_err_step_pkt = None
        self.last_err_step_vals = None
        self.last_err_step_t = 0.0
        # Track per-channel RX sequence continuity (adc_id=0/1).
        # This matches the user's definition of "no losses": each channel independently.
        self.seq_last_ch0: int | None = None
        self.seq_last_ch1: int | None = None
        self.seq_gap_ch0: int = 0
        self.seq_gap_ch1: int = 0
        self.seq_dup_ch0: int = 0
        self.seq_dup_ch1: int = 0
        self.seq_reset_ch0: int = 0
        self.seq_reset_ch1: int = 0
        # Per-channel timestamp continuity (u32 from header)
        self.ts_last_ch0: int | None = None
        self.ts_last_ch1: int | None = None
        self.ts_dup_ch0: int = 0
        self.ts_dup_ch1: int = 0
        self.ts_reset_ch0: int = 0
        self.ts_reset_ch1: int = 0
        # Timestamp delta histograms collected within each 1s stats interval
        self._ts_dhist_ch0: dict[int, int] = {}
        self._ts_dhist_ch1: dict[int, int] = {}
        self._ts_hist_lock = threading.Lock()

        # Seq delta histograms collected within each 1s stats interval.
        # Important: seq often increments by STEP=2 per-channel when A/B are interleaved.
        # We infer STEP from these histograms and estimate gaps based on it.
        self._seq_dhist_ch0: dict[int, int] = {}
        self._seq_dhist_ch1: dict[int, int] = {}

        # Warm-up: ignore early unstable intervals when switching modes.
        try:
            self.warmup_sec = float(os.getenv('BMI30_WARMUP_SEC', '5'))
        except Exception:
            self.warmup_sec = 5.0

        # Timestamp gap detection strictness.
        # We treat dt=2*step as jitter/batching by default and count gaps only for dt >= 3*step.
        # Override via env BMI30_TS_GAP_FACTOR (float).
        try:
            self.ts_gap_factor = float(os.getenv('BMI30_TS_GAP_FACTOR', '3'))
        except Exception:
            self.ts_gap_factor = 3.0
        self._stats_epoch = time.time()
        # Cumulative (post-warmup) totals for timestamp-based gaps/loss
        self._ts_gap_tot_ch0 = 0
        self._ts_gap_tot_ch1 = 0
        self._rx_tot_ch0 = 0
        self._rx_tot_ch1 = 0
        # Per-channel received frame counters (RX loop)
        self.rx_cnt_ch0: int = 0
        self.rx_cnt_ch1: int = 0
        # snapshots for per-second delta printing
        self._seq_gap_ch0_0: int = 0
        self._seq_gap_ch1_0: int = 0
        self._seq_dup_ch0_0: int = 0
        self._seq_dup_ch1_0: int = 0
        self._seq_reset_ch0_0: int = 0
        self._seq_reset_ch1_0: int = 0
        self._rx_cnt_ch0_0: int = 0
        self._rx_cnt_ch1_0: int = 0
        self._ts_dup_ch0_0: int = 0
        self._ts_dup_ch1_0: int = 0
        self._ts_reset_ch0_0: int = 0
        self._ts_reset_ch1_0: int = 0
        # allow overriding pairing policy via constructor params
        try:
            asm_relaxed = getattr(self, '_asm_relaxed_override', None)
            asm_relaxed_order = getattr(self, '_asm_relaxed_order_override', None)
            asm_ts_pair = getattr(self, '_asm_ts_pair_override', None)
            asm_ts_tol = getattr(self, '_asm_ts_tol_override', None)
            asm_indep = getattr(self, '_asm_independent_override', None)
        except Exception:
            asm_relaxed = asm_relaxed_order = asm_ts_pair = asm_ts_tol = asm_indep = None
        self.asm = StereoAssembler(relaxed=asm_relaxed, relaxed_order=asm_relaxed_order, ts_pairing=asm_ts_pair, ts_tol=asm_ts_tol, independent=asm_indep)
        try:
            print(f"[USBStream] StereoAssembler relaxed={self.asm.relaxed}, relaxed_order={self.asm.relaxed_order} independent={getattr(self.asm,'independent',False)}", flush=True)
        except Exception:
            pass
        self.stat_t = time.time()
        self._closed = False
        self._fallback_done = False
        self._working_seen = False
        self.keepalive_last = self.connected_t
        self.restart_attempts = 0
        self._start_device_state_cache_writer()
        try:
            self.host_rx_ack_enabled = str(os.getenv('BMI30_HOST_RX_ACK', '1')).lower() not in ('0', 'false', 'no')
        except Exception:
            self.host_rx_ack_enabled = True
        try:
            self.host_rx_ack_interval = max(0.1, float(os.getenv('BMI30_HOST_RX_ACK_INTERVAL', '0.25')))
        except Exception:
            self.host_rx_ack_interval = 0.25
        self.host_rx_ack_last = 0.0
        self.host_rx_ack_fail = 0
        self.host_rx_ack_write_t = 0.0
        self.host_rx_ack_write_total_frames = 0
        self.host_rx_ack_write_count = 0
        self._host_rx_ack_stop = False
        self._host_rx_ack_q = queue.Queue(maxsize=1)
        self._host_rx_ack_th = threading.Thread(target=self._host_rx_ack_loop, daemon=True)
        self._host_rx_ack_th.start()
        try:
            self.force_reopen = str(os.getenv('BMI30_FORCE_REOPEN','1')).lower() not in ('0','false','no')
        except Exception:
            self.force_reopen = True
        # Флаг: один раз авто-исправить несоответствие профиля/частоты после старта
        self._rate_mismatch_fixed = False
        self.th = threading.Thread(target=self._rx_loop, daemon=True)
        self.th.start()
        self._rs485_sensor_stop = threading.Event()
        self._rs485_sensor_th = threading.Thread(target=self._rs485_sensor_loop, daemon=True)
        self._rs485_sensor_th.start()

    def set_block_rate(self, rate_hz: int):
        """Задать частоту блока (в Гц) через вендорский пакет 0x11."""
        try:
            import struct as _st
            payload = _st.pack('<BH', 0x11, int(rate_hz) & 0xFFFF)
            with self._ep_out_lock:
                _ = self.dev.write(EP_OUT, payload, timeout=1000)
            try:
                print(f"[tx] set_block_rate {rate_hz}Hz")
            except Exception:
                pass
        except Exception as e:
            try:
                print('[tx] set_block_rate failed:', e)
            except Exception:
                pass

    def tx_phase_control(self):
        with self._ep0_lock:
            if self._tx_phase_control is None:
                from .tx_phase import TxPhaseControl
                self._tx_phase_control = TxPhaseControl(self, self._tx_phase_config_path)
            return self._tx_phase_control

    def set_tx_enable(self, enabled: bool):
        """Save and apply the explicit Tx200 request through Vendor command 0x33."""
        self.desired_tx_enabled = bool(enabled)
        self.send_cmd(CMD_SET_TX_ENABLE, b"\x01" if self.desired_tx_enabled else b"\x00")
        return True

    def _start_stream_with_tx(self, reason: str = "start"):
        """START first, then restore Tx200 as the final TX-control command."""
        self.send_cmd(CMD_START_STREAM, b"")
        time.sleep(0.02)
        self.set_tx_enable(bool(getattr(self, "desired_tx_enabled", False)))
        try:
            print(
                f"[tx-state] reason={reason} start=1 desired_tx={int(bool(self.desired_tx_enabled))}",
                flush=True,
            )
        except Exception:
            pass
        return True

    def start_stream(self):
        """Public START helper that preserves the owning engine's Tx200 request."""
        return self._start_stream_with_tx(reason="public_start")

    def _parse_stat_ready(self, st: bytes) -> tuple[bool, bool]:
        """Парсим STAT, возвращаем (alt1, out_armed). Безопасно при любом буфере."""
        try:
            if not isinstance(st, (bytes, bytearray)) or len(st) < 64 or st[:4] != b'STAT':
                return (False, False)
            flags2 = int.from_bytes(st[50:52], 'little')
            alt1 = ((flags2 >> 15) & 1) == 1
            # STAT v1: reserved2 @53
            reserved2 = st[53]
            out_armed = ((reserved2 >> 7) & 1) == 1
            return (alt1, out_armed)
        except Exception:
            return (False, False)

    def _wait_ready(self, timeout: float = 1.0):
        """Дождаться готовности устройства (alt=1 и out_armed=1 по STAT v1).
        Опрос EP0 каждые 5–10 мс до timeout. После успеха — CLEAR_HALT."""
        t0 = time.time()
        ready = False
        poll_int = 0.007  # ~7 мс между опросами
        max_to = max(0.05, float(timeout))
        
        while time.time() - t0 < max_to:
            try:
                self._get_status_ep0()
                st = getattr(self, 'last_stat', None)
                if st and len(st) >= 64:
                    if self.ignore_ready_flags:
                        # Считаем готовым при получении STAT, не проверяя alt1/out_armed
                        ready = True
                        break
                    else:
                        alt1, out_armed = self._parse_stat_ready(st)
                        if alt1 and out_armed:
                            ready = True
                            break
            except Exception:
                pass
            time.sleep(poll_int)
        
        # После успеха или таймаута сделать CLEAR_HALT
        try:
            self._clear_halt_eps()
        except Exception:
            pass
        
        if not ready:
            try:
                print(f'[ep0] wait_ready: готовность не достигнута за {time.time()-t0:.3f}с')
            except Exception:
                pass
        return ready

    def _status_len(self):
        try:
            return max(64, min(192, int(os.getenv('BMI30_STAT_LEN', '137'))))
        except Exception:
            return STAT_PACKET_V6_LEN

    def _ep0_ctrl_transfer(self, *args, **kwargs):
        """Serialize EP0 requests; overlapping libusb control reads corrupt responses."""
        lock = getattr(self, "_ep0_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._ep0_lock = lock
        with lock:
            return self.dev.ctrl_transfer(*args, **kwargs)

    def capabilities(self):
        from .capabilities import for_stream
        return for_stream(self).snapshot()

    def get_communication_recovery(self, force=False):
        now = time.monotonic()
        if not force and now - getattr(self, '_comm_recovery_checked', -10.0) < 1.0:
            return getattr(self, '_comm_recovery_cache', None)
        self._comm_recovery_checked = now
        try:
            from .capabilities import for_stream
            self._comm_recovery_cache = for_stream(self).read('communication_recovery', parse_communication_recovery,
                                                            0xC0, 0x4F, 0, 0, 24, timeout=120)
        except Exception:
            # Earlier firmware does not implement this optional request.
            self._comm_recovery_cache = None
        return self._comm_recovery_cache

    def ack_communication_recovery(self, generation):
        if type(generation) is not int or not 1 <= generation <= 65535:
            raise ValueError('Invalid recovery generation')
        state = self.get_communication_recovery(force=True)
        if not state or not state['pending'] or not state['durable'] or state['generation'] != generation:
            return False
        from .capabilities import for_stream
        for_stream(self).transfer('communication_recovery', 0x40, 0x50, generation, 0, None, timeout=120)
        self._comm_recovery_checked = -10.0
        return True

    def _get_status_ep0(self, quiet: bool = False):
        """Попробовать прочитать статус через EP0 (vendor control IN, recipient: device)."""
        try:
            # bmRequestType: 0xC0 (Device to Host, Vendor, Device)
            # bRequest: CMD_GET_STATUS
            # wValue: 0
            # wIndex: 0 (device)
            # Firmware 1.2.13 exposes authoritative direct-ID STAT v6 at 137 bytes.
            data = None
            try:
                data = self._ep0_ctrl_transfer(0xC0, CMD_GET_STATUS, 0, 0, self._status_len(), timeout=300)
            except usb.core.USBError as e:
                # Время от времени устройство может NAK/STALL — это нормально
                try:
                    if not quiet and e.errno not in (110,):
                        print('[ep0] GET_STATUS usb err:', e)
                except Exception:
                    pass
                return
            if data is not None and len(data) > 0:
                bs = bytes(data)
                self.last_stat = bs
                self.last_stat_t = time.time()
                try:
                    patch = self._parse_stat_device_state(bs)
                    patch["source"] = "ep0_stat"
                    self._write_device_state_cache(patch)
                except Exception:
                    pass
                if not quiet:
                    print('[ep0] status len=', len(bs))
                return bs
        except Exception as e:
            try:
                if not quiet:
                    print('[ep0] GET_STATUS failed:', e)
            except Exception:
                pass

    def _host_rx_ack_loop(self):
        """Write coalesced host acknowledgements outside the Bulk reader.

        Firmware defines this as an acknowledgement of *newly parsed* A/B
        frames, not a free-running keepalive.  Repeating an unchanged counter
        would falsely report HOST_RX_ALIVE during a real ADC stall, so timer
        wakeups only coalesce genuine counter progress.
        """
        try:
            last_ack_total = int(getattr(self, 'host_rx_ack_write_total_frames', 0) or 0)
        except Exception:
            last_ack_total = 0
        while (not bool(getattr(self, '_host_rx_ack_stop', False))) and getattr(self, '_running', True):
            try:
                interval = max(0.1, float(getattr(self, 'host_rx_ack_interval', 0.25) or 0.25))
            except Exception:
                interval = 0.25
            total_frames = None
            try:
                total_frames = self._host_rx_ack_q.get(timeout=max(0.05, min(0.2, interval * 0.5)))
            except queue.Empty:
                try:
                    total_frames = int(getattr(self, 'rx_cnt_ch0', 0) or 0) + int(getattr(self, 'rx_cnt_ch1', 0) or 0)
                except Exception:
                    total_frames = 0
            except Exception:
                total_frames = None
            if total_frames is None:
                continue
            try:
                last_write = float(getattr(self, 'host_rx_ack_write_t', 0.0) or 0.0)
                total_i = int(total_frames)
                if total_i == int(last_ack_total):
                    continue
                if last_write <= 0.0 or (time.time() - last_write) >= interval:
                    if bool(self._write_host_rx_ack(total_i)):
                        last_ack_total = total_i
            except Exception:
                pass

    def _queue_host_rx_ack(self, total_frames: int):
        try:
            self._host_rx_ack_q.put_nowait(int(total_frames))
            return
        except queue.Full:
            pass
        except Exception:
            return
        try:
            _ = self._host_rx_ack_q.get_nowait()
        except Exception:
            pass
        try:
            self._host_rx_ack_q.put_nowait(int(total_frames))
        except Exception:
            pass

    def _send_host_rx_ack(self, total_frames: int | None = None, force: bool = False):
        """Queue a best-effort RX heartbeat without blocking the Bulk IN reader."""
        try:
            if not bool(getattr(self, 'host_rx_ack_enabled', True)):
                return
            if (not getattr(self, '_running', True)) or bool(getattr(self, 'disconnected', False)):
                return
            now = time.time()
            interval = float(getattr(self, 'host_rx_ack_interval', 1.0) or 1.0)
            if (not force) and (now - float(getattr(self, 'host_rx_ack_last', 0.0) or 0.0)) < interval:
                return
            if total_frames is None:
                total_frames = int(getattr(self, 'rx_cnt_ch0', 0) or 0) + int(getattr(self, 'rx_cnt_ch1', 0) or 0)
            self.host_rx_ack_last = now
            self._queue_host_rx_ack(int(total_frames))
        except Exception:
            pass

    def _write_host_rx_ack(self, total_frames: int):
        """Worker-side write for HOST_RX_ACK. Never called from the reader hot path."""
        try:
            if not bool(getattr(self, 'host_rx_ack_enabled', True)):
                return False
            if (not getattr(self, '_running', True)) or bool(getattr(self, 'disconnected', False)):
                return False
            pkt = bytes([CMD_HOST_RX_ACK]) + struct.pack('<I', int(total_frames) & 0xFFFFFFFF)
            try:
                timeout_ms = int(os.getenv('BMI30_HOST_RX_ACK_TIMEOUT_MS', '20'))
            except Exception:
                timeout_ms = 20
            timeout_ms = max(5, min(100, int(timeout_ms)))
            with self._ep_out_lock:
                self.dev.write(EP_OUT, pkt, timeout=timeout_ms)
            self.host_rx_ack_fail = 0
            self.host_rx_ack_write_t = time.time()
            self.host_rx_ack_write_total_frames = int(total_frames) & 0xFFFFFFFF
            self.host_rx_ack_write_count = int(getattr(self, 'host_rx_ack_write_count', 0) or 0) + 1
            return True
        except Exception as e:
            try:
                self.host_rx_ack_fail = int(getattr(self, 'host_rx_ack_fail', 0) or 0) + 1
            except Exception:
                self.host_rx_ack_fail = 1
            try:
                eno = getattr(e, 'errno', None)
            except Exception:
                eno = None
            if eno in (5, 19, 32) or 'Invalid endpoint' in str(e):
                try:
                    self.disconnected = True
                except Exception:
                    pass
            return False

    def _clear_halt_eps(self):
        try:
            usb.util.clear_halt(self.dev, EP_IN)
        except Exception:
            pass
        try:
            usb.util.clear_halt(self.dev, EP_OUT)
        except Exception:
            pass

    def _reset_altsetting(self):
        try:
            _disable_alt = str(os.getenv('BMI30_RESET_ALT','1')).lower() in ('0','false','no')
        except Exception:
            _disable_alt = False
        if _disable_alt:
            return
        try:
            if hasattr(self, 'intf_num') and self.intf_num is not None:
                # Тоггл alt 0 -> 1 для мягкого переинициализирования пайплайна
                self._ensure_alt(self.intf_num, desired_alt=0)
                time.sleep(0.005)
                self._ensure_alt(self.intf_num, desired_alt=1)
        except Exception as e:
            try:
                print('[alt] set_interface_altsetting failed:', e)
            except Exception:
                pass

    def _prepare_clean_start(self, stop_first: bool = True):
        try:
            _disable_clean = str(os.getenv('BMI30_CLEAN_START','1')).lower() in ('0','false','no')
        except Exception:
            _disable_clean = False
        if _disable_clean:
            return
        # На всякий случай остановим поток на устройстве, затем почистим EP и altsetting
        if stop_first:
            try:
                # перед отправкой STOP дождёмся готовности
                self._wait_ready(timeout=1.0)
                self.send_cmd(CMD_STOP_STREAM, b"")
                time.sleep(0.02)
            except Exception:
                pass
        self._clear_halt_eps()
        # Небольшая пауза перед переустановкой altsetting
        time.sleep(0.01)
        self._reset_altsetting()
        # после смены альта дождёмся готовности
        try:
            self._wait_ready(timeout=0.5)
        except Exception:
            pass
        # И ещё раз очистим HALT на случай, если altsetting переинициализировал пайпы
        self._clear_halt_eps()

    def _kick_cdc_start(self):
        if getattr(self, 'disable_cdc_kick', False):
            return
        try:
            cfg2 = self.dev.get_active_configuration()
            cdc_intf = None; cdc_out = None
            for it in cfg2:
                try:
                    if getattr(it, 'bInterfaceClass', None) == 0x0A:  # CDC Data
                        for e in it.endpoints():
                            if (e.bEndpointAddress & 0x80) == 0 and (e.bmAttributes & 0x03) == 2:
                                cdc_intf = it; cdc_out = e.bEndpointAddress; break
                except Exception:
                    continue
                if cdc_out is not None:
                    break
            if cdc_out is not None and cdc_intf is not None:
                try:
                    usb.util.claim_interface(self.dev, cdc_intf.bInterfaceNumber)
                except Exception:
                    pass
                try:
                    if getattr(self, '_tx_phase_config_path', None) is not None:
                        self.tx_phase_control().restore(before_start=True)
                    _ = self.dev.write(cdc_out, bytes([CMD_START_STREAM]), timeout=300)
                    print("[kick] CDC START sent")
                    # CDC START is still a START_STREAM path.  Restore the
                    # explicit Tx200 request afterwards through Vendor OUT.
                    self.set_tx_enable(bool(getattr(self, "desired_tx_enabled", False)))
                except Exception as e:
                    print("[kick] CDC write failed:", e)
                try:
                    usb.util.release_interface(self.dev, cdc_intf.bInterfaceNumber)
                except Exception:
                    pass
        except Exception:
            pass

    def request_rx_flush(self):
        """Сигнал потоку чтения: очистить накопленный буфер после рестарта.
        Устаревшие данные в buf могут содержать мусор или частичные кадры
        от предыдущей сессии — их нужно выбросить перед первым новым кадром.
        """
        self._rx_flush_requested = True

    def restart_stream(self, full=True):
        """Повторно пнуть поток, если устройство молчит."""
        try:
            # Чистый рестарт: остановить, очистить и только потом запускать
            self._prepare_clean_start(stop_first=True)
            # После STOP + alt-toggle устаревшие данные в буфере деффреймера
            # становятся мусором — очищаем их перед первым новым кадром.
            self.request_rx_flush()
            if full:
                self.send_cmd(CMD_SET_FULL_MODE, bytes([1])); time.sleep(0.02)
            self._start_stream_with_tx(reason="restart_stream"); time.sleep(0.02)
            self._prime_get_status()
            self._kick_cdc_start()
            self.last_restart_t = time.time()
            print("[kick] restart_stream done")
        except Exception as e:
            print("[kick] restart_stream failed:", e)
    def _prime_get_status(self):
        try:
            for _ in range(2):
                self._get_status_ep0()
                time.sleep(0.05)
        except Exception:
            pass
    def _do_fallback_start(self):
        """Единовременный мягкий пинок потока, если видим только STAT/тишину."""
        if not bool(getattr(self, 'local_recovery_enabled', False)):
            try:
                if not bool(getattr(self, '_local_recovery_disabled_logged', False)):
                    print("[recovery] USBStream local fallback disabled; upper layer must fault-probe via EP0 STAT", flush=True)
                    self._local_recovery_disabled_logged = True
            except Exception:
                pass
            self._fallback_done = True
            return
        if self._fallback_done:
            return
        try:
            self.send_cmd(CMD_SET_PROFILE, bytes([self.profile])); time.sleep(0.02)
        except Exception:
            pass
        try:
            self.send_cmd(CMD_SET_FULL_MODE, bytes([1 if self.full else 0])); time.sleep(0.02)
        except Exception:
            pass
        try:
            self._start_stream_with_tx(reason="fallback_start"); time.sleep(0.02)
        except Exception:
            pass
        # Попробуем дополнительно CDC START (если есть CDC Data интерфейс)
        if not getattr(self, 'disable_cdc_kick', False):
            try:
                self._kick_cdc_start()
            except Exception:
                pass
        self._fallback_done = True
    def close(self):
        with self._close_lock:
            if bool(getattr(self, '_closed', False)):
                return
            self._closed = True

            # Отправляем STOP напрямую через dev.write ДО того как снять _running,
            # потому что send_cmd проверяет _running и вернётся немедленно если False.
            try:
                skip_stop = bool(getattr(self, 'disconnected', False))
            except Exception:
                skip_stop = False
            self._host_rx_ack_stop = True
            try:
                sensor_stop = getattr(self, "_rs485_sensor_stop", None)
                if sensor_stop is not None:
                    sensor_stop.set()
                sensor_th = getattr(self, "_rs485_sensor_th", None)
                if sensor_th is not None and sensor_th.is_alive() and sensor_th is not threading.current_thread():
                    sensor_th.join(timeout=0.35)
            except Exception:
                pass
            try:
                th_ack = getattr(self, '_host_rx_ack_th', None)
                if th_ack is not None and th_ack.is_alive() and th_ack is not threading.current_thread():
                    th_ack.join(timeout=0.15)
            except Exception:
                pass
            if not skip_stop:
                try:
                    with self._ep_out_lock:
                        self.dev.write(EP_OUT, bytes([CMD_STOP_STREAM]), timeout=500)
                    print(f"[tx] cmd=0x{CMD_STOP_STREAM:02X} (close/direct)")
                except Exception:
                    pass

            self._running = False

            # Дождёмся выхода RX-потока, чтобы не освобождать libusb ресурсы во время read().
            try:
                th = getattr(self, 'th', None)
                if th is not None and th.is_alive() and th is not threading.current_thread():
                    th.join(timeout=1.5)
            except Exception:
                pass

            # RX and RS485 are the cache producers.  Stop the cache writer only
            # after they have exited so its shutdown snapshot contains their
            # last service/device update and no disk I/O can outlive close().
            try:
                cache_stopped = self._stop_device_state_cache_writer(timeout=2.0)
                if not cache_stopped:
                    print("[cache] device-state writer did not stop before close timeout", flush=True)
            except Exception:
                pass

            # Переведём IF в alt=0 (idle), если возможно
            if not skip_stop:
                try:
                    if hasattr(self, 'intf_num') and self.intf_num is not None:
                        try:
                            usb.util.claim_interface(self.dev, self.intf_num)
                        except Exception:
                            pass
                        try:
                            self.dev.set_interface_altsetting(interface=self.intf_num, alternate_setting=0)
                            self.current_alt = 0
                        except Exception:
                            pass
                except Exception:
                    pass
            # Освободим интерфейс и очистим ресурсы, чтобы следующий запуск был «с нуля»
            try:
                if hasattr(self, 'intf_num') and self.intf_num is not None:
                    try:
                        usb.util.release_interface(self.dev, self.intf_num)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
    def soft_reset(self):
        """Отправить EP0 SOFT_RESET (0x7E) вендорским control OUT без данных."""
        try:
            self._ep0_ctrl_transfer(0x40, CMD_SOFT_RESET, 0, 0, None, timeout=500)
            self._optic_reaction_source_sent = None
            print('[ep0] SOFT_RESET sent')
        except Exception as e:
            self._optic_reaction_source_sent = None
            print('[ep0] SOFT_RESET failed:', e)
            raise
    def deep_reset(self):
        """Отправить EP0 DEEP_RESET (0x7F) вендорским control OUT без данных."""
        try:
            self._ep0_ctrl_transfer(0x40, CMD_DEEP_RESET, 0, 0, None, timeout=800)
            self._optic_reaction_source_sent = None
            print('[ep0] DEEP_RESET sent')
        except Exception as e:
            self._optic_reaction_source_sent = None
            print('[ep0] DEEP_RESET failed:', e)
            raise
    def set_alt(self, alt:int):
        """Принудительно установить altsetting интерфейса vendor (обычно 0 или 1)."""
        if not hasattr(self, 'intf_num') or self.intf_num is None:
            return
        self._ensure_alt(self.intf_num, desired_alt=int(alt))

    def request_err_step(self):
        """Quietly request short bulk status `0x31`; device replies with `[0x31, err, step]`."""
        try:
            _ = self.dev.write(EP_OUT, bytes([0x31]), timeout=300)
            return True
        except Exception:
            return False

    # --- internals ---
    def _ensure_alt(self, intf_num:int, desired_alt:int, retries:int=2):
        """Установить alt: стандартный SET_INTERFACE (0x0B) как основной, вендор SET_ALT(0x31) как fallback.
        После успеха — дождаться alt1/out_armed и сделать CLEAR_HALT."""
        try:
            retries = max(int(retries), int(os.getenv('BMI30_ALT_RETRIES', '8')))
        except Exception:
            retries = max(int(retries), 8)
        try:
            retry_sleep = max(0.02, min(0.5, float(os.getenv('BMI30_ALT_RETRY_SLEEP_S', '0.08'))))
        except Exception:
            retry_sleep = 0.08
        try:
            ready_timeout = max(0.1, min(2.0, float(os.getenv('BMI30_ALT_READY_TIMEOUT_S', '0.5'))))
        except Exception:
            ready_timeout = 0.5
        # === Попытка 1: стандартный SET_INTERFACE (preferred) ===
        for attempt in range(retries+1):
            try:
                try:
                    if self.dev.is_kernel_driver_active(intf_num):
                        try:
                            self.dev.detach_kernel_driver(intf_num)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    usb.util.claim_interface(self.dev, intf_num)
                except Exception:
                    pass
                time.sleep(0.002)
                self.dev.set_interface_altsetting(interface=intf_num, alternate_setting=int(desired_alt))
                self.current_alt = int(desired_alt)
                print(f"[alt] set_interface_altsetting alt={desired_alt} ok (attempt {attempt+1})")
                # После успешного alt — дождёмся готовности и сделаем CLEAR_HALT
                try:
                    self._wait_ready(timeout=ready_timeout)
                    self._clear_halt_eps()
                except Exception:
                    pass
                return True
            except Exception as e:
                if attempt == retries:
                    try:
                        print(f"[alt] set_interface_altsetting alt={desired_alt} failed after {retries+1} attempts:", e)
                    except Exception:
                        pass
                try:
                    usb.util.release_interface(self.dev, intf_num)
                except Exception:
                    pass
                time.sleep(min(0.5, retry_sleep * (1.0 + 0.25 * attempt)))
        # === Попытка 2: стандартный control SET_INTERFACE (0x0B/0x01) ===
        try:
            bm = 0x01  # Host-to-Device, Standard, Interface
            REQ_SET_INTERFACE = 0x0B
            self._ep0_ctrl_transfer(bm, REQ_SET_INTERFACE, int(desired_alt), int(intf_num), None, timeout=300)
            self.current_alt = int(desired_alt)
            print(f"[alt] ctrl SET_INTERFACE (0x0B/0x01) alt={desired_alt} ok")
            try:
                self._wait_ready(timeout=ready_timeout)
                self._clear_halt_eps()
            except Exception:
                pass
            return True
        except Exception as e:
            try:
                print(f"[alt] ctrl SET_INTERFACE (0x0B/0x01) alt={desired_alt} failed:", e)
            except Exception:
                pass
        # === Попытка 3: вендорский SET_ALT (0x31) как fallback ===
        try:
            # Device (0x40) с wIndex=2 — согласно спецификации прошивки
            try:
                self._ep0_ctrl_transfer(0x40, CMD_SET_ALT, int(desired_alt), int(intf_num), None, timeout=300)
                self.current_alt = int(desired_alt)
                print(f"[alt] vendor SET_ALT(0x40) alt={desired_alt} ok")
                try:
                    self._wait_ready(timeout=ready_timeout)
                    self._clear_halt_eps()
                except Exception:
                    pass
                return True
            except Exception:
                pass
            # Interface (0x41) с wIndex=2 как дополнительная попытка
            try:
                self._ep0_ctrl_transfer(0x41, CMD_SET_ALT, int(desired_alt), int(intf_num), None, timeout=300)
                self.current_alt = int(desired_alt)
                print(f"[alt] vendor SET_ALT(0x41) alt={desired_alt} ok")
                try:
                    self._wait_ready(timeout=ready_timeout)
                    self._clear_halt_eps()
                except Exception:
                    pass
                return True
            except Exception:
                pass
        except Exception as ee:
            try:
                print('[alt] vendor SET_ALT fallback failed:', ee)
            except Exception:
                pass
        # На ошибку
        try:
            print(f"[alt] unable to set alt={desired_alt} (all methods failed)")
        except Exception:
            pass
        return False
    def set_optic_reaction_source(self, source_id: int) -> bool:
        """Send the system WS2812's selected optic ID (0xFF selects none).

        The host chooses an enabled source; firmware applies that selection.
        Older firmware may still impose its unconditional local priority.
        A successful
        zero-length EP0 OUT confirms USB delivery, not a color/readback ACK.
        Cache only successful writes and serialize them with other EP0 users.
        """
        if type(source_id) is not int or not (0 <= source_id <= 31 or source_id == 0xFF):
            raise ValueError("Optic source must be 0..31 or 255")
        with self._ep0_lock:
            if not getattr(self, '_running', True) or getattr(self, 'disconnected', False):
                raise RuntimeError("USB stream is not connected")
            if getattr(self, '_optic_reaction_source_sent', None) == source_id:
                return True
            written = self._ep0_ctrl_transfer(
                0x40, CMD_SET_OPTIC_REACTION_SOURCE, source_id, 0, None, timeout=300)
            if written != 0:
                raise RuntimeError("Unexpected optic-source control OUT result")
            self._optic_reaction_source_sent = source_id
            self._optic_reaction_source_sent_at = time.time()
            self._optic_reaction_source_send_count = int(
                getattr(self, '_optic_reaction_source_send_count', 0)) + 1
            print(f"[optic-source] EP0 0x44 source={source_id} delivered", flush=True)
            return True

    def send_cmd(self, cmd, payload:bytes):
        # Не отправляем команды в закрытый/остановленный поток.
        # close() устанавливает _running=False и alt=0, после чего EP_OUT
        # становится недействительным — любые write() дадут "Invalid endpoint".
        if (not getattr(self, '_running', True)) or bool(getattr(self, 'disconnected', False)):
            return
        if cmd == CMD_START_STREAM and getattr(self, '_tx_phase_config_path', None) is not None:
            self.tx_phase_control().restore(before_start=True)
        pkt = bytes([cmd])+payload
        last_err=None
        for i in range(3):
            try:
                with self._ep_out_lock:
                    n = self.dev.write(EP_OUT, pkt, timeout=1000)
                print(f"[tx] cmd=0x{cmd:02X} n={n}")
                if cmd in (CMD_START_STREAM, CMD_DEVICE_RESET):
                    self._optic_reaction_source_sent = None
                return
            except Exception as e:
                last_err=e
                print(f"[tx-err] cmd=0x{cmd:02X} try={i+1} err={e}")
                if 'Invalid endpoint' in str(e):
                    self.disconnected = True
                    break
                # Прерываем retry немедленно если поток закрыт за время ожидания
                if not getattr(self, '_running', True):
                    return
                try:
                    eno = getattr(e, 'errno', None)
                except Exception:
                    eno = None
                # EIO/STALL/EPIPE или ETIMEDOUT (110): пробуем CLEAR_HALT через EP0.
                # EP0 всегда обрабатывается аппаратурой STM32 даже при зависшем firmware.
                if eno in (5, 32, 110) or 'EPIPE' in str(e) or 'Input/Output' in str(e):
                    try:
                        self._get_status_ep0()
                        st = getattr(self, 'last_stat', None)
                        if st and len(st) >= 64:
                            alt1, out_armed = self._parse_stat_ready(st)
                            print(f"[tx-diag] alt1={alt1}, out_armed={out_armed} → CLEAR_HALT + retry")
                    except Exception:
                        pass
                    try:
                        self._clear_halt_eps()
                        # подождём готовности alt1/out_armed
                        self._wait_ready(timeout=0.2)
                    except Exception:
                        pass
                time.sleep(0.05)
        # Пометить disconnected при критических ошибках, чтобы верхний уровень переподключился.
        try:
            eno_last = getattr(last_err, 'errno', None) if isinstance(last_err, Exception) else None
            if isinstance(last_err, usb.core.USBError) and eno_last in (16, 19, 5):
                # EBUSY/ENODEV/EIO — точно потеряли устройство
                self.disconnected = True
            elif isinstance(last_err, Exception) and 'Invalid endpoint' in str(last_err):
                # Endpoint стал недействительным (alt сменился снаружи) — перезапуск
                self.disconnected = True
        except Exception:
            pass
        print(f"[tx-err] cmd=0x{cmd:02X} failed after retries: {last_err}")
    
    def set_buf_rate_fine(self, hz: int):
        """
        Установить частоту следования буферов.
        
        Args:
            hz: Целевая частота буферов (поддерживается 200-400 Гц)
            
        Raises:
            ValueError: Если частота вне допустимого диапазона
        """
        if not (200 <= hz <= 400):
            raise ValueError(f"Частота {hz} Гц вне диапазона 200-400 Гц")
        
        # CMD_SET_BUF_RATE_FINE = 0x1C
        # Формат: [cmd, rate_hz[7:0], rate_hz[15:8]]
        cmd = 0x1C
        payload = bytes([hz & 0xFF, (hz >> 8) & 0xFF])
        self.send_cmd(cmd, payload)

    def publish_det_adc(self, detadc1, detadc2):
        """Publish detector edges before a USB command can block or wait."""
        self._write_device_state_cache({"_host_only": True,
            "host_detection": {"detadc1": bool(detadc1), "detadc2": bool(detadc2), "at": time.time()}})

    def set_det_adc(self, detadc1: bool = False, detadc2: bool = False, bits: int | None = None):
        """Configure local DetADC status bits exported through RS485 status."""
        if bits is None:
            bits = (1 if detadc1 else 0) | (2 if detadc2 else 0)
        self.send_cmd(CMD_SET_DET_ADC, bytes([int(bits) & 0x03]))

    def set_rs485_id(self, node_id: int) -> bool:
        """Persist the local RS485 device ID; zero is a valid assigned ID."""
        try:
            nid = int(node_id)
        except Exception as exc:
            raise ValueError("RS485 device ID must be an integer from 0 to 31") from exc
        if not 0 <= nid <= 31:
            raise ValueError("RS485 device ID must be from 0 to 31")
        try:
            self.send_cmd(CMD_SET_RS485_ID, bytes([nid & 0x1F]))
            return True
        except Exception:
            return False

    def set_rs485_ip(self, ip) -> bool:
        """Publish only the local RPI IPv4 address (legacy command 0x3E)."""
        parts = _parse_ipv4(ip)
        if parts is None:
            return False
        payload = bytes(parts)
        try:
            self._ep0_ctrl_transfer(0x40, CMD_SET_RS485_IP, 0, 0, payload, timeout=300)
            return True
        except Exception:
            try:
                self.send_cmd(CMD_SET_RS485_IP, payload)
                return True
            except Exception:
                return False

    def set_rpi_info(self, rpi_id, ip) -> bool:
        """Publish the 9-hex hostname suffix and IPv4 address (command 0x42 v2)."""
        parts = _parse_ipv4(ip)
        if parts is None:
            raise ValueError("RPI IPv4 address must contain four octets")
        identifier = _normalize_rpi_id(rpi_id)
        if not identifier:
            raise ValueError("RPI ID must be the 9-hex suffix from hostname BMI30-XXXXXXXXX")
        payload = identifier.encode("ascii") + bytes(parts)
        published = False
        try:
            self._ep0_ctrl_transfer(0x40, CMD_SET_RPI_INFO, 0, 0, payload, timeout=300)
            published = True
        except Exception:
            try:
                self.send_cmd(CMD_SET_RPI_INFO, payload)
                published = True
            except Exception:
                published = False
        # Command 0x3E remains valid in RID2 firmware and preserves correct IP
        # publication on older RID1 firmware that cannot store the full RPI ID.
        try:
            self.set_rs485_ip(ip)
        except Exception:
            pass
        return published

    def request_rs485_ident(self) -> bool:
        """Ask the current RS485 master to run one identity/IP scan pass."""
        try:
            self._ep0_ctrl_transfer(0x40, CMD_REQUEST_RS485_IDENT, 0, 0, None, timeout=300)
            return True
        except Exception:
            try:
                self.send_cmd(CMD_REQUEST_RS485_IDENT, b"")
                return True
            except Exception:
                return False

    def get_rs485_ident(self, node_id: int | None = None) -> dict | None:
        """Read RID2/RID1 via EP0; None selects local, while 0 is device ID 00."""
        if node_id is None:
            selector = 0xFF
        else:
            try:
                selector = int(node_id)
            except Exception:
                return None
            if not 0 <= selector <= 31:
                return None
        try:
            data = self._ep0_ctrl_transfer(
                0xC0,
                CMD_GET_RS485_IDENT,
                selector,
                0,
                RS485_IDENT_V2_LEN,
                timeout=250,
            )
            entry = _parse_rs485_ident_packet(bytes(data or b""), requested_node_id=selector)
            return entry
        except Exception:
            return None

    def get_rs485_sensor(self, node_id: int | None = None) -> dict | None:
        """Read one direct local/neighbor sensor snapshot through EP0 SNS1."""
        if node_id is None:
            selector = 0xFF
        else:
            try:
                selector = int(node_id)
            except Exception:
                return None
            if not 0 <= selector <= 31:
                return None
        try:
            data = self._ep0_ctrl_transfer(
                0xC0,
                CMD_GET_RS485_SENSOR,
                selector,
                0,
                RS485_SENSOR_V1_LEN,
                timeout=120,
            )
            return _parse_rs485_sensor_packet(bytes(data or b""), requested_node_id=selector)
        except Exception:
            return None

    def poll_rs485_sensors(self) -> list[dict]:
        """Refresh trusted per-device optic/DetADC states from direct-ID STAT v6."""
        lock = getattr(self, "_rs485_ident_lock", None)
        if lock is None or not lock.acquire(blocking=False):
            return []
        try:
            now = time.time()
            try:
                stat_refresh_s = max(
                    0.2,
                    min(5.0, float(os.getenv("BMI30_RS485_STAT_V6_POLL_S", "2.00"))),
                )
            except Exception:
                stat_refresh_s = 2.00
            last_stat_poll = float(getattr(self, "_rs485_stat_v6_poll_t", 0.0) or 0.0)
            if (now - last_stat_poll) >= stat_refresh_s:
                self._rs485_stat_v6_poll_t = now
                try:
                    self._get_status_ep0(quiet=True)
                except Exception:
                    pass

            cache = getattr(self, "device_state_cache", {}) or {}
            events = cache.get("events") if isinstance(cache.get("events"), dict) else {}
            sensor_map = (
                events.get("sensor_map")
                if isinstance(events.get("sensor_map"), dict)
                else {}
            )
            sync = events.get("sync_state") if isinstance(events.get("sync_state"), dict) else {}
            if not sync:
                sync = cache.get("sync") if isinstance(cache.get("sync"), dict) else {}
            stat = cache.get("stat") if isinstance(cache.get("stat"), dict) else {}
            direct_layout_valid = bool(
                stat.get("sync_layout_valid") is True
                and stat.get("direct_device_id_layout") is True
            )
            authority_validation = ""
            if sensor_map.get("valid") is True:
                try:
                    seen_mask = int(sensor_map.get("sync_seen_mask", 0) or 0) & 0xFFFFFFFF
                    local_node_id = int(sensor_map.get("local_node_id", -1))
                    authority_validation = "evt1_sensor_map"
                except Exception:
                    seen_mask = 0
                    local_node_id = -1
            elif direct_layout_valid:
                try:
                    seen_mask = int(stat.get("sync_seen_mask", 0) or 0) & 0xFFFFFFFF
                    local_node_id = int(sync.get("local_node_id", sync.get("device_id", -1)))
                    authority_validation = "stat_v6_direct_id_layout"
                except Exception:
                    seen_mask = 0
                    local_node_id = -1
            else:
                self._write_device_state_cache({
                    "_host_only": True,
                    "source": "ep0_sns1_rejected",
                    "sensors": {
                        "remote": [],
                        "remote_count": 0,
                        "remote_updated_at": now,
                        "remote_valid": False,
                        "remote_validation": "requires_sensor_map_or_stat_v6",
                        "remote_seen_mask": 0,
                    },
                })
                return []
            node_ids = [
                node_id
                for node_id in range(32)
                if bool(seen_mask & (1 << node_id)) and node_id != local_node_id
            ]
            if not node_ids:
                self._write_device_state_cache({
                    "_host_only": True,
                    "source": "ep0_sns1",
                    "sensors": {
                        "remote": [],
                        "remote_count": 0,
                        "remote_updated_at": now,
                        "remote_valid": True,
                        "remote_validation": authority_validation,
                        "remote_seen_mask": seen_mask,
                    },
                })
                return []
            remote = []
            for node_id in node_ids:
                if not bool(getattr(self, "_running", False)) or bool(getattr(self, "disconnected", False)):
                    break
                item = self.get_rs485_sensor(node_id)
                if not isinstance(item, dict):
                    continue
                if item.get("packet_validated") is not True:
                    continue
                if item.get("node_id_matches_status") is not True:
                    continue
                if int(item.get("node_id", -1)) != node_id:
                    continue
                if item.get("local") is True:
                    continue
                if item.get("seen") is not True or item.get("recent") is not True or item.get("online") is not True:
                    continue
                remote.append(item)
            self._write_device_state_cache({
                "_host_only": True,
                "source": "ep0_sns1",
                "sensors": {
                    "remote": remote,
                    "remote_count": len(remote),
                    "remote_updated_at": now,
                    "remote_valid": True,
                    "remote_validation": authority_validation,
                    "remote_seen_mask": seen_mask,
                },
            })
            return remote
        finally:
            lock.release()

    def _rs485_sensor_loop(self):
        """Keep group sensor columns and optic gating current at sub-second cadence."""
        try:
            interval = max(0.10, min(2.0, float(os.getenv("BMI30_RS485_SENSOR_POLL_S", "0.20"))))
        except Exception:
            interval = 0.20
        stop_event = getattr(self, "_rs485_sensor_stop", None)
        if stop_event is None:
            return
        stop_event.wait(min(0.5, interval))
        while bool(getattr(self, "_running", False)) and not bool(getattr(self, "disconnected", False)):
            try:
                self.poll_rs485_sensors()
            except Exception:
                pass
            if stop_event.wait(interval):
                break

    def _write_rs485_ident_cache(
        self,
        entries: list[dict],
        requested_scan: bool = False,
        local_ip: str | None = None,
        authoritative: bool = False,
    ) -> dict:
        def _entry_valid(entry) -> bool:
            if not isinstance(entry, dict):
                return False
            try:
                flags = int(entry.get("flags", 0) or 0)
            except Exception:
                flags = 0
            try:
                node_id = int(entry.get("node_id", -1))
            except Exception:
                node_id = -1
            return (
                0 <= node_id <= 31
                and bool(flags & 0x07FF)
                and entry.get("node_id_matches_status") is not False
            )

        now = time.time()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
        current = getattr(self, "device_state_cache", {}) or {}
        previous = current.get("rs485_ident") if isinstance(current.get("rs485_ident"), dict) else {}
        previous_nodes = previous.get("nodes") if isinstance(previous.get("nodes"), dict) else {}
        nodes = {} if authoritative else {
            str(k): v for k, v in dict(previous_nodes or {}).items() if _entry_valid(v)
        }
        local = {} if authoritative else (
            previous.get("local") if isinstance(previous.get("local"), dict) else {}
        )
        if not _entry_valid(local):
            local = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                node_id = int(entry.get("node_id", 0) or 0)
            except Exception:
                node_id = 0
            if node_id < 0 or node_id > 31:
                continue
            key = str(node_id)
            merged = _merge_dict(nodes.get(key) if isinstance(nodes.get(key), dict) else {}, entry)
            nodes[key] = merged
            try:
                requested_selector = int(entry.get("requested_node_id", -1))
            except Exception:
                requested_selector = -1
            if bool(entry.get("local")) or requested_selector == 0xFF:
                local = merged
        node_list = []
        for key, value in nodes.items():
            if isinstance(value, dict):
                node_list.append(value)
        node_list.sort(key=lambda item: int(item.get("node_id", 0) or 0))
        cache_source = (
            "ep0_rid2"
            if any(str(item.get("wire_format") or "") == "RID2" for item in entries if isinstance(item, dict))
            else "ep0_rid1"
        )
        ident = _merge_dict({} if authoritative else previous, {
            "source": cache_source,
            "updated_at": now,
            "updated_iso": now_iso,
            "requested_scan": bool(requested_scan),
            "authoritative": bool(authoritative),
            "local_ip": (
                ""
                if str(local_ip or "").strip() == "0.0.0.0"
                else str(local_ip or previous.get("local_ip") or "")
            ),
            "nodes": nodes,
            "node_list": node_list,
            "node_count": len(node_list),
        })
        if local:
            ident["local"] = local
        elif authoritative:
            ident.pop("local", None)
        if authoritative:
            ident["authoritative_updated_at"] = now
            ident["authoritative_updated_iso"] = now_iso
        if requested_scan:
            ident["last_master_request_at"] = now
            ident["last_master_request_iso"] = now_iso
        self._write_device_state_cache({"source": cache_source, "rs485_ident": ident, "_host_only": True})
        return ident

    def poll_rs485_ident(
        self,
        node_ids=None,
        request_scan: bool = False,
        local_ip: str | None = None,
        rpi_id: str | None = None,
    ) -> dict:
        """Serialize identity scans so multiple Portal clients cannot overlap EP0 reads."""
        lock = getattr(self, "_rs485_ident_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._rs485_ident_lock = lock
        if not lock.acquire(blocking=False):
            current = getattr(self, "device_state_cache", {}) or {}
            ident = current.get("rs485_ident") if isinstance(current.get("rs485_ident"), dict) else {}
            return ident if isinstance(ident, dict) else {}
        try:
            return self._poll_rs485_ident_unlocked(
                node_ids=node_ids,
                request_scan=request_scan,
                local_ip=local_ip,
                rpi_id=rpi_id,
            )
        finally:
            lock.release()

    def _poll_rs485_ident_unlocked(
        self,
        node_ids=None,
        request_scan: bool = False,
        local_ip: str | None = None,
        rpi_id: str | None = None,
    ) -> dict:
        """Best-effort RS485 identity/IP polling. Keeps the previous rows until refreshed."""
        if local_ip:
            try:
                if not rpi_id:
                    self.set_rs485_ip(local_ip)
                else:
                    self.set_rpi_info(rpi_id, local_ip)
            except Exception:
                pass
        requested = False
        if request_scan:
            requested = bool(self.request_rs485_ident())
            if requested:
                try:
                    settle_s = max(
                        0.0,
                        min(0.5, float(os.getenv("BMI30_RS485_IDENT_SCAN_SETTLE_S", "0.08"))),
                    )
                except Exception:
                    settle_s = 0.08
                if settle_s > 0.0:
                    time.sleep(settle_s)
        ids = set(range(32)) if node_ids is None else set()
        try:
            for node_id in node_ids or []:
                node_i = int(node_id)
                if 0 <= node_i <= 31:
                    ids.add(node_i)
        except Exception:
            pass
        entries = []
        local_entry = self.get_rs485_ident(None)
        if local_entry:
            entries.append(local_entry)
        try:
            local_entry_id = int(local_entry.get("node_id", -1)) if isinstance(local_entry, dict) else -1
        except Exception:
            local_entry_id = -1
        for node_id in sorted(ids):
            if node_id == local_entry_id:
                continue
            entry = self.get_rs485_ident(node_id)
            if entry:
                entries.append(entry)
        if not entries and not requested:
            current = getattr(self, "device_state_cache", {}) or {}
            ident = current.get("rs485_ident") if isinstance(current.get("rs485_ident"), dict) else {}
            return ident if isinstance(ident, dict) else {}
        authoritative = bool(node_ids is None and isinstance(local_entry, dict))
        return self._write_rs485_ident_cache(
            entries,
            requested_scan=requested,
            local_ip=local_ip,
            authoritative=authoritative,
        )

    def host_rx_clear(self):
        """Tell firmware to clear host RX/accounting state when recovering reader state."""
        self.send_cmd(CMD_HOST_RX_CLEAR, b"")

    def save_dc_to_flash(self):
        """Request one-shot firmware-side save of the current DC snapshot to Flash."""
        self.send_cmd(CMD_SAVE_DC_TO_FLASH, b"")

    def toggle_tim2ch3_inv(self):
        """Toggle TIM2CH3 inversion in firmware diagnostics."""
        self.send_cmd(CMD_TOGGLE_TIM2CH3_INV, b"")

    def device_reset(self):
        """Request firmware-side DEVICE_RESET. Reset mode depends on firmware build."""
        self.send_cmd(CMD_DEVICE_RESET, b"")

    def set_dc_speed_ms(self, settle_ms: int):
        """Set the only STM32 DC control value; zero stops DC learning."""
        try:
            value = int(settle_ms)
        except Exception as exc:
            raise ValueError("settle_ms must be an integer") from exc
        if value < 0 or value > DC_SETTLE_MAX_MS:
            raise ValueError(f"settle_ms must be 0..{DC_SETTLE_MAX_MS}")
        payload = struct.pack('<I', value)
        self.send_cmd(CMD_SET_DC_SPEED, payload)
        return payload

    def set_dc_speed_seconds(self, settle_s: float):
        """Set DC correction time in seconds; 0 disables DC learning."""
        try:
            value = float(settle_s)
        except Exception as exc:
            raise ValueError("settle_s must be a number") from exc
        if not math.isfinite(value) or value < 0.0 or value > (DC_SETTLE_MAX_MS / 1000.0):
            raise ValueError(f"settle_s must be 0..{DC_SETTLE_MAX_MS / 1000.0:g}")
        return self.set_dc_speed_ms(int(round(value * 1000.0)))

    def get_dc_config(self):
        """Read STM32-side DC adaptation config via EP0 when firmware supports it."""
        data = self._ep0_ctrl_transfer(0xC0, CMD_GET_DC_CONFIG, 0, 0, 40, timeout=500)
        bs = bytes(data) if data is not None else b''
        if len(bs) < 40 or bs[:4] != b'DCCF':
            return {'raw': bs.hex()}
        sig, ver, enabled, flags, settle_ms, set_at_ms, adapt_updates, reserved0, reserved1, reserved2, reserved3, reserved4 = struct.unpack('<4sBBHIII5I', bs[:40])
        return {
            'version': int(ver),
            'enabled': bool(enabled),
            'flags': int(flags),
            'settle_ms': int(settle_ms),
            'set_at_ms': int(set_at_ms),
            'adapt_updates': int(adapt_updates),
            'dirty': bool(int(flags) & 0x0004),
            'reserved': [int(reserved0), int(reserved1), int(reserved2), int(reserved3), int(reserved4)],
        }

    def _device_state_path(self) -> str:
        return os.getenv("BMI30_DEVICE_STATE_JSON", DEVICE_STATE_JSON)

    def _init_device_state_cache_writer(self):
        """Initialise the coalescing cache publisher without starting I/O."""
        if getattr(self, "_device_state_cache_cv", None) is not None:
            return
        lock = threading.RLock()
        self._device_state_cache_lock = lock
        self._device_state_cache_cv = threading.Condition(lock)
        self._device_state_cache_generation = 0
        self._device_state_cache_persisted_generation = 0
        self._device_state_cache_dirty = False
        self._device_state_cache_writer_stop = False
        self._device_state_cache_accepting = True
        self._device_state_cache_last_attempt_mono = 0.0
        self._device_state_cache_write_count = 0
        self._device_state_cache_write_fail = 0
        self._device_state_cache_writer_thread = None
        try:
            interval = float(os.getenv("BMI30_DEVICE_STATE_WRITE_INTERVAL_S", "0.5"))
        except Exception:
            interval = 0.5
        # Device state is diagnostic persistence, not part of the USB data
        # path.  Clamp it to 1..2 writes/s even if an unsafe environment value
        # is supplied.
        self._device_state_cache_write_interval_s = max(0.5, min(1.0, interval))
        if not isinstance(getattr(self, "device_state_cache", None), dict):
            self.device_state_cache = {}

    def _start_device_state_cache_writer(self) -> bool:
        self._init_device_state_cache_writer()
        cv = self._device_state_cache_cv
        with cv:
            thread = getattr(self, "_device_state_cache_writer_thread", None)
            if thread is not None and thread.is_alive():
                return True
            if bool(getattr(self, "_device_state_cache_writer_stop", False)):
                return False
            thread = threading.Thread(
                target=self._device_state_cache_writer_loop,
                name="bmi30-device-state-cache",
                daemon=True,
            )
            try:
                from .owner_events import Publisher
                self._group_event_publisher = Publisher()
            except Exception:
                self._group_event_publisher = None
            self._device_state_cache_writer_thread = thread
            thread.start()
            return True

    def _persist_device_state_snapshot(self, payload: dict) -> bool:
        """Atomically persist one immutable worker-owned cache snapshot."""
        tmp = ""
        try:
            path = self._device_state_path()
            directory = os.path.dirname(path) or "."
            os.makedirs(directory, exist_ok=True)
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.monotonic_ns()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, sort_keys=True)
                f.write("\n")
            os.replace(tmp, path)
            return True
        except Exception:
            if tmp:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
            return False

    def _persist_device_state_generation(self, payload: dict, generation: int) -> bool:
        """Persist a snapshot and publish actual write metadata back to RAM."""
        written_at = time.time()
        payload["cache_generation"] = generation
        publisher = getattr(self, "_group_event_publisher", None)
        if publisher is not None:
            payload["owner_event_boot"] = publisher.boot
        written_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(written_at))
        payload["cache_written_at"] = written_at
        payload["cache_written_iso"] = written_iso
        try:
            success = bool(self._persist_device_state_snapshot(payload))
        except Exception:
            success = False
        finished_mono = time.monotonic()
        cv = self._device_state_cache_cv
        with cv:
            self._device_state_cache_last_attempt_mono = finished_mono
            if success:
                self._device_state_cache_persisted_generation = max(
                    int(getattr(self, "_device_state_cache_persisted_generation", 0) or 0),
                    int(generation),
                )
                self._device_state_cache_write_count = int(
                    getattr(self, "_device_state_cache_write_count", 0) or 0
                ) + 1
                self._device_state_cache_write_fail = 0
                # A newer update may have arrived while the snapshot was on
                # disk.  Merge only persistence metadata into that newer RAM
                # state; do not mark it as another user/device update.
                self.device_state_cache = _merge_dict(
                    getattr(self, "device_state_cache", {}) or {},
                    {
                        "cache_written_at": written_at,
                        "cache_written_iso": written_iso,
                    },
                )
            else:
                self._device_state_cache_write_fail = int(
                    getattr(self, "_device_state_cache_write_fail", 0) or 0
                ) + 1
        return success

    def _device_state_cache_writer_loop(self):
        """Persist the latest full cache snapshot at a bounded rate."""
        cv = self._device_state_cache_cv
        while True:
            with cv:
                while (
                    not bool(getattr(self, "_device_state_cache_dirty", False))
                    and not bool(getattr(self, "_device_state_cache_writer_stop", False))
                ):
                    cv.wait()

                stopping = bool(getattr(self, "_device_state_cache_writer_stop", False))
                generation = int(getattr(self, "_device_state_cache_generation", 0) or 0)
                persisted = int(getattr(self, "_device_state_cache_persisted_generation", 0) or 0)
                if stopping and generation <= persisted:
                    return

                if not stopping:
                    interval = float(getattr(self, "_device_state_cache_write_interval_s", 0.5) or 0.5)
                    last_attempt = float(getattr(self, "_device_state_cache_last_attempt_mono", 0.0) or 0.0)
                    remaining = (last_attempt + interval) - time.monotonic()
                    if remaining > 0.0:
                        # Further publishers only replace the pending full
                        # state, so they do not wake this timed wait repeatedly.
                        cv.wait(timeout=remaining)
                        continue

                generation = int(getattr(self, "_device_state_cache_generation", 0) or 0)
                payload = copy.deepcopy(getattr(self, "device_state_cache", {}) or {})
                self._device_state_cache_dirty = False

            success = self._persist_device_state_generation(payload, generation)

            with cv:
                current_generation = int(getattr(self, "_device_state_cache_generation", 0) or 0)
                persisted = int(getattr(self, "_device_state_cache_persisted_generation", 0) or 0)
                if (not success) or current_generation > persisted:
                    self._device_state_cache_dirty = True
                stopping = bool(getattr(self, "_device_state_cache_writer_stop", False))
                if stopping:
                    # On shutdown, drain every update that raced the snapshot.
                    # A failed final atomic write cannot be repaired here
                    # indefinitely, so leave dirty=true for diagnostics and exit.
                    if not success:
                        return
                    if current_generation <= persisted:
                        return

    def _stop_device_state_cache_writer(self, timeout: float = 2.0) -> bool:
        """Reject new cache updates, flush the latest generation, and join."""
        self._init_device_state_cache_writer()
        cv = self._device_state_cache_cv
        with cv:
            self._device_state_cache_accepting = False
            self._device_state_cache_writer_stop = True
            cv.notify_all()
            thread = getattr(self, "_device_state_cache_writer_thread", None)
        publisher = getattr(self, "_group_event_publisher", None)
        if publisher is not None:
            publisher.close()

        # A partially constructed object may never have started the worker.
        # Preserve the same final-flush guarantee without holding the cache lock.
        if thread is None or not thread.is_alive():
            with cv:
                generation = int(getattr(self, "_device_state_cache_generation", 0) or 0)
                persisted = int(getattr(self, "_device_state_cache_persisted_generation", 0) or 0)
                if generation <= persisted:
                    return True
                payload = copy.deepcopy(getattr(self, "device_state_cache", {}) or {})
                self._device_state_cache_dirty = False
            success = self._persist_device_state_generation(payload, generation)
            if not success:
                with cv:
                    self._device_state_cache_dirty = True
            return success

        if thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        return not thread.is_alive()

    def _write_device_state_cache(self, patch: dict):
        try:
            self._init_device_state_cache_writer()
            now = time.time()
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
            patch = dict(patch or {})
            host_only = bool(patch.pop("_host_only", False))
            source = str(patch.get("source", "") or "")
            service_patch = patch.get("service") if isinstance(patch.get("service"), dict) else {}
            service_patch = dict(service_patch)
            if source in {"bulk_evt1", "bulk_stat", "ep0_stat"}:
                service_patch.update({
                    "last_packet_at": now,
                    "last_packet_iso": now_iso,
                    "last_source": source,
                    "event_lag": False,
                    "event_lag_age_s": 0.0,
                    "heartbeat_s": SERVICE_HEARTBEAT_S,
                    "lag_threshold_s": SERVICE_LAG_S,
                })
            if source == "bulk_evt1":
                evt = dict((patch.get("evt1") or {}).get("last") or {})
                evt["host_time"] = now
                evt["host_iso"] = now_iso
                event_type = evt.get("event_type")
                event_key = EVT1_EVENT_NAMES.get(int(event_type or 0), f"evt1_{int(event_type or 0):02x}")
                evt["event_name"] = event_key
                patch["evt1"] = _merge_dict(patch.get("evt1") if isinstance(patch.get("evt1"), dict) else {}, {"last": evt})
                service_patch.update({
                    "last_evt1_at": now,
                    "last_evt1_iso": now_iso,
                    "last_evt1_type": event_key,
                    "last_evt1_seq": evt.get("event_seq"),
                })
                patch["event_updates"] = {
                    event_key: {
                        "updated_at": now,
                        "updated_iso": now_iso,
                        "event_seq": evt.get("event_seq"),
                        "event_type": event_type,
                    }
                }
            if service_patch:
                patch["service"] = _merge_dict(patch.get("service") if isinstance(patch.get("service"), dict) else {}, service_patch)

            cv = self._device_state_cache_cv
            with cv:
                if not bool(getattr(self, "_device_state_cache_accepting", True)):
                    return False
                payload = _merge_dict(getattr(self, "device_state_cache", {}) or {}, patch)
                payload["schema"] = 1
                if (not host_only) or not payload.get("updated_at"):
                    payload["updated_at"] = now
                    payload["updated_iso"] = now_iso
                self.device_state_cache = payload
                self._device_state_cache_generation = int(
                    getattr(self, "_device_state_cache_generation", 0) or 0
                ) + 1
                was_dirty = bool(getattr(self, "_device_state_cache_dirty", False))
                self._device_state_cache_dirty = True
                publisher = getattr(self, "_group_event_publisher", None)
                if publisher is not None:
                    # Only enqueue a small immutable projection; socket I/O
                    # and serialization belong to the separate event worker.
                    publisher.update(payload, self._device_state_cache_generation)
                if not was_dirty:
                    cv.notify()
            return True
        except Exception:
            return False

    def _parse_stat_device_state(self, packet: bytes) -> dict:
        bs = bytes(packet or b"")
        stat_version = _u8_at(bs, 4)
        full_v5 = bool((stat_version or 0) >= 5 and len(bs) >= STAT_PACKET_V5_LEN)
        full_v6 = bool((stat_version or 0) >= 6 and len(bs) >= STAT_PACKET_V6_LEN)
        flags_rt = _u16_at(bs, 48)
        flags2 = _u16_at(bs, 50)
        sync_local_status_raw = _u8_at(bs, 99) if full_v5 else None
        sync_seen_mask_raw = (_u32_at(bs, 100) or 0) if full_v5 else 0
        sync_node_count_raw = _u8_at(bs, 104) if full_v5 else None
        previous = getattr(self, "device_state_cache", {}) or {}
        previous_events = previous.get("events") if isinstance(previous.get("events"), dict) else {}
        sensor_map_event = (
            previous_events.get("sensor_map")
            if isinstance(previous_events.get("sensor_map"), dict)
            else {}
        )
        sync_state_event = (
            previous_events.get("sync_state")
            if isinstance(previous_events.get("sync_state"), dict)
            else {}
        )
        expected_mask = None
        if sensor_map_event.get("valid") is True:
            expected_mask = sensor_map_event.get("sync_seen_mask")
        elif isinstance(sync_state_event.get("sync_seen_mask"), int):
            expected_mask = sync_state_event.get("sync_seen_mask")
        expected_local_node_id = sensor_map_event.get(
            "local_node_id",
            sync_state_event.get("local_node_id"),
        )
        sync_layout_valid = False
        if full_v6:
            bit_count = int(sync_seen_mask_raw).bit_count()
            sync_layout_valid = bool(
                sync_node_count_raw is not None
                and int(sync_node_count_raw) == bit_count
                and 0 <= bit_count <= 32
            )
            if sync_layout_valid and isinstance(expected_mask, int) and expected_mask >= 0:
                sync_layout_valid = int(sync_seen_mask_raw) == (int(expected_mask) & 0xFFFFFFFF)
            local_node_id_raw = (
                (int(sync_local_status_raw) & 0x1F)
                if sync_local_status_raw is not None
                else -1
            )
            if sync_layout_valid and isinstance(expected_local_node_id, int):
                sync_layout_valid = local_node_id_raw == int(expected_local_node_id)
            if sync_layout_valid:
                sync_layout_valid = bool(sync_seen_mask_raw & (1 << local_node_id_raw))
            if sync_layout_valid:
                status_bytes_v6 = bytes(bs[105:137])
                sync_layout_valid = len(status_bytes_v6) == 32 and all(
                    (int(status_bytes_v6[node_id]) & 0x1F) == node_id
                    for node_id in range(32)
                    if bool(sync_seen_mask_raw & (1 << node_id))
                )
        if full_v5 and not full_v6:
            bit_count = int(sync_seen_mask_raw).bit_count()
            sync_layout_valid = bool(
                sync_node_count_raw is not None
                and 0 <= int(sync_node_count_raw) <= 32
                and int(sync_node_count_raw) in {bit_count, max(0, bit_count - 1)}
            )
            if isinstance(expected_mask, int) and expected_mask >= 0:
                sync_layout_valid = sync_layout_valid and int(sync_seen_mask_raw) == int(expected_mask)
        sync_local_status = sync_local_status_raw if sync_layout_valid else None
        sync_seen_mask = sync_seen_mask_raw if sync_layout_valid else 0
        sync_node_count = sync_node_count_raw if sync_layout_valid else None
        local = _status_byte_fields(sync_local_status or 0, "Local") if sync_local_status is not None else {}
        local_node_id = (int(sync_local_status) & 0x1F) if sync_local_status is not None else -1
        local_optic_active = bool((flags_rt or 0) & 0x0020)
        master_optic_active = bool((flags_rt or 0) & 0x0080)
        any_optic_active = bool((flags_rt or 0) & 0x0100)
        if local:
            try:
                if int(local.get("status_node_id", 0) or 0) > 0:
                    local["node_id"] = int(local.get("status_node_id", 0) or 0)
            except Exception:
                pass
            local["optic_active_sync"] = bool((sync_local_status or 0) & 0x20)
            if flags_rt is not None:
                local["optic_active_flags_runtime"] = local_optic_active
                local["optic_master_flags_runtime"] = master_optic_active
                local["optic_any_flags_runtime"] = any_optic_active
                local["optic_active"] = local_optic_active
                local["optic_indication_allow"] = bool(local_optic_active or master_optic_active or any_optic_active)
                local["tx_enabled"] = bool(flags_rt & 0x0010)
        remote = []
        if full_v5:
            status_end = 137 if full_v6 else 136
            status_bytes = bytes(bs[105:min(len(bs), status_end)])
            for idx, value in enumerate(status_bytes):
                node_id = idx if full_v6 else idx + 1
                mask_bit = node_id if full_v6 else idx
                seen = bool(sync_seen_mask & (1 << mask_bit))
                if not seen:
                    continue
                if node_id == local_node_id:
                    continue
                item = _status_byte_fields(value, f"Node {node_id}", node_id)
                item.update({
                    "seen": seen,
                    "recent": seen,
                    "online": seen,
                    "packet_validated": bool(full_v6),
                    "optic_active_raw": bool(item.get("optic_active")),
                    "optic_active_physical": bool(item.get("optic_active")),
                    "source": "stat_v6_direct" if full_v6 else "stat_v5_compat",
                })
                remote.append(item)
        result = {
            "source": "bulk_stat",
            "device_responded": True,
            "stat": {
                "len": len(bs),
                "raw_packet_hex": bs.hex(),
                "version": stat_version,
                "layout_complete": full_v5,
                "direct_device_id_layout": bool(full_v6 and sync_layout_valid),
                "sync_layout_valid": sync_layout_valid,
                "cur_samples": _u16_at(bs, 6),
                "frame_bytes": _u16_at(bs, 8),
                "flags_runtime": flags_rt,
                "flags2": flags2,
                "streaming": bool((flags_rt or 0) & 0x0001),
                "stream_active": bool((flags_rt or 0) & 0x0008),
                "host_rx_alive": bool((flags_rt or 0) & 0x0040),
                "tx_enabled": bool((flags_rt or 0) & 0x0010),
                "optic_active": local_optic_active,
                "master_optic_active": master_optic_active,
                "any_optic_active": any_optic_active,
                "optic_gate_active": bool(local_optic_active or master_optic_active or any_optic_active),
                "optic_hold_ds": _u16_at(bs, 96) if full_v5 else None,
                "led_pattern": _u8_at(bs, 98) if full_v5 else None,
                "sync_local_status": sync_local_status,
                "sync_seen_mask": sync_seen_mask,
                "sync_node_count": sync_node_count,
                "sync_local_status_raw": sync_local_status_raw,
                "sync_seen_mask_raw": sync_seen_mask_raw,
                "sync_node_count_raw": sync_node_count_raw,
            },
        }
        # Bulk streaming deliberately stays STAT v5 (136 bytes) on firmware
        # 1.2.13.  Its compatibility tail is not authoritative and must not
        # overwrite a validated EVT1 SENSOR_MAP between map events.
        if full_v6 and sync_layout_valid:
            remote_updated_at = time.time()
            result["sensors"] = {
                "local": local,
                "remote": remote,
                "remote_count": len(remote),
                "remote_updated_at": remote_updated_at,
                "remote_valid": True,
                "remote_validation": "stat_v6_direct_id_layout",
                "remote_seen_mask": sync_seen_mask,
            }
        return result

    def _parse_evt1_device_state(self, packet: bytes) -> dict:
        bs = bytes(packet or b"")
        if len(bs) < 16 or bs[:4] != b"EVT1":
            return {}
        version = _u8_at(bs, 4) or 0
        event_type = _u8_at(bs, 5) or 0
        payload_len = _u16_at(bs, 6) or 0
        payload = bs[16:16 + payload_len]
        evt = {
            "version": version,
            "event_type": event_type,
            "event_name": EVT1_EVENT_NAMES.get(event_type, f"evt1_{event_type:02x}"),
            "event_seq": _u32_at(bs, 8),
            "device_tick_ms": _u32_at(bs, 12),
            "payload_len": payload_len,
        }
        patch = {
            "source": "bulk_evt1",
            "device_responded": True,
            "evt1": {"last": evt},
        }
        if event_type == 0x00 and len(payload) >= 47:
            def _ascii_field(off: int, size: int) -> str:
                try:
                    return bytes(payload[off:off + size]).decode("ascii", errors="ignore").strip("\x00 ")
                except Exception:
                    return ""

            uid_w0 = _u32_at(payload, 5)
            uid_w1 = _u32_at(payload, 9)
            uid_w2 = _u32_at(payload, 13)
            fw_major = _u8_at(payload, 1)
            fw_minor = _u8_at(payload, 2)
            fw_patch = _u8_at(payload, 3)
            fw_build = _u8_at(payload, 4)
            version_text = ".".join(str(int(v or 0)) for v in (fw_major, fw_minor, fw_patch))
            if fw_build:
                version_text += f"+{int(fw_build)}"
            fw_info = {
                "payload_version": _u8_at(payload, 0),
                "fw_major": fw_major,
                "fw_minor": fw_minor,
                "fw_patch": fw_patch,
                "fw_build": fw_build,
                "fw_version": version_text,
                "uid_w0": uid_w0,
                "uid_w1": uid_w1,
                "uid_w2": uid_w2,
                "uid96": "".join(f"{int(v or 0):08X}" for v in (uid_w2, uid_w1, uid_w0)),
                "uid96_words": " ".join(f"{int(v or 0):08X}" for v in (uid_w2, uid_w1, uid_w0)),
                "build_date": _ascii_field(17, 10),
                "build_time": _ascii_field(27, 8),
                "git_hash": _ascii_field(35, 12),
            }
            patch["events"] = {"fw_info": fw_info}
            patch["identity"] = {"stm32": fw_info}
        elif event_type == 0x01 and len(payload) >= 2:
            temp_c = _i16_at(payload, 0)
            patch["temperature"] = {"temp_c": temp_c, "source": "EVT1_TEMP_C"}
            patch["events"] = {"temp_c": {"temp_c": temp_c}}
        elif event_type == 0x02 and len(payload) >= 16:
            temp_c = _i16_at(payload, 2)
            mcu_adc = {
                "payload_version": _u8_at(payload, 0),
                "flags": _u8_at(payload, 1),
                "temp_c": temp_c,
                "vdda_mv": _u16_at(payload, 4),
                "vbat_mv": _u16_at(payload, 6),
                "raw_temp": _u16_at(payload, 8),
                "raw_vrefint": _u16_at(payload, 10),
                "raw_vbat": _u16_at(payload, 12),
            }
            patch["temperature"] = {"temp_c": temp_c, "source": "EVT1_MCU_ADC"}
            patch["events"] = {"mcu_adc": mcu_adc}
        elif event_type == 0x10 and len(payload) >= 8:
            flags = _u8_at(payload, 1) or 0
            optic = {
                "payload_version": _u8_at(payload, 0),
                "flags": flags,
                "optic_active": bool(flags & 0x01),
                "optic_indication_allow": bool(flags & 0x01),
                "tx_enabled": bool(flags & 0x02),
                "optic_power": _u8_at(payload, 2),
                "led_pattern": _u8_at(payload, 3),
                "optic_hold_ds": _u16_at(payload, 4),
            }
            patch["events"] = {"optic_state": optic}
            patch["sensors"] = {"local": {
                "label": "Local",
                "optic_active": optic["optic_active"],
                "optic_indication_allow": optic["optic_indication_allow"],
                "tx_enabled": optic["tx_enabled"],
                "optic_power": optic["optic_power"],
                "led_pattern": optic["led_pattern"],
                "optic_hold_ds": optic["optic_hold_ds"],
            }}
        elif event_type == 0x11 and len(payload) >= 16:
            raw_mode = _u8_at(payload, 1)
            display_char_i = _u8_at(payload, 3) or 0
            try:
                display_char = chr(display_char_i) if 32 <= display_char_i < 127 else ""
            except Exception:
                display_char = ""
            local_node_id = _u8_at(payload, 4) or 0
            assignment_status = _u8_at(payload, 13) or 0
            saved_role_code = assignment_status & 0x03
            assigned_role = {1: "master", 2: "slave"}.get(saved_role_code, "")
            device_id_assigned = bool(assignment_status & 0x04)
            local_status = (assignment_status & 0xE0) | (local_node_id & 0x1F)
            sync_seen_mask = _u32_at(payload, 8) or 0
            sync = {
                "payload_version": _u8_at(payload, 0),
                "raw_mode": raw_mode,
                "role": {0: "master", 1: "slave", 2: "off"}.get(raw_mode, "---"),
                "display_mode": _u8_at(payload, 2),
                "display_char": display_char,
                "local_node_id": local_node_id,
                "device_id": local_node_id,
                "device_id_assigned": device_id_assigned,
                "assignment_status": assignment_status,
                "saved_role_code": saved_role_code,
                "assigned_role": assigned_role,
                "role_persisted": bool((_u8_at(payload, 7) or 0) & 0x20),
                "active_status_count": _u8_at(payload, 5),
                "total_devices": _u8_at(payload, 6),
                "flags": _u8_at(payload, 7),
                "sync_seen_mask": sync_seen_mask,
                "sync_seen_mask_hex": f"0x{sync_seen_mask:08X}",
                "display_value": _u8_at(payload, 12),
                "local_status_flags": local_status,
                "local_status_raw": assignment_status,
                "sync_age_ds": _u16_at(payload, 14),
                "raw_payload_hex": bytes(payload).hex(),
                "raw_packet_hex": bs.hex(),
            }
            local = _status_byte_fields(local_status, "Local")
            try:
                local_sync_active = bool(local.get("optic_active", False))
                local["optic_active_sync"] = local_sync_active
                local["optic_indication_allow_sync"] = local_sync_active
                local.pop("optic_active", None)
                local.pop("optic_indication_allow", None)
            except Exception:
                pass
            try:
                local_node_id = int(sync.get("local_node_id", 0) or 0)
                if device_id_assigned and 0 <= local_node_id <= 31:
                    local["node_id"] = local_node_id
                    local["device_id_assigned"] = True
            except Exception:
                pass
            patch["sync"] = sync
            patch["events"] = {"sync_state": sync}
            patch["sensors"] = {"local": local}
        elif event_type == 0x12 and len(payload) >= 16:
            flags = _u8_at(payload, 1) or 0
            mode = {
                "payload_version": _u8_at(payload, 0),
                "flags": flags,
                "streaming": bool(flags & 0x01),
                "diag": bool(flags & 0x02),
                "pending_init": bool(flags & 0x04),
                "stream_active": bool(flags & 0x08),
                "full_mode": bool(flags & 0x10),
                "async_mode": bool(flags & 0x20),
                "tx_enabled": bool(flags & 0x40),
                "host_rx_alive": bool(flags & 0x80),
                "stream_mode": _u8_at(payload, 2),
                "ch_mode": _u8_at(payload, 3),
                "host_profile": _u8_at(payload, 4),
                "avg_n": _u8_at(payload, 5),
                "cur_samples_per_frame": _u16_at(payload, 6),
                "frame_samples_req": _u16_at(payload, 8),
                "trunc_samples": _u16_at(payload, 10),
                "sync_mode_public": _u8_at(payload, 12),
                "sync_mode_host_forced": _u8_at(payload, 13),
            }
            patch["events"] = {"mode_state": mode}
            patch["mode"] = mode
        elif event_type == 0x13 and len(payload) >= 16:
            flags = _u8_at(payload, 1) or 0
            err = {
                "payload_version": _u8_at(payload, 0),
                "flags": flags,
                "last_error_nonzero": bool(flags & 0x01),
                "usb_in_busy_or_inflight": bool(flags & 0x02),
                "last_error": _u16_at(payload, 2),
                "error_counter": _u32_at(payload, 4),
                "tx_force_idle_count": _u32_at(payload, 8),
                "tx_drop_recovery_count": _u32_at(payload, 12),
            }
            patch["events"] = {"error_state": err}
        elif event_type == 0x14 and len(payload) >= 40:
            payload_version = _u8_at(payload, 0) or 0
            local_node_id = _u8_at(payload, 1) or 0
            flags = _u8_at(payload, 2) or 0
            node_count = _u8_at(payload, 3) or 0
            seen_mask = _u32_at(payload, 4) or 0
            id_assigned = bool(flags & 0x01)
            local_master = bool(flags & 0x02)
            valid = bool(
                payload_version == 1
                and 0 <= local_node_id <= 31
                and node_count == int(seen_mask).bit_count()
                and (not id_assigned or bool(seen_mask & (1 << local_node_id)))
            )
            now = time.time()
            local = {}
            remote = []
            if valid:
                for node_id in range(32):
                    if not bool(seen_mask & (1 << node_id)):
                        continue
                    item = _status_byte_fields(
                        int(payload[8 + node_id]) & 0xFF,
                        "Local" if id_assigned and node_id == local_node_id else f"Node {node_id}",
                        node_id,
                    )
                    if (int(payload[8 + node_id]) & 0x1F) != node_id:
                        valid = False
                        local = {}
                        remote = []
                        break
                    item.update({
                        "seen": True,
                        "recent": True,
                        "online": True,
                        "packet_validated": True,
                        "optic_active_raw": bool(item.get("optic_active")),
                        "optic_active_physical": bool(item.get("optic_active")),
                        "source": "evt1_sensor_map",
                        "sensor_updated_at": now,
                        "sensor_updated_iso": time.strftime(
                            "%Y-%m-%dT%H:%M:%S%z",
                            time.localtime(now),
                        ),
                    })
                    if id_assigned and node_id == local_node_id:
                        item["local"] = True
                        item["master"] = local_master
                        local = item
                    else:
                        item["local"] = False
                        remote.append(item)
            sensor_map = {
                "payload_version": payload_version,
                "local_node_id": local_node_id,
                "device_id": local_node_id,
                "device_id_assigned": id_assigned,
                "local_master": local_master,
                "node_count": node_count,
                "sync_seen_mask": seen_mask,
                "sync_seen_mask_hex": f"0x{seen_mask:08X}",
                "valid": valid,
                "raw_payload_hex": bytes(payload).hex(),
                "raw_packet_hex": bs.hex(),
            }
            patch["events"] = {"sensor_map": sensor_map}
            patch["sync"] = {
                "sync_seen_mask": seen_mask if valid else 0,
                "sync_seen_mask_hex": f"0x{seen_mask if valid else 0:08X}",
                "active_status_count": node_count if valid else 0,
                "total_devices": node_count if valid else 0,
                "sensor_map_valid": valid,
            }
            patch["sensors"] = {
                "local": local,
                "remote": remote,
                "remote_count": len(remote),
                "remote_updated_at": now,
                "remote_valid": valid,
                "remote_validation": "evt1_sensor_map" if valid else "evt1_sensor_map_invalid",
                "remote_seen_mask": seen_mask if valid else 0,
            }
        return patch

    def _evt1_payload_sane(self, packet: bytes) -> bool:
        try:
            bs = bytes(packet or b"")
            if len(bs) < 16 or bs[:4] != b"EVT1":
                return False
            event_type = int(bs[5]) & 0xFF
            payload_len = int.from_bytes(bs[6:8], "little")
            expected_len = EVT1_PAYLOAD_LENGTHS.get(event_type)
            if expected_len is None or payload_len != expected_len or len(bs) != 16 + expected_len:
                return False
            payload = bs[16:]
            payload_version = int(payload[0]) & 0xFF if payload else 0
            if payload_version == 0 or payload_version > 10:
                return False
            if event_type == 0x11:
                raw_mode = int(payload[1]) & 0xFF
                display_char = int(payload[3]) & 0xFF
                local_node = int(payload[4]) & 0xFF
                active_count = int(payload[5]) & 0xFF
                total_devices = int(payload[6]) & 0xFF
                display_value = int(payload[12]) & 0xFF
                if raw_mode not in (0, 1, 2):
                    return False
                if display_char not in (0, ord("M"), ord("S"), ord("O")):
                    return False
                if local_node > 31 or active_count > 31 or total_devices > 32 or display_value > 31:
                    return False
            elif event_type == 0x14:
                local_node = int(payload[1]) & 0xFF
                flags = int(payload[2]) & 0xFF
                node_count = int(payload[3]) & 0xFF
                seen_mask = int.from_bytes(payload[4:8], "little")
                if local_node > 31 or node_count > 32 or node_count != seen_mask.bit_count():
                    return False
                if bool(flags & 0x01) and not bool(seen_mask & (1 << local_node)):
                    return False
            return True
        except Exception:
            return False

    def _stat_packet_sane(self, packet: bytes) -> bool:
        try:
            bs = bytes(packet or b"")
            if len(bs) < STAT_PACKET_MIN_LEN or bs[:4] != b"STAT":
                return False
            expected_len = _stat_packet_len_from_header(bs)
            if len(bs) != expected_len:
                return False
            version = _u8_at(bs, 4) or 0
            cur_samples = _u16_at(bs, 6) or 0
            frame_bytes = _u16_at(bs, 8) or 0
            if version <= 0 or version > 10:
                return False
            if cur_samples <= 0 or cur_samples > 8192:
                return False
            if frame_bytes <= 0 or frame_bytes > 65535:
                return False
            return True
        except Exception:
            return False

    def _service_next_known(self, mv, off: int, end: int) -> bool:
        if off >= end:
            return True
        try:
            if off + 2 <= end and bytes(mv[off:off + 2]) == b"\x5A\xA5":
                return True
            if off + 4 <= end and bytes(mv[off:off + 4]) in (b"STAT", b"EVT1"):
                return True
            if int(mv[off]) == 0x31 and (end - off) <= 16:
                return True
        except Exception:
            pass
        return False

    def _service_packet_len(self, mv, pos: int, end: int):
        try:
            if pos + 4 <= end and bytes(mv[pos:pos + 4]) == b"EVT1":
                if pos + 16 > end:
                    return None
                event_type = int(mv[pos + 5]) & 0xFF
                payload_len = int.from_bytes(bytes(mv[pos + 6:pos + 8]), "little")
                expected_len = EVT1_PAYLOAD_LENGTHS.get(event_type)
                if expected_len is None or payload_len != expected_len:
                    return -1
                total = 16 + payload_len
                if pos + total <= end:
                    packet = bytes(mv[pos:pos + total])
                    if not self._evt1_payload_sane(packet):
                        return -1
                    return total
                return None
            if pos + 4 <= end and bytes(mv[pos:pos + 4]) == b"STAT":
                if pos + 5 > end:
                    return None
                packet_len = _stat_packet_len_from_header(mv, pos)
                rem = end - pos
                if rem < packet_len:
                    return None
                packet = bytes(mv[pos:pos + packet_len])
                if not self._stat_packet_sane(packet):
                    return -1
                return packet_len
            if pos < end and int(mv[pos]) == 0x31 and (end - pos) <= 16:
                return end - pos
        except Exception:
            return None
        return None

    def _find_next_known_packet(self, buf: bytearray) -> int:
        try:
            data = bytes(buf)
            found = []
            for marker in (b"\x5A\xA5", b"STAT", b"EVT1"):
                idx = data.find(marker)
                if idx >= 0:
                    found.append(idx)
            return min(found) if found else -1
        except Exception:
            return -1

    def _trim_to_possible_packet_tail(self, buf: bytearray):
        try:
            if not buf:
                return
            data = bytes(buf)
            max_keep = min(3, len(data))
            keep = 0
            for n in range(1, max_keep + 1):
                tail = data[-n:]
                if any(marker.startswith(tail) for marker in (b"\x5A\xA5", b"STAT", b"EVT1")):
                    keep = n
            if keep > 0:
                del buf[:len(buf) - keep]
            else:
                buf.clear()
        except Exception:
            try:
                del buf[:max(0, len(buf) - 1)]
            except Exception:
                pass

    def _handle_service_packet(self, packet: bytes):
        try:
            bs = bytes(packet or b"")
            now = time.time()
            if len(bs) >= 4 and bs[:4] == b"STAT":
                self.last_stat = bs
                self.last_stat_t = now
                self.last_service_t = now
                self.service_packets = int(getattr(self, "service_packets", 0) or 0) + 1
                self.stat_packets = int(getattr(self, "stat_packets", 0) or 0) + 1
                self.service_lag_reported = False
                self._write_device_state_cache(self._parse_stat_device_state(bs))
                return
            if len(bs) >= 16 and bs[:4] == b"EVT1":
                self.last_evt1 = bs
                self.last_service_t = now
                self.last_evt1_t = now
                self.service_packets = int(getattr(self, "service_packets", 0) or 0) + 1
                self.evt1_packets = int(getattr(self, "evt1_packets", 0) or 0) + 1
                self.service_lag_reported = False
                patch = self._parse_evt1_device_state(bs)
                if patch:
                    self._write_device_state_cache(patch)
                return
            if bs and bs[0] == 0x31 and len(bs) <= 16:
                self.last_err_step_pkt = bs
                self.last_err_step_t = time.time()
                if len(bs) >= 3:
                    self.last_err_step_vals = (int(bs[1]), int(bs[2]))
                else:
                    self.last_err_step_vals = None
        except Exception:
            pass

    def _maybe_report_service_lag(self, now: float | None = None):
        try:
            now = time.time() if now is None else float(now)
            if not bool(getattr(self, "_working_seen", False)):
                return
            last_evt = float(getattr(self, "last_evt1_t", 0.0) or 0.0)
            if last_evt <= 0.0:
                last_evt = float(getattr(self, "connected_t", now) or now)
            age = max(0.0, now - last_evt)
            if age < SERVICE_LAG_S:
                return
            last_write = float(getattr(self, "service_lag_last_write_t", 0.0) or 0.0)
            if (now - last_write) < SERVICE_LAG_WRITE_S:
                return
            self.service_lag_reported = True
            self.service_lag_last_write_t = now
            self._write_device_state_cache({
                "_host_only": True,
                "service": {
                    "event_lag": True,
                    "event_lag_age_s": age,
                    "heartbeat_s": SERVICE_HEARTBEAT_S,
                    "lag_threshold_s": SERVICE_LAG_S,
                    "last_evt1_at": last_evt if last_evt > 0.0 else None,
                    "last_evt1_age_s": age,
                    "last_rx_at": float(getattr(self, "last_rx_t", 0.0) or 0.0),
                    "rx_cnt_ch0": int(getattr(self, "rx_cnt_ch0", 0) or 0),
                    "rx_cnt_ch1": int(getattr(self, "rx_cnt_ch1", 0) or 0),
                    "evt1_packets": int(getattr(self, "evt1_packets", 0) or 0),
                    "stat_packets": int(getattr(self, "stat_packets", 0) or 0),
                    "service_packets": int(getattr(self, "service_packets", 0) or 0),
                },
            })
        except Exception:
            pass
    
    def _rx_loop(self):
        buf = bytearray()
        MAGIC_LE = b"\x5A\xA5"
        while self._running and not self.disconnected:
            # Очищаем устаревшие данные если запрошен flush после рестарта.
            # Это нужно делать ДО чтения, чтобы мусор от старой сессии не попал
            # в деффреймер вместе с новыми кадрами.
            if getattr(self, '_rx_flush_requested', False):
                if buf:
                    print(f"[rx] flush stale buf ({len(buf)} bytes) after restart", flush=True)
                buf.clear()
                self._rx_flush_requested = False
            try:
                # Читаем умеренными порциями как в рабочих скриптах (2KB)
                data = bytes(self.dev.read(EP_IN, 2048, timeout=1000))
            except ValueError as e:
                if 'Invalid endpoint' in str(e):
                    print(f"[disconnect] {e} => stop loop")
                    self.disconnected = True
                    break
                print("USB value err", e); time.sleep(0.1); continue
            except usb.core.USBError as e:
                if e.errno == 110: # timeout
                    # При длительном отсутствии рабочих кадров попробуем единоразовый fallback
                    now_t = time.time()
                    if bool(getattr(self, 'local_recovery_enabled', False)) and (not self.passive_connect) and (not self._working_seen) and (not self._fallback_done) and (now_t - self.connected_t > 1.6):
                        self._do_fallback_start()
                    # Keepalive/мягкий рестарт: если давно не было вообще RX
                    if (now_t - self.last_rx_t) > float(getattr(self, 'keepalive_sec', 2.0)) and (now_t - self.keepalive_last) > 0.9:
                        # EP0 keepalive, даже если bulk OUT залип
                        self._get_status_ep0()
                        self.keepalive_last = now_t
                    if bool(getattr(self, 'local_recovery_enabled', False)) and (not self.passive_connect) and (not getattr(self, 'disable_restart', False)) and (now_t - self.last_rx_t) > float(getattr(self, 'restart_after', 4.0)) and (now_t - self.last_restart_t) > float(getattr(self, 'restart_min_interval', 3.0)):
                        try:
                            # Выполним мягкий «чистый» рестарт: STOP + очистка EP + переустановка altsetting
                            self._prepare_clean_start(stop_first=True)
                            try:
                                if self.profile is not None:
                                    self.send_cmd(CMD_SET_PROFILE, bytes([int(self.profile) & 0xFF])); time.sleep(0.02)
                            except Exception:
                                pass
                            try:
                                self.send_cmd(CMD_SET_FULL_MODE, bytes([1 if self.full else 0])); time.sleep(0.02)
                            except Exception:
                                pass
                            if self.frame_samples is not None and int(self.profile or 2) == 2:
                                try:
                                    ns = max(1, int(self.frame_samples)) & 0xFFFF
                                    self.send_cmd(CMD_SET_FRAME_SAMPLES, ns.to_bytes(2,'little'))
                                    time.sleep(0.02)
                                except Exception:
                                    pass
                            self._start_stream_with_tx(reason="gentle_restart"); time.sleep(0.02)
                            # После рестарта устаревшие данные в buf — мусор.
                            # Запрашиваем очистку в начале следующей итерации.
                            self._rx_flush_requested = True
                            self._prime_get_status()
                            self._kick_cdc_start()
                            self.last_restart_t = time.time()
                            self.restart_attempts += 1
                            print("[kick] gentle restart (no RX)")
                        except Exception as e2:
                            print("[kick] gentle restart failed:", e2)
                        # По достижении нескольких неудачных рестартов можно попробовать жёсткий reset устройства (опционально)
                        try:
                            import os as _os
                            if self.restart_attempts >= 3 and str(_os.getenv('BMI30_USB_HARD_RESET','0')).lower() not in ('0','false','no'):
                                print('[reset] Performing usb device reset()')
                                try:
                                    self.dev.reset()
                                except Exception as _e:
                                    print('[reset] dev.reset() failed:', _e)
                                self.restart_attempts = 0
                        except Exception:
                            pass
                        # Или принудительно инициировать полное переподключение на верхнем уровне
                        if self.restart_attempts >= 3 and getattr(self, 'force_reopen', True):
                            print('[reopen] Marking stream as disconnected to force full reopen')
                            self.disconnected = True
                            break
                    continue
                # fatal disconnect codes: 5=EIO, 19=ENODEV, 32=EPIPE
                if e.errno in (5,19,32):
                    print(f"[disconnect] USB error {e.errno} => stop loop")
                    self.disconnected=True
                    break
                # 16=EBUSY: транзиентная занятость — подождём и продолжим
                if e.errno == 16:
                    print(f"[busy] USB error {e.errno} (Resource busy)")
                    time.sleep(0.05)
                    continue
                print("USB err", e); time.sleep(0.1); continue
            # Перехват служебных пакетов перед ADC deframer:
            # STAT/EVT1 приходят по тому же Bulk IN, но не участвуют в seq-проверке ADC.
            if data:
                mv = memoryview(data)
                pos = 0
                n = len(mv)
                while pos < n:
                    pkt_len = self._service_packet_len(mv, pos, n)
                    if pkt_len is None:
                        break
                    if pkt_len <= 0:
                        pos += 1
                        continue
                    self._handle_service_packet(bytes(mv[pos:pos + pkt_len]))
                    pos += int(pkt_len)
                if pos < n:
                    buf.extend(mv[pos:].tobytes())
            if data:
                self.last_rx_t = time.time()
            # Дефрамер: ищем магию 0xA55A (LE: 5A A5), затем ждём весь кадр
            while True:
                if (len(buf) >= 4 and bytes(buf[:4]) in (b"STAT", b"EVT1")) or (buf and buf[0] == 0x31 and len(buf) <= 16):
                    buf_view = memoryview(bytes(buf))
                    pkt_len = self._service_packet_len(buf_view, 0, len(buf_view))
                    if pkt_len is None:
                        break
                    if pkt_len <= 0:
                        del buf[0]
                        continue
                    if pkt_len:
                        self._handle_service_packet(bytes(buf[:pkt_len]))
                        del buf[:pkt_len]
                        continue
                if len(buf) < HDR_SIZE:
                    idx = self._find_next_known_packet(buf)
                    if idx > 0:
                        del buf[:idx]
                        continue
                    if idx < 0 and len(buf) > 3:
                        self._trim_to_possible_packet_tail(buf)
                    break
                if not (buf[0] == 0x5A and buf[1] == 0xA5):
                    idx = self._find_next_known_packet(buf)
                    if idx == -1:
                        # Оставим возможный хвост сигнатуры ADC/STAT/EVT1.
                        self._trim_to_possible_packet_tail(buf)
                        break
                    else:
                        del buf[:idx]
                        continue
                hdr_bytes = bytes(buf[:HDR_SIZE])
                try:
                    (magic,ver,flags,seq,timestamp,total_samples,zone_count,zone1_offset,zone1_length,reserved,reserved2,crc16v)= struct.unpack('<H B B I I H H I I I H H', hdr_bytes)
                except struct.error:
                    # недостаточно данных для заголовка — ждём
                    break
                if magic != MAGIC:
                    # сдвинемся на байт вперёд и поищем магию снова
                    self.magic_bad += 1
                    del buf[0]
                    continue
                # Sanity-check: отбрасываем кадры с аномальным total_samples.
                # Нулевой или слишком большой total_samples — признак битого заголовка.
                # Ждать несуществующий payload нельзя: это заморозит поток.
                # Граница 8192 покрывает все известные профили с запасом.
                if total_samples == 0 or total_samples > 8192:
                    self.magic_bad += 1
                    del buf[0]
                    continue
                payload_len = int(total_samples)*2
                frame_total = HDR_SIZE + payload_len
                if len(buf) < frame_total:
                    # ждём остаток кадра
                    break
                payload = bytes(buf[HDR_SIZE:frame_total])
                # CRC опционален: при несовпадении не отбрасываем кадр, только считаем ошибку
                if flags & VF_CRC:
                    try:
                        calc = crc16_ccitt_false(hdr_bytes[:-2])
                        calc = crc16_ccitt_false(payload, calc)
                        if calc != crc16v:
                            self.crc_bad += 1
                            self.last_crc_bad_info = {
                                "seq": int(seq),
                                "timestamp": int(timestamp),
                                "adc_id": 0 if (flags & VF_ADC0) else (1 if (flags & VF_ADC1) else -1),
                                "flags": int(flags),
                                "samples": int(total_samples),
                                "reserved": int(reserved),
                                "reserved2": int(reserved2),
                                "crc_expected": int(crc16v),
                                "crc_calc": int(calc),
                            }
                            try:
                                if int(getattr(self, '_crc_bad_log_count', 0) or 0) < int(os.getenv('BMI30_CRC_BAD_PRINT_LIMIT', '5')):
                                    self._crc_bad_log_count = int(getattr(self, '_crc_bad_log_count', 0) or 0) + 1
                                    print(
                                        f"[crc_bad] seq={seq} flags=0x{int(flags)&0xFF:02X} adc={self.last_crc_bad_info['adc_id']} "
                                        f"samples={total_samples} reserved={int(reserved)} reserved2=0x{int(reserved2)&0xFFFF:04X} "
                                        f"calc=0x{int(calc)&0xFFFF:04X} got=0x{int(crc16v)&0xFFFF:04X}",
                                        flush=True,
                                    )
                            except Exception:
                                pass
                    except Exception:
                        # если что-то пошло не так при расчёте CRC — не мешаем потоку
                        self.crc_bad += 1
                # TEST-бит (0x80):
                # - если это «чистый» тестовый кадр (нет битов ADC0/ADC1) — по умолчанию пропускаем,
                #   а при test_as_data дублируем на A и B;
                # - если вместе с TEST выставлены биты канала (DIAG: 0x81/0x82) — считаем обычным A/B кадром.
                if (flags & 0x80) and (flags & (VF_ADC0 | VF_ADC1)) == 0:
                    self.test_seen += 1
                    if self.test_as_data:
                        try:
                            fA = Frame(seq, timestamp, 0, flags, total_samples, payload, reserved=reserved, reserved2=reserved2, ver=ver)
                            fB = Frame(seq, timestamp, 1, flags, total_samples, payload, reserved=reserved, reserved2=reserved2, ver=ver)
                            self.asm.push(fA)
                            self.asm.push(fB)
                            self.frames += 2
                            self.bytes += payload_len * 2
                            self._working_seen = True
                        except Exception:
                            pass
                    del buf[:frame_total]
                    continue
                if flags & VF_ADC0:
                    adc_id = 0
                elif flags & VF_ADC1:
                    adc_id = 1
                else:
                    # неизвестный флаг — отбрасываем кадр
                    del buf[:frame_total]
                    continue
                f = Frame(seq,timestamp,adc_id,flags,total_samples,payload,reserved=reserved,reserved2=reserved2,ver=ver)
                # Автопроверка: если хост явно задал frame_samples (и это профиль 2),
                # а устройство пошло с другим total_samples — один раз попробуем пере-применить конфиг.
                try:
                    if (not self.passive_connect) and (not self._rate_mismatch_fixed) and self.frame_samples is not None and int(getattr(self, 'profile', 2) or 2) == 2:
                        exp = int(self.frame_samples)
                        if int(total_samples) != exp:
                            self._rate_mismatch_fixed = True
                            print(f"[verify] mismatch: expected {exp}, got {total_samples} → reapply config", flush=True)
                            try:
                                prof = int(getattr(self, 'profile', 2) or 2)
                                self.send_cmd(CMD_SET_PROFILE, bytes([prof & 0xFF])); time.sleep(0.01)
                            except Exception:
                                pass
                            try:
                                ns = max(1, exp) & 0xFFFF
                                self.send_cmd(CMD_SET_FRAME_SAMPLES, ns.to_bytes(2, 'little')); time.sleep(0.01)
                            except Exception:
                                pass
                            try:
                                rate = 200 if int(getattr(self, 'profile', 1) or 1) == 1 else 300
                                self.set_block_rate(rate); time.sleep(0.01)
                            except Exception:
                                pass
                            try:
                                self._start_stream_with_tx(reason="rate_mismatch"); time.sleep(0.02)
                            except Exception:
                                pass
                except Exception:
                    pass
                self.asm.push(f)
                self.frames += 1
                self.bytes += payload_len
                # Update per-channel seq continuity counters.
                try:
                    s = int(seq) & 0xFFFFFFFF
                    tsu = int(timestamp) & 0xFFFFFFFF
                    if int(adc_id) == 0:
                        self.rx_cnt_ch0 += 1
                        last = self.seq_last_ch0
                        if last is None:
                            self.seq_last_ch0 = s
                        else:
                            d = (s - int(last)) & 0xFFFFFFFF
                            if d == 0:
                                self.seq_dup_ch0 += 1
                            elif 0 < d < 0x80000000:
                                # Collect small deltas to infer nominal seq STEP (often 2).
                                if 0 < d <= 16:
                                    try:
                                        with self._ts_hist_lock:
                                            dhs = self._seq_dhist_ch0
                                            dhs[int(d)] = dhs.get(int(d), 0) + 1
                                    except Exception:
                                        pass
                                self.seq_last_ch0 = s
                            else:
                                self.seq_reset_ch0 += 1
                                self.seq_last_ch0 = s

                        # timestamp continuity for ch0
                        tlast = self.ts_last_ch0
                        if tlast is None:
                            self.ts_last_ch0 = tsu
                        else:
                            dt = (tsu - int(tlast)) & 0xFFFFFFFF
                            if dt == 0:
                                self.ts_dup_ch0 += 1
                            elif 0 < dt < 0x80000000:
                                # Record only reasonable deltas to infer nominal step (avoid huge outliers)
                                if 0 < dt <= 10_000_000:
                                    try:
                                        with self._ts_hist_lock:
                                            dh = self._ts_dhist_ch0
                                            dh[dt] = dh.get(dt, 0) + 1
                                    except Exception:
                                        pass
                                self.ts_last_ch0 = tsu
                            else:
                                self.ts_reset_ch0 += 1
                                self.ts_last_ch0 = tsu
                    else:
                        self.rx_cnt_ch1 += 1
                        last = self.seq_last_ch1
                        if last is None:
                            self.seq_last_ch1 = s
                        else:
                            d = (s - int(last)) & 0xFFFFFFFF
                            if d == 0:
                                self.seq_dup_ch1 += 1
                            elif 0 < d < 0x80000000:
                                # Collect small deltas to infer nominal seq STEP (often 2).
                                if 0 < d <= 16:
                                    try:
                                        with self._ts_hist_lock:
                                            dhs = self._seq_dhist_ch1
                                            dhs[int(d)] = dhs.get(int(d), 0) + 1
                                    except Exception:
                                        pass
                                self.seq_last_ch1 = s
                            else:
                                self.seq_reset_ch1 += 1
                                self.seq_last_ch1 = s

                        # timestamp continuity for ch1
                        tlast = self.ts_last_ch1
                        if tlast is None:
                            self.ts_last_ch1 = tsu
                        else:
                            dt = (tsu - int(tlast)) & 0xFFFFFFFF
                            if dt == 0:
                                self.ts_dup_ch1 += 1
                            elif 0 < dt < 0x80000000:
                                if 0 < dt <= 10_000_000:
                                    try:
                                        with self._ts_hist_lock:
                                            dh = self._ts_dhist_ch1
                                            dh[dt] = dh.get(dt, 0) + 1
                                    except Exception:
                                        pass
                                self.ts_last_ch1 = tsu
                            else:
                                self.ts_reset_ch1 += 1
                                self.ts_last_ch1 = tsu
                except Exception:
                    pass
                del buf[:frame_total]
                self._working_seen = True
            now=time.time()
            # Если видим только STAT и нет рабочих кадров — один раз пробуем fallback
            if bool(getattr(self, 'local_recovery_enabled', False)) and (not self.passive_connect) and (not self._working_seen) and (not self._fallback_done) and (now - self.connected_t > 1.6):
                self._do_fallback_start()
            self._maybe_report_service_lag(now)
            if now - self.stat_t >= 1.0:
                with self.lock:
                    frames_n = self.frames
                    bytes_n = self.bytes
                    try:
                        dt_stat = float(now - float(self.stat_t))
                        if dt_stat <= 0:
                            dt_stat = 1.0
                    except Exception:
                        dt_stat = 1.0
                    try:
                        fps_hz = float(frames_n) / float(dt_stat)
                        bps_hz = float(bytes_n) / float(dt_stat)
                    except Exception:
                        fps_hz = float(frames_n)
                        bps_hz = float(bytes_n)
                    if frames_n > 0:
                        try:
                            total_ack = int(getattr(self, 'rx_cnt_ch0', 0) or 0) + int(getattr(self, 'rx_cnt_ch1', 0) or 0)
                            self._send_host_rx_ack(total_ack)
                        except Exception:
                            pass
                    try:
                        # compute deltas since last stat tick
                        try:
                            dd0 = int(self.seq_dup_ch0) - int(getattr(self, '_seq_dup_ch0_0', 0))
                            dd1 = int(self.seq_dup_ch1) - int(getattr(self, '_seq_dup_ch1_0', 0))
                            dr0 = int(self.seq_reset_ch0) - int(getattr(self, '_seq_reset_ch0_0', 0))
                            dr1 = int(self.seq_reset_ch1) - int(getattr(self, '_seq_reset_ch1_0', 0))
                            rc0 = int(self.rx_cnt_ch0) - int(getattr(self, '_rx_cnt_ch0_0', 0))
                            rc1 = int(self.rx_cnt_ch1) - int(getattr(self, '_rx_cnt_ch1_0', 0))
                            td0 = int(self.ts_dup_ch0) - int(getattr(self, '_ts_dup_ch0_0', 0))
                            td1 = int(self.ts_dup_ch1) - int(getattr(self, '_ts_dup_ch1_0', 0))
                            tr0 = int(self.ts_reset_ch0) - int(getattr(self, '_ts_reset_ch0_0', 0))
                            tr1 = int(self.ts_reset_ch1) - int(getattr(self, '_ts_reset_ch1_0', 0))
                        except Exception:
                            dd0 = dd1 = dr0 = dr1 = rc0 = rc1 = 0
                            td0 = td1 = tr0 = tr1 = 0

                        # infer timestamp step and estimate timestamp-based gaps within this interval
                        def _mode_step(dh: dict[int, int]) -> int | None:
                            try:
                                if not dh:
                                    return None
                                return max(dh.items(), key=lambda kv: kv[1])[0]
                            except Exception:
                                return None

                        def _est_seq_gaps_from_step(dh: dict[int, int], step: int | None) -> int:
                            """Estimate missing frames based on observed seq deltas.

                            Handles STEP>1 (e.g. per-channel STEP=2 when A/B are interleaved).
                            """
                            try:
                                if not dh or not step or step <= 0:
                                    return 0
                                gaps = 0
                                for d, c in dh.items():
                                    if c <= 0 or d <= 0:
                                        continue
                                    if d <= step:
                                        continue
                                    missing = max(0, (int(d) // int(step)) - 1)
                                    # If delta is not an exact multiple of step, count at least one anomaly.
                                    if (int(d) % int(step)) != 0:
                                        missing = max(missing, 1)
                                    gaps += int(c) * int(missing)
                                return int(gaps)
                            except Exception:
                                return 0

                        def _est_gaps_from_step(dh: dict[int, int], step: int | None) -> int:
                            try:
                                if not dh or not step or step <= 0:
                                    return 0
                                gaps = 0
                                st = float(step)
                                try:
                                    factor = float(getattr(self, 'ts_gap_factor', 3.0))
                                except Exception:
                                    factor = 3.0
                                if not (factor > 1.0):
                                    factor = 2.0
                                for d, c in dh.items():
                                    if c <= 0 or d <= 0:
                                        continue
                                    dt = float(d)
                                    # Stricter rule: ignore small multiples (e.g., 2*step) as jitter/batching.
                                    if dt < (factor * st):
                                        continue
                                    # Estimate missing ticks conservatively using floor (avoid overcounting jitter).
                                    k_floor = int(dt // st)
                                    if k_floor <= 1:
                                        continue
                                    gaps += int(c) * int(max(0, k_floor - 1))
                                return int(gaps)
                            except Exception:
                                return 0

                        try:
                            with self._ts_hist_lock:
                                dh_ts0 = dict(getattr(self, '_ts_dhist_ch0', {}) or {})
                                dh_ts1 = dict(getattr(self, '_ts_dhist_ch1', {}) or {})
                                dh_seq0 = dict(getattr(self, '_seq_dhist_ch0', {}) or {})
                                dh_seq1 = dict(getattr(self, '_seq_dhist_ch1', {}) or {})
                                try:
                                    getattr(self, '_ts_dhist_ch0', {}).clear()
                                    getattr(self, '_ts_dhist_ch1', {}).clear()
                                    getattr(self, '_seq_dhist_ch0', {}).clear()
                                    getattr(self, '_seq_dhist_ch1', {}).clear()
                                except Exception:
                                    pass
                        except Exception:
                            dh_ts0 = {}
                            dh_ts1 = {}
                            dh_seq0 = {}
                            dh_seq1 = {}

                        ts_step0 = _mode_step(dh_ts0)
                        ts_step1 = _mode_step(dh_ts1)
                        ts_gap0 = _est_gaps_from_step(dh_ts0, ts_step0)
                        ts_gap1 = _est_gaps_from_step(dh_ts1, ts_step1)

                        # infer seq step and estimate seq-based gaps within this interval
                        seq_step0 = _mode_step(dh_seq0)
                        seq_step1 = _mode_step(dh_seq1)
                        dg0 = _est_seq_gaps_from_step(dh_seq0, seq_step0)
                        dg1 = _est_seq_gaps_from_step(dh_seq1, seq_step1)

                        # Maintain cumulative seq gap counters for compatibility with older consumers.
                        try:
                            self.seq_gap_ch0 += int(dg0)
                            self.seq_gap_ch1 += int(dg1)
                        except Exception:
                            pass

                        # compute per-channel loss percentage for this interval
                        try:
                            loss0 = (100.0 * float(dg0) / float(dg0 + rc0)) if (dg0 + rc0) > 0 else 0.0
                            loss1 = (100.0 * float(dg1) / float(dg1 + rc1)) if (dg1 + rc1) > 0 else 0.0
                        except Exception:
                            loss0 = loss1 = 0.0

                        try:
                            tsloss0 = (100.0 * float(ts_gap0) / float(ts_gap0 + rc0)) if (ts_gap0 + rc0) > 0 else 0.0
                            tsloss1 = (100.0 * float(ts_gap1) / float(ts_gap1 + rc1)) if (ts_gap1 + rc1) > 0 else 0.0
                        except Exception:
                            tsloss0 = tsloss1 = 0.0

                        # cumulative post-warmup
                        try:
                            warm = (now - float(getattr(self, '_stats_epoch', now))) < float(getattr(self, 'warmup_sec', 5.0))
                        except Exception:
                            warm = False
                        if not warm:
                            try:
                                self._ts_gap_tot_ch0 += int(ts_gap0)
                                self._ts_gap_tot_ch1 += int(ts_gap1)
                                self._rx_tot_ch0 += int(rc0)
                                self._rx_tot_ch1 += int(rc1)
                            except Exception:
                                pass
                        try:
                            tot0 = int(getattr(self, '_rx_tot_ch0', 0)) + int(getattr(self, '_ts_gap_tot_ch0', 0))
                            tot1 = int(getattr(self, '_rx_tot_ch1', 0)) + int(getattr(self, '_ts_gap_tot_ch1', 0))
                            tsloss0_tot = (100.0 * float(getattr(self, '_ts_gap_tot_ch0', 0)) / float(tot0)) if tot0 > 0 else 0.0
                            tsloss1_tot = (100.0 * float(getattr(self, '_ts_gap_tot_ch1', 0)) / float(tot1)) if tot1 > 0 else 0.0
                        except Exception:
                            tsloss0_tot = tsloss1_tot = 0.0

                        try:
                            rx0_hz = float(rc0) / float(dt_stat) if dt_stat > 0 else float(rc0)
                            rx1_hz = float(rc1) / float(dt_stat) if dt_stat > 0 else float(rc1)
                        except Exception:
                            rx0_hz = float(rc0)
                            rx1_hz = float(rc1)

                        if getattr(self.asm, 'independent', False):
                            qszA = self.asm.qA.qsize() if hasattr(self.asm, 'qA') else 0
                            qszB = self.asm.qB.qsize() if hasattr(self.asm, 'qB') else 0
                            # print(
                            #     f"dt={dt_stat:.3f}s frames={frames_n} fps={fps_hz:.1f}/s bytes={int(bps_hz)}B/s "
                            #     f"rxA={rx0_hz:.1f}/s rxB={rx1_hz:.1f}/s lossA={loss0:.1f}% lossB={loss1:.1f}% "
                            #     f"tsStepA={ts_step0} tsStepB={ts_step1} tsLossA={tsloss0:.1f}% tsLossB={tsloss1:.1f}% "
                            #     f"tsLossTotA={tsloss0_tot:.1f}% tsLossTotB={tsloss1_tot:.1f}% "
                            #     f"rxGapA+={dg0} rxGapB+={dg1} "
                            #     f"rxDupA+={dd0} rxDupB+={dd1} "
                            #     f"rxRstA+={dr0} rxRstB+={dr1} "
                            #     f"tsDupA+={td0} tsDupB+={td1} tsRstA+={tr0} tsRstB+={tr1} "
                            #     f"crc_bad={self.crc_bad} magic_bad={self.magic_bad} "
                            #     f"qA={qszA} qB={qszB} dropA={getattr(self.asm,'drop_a',0)} dropB={getattr(self.asm,'drop_b',0)}",
                            #     flush=True,
                            # )
                        else:
                            pass
                            # print(
                            #     f"dt={dt_stat:.3f}s frames={frames_n} fps={fps_hz:.1f}/s bytes={int(bps_hz)}B/s "
                            #     f"rxA={rx0_hz:.1f}/s rxB={rx1_hz:.1f}/s lossA={loss0:.1f}% lossB={loss1:.1f}% "
                            #     f"tsStepA={ts_step0} tsStepB={ts_step1} tsLossA={tsloss0:.1f}% tsLossB={tsloss1:.1f}% "
                            #     f"tsLossTotA={tsloss0_tot:.1f}% tsLossTotB={tsloss1_tot:.1f}% "
                            #     f"rxGapA+={dg0} rxGapB+={dg1} "
                            #     f"rxDupA+={dd0} rxDupB+={dd1} "
                            #     f"rxRstA+={dr0} rxRstB+={dr1} "
                            #     f"tsDupA+={td0} tsDupB+={td1} tsRstA+={tr0} tsRstB+={tr1} "
                            #     f"crc_bad={self.crc_bad} magic_bad={self.magic_bad} "
                            #     f"stereo_ready={self.asm.q.qsize()} dropPairs={getattr(self.asm,'drop_pairs',0)}",
                            #     flush=True,
                            # )
                    except Exception:
                        pass
                        # print(f"dt={dt_stat:.3f}s frames={frames_n} fps={fps_hz:.1f}/s bytes={int(bps_hz)}B/s crc_bad={self.crc_bad} magic_bad={self.magic_bad}")

                    # store snapshots for next tick
                    try:
                        self._seq_gap_ch0_0 = int(self.seq_gap_ch0)
                        self._seq_gap_ch1_0 = int(self.seq_gap_ch1)
                        self._seq_dup_ch0_0 = int(self.seq_dup_ch0)
                        self._seq_dup_ch1_0 = int(self.seq_dup_ch1)
                        self._seq_reset_ch0_0 = int(self.seq_reset_ch0)
                        self._seq_reset_ch1_0 = int(self.seq_reset_ch1)
                        self._rx_cnt_ch0_0 = int(self.rx_cnt_ch0)
                        self._rx_cnt_ch1_0 = int(self.rx_cnt_ch1)
                        self._ts_dup_ch0_0 = int(self.ts_dup_ch0)
                        self._ts_dup_ch1_0 = int(self.ts_dup_ch1)
                        self._ts_reset_ch0_0 = int(self.ts_reset_ch0)
                        self._ts_reset_ch1_0 = int(self.ts_reset_ch1)
                    except Exception:
                        pass
                    self.frames = 0; self.bytes = 0; self.stat_t = now

    def _live_drop_old_frames_enabled(self) -> bool:
        try:
            return str(os.getenv("BMI30_LIVE_DROP_OLD_FRAMES", "1")).strip().lower() not in ("0", "false", "no", "off")
        except Exception:
            return True

    def _live_queue_target(self) -> int:
        try:
            return max(1, int(os.getenv("BMI30_LIVE_QUEUE_TARGET", "4")))
        except Exception:
            return 4

    def _live_queue_drain_max(self) -> int:
        try:
            return max(1, int(os.getenv("BMI30_LIVE_QUEUE_DRAIN_MAX", "512")))
        except Exception:
            return 512

    def _trim_live_queue(self, q, drop_attr: str | None = None) -> int:
        if q is None or not self._live_drop_old_frames_enabled():
            return 0
        try:
            extra = int(q.qsize()) - int(self._live_queue_target())
        except Exception:
            return 0
        if extra <= 0:
            return 0
        dropped = 0
        limit = min(extra, self._live_queue_drain_max())
        for _ in range(limit):
            try:
                q.get_nowait()
                dropped += 1
            except queue.Empty:
                break
            except Exception:
                break
        if dropped and drop_attr:
            try:
                asm = getattr(self, "asm", None)
                setattr(asm, drop_attr, int(getattr(asm, drop_attr, 0) or 0) + dropped)
            except Exception:
                pass
        return dropped

    def get_stereo(self, timeout=0.0):
        try:
            if getattr(self.asm, 'independent', False):
                # Independent mode: return a single frame ('A', frame) or ('B', frame).
                # IMPORTANT: do not starve channel B by always reading qA first.
                start = time.monotonic()
                try:
                    rr = int(getattr(self, '_indep_rr', 0))
                except Exception:
                    rr = 0
                # Toggle preference for next call.
                try:
                    self._indep_rr = 1 - rr
                except Exception:
                    pass

                def _remaining() -> float:
                    if timeout is None:
                        return 0.0
                    try:
                        t = float(timeout)
                    except Exception:
                        t = 0.0
                    if t <= 0:
                        return 0.0
                    rem = t - (time.monotonic() - start)
                    return rem if rem > 0 else 0.0

                first = ('A', getattr(self.asm, 'qA', None)) if rr == 0 else ('B', getattr(self.asm, 'qB', None))
                second = ('B', getattr(self.asm, 'qB', None)) if rr == 0 else ('A', getattr(self.asm, 'qA', None))
                try:
                    self._trim_live_queue(getattr(self.asm, 'qA', None), 'drop_a')
                    self._trim_live_queue(getattr(self.asm, 'qB', None), 'drop_b')
                except Exception:
                    pass
                # Fast path: check both queues without blocking first.
                # This avoids spending the whole timeout on an empty preferred queue
                # while data is already waiting in the other queue.
                for tag, q in (first, second):
                    if q is None:
                        continue
                    try:
                        item = q.get_nowait()
                        return (tag, item)
                    except queue.Empty:
                        continue

                # Slow path: wait in short slices and alternate both queues.
                # This keeps latency low and prevents artificial FPS cap (~1/timeout).
                rem = _remaining()
                if rem <= 0.0:
                    return None
                while rem > 0.0:
                    wait_slice = min(0.01, rem)
                    for tag, q in (first, second):
                        if q is None:
                            continue
                        try:
                            item = q.get(timeout=wait_slice)
                            return (tag, item)
                        except queue.Empty:
                            continue
                    rem = _remaining()
                return None
            else:
                try:
                    self._trim_live_queue(getattr(self.asm, 'q', None), 'drop_pairs')
                except Exception:
                    pass
                return self.asm.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_frame(self, adc_id:int, timeout=0.0):
        """Return a single frame for adc_id (0 or 1) or None.

        adc_id can be 0 (A) or 1 (B).
        """
        try:
            if not getattr(self.asm, 'independent', False):
                return None
            if int(adc_id) == 0:
                try:
                    self._trim_live_queue(getattr(self.asm, 'qA', None), 'drop_a')
                except Exception:
                    pass
                return self.asm.qA.get(timeout=timeout)
            else:
                try:
                    self._trim_live_queue(getattr(self.asm, 'qB', None), 'drop_b')
                except Exception:
                    pass
                return self.asm.qB.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_buffer_depths(self) -> dict:
        """Return current queue/buffer depths for diagnostics.

        Returns dict with keys depending on assembler mode:
         - 'stereo_q' : number of paired A/B frames waiting
         - 'qA', 'qB' : per-channel queue sizes when independent
         - 'bufA', 'bufB' : number of unpaired frames buffered by assembler
        """
        try:
            depths = {}
            asm = getattr(self, 'asm', None)
            if asm is None:
                return {'stereo_q': 0}
            # paired queue
            try:
                depths['stereo_q'] = asm.q.qsize() if hasattr(asm, 'q') else 0
            except Exception:
                depths['stereo_q'] = 0
            # independent queues
            try:
                if getattr(asm, 'independent', False):
                    depths['qA'] = asm.qA.qsize() if hasattr(asm, 'qA') else 0
                    depths['qB'] = asm.qB.qsize() if hasattr(asm, 'qB') else 0
            except Exception:
                depths['qA'] = depths.get('qA', 0); depths['qB'] = depths.get('qB', 0)
            # internal assembler buffers awaiting pairing
            try:
                depths['bufA'] = len(getattr(asm, 'bufA', {}))
                depths['bufB'] = len(getattr(asm, 'bufB', {}))
            except Exception:
                depths['bufA'] = depths.get('bufA', 0); depths['bufB'] = depths.get('bufB', 0)
            return depths
        except Exception:
            return {'stereo_q': 0}

    # --- helpers for GUI ---
    def get_port_path_info(self):
        """Вернуть информацию о топологии USB для uhubctl/sysfs.

        Возвращает dict: {
          'bus': int|None,
          'address': int|None,
          'port_numbers': [ints] | None,
          'port_path': '1-1.3.2' | None,
          'hub_loc': '1-1.3' | None,
          'hub_port': 2 | None,
          'vid': int,
          'pid': int,
        }
        """
        info = {
            'bus': None,
            'address': None,
            'port_numbers': None,
            'port_path': None,
            'hub_loc': None,
            'hub_port': None,
            'vid': getattr(self.dev, 'idVendor', None),
            'pid': getattr(self.dev, 'idProduct', None),
        }
        try:
            bus = getattr(self.dev, 'bus', None)
            addr = getattr(self.dev, 'address', None)
            ports = None
            # PyUSB may expose 'port_numbers' (list) or only 'port_number'
            try:
                ports = list(getattr(self.dev, 'port_numbers'))
            except Exception:
                p1 = getattr(self.dev, 'port_number', None)
                if p1:
                    ports = [int(p1)]
            info['bus'] = bus
            info['address'] = addr
            info['port_numbers'] = ports
            if bus and ports:
                port_path = f"{bus}-" + ".".join(str(x) for x in ports)
                info['port_path'] = port_path
                if len(ports) >= 1:
                    hub_loc = f"{bus}-" + ".".join(str(x) for x in ports[:-1]) if len(ports) > 1 else f"{bus}-"
                    hub_port = int(ports[-1])
                    # Корректируем hub_loc: для верхнего уровня оставим вида '1-1'
                    if hub_loc.endswith('-') and ports:
                        hub_loc = f"{bus}-{ports[0]}"
                    info['hub_loc'] = hub_loc
                    info['hub_port'] = hub_port
        except Exception:
            pass
        return info

def watch_loop(interval=1.0):
    last_state = None
    us = None
    last_msg = 0
    while True:
        try:
            if us is None:
                dev_present = usb.core.find(idVendor=VID, idProduct=PID) is not None
                if dev_present:
                    try:
                        us = USBStream(profile=1, full=True, vid=VID, pid=PID, interactive=False)
                        print('[state] STREAMING start')
                    except Exception as e:
                        us = None
                        if time.time() - last_msg > 2:
                            print(f"[wait] found device but open failed: {e}")
                            last_msg = time.time()
                else:
                    if time.time() - last_msg > 2:
                        print(f"[wait] нет устройства {hex(VID)}:{hex(PID)}")
                        last_msg = time.time()
                    time.sleep(interval)
            else:
                if us.disconnected:
                    print('[state] LOST device, returning to wait')
                    try:
                        us.close()
                    except Exception:
                        pass
                    us = None
                    time.sleep(interval)
                else:
                    # poll for stereo pairs lightly (discard output here)
                    _ = us.get_stereo(timeout=0.01)
                    time.sleep(0.1)
        except KeyboardInterrupt:
            print('\n[exit]')
            if us:
                try:
                    us.close()
                except Exception:
                    pass
            break

if __name__=='__main__':
    if '--watch' in sys.argv:
        watch_loop()
    else:
        # Demo: by default print compact stats once per second.
        # Use --print to also print individual frames (can be very noisy).
        print_frames = '--print' in sys.argv

        # Optional: switch streaming mode to match GUI behaviors.
        # --lossless-roi  : STOP → SET_WINDOWS(roi_start,roi_len,0,0) → SET_STREAM_MODE(1) → SET_ASYNC(0) → START
        # --latest        : STOP → SET_WINDOWS(0,0,0,0)     → SET_STREAM_MODE(0) → SET_ASYNC(1) → START
        want_lossless = ('--lossless-roi' in sys.argv) or ('--roi200' in sys.argv)
        want_latest = ('--latest' in sys.argv)
        force_async1 = ('--async1' in sys.argv)
        force_async0 = ('--async0' in sys.argv)

        # LOSSLESS_ROI window parameters
        roi_start = 280
        roi_len = 200
        # Demo connection parameters
        demo_profile = 1
        demo_frame_samples: int | None = None
        for a in list(sys.argv):
            if a.startswith('--profile='):
                try:
                    demo_profile = int(a.split('=', 1)[1])
                except Exception:
                    pass
            elif a.startswith('--frame-samples='):
                try:
                    demo_frame_samples = int(a.split('=', 1)[1])
                except Exception:
                    demo_frame_samples = None
            if a.startswith('--roi-len='):
                try:
                    roi_len = int(a.split('=', 1)[1])
                except Exception:
                    pass
            elif a.startswith('--roi-start='):
                try:
                    roi_start = int(a.split('=', 1)[1])
                except Exception:
                    pass
        try:
            roi_start = max(0, min(65535, int(roi_start)))
        except Exception:
            roi_start = 280
        try:
            roi_len = max(1, min(65535, int(roi_len)))
        except Exception:
            roi_len = 200

        # Optional: warm-up seconds for stats (ignore early unstable period after switching modes)
        warmup_sec = None
        for i, a in enumerate(list(sys.argv)):
            if a.startswith('--warmup='):
                try:
                    warmup_sec = float(a.split('=', 1)[1])
                except Exception:
                    warmup_sec = None
        if warmup_sec is not None:
            try:
                os.environ['BMI30_WARMUP_SEC'] = str(warmup_sec)
            except Exception:
                pass

        us = USBStream(profile=demo_profile, full=True, frame_samples=demo_frame_samples)
        try:
            if want_lossless or want_latest:
                import struct as _st
                try:
                    us.send_cmd(CMD_STOP_STREAM, b"")
                    time.sleep(0.05)
                except Exception:
                    pass

                if want_lossless:
                    us.send_cmd(CMD_SET_WINDOWS, _st.pack('<HHHH', int(roi_start) & 0xFFFF, int(roi_len) & 0xFFFF, 0, 0))
                    time.sleep(0.02)
                    us.send_cmd(CMD_SET_STREAM_MODE, b"\x01")
                    time.sleep(0.02)
                    # LOSSLESS_ROI default is strict pairs (0), but user may want independent channels (1)
                    async_mode = 0
                    if force_async1:
                        async_mode = 1
                    if force_async0:
                        async_mode = 0
                    us.send_cmd(CMD_ASYNC, bytes([async_mode]))
                    time.sleep(0.02)
                    us.start_stream()
                    time.sleep(0.05)
                    print(f'[demo] LOSSLESS_ROI enabled: windows({roi_start},{roi_len},0,0) stream_mode=1 async={async_mode}', flush=True)
                else:
                    us.send_cmd(CMD_SET_WINDOWS, _st.pack('<HHHH', 0, 0, 0, 0))
                    time.sleep(0.02)
                    us.send_cmd(CMD_SET_STREAM_MODE, b"\x00")
                    time.sleep(0.02)
                    # LATEST default is independent (1), but allow forcing strict (0)
                    async_mode = 1
                    if force_async0:
                        async_mode = 0
                    if force_async1:
                        async_mode = 1
                    us.send_cmd(CMD_ASYNC, bytes([async_mode]))
                    time.sleep(0.02)
                    us.start_stream()
                    time.sleep(0.05)
                    print(f'[demo] LATEST enabled: windows(0,0,0,0) stream_mode=0 async={async_mode}', flush=True)
        except Exception as e:
            try:
                print('[demo] mode switch failed:', e, flush=True)
            except Exception:
                pass

        last_t = time.time()
        cnt_a = 0
        cnt_b = 0
        last_seq_a = None
        last_seq_b = None
        last_seq_p = None
        step_a = None
        step_b = None
        step_p = None
        # Track delta distribution to infer step robustly (often STEP=2 when A/B are interleaved)
        delta_hist_a: dict[int, int] = {}
        delta_hist_b: dict[int, int] = {}
        delta_hist_p: dict[int, int] = {}
        crc0 = 0
        magic0 = 0
        dropa0 = 0
        dropb0 = 0
        dropp0 = 0
        samp_hist_a: dict[int, int] = {}
        samp_hist_b: dict[int, int] = {}
        pm_cnt = 0

        def _infer_step(delta_hist: dict[int, int]) -> int | None:
            try:
                if not delta_hist:
                    return None
                return max(delta_hist.items(), key=lambda kv: kv[1])[0]
            except Exception:
                return None

        def _estimate_gaps(delta_hist: dict[int, int], step: int | None) -> int:
            """Estimate missing frames in the interval based on observed seq deltas.

            This is computed at print-time to avoid artifacts when step inference changes.
            """
            try:
                if not delta_hist or not step or step <= 0:
                    return 0
                gaps = 0
                for d, c in delta_hist.items():
                    if d <= 0 or c <= 0:
                        continue
                    if d <= step:
                        continue
                    missing = max(0, (d // step) - 1)
                    # If delta is not an exact multiple of step, count at least one anomaly.
                    if (d % step) != 0:
                        missing = max(missing, 1)
                    gaps += int(c) * int(missing)
                return int(gaps)
            except Exception:
                return 0
        try:
            while True:
                got_any = False
                if getattr(us.asm, 'independent', False):
                    # Drain both channels so we don't artificially overflow qB.
                    a = us.get_frame(0, timeout=0.001)
                    if a is not None:
                        got_any = True
                        cnt_a += 1
                        try:
                            sa = int(getattr(a, 'samples', 0) or 0)
                            if sa > 0:
                                samp_hist_a[sa] = samp_hist_a.get(sa, 0) + 1
                        except Exception:
                            pass
                        if print_frames:
                            print(f"A seq={a.seq} samples={a.samples}")
                        if last_seq_a is not None:
                            d = int(a.seq) - int(last_seq_a)
                            if 0 < d <= 16:
                                delta_hist_a[d] = delta_hist_a.get(d, 0) + 1
                        last_seq_a = a.seq

                    b = us.get_frame(1, timeout=0.001)
                    if b is not None:
                        got_any = True
                        cnt_b += 1
                        try:
                            sb = int(getattr(b, 'samples', 0) or 0)
                            if sb > 0:
                                samp_hist_b[sb] = samp_hist_b.get(sb, 0) + 1
                        except Exception:
                            pass
                        if print_frames:
                            print(f"B seq={b.seq} samples={b.samples}")
                        if last_seq_b is not None:
                            d = int(b.seq) - int(last_seq_b)
                            if 0 < d <= 16:
                                delta_hist_b[d] = delta_hist_b.get(d, 0) + 1
                        last_seq_b = b.seq
                else:
                    pair = us.get_stereo(timeout=0.01)
                    if pair:
                        got_any = True
                        (a, b) = pair
                        cnt_a += 1
                        cnt_b += 1
                        try:
                            if int(getattr(a, 'seq', 0) or 0) != int(getattr(b, 'seq', 0) or 0):
                                pm_cnt += 1
                        except Exception:
                            pass
                        try:
                            sa = int(getattr(a, 'samples', 0) or 0)
                            sb = int(getattr(b, 'samples', 0) or 0)
                            if sa > 0:
                                samp_hist_a[sa] = samp_hist_a.get(sa, 0) + 1
                            if sb > 0:
                                samp_hist_b[sb] = samp_hist_b.get(sb, 0) + 1
                        except Exception:
                            pass
                        try:
                            # Use A's seq as pair seq reference
                            if last_seq_p is not None:
                                d = int(a.seq) - int(last_seq_p)
                                if 0 < d <= 16:
                                    delta_hist_p[d] = delta_hist_p.get(d, 0) + 1
                            last_seq_p = a.seq
                        except Exception:
                            pass
                        if print_frames:
                            print(f"stereo seq={a.seq} samplesA={a.samples} samplesB={b.samples}")

                if not got_any:
                    time.sleep(0.001)

                now = time.time()
                if now - last_t >= 1.0:
                    # Update inferred step as the most common observed delta (within last interval)
                    step_a = _infer_step(delta_hist_a)
                    step_b = _infer_step(delta_hist_b)
                    step_p = _infer_step(delta_hist_p)
                    gap_a = _estimate_gaps(delta_hist_a, step_a)
                    gap_b = _estimate_gaps(delta_hist_b, step_b)
                    gap_p = _estimate_gaps(delta_hist_p, step_p)
                    crc1 = getattr(us, 'crc_bad', 0)
                    magic1 = getattr(us, 'magic_bad', 0)
                    dropa1 = getattr(us.asm, 'drop_a', 0)
                    dropb1 = getattr(us.asm, 'drop_b', 0)
                    dropp1 = getattr(us.asm, 'drop_pairs', 0)
                    qszA = us.asm.qA.qsize() if getattr(us.asm, 'independent', False) and hasattr(us.asm, 'qA') else 0
                    qszB = us.asm.qB.qsize() if getattr(us.asm, 'independent', False) and hasattr(us.asm, 'qB') else 0
                    qszP = us.asm.q.qsize() if (not getattr(us.asm, 'independent', False)) and hasattr(us.asm, 'q') else 0

                    if getattr(us.asm, 'independent', False):
                        # Most-common sample count seen in the last second
                        try:
                            sa_mode = max(samp_hist_a.items(), key=lambda kv: kv[1])[0] if samp_hist_a else None
                            sb_mode = max(samp_hist_b.items(), key=lambda kv: kv[1])[0] if samp_hist_b else None
                        except Exception:
                            sa_mode = sb_mode = None
                        print(
                            f"A={cnt_a}/s B={cnt_b}/s stepA={step_a} stepB={step_b} "
                            f"gapA={gap_a} gapB={gap_b} "
                            f"samplesA~={sa_mode} samplesB~={sb_mode} "
                            f"crc+={crc1-crc0} magic+={magic1-magic0} "
                            f"qA={qszA} qB={qszB} dropA+={dropa1-dropa0} dropB+={dropb1-dropb0}",
                            flush=True,
                        )
                    else:
                        try:
                            sa_mode = max(samp_hist_a.items(), key=lambda kv: kv[1])[0] if samp_hist_a else None
                            sb_mode = max(samp_hist_b.items(), key=lambda kv: kv[1])[0] if samp_hist_b else None
                        except Exception:
                            sa_mode = sb_mode = None
                        print(
                            f"stereo={cnt_a}/s stepP={step_p} gapP={gap_p} pm={pm_cnt} "
                            f"crc+={crc1-crc0} magic+={magic1-magic0} "
                            f"samplesA~={sa_mode} samplesB~={sb_mode} "
                            f"qPairs={qszP} dropPairs+={dropp1-dropp0}",
                            flush=True,
                        )

                    cnt_a = 0
                    cnt_b = 0
                    delta_hist_a.clear()
                    delta_hist_b.clear()
                    delta_hist_p.clear()
                    samp_hist_a.clear()
                    samp_hist_b.clear()
                    pm_cnt = 0
                    crc0 = crc1
                    magic0 = magic1
                    dropa0 = dropa1
                    dropb0 = dropb1
                    dropp0 = dropp1
                    last_t = now
        except KeyboardInterrupt:
            pass
        finally:
            us.close()

