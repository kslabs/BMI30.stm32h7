#!/usr/bin/env python3
# Read-only 2-minute smoke test: claim IF#2, set alt=1, read EP 0x83 in a loop.
# Prints summary with counts of packets and errors.

import os, sys, time, argparse, usb.core, usb.util

VID = int(os.getenv('VND_VID', '0xCAFE'), 16)
PID = int(os.getenv('VND_PID', '0x4001'), 16)
IFACE_INDEX = int(os.getenv('VND_INTF', '2'))
IN_EP = 0x83
TIMEOUT_MS = int(os.getenv('VND_TIMEOUT', '300'))
RUN_SECS_DEFAULT = int(os.getenv('VND_RUN_SECS', '120'))
SUMMARY_PATH_DEFAULT = os.getenv('VND_SMOKE_SUMMARY', 'HostTools/host_smoke_summary.txt')


def find_iface_with_eps(dev, out_ep=0x03, in_ep=0x83):
    for cfg in dev:
        for intf in cfg:
            eps = [ep.bEndpointAddress for ep in intf]
            if out_ep in eps and in_ep in eps:
                return cfg, intf
    return None, None


def main():
    ap = argparse.ArgumentParser(description='USB IN smoke test (read-only)')
    ap.add_argument('--secs', type=int, default=RUN_SECS_DEFAULT)
    ap.add_argument('--timeout', type=int, default=TIMEOUT_MS)
    ap.add_argument('--summary', type=str, default=SUMMARY_PATH_DEFAULT)
    args = ap.parse_args()
    run_secs = max(1, int(args.secs))
    timeout_ms = max(1, int(args.timeout))
    summary_path = args.summary
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("[SMOKE][ERR] Device not found")
        return 2
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    # Try robust interface selection like in read-only helper
    try:
        usb.util.claim_interface(dev, IFACE_INDEX)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=IFACE_INDEX, alternate_setting=1)
    except Exception:
        pass
    cfg, intf = find_iface_with_eps(dev, out_ep=0x03, in_ep=IN_EP)
    if cfg is None or intf is None:
        # Fallback to known interface index
        try:
            usb.util.claim_interface(dev, IFACE_INDEX)
        except Exception:
            pass
        class Tmp: pass
        intf = Tmp()
        intf.bInterfaceNumber = IFACE_INDEX
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except Exception:
        pass
    try:
        usb.util.claim_interface(dev, intf.bInterfaceNumber)
    except Exception:
        pass

    t0 = time.time()
    deadline = t0 + run_secs
    pkts = 0
    timeouts = 0
    errors = 0
    while time.time() < deadline:
        try:
            _ = dev.read(IN_EP, 512, timeout=timeout_ms)
            pkts += 1
        except usb.core.USBError as e:
            s = str(e).lower()
            if 'timed out' in s or e.errno == 110:
                timeouts += 1
                continue
            errors += 1
            break
    summary = f"secs={run_secs} pkts={pkts} timeouts={timeouts} errors={errors}"
    print(f"[SMOKE][SUMMARY] {summary}")
    try:
        d = os.path.dirname(summary_path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(summary + "\n")
    except Exception:
        pass
    return 0 if errors == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
