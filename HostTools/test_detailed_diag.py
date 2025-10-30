#!/usr/bin/env python3
"""
Детальная диагностика: запускаем streaming и каждые 100ms через EP0 запрашиваем STAT.
Смотрим как растут счётчики DMA, produced, sent, TxCplt.
"""
import sys, time, usb.core, usb.util

VID = 0xCAFE
PID = 0x4001
EP_OUT = 0x03
CMD_START = 0x20
CMD_STOP = 0x21
CMD_GET_STATUS = 0x30

def ctrl_get_status(dev):
    bm = usb.util.build_request_type(usb.util.CTRL_IN, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_DEVICE)
    data = dev.ctrl_transfer(bm, CMD_GET_STATUS, 0, 0, 64, timeout=500)
    ba = bytes(data)
    if len(ba) < 64 or ba[:4] != b'STAT':
        return None
    
    prep_calls = ba[5]  # reserved0 теперь содержит dbg_prepare_calls (младшие 4 бита)
    produced_seq = int.from_bytes(ba[12:16], 'little')
    sent0 = int.from_bytes(ba[16:20], 'little')
    sent1 = int.from_bytes(ba[20:24], 'little')
    dbg_tx_cplt = int.from_bytes(ba[24:28], 'little')
    dma0 = int.from_bytes(ba[36:40], 'little')
    dma1 = int.from_bytes(ba[40:44], 'little')
    wr_seq = int.from_bytes(ba[44:48], 'little')
    flags_rt = int.from_bytes(ba[48:50], 'little')
    flags2 = int.from_bytes(ba[50:52], 'little')
    pair_idx = int.from_bytes(ba[54:56], 'little')
    
    # Распаковка счётчиков выходов из reserved2 (байт 53)
    reserved2 = ba[53]
    exit_wr_eq_rd = (reserved2 >> 0) & 0x03
    exit_samples_0 = (reserved2 >> 2) & 0x03
    exit_size_mismatch = (reserved2 >> 4) & 0x03
    exit_buf_not_fill = (reserved2 >> 6) & 0x03
    
    # reserved3 (байты 62-63): ВРЕМЕННО usb_isr_counter
    isr_counter = int.from_bytes(ba[62:64], 'little')
    
    return {
        'prep_calls': prep_calls,
        'produced_seq': produced_seq,
        'sent0': sent0,
        'sent1': sent1,
        'dbg_tx_cplt': dbg_tx_cplt,
        'dma0': dma0,
        'dma1': dma1,
        'wr_seq': wr_seq,
        'flags_rt': flags_rt,
        'flags2': flags2,
        'pair_fill': (pair_idx >> 8) & 0xFF,
        'pair_send': pair_idx & 0xFF,
        'exit_wr_eq_rd': exit_wr_eq_rd,
        'exit_samples_0': exit_samples_0,
        'exit_size_mismatch': exit_size_mismatch,
        'exit_buf_not_fill': exit_buf_not_fill,
        'isr_counter': isr_counter,
    }

def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Device not found")
        sys.exit(1)
    try:
        dev.set_configuration()
    except:
        pass
    
    # Claim vendor interface
    try:
        usb.util.claim_interface(dev, 2)
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
    except Exception as e:
        print(f"Warning: interface setup failed: {e}")
    
    print("Sending START...")
    dev.write(EP_OUT, bytes([CMD_START]), timeout=500)
    
    print("\nMonitoring counters (every 100ms, 5 seconds):")
    print("Time(ms) | DMA0  | WR_seq | PrepCalls | Produced | SentA | SentB | TxCplt | ISR_CNT | Exits[wr=rd|smpl=0|sz_m|buf] | Flags")
    print("-" * 140)
    
    t0 = time.time()
    last_stat = None
    
    for i in range(50):  # 50 * 100ms = 5 seconds
        time.sleep(0.1)
        elapsed_ms = int((time.time() - t0) * 1000)
        
        st = ctrl_get_status(dev)
        if st:
            # Calculate deltas
            if last_stat:
                delta_dma = st['dma0'] - last_stat['dma0']
                delta_wr = st['wr_seq'] - last_stat['wr_seq']
                delta_prep = st['prep_calls'] - last_stat['prep_calls']
                delta_prod = st['produced_seq'] - last_stat['produced_seq']
                delta_sentA = st['sent0'] - last_stat['sent0']
                delta_sentB = st['sent1'] - last_stat['sent1']
                delta_cplt = st['dbg_tx_cplt'] - last_stat['dbg_tx_cplt']
            else:
                delta_dma = delta_wr = delta_prep = delta_prod = delta_sentA = delta_sentB = delta_cplt = 0
            
            streaming = (st['flags_rt'] & 0x01) != 0
            flag_str = "STREAM" if streaming else "STOP"
            
            # Формат выхода с новыми счётчиками выходов
            print(f"{elapsed_ms:7d} | {st['dma0']:5d} (+{delta_dma:2d}) | {st['wr_seq']:5d} (+{delta_wr:2d}) | "
                  f"{st['prep_calls']:3d} (+{delta_prep:2d}) | "
                  f"{st['produced_seq']:4d} (+{delta_prod:2d}) | "
                  f"{st['sent0']:3d} (+{delta_sentA:2d}) | {st['sent1']:3d} (+{delta_sentB:2d}) | "
                  f"{st['dbg_tx_cplt']:4d} (+{delta_cplt:2d}) | "
                  f"ISR={st['isr_counter']:6d} Exits[{st['exit_wr_eq_rd']}|{st['exit_samples_0']}|{st['exit_size_mismatch']}|{st['exit_buf_not_fill']}] | "
                  f"{flag_str}")
            
            last_stat = st
        else:
            print(f"{elapsed_ms:7d} | ERROR reading STAT")
    
    print("\nSending STOP...")
    try:
        dev.write(EP_OUT, bytes([CMD_STOP]), timeout=500)
    except:
        pass
    
    print("\nRESULT:")
    if last_stat:
        print(f"  DMA0 received: {last_stat['dma0']} buffers")
        print(f"  Pairs created (produced_seq): {last_stat['produced_seq']}")
        print(f"  Sent A/B: {last_stat['sent0']}/{last_stat['sent1']}")
        print(f"  TxCplt callbacks: {last_stat['dbg_tx_cplt']}")
        print(f"  WR_seq: {last_stat['wr_seq']}")
        print(f"  EXIT COUNTERS (vnd_prepare_pair early exits):")
        print(f"    wr_eq_rd (no new frames):     {last_stat['exit_wr_eq_rd']}")
        print(f"    samples_0 (no ADC samples):   {last_stat['exit_samples_0']}")
        print(f"    size_mismatch (frame size):   {last_stat['exit_size_mismatch']}")
        print(f"    buf_not_FILL (buffer state):  {last_stat['exit_buf_not_fill']}")
        print(f"\n  USB ISR Counter: {last_stat['isr_counter']}")
        print(f"\n  CONCLUSION:")
        if last_stat['dma0'] > 100 and last_stat['sent0'] < 20:
            print("    ERROR: DMA works, but USB does NOT TRANSMIT!")
            print("    Problem in USB transmission, NOT in ADC/DMA.")
            # Проверим ISR counter
            if last_stat['isr_counter'] == 0:
                print("    ROOT CAUSE: USB ISR NEVER CALLED! Check interrupt enable (NVIC, USB_OTGx_IRQn).")
            elif last_stat['isr_counter'] > 0 and last_stat['dbg_tx_cplt'] == 0:
                print(f"    ISR called {last_stat['isr_counter']} times, but TxCplt NEVER invoked!")
                print("    ROOT CAUSE: HAL_PCD_IRQHandler() not processing IN endpoint completion.")
            elif last_stat['exit_buf_not_fill'] > 100:
                print("    ROOT CAUSE: Buffers not in FB_FILL state! TxCplt not called - buffers never freed.")
            elif last_stat['exit_samples_0'] > 10:
                print("    ROOT CAUSE: adc_stream_get_active_samples() returns 0! Check ADC profile init.")
            elif last_stat['exit_size_mismatch'] > 10:
                print("    ROOT CAUSE: Size mismatch between effective and cur_samples_per_frame!")
            elif last_stat['exit_wr_eq_rd'] > 100:
                print("    ROOT CAUSE: wr==rd despite DMA growing. Check frame_rd_seq update logic.")
        elif last_stat['produced_seq'] > 20 and last_stat['sent0'] < last_stat['produced_seq']:
            print("    ERROR: Buffers are created, but USB does NOT SEND!")
            print("    Problem in USB TX logic or hardware.")
        elif last_stat['sent0'] > 0 and last_stat['dbg_tx_cplt'] == 0:
            print("    ERROR: USB transmits, but TxCplt NOT CALLED!")
            print("    Problem in HAL DataIn callback chain.")
        else:
            print("    OK: Everything works (or test too short)")

if __name__ == '__main__':
    main()
