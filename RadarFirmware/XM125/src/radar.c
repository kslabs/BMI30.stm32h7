/* Uses the Acconeer SDK 1.13.0 Distance Detector API. */
#include "radar.h"
#include "main.h"
#include "acc_detector_distance.h"
#include "acc_hal_integration_a121.h"
#include "acc_rss_a121.h"
#include "acc_sensor.h"
#include "acc_config_subsweep.h"
#include "acc_integration.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SENSOR 1
#define SENSOR_TIMEOUT 500
volatile uint32_t radar_stage;
volatile float radar_config_readback[4];
static struct {
    uint32_t time_ms, measure_ms;
    bool valid, near_edge;
    int16_t temperature;
    uint8_t count, subsweeps;
    uint16_t range_end_mm;
    uint16_t mm[GATE_MAX_PEAKS];
    int32_t strength_mdb[GATE_MAX_PEAKS];
    struct { int32_t start_point; uint16_t points, step, hwaas; unsigned profile; } sweep[4];
} diagnostic;
static struct {
    acc_sensor_t *sensor;
    acc_detector_distance_config_t *config;
    acc_detector_distance_handle_t *handle;
    void *buffer;
    uint32_t buffer_size;
    uint8_t *static_cal;
    uint32_t static_size;
    acc_detector_cal_result_dynamic_t dynamic_cal;
    acc_cal_result_t sensor_cal;
} r;

static bool wait_sensor(void)
{
    uint32_t start = HAL_GetTick();
    do {
        gate_background();
        if (acc_hal_integration_wait_for_sensor_interrupt(SENSOR, 1)) return true;
    } while (HAL_GetTick() - start < SENSOR_TIMEOUT);
    return false;
}
static bool sensor_calibrate(void)
{
    for (unsigned retry = 0; retry < 2; retry++) {
        bool done = false, success;
        acc_hal_integration_sensor_disable(SENSOR);
        acc_hal_integration_sensor_enable(SENSOR);
        do {
            success = acc_sensor_calibrate(r.sensor, &done, &r.sensor_cal, r.buffer, r.buffer_size);
            if (success && !done) success = wait_sensor();
        } while (success && !done);
        if (success) {
            acc_hal_integration_sensor_disable(SENSOR);
            acc_hal_integration_sensor_enable(SENSOR);
            return true;
        }
    }
    return false;
}
static bool detector_calibrate(bool full)
{
    bool done = false, success;
    do {
        if (full)
            success = acc_detector_distance_calibrate(r.sensor, r.handle, &r.sensor_cal,
                r.buffer, r.buffer_size, r.static_cal, r.static_size, &r.dynamic_cal, &done);
        else
            success = acc_detector_distance_update_calibration(r.sensor, r.handle, &r.sensor_cal,
                r.buffer, r.buffer_size, &r.dynamic_cal, &done);
        if (success && !done) success = wait_sensor();
    } while (success && !done);
    return success;
}

void radar_stop(void)
{
    if (r.sensor) acc_sensor_destroy(r.sensor);
    if (r.handle) acc_detector_distance_destroy(r.handle);
    if (r.config) acc_detector_distance_config_destroy(r.config);
    free(r.buffer); free(r.static_cal);
    acc_hal_integration_sensor_disable(SENSOR);
    acc_hal_integration_sensor_supply_off(SENSOR);
    r.sensor = NULL; r.handle = NULL; r.config = NULL;
    r.buffer = NULL; r.static_cal = NULL;
}

bool radar_start(const gate_config_t *config)
{
    radar_stop();
    memset(&diagnostic, 0, sizeof(diagnostic));
    radar_stage = 1;
    if (!acc_rss_hal_register(acc_hal_rss_integration_get_implementation())) return false;
    radar_stage = 2;
    r.config = acc_detector_distance_config_create();
    if (!r.config) return false;
    /* Scan to the configured ROI end instead of spending time on objects
     * beyond the passage. Keep the start at 200 mm so close foreground is
     * measured independently of the 600-mm minimum antenna spacing. */
    acc_detector_distance_config_start_set(r.config, 0.2f);
    acc_detector_distance_config_end_set(r.config, config->range_end_mm / 1000.0f);
    diagnostic.range_end_mm = config->range_end_mm;
    /* Profile 3 resolves a foreground peak next to the strong far antenna
     * more finely than profile 5. Dense samples retain filtering gain. */
    acc_detector_distance_config_max_profile_set(r.config, ACC_CONFIG_PROFILE_3);
    acc_detector_distance_config_peak_sorting_set(r.config, ACC_DETECTOR_DISTANCE_PEAK_SORTING_CLOSEST);
    acc_detector_distance_config_threshold_method_set(r.config, ACC_DETECTOR_DISTANCE_THRESHOLD_METHOD_CFAR);
    acc_detector_distance_config_threshold_sensitivity_set(r.config, config->sensitivity_milli / 1000.0f);
    acc_detector_distance_config_signal_quality_set(r.config, (float)config->signal_quality_db);
    acc_detector_distance_config_reflector_shape_set(r.config, ACC_DETECTOR_DISTANCE_REFLECTOR_SHAPE_GENERIC);
    /* Dense radial samples improve matched-filter SNR for weak, oblique
     * human echoes. The acquisition
     * time and actual subsweeps are exposed by RADAR for on-target checks. */
    acc_detector_distance_config_max_step_length_set(r.config, 4);
    acc_detector_distance_config_close_range_leakage_cancellation_set(r.config, false);
    radar_config_readback[0] = acc_detector_distance_config_start_get(r.config);
    radar_config_readback[1] = acc_detector_distance_config_end_get(r.config);
    radar_config_readback[2] = acc_detector_distance_config_signal_quality_get(r.config);
    radar_config_readback[3] = acc_detector_distance_config_threshold_sensitivity_get(r.config);
    radar_stage = 3;
    r.handle = acc_detector_distance_create(r.config);
    if (!r.handle || !acc_detector_distance_get_sizes(r.handle, &r.buffer_size, &r.static_size)) return false;
    radar_stage = 4;
    r.buffer = malloc(r.buffer_size);
    r.static_cal = malloc(r.static_size);
    if (!r.buffer || !r.static_cal) return false;
    acc_hal_integration_sensor_supply_on(SENSOR);
    acc_hal_integration_sensor_enable(SENSOR);
    radar_stage = 5;
    r.sensor = acc_sensor_create(SENSOR);
    if (!r.sensor) return false;
    radar_stage = 6;
    if (!sensor_calibrate()) return false;
    radar_stage = 7;
    if (!detector_calibrate(true)) return false;
    radar_stage = 8;
    return true;
}

bool radar_frame(uint16_t *peaks, size_t *count, bool *near_edge)
{
    uint32_t started_ms = HAL_GetTick();
    if (!r.sensor || !r.handle) { diagnostic.valid = false; return false; }
    bool available = false;
    acc_detector_distance_result_t result = {0};
    unsigned rounds = 0;
    do {
        if (++rounds > 10 ||
            !acc_detector_distance_prepare(r.handle, r.config, r.sensor, &r.sensor_cal, r.buffer, r.buffer_size) ||
            !acc_sensor_measure(r.sensor) || !wait_sensor() ||
            !acc_sensor_read(r.sensor, r.buffer, r.buffer_size) ||
            !acc_detector_distance_process(r.handle, r.buffer, r.static_cal, &r.dynamic_cal, &available, &result))
            { diagnostic.valid = false; return false; }
    } while (!available);
    if (result.calibration_needed) {
        /* Caller's invalid frame freezes background and preserves occupancy.
         * Sensor temperature calibration never recalibrates the passage gap. */
        diagnostic.valid = false;
        if (!sensor_calibrate() || !detector_calibrate(false)) return false;
        return false;
    }
    *count = 0;
    *near_edge = result.near_start_edge_status;
    diagnostic.time_ms = HAL_GetTick();
    diagnostic.measure_ms = diagnostic.time_ms - started_ms;
    diagnostic.near_edge = *near_edge;
    diagnostic.temperature = result.temperature;
    diagnostic.subsweeps = result.sensor_config ? acc_config_num_subsweeps_get(result.sensor_config) : 0;
    if (diagnostic.subsweeps > 4) diagnostic.subsweeps = 4;
    for (unsigned i = 0; i < diagnostic.subsweeps; i++) {
        diagnostic.sweep[i].start_point = acc_config_subsweep_start_point_get(result.sensor_config, i);
        diagnostic.sweep[i].points = acc_config_subsweep_num_points_get(result.sensor_config, i);
        diagnostic.sweep[i].step = acc_config_subsweep_step_length_get(result.sensor_config, i);
        diagnostic.sweep[i].profile = acc_config_subsweep_profile_get(result.sensor_config, i);
        diagnostic.sweep[i].hwaas = acc_config_subsweep_hwaas_get(result.sensor_config, i);
    }
    for (unsigned i = 0; i < result.num_distances && *count < GATE_MAX_PEAKS; i++) {
        float mm = result.distances[i] * 1000.0f;
        if (isfinite(mm) && mm > 0.0f && mm < 65535.0f) {
            diagnostic.mm[*count] = peaks[*count] = (uint16_t)(mm + 0.5f);
            float strength = result.strengths[i];
            diagnostic.strength_mdb[*count] = isfinite(strength) && fabsf(strength) < 1000.0f ?
                (int32_t)(strength * 1000.0f) : 0;
            (*count)++;
        }
    }
    diagnostic.count = (uint8_t)*count;
    diagnostic.valid = true;
    return true;
}

size_t radar_diagnostics_reply(void *context, char *reply, size_t capacity)
{
    (void)context;
    if (!capacity) return 0;
    size_t used = 0;
#define APPEND(...) do { \
    int n = snprintf(reply + used, capacity - used, __VA_ARGS__); \
    if (n < 0 || (size_t)n >= capacity - used) { reply[0] = 0; return 0; } \
    used += (size_t)n; \
} while (0)
    APPEND("{\"ok\":true,\"time_ms\":%lu,\"valid\":%u,\"measure_ms\":%lu,"
           "\"near_edge\":%u,\"temperature\":%d,\"max_step_length\":4,"
           "\"max_profile\":3,\"range_end_mm\":%u,\"peaks\":[",
           (unsigned long)diagnostic.time_ms, diagnostic.valid, (unsigned long)diagnostic.measure_ms,
           diagnostic.near_edge, diagnostic.temperature, diagnostic.range_end_mm);
    for (unsigned i = 0; i < diagnostic.count; i++)
        APPEND("%s{\"mm\":%u,\"strength_mdb\":%ld}", i ? "," : "", diagnostic.mm[i],
               (long)diagnostic.strength_mdb[i]);
    APPEND("],\"last_processing_subsweeps\":[");
    for (unsigned i = 0; i < diagnostic.subsweeps; i++)
        APPEND("%s{\"start\":%ld,\"points\":%u,\"step\":%u,\"profile\":%u,\"hwaas\":%u}",
               i ? "," : "", (long)diagnostic.sweep[i].start_point, diagnostic.sweep[i].points,
               diagnostic.sweep[i].step, diagnostic.sweep[i].profile, diagnostic.sweep[i].hwaas);
    APPEND("]}\n");
#undef APPEND
    return used;
}
