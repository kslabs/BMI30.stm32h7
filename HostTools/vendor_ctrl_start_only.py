#!/usr/bin/env python3
# Send vendor configuration via control EP0 and START streaming.
# Defaults: ASYNC=1, CHMODE=A-only, FULL_MODE=1, PROFILE=2 (can be changed by CLI).

import sys, argparse, usb.core, usb.util, time

VID = 0xCAFE
PID = 0x4001
IFACE_INDEX = 2

VND_CMD_SET_FULL_MODE   = 0x13
VND_CMD_SET_PROFILE     = 0x14
VND_CMD_SET_FRAME_SAMPLES = 0x17
VND_CMD_SET_ASYNC_MODE  = 0x18
VND_CMD_SET_CHMODE      = 0x19
VND_CMD_START           = 0x20
VND_CMD_STOP            = 0x21
VND_CMD_GET_STATUS      = 0x30


def build_bm_out(recipient_interface=True):
    # Build bmRequestType for control OUT: vendor, to INTERFACE (preferred) or DEVICE (fallback)
    recip = usb.util.CTRL_RECIPIENT_INTERFACE if recipient_interface else usb.util.CTRL_RECIPIENT_DEVICE
    return usb.util.build_request_type(usb.util.CTRL_OUT, usb.util.CTRL_TYPE_VENDOR, recip)


def ctrl_out(dev, bRequest, payload=b"", timeout=500):
    # Try INTERFACE recipient first, then fallback to DEVICE recipient
    last_err = None
    for use_iface in (True, False):
        bm = build_bm_out(recipient_interface=use_iface)
        try:
            if payload:
                return dev.ctrl_transfer(bm, bRequest, 0, IFACE_INDEX if use_iface else 0, payload, timeout=timeout)
            else:
                return dev.ctrl_transfer(bm, bRequest, 0, IFACE_INDEX if use_iface else 0, 0, timeout=timeout)
        except usb.core.USBError as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("ctrl_out failed")

def ctrl_out_wvalue(dev, bRequest, wValue=0, timeout=500):
    # Send control OUT without payload, parameter in wValue (works reliably on Windows)
    last_err = None
    for use_iface in (True, False):
        bm = build_bm_out(recipient_interface=use_iface)
        try:
            return dev.ctrl_transfer(bm, bRequest, wValue, IFACE_INDEX if use_iface else 0, 0, timeout=timeout)
        except usb.core.USBError as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("ctrl_out_wvalue failed")


def main():
    ap = argparse.ArgumentParser(description="Send vendor SET_* via EP0 control then START")
    ap.add_argument('--async', dest='async_mode', type=int, default=1, choices=[0,1], help='ASYNC mode (0=strict pair, 1=async)')
    ap.add_argument('--chmode', type=int, default=0, choices=[0,1,2], help='Channel mode: 0=A-only, 1=B-only, 2=both')
    ap.add_argument('--full', dest='full_mode', type=int, default=1, choices=[0,1], help='FULL_MODE (1=full, 0=ROI/diag)')
    ap.add_argument('--profile', type=int, default=2, help='Profile id (firmware-defined)')
    ap.add_argument('--frame-samples', type=int, default=None, help='Override samples per frame (u16)')
    ap.add_argument('--no-config', action='store_true', help='Do not send SET_* before START')
    ap.add_argument('--sleep', type=float, default=0.1, help='Sleep after START (sec)')
    ap.add_argument('--stop', action='store_true', help='Send STOP via control and exit')
    args = ap.parse_args()

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("[CTRL-START][ERR] Device not found")
        return 2
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass

    # Claim vendor interface to placate Windows/libusb
    try:
        usb.util.claim_interface(dev, IFACE_INDEX)
    except Exception as e:
        print(f"[CTRL-START][WARN] claim_interface({IFACE_INDEX}) failed or not needed: {e}")

    # Configure via control OUT
    if args.stop:
        try:
            ctrl_out(dev, VND_CMD_STOP)
            print("[CTRL-STOP] STOP sent via control OUT")
        except Exception as e:
            print(f"[CTRL-STOP][ERR] STOP via control failed: {e}")
        return 0
    if not args.no_config:
        try:
            ctrl_out_wvalue(dev, VND_CMD_SET_FULL_MODE, wValue=(args.full_mode & 0xFF))
            ctrl_out_wvalue(dev, VND_CMD_SET_PROFILE, wValue=(args.profile & 0xFF))
            ctrl_out_wvalue(dev, VND_CMD_SET_ASYNC_MODE, wValue=(args.async_mode & 0xFF))
            ctrl_out_wvalue(dev, VND_CMD_SET_CHMODE, wValue=(args.chmode & 0xFF))
            if args.frame_samples is not None:
                fs = max(0, min(65535, int(args.frame_samples)))
                ctrl_out_wvalue(dev, VND_CMD_SET_FRAME_SAMPLES, wValue=fs)
        except Exception as e:
            print(f"[CTRL-START][ERR] SET_* via control failed: {e}")
            return 3

    # START via control (no payload)
    try:
        ctrl_out(dev, VND_CMD_START)
        print("[CTRL-START] START sent via control OUT")
    except Exception as e:
        print(f"[CTRL-START][ERR] START via control failed: {e}")
        return 4

    time.sleep(args.sleep)
    try:
        usb.util.release_interface(dev, IFACE_INDEX)
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
