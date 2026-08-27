#ifndef __WS2812_SPI_H
#define __WS2812_SPI_H

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"

#ifndef WS2812_ONBOARD_LED_COUNT
#define WS2812_ONBOARD_LED_COUNT 1u
#endif

#ifndef WS2812_STRIP_LED_COUNT
#define WS2812_STRIP_LED_COUNT 20u
#endif

#define WS2812_LED_COUNT (WS2812_ONBOARD_LED_COUNT + WS2812_STRIP_LED_COUNT)

#define WS2812_OPTIC_REACTION_SOURCE_DISABLED 0xFFu

typedef enum {
  WS2812_PATTERN_OFF = 0,
  WS2812_PATTERN_UP_RED_1,
  WS2812_PATTERN_UP_RED_2,
  WS2812_PATTERN_UP_YELLOW_1,
  WS2812_PATTERN_UP_YELLOW_2,
  WS2812_PATTERN_DOWN_RED_1,
  WS2812_PATTERN_DOWN_RED_2,
  WS2812_PATTERN_DOWN_YELLOW_1,
  WS2812_PATTERN_DOWN_YELLOW_2,
  WS2812_PATTERN_IN_RED_1,
  WS2812_PATTERN_IN_RED_2,
  WS2812_PATTERN_IN_YELLOW_1,
  WS2812_PATTERN_IN_YELLOW_2,
  WS2812_PATTERN_OUT_RED,
  WS2812_PATTERN_OUT_YELLOW,
  WS2812_PATTERN_UA_DEMO,
  WS2812_PATTERN_COUNT
} ws2812_pattern_t;

typedef enum {
  WS2812_EVENT_NONE = 0,
  WS2812_EVENT_CHANNEL_B,
  WS2812_EVENT_CHANNEL_A,
  WS2812_EVENT_CHANNEL_BOTH,
  WS2812_EVENT_SPLIT_IN,
  WS2812_EVENT_SPLIT_OUT
} ws2812_event_t;

void ws2812_spi_init(void);
void ws2812_spi_clear(void);
void ws2812_spi_fill_rgb(uint8_t r, uint8_t g, uint8_t b);
void ws2812_spi_set_rgb(uint16_t index, uint8_t r, uint8_t g, uint8_t b);
uint8_t ws2812_spi_show(void);
uint8_t ws2812_spi_is_busy(void);
uint32_t ws2812_spi_get_frame_count(void);
uint32_t ws2812_spi_get_recovery_count(void);
uint32_t ws2812_spi_get_phase_late_skip_count(void);
uint32_t ws2812_spi_get_phase_start_delay_max_us(void);
uint32_t ws2812_spi_get_wire_time_us(void);
void ws2812_spi_set_pattern(ws2812_pattern_t pattern);
ws2812_pattern_t ws2812_spi_get_pattern(void);
ws2812_pattern_t ws2812_spi_get_active_pattern(void);
void ws2812_spi_trigger_pattern(ws2812_pattern_t pattern, uint16_t duration_ms);
void ws2812_spi_trigger_event(ws2812_event_t event, uint16_t duration_ms);
uint8_t ws2812_spi_set_optic_reaction_source(uint8_t source_id);
uint8_t ws2812_spi_get_optic_reaction_source(void);
uint8_t ws2812_spi_get_optic_reaction_active(void);
uint8_t ws2812_spi_get_optic_reaction_remote(void);
void ws2812_spi_service(uint32_t now_ms);
void ws2812_spi_prepare_phase_frame(void);
void ws2812_spi_on_phase_start(uint32_t phase_start_cycles);
void ws2812_spi_dma_irq_trace(void);
void ws2812_spi_spi_irq_trace(void);

#ifdef __cplusplus
}
#endif

#endif /* __WS2812_SPI_H */
