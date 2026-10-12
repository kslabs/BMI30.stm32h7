#include "gate.h"
#include "protocol.h"
#include "storage.h"
#include "radar.h"
#include "main.h"
#include "acc_integration_log.h"
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

extern UART_HandleTypeDef huart1, huart2;
gate_t bmi30_gate; /* named symbol also useful for SWD diagnostics */
volatile uint32_t radar_restarts;
char radar_last_log[768];
static bool background_ready;

typedef struct {
    UART_HandleTypeDef *uart;
    gate_protocol_t protocol;
    uint8_t rx_byte;
    uint8_t rx[256];
    volatile uint16_t head, tail;
    volatile bool rx_overflow, tx_busy;
    char reply[GATE_REPLY_SIZE];
    uint32_t streamed_ms;
} port_t;
static port_t ports[2];

/* Acconeer logs never share the settings UARTs. Error state is returned in GET. */
void acc_integration_log(acc_log_level_t level, const char *module, const char *format, ...)
{
    (void)level;
    size_t used = strlen(radar_last_log);
    if (used + 200 >= sizeof(radar_last_log)) return;
    int prefix = snprintf(radar_last_log + used, sizeof(radar_last_log) - used, "%s: ", module);
    if (prefix < 0) return;
    used += (size_t)prefix;
    va_list args;
    va_start(args, format);
    vsnprintf(radar_last_log + used, sizeof(radar_last_log) - used - 2, format, args);
    va_end(args);
    used = strlen(radar_last_log);
    radar_last_log[used] = '\n'; radar_last_log[used + 1] = 0;
}

static port_t *find_port(UART_HandleTypeDef *uart)
{
    for (unsigned i = 0; i < 2; i++) if (ports[i].uart == uart) return &ports[i];
    return NULL;
}
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *uart)
{
    port_t *p = find_port(uart);
    if (!p) return;
    uint16_t next = (p->head + 1) % sizeof(p->rx);
    if (next == p->tail) p->rx_overflow = true;
    else { p->rx[p->head] = p->rx_byte; p->head = next; }
    HAL_UART_Receive_IT(uart, &p->rx_byte, 1);
}
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *uart)
{
    port_t *p = find_port(uart);
    if (p) p->tx_busy = false;
}
void HAL_UART_ErrorCallback(UART_HandleTypeDef *uart)
{
    port_t *p = find_port(uart);
    if (!p) return;
    p->rx_overflow = true;
    HAL_UART_AbortReceive(uart);
    HAL_UART_Receive_IT(uart, &p->rx_byte, 1);
}
void USART1_IRQHandler(void) { HAL_UART_IRQHandler(&huart1); }

static void send(port_t *p, size_t size)
{
    if (!size || p->tx_busy) return;
    p->tx_busy = true;
    if (HAL_UART_Transmit_IT(p->uart, (uint8_t *)p->reply, (uint16_t)size) != HAL_OK)
        p->tx_busy = false;
}
static void service_port(port_t *p)
{
    if (p->tx_busy) return;
    if (p->rx_overflow) {
        /* Reset the framing after loss of bytes. No truncated command executes. */
        p->protocol.overflow = true;
        p->rx_overflow = false;
    }
    while (p->tail != p->head && !p->tx_busy) {
        uint8_t byte = p->rx[p->tail];
        p->tail = (p->tail + 1) % sizeof(p->rx);
        send(p, gate_protocol_byte(&p->protocol, byte, p->reply, sizeof(p->reply)));
    }
    if (p->protocol.stream && !p->tx_busy && HAL_GetTick() - p->streamed_ms >= 100) {
        p->streamed_ms = HAL_GetTick();
        send(p, gate_status(&p->protocol, p->reply, sizeof(p->reply)));
    }
}

void gate_background(void)
{
    if (!background_ready) return;
    gate_tick(&bmi30_gate, HAL_GetTick());
    HAL_GPIO_WritePin(MISC_GPIO0_GPIO_Port, MISC_GPIO0_Pin,
                      bmi30_gate.gpio0 ? GPIO_PIN_SET : GPIO_PIN_RESET);
    service_port(&ports[0]); service_port(&ports[1]);
}

int acconeer_main(int argc, char *argv[])
{
    (void)argc; (void)argv;
    gate_storage_prepare();
    gate_config_t config = gate_defaults();
    uint16_t saved_gap = 0;
    gate_storage_load(NULL, &config, &saved_gap);
    gate_init(&bmi30_gate, &config, saved_gap, HAL_GetTick());
    ports[0].uart = &huart2; /* SparkFun USB / CH340C */
    ports[1].uart = &huart1; /* exposed TX/RX, direct Raspberry Pi UART */
    gate_hooks_t hooks = {.save = gate_storage_save, .load = gate_storage_load,
                          .diagnostics = radar_diagnostics_reply};
    for (unsigned i = 0; i < 2; i++) {
        gate_protocol_init(&ports[i].protocol, &bmi30_gate, hooks);
        HAL_UART_Receive_IT(ports[i].uart, &ports[i].rx_byte, 1);
    }
    HAL_NVIC_SetPriority(USART1_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(USART1_IRQn);
    background_ready = true;
    bool ready = false;
    uint16_t active_sensitivity = 0, active_quality = 0, active_range_end = 0;
    uint32_t attempt_ms = HAL_GetTick() - 1000, next_ms = HAL_GetTick();
    while (true) {
        gate_background();
        uint32_t now = HAL_GetTick();
        if (ready && (bmi30_gate.config.sensitivity_milli != active_sensitivity ||
                      bmi30_gate.config.signal_quality_db != active_quality ||
                      bmi30_gate.config.range_end_mm != active_range_end)) {
            gate_frame(&bmi30_gate, now, false, NULL, 0);
            ready = false; attempt_ms = now - 1000;
        }
        if (!ready) {
            if (now - attempt_ms >= 1000) {
                attempt_ms = now;
                radar_restarts++;
                radar_last_log[0] = 0;
                gate_config_t requested = bmi30_gate.config;
                ready = radar_start(&requested);
                if (ready) {
                    active_sensitivity = requested.sensitivity_milli;
                    active_quality = requested.signal_quality_db;
                    active_range_end = requested.range_end_mm;
                }
                next_ms = HAL_GetTick();
                if (!ready) gate_frame(&bmi30_gate, HAL_GetTick(), false, NULL, 0);
            }
        } else if ((int32_t)(now - next_ms) >= 0) {
            uint16_t peaks[GATE_MAX_PEAKS]; size_t count = 0; bool near_edge = false;
            bool valid = radar_frame(peaks, &count, &near_edge);
            if (valid && near_edge) {
                /* A near-edge object has no precise range estimate. Treat it
                 * as foreground, including if the far antenna is still visible. */
                if (count == GATE_MAX_PEAKS) count--;
                peaks[count++] = bmi30_gate.config.range_start_mm;
            }
            gate_frame(&bmi30_gate, HAL_GetTick(), valid, peaks, count);
            if (!valid) { ready = false; attempt_ms = HAL_GetTick(); }
            uint32_t period = 1000 / bmi30_gate.config.rate_hz;
            next_ms += period;
            if ((int32_t)(HAL_GetTick() - next_ms) > 0) next_ms = HAL_GetTick() + 1;
        }
        gate_background();
        __WFI();
    }
}
