#!/usr/bin/env python3
# Start via control EP0, set alt=1, then read IN (0x83) for a duration.

import os, sys, time, argparse, usb.core, usb.util

VID = int(os.getenv('VND_VID', '0xCAFE'), 16)
PID = int(os.getenv('VND_PID', '0x4001'), 16)
IFACE_INDEX = int(os.getenv('VND_INTF', '2'))
IN_EP = 0x83

VND_CMD_SET_FULL_MODE   = 0x13
VND_CMD_SET_PROFILE     = 0x14
VND_CMD_SET_FRAME_SAMPLES = 0x17
VND_CMD_SET_ASYNC_MODE  = 0x18
VND_CMD_SET_CHMODE      = 0x19
VND_CMD_START           = 0x20
VND_CMD_STOP            = 0x21


def ctrl_out(dev, bRequest, wValue=0, recipient_interface=False, timeout=500):
    recip = usb.util.CTRL_RECIPIENT_INTERFACE if recipient_interface else usb.util.CTRL_RECIPIENT_DEVICE
    bm = usb.util.build_request_type(usb.util.CTRL_OUT, usb.util.CTRL_TYPE_VENDOR, recip)
    wIndex = IFACE_INDEX if recipient_interface else 0
    return dev.ctrl_transfer(bm, bRequest, wValue, wIndex, 0, timeout=timeout)


def main():
    ap = argparse.ArgumentParser(description='CTRL START + read IN for duration')
    ap.add_argument('--secs', type=int, default=120)
    ap.add_argument('--timeout', type=int, default=500)
    ap.add_argument('--full', type=int, default=1)
    ap.add_argument('--profile', type=int, default=2)
    ap.add_argument('--async', dest='async_mode', type=int, default=1)
    ap.add_argument('--chmode', type=int, default=0)
    args = ap.parse_args()

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print('[CTRL+READ][ERR] Device not found')
        return 2
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass

    # Pre-claim and set alt=1 before START to avoid races
    try:
        usb.util.claim_interface(dev, IFACE_INDEX)
    except Exception:
        pass
    try:
        dev.set_interface_altsetting(interface=IFACE_INDEX, alternate_setting=1)
    except Exception:
        pass

    # Configure via EP0 (wValue)
    try:
        ctrl_out(dev, VND_CMD_SET_FULL_MODE, wValue=(args.full & 0xFF))
        ctrl_out(dev, VND_CMD_SET_PROFILE, wValue=(args.profile & 0xFF))
        ctrl_out(dev, VND_CMD_SET_ASYNC_MODE, wValue=(args.async_mode & 0xFF))
        ctrl_out(dev, VND_CMD_SET_CHMODE, wValue=(args.chmode & 0xFF))
    except Exception as e:
        print(f"[CTRL+READ][WARN] SET_* via control failed: {e}")

    # START
    try:
        ctrl_out(dev, VND_CMD_START)
        print('[CTRL+READ] START via control OK')
    except Exception as e:
        print(f"[CTRL+READ][ERR] START via control failed: {e}")
        return 3

    # Read loop
    deadline = time.time() + max(1, int(args.secs))
    pkts = 0
    timeouts = 0
    errors = 0
    while time.time() < deadline:
        try:
            _ = dev.read(IN_EP, 512, timeout=max(1, int(args.timeout)))
            pkts += 1
        except usb.core.USBError as e:
            s = str(e).lower()
            if 'timed out' in s or e.errno == 110:
                timeouts += 1
                continue
            errors += 1
            break
    print(f"[CTRL+READ][SUMMARY] secs={int(args.secs)} pkts={pkts} timeouts={timeouts} errors={errors}")

    # STOP (best effort)
    try:
        ctrl_out(dev, VND_CMD_STOP)
    except Exception:
        pass
    try:
        usb.util.release_interface(dev, IFACE_INDEX)
    except Exception:
        pass
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass
    return 0 if errors == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
