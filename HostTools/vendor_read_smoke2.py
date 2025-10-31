#!/usr/bin/env python3
# Robust 2-minute smoke test using the same interface handling as vendor_usb_read_only.py

import os, sys, time, argparse, usb.core, usb.util

VID = int(os.getenv('VND_VID', '0xCAFE'), 16)
PID = int(os.getenv('VND_PID', '0x4001'), 16)
OUT_EP = 0x03
IN_EP  = 0x83
IFACE_INDEX = int(os.getenv('VND_INTF', '2'))


def find_iface_with_eps(dev, out_ep=OUT_EP, in_ep=IN_EP):
    for cfg in dev:
        for intf in cfg:
            eps = [ep.bEndpointAddress for ep in intf]
            if out_ep in eps and in_ep in eps:
                return cfg, intf
    return None, None


def main():
    ap = argparse.ArgumentParser(description='USB IN smoke test (robust)')
    ap.add_argument('--secs', type=int, default=int(os.getenv('VND_RUN_SECS', '120')))
    ap.add_argument('--timeout', type=int, default=int(os.getenv('VND_TIMEOUT', '500')))
    ap.add_argument('--summary', type=str, default=os.getenv('VND_SMOKE_SUMMARY', 'HostTools/host_smoke_summary.txt'))
    args = ap.parse_args()
    run_secs = max(1, int(args.secs))
    timeout_ms = max(1, int(args.timeout))
    summary_path = args.summary

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print('[SMOKE2][ERR] Device not found')
        return 2
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass

    # Claim and set alt=1; find the interface by endpoints
    try:
        usb.util.claim_interface(dev, IFACE_INDEX)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=IFACE_INDEX, alternate_setting=1)
    except Exception:
        pass
    cfg, intf = find_iface_with_eps(dev)
    if cfg is None or intf is None:
        # Fallback to known interface index
        class Tmp: pass
        intf = Tmp(); intf.bInterfaceNumber = IFACE_INDEX
    try:
        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
            dev.detach_kernel_driver(intf.bInterfaceNumber)
    except Exception:
        pass
    try:
        usb.util.claim_interface(dev, intf.bInterfaceNumber)
    except Exception:
        pass

    # Run timed loop
    t0 = time.time()
    deadline = t0 + run_secs
    pkts = 0
    timeouts = 0
    errors = 0
    # Optional initial warm-up read
    try:
        dev.read(IN_EP, 512, timeout=timeout_ms)
    except usb.core.USBError:
        pass
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
    print(f"[SMOKE2][SUMMARY] {summary}")
    try:
        d = os.path.dirname(summary_path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(summary + '\n')
    except Exception:
        pass
    try:
        usb.util.release_interface(dev, intf.bInterfaceNumber)
    except Exception:
        pass
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass
    return 0 if errors == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
