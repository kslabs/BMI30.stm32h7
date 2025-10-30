#!/usr/bin/env python3
# Read STAT via Control EP0 WITHOUT sending START/STOP
import sys, usb.core, usb.util

VID = 0xCAFE
PID = 0x4001
CMD_GET_STATUS = 0x30

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Device not found")
        sys.exit(1)
    try:
        dev.set_configuration()
    except:
        pass
    
    # Control GET_STATUS
    bm = usb.util.build_request_type(usb.util.CTRL_IN, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_DEVICE)
    data = dev.ctrl_transfer(bm, CMD_GET_STATUS, 0, 0, 64, timeout=500)
    ba = bytes(data)
    
    if len(ba) < 64 or ba[:4] != b'STAT':
        print("Invalid STAT response")
        sys.exit(2)
    
    sent0 = int.from_bytes(ba[16:20], 'little')
    sent1 = int.from_bytes(ba[20:24], 'little')
    dbg_tx_cplt = int.from_bytes(ba[24:28], 'little')
    flags_rt = int.from_bytes(ba[48:50], 'little')
    flags2 = int.from_bytes(ba[50:52], 'little')
    sending_ch = ba[52]
    pair_idx = int.from_bytes(ba[54:56], 'little')
    
    print(f"STAT: sentA/B={sent0}/{sent1} TxCplt={dbg_tx_cplt} flags=0x{flags_rt:04X} flags2=0x{flags2:04X} send_ch={sending_ch} pair {(pair_idx>>8)&0xFF}/{pair_idx&0xFF}")

if __name__ == '__main__':
    main()
