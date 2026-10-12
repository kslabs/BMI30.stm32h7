#ifndef BMI30_RADAR_PROTOCOL_H
#define BMI30_RADAR_PROTOCOL_H
#include "gate.h"

#define GATE_LINE_SIZE 96
#define GATE_REPLY_SIZE 1024

typedef struct {
    void *context;
    /* Called before changing the live config. Must leave the old measurement
     * setup operational on failure. No callback means core-only test mode. */
    bool (*apply)(void *context, const gate_config_t *config);
    bool (*save)(void *context, const gate_config_t *config, uint16_t gap_mm);
    bool (*load)(void *context, gate_config_t *config, uint16_t *gap_mm);
    size_t (*diagnostics)(void *context, char *reply, size_t capacity);
} gate_hooks_t;

typedef struct {
    gate_t *gate;
    gate_hooks_t hooks;
    char line[GATE_LINE_SIZE];
    size_t used;
    bool overflow;
    bool stream;
} gate_protocol_t;

void gate_protocol_init(gate_protocol_t *protocol, gate_t *gate, gate_hooks_t hooks);
/* Modifies line while tokenizing. Each response is one JSON object, followed
 * by LF. An empty or undersized reply buffer returns zero. */
size_t gate_command(gate_protocol_t *protocol, char *line, char *reply, size_t capacity);
size_t gate_status(const gate_protocol_t *protocol, char *reply, size_t capacity);
/* Main-loop API. A line overflow is discarded through the next LF, so its
 * truncated prefix cannot execute a command. Send replies from the main loop,
 * never from the UART ISR. */
size_t gate_protocol_byte(gate_protocol_t *protocol, uint8_t byte,
                          char *reply, size_t capacity);

#endif
