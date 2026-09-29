#ifndef RX_PHASE_DETECTOR_H
#define RX_PHASE_DETECTOR_H
#include <stdint.h>

#define RX_PHASE_MAGIC 0x31505852u /* RXP1 */
#define RX_PHASE_STATUS_SIZE 112u
#define RX_PHASE_UNKNOWN 255u
#define RX_PHASE_COLLECTING 1u
#define RX_PHASE_WEAK 2u
#define RX_PHASE_AMBIGUOUS 4u
#define RX_PHASE_CLIPPED 8u
#define RX_PHASE_STALE 16u
#define RX_PHASE_UNSUPPORTED 32u
#define RX_PHASE_BLOCKS 3u
#define RX_PHASE_BLOCK_MS 500u

typedef struct {
    uint8_t phase; /* 0=0 degrees, 1=180 degrees, 255=no decision yet */
    uint8_t valid;
    uint8_t confidence; /* 0..100 quality score, not a probability */
    uint8_t flags;
    int16_t angle_cdeg; /* measured angle relative to fixed zero axis */
    uint16_t coherence_permille;
    uint16_t amplitude;
    uint16_t even_count;
    uint16_t odd_count;
    uint16_t clipped_permille;
    uint32_t last_valid_ms;
    uint32_t changes;
    uint16_t roi_clipped_permille;
    uint16_t roi_min;
    uint16_t roi_max;
    uint16_t roi_span;
} rx_phase_channel_status_t;

typedef struct {
    uint32_t magic;
    uint8_t version;
    uint8_t channels;
    uint16_t size;
    uint32_t seq_begin;
    uint32_t uptime_ms;
    uint32_t capture_generation;
    uint32_t frames;
    uint32_t drops;
    uint32_t max_service_cycles;
    uint32_t total_service_cycles;
    uint32_t last_frame_ms;
    rx_phase_channel_status_t channel[2];
    uint32_t enabled;
    uint32_t seq_end;
} rx_phase_status_t;

typedef struct {
    float sum[2][3][2]; /* parity, window, I/Q of normalized frame vector */
    float amplitude_sum;
    uint32_t clipped;
    uint16_t count[2];
    uint16_t weak;
    uint32_t roi_clipped;
    uint16_t roi_min, roi_max, seen;
} rx_phase_block_t;

typedef struct {
    uint16_t clipped, roi_clipped, roi_min, roi_max;
} rx_phase_sample_stats_t;

typedef struct {
    rx_phase_block_t blocks[2][RX_PHASE_BLOCKS];
    rx_phase_block_t pending[2];
    rx_phase_channel_status_t channel[2];
    uint32_t block_start_ms;
    uint8_t next_block;
    uint8_t completed;
} rx_phase_detector_t;

void rx_phase_detector_reset(rx_phase_detector_t *d, uint32_t now_ms);
void rx_phase_project_chunk(const uint16_t samples[2][600], uint16_t start,
                            uint16_t stop, int32_t accum[2][6]);
void rx_phase_detector_add(rx_phase_detector_t *d, const int32_t accum[2][6],
                           uint8_t parity, const rx_phase_sample_stats_t stats[2]);
uint8_t rx_phase_detector_tick(rx_phase_detector_t *d, uint32_t now_ms);
#endif
