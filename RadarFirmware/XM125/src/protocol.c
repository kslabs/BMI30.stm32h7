#include "protocol.h"
#include <stddef.h>
#include <string.h>

typedef struct { char *data; size_t capacity, used; bool failed; } writer_t;
static void put(writer_t *w, const char *text)
{
    while (*text) {
        if (w->used + 1 >= w->capacity) { w->failed = true; return; }
        w->data[w->used++] = *text++;
    }
}
static void number(writer_t *w, uint32_t n)
{
    char buf[11]; unsigned pos = 10; buf[10] = 0;
    do { buf[--pos] = (char)('0' + n % 10); n /= 10; } while (n);
    put(w, buf + pos);
}
static void field(writer_t *w, const char *key, uint32_t value)
{
    put(w, ",\""); put(w, key); put(w, "\":"); number(w, value);
}
static size_t finish(writer_t *w)
{
    put(w, "}\n");
    if (!w->capacity) return 0;
    w->data[w->failed ? 0 : w->used] = 0;
    return w->failed ? 0 : w->used;
}
static size_t error(char *reply, size_t cap, const char *code)
{
    writer_t w = {reply, cap, 0, false};
    put(&w, "{\"ok\":false,\"error\":\""); put(&w, code); put(&w, "\"");
    return finish(&w);
}
static size_t ok(char *reply, size_t cap)
{
    writer_t w = {reply, cap, 0, false}; put(&w, "{\"ok\":true"); return finish(&w);
}

enum { U16, U32, BOOL };
typedef struct { const char *name; size_t offset; unsigned type; } setting_t;
#define SETTING(name, type) { #name, offsetof(gate_config_t, name), type }
static const setting_t settings[] = {
    SETTING(range_start_mm, U16), SETTING(range_end_mm, U16),
    SETTING(gap_min_mm, U16), SETTING(gap_max_mm, U16),
    SETTING(trigger_delta_mm, U16), SETTING(release_delta_mm, U16),
    SETTING(on_delay_ms, U16), SETTING(off_delay_ms, U16),
    SETTING(clear_delay_ms, U16), SETTING(no_echo_ms, U16),
    SETTING(calibration_ms, U16), SETTING(calibration_spread_mm, U16),
    SETTING(rate_hz, U16), SETTING(adaptation_ms, U32), SETTING(fault_high, BOOL),
    SETTING(sensitivity_milli, U16), SETTING(signal_quality_db, U16)
};

static uint32_t setting_read(const gate_config_t *c, const setting_t *s)
{
    const void *address = (const char *)c + s->offset;
    if (s->type == U16) return *(const uint16_t *)address;
    if (s->type == U32) return *(const uint32_t *)address;
    return *(const bool *)address;
}

size_t gate_status(const gate_protocol_t *p, char *reply, size_t cap)
{
    const gate_t *g = p->gate;
    writer_t w = {reply, cap, 0, false};
    put(&w, "{\"ok\":true,\"device\":\"BMI30_XM125_GATE\",\"firmware\":\"" GATE_VERSION "\","
            "\"gpio0_mode\":\"open_drain\"");
    field(&w, "time_ms", g->now_ms); field(&w, "armed", g->armed);
    field(&w, "calibrating", g->calibrating); field(&w, "gap_mm", gate_gap_mm(g));
    field(&w, "nearest_mm", g->nearest_mm); field(&w, "occupied", g->occupied);
    field(&w, "gpio0", g->gpio0); field(&w, "fault", g->fault);
    field(&w, "stream", p->stream);
    field(&w, "frame_count", g->frame_count); field(&w, "frame_interval_ms", g->frame_interval_ms);
    put(&w, ",\"config\":{\"schema\":1");
    for (size_t i = 0; i < sizeof(settings) / sizeof(settings[0]); i++)
        field(&w, settings[i].name, setting_read(&g->config, &settings[i]));
    put(&w, "}");
    return finish(&w);
}

static char *token(char **cursor)
{
    while (**cursor == ' ' || **cursor == '\t') (*cursor)++;
    if (!**cursor) return 0;
    char *begin = *cursor;
    while (**cursor && **cursor != ' ' && **cursor != '\t') (*cursor)++;
    if (**cursor) *(*cursor)++ = 0;
    return begin;
}
static bool parse_number(const char *s, uint32_t *value)
{
    if (!s || !*s) return false;
    uint32_t v = 0;
    while (*s) {
        if (*s < '0' || *s > '9') return false;
        uint32_t digit = (uint32_t)(*s++ - '0');
        if (v > (UINT32_MAX - digit) / 10) return false;
        v = v * 10 + digit;
    }
    *value = v;
    return true;
}
static bool apply(gate_protocol_t *p, const gate_config_t *c)
{
    uint16_t gap = gate_gap_mm(p->gate);
    if (!gate_config_valid(c) || (p->gate->armed && (gap < c->gap_min_mm || gap > c->gap_max_mm)))
        return false;
    if (p->hooks.apply && !p->hooks.apply(p->hooks.context, c)) return false;
    return gate_set_config(p->gate, c);
}

void gate_protocol_init(gate_protocol_t *p, gate_t *g, gate_hooks_t hooks)
{
    *p = (gate_protocol_t){.gate = g, .hooks = hooks};
}

size_t gate_command(gate_protocol_t *p, char *line, char *reply, size_t cap)
{
    char *cursor = line;
    char *cmd = token(&cursor);
    if (cmd && !strcmp(cmd, "CONFIG")) {
        uint32_t n[11];
        for (unsigned i = 0; i < 11; i++)
            if (!parse_number(token(&cursor), &n[i])) return error(reply, cap, "syntax");
        if (token(&cursor)) return error(reply, cap, "syntax");
        for (unsigned i = 0; i < 11; i++)
            if (i != 5 && n[i] > UINT16_MAX) return error(reply, cap, "value");
        if (n[8] > 1) return error(reply, cap, "value");
        gate_config_t c = p->gate->config;
        c.trigger_delta_mm = (uint16_t)n[0]; c.release_delta_mm = (uint16_t)n[1];
        c.on_delay_ms = (uint16_t)n[2]; c.off_delay_ms = (uint16_t)n[3];
        c.clear_delay_ms = (uint16_t)n[4]; c.adaptation_ms = n[5];
        c.rate_hz = (uint16_t)n[6]; c.no_echo_ms = (uint16_t)n[7]; c.fault_high = n[8] != 0;
        c.sensitivity_milli = (uint16_t)n[9]; c.signal_quality_db = (uint16_t)n[10];
        if (!apply(p, &c)) return error(reply, cap, "configuration");
        return gate_status(p, reply, cap);
    }
    char *arg = token(&cursor), *value = token(&cursor);
    if (!cmd || token(&cursor)) return error(reply, cap, "syntax");
    if ((!strcmp(cmd, "GET") || !strcmp(cmd, "ID")) && !arg)
        return gate_status(p, reply, cap);
    if (!strcmp(cmd, "RADAR") && !arg) {
        if (!p->hooks.diagnostics) return error(reply, cap, "diagnostics_unavailable");
        return p->hooks.diagnostics(p->hooks.context, reply, cap);
    }
    if (!strcmp(cmd, "SET") && arg && value) {
        uint32_t n;
        if (!parse_number(value, &n)) return error(reply, cap, "value");
        gate_config_t c = p->gate->config;
        bool found = false;
        for (size_t i = 0; i < sizeof(settings) / sizeof(settings[0]); i++) {
            const setting_t *s = &settings[i];
            if (strcmp(s->name, arg)) continue;
            found = true;
            void *address = (char *)&c + s->offset;
            if (s->type == U16 && n <= UINT16_MAX) *(uint16_t *)address = (uint16_t)n;
            else if (s->type == U32) *(uint32_t *)address = n;
            else if (s->type == BOOL && n <= 1) *(bool *)address = n != 0;
            else return error(reply, cap, "value");
        }
        if (!found) return error(reply, cap, "setting");
        if (!apply(p, &c)) return error(reply, cap, "configuration");
        return gate_status(p, reply, cap);
    }
    if (!strcmp(cmd, "GAP") && arg && !value) {
        uint32_t n;
        if (!parse_number(arg, &n) || n > UINT16_MAX || !gate_set_gap(p->gate, (uint16_t)n))
            return error(reply, cap, "gap_range");
        return gate_status(p, reply, cap);
    }
    if (!strcmp(cmd, "CALIBRATE") && !arg) {
        gate_calibrate(p->gate);
        return gate_status(p, reply, cap);
    }
    if (!strcmp(cmd, "SAVE") && !arg) {
        if (!p->gate->armed || p->gate->calibrating) return error(reply, cap, "not_calibrated");
        if (!p->hooks.save || !p->hooks.save(p->hooks.context, &p->gate->config, gate_gap_mm(p->gate)))
            return error(reply, cap, "storage");
        return ok(reply, cap);
    }
    if (!strcmp(cmd, "LOAD") && !arg) {
        gate_config_t c; uint16_t gap;
        if (!p->hooks.load || !p->hooks.load(p->hooks.context, &c, &gap))
            return error(reply, cap, "storage");
        if (!gate_config_valid(&c) || gap < c.gap_min_mm || gap > c.gap_max_mm)
            return error(reply, cap, "configuration");
        if (p->hooks.apply && !p->hooks.apply(p->hooks.context, &c))
            return error(reply, cap, "configuration");
        gate_init(p->gate, &c, gap, p->gate->now_ms);
        return gate_status(p, reply, cap);
    }
    if (!strcmp(cmd, "STREAM") && arg && !value) {
        uint32_t n;
        if (!parse_number(arg, &n) || n > 1) return error(reply, cap, "value");
        p->stream = n != 0;
        return gate_status(p, reply, cap);
    }
    return error(reply, cap, "command");
}

size_t gate_protocol_byte(gate_protocol_t *p, uint8_t byte, char *reply, size_t cap)
{
    if (byte == '\r') return 0;
    if (byte == '\n') {
        bool overflow = p->overflow;
        p->overflow = false;
        p->line[p->used] = 0;
        p->used = 0;
        return overflow ? error(reply, cap, "line_length") : gate_command(p, p->line, reply, cap);
    }
    if (p->overflow) return 0;
    if ((byte < 32 && byte != '\t') || byte > 126 || p->used >= sizeof(p->line) - 1) {
        p->overflow = true;
        return 0;
    }
    p->line[p->used++] = (char)byte;
    return 0;
}
