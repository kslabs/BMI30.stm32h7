#!/usr/bin/env python3
# Query extended STAT via control transfer (EP0) during streaming to inspect runtime flags.
# Requires: pyusb and WinUSB/libusb bound to Vendor interface.

import sys, time, struct, usb.core, usb.util

VID = 0xCAFE
PID = 0x4001
EP_OUT = 0x03
IFACE_INDEX = 2
CMD_START = 0x20
CMD_STOP  = 0x21
CMD_GET_STATUS = 0x30

def ctrl_get_status(dev):
    # На Windows надёжнее адресовать DEVICE, а не INTERFACE; прошивка принимает любой recipient
    bm = usb.util.build_request_type(usb.util.CTRL_IN, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_DEVICE)
    data = dev.ctrl_transfer(bm, CMD_GET_STATUS, 0, 0, 64, timeout=500)
    ba = bytes(data)
    if len(ba) < 64 or ba[:4] != b'STAT':
        return None
    ver = ba[4]
    cur_samples = int.from_bytes(ba[6:8], 'little')
    frame_bytes = int.from_bytes(ba[8:10], 'little')
    test_frames = int.from_bytes(ba[10:12], 'little')
    produced_seq = int.from_bytes(ba[12:16], 'little')
    sent0 = int.from_bytes(ba[16:20], 'little')
    sent1 = int.from_bytes(ba[20:24], 'little')
    dbg_tx_cplt = int.from_bytes(ba[24:28], 'little')
    dbg_partial = int.from_bytes(ba[28:32], 'little')
    dbg_size_mismatch = int.from_bytes(ba[32:36], 'little')
    dma0 = int.from_bytes(ba[36:40], 'little')
    dma1 = int.from_bytes(ba[40:44], 'little')
    wr = int.from_bytes(ba[44:48], 'little')
    flags_rt = int.from_bytes(ba[48:50], 'little')
    flags2 = int.from_bytes(ba[50:52], 'little')
    sending_ch = ba[52]
    pair_idx = int.from_bytes(ba[54:56], 'little')
    last_tx_len = int.from_bytes(ba[56:58], 'little')
    cur_stream_seq = int.from_bytes(ba[58:62], 'little')
    return {
        'ver': ver,
        'cur_samples': cur_samples,
        'frame_bytes': frame_bytes,
        'test_frames': test_frames,
        'produced_seq': produced_seq,
        'sent0': sent0,
        'sent1': sent1,
        'dbg_tx_cplt': dbg_tx_cplt,
        'dbg_partial': dbg_partial,
        'dbg_size_mismatch': dbg_size_mismatch,
        'dma0': dma0,
        'dma1': dma1,
        'wr': wr,
        'flags_rt': flags_rt,
        'flags2': flags2,
        'sending_ch': sending_ch,
        'pair_fill': (pair_idx >> 8) & 0xFF,
        'pair_send': pair_idx & 0xFF,
        'last_tx_len': last_tx_len,
        'cur_stream_seq': cur_stream_seq,
    }

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Device not found")
        sys.exit(1)
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    # На Windows/WinUSB/libusb требуется явно захватить интерфейс для
    # vendor-specific CTRL (RECIPIENT_INTERFACE), иначе будет Pipe error.
    try:
        usb.util.claim_interface(dev, IFACE_INDEX)
    except Exception as e:
        # Продолжаем: на некоторых платформах auto-claim работает
        print(f"[warn] claim_interface({IFACE_INDEX}) failed or not needed: {e}")
    # START stream
    try:
        dev.write(EP_OUT, bytes([CMD_START]), timeout=500)
    except Exception as e:
        print(f"START write failed: {e}")
        sys.exit(2)
    t_end = time.time() + 3.0
    while time.time() < t_end:
        try:
            st = ctrl_get_status(dev)
            if st:
                print(f"STAT v{st['ver']} flags2=0x{st['flags2']:04X} cur_samples={st['cur_samples']} wr={st['wr']} seq={st['cur_stream_seq']} sentA/B={st['sent0']}/{st['sent1']} dma0/1={st['dma0']}/{st['dma1']} sending={st['sending_ch']} pair {st['pair_fill']}/{st['pair_send']} lastTX={st['last_tx_len']}")
        except Exception as e:
            print(f"CTRL status err: {e}")
        time.sleep(0.3)
    # STOP
    try:
        dev.write(EP_OUT, bytes([CMD_STOP]), timeout=500)
    except Exception:
        pass
    try:
        usb.util.release_interface(dev, IFACE_INDEX)
    except Exception:
        pass

if __name__ == '__main__':
    main()
