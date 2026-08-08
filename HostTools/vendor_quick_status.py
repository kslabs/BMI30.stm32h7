import usb.core, usb.util, time, struct, argparse, sys, traceback, os, io

# Find device by VID/PID from usbd_desc.c
VID = 0xCAFE
PID = 0x4001

# Endpoints
EP_OUT = 0x03
EP_IN  = 0x83

CMD_START = 0x20
CMD_STOP  = 0x21
CMD_GET_STATUS = 0x30
CMD_GET_STATUS_IMM = 0x31

VND_STFLAG_OPTIC_ACTIVE = 0x0020
VND_STFLAG_MASTER_OPTIC_ACTIVE = 0x0080
VND_STFLAG_GROUP_OPTIC_ACTIVE = 0x0100

def _configure_unbuffered_io():
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(line_buffering=True)
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        # Best-effort; ignore if not supported
        pass

_configure_unbuffered_io()

def _enable_dual_logging():
    """Mirror stdout/stderr to a UTF-8 log file alongside console."""
    try:
        log_path = os.path.join(os.path.dirname(__file__), 'quick_status_debug.log')
        log_fp = open(log_path, 'w', encoding='utf-8', buffering=1)

        class _Tee:
            def __init__(self, a, b):
                self.a = a
                self.b = b
            def write(self, s):
                try:
                    self.a.write(s)
                except Exception:
                    pass
                try:
                    self.b.write(s)
                except Exception:
                    pass
                return len(s)
            def flush(self):
                for t in (self.a, self.b):
                    try:
                        t.flush()
                    except Exception:
                        pass

        sys.stdout = _Tee(sys.stdout, log_fp)
        sys.stderr = _Tee(sys.stderr, log_fp)
        print(f"[DBG] Logging to {log_path}")
    except Exception:
        # Non-fatal if logging can't be enabled
        traceback.print_exc()

_enable_dual_logging()

def enumerate_devices():
    print("[DBG] Enumerating USB devices (PyUSB)...", flush=True)
    try:
        for dev in usb.core.find(find_all=True):
            try:
                vid = dev.idVendor
                pid = dev.idProduct
                bus = getattr(dev, 'bus', '?')
                addr = getattr(dev, 'address', '?')
                print(f"  - {vid:04x}:{pid:04x} bus={bus} addr={addr}")
                for cfg in dev:
                    for intf in cfg:
                        eps = [f"0x{ep.bEndpointAddress:02x}" for ep in intf]
                        print(f"     IF#{intf.bInterfaceNumber} alt={intf.bAlternateSetting} eps={eps}")
            except Exception as inner:
                print("    (error enumerating device)", inner)
    except Exception as e:
        print("[DBG] enumerate_devices failed:", e)

def find_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"[FATAL] Device not found VID:PID={VID:04x}:{PID:04x}")
        return None
    try:
        dev.set_configuration()
    except usb.core.USBError:
        # Might already be configured
        pass
    # Ensure Vendor IF#2 alt=1 is active and claim interface if needed
    try:
        usb.util.claim_interface(dev, 2)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        print("[OK] IF#2 alt=1 set (Vendor endpoints active)")
    except usb.core.USBError as e:
        print(f"[WARN] SET_INTERFACE alt=1 failed: {e}")
    # Validate endpoints exist in active config; if not, re-apply config and alt
    eps = []
    try:
        cfg = dev.get_active_configuration()
        for intf in cfg:
            if intf.bInterfaceNumber == 2 and intf.bAlternateSetting == 1:
                eps = [ep.bEndpointAddress for ep in intf]
                break
    except Exception:
        pass
    if EP_IN not in eps or EP_OUT not in eps:
        print("[WARN] Vendor endpoints not found on IF#2, retrying set_configuration+alt=1 …")
        try:
            dev.set_configuration()
            usb.util.claim_interface(dev, 2)
            dev.set_interface_altsetting(interface=2, alternate_setting=1)
            # Re-check after retry
            eps = []
            try:
                cfg = dev.get_active_configuration()
                for intf in cfg:
                    if intf.bInterfaceNumber == 2 and intf.bAlternateSetting == 1:
                        eps = [ep.bEndpointAddress for ep in intf]
                        break
            except Exception:
                pass
            if EP_IN in eps and EP_OUT in eps:
                print("[OK] IF#2 alt=1 set after retry")
            else:
                print(f"[ERR] IF#2 alt=1 enabled but endpoints still missing; eps={eps}")
        except Exception as e:
            print(f"[ERR] Unable to enable IF#2 alt=1: {e}")
    
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
    reserved3 = int.from_bytes(buf[62:64], 'little')
    optic_power = (reserved3 >> 8) & 0xFF
    optic_hold_seconds = (reserved3 >> 2) & 0x3F
    optic_active_packed = 1 if (reserved3 & 0x01) else 0
    tx_enable_packed = 1 if (reserved3 & 0x02) else 0
    optic_active_flag = 1 if (flags_runtime & VND_STFLAG_OPTIC_ACTIVE) else 0
    master_optic_flag = 1 if (flags_runtime & VND_STFLAG_MASTER_OPTIC_ACTIVE) else 0
    group_optic_flag = 1 if (flags_runtime & VND_STFLAG_GROUP_OPTIC_ACTIVE) else 0
    st = {
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
        'optic_power': optic_power,
        'optic_hold_seconds': optic_hold_seconds,
        'optic_active_packed': optic_active_packed,
        'optic_active_flag': optic_active_flag,
        'master_optic_flag': master_optic_flag,
        'group_optic_flag': group_optic_flag,
        'tx_enable_packed': tx_enable_packed,
    }
    if ver >= 2 and len(buf) >= 76:
        st['stage_alt1_ms'] = int.from_bytes(buf[64:68], 'little')
        st['stage_start_ms'] = int.from_bytes(buf[68:72], 'little')
        st['stage_first_frame_ms'] = int.from_bytes(buf[72:76], 'little')
    if len(buf) >= 136:
        st['optic_hold_ds'] = int.from_bytes(buf[96:98], 'little')
        st['led_pattern'] = buf[98]
        st['sync_local_status'] = buf[99]
        st['sync_seen_mask'] = int.from_bytes(buf[100:104], 'little')
        st['sync_node_count'] = buf[104]
        st['sync_status_bytes'] = list(buf[105:137])
    return st


def sync_remote_summary(st):
    if 'sync_local_status' not in st:
        return ''
    local_status = st.get('sync_local_status', 0)
    local_id = local_status & 0x1F
    seen_mask = st.get('sync_seen_mask', 0)
    direct_ids = st.get('ver', 0) >= 6
    remote = []
    for idx, status in enumerate(st.get('sync_status_bytes', [])):
        node_id = idx if direct_ids else idx + 1
        if not (seen_mask & (1 << idx)):
            continue
        if node_id == local_id:
            continue
        remote.append((node_id, status, 1 if (status & 0x20) else 0))
    remote_txt = ','.join(f'{node}:0x{status:02X}/optic={optic}' for node, status, optic in remote) or '-'
    remote_optic_any = 1 if any(optic for _, _, optic in remote) else 0
    local_optic = 1 if (local_status & 0x20) else 0
    master_optic = 1 if st.get('master_optic_flag', 0) else 0
    return (
        f' sync_local_id={local_id} sync_local_optic={local_optic}'
        f' master_optic={master_optic}'
        f' remote_status={remote_txt} remote_optic_any={remote_optic_any}'
        f' group_optic={st.get("group_optic_flag", 0)}'
    )


def main(duration_secs: float = 5.0):
    print("==== vendor_quick_status: debug start ====", flush=True)
    print("[DBG] Python:", sys.version, flush=True)
    enumerate_devices()
    dev = find_dev()
    if dev is None:
        print("[FATAL] Device not available — exiting without exception.")
        print("==== vendor_quick_status: debug end ====")
        return
    try:
        # START
        try:
            dev.write(EP_OUT, bytes([CMD_START]))
            print("[OK] START sent")
        except Exception as e:
            print("[ERR] Failed to send START:", e)
        # Give device a brief moment to arm streaming before first bulk IN
        time.sleep(0.15)
        t0 = time.time()
        got_test = False
        deadline = time.time() + float(duration_secs)
        retried_alt = False
        while time.time() < deadline:
            try:
                data = dev.read(EP_IN, 512, timeout=500)
            except usb.core.USBTimeoutError:
                # Windows libusb backend raises USBTimeoutError (e.errno == 10060)
                # Try GET_STATUS_IMM during stream (diagnostic)
                try:
                    dev.write(EP_OUT, bytes([CMD_GET_STATUS_IMM]))
                except Exception:
                    pass
                continue
            except usb.core.USBError as e:
                if getattr(e, 'errno', None) in (110, 10060):
                    try:
                        dev.write(EP_OUT, bytes([CMD_GET_STATUS_IMM]))
                    except Exception:
                        pass
                    continue
                msg = str(e)
                if 'endpoint' in msg.lower() or 'pipe' in msg.lower():
                    if not retried_alt:
                        print(f"[WARN] Read failed ({e}); retry IF#2 alt=1 …")
                        # Try to recover from a stalled pipe: clear HALT on IN/OUT before re-applying alt
                        try:
                            dev.clear_halt(EP_IN)
                        except Exception:
                            pass
                        try:
                            dev.clear_halt(EP_OUT)
                        except Exception:
                            pass
                        try:
                            usb.util.claim_interface(dev, 2)
                        except Exception:
                            pass
                        try:
                            dev.set_interface_altsetting(interface=2, alternate_setting=1)
                            retried_alt = True
                            continue
                        except Exception:
                            pass
                # Try to solicit immediate STAT and continue loop
                try:
                    dev.write(EP_OUT, bytes([CMD_GET_STATUS_IMM]))
                except Exception:
                    pass
                print("[ERR] USBError during read:", e)
                traceback.print_exc()
                continue
            except Exception as e:
                print("[ERR] Non-USB exception during read:", e)
                traceback.print_exc()
                break
            head = bytes(data[:4])
            if head == b'STAT':
                st = parse_stat(bytes(data))
                if st:
                    line = f"STAT v{st['ver']} flags2=0x{st['flags2']:04X} cur_samples={st['cur_samples']} wr={st['wr']} seq={st['cur_stream_seq']} sentA/B={st['sent0']}/{st['sent1']} dma0/1={st['dma0']}/{st['dma1']} sending={st['sending_ch']} pair fs={st['pair_fill']}/{st['pair_send']} lastTX={st['last_tx_len']} optic_power={st['optic_power']} optic_hold_ds={st.get('optic_hold_ds', st['optic_hold_seconds']*10)} optic_active={1 if (st['optic_active_packed'] or st['optic_active_flag']) else 0} tx_enable={st['tx_enable_packed']} led_pattern={st.get('led_pattern', '-')} sync_count={st.get('sync_node_count', '-')}{sync_remote_summary(st)}"
                    if st.get('stage_start_ms') and st.get('stage_first_frame_ms'):
                        dt = st['stage_first_frame_ms'] - st['stage_start_ms']
                        line += f" stages: alt1={st.get('stage_alt1_ms', 0)} start={st['stage_start_ms']} first={st['stage_first_frame_ms']} Δ={dt}ms"
                    print(line)
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
    except Exception:
        print("[ERR] Uncaught exception in main loop:")
        traceback.print_exc()
    finally:
        try:
            dev.write(EP_OUT, bytes([CMD_STOP]))
            print("[OK] STOP sent")
        except Exception as e:
            print("[WARN] Failed to send STOP:", e)
        print("==== vendor_quick_status: debug end ====")
        # normal exit

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Quick vendor stream status monitor")
    parser.add_argument('--secs', type=float, default=30.0, help='Duration to run (seconds)')
    args = parser.parse_args()
    print(f"[INFO] Running quick status for {args.secs:.1f}s...", flush=True)
    main(duration_secs=args.secs)
