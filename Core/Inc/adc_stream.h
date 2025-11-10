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

// СЧЁТЧИКИ ПАРНОЙ МОДЕЛИ (legacy)
extern volatile uint32_t frame_wr_seq;         // (DEPRECATED) парно записано (ISR)
extern volatile uint32_t frame_rd_seq;         // (DEPRECATED) парно выдано (main)
extern volatile uint32_t frame_overflow_drops; // (DEPRECATED) отброшено при переполнении
extern volatile uint32_t frame_sent_seq;       // (DEPRECATED) парно отправлено по USB (успешно)
extern volatile uint32_t frame_backlog_max;    // (DEPRECATED) максимум backlog

// НОВОЕ: независимые счётчики по каналам (декуплинг A/B)
extern volatile uint32_t adc_ch_wr_seq[2];     // записано по каналам (ISR)
extern volatile uint32_t adc_ch_rd_seq[2];     // выдано по каналам (main)
extern volatile uint32_t adc_ch_overflow_drops[2]; // переполнения по каналам

// ДИАГНОСТИКА: счётчики нулевых буферов для выявления проблем с ADC/триггером
extern volatile uint32_t adc_ch_zero_buffers[2]; // количество буферов с полностью нулевым содержимым

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
    // Независимые seq/overflows по каналам
    uint32_t ch_wr_seq[2];
    uint32_t ch_rd_seq[2];
    uint32_t ch_overflow_drops[2];
    // ДИАГНОСТИКА: счётчики нулевых буферов (для выявления проблем ADC)
    uint32_t ch_zero_buffers[2]; // количество буферов с полностью нулевым содержимым (по каналу A/B)
    uint16_t active_samples; // current profile samples per buffer
    uint16_t reserved;
} adc_stream_debug_t;

void adc_stream_init(void);
HAL_StatusTypeDef adc_stream_start(ADC_HandleTypeDef* a1, ADC_HandleTypeDef* a2);
HAL_StatusTypeDef adc_stream_restart(ADC_HandleTypeDef* a1, ADC_HandleTypeDef* a2);
// НОВОЕ: получить кадр конкретного канала (0=A/ADC1, 1=B/ADC2). Возвращает 1 при успехе.
uint8_t adc_get_frame_ch(uint8_t ch, uint16_t **buf, uint16_t *samples);
// УСТАРЕВШЕ: парный интерфейс для обратной совместимости
uint8_t adc_get_frame(uint16_t **ch1, uint16_t **ch2, uint16_t *samples);
void adc_stream_get_debug(adc_stream_debug_t *out);

// Хук: вызывается из ISR (ADC1 half/full) с количеством добавленных кадров FIFO (frames_added)
void adc_stream_on_new_frames(uint32_t frames_added);

#ifdef __cplusplus
}
#endif

#endif // ADC_STREAM_H