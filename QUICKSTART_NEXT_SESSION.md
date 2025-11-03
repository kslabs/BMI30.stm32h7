# Quick Start - Next Session

## Current Commit
```bash
git log -1 --oneline
# 6e0811f WIP: FPS profiling system + dual-channel fix
```

## What Works Now ✅
- **Dual-channel streaming**: A+B frames both received
- **Throughput**: ~50-90 pairs/sec (below target 200 Hz)
- **Build**: text=126036 bss=160624 (VND_PAIR_BUFFERS=8)
- **FPS profiling code**: Compiled and integrated

## What Doesn't Work ❌
- **CDC output**: FPS stats not visible via COM4
- **PERF command**: No response to "PERF\n" via CDC

## Critical Fix Applied
**VND_PAIR_BUFFERS MUST be 8, NOT 16**
- 16 causes BSS=204KB → streaming fails (A=0 B=0)
- 8 works: BSS=160KB → streaming OK

## Quick Test Commands

### 1. Basic 10s Test
```bash
py -3 HostTools\vendor_usb_start_and_read.py --window-sec 10 --pairs 10000 --ch-mode 2
```

### 2. Status Check
```bash
py -3 HostTools\vendor_quick_status.py
```

### 3. USB Reset (if device stuck)
```bash
py -3 -c "import usb.core; d=usb.core.find(idVendor=0xCAFE, idProduct=0x4001); d and d.reset()"
```

### 4. Build + Flash
```bash
make -C Debug all
# Flash via OpenOCD task or:
c:\Users\TEST\Documents\Work\BMI20\STM32\xpack-openocd-0.12.0-2-win32-x64\xpack-openocd-0.12.0-2\bin\openocd.exe -s c:/Users/TEST/Documents/Work/BMI20/STM32/xpack-openocd-0.12.0-2-win32-x64/xpack-openocd-0.12.0-2/scripts -f interface/stlink.cfg -f target/stm32h7x.cfg -c "program {C:\\Users\\TEST\\Documents\\Work\\BMI20\\STM32\\BMI30.stm32h7/Debug/BMI30.stm32h7.elf} verify reset exit"
```

## Priority Tasks for Next Session

### 1. Fix CDC FPS Output (1-2 hours)
**Option A - LED Debug**:
```c
// In vnd_report_fps_stats() - line 599
HAL_GPIO_TogglePin(GPIOC, GPIO_PIN_13); // or Data_ready_GPIO22
```
Confirms function is called even if CDC silent.

**Option B - VND Status Polling** (recommended):
```c
// Extend GET_STATUS response to include perf_stats
// Host polls via ctrl transfer instead of CDC
```

**Option C - Check CDC_Transmit_HS**:
```c
// In cdc_logf() - line 345
uint8_t result = CDC_Transmit_HS((uint8_t*)cdc_evt_buf, (uint16_t)n);
if(result != USBD_OK) {
    // Log failure via GPIO or vendor counter
}
```

### 2. Analyze Bottleneck (30 min - after fix #1)
Once FPS stats visible, identify bottleneck:
- **avg_prepare_us** > 2000 → ADC copy optimization needed
- **avg_transmit_us** > 1000 → USB speed issue
- **pair_interval_ms** > 10 → Scheduler problem

### 3. Optimize Based on Data (2-3 hours)
**If prepare slow**:
- Profile ADC buffer copy
- Use DMA or optimized memcpy

**If transmit slow**:
- Check USB HS vs FS mode
- Verify endpoint configuration

**If interval high**:
- Review vnd_next_pair_ms scheduling logic
- Check buffer management

## Files to Check First
1. `FPS_PROFILING_STATUS.md` - Detailed status document
2. `USB_DEVICE/App/usb_vendor_app.c` - Lines 599-673 (FPS functions)
3. `USB_DEVICE/App/usbd_cdc_if.c` - Lines 286-290 (PERF command)
4. `HostTools/vendor_usb_start_and_read.py` - Line 39 (ch-mode=2)

## Expected Timeline to 200 Hz
- Session 1 (today): ✅ FPS profiling infrastructure
- Session 2 (next): 🔧 Fix CDC output + collect data
- Session 3: ⚡ Implement optimization
- Session 4: 🎯 Achieve 200 Hz sustained

## Notes
- Always USB reset after flash: `d.reset()`
- CDC COM4 @ 115200 baud
- Device may need power cycle if really stuck
- Check `.elf` size after changes (should be ~126KB text)

---
**Last Update**: 2025-11-03  
**Commit**: 6e0811f  
**Status**: FPS profiling compiled, dual-channel works, CDC output pending
