# Host Quick Start (Vendor USB)

Use this to verify STAT → TEST → A/B sequence and GET_STATUS mid‑stream.

## Requirements
- Python 3.10+; PyUSB; libusb backend
- Linux/RPi: usually already present; use sudo if permissions block access
- Windows: assign WinUSB to Vendor Interface (#2) via Zadig

## Run
```bash
python vendor_usb_start_and_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --pairs 4
```

Expected device UART log:
- [CMD] 0x20 len=1
- [VND_TX] ep=0x83 len=64 head=53 54 41 54 … (STAT, ACK-START)
- [VND_TX] ep=0x83 len=48 head=5A A5 01 80 … (TEST)
- Repeating pairs:
  - len=1856 head=5A A5 01 01 … (A)
  - len=1856 head=5A A5 01 02 … (B)
- Mid-stream GET_STATUS from host: exactly one STAT between pairs
- STOP (0x21): STAT, then stream stops

## Troubleshooting
- If no STAT on GET_STATUS: check device gate logic (permit_once set before sending from task)
- If no IN data at all: verify OUT 0x03 is re-armed after each OUT, and that device uses IN 0x83
- If frames are missing: ensure host reassembles full 1856B frames from 512B packets
