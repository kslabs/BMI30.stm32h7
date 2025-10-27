#!/usr/bin/env python3
import usb.core, usb.util, time, argparse, struct, sys

VID=0xCAFE; PID=0x4001; IF_NUM=2; EP_OUT=0x03; EP_IN=0x83
CMD_START=0x20; CMD_STOP=0x21; CMD_GET_STATUS=0x30

HDR_SIZE=32


def find_dev():
    d=usb.core.find(idVendor=VID,idProduct=PID)
    if d is None: raise SystemExit('device not found')
    try: d.set_configuration()
    except Exception: pass
    try: usb.util.claim_interface(d, IF_NUM)
    except Exception: pass
    return d


def classify_frame(frame: bytes):
    """Классификация ПОЛНОГО логического кадра (агрегированного)."""
    if frame.startswith(b'STAT'): return 'STAT'
    if len(frame) >= HDR_SIZE and frame[0]==0x5A and frame[1]==0xA5:
        flags = frame[3]
        seq = struct.unpack_from('<I', frame, 4)[0]
        ns  = struct.unpack_from('<H', frame, 12)[0]
        if flags == 0x80: return f'TEST seq={seq} ns={ns}'
        if flags == 0x01: return f'ADC0 seq={seq} ns={ns}'
        if flags == 0x02: return f'ADC1 seq={seq} ns={ns}'
        return f'FRAME? f=0x{flags:02X} seq={seq} ns={ns}'
    return 'UNKNOWN'

def frame_target_length(hdr: bytes):
    """Определить полную длину логического кадра из 32-байтного заголовка."""
    if len(hdr) < HDR_SIZE: return None
    if not (hdr[0]==0x5A and hdr[1]==0xA5): return None
    total_samples = struct.unpack_from('<H', hdr, 12)[0]
    return HDR_SIZE + total_samples*2


def pretty_status(buf: bytes):
    if not buf.startswith(b'STAT'):
        return 'not STAT len='+str(len(buf))
    # простая распаковка первых 48 байт для диагностики
    def rd32(off): return struct.unpack_from('<I', buf, off)[0]
    def rd16(off): return struct.unpack_from('<H', buf, off)[0]
    # Предполагаем layout: magic 'STAT'(4) + ver/u32, produced_seq,u32, sent0,u32, sent1,u32, dbg_tx_cplt,u32, dma_done0,u32, dma_done1,u32, flags2,u32, ...
    try:
        ver = rd32(4)
        produced_seq = rd32(8)
        sent0 = rd32(12)
        sent1 = rd32(16)
        dbg_tx_cplt = rd32(20)
        dma0 = rd32(24)
        dma1 = rd32(28)
        flags2 = rd32(32)
        cur_samples = rd16(40)
        return f'ver={ver} prod={produced_seq} sent0={sent0} sent1={sent1} txcplt={dbg_tx_cplt} dma0={dma0} dma1={dma1} flags2=0x{flags2:08X} curS={cur_samples}'
    except Exception as e:
        return f'parse_err:{e} len={len(buf)}'


def get_status(dev):
    try:
        dev.write(EP_OUT, bytes([CMD_GET_STATUS]))
        # Ожидаем что устройство ответит STAT отдельным IN пакетом (в stream path или control)
        # Пробуем несколько коротких чтений
        for _ in range(3):
            try:
                data = bytes(dev.read(EP_IN, 512, 50))
            except usb.core.USBError as e:
                if getattr(e,'errno',None) in (110,10060):
                    continue
                raise
            if data.startswith(b'STAT'):
                print('[STATUS]', pretty_status(data))
                return True
        print('[STATUS] timeout (no STAT)')
        return False
    except Exception as e:
        print('[STATUS] error', e)
        return False


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--start',action='store_true')
    ap.add_argument('--count',type=int,default=30, help='(устар) количество USB пакетов для старого режима')
    ap.add_argument('--frames',type=int,default=10, help='Количество логических кадров для аггрегированного режима')
    ap.add_argument('--no-agg',action='store_true',help='Отключить агрегацию (старый режим по пакетам)')
    ap.add_argument('--timeout',type=int,default=120)
    ap.add_argument('--timeout-status',type=int,default=5, help='Каждые N подряд таймаутов запрашивать STAT (0=off)')
    ap.add_argument('--fallback-packets',type=int,default=12, help='После N подряд таймаутов перейти в пакетный режим (0=off)')
    ap.add_argument('--no-status-on-timeout',action='store_true',help='Не делать GET_STATUS при таймаутах')
    ap.add_argument('--gap',type=float,default=0.0)
    args=ap.parse_args()
    dev=find_dev()
    if args.start:
        dev.write(EP_OUT, bytes([CMD_START]))
        print('START sent')
    if args.no_agg:
        # Старый пакетный режим
        got=0
        while got < args.count:
            try:
                data=bytes(dev.read(EP_IN,512,args.timeout))
            except usb.core.USBError as e:
                if getattr(e,'errno',None) in (110,10060):
                    print('timeout (no packet)')
                    continue
                raise
            got+=1
            tag=classify_frame(data)
            print(f'[PKT {got}] len={len(data)} tag={tag} head32={data[:32].hex()}')
            if args.gap: time.sleep(args.gap)
        return

    # Новый режим агрегации логических кадров
    frames = 0
    acc = bytearray()
    target = None
    segs = 0
    consecutive_timeouts = 0
    fallback_mode = False
    while frames < args.frames:
        if fallback_mode:
            # Пакетный режим до конца
            try:
                data=bytes(dev.read(EP_IN,512,args.timeout))
            except usb.core.USBError as e:
                if getattr(e,'errno',None) in (110,10060):
                    print('timeout (fallback)')
                    consecutive_timeouts += 1
                    continue
                raise
            tag=classify_frame(data)
            print(f'[FALLBACK PKT] len={len(data)} tag={tag} head32={data[:32].hex()}')
            if args.gap: time.sleep(args.gap)
            continue
        try:
            data = bytes(dev.read(EP_IN,512,args.timeout))
        except usb.core.USBError as e:
            if getattr(e,'errno',None) in (110,10060):
                consecutive_timeouts += 1
                print(f'timeout (no packet) x{consecutive_timeouts}')
                if not args.no_status_on_timeout and args.timeout_status>0 and (consecutive_timeouts % args.timeout_status)==0:
                    get_status(dev)
                if args.fallback_packets>0 and consecutive_timeouts >= args.fallback_packets:
                    print('[FALLBACK] switching to packet mode due to repeated timeouts')
                    fallback_mode = True
                continue
            raise
        consecutive_timeouts = 0
        segs += 1
        # Если начинаем новый кадр
        if not acc:
            # Определяем тип: STAT фиксирован 64? (у нас 64) — ловим сразу
            if data.startswith(b'STAT') and len(data) == 64:
                tag = 'STAT'
                print(f'[FRAME {frames+1}] len=64 tag=STAT head32={data[:32].hex()} (single)')
                frames += 1
                continue
            # Иначе ожидаем начало логического кадра
            if len(data) >= HDR_SIZE and data[0]==0x5A and data[1]==0xA5:
                target = frame_target_length(data)
                if target is None or target < HDR_SIZE:
                    print(f'[DESYNC] invalid header target frame, dropping packet head32={data[:32].hex()}')
                    acc.clear(); target=None; segs=0
                    continue
                acc.extend(data)
            else:
                print(f'[ORPHAN] pkt len={len(data)} head32={data[:32].hex()} (ignoring until magic)')
                continue
        else:
            # Продолжаем собирать
            acc.extend(data)
        # Проверяем переполнение
        if target and len(acc) > target:
            print(f'[OVERFLOW] collected {len(acc)}/{target}, dropping (head32={acc[:32].hex()})')
            acc.clear(); target=None; segs=0
            continue
        # Закончили кадр?
        if target and len(acc) == target:
            frame = bytes(acc)
            tag = classify_frame(frame)
            head32 = frame[:32].hex()
            print(f'[FRAME {frames+1}] len={len(frame)} tag={tag} segs={segs} head32={head32}')
            # Подготовка к следующему кадру
            frames += 1
            acc.clear(); target=None; segs=0
            if args.gap: time.sleep(args.gap)

if __name__=='__main__':
    main()
