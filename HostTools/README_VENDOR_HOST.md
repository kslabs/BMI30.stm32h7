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

Required commands for bulk stream start
- Bulk stream does not start from DC commands alone.
- The minimum working sequence for Vendor IF#2 is:
  - `SetInterface(IF#2, alt=1)`
  - `SET_WINDOWS (0x10)`
  - `SET_STREAM_MODE (0x15)`
  - `SET_ASYNC (0x18)` and/or `SET_PROFILE (0x14)` as required by the host mode
  - `START_STREAM (0x20)`
- `SET_DC_ADAPT (0x1B)`, `CALIB_DC_FAST (0x1E)`, and `GET_STATUS` via EP0 do not start the bulk stream by themselves.
- Symptom from logs: if you only see repeated `0x1B`, `0x1E`, and `EP0 status len=64`, then the device is alive on EP0, but the host has not executed the stream-start sequence above.

Minimal stream read (~20 FPS)
- 200 Hz blocks, Ns=10:
  - `python HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 2 --block-hz 200 --frame-samples 10 --frames 80 --ab-strict`
  - Expect: STAT only between pairs; alternating A/B frames with strict A→B; ns=10, len≈32+20=52 per frame.

- 300 Hz blocks, Ns=15:
  - `python HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 2 --block-hz 300 --frame-samples 15 --frames 100 --ab-strict`

Notes
- A→B ordering is enforced in firmware (seq increments on B completion). The script enforces it with `--ab-strict` and will fail on violations or if STAT arrives mid‑pair.
- GET_STATUS is a keepalive: device sends STAT between pairs only in full mode. Timeouts when idle are expected; reading resumes on next A.
- Frame parity/even-odd: in current protocol it is derived from `reserved2` (buffer index), `parity = reserved2 & 1`. Do not use `flags & 0x80` for parity, because bit7 in flags is reserved for TEST frames.

Troubleshooting
- IN timeouts: normal when no data mid‑pair. Keep the window open or queue GET_STATUS; device will respond between pairs.
- If STAT appears mid‑pair or B seq mismatches last A: the script prints [VIOLATION]; collect logs and check firmware watchdogs.
- To stop: the script sends STOP on exit; you can also send STOP manually to OUT 0x03.

Optic, sync status, and dynamic LEDs (RPI)
- Set optical TX power/sensitivity with command `0x34` (payload `u8 0..255`).
- Set optic active hold time with command `0x39`.
  - New payload: `u16 hold_ds` little-endian, in 0.1 second units. `0=default 30` (3.0 s), valid range after clamp is `1..600` (0.1..60.0 s).
  - Legacy payload is still accepted: one byte `u8 seconds`, `0=default 3`, old hosts keep working.
- In `HostTools/vendor_stream_read.py` use:
  - `--optic-power 0..255`
  - `--optic-hold-s 0..60` (float is accepted, e.g. `1.5`)
- Firmware logic: pulse counting is no longer used; any input change on the photoreceiver sets `optic_active=1` and each new change extends the active state by the configured number of seconds.
- Read current configured values and current photoreceiver trigger from `STAT`:
  - `reserved3[15:8]` = `optic_power`
  - `reserved3[7:2]` = legacy rounded-up `optic_hold_seconds`
  - `reserved3[0]` = `optic_active` (1/0)
  - `flags_runtime bit 0x20` duplicates `optic_active`
  - Full `STAT` v5 is 136 bytes. Request 136 bytes via EP0 `GET_STATUS` to get:
    - offset `96`: `u16 optic_hold_ds`
    - offset `98`: `u8 led_pattern`
    - offset `99`: `u8 sync_local_status`
    - offset `100`: `u32 sync_seen_mask`, bit0=node1 ... bit30=node31
    - offset `104`: `u8 sync_node_count`
    - offset `105..135`: `u8 sync_status_bytes[31]`, index0=node1 ... index30=node31
  - Each sync status byte uses bits `0..4=node_id`, bit `5=photoreceiver active`, bit `6=TX enabled`, bit `7=label/reserved`.
- Select one of the preprogrammed dynamic LED patterns with command `0x3B` (payload `u8 pattern_id`). Pattern `0` is OFF. The first onboard/system LED is still overlaid by STM32 status logic; host control applies to the 20 dynamic LEDs.
  - Useful pattern IDs: `1=IDLE_BREATHE`, `2=STREAMING`, `3=SYNC_PULSE`, `8=EVENT_B_UP`, `9=EVENT_A_DOWN`, `10=EVENT_BOTH_ALT`, `11=EVENT_SPLIT_IN`, `12=EVENT_SPLIT_OUT`, `13=TEST_DRIP`, `14=TEST_SCOPE_RGB`, `15=TEST_BLUE`, `16=TEST_COLOR_CYCLE`.
  - Existing command `0x35` still triggers temporary event patterns (`event_id`, `duration_ms`) without changing the selected base pattern.
  - Helper: `python HostTools/send_led_event.py --pattern TEST_DRIP` or `--pattern OFF`.
- Ready-to-use readers:
  - `python HostTools/vendor_get_status.py --ctrl --repeat 5`
  - `python HostTools/vendor_quick_status.py --secs 5`

DC compensation speed
- Быстрое управление обучением DC (freeze/active): команда `0x1B` (`CMD_SET_DC_ADAPT`), payload `u8`:
  - `0` = FREEZE (обучение остановлено, вычитание DC продолжается)
  - `1` = ACTIVE (обучение возобновлено)
- Полная конфигурация режимов/временных констант: команда `0x1F` (`SET_DC_CONFIG`) и чтение `0x3A` (`GET_DC_CONFIG`).
- Modes:
  - `0` = `FREEZE`: keep subtracting current DC, stop learning.
  - `1` = `WORK`: slow continuous tracking.
  - `2` = `DETECT`: medium tracking while tag detection is active.
  - `3` = `BOOT_FAST`: fast tracking, then automatic `WORK` after `fast_duration`.
- Recommended defaults for the web host:
  - `work_settle_s=900`
  - `detect_settle_s=60`
  - `fast_settle_s=5`
  - `fast_duration_s=30`
- Python helper:
  ```python
  from usb_vendor.usb_stream import (
      USBStream,
      DC_MODE_WORK,
      DC_MODE_DETECT,
      DC_MODE_FREEZE,
      DC_MODE_BOOT_FAST,
  )

  s = USBStream()

  # Quick toggle (0x1B)
  s.set_dc_adapt(False)   # freeze learning
  s.set_dc_adapt(True)    # resume learning

  # Apply settings from the authorized web page.
  s.set_dc_config_seconds(
      mode=DC_MODE_WORK,
      work_settle_s=900,
      detect_settle_s=60,
      fast_settle_s=5,
      fast_duration_s=30,
  )

  # When tag detection starts.
  s.set_dc_mode(DC_MODE_DETECT)

  # When tag detection ends.
  s.set_dc_mode(DC_MODE_WORK)

  # Optional: freeze learning while still subtracting stored DC.
  s.set_dc_mode(DC_MODE_FREEZE)

  # Read back current firmware state via EP0.
  cfg = s.get_dc_config()
  print(cfg)
  ```
- Raw `SET_DC_CONFIG` payload after opcode `0x1F`:
  - `<BBHIIII`
  - `version=1`
  - `mode`
  - `flags=0`
  - `work_settle_ms`
  - `detect_settle_ms`
  - `fast_settle_ms`
  - `fast_duration_ms`
- Raw `GET_DC_CONFIG`: EP0 vendor IN, request `0x3A`, length `40`; response starts with `DCCF`.

### Сохранение массива DC в Flash
- Команда: `0x2B` (`VND_CMD_SAVE_DC_TO_FLASH`)
- Описание: сохраняет текущий массив DC компенсации в память Flash.
- Использование: отправьте команду с пустым payload; устройство выполнит сохранение и вернёт подтверждение.
- Назначение: позволяет RPI (хосту) быстро сохранить уже скомпенсированный массив DC после завершения процесса адаптации, вместо ожидания автоматического сохранения через 20 минут.

## Temperature sensor (GET_TEMP)

Read the built-in STM32H723 crystal temperature sensor via command `0x31` (CMD_GET_TEMP).

### Protocol
- **Command (OUT → device)**: single byte `0x31`
- **Response (IN ← device)**: 4 bytes
  - Byte 0: `0x80` (RSP_ACK)
  - Byte 1: `0x31` (echo command)
  - Bytes 2-3: temperature in °C as signed 16-bit integer (LE), e.g. `0x1C 0x00` = +28°C

### Example (Python)
```python
import usb.core

# Find device
dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
if not dev:
    raise RuntimeError("Device not found")

# Set alt interface (active endpoints)
dev.set_interface_altsetting(interface_number=2, alternate_setting=1)

# Send command 0x31 (CMD_GET_TEMP)
ep_out = 0x03
cmd = bytes([0x31])
dev.write(ep_out, cmd)

# Read response (4 bytes)
ep_in = 0x83
response = dev.read(ep_in, 4, timeout=1000)  # timeout 1000 ms

# Parse
if len(response) >= 4 and response[0] == 0x80 and response[1] == 0x31:
    temp_raw = response[2] | (response[3] << 8)
    # Convert from unsigned to signed 16-bit
    if temp_raw & 0x8000:
        temp_c = temp_raw - 0x10000
    else:
        temp_c = temp_raw
    print(f"Temperature: {temp_c}°C")
else:
    print("Invalid response")
```

### Notes
- Temperature accuracy: ±5°C typical (built-in sensor calibration)
- Resolution: 0.5°C
- Temperature range: -40°C to +85°C (STM32H723 specification)
- Sensor response time: ~1 ms
- Command can be issued during streaming or when idle
- No payload required; just send single byte `0x31`

## Firmware version (GET_VERSION)

Get the firmware version from device command `0x32` (CMD_GET_VERSION).

### Protocol
- **Command (OUT → device)**: single byte `0x32`
- **Response (IN ← device)**: 6 bytes
  - Byte 0: `0x80` (RSP_ACK)
  - Byte 1: `0x32` (echo command)
  - Byte 2: firmware major version (e.g., `1`)
  - Byte 3: firmware minor version (e.g., `2`)
  - Byte 4: firmware patch version (e.g., `3`)
  - Byte 5: build number (reserved, usually `0`)

**Example response for v1.2.3**:
```
[0x80, 0x32, 0x01, 0x02, 0x03, 0x00]
```

### Example (Python)
```python
import usb.core

# Find device
dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
if not dev:
    raise RuntimeError("Device not found")

# Set alt interface (active endpoints)
dev.set_interface_altsetting(interface_number=2, alternate_setting=1)

# Send command 0x32 (CMD_GET_VERSION)
ep_out = 0x03
cmd = bytes([0x32])
dev.write(ep_out, cmd)

# Read response (6 bytes)
ep_in = 0x83
response = dev.read(ep_in, 6, timeout=1000)  # timeout 1000 ms

# Parse
if len(response) >= 6 and response[0] == 0x80 and response[1] == 0x32:
    major = response[2]
    minor = response[3]
    patch = response[4]
    build = response[5]
    print(f"Firmware version: {major}.{minor}.{patch} (build {build})")
else:
    print("Invalid response")
```

### Notes
- Version format: `MAJOR.MINOR.PATCH` (semantic versioning)
- Build number is reserved for future use
- Command can be issued anytime (idle, streaming, or after errors)
- No payload required; just send single byte `0x32`
- Useful for debugging and compatibility checks
