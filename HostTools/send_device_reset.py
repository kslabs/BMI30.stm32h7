#!/usr/bin/env python3
"""Send DEVICE_RESET (0x22) vendor command over USB without using CDC COM port.

Falls back to CDC textual RESET if vendor interface not found or transmission fails.

Usage:
  python send_device_reset.py [--vid 0x0483] [--pid 0x5740] [--timeout 2.5]

Exit codes:
  0 - reset command sent (vendor or CDC) and delay elapsed
  1 - no device found
  2 - vendor transfer failed and CDC fallback failed
"""
import argparse, sys, time

try:
    import usb.core, usb.util
except Exception as e:  # pragma: no cover
    print(f"[RESET][ERR] PyUSB import failed: {e}")
    sys.exit(1)

CMD_DEVICE_RESET = 0x22  # Legacy: bulk vendor command handled in stream path
CMD_SOFT_RESET  = 0x7E  # Preferred: vendor control OUT without data
CMD_DEEP_RESET  = 0x7F  # Preferred: vendor control OUT without data

def find_dev(vid: int, pid: int):
    return usb.core.find(idVendor=vid, idProduct=pid)

def send_vendor_reset(dev, deep: bool=False) -> bool:
    # Assume interface 0, endpoint OUT not required for control; use control OUT (EP0)
    bmRequestType = 0x40  # Host to device, vendor, recipient: device (accepted regardless of recipient)
    bRequest = CMD_DEEP_RESET if deep else CMD_SOFT_RESET
    wValue = 0
    wIndex = 0
    data = None  # no data stage
    try:
        # Some stacks require a tiny delay before control after configuration
        time.sleep(0.01)
        # Control OUT with no data: use wLength=0; pyusb: pass length 0 or omit data buffer
        dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, None, timeout=250)
        print(f"[RESET][VND] {'DEEP' if deep else 'SOFT'}_RESET sent via control OUT (no data)")
        return True
    except usb.core.USBError as e:
        print(f"[RESET][VND][ERR] USBError: {e}")
        return False

def send_cdc_text_reset(vid: int, pid: int) -> bool:
    """Fallback: open CDC interface if accessible via Win32 COM port mapping.
    We do not enumerate COM ports here to avoid pyserial dependency; just log guidance.
    """
    print("[RESET][CDC][INFO] Fallback not implemented (pyserial missing). Please ensure COM port is free.")
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vid', type=lambda x: int(x,16), default=0x0483)
    ap.add_argument('--pid', type=lambda x: int(x,16), default=0x5740)
    ap.add_argument('--timeout', type=float, default=2.5, help='Seconds to wait after reset command')
    ap.add_argument('--deep', action='store_true', help='Use DEEP reset (0x7F) instead of SOFT (0x7E)')
    args = ap.parse_args()

    dev = find_dev(args.vid, args.pid)
    if dev is None:
        print(f"[RESET][ERR] USB device VID=0x{args.vid:04X} PID=0x{args.pid:04X} not found")
        sys.exit(1)
    # Detach kernel driver if needed
    try:
        if dev.is_kernel_driver_active(0):
            try:
                dev.detach_kernel_driver(0)
                print("[RESET][DBG] Detached kernel driver from interface 0")
            except Exception as e:
                print(f"[RESET][WARN] detach_kernel_driver failed: {e}")
    except Exception:
        pass
    sent = send_vendor_reset(dev, deep=args.deep)
    if not sent:
        if not send_cdc_text_reset(args.vid, args.pid):
            sys.exit(2)
    print(f"[RESET] Waiting {args.timeout:.2f}s for device reboot...")
    time.sleep(args.timeout)
    print("[RESET] Done")
    sys.exit(0)

if __name__ == '__main__':
    main()
