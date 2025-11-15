#!/usr/bin/env python3
"""Send DEVICE_RESET (0x22) over vendor BULK OUT (EP 0x03) on interface 2, then wait for re-enumeration.

Usage:
  py -3 HostTools/vendor_usb_device_reset.py [--timeout 3.0]
"""
import sys, time, argparse

try:
    import usb.core, usb.util
except Exception as e:
    print(f"[VND-RESET][ERR] PyUSB import failed: {e}")
    sys.exit(1)

VID=0xCAFE
PID=0x4001
INTERFACE=2
EP_OUT=0x03
CMD_DEVICE_RESET=0x22

def find_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit("[VND-RESET][ERR] Device not found")
    try:
        dev.set_configuration()  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        usb.util.claim_interface(dev, INTERFACE)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=INTERFACE, alternate_setting=1)  # type: ignore[attr-defined]
    except Exception:
        pass
    return dev

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout', type=float, default=3.0, help='Seconds to wait for re-enumeration')
    args = ap.parse_args()

    dev = find_dev()
    # Try STOP before RESET
    try:
        dev.write(EP_OUT, bytes([0x21]), timeout=300)  # STOP
        time.sleep(0.1)
    except Exception:
        pass
    # Send RESET via bulk
    try:
        dev.write(EP_OUT, bytes([CMD_DEVICE_RESET]), timeout=300)
        print("[VND-RESET] DEVICE_RESET sent via BULK OUT")
    except Exception as e:
        print(f"[VND-RESET][ERR] bulk write failed: {e}")
        sys.exit(2)
    # Release and wait for disappearance + reappear
    try:
        usb.util.release_interface(dev, INTERFACE)
    except Exception:
        pass
    t0 = time.time()
    # Wait for gone
    while time.time()-t0 < args.timeout:
        time.sleep(0.2)
        if usb.core.find(idVendor=VID, idProduct=PID) is None:
            break
    # Wait come back
    t1 = time.time()
    while time.time()-t1 < args.timeout:
        time.sleep(0.3)
        if usb.core.find(idVendor=VID, idProduct=PID) is not None:
            print("[VND-RESET] Device re-enumerated")
            sys.exit(0)
    print("[VND-RESET][WARN] Device may not have re-enumerated in time; continue")
    sys.exit(0)

if __name__ == '__main__':
    main()
