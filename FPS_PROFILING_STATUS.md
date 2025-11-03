# FPS Profiling Implementation Status
**Date**: 2025-11-03  
**Branch**: rollback/diag-2025-11-02  
**Firmware**: text=126036 bss=160624 (VND_PAIR_BUFFERS=8)

## Summary
Добавлена система профилирования производительности для измерения FPS и узких мест в передаче данных.

## Current Status: РАБОТАЕТ (частично)
- ✅ Dual-channel mode работает (A+B frames received)
- ✅ Код профилирования добавлен и скомпилирован
- ✅ VND_PAIR_BUFFERS откачен с 16→8 (критичное исправление)
- ⚠️ FPS reporting через CDC не работает (причина неясна)
- ⚠️ Throughput ~50-90 pairs/sec вместо целевых 200 Hz

## Key Changes

### 1. Firmware (USB_DEVICE/App/usb_vendor_app.c)
**FPS Measurement Variables** (lines 221-228):
```c
static uint32_t fps_pair_count = 0;
static uint32_t fps_frame_a_count = 0;
static uint32_t fps_frame_b_count = 0;
static uint32_t fps_prepare_count = 0;
static uint32_t fps_measurement_start_ms = 0;
static uint32_t fps_last_report_ms = 0;
```

**Performance Stats Structure** (lines 230-241):
```c
typedef struct {
    uint32_t prepare_total_us;
    uint32_t prepare_count;
    uint32_t transmit_total_us;
    uint32_t transmit_count;
    uint32_t txcplt_total_us;
    uint32_t txcplt_count;
    uint32_t last_pair_complete_ms;
    uint32_t min_pair_interval_ms;
    uint32_t max_pair_interval_ms;
} perf_stats_t;
static perf_stats_t perf_stats = {0};
```

**Timing Function** (lines 245-256):
```c
static inline uint32_t get_us_approx(void) {
    // HAL_GetTick() based microsecond approximation
    // Safer than DWT for our purposes
    static uint32_t last_tick = 0, us_offset = 0;
    uint32_t tick = HAL_GetTick();
    if(tick != last_tick){ us_offset = 0; last_tick = tick; }
    return tick * 1000 + us_offset++;
}
```

**FPS Reporting Functions**:
- `vnd_report_fps_stats()` (lines 599-629): Auto-output every 2s during streaming
- `vnd_print_perf_stats()` (lines 633-670): Detailed stats on "PERF" CDC command

**Integration Points**:
- `vnd_prepare_pair()`: Timing measurements added
- `USBD_VND_TxCpltCallback()`: FPS counting for A/B frames
- `VND_CMD_START_STREAM`: Reset/init FPS counters (line 2048-2060)
- `Vendor_Stream_Task()`: Auto FPS report every 2s (line 1684-1688)

**CRITICAL FIX**:
```c
#define VND_PAIR_BUFFERS 8  // MUST be 8, NOT 16!
// VND_PAIR_BUFFERS=16 caused BSS overflow → device fails to stream
```

### 2. CDC Command Handler (USB_DEVICE/App/usbd_cdc_if.c)
**PERF Command** (lines 286-290):
```c
if (*Len >= 4 && Buf[0] == 'P' && Buf[1] == 'E' && Buf[2] == 'R' && Buf[3] == 'F') {
    extern void vnd_print_perf_stats(void);
    vnd_print_perf_stats();
}
```

### 3. Host Script (HostTools/vendor_usb_start_and_read.py)
**Line 39 - Default ch-mode fixed**:
```python
parser.add_argument('--ch-mode', type=int, choices=[0,1,2], default=2,  # WAS: default=0
```
- Changed from A-only (0) to dual-channel (2)
- Critical for testing dual-channel performance

## Issues Found

### Issue #1: VND_PAIR_BUFFERS=16 Breaks Streaming
**Symptom**: Device receives START but sends no frames (A=0 B=0)  
**Root Cause**: BSS increased from 160KB to 204KB → memory corruption or init issues  
**Fix**: Reverted to VND_PAIR_BUFFERS=8  
**Result**: Streaming restored (48-95 pairs/sec)

### Issue #2: CDC FPS Reports Not Visible
**Symptom**: No FPS output on COM4 despite `vnd_report_fps_stats()` being called  
**Possible Causes**:
1. CDC TX buffer full/blocked during vendor streaming
2. `CDC_Transmit_HS()` failing silently (non-blocking call)
3. `cdc_logf()` conflict with vendor USB activity
4. Serial monitor timing/buffering issue

**Workarounds Tried**:
- ✗ "PERF" CDC command - no response
- ✗ STOP + PERF - no response
- ✗ CDC RESET - works but no logs

**Not Yet Tried**:
- Check CDC_Transmit_HS return code
- Add GPIO LED toggle in vnd_report_fps_stats() to confirm execution
- Use VND bulk IN for stats instead of CDC
- Increase CDC TX buffer size

### Issue #3: Low Throughput (~50-90 Hz instead of 200 Hz)
**Observed**: HOST_SUM shows 33-88 pairs/sec  
**Expected**: 200 pairs/sec (5ms period @ 200Hz)  
**Next Steps**: Analyze profiling data to find bottleneck:
- avg_prepare_us (target <1000us)
- avg_transmit_us (target <500us)
- pair_interval_ms (target 5ms)

## Test Results
```
Test 1 (after VND_PAIR_BUFFERS=8 fix):
[HOST_SUM] +1.0s A=48 (46.7/s) B=49 (47.6/s) STAT=0 timeouts=0 pipes=0
[HOST_SUM] +1.1s A=36 (33.4/s) B=36 (33.4/s) STAT=0 timeouts=0 pipes=0
[HOST_SUM] +1.1s A=95 (88.3/s) B=95 (88.3/s) STAT=0 timeouts=0 pipes=0
[HOST_SUM] +1.0s A=62 (61.9/s) B=62 (61.9/s) STAT=0 timeouts=0 pipes=0
```
**Analysis**: Dual-channel works but rate unstable (33-88 Hz)

## Build Info
```bash
$ arm-none-eabi-size Debug/BMI30.stm32h7.elf
   text    data     bss     dec     hex filename
 126036     680  160624  287340   4626c BMI30.stm32h7.elf
```

## How to Continue

### Priority 1: Fix CDC FPS Reporting
```bash
# Option A: Debug CDC output
1. Add LED toggle in vnd_report_fps_stats() to confirm execution
2. Check CDC_Transmit_HS() return code
3. Try sending FPS via VND bulk IN instead of CDC

# Option B: Alternative profiling output
1. Use vendor GET_STATUS to include perf_stats fields
2. Host script polls stats via ctrl transfer
3. Parse and display on host side
```

### Priority 2: Analyze Performance Bottleneck
Once FPS stats visible:
```
1. Check avg_prepare_us → ADC data copy overhead
2. Check avg_transmit_us → USB transmission time
3. Check pair_interval_ms → scheduling/timing issues
4. Identify if bottleneck is prepare/transmit/interval
```

### Priority 3: Optimize Based on Data
Likely scenarios:
```
If avg_prepare_us > 2000us:
  → Optimize ADC buffer copy (DMA?, memcpy optimization)
  
If avg_transmit_us > 1000us:
  → USB endpoint too slow (check HS vs FS mode)
  
If pair_interval_ms > 10ms:
  → Scheduler issue (vnd_next_pair_ms logic)
  → Buffer management bottleneck
```

## Test Commands

### Quick Status Check
```bash
py -3 HostTools\vendor_quick_status.py
```

### 10-Second Dual-Channel Test
```bash
py -3 HostTools\vendor_usb_start_and_read.py --window-sec 10 --pairs 10000 --ch-mode 2
```

### CDC PERF Command (currently not working)
```bash
py -3 -c "import serial,time; s=serial.Serial('COM4',115200); s.write(b'PERF\n'); time.sleep(0.5); print(s.read(2000).decode())"
```

### Manual USB Reset
```bash
py -3 -c "import usb.core; d=usb.core.find(idVendor=0xCAFE, idProduct=0x4001); d and d.reset()"
```

## Files Changed
- `USB_DEVICE/App/usb_vendor_app.c` - FPS profiling system
- `USB_DEVICE/App/usbd_cdc_if.c` - PERF command handler
- `HostTools/vendor_usb_start_and_read.py` - ch-mode default fix

## Known Working State
- **Commit**: (to be created)
- **Dual-channel**: ✅ Works
- **FPS profiling code**: ✅ Compiled
- **FPS output**: ❌ Not visible yet
- **Throughput**: ~50-90 Hz (below target 200 Hz)

## Next Session Goals
1. ✅ Make FPS stats visible (CDC or alternative method)
2. 📊 Collect 60s profiling run data
3. 🔍 Identify bottleneck (prepare/tx/interval)
4. ⚡ Implement targeted optimization
5. 🎯 Achieve sustained 200 Hz dual-channel streaming
