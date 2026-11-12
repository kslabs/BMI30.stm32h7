# Vendor USB host quick guide (Windows/RPi)

Minimal steps to verify STAT/TEST/A/B sequence and GET_STATUS mid-stream.

Note about VID/PID and examples:
- Defaults in all host tools remain VID=0xCAFE / PID=0x4001. You do NOT need to edit the code to use them.
- If your device uses other IDs (for example ST’s 0x0483/0x5740), pass them via CLI flags `--vid/--pid` when running the scripts. Examples below keep 0xCAFE/0x4001 to avoid confusion.

## Endpoints and protocol
- Vendor Bulk OUT: 0x03 (commands: 1-byte: 0x20 START, 0x21 STOP, 0x30 GET_STATUS, 0x10 SET_WINDOWS, 0x11 SET_BLOCK_HZ)
- Vendor Bulk IN:  0x83 (STAT 64B; TEST 48B; Work frames 1856B)
- Frame header (LE, 32 bytes): 5A A5 01 [flags] [seq:u32] [ts:u32] [total:u16=912] rest=0, crc16=0; flags: 0x80 TEST, 0x01 A, 0x02 B

## Windows
1) Use Zadig to assign WinUSB driver to "Vendor Interface" (interface #2) of the device.
2) Install Python 3.10+ and PyUSB: `pip install pyusb`.
3) Detect actual VID/PID and interface/EP on your device:
   - `py HostTools/list_usb_interfaces.py`
   Typical output (example): `VID=0xCAFE PID=0x4001`, Interface `#2`, EP OUT `0x03`, EP IN `0x83`.
4) Run script (defaults shown; replace only if your device uses different IDs):
   - `py HostTools/vendor_usb_start_and_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --pairs 4 --read-timeout-ms 1000`
4) Expected log on device UART (115200, [VND_TX] filter optional):
   - `[CMD] 0x20 len=1` after START send
   - `[VND_TX] ep=0x83 len=64 head=53 54 41 54 ...` — STAT (ACK-START) once
   - `[VND_TX] ep=0x83 len=48 head=5A A5 01 80 ...` — TEST once
   - Repeating:
     - `[VND_TX] ep=0x83 len=1856 head=5A A5 01 01 ...` — A
     - `[VND_TX] ep=0x83 len=1856 head=5A A5 01 02 ...` — B
5) Mid-stream GET_STATUS: the script will issue 0x30; you should see exactly one STAT between A/B pairs.
6) STOP (0x21): device sends STAT (ACK-STOP) then stops streaming.

## Raspberry Pi (RPi OS)
- Ensure `libusb-1.0-0` and permissions are OK.
- Detect IDs first:
   - `python3 HostTools/list_usb_interfaces.py`
- Quick start (defaults use 0xCAFE/0x4001):
   - `python3 HostTools/vendor_usb_start_and_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --pairs 4 --read-timeout-ms 1000`
   - or `sudo -E python3 HostTools/vendor_usb_start_and_read.py ...` if permissions require.
- Same expectations as on Windows. After START you should see STAT (64B), then TEST (48B), then A (1856B) and B (1856B) alternating.
- New firmware note: right after TEST the device explicitly queues the first working A/B pair. If ADC data isn’t ready yet, it sends a one-time synthetic pair (A: payload[i]=i; B: payload[i]=~i) to prove the USB path.
   Diagnostics in device log (UART/CDC):
   - `FIRST_A queued` — A поставлен на передачу;
   - `FIRST_A txcplt` — A подтверждён хостом (DataIn/ZLP завершён);
   - `FIRST_B queued` — B поставлен сразу после подтверждения A;
   - `FIRST_B txcplt` — B подтверждён, пара закрыта, seq++.
   Also, watchdogs:
   - `A_TXCPLT_WD (>120ms) -> assume complete, queue B` — если A завис без TxCplt, устройство принудительно открывает B;
   - `B_BUSY_RETRY cnt=N` — драйвер занят, B будет поставлен повторно.
   These markers help confirm the A→B transition on fragile hosts.
- If nothing is received, check dmesg and that the device enumerated as HS.

## Troubleshooting
- If `GET_STATUS` doesn't return STAT: confirm device logs `[CMD] 0x30` and look for `[VND_TX]` of size 64 between pairs. The gate is opened with a one-shot permit immediately before sending STAT from task.
- If vendor OUT seems ignored: verify OUT 0x03 is armed after each reception; our firmware calls `USBD_LL_PrepareReceive()` every time in DataOut handler.
- If frames are missing on host: ensure your reader reassembles 512-byte USB packets into the full 1856B frames.
- If you see zeros or only TEST after START on RPi: update firmware to this version. It forces transition TEST→A/B (with synthetic fallback) and logs `FIRST_A queued/txcplt` and `FIRST_B queued/txcplt`. Use the provided reader to verify sizes and flags.

### Minimal host sanity check (PyUSB)
If GUI shows TEST but not A/B, try a minimal reader to confirm bulk IN flow:

1) Run the quick reader without GUI (replace IDs accordingly):
   - `python3 HostTools/vendor_usb_start_and_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --pairs 4 --read-timeout-ms 1000`
2) Expected on device log: `STAT` (64B) → `TEST` (48B) → `FIRST_A queued/txcplt` → `FIRST_B queued/txcplt` and alternating A/B.
3) If you see `A_TXCPLT_WD` and `B_BUSY_RETRY` repeating, capture 10–20 lines around them and share; the device is auto‑recovering the transition A→B.

## Extra tools
- `HostTools/usb_vendor_probe.py` — scans vendor interfaces/EP, sends START/GET_STATUS, reads IN to verify basic connectivity.