#include "gate.h"

static uint32_t elapsed(uint32_t now, uint32_t then) { return now - then; }
static void output(gate_t *g)
{
    g->gpio0 = g->armed && (g->occupied || (g->fault != GATE_OK && g->config.fault_high));
}

gate_config_t gate_defaults(void)
{
    return (gate_config_t){
        .range_start_mm = 100, .range_end_mm = 2000,
        .gap_min_mm = 600, .gap_max_mm = 1800,
        .trigger_delta_mm = 100, .release_delta_mm = 60,
        .on_delay_ms = 50, .off_delay_ms = 100, .clear_delay_ms = 2000,
        .no_echo_ms = 500, .calibration_ms = 3000,
        .calibration_spread_mm = 30, .rate_hz = 20,
        .sensitivity_milli = 750, .signal_quality_db = 20,
        .adaptation_ms = 60000, .fault_high = true
    };
}

bool gate_config_valid(const gate_config_t *c)
{
    return c && c->range_start_mm >= 80 && c->range_start_mm <= 400 &&
        c->range_end_mm >= 1800 && c->range_end_mm <= 3000 &&
        c->gap_min_mm >= 600 && c->gap_max_mm <= 1800 &&
        c->gap_min_mm < c->gap_max_mm && c->range_end_mm >= c->gap_max_mm &&
        c->release_delta_mm >= 10 && c->trigger_delta_mm > c->release_delta_mm &&
        c->trigger_delta_mm <= 400 && c->trigger_delta_mm < c->gap_min_mm - c->range_start_mm &&
        c->on_delay_ms <= 2000 && c->off_delay_ms <= 5000 &&
        c->clear_delay_ms >= 500 && c->clear_delay_ms <= 30000 &&
        c->no_echo_ms >= 200 && c->no_echo_ms <= 10000 &&
        c->calibration_ms >= 500 && c->calibration_ms <= 10000 &&
        c->calibration_spread_mm >= 5 && c->calibration_spread_mm <= 100 &&
        c->rate_hz >= 5 && c->rate_hz <= 30 &&
        c->sensitivity_milli >= 100 && c->sensitivity_milli <= 950 &&
        c->signal_quality_db >= 5 && c->signal_quality_db <= 30 &&
        c->adaptation_ms >= 10000 && c->adaptation_ms <= 3600000;
}

void gate_init(gate_t *g, const gate_config_t *c, uint16_t saved_gap, uint32_t now)
{
    *g = (gate_t){0};
    g->config = gate_config_valid(c) ? *c : gate_defaults();
    g->now_ms = g->last_frame_ms = g->last_echo_ms = now;
    if (saved_gap) gate_set_gap(g, saved_gap);
}

bool gate_set_config(gate_t *g, const gate_config_t *c)
{
    if (!gate_config_valid(c)) return false;
    uint16_t gap = gate_gap_mm(g);
    if (g->armed && (gap < c->gap_min_mm || gap > c->gap_max_mm)) return false;
    g->config = *c;
    g->on_pending = g->off_pending = g->clear_pending = false;
    g->calibration_pending = false;
    output(g);
    return true;
}

uint16_t gate_gap_mm(const gate_t *g) { return (uint16_t)((g->baseline_q16 + 32768) / 65536); }

bool gate_set_gap(gate_t *g, uint16_t gap)
{
    if (gap < g->config.gap_min_mm || gap > g->config.gap_max_mm) return false;
    g->baseline_q16 = (int32_t)gap * 65536;
    g->armed = true;
    g->calibrating = g->occupied = false;
    g->fault = GATE_OK;
    g->on_pending = g->off_pending = g->clear_pending = false;
    g->last_echo_ms = g->now_ms;
    output(g);
    return true;
}

void gate_calibrate(gate_t *g)
{
    g->armed = g->occupied = false;
    g->calibrating = true;
    g->baseline_q16 = 0;
    g->fault = GATE_OK;
    g->calibration_pending = false;
    g->on_pending = g->off_pending = g->clear_pending = false;
    output(g);
}

static void calibrate_frame(gate_t *g, uint16_t nearest)
{
    if (nearest < g->config.gap_min_mm || nearest > g->config.gap_max_mm) {
        g->calibration_pending = false;
        return;
    }
    uint16_t lo = nearest < g->calibration_min ? nearest : g->calibration_min;
    uint16_t hi = nearest > g->calibration_max ? nearest : g->calibration_max;
    if (!g->calibration_pending || hi - lo > g->config.calibration_spread_mm) {
        g->calibration_pending = true;
        g->calibration_since_ms = g->now_ms;
        g->calibration_sum = nearest;
        g->calibration_count = 1;
        g->calibration_min = g->calibration_max = nearest;
        return;
    }
    g->calibration_min = lo;
    g->calibration_max = hi;
    g->calibration_sum += nearest;
    g->calibration_count++;
    if (elapsed(g->now_ms, g->calibration_since_ms) >= g->config.calibration_ms)
        gate_set_gap(g, (uint16_t)(g->calibration_sum / g->calibration_count));
}

void gate_tick(gate_t *g, uint32_t now)
{
    g->now_ms = now;
    if (g->armed && elapsed(now, g->last_frame_ms) >= g->config.no_echo_ms) {
        g->fault = GATE_STALE;
        g->on_pending = g->off_pending = g->clear_pending = false;
    }
    output(g);
}

void gate_frame(gate_t *g, uint32_t now, bool valid, const uint16_t *peaks, size_t count)
{
    uint32_t dt = elapsed(now, g->last_frame_ms);
    g->frame_interval_ms = g->have_frame ? dt : 0;
    if (valid) g->frame_count++;
    uint32_t max_interval = 3 * (1000 / g->config.rate_hz);
    g->now_ms = now;
    if (!g->have_frame || dt > max_interval) {
        g->on_pending = g->off_pending = g->clear_pending = false;
        g->calibration_pending = false;
        dt = 0;
    }
    g->have_frame = true;
    g->last_frame_ms = now;
    g->nearest_mm = 0;
    if (!valid || (count && !peaks) || count > GATE_MAX_PEAKS) {
        g->fault = GATE_SENSOR_ERROR;
        g->on_pending = g->off_pending = g->clear_pending = false;
        g->calibration_pending = false;
        output(g);
        return;
    }
    for (size_t i = 0; i < count; i++) {
        if (peaks[i] >= g->config.range_start_mm && peaks[i] <= g->config.range_end_mm &&
            (!g->nearest_mm || peaks[i] < g->nearest_mm)) g->nearest_mm = peaks[i];
    }
    if (!g->nearest_mm) {
        g->on_pending = g->off_pending = g->clear_pending = false;
        g->calibration_pending = false;
        if (elapsed(now, g->last_echo_ms) >= g->config.no_echo_ms) g->fault = GATE_NO_ECHO;
        output(g);
        return;
    }
    g->have_echo = true;
    g->last_echo_ms = now;
    /* An error does not become CLEAR from one frame. It needs the same
     * continuous empty-passage confirmation as release of an obstacle. */
    if (g->calibrating) {
        g->fault = GATE_OK;
        calibrate_frame(g, g->nearest_mm);
        output(g);
        return;
    }
    if (!g->armed) { g->fault = GATE_OK; output(g); return; }
    int32_t delta_q16 = g->baseline_q16 - (int32_t)g->nearest_mm * 65536;
    if (delta_q16 >= (int32_t)g->config.trigger_delta_mm * 65536) {
        g->off_pending = g->clear_pending = false;
        if (!g->on_pending) { g->on_pending = true; g->on_since_ms = now; }
        if (elapsed(now, g->on_since_ms) >= g->config.on_delay_ms) {
            g->occupied = true;
            g->fault = GATE_OK;
        }
    } else if (delta_q16 <= (int32_t)g->config.release_delta_mm * 65536 &&
               ((!g->occupied && g->fault == GATE_OK) ||
                delta_q16 >= -(int32_t)g->config.trigger_delta_mm * 65536) &&
               g->nearest_mm >= g->config.gap_min_mm &&
               g->nearest_mm <= g->config.gap_max_mm) {
        g->on_pending = false;
        if (!g->off_pending) { g->off_pending = true; g->off_since_ms = now; }
        if (elapsed(now, g->off_since_ms) >= g->config.off_delay_ms) {
            g->occupied = false;
            g->fault = GATE_OK;
        }
        if (!g->occupied && g->fault == GATE_OK) {
            if (!g->clear_pending) { g->clear_pending = true; g->clear_since_ms = now; }
            if (elapsed(now, g->clear_since_ms) >= g->config.clear_delay_ms) {
                int64_t change = (int64_t)((int32_t)g->nearest_mm * 65536 - g->baseline_q16) * dt;
                g->baseline_q16 += (int32_t)(change / (g->config.adaptation_ms + dt));
            }
        }
    } else {
        /* Hysteresis or unrelated far echo: preserve state and background.
         * An occupied passage clears only when the original target returns. */
        g->on_pending = g->off_pending = g->clear_pending = false;
    }
    output(g);
}
