#!/usr/bin/env python3
# Sequential USB reset attempts for BMI30 device
# Methods: soft (EP0 0x7E), deep (EP0 0x7F), bulk (0x22 to EP_OUT), port (libusb dev.reset())
# Requires: pyusb

import sys, time, argparse
import usb.core, usb.util

VENDOR_DEFAULT = 0xCAFE
PRODUCT_DEFAULT = 0x4001
INTERFACE = 2
EP_OUT = 0x03
CMD_DEVICE_RESET = 0x22
CMD_SOFT_RESET = 0x7E
CMD_DEEP_RESET = 0x7F

METHODS_DEFAULT = ["soft", "deep", "bulk", "port"]


def find_dev(vid: int, pid: int):
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    return dev


def claim_if_needed(dev):
    try:
        dev.set_configuration()  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        usb.util.claim_interface(dev, INTERFACE)
    except Exception:
        pass


def wait_until_gone(vid: int, pid: int, timeout: float, poll: float = 0.2) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if usb.core.find(idVendor=vid, idProduct=pid) is None:
            return True
        time.sleep(poll)
    return False


def wait_until_present(vid: int, pid: int, timeout: float, poll: float = 0.3) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if usb.core.find(idVendor=vid, idProduct=pid) is not None:
            return True
        time.sleep(poll)
    return False


def do_soft(dev) -> bool:
    try:
        dev.ctrl_transfer(0x40, CMD_SOFT_RESET, 0, 0, None, timeout=400)  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def do_deep(dev) -> bool:
    try:
        dev.ctrl_transfer(0x40, CMD_DEEP_RESET, 0, 0, None, timeout=500)  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def do_bulk(dev) -> bool:
    try:
        dev.write(EP_OUT, bytes([CMD_DEVICE_RESET]), timeout=700)  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def do_port_reset(dev) -> bool:
    try:
        try:
            usb.util.release_interface(dev, INTERFACE)
        except Exception:
            pass
        dev.reset()  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def run_once(vid: int, pid: int, methods, wait_gone: float, wait_present: float, verbose: bool) -> bool:
    dev = find_dev(vid, pid)
    if dev is None:
        print(f"[RESET] Device VID:PID {vid:04X}:{pid:04X} not found")
        return False
    claim_if_needed(dev)

    for m in methods:
        m = m.lower().strip()
        print(f"[RESET] Method: {m}")
        ok = False
        if m == "soft":
            ok = do_soft(dev)
        elif m == "deep":
            ok = do_deep(dev)
        elif m == "bulk":
            ok = do_bulk(dev)
        elif m == "port":
            ok = do_port_reset(dev)
        else:
            print(f"[RESET] Unknown method: {m}")
            continue
        if not ok:
            print(f"[RESET] {m} send failed")
        # small delay so device can start reset
        time.sleep(0.15)
        # try to detect disappearance
        gone = wait_until_gone(vid, pid, timeout=wait_gone)
        if verbose:
            print(f"[RESET] gone={gone}")
        if not gone and m != "port":
            # if device did not disappear after soft/deep/bulk, escalate to next
            continue
        # wait for re-appearance (port reset may not make it disappear; still wait present)
        present = wait_until_present(vid, pid, timeout=wait_present)
        if verbose:
            print(f"[RESET] present={present}")
        if present:
            print(f"[RESET] OK via {m}")
            return True
    print("[RESET] Sequence finished without confirmed re-enumeration")
    return False


def main():
    ap = argparse.ArgumentParser(description="Sequential USB reset attempts for BMI30")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=VENDOR_DEFAULT)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=PRODUCT_DEFAULT)
    ap.add_argument("--methods", type=str, default=",".join(METHODS_DEFAULT), help="Comma-separated list: soft,deep,bulk,port")
    ap.add_argument("--wait-gone", type=float, default=3.0)
    ap.add_argument("--wait-present", type=float, default=10.0)
    ap.add_argument("--retry", type=int, default=1, help="Repeat sequence up to N times until success")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    for i in range(args.retry):
        print(f"[RESET] Attempt {i+1}/{args.retry}: methods={methods}")
        ok = run_once(args.vid, args.pid, methods, args.wait_gone, args.wait_present, args.verbose)
        if ok:
            sys.exit(0)
        # small pause before next iteration
        time.sleep(0.5)
    sys.exit(2)


if __name__ == "__main__":
    main()
