import usb.core, usb.util, time, struct

# Find device by VID/PID from usbd_desc.c
VID = 0xCAFE
PID = 0x4001

# Endpoints
EP_OUT = 0x03
EP_IN  = 0x83

CMD_START = 0x20
CMD_STOP  = 0x21
CMD_GET_STATUS = 0x30

def find_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit("Device not found")
    try:
        dev.set_configuration()
    except usb.core.USBError:
        # Might already be configured
        pass
    
    # Set alternate setting 1 for Vendor interface (IF#2) to activate endpoints
    try:
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        print("[OK] Set IF#2 altsetting 1 (Vendor endpoints active)")
    except usb.core.USBError as e:
        print(f"[WARN] Could not set altsetting: {e}")
    
    return dev

def parse_stat(buf: bytes):
    if len(buf) < 64 or buf[:4] != b'STAT':
        return None
    ver = buf[4]
    cur_samples = int.from_bytes(buf[6:8], 'little')
    frame_bytes = int.from_bytes(buf[8:10], 'little')
    test_frames = int.from_bytes(buf[10:12], 'little')
    produced_seq = int.from_bytes(buf[12:16], 'little')
    sent0 = int.from_bytes(buf[16:20], 'little')
    sent1 = int.from_bytes(buf[20:24], 'little')
    dbg_tx_cplt = int.from_bytes(buf[24:28], 'little')
    dbg_partial = int.from_bytes(buf[28:32], 'little')
    dbg_size_mismatch = int.from_bytes(buf[32:36], 'little')
    dma0 = int.from_bytes(buf[36:40], 'little')
    dma1 = int.from_bytes(buf[40:44], 'little')
    wr = int.from_bytes(buf[44:48], 'little')
    flags_runtime = int.from_bytes(buf[48:50], 'little')
    flags2 = int.from_bytes(buf[50:52], 'little')
    sending_ch = buf[52]
    pair_idx = int.from_bytes(buf[54:56], 'little')
    last_tx_len = int.from_bytes(buf[56:58], 'little')
    cur_stream_seq = int.from_bytes(buf[58:62], 'little')
    return {
        'ver': ver,
        'cur_samples': cur_samples,
        'frame_bytes': frame_bytes,
        'test_frames': test_frames,
        'produced_seq': produced_seq,
        'sent0': sent0, 'sent1': sent1,
        'dma0': dma0, 'dma1': dma1, 'wr': wr,
        'flags_rt': flags_runtime,
        'flags2': flags2,
        'sending_ch': sending_ch,
        'pair_fill': (pair_idx >> 8) & 0xFF,
        'pair_send': pair_idx & 0xFF,
        'last_tx_len': last_tx_len,
        'cur_stream_seq': cur_stream_seq,
    }


def main():
    dev = find_dev()
    # START
    dev.write(EP_OUT, bytes([CMD_START]))
    t0 = time.time()
    got_test = False
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            data = dev.read(EP_IN, 512, timeout=500)
        except usb.core.USBError as e:
            if getattr(e, 'errno', None) == 110:
                # Try GET_STATUS during stream
                try:
                    dev.write(EP_OUT, bytes([CMD_GET_STATUS]))
                except Exception:
                    pass
                continue
            raise
        head = bytes(data[:4])
        if head == b'STAT':
            st = parse_stat(bytes(data))
            if st:
                print(f"STAT v{st['ver']} flags2=0x{st['flags2']:04X} cur_samples={st['cur_samples']} wr={st['wr']} seq={st['cur_stream_seq']} sentA/B={st['sent0']}/{st['sent1']} dma0/1={st['dma0']}/{st['dma1']} sending={st['sending_ch']} pair fs={st['pair_fill']}/{st['pair_send']} lastTX={st['last_tx_len']}")
        elif len(data) >= 4 and data[0]==0x5A and data[1]==0xA5:
            flags = data[3]
            if flags & 0x80:
                print("TEST frame received")
                got_test = True
            else:
                typ = 'A' if (flags & 0x01) else ('B' if (flags & 0x02) else '?')
                seq = int.from_bytes(bytes(data[4:8]), 'little')
                ts  = int.from_bytes(bytes(data[8:12]), 'little')
                ns  = int.from_bytes(bytes(data[12:14]), 'little')
                print(f"{typ} seq={seq} ns={ns} ts={ts} len={len(data)}")
        # continue until deadline
    # STOP
    dev.write(EP_OUT, bytes([CMD_STOP]))

if __name__ == '__main__':
    main()
