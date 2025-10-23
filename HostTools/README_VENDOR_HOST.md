# Vendor USB host quick guide (Windows / PowerShell)

Prereqs
- Python 3.8+ and PyUSB: `pip install pyusb libusb-package`
- Device: VID=0xCAFE PID=0x4001, Vendor IF#2, OUT 0x03, IN 0x83
- Windows driver: bind WinUSB to Interface #2 (Zadig → Options → List All Devices → pick Interface 2 → WinUSB → Install)
- Alt-settings: Vendor IF#2 uses alt 0 (idle, no endpoints) and alt 1 (active, EP 0x03/0x83). Host tools automatically call SetInterface(IF#2, alt=1) before streaming.

Verify device
- `python -c "import usb.core;print(usb.core.find(idVendor=0xCAFE,idProduct=0x4001))"`

List interfaces and alt-settings
- `python HostTools/list_usb_interfaces.py` — you should see IF#2 with two alternates; streams require alt=1.

Minimal stream read (~20 FPS)
- 200 Hz blocks, Ns=10:
  - `python HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 2 --block-hz 200 --frame-samples 10 --frames 80 --ab-strict`
  - Expect: STAT only between pairs; alternating A/B frames with strict A→B; ns=10, len≈32+20=52 per frame.

- 300 Hz blocks, Ns=15:
  - `python HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 2 --block-hz 300 --frame-samples 15 --frames 100 --ab-strict`

Notes
- A→B ordering is enforced in firmware (seq increments on B completion). The script enforces it with `--ab-strict` and will fail on violations or if STAT arrives mid‑pair.
- GET_STATUS is a keepalive: device sends STAT between pairs only in full mode. Timeouts when idle are expected; reading resumes on next A.

Troubleshooting
- IN timeouts: normal when no data mid‑pair. Keep the window open or queue GET_STATUS; device will respond between pairs.
- If STAT appears mid‑pair or B seq mismatches last A: the script prints [VIOLATION]; collect logs and check firmware watchdogs.
- To stop: the script sends STOP on exit; you can also send STOP manually to OUT 0x03.

