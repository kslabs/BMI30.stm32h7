# BMI30 STM32H7 - USB Streaming Project

Embedded firmware for STM32H723 microcontroller with dual ADC streaming over USB Vendor Interface.

## Features

- **Dual ADC Streaming**: Support for two independent ADC channels (ADC1/ADC2)
- **USB Composite Device**: CDC (Serial) + Vendor Interface
  - CDC: Virtual COM port for logging (COM4 @115200)
  - Vendor: Bulk transfers (IN 0x83, OUT 0x03) for high-speed data streaming
- **Multiple Profiles**:
  - Profile 1: 200 Hz @ 1360 samples/frame
  - Profile 2: 300 Hz @ 912 samples/frame
- **Frame Format**: 32-byte header + payload with MAGIC 0xA55A
- **Real-time Diagnostics**: Status queries via EP0 Control endpoint

## Hardware

- MCU: STM32H723VGT6
- Debugger: ST-LINK/V2
- USB: Full-Speed 12 Mbps

## Build

```bash
cd Debug
make -j4 all
```

## Flash

```bash
STM32_Programmer_CLI -c port=SWD freq=4000 \
  -w Debug/BMI30.stm32h7.elf -v -rst
```

Or with OpenOCD:
```bash
openocd -s scripts -f interface/stlink.cfg -f target/stm32h7x.cfg \
  -c "program {Debug/BMI30.stm32h7.elf} verify reset exit"
```

## Host Tools

Located in `HostTools/` directory:

- `vendor_usb_start_and_read.py` - Start streaming and read frames
- `vendor_quick_status.py` - Query device status
- `vendor_enable_diag_and_read.py` - Enable diagnostic mode
- `com4_reader.py` - Monitor CDC serial port

## USB Protocol

### Commands (Bulk OUT 0x03)

| Command | Code | Payload | Description |
|---------|------|---------|-------------|
| START_STREAM | 0x20 | - | Start USB streaming |
| STOP_STREAM | 0x21 | - | Stop USB streaming |
| GET_STATUS | 0x30 | - | Query device status |
| SET_PROFILE | 0x14 | u8 (1 or 2) | Select profile |
| SET_FULL_MODE | 0x13 | u8 (0 or 1) | Full/Diagnostic mode |
| SET_FRAME_SAMPLES | 0x17 | u16 LE | Samples per frame |
| SET_WINDOWS | 0x10 | 8 bytes | ROI windows |
| SET_BLOCK_HZ | 0x11 | u16 LE | Block rate (Hz) |

### Frame Format (Bulk IN 0x83)

```
Header (32 bytes):
  [0..1]   : MAGIC 0xA55A (LE)
  [2]      : Version 0x01
  [3]      : Flags (0x01=ADC0, 0x02=ADC1)
  [4..7]   : Sequence number (u32 LE)
  [8..11]  : Timestamp (μs, u32 LE)
  [12..13] : Total samples (u16 LE)
  [14..31] : Reserved

Payload (variable):
  [32 .. 32+2*N-1]: Sample data (u16 LE pairs)
```

## Development

### Serial Monitor (CDC)

```powershell
.vscode/serial-monitor.ps1 -Port COM4 -Baud 115200
```

### Build + Flash

```powershell
# Quick build
make -C Debug all

# Build + Flash + Host test
HostTools/vendor_usb_start_and_read.py --pairs 12 --profile 1
```

## Changelog

### v1.0 (2025-10-23)
- Initial implementation
- Profile 1 (200 Hz) support fixed
- Dual ADC streaming stable
- USB vendor interface working

## License

Proprietary - AMSEC

## Support

For issues, check CDC output and use `vendor_quick_status.py` to query device state.
