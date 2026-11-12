#pragma once
#include <stdint.h>
#include <stdbool.h>

#define RING_CAP 1024

typedef struct {
    uint32_t seq;
    uint32_t timestamp_ms;
    uint16_t samples;
    uint8_t  adc_id;
    uint8_t  flags;
    uint16_t data_off; // offset in payload arena
    uint16_t data_len; // bytes
} FrameMeta;

typedef struct {
    FrameMeta meta[RING_CAP];
    uint8_t  *arena; // contiguous sample storage
    uint32_t arena_size;
    uint32_t head, tail;
    uint32_t drops;
} FrameRing;

bool ring_init(FrameRing *r, uint32_t arena_size);
void ring_free(FrameRing *r);
bool ring_push(FrameRing *r, const FrameMeta *m, const uint8_t *data);
bool ring_pop(FrameRing *r, FrameMeta *m, uint8_t *out);
uint32_t ring_count(const FrameRing *r);
