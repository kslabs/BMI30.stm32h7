#include "gate.h"
#include "protocol.h"
#include <string.h>
#include "storage.h"
#include "main.h"

volatile unsigned test_failure;
volatile unsigned test_count;
volatile unsigned test_done;
static gate_t g;
static uint32_t clock_ms;
static char reply[GATE_REPLY_SIZE];
static gate_protocol_t protocol;
static int flash_budget = 100;
uint32_t test_flash_status;
void test_flash_clear(uint32_t flags)
{
    test_flash_status ^= flags & FLASH_FLAG_PEMPTY;
    test_flash_status &= ~(flags & FLASH_FLAG_ALL_ERRORS);
}
int HAL_FLASH_Unlock(void) { return HAL_OK; }
int HAL_FLASH_Lock(void) { return HAL_OK; }
int HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *erase, uint32_t *error)
{
    *error = 0;
    if (test_flash_status & FLASH_FLAG_PEMPTY) return HAL_ERROR;
    uint8_t *page = (uint8_t *)(FLASH_BASE + erase->Page * FLASH_PAGE_SIZE);
    for (unsigned i = 0; i < FLASH_PAGE_SIZE; i++) page[i] = 0xFF;
    return HAL_OK;
}
int HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t data)
{
    (void)type;
    if (flash_budget-- <= 0) return HAL_ERROR;
    *(uint64_t *)address = data;
    return HAL_OK;
}
static bool hook_apply(void *context, const gate_config_t *config)
{
    (void)context;
    return config->rate_hz != 30;
}
static void command(const char *text)
{
    for (const char *p = text; *p; p++) gate_protocol_byte(&protocol, (uint8_t)*p, reply, sizeof(reply));
}
static size_t hook_diagnostics(void *context, char *buffer, size_t capacity)
{
    (void)context;
    const char *text = "{\"ok\":true,\"measure_ms\":12}\n";
    size_t size = strlen(text);
    if (capacity <= size) return 0;
    memcpy(buffer, text, size + 1);
    return size;
}

#define CHECK(x, id) do { test_count++; if (!(x) && !test_failure) test_failure = id; } while (0)
static void init(uint16_t gap) { clock_ms = 0; gate_config_t c = gate_defaults(); gate_init(&g, &c, gap, clock_ms); }
static void frame(uint16_t distance)
{
    clock_ms += 50;
    gate_frame(&g, clock_ms, true, distance ? &distance : 0, distance ? 1 : 0);
}
static void fast_frame(uint16_t distance)
{
    clock_ms += 33;
    gate_frame(&g, clock_ms, true, &distance, 1);
}

void test_entry(void)
{
    init(1200);
    CHECK(g.armed && !g.gpio0, 1);
    frame(1200);
    frame(700);
    CHECK(!g.gpio0, 2);
    frame(700);
    CHECK(g.gpio0 && g.occupied, 3);
    uint16_t frozen = gate_gap_mm(&g);
    for (unsigned i = 0; i < 24000; i++) frame(700); /* 20 minutes standing */
    CHECK(g.gpio0 && gate_gap_mm(&g) == frozen, 4);
    frame(1200);
    frame(1200);
    CHECK(g.gpio0, 5);
    frame(1200);
    CHECK(!g.gpio0 && !g.occupied, 6);

    init(1200);
    frame(1200);
    frame(700);
    frame(1200); /* single-frame interference must not trigger */
    CHECK(!g.gpio0, 7);
    uint16_t peaks[] = {1200, 700, 1900};
    clock_ms += 50; gate_frame(&g, clock_ms, true, peaks, 3);
    clock_ms += 50; gate_frame(&g, clock_ms, true, peaks, 3);
    CHECK(g.gpio0, 8); /* foreground wins even with strong background */
    for (unsigned i = 0; i < 100; i++) frame(1130); /* hysteresis */
    CHECK(g.gpio0 && gate_gap_mm(&g) == 1200, 9);

    for (unsigned i = 0; i < 20; i++) frame(0);
    CHECK(g.gpio0 && g.occupied && g.fault == GATE_NO_ECHO, 10);
    frame(1200); frame(1200); frame(1200);
    CHECK(!g.gpio0 && g.fault == GATE_OK, 11);

    init(1200); frame(1200);
    gate_tick(&g, clock_ms + 500);
    CHECK(g.gpio0 && g.fault == GATE_STALE, 12);
    clock_ms += 500; frame(1200);
    CHECK(g.gpio0, 13);
    frame(1200); frame(1200);
    CHECK(!g.gpio0 && g.fault == GATE_OK, 14);
    gate_frame(&g, clock_ms + 50, false, 0, 0);
    CHECK(g.gpio0 && g.fault == GATE_SENSOR_ERROR, 15);

    init(1200);
    for (unsigned i = 0; i < 1200; i++) frame(1240); /* one adaptation constant */
    CHECK(gate_gap_mm(&g) > 1215 && gate_gap_mm(&g) < 1230 && !g.gpio0, 16);
    init(1200);
    for (unsigned i = 0; i < 1200; i++) frame(1170);
    CHECK(gate_gap_mm(&g) < 1190 && gate_gap_mm(&g) > 1175 && !g.gpio0, 17);

    init(0);
    CHECK(!g.armed && !g.gpio0, 18);
    frame(900);
    CHECK(!g.armed, 19); /* cannot silently learn someone as background */
    gate_calibrate(&g);
    for (unsigned i = 0; i < 70; i++) frame(300);
    CHECK(!g.armed && !g.gpio0, 20);
    for (unsigned i = 0; i < 30; i++) frame(900);
    frame(1200); /* unstable installation resets calibration */
    for (unsigned i = 0; i < 35; i++) frame(900);
    CHECK(!g.armed, 21);
    for (unsigned i = 0; i < 30; i++) frame(900);
    CHECK(g.armed && gate_gap_mm(&g) == 900 && !g.gpio0, 22);

    init(600); frame(600); frame(200); frame(200);
    CHECK(g.gpio0, 23);
    init(1800); frame(1800); frame(1300); frame(1300);
    CHECK(g.gpio0, 24);
    CHECK(!gate_set_gap(&g, 599) && !gate_set_gap(&g, 1801), 25);
    gate_config_t invalid = gate_defaults();
    invalid.release_delta_mm = invalid.trigger_delta_mm;
    CHECK(!gate_set_config(&g, &invalid), 26);

    init(1200);
    clock_ms = 0xFFFFFF80u;
    g.last_frame_ms = g.last_echo_ms = clock_ms;
    frame(1200); frame(700); frame(700);
    CHECK(g.gpio0, 27); /* unsigned millisecond rollover */

    init(1200); frame(1200); frame(700);
    clock_ms += 1000; frame(700);
    CHECK(!g.occupied, 28); /* gaps are not continuous evidence */
    frame(700);
    CHECK(g.occupied, 29);

    init(1200);
    gate_protocol_init(&protocol, &g, (gate_hooks_t){.apply = hook_apply});
    command("GET\n");
    CHECK(strstr(reply, "BMI30_XM125_GATE") && strstr(reply, "\"gap_mm\":1200"), 30);
    command("SET trigger_delta_mm 140\n");
    CHECK(g.config.trigger_delta_mm == 140 && strstr(reply, "\"ok\":true"), 31);
    command("SET release_delta_mm 150\n");
    CHECK(g.config.release_delta_mm == 60 && strstr(reply, "\"ok\":false"), 32);
    command("SET on_delay_ms -1\n");
    CHECK(g.config.on_delay_ms == 50 && strstr(reply, "\"ok\":false"), 33);
    command("SET on_delay_ms 4294967296\n");
    CHECK(g.config.on_delay_ms == 50 && strstr(reply, "\"ok\":false"), 34);
    command("SET rate_hz 30\n");
    CHECK(g.config.rate_hz == 20 && strstr(reply, "\"ok\":false"), 35);
    command("GAP 599\n");
    CHECK(gate_gap_mm(&g) == 1200 && strstr(reply, "\"ok\":false"), 36);
    command("GAP 1600\n");
    CHECK(gate_gap_mm(&g) == 1600 && !g.gpio0, 37);
    command("STREAM 1\n");
    CHECK(protocol.stream, 38);
    command("STREAM 2\n");
    CHECK(protocol.stream && strstr(reply, "\"ok\":false"), 39);
    command("SAVE\n");
    CHECK(strstr(reply, "\"error\":\"storage\""), 40);
    command("CALIBRATE\r\n");
    CHECK(g.calibrating && !g.armed && !g.gpio0, 41);
    command("SAVE\n");
    CHECK(strstr(reply, "not_calibrated"), 42);
    command("GAP 1200 extra\n");
    CHECK(!g.armed && strstr(reply, "\"ok\":false"), 43);
    command("SET trigger_delta_mm 140 extra\n");
    CHECK(strstr(reply, "syntax"), 44);
    for (unsigned i = 0; i < GATE_LINE_SIZE + 5; i++)
        gate_protocol_byte(&protocol, 'A', reply, sizeof(reply));
    command("GAP 1200\n");
    CHECK(!g.armed && strstr(reply, "line_length"), 45);
    CHECK(gate_status(&protocol, reply, 5) == 0 && reply[0] == 0, 46);
    command("CONFIG 80 40 50 100 2000 120000 15 500 1 750 20\n");
    CHECK(g.config.trigger_delta_mm == 80 && g.config.release_delta_mm == 40 &&
          g.config.adaptation_ms == 120000 && g.config.rate_hz == 15, 47);
    command("CONFIG 30 40 50 100 2000 120000 15 500 1 750 20\n");
    CHECK(g.config.trigger_delta_mm == 80 && g.config.release_delta_mm == 40 && strstr(reply, "configuration"), 48);
    command("CONFIG 100 60 50 100 2000 60000 20 500\n");
    CHECK(g.config.rate_hz == 15 && strstr(reply, "syntax"), 49);

    gate_config_t saved;
    uint16_t saved_gap;
    FLASH_EraseInitTypeDef erase = {.Page = 62}; uint32_t error;
    HAL_FLASHEx_Erase(&erase, &error); erase.Page = 63; HAL_FLASHEx_Erase(&erase, &error);
    CHECK(!gate_storage_load(0, &saved, &saved_gap), 50);
    gate_config_t c = gate_defaults();
    CHECK(!gate_storage_save(0, &c, 599), 51);
    CHECK(gate_storage_save(0, &c, 1200) && gate_storage_load(0, &saved, &saved_gap) && saved_gap == 1200, 52);
    c.adaptation_ms = 120000;
    CHECK(gate_storage_save(0, &c, 1000) && gate_storage_load(0, &saved, &saved_gap) &&
          saved_gap == 1000 && saved.adaptation_ms == 120000, 53);
    flash_budget = 2; c.rate_hz = 15;
    CHECK(!gate_storage_save(0, &c, 1300) && gate_storage_load(0, &saved, &saved_gap) &&
          saved_gap == 1000 && saved.rate_hz == 20, 54); /* interrupted write */
    flash_budget = 100;
    CHECK(gate_storage_save(0, &c, 1300) && gate_storage_load(0, &saved, &saved_gap) && saved_gap == 1300, 55);
    *(uint32_t *)GATE_FLASH_A = 0; /* damaged newest record, fallback to previous */
    CHECK(gate_storage_load(0, &saved, &saved_gap) && saved_gap == 1000, 56);
    *(uint32_t *)GATE_FLASH_B = 0;
    CHECK(!gate_storage_load(0, &saved, &saved_gap), 57);
    test_flash_status = FLASH_FLAG_PEMPTY;
    CHECK(gate_storage_save(0, &c, 1200) && !test_flash_status, 58);
    gate_storage_prepare();
    CHECK(!test_flash_status && gate_storage_save(0, &c, 1200), 59);
    init(1200); frame(700); frame(700);
    for (unsigned i = 0; i < 100; i++) frame(1500);
    CHECK(g.occupied && g.gpio0 && gate_gap_mm(&g) == 1200, 60);
    frame(1200); frame(1200); frame(1200);
    CHECK(!g.occupied && !g.gpio0, 61);
    init(1200);
    gate_config_t timed = gate_defaults(); timed.off_delay_ms = 300;
    gate_set_config(&g, &timed);
    frame(700); frame(700);
    for (unsigned i = 0; i < 4; i++) frame(1200);
    CHECK(g.occupied && g.gpio0, 62); /* brief background echo during entry */
    frame(700);
    for (unsigned i = 0; i < 7; i++) frame(1200);
    CHECK(!g.occupied && !g.gpio0, 63);
    /* Fast passage preset: two consecutive foreground measurements, without
     * accepting a single-frame echo or shortening the occupied-state hold. */
    init(1177);
    gate_config_t fast = gate_defaults();
    fast.rate_hz = 30; fast.on_delay_ms = 20; fast.off_delay_ms = 300;
    CHECK(gate_set_config(&g, &fast), 64);
    fast_frame(620);
    CHECK(!g.gpio0, 65);
    fast_frame(1177);
    CHECK(!g.gpio0, 66);
    fast_frame(620); fast_frame(620);
    CHECK(g.gpio0 && g.occupied && clock_ms == 132, 67);
    for (unsigned i = 0; i < 100; i++) {
        fast_frame(620);
        fast_frame(1177); fast_frame(1177); fast_frame(1177);
    }
    CHECK(g.gpio0 && g.occupied && gate_gap_mm(&g) == 1177, 68);
    fast_frame(620);
    for (unsigned i = 0; i < 10; i++) fast_frame(1177);
    CHECK(g.gpio0 && g.occupied, 69); /* 297 ms since first clear frame */
    fast_frame(1177);
    CHECK(!g.gpio0 && !g.occupied, 70);
    CHECK(gate_storage_save(0, &fast, 1177) && gate_storage_load(0, &saved, &saved_gap) &&
          saved_gap == 1177 && saved.rate_hz == 30 && saved.on_delay_ms == 20 &&
          saved.off_delay_ms == 300, 71);
    gate_protocol_init(&protocol, &g, (gate_hooks_t){.diagnostics = hook_diagnostics});
    command("RADAR\n");
    CHECK(strstr(reply, "\"measure_ms\":12") && g.config.rate_hz == 30, 72);
    command("RADAR extra\n");
    CHECK(strstr(reply, "\"ok\":false"), 73);
    gate_protocol_init(&protocol, &g, (gate_hooks_t){0});
    command("RADAR\n");
    CHECK(strstr(reply, "diagnostics_unavailable"), 74);
    /* Early antenna enable: smaller range delta, longer hold at the fringe. */
    init(1177);
    fast.trigger_delta_mm = 60; fast.release_delta_mm = 30; fast.off_delay_ms = 700;
    CHECK(gate_set_config(&g, &fast), 75);
    fast_frame(1130); fast_frame(1130);
    CHECK(!g.gpio0, 76); /* antenna-range jitter alone must not enable */
    fast_frame(1110);
    CHECK(!g.gpio0, 77);
    fast_frame(1110);
    CHECK(g.gpio0 && g.occupied, 78);
    for (unsigned i = 0; i < 22; i++) fast_frame(1177);
    CHECK(g.gpio0 && g.occupied, 79); /* 693 ms dropout stays enabled */
    for (unsigned i = 0; i < 100; i++) fast_frame(1110);
    CHECK(g.gpio0 && gate_gap_mm(&g) == 1177, 80);
    for (unsigned i = 0; i < 24; i++) fast_frame(1177);
    CHECK(!g.gpio0 && !g.occupied, 81);
    CHECK(gate_storage_save(0, &fast, 1177) && gate_storage_load(0, &saved, &saved_gap) &&
          saved.trigger_delta_mm == 60 && saved.release_delta_mm == 30 &&
          saved.off_delay_ms == 700, 82);
    init(1177);
    fast.off_delay_ms = 1000;
    CHECK(gate_set_config(&g, &fast), 83);
    fast_frame(1110); fast_frame(1110);
    CHECK(g.gpio0 && g.occupied, 84);
    for (unsigned i = 0; i < 31; i++) fast_frame(1177);
    CHECK(g.gpio0 && g.occupied && gate_gap_mm(&g) == 1177, 85); /* 990 ms */
    fast_frame(1177);
    CHECK(!g.gpio0 && !g.occupied, 86); /* 1023 ms */
    CHECK(gate_storage_save(0, &fast, 1177) && gate_storage_load(0, &saved, &saved_gap) &&
          saved.off_delay_ms == 1000 && saved.trigger_delta_mm == 60, 87);
    /* Three-minute background time constant: track a small empty-passage
     * drift gradually, then freeze the exact baseline for a standing person. */
    init(1200);
    fast.adaptation_ms = 180000;
    CHECK(gate_set_config(&g, &fast), 88);
    for (unsigned i = 0; i < 5455; i++) fast_frame(1180);
    CHECK(!g.gpio0 && !g.occupied && gate_gap_mm(&g) >= 1187 &&
          gate_gap_mm(&g) <= 1189, 89); /* about 63% of a 20-mm shift */
    fast_frame(700); fast_frame(700);
    CHECK(g.gpio0 && g.occupied, 90);
    int32_t frozen_baseline = g.baseline_q16;
    for (unsigned i = 0; i < 5455; i++) fast_frame(700);
    CHECK(g.gpio0 && g.occupied && g.baseline_q16 == frozen_baseline &&
          g.fault == GATE_OK, 91);
    CHECK(gate_storage_save(0, &fast, gate_gap_mm(&g)) &&
          gate_storage_load(0, &saved, &saved_gap) &&
          saved.adaptation_ms == 180000 && saved.off_delay_ms == 1000, 92);
    test_done = 1;
    for (;;) __asm__ volatile("nop");
}
