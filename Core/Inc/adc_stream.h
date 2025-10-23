#include <stdbool.h>

// Выводит 30 семплов из последнего доступного кадра в терминал
void adc_stream_print_samples(uint32_t count, bool ch2);
void adc_stream_stop(void);
#ifndef ADC_STREAM_H
#define ADC_STREAM_H

#include <stdint.h>
#include <stddef.h>
#include "main.h" // FIFO_FRAMES / profile params

#ifdef __cplusplus
extern "C" {
#endif

// Совместимость: если где-то ещё используется FRAME_SAMPLES — мапим на активный дефолт
#ifndef FRAME_SAMPLES
#define FRAME_SAMPLES FRAME_SAMPLES_DEFAULT
#endif

// Внешние буферы DMA (максимальный размер строки = MAX_FRAME_SAMPLES)
extern uint16_t adc1_buffers[FIFO_FRAMES][MAX_FRAME_SAMPLES];
extern uint16_t adc2_buffers[FIFO_FRAMES][MAX_FRAME_SAMPLES];

// Счётчики состояния FIFO
extern volatile uint32_t frame_wr_seq;         // записано (ISR)
extern volatile uint32_t frame_rd_seq;         // выдано (main)
extern volatile uint32_t frame_overflow_drops; // отброшено при переполнении
extern volatile uint32_t frame_sent_seq;       // отправлено по USB (успешно)
extern volatile uint32_t frame_backlog_max;    // максимум backlog

// Время последнего полного DMA кадра (ms HAL_GetTick) для диагностики
extern volatile uint32_t adc_last_full0_ms;
extern volatile uint32_t adc_last_full1_ms;

// Debug info structure for runtime inspection
typedef struct {
    uint32_t frame_wr_seq;
    uint32_t frame_rd_seq;
    uint32_t frame_overflow_drops;
    uint32_t frame_backlog_max;
    uint32_t dma_half0; // ADC1 half transfers
    uint32_t dma_full0; // ADC1 full transfers
    uint32_t dma_half1; // ADC2 half transfers
    uint32_t dma_full1; // ADC2 full transfers
    uint16_t active_samples; // current profile samples per buffer
    uint16_t reserved;
} adc_stream_debug_t;

void adc_stream_init(void);
HAL_StatusTypeDef adc_stream_start(ADC_HandleTypeDef* a1, ADC_HandleTypeDef* a2);
HAL_StatusTypeDef adc_stream_restart(ADC_HandleTypeDef* a1, ADC_HandleTypeDef* a2);
uint8_t adc_get_frame(uint16_t **ch1, uint16_t **ch2, uint16_t *samples);
void adc_stream_get_debug(adc_stream_debug_t *out);

/* Getter функции для получения текущих параметров профиля */
uint8_t adc_stream_get_profile(void);
uint16_t adc_stream_get_active_samples(void);
uint16_t adc_stream_get_buf_rate(void);

// Хук: вызывается из ISR (ADC1 half/full) с количеством добавленных кадров FIFO (frames_added)
void adc_stream_on_new_frames(uint32_t frames_added);

#ifdef __cplusplus
}
#endif

#endif // ADC_STREAM_H