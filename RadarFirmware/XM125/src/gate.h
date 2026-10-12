#ifndef BMI30_RADAR_GATE_H
#define BMI30_RADAR_GATE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define GATE_VERSION "0.1.2"
#define GATE_MAX_PEAKS 10

typedef struct {
    uint16_t range_start_mm;
    uint16_t range_end_mm;
    uint16_t gap_min_mm;
    uint16_t gap_max_mm;
    uint16_t trigger_delta_mm;
    uint16_t release_delta_mm;
    uint16_t on_delay_ms;
    uint16_t off_delay_ms;
    uint16_t clear_delay_ms;
    uint16_t no_echo_ms;
    uint16_t calibration_ms;
    uint16_t calibration_spread_mm;
    uint16_t rate_hz;
    uint16_t sensitivity_milli;
    uint16_t signal_quality_db;
    uint32_t adaptation_ms;
    bool fault_high;
} gate_config_t;

typedef enum {
    GATE_OK = 0,
    GATE_NO_ECHO = 1,
    GATE_SENSOR_ERROR = 2,
    GATE_STALE = 3
} gate_fault_t;

typedef struct {
    gate_config_t config;
    int32_t baseline_q16;
    uint16_t nearest_mm;
    bool armed;
    bool occupied;
    bool gpio0;
    bool calibrating;
    gate_fault_t fault;
    uint32_t now_ms;
    uint32_t last_frame_ms;
    uint32_t last_echo_ms;
    uint32_t frame_count;
    uint32_t frame_interval_ms;
    bool have_frame;
    bool have_echo;
    bool on_pending;
    bool off_pending;
    bool clear_pending;
    uint32_t on_since_ms;
    uint32_t off_since_ms;
    uint32_t clear_since_ms;
    bool calibration_pending;
    uint32_t calibration_since_ms;
    uint32_t calibration_sum;
    uint16_t calibration_count;
    uint16_t calibration_min;
    uint16_t calibration_max;
} gate_t;

gate_config_t gate_defaults(void);
bool gate_config_valid(const gate_config_t *config);
void gate_init(gate_t *gate, const gate_config_t *config, uint16_t saved_gap_mm,
               uint32_t now_ms);
bool gate_set_config(gate_t *gate, const gate_config_t *config);
bool gate_set_gap(gate_t *gate, uint16_t gap_mm);
uint16_t gate_gap_mm(const gate_t *gate);
void gate_calibrate(gate_t *gate);
/* All detected peaks must be supplied, in any order. Range is in mm.
 * valid=false means a sensor/calibration error, not an empty passage.
 * This function never sleeps, allocates memory, or writes Flash. */
void gate_frame(gate_t *gate, uint32_t now_ms, bool valid,
                const uint16_t *peaks_mm, size_t peak_count);
/* Call from the main loop even when measurements stop. */
void gate_tick(gate_t *gate, uint32_t now_ms);

#endif
