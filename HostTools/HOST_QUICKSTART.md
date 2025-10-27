# Host Quick Start (Vendor USB, strict A→B)

Use this to verify: STAT only between A/B pairs, strict A→B ordering, and GET_STATUS as a non‑blocking keepalive.

## Requirements
- Python 3.8+; PyUSB; libusb backend (Windows: `pip install pyusb libusb-package`)
- Windows: bind WinUSB to the Vendor Interface (#2) using Zadig (Options → List All Devices → "… (Interface 2)" → WinUSB → Install)
- Linux/RPi: typically works out of the box; use sudo or udev rules if permissions block access

## Run (PowerShell on Windows)
- Baseline 200 Hz blocks, ~20 FPS (Ns=10):
  - `python HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 2 --block-hz 200 --frame-samples 10 --frames 80 --ab-strict`

- 300 Hz blocks, ~20 FPS (Ns=15):
  - `python HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 2 --block-hz 300 --frame-samples 15 --frames 100 --ab-strict`

## Expected
- On START: one STAT (ACK) then immediate A; B follows right after A.
- During streaming in full mode: STAT appears only between complete A/B pairs.
- The script enforces strict A→B with `--ab-strict` and will exit on violations.
- Summary contains pairs_fps close to ~20 for the above settings.

## Troubleshooting
- IN timeouts while idle are normal; reading resumes on the next A. You can queue GET_STATUS as a keepalive.
- If STAT appears mid‑pair or B does not match the last A seq: the script prints [VIOLATION]. Capture logs and check device watchdogs.
- If no IN data: verify the correct interface/endpoint numbers (IF#2, OUT 0x03, IN 0x83) and that the driver is WinUSB on Windows.
