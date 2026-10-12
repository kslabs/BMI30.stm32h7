#ifndef BMI30_XM125_RADAR_H
#define BMI30_XM125_RADAR_H
#include "gate.h"
bool radar_start(const gate_config_t *config);
void radar_stop(void);
bool radar_frame(uint16_t *peaks, size_t *count, bool *near_edge);
/* Diagnostic snapshot from the latest detector result; does not acquire data. */
size_t radar_diagnostics_reply(void *context, char *reply, size_t capacity);
/* Keep USB/UART, fault timers and GPIO serviced while waiting for the sensor. */
void gate_background(void);
#endif
