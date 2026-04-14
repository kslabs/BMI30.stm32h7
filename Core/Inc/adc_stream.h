#include <stdbool.h>

// Function for manual TIM15 correction by direction (+1 or -1)
void adc_stream_push_tim15_phase(int32_t correction_ticks);

// Выводит 30 семплов из последнего доступного кадра в терминал
void adc_stream_print_samples(uint32_t count, bool ch2);
void adc_stream_stop(void);
#ifndef ADC_STREAM_H
#define ADC_STREAM_H

#include <stdint.h>
#include <stddef.h>
#include "main.h" // FIFO_FRAMES / profile params

/* Диагностические флаги по умолчанию (могут быть переопределены в compile flags) */
#ifndef DIAG_SINGLE_ADC1
#define DIAG_SINGLE_ADC1 0  /* 0 = использовать оба ADC (A и B) */
#endif
#ifndef ADC_USB_STAGE_ENABLE
#define ADC_USB_STAGE_ENABLE 0  // ОТКЛЮЧЕНО: прямое чтение из DMA buffers с cache invalidation
#endif

#ifdef __cplusplus
extern "C" {
#endif

// Совместимость: если где-то ещё используется FRAME_SAMPLES — мапим на активный дефолт
#ifndef FRAME_SAMPLES
#define FRAME_SAMPLES FRAME_SAMPLES_DEFAULT
#endif

// Внешние буферы DMA (смещены после guard-слов)
extern uint16_t (*adc1_buffers)[MAX_FRAME_SAMPLES];
extern uint16_t (*adc2_buffers)[MAX_FRAME_SAMPLES];
#if ADC_USB_STAGE_ENABLE
extern uint16_t usb_stage_bufA[MAX_FRAME_SAMPLES];
extern volatile uint16_t adc_stage_crc_a;
extern volatile uint32_t adc_stage_crc_seq;
#endif

// RING BUFFER INDEX (for TC-driven mode - читается напрямую из main loop)
extern volatile uint32_t s_next_ring_index;    // индекс следующего буфера для записи (0-3)

// СЧЁТЧИКИ ПАРНОЙ МОДЕЛИ (legacy - НЕ используются в TC-driven режиме)
extern volatile uint32_t frame_wr_seq;         // (DEPRECATED) парно записано (ISR)
extern volatile uint32_t frame_rd_seq;         // (DEPRECATED) парно выдано (main)
extern volatile uint32_t frame_overflow_drops; // (DEPRECATED) отброшено при переполнении
extern volatile uint32_t frame_sent_seq;       // (DEPRECATED) парно отправлено по USB (успешно)
extern volatile uint32_t frame_backlog_max;    // (DEPRECATED) максимум backlog

// НОВОЕ: независимые счётчики по каналам (декуплинг A/B)
extern volatile uint32_t adc_ch_wr_seq[2];     // записано по каналам (ISR)
extern volatile uint32_t adc_ch_rd_seq[2];     // выдано по каналам (main)
extern volatile uint32_t adc_ch_overflow_drops[2]; // переполнения по каналам

// LOSSLESS/BACKPRESSURE: при заполнении FIFO поток может быть поставлен на паузу (без потери кадров)
extern volatile uint8_t  adc_stream_paused;
extern volatile uint32_t adc_stream_pause_events;

// ДИАГНОСТИКА: счётчики нулевых буферов для выявления проблем с ADC/триггером
extern volatile uint32_t adc_ch_zero_buffers[2]; // количество буферов с полностью нулевым содержимым

// Время последнего полного DMA кадра (ms HAL_GetTick) для диагностики
extern volatile uint32_t adc_last_full0_ms;
extern volatile uint32_t adc_last_full1_ms;

/* Диагностика синхронизации: индекс сэмпла TIM16 на конце буфера (slave) */
extern volatile uint16_t adc_sync_dbg_last_idx;
extern volatile uint16_t adc_sync_dbg_last_samples;
extern volatile uint32_t adc_sync_dbg_last_buf;
extern volatile uint32_t adc_sync_dbg_last_ms;
extern volatile uint8_t  adc_sync_dbg_updated;
// Текущая средняя фазовая ошибка TIM5 (фильтрованная, за 32 буфера)
extern volatile int32_t g_tim5_avg_phase;
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
     // Расширение v4: метрики публикации и вотчдог перезапусков (не добавляем в USB STAT пока; только для внутренних GET_DEBUG)
    uint32_t last_full0_ms;      // HAL_GetTick() последнего полного завершения DMA ADC1
    uint32_t publish_count;      // сколько раз опубликован кадр (frame_wr_seq++)
    uint32_t last_publish_ms;    // время последней публикации (frame_wr_seq инкремент)
    uint32_t restart_attempts;   // попыток перезапуска ADC/DMA вотчдогом
    uint32_t restart_success;    // успешных перезапусков
    uint16_t active_samples; // current profile samples per buffer
    uint16_t reserved;
} adc_stream_debug_t;

void adc_stream_init(void);
HAL_StatusTypeDef adc_stream_start(ADC_HandleTypeDef* a1, ADC_HandleTypeDef* a2);
HAL_StatusTypeDef adc_stream_restart(ADC_HandleTypeDef* a1, ADC_HandleTypeDef* a2);
// НОВОЕ: получить кадр конкретного канала (0=A/ADC1, 1=B/ADC2). Возвращает 1 при успехе.
// Вернуть кадр конкретного канала и его порядковый номер DMA (seq_out опционален)
uint8_t adc_get_frame_ch(uint8_t ch, uint16_t **buf, uint16_t *samples, uint32_t *seq_out);

// LOSSLESS: подтвердить (consume) кадр, который ранее был получен через adc_get_frame_ch (seq должен совпадать)
uint8_t adc_consume_frame_ch(uint8_t ch, uint32_t seq);
// УСТАРЕВШЕ: парный интерфейс для обратной совместимости
uint8_t adc_get_frame(uint16_t **ch1, uint16_t **ch2, uint16_t *samples);
// НОВОЕ: парный интерфейс + вернуть seq (pair seq = frame_rd_seq до инкремента)
uint8_t adc_get_frame_pair(uint16_t **ch1, uint16_t **ch2, uint16_t *samples, uint32_t *seq_out);

// НОВОЕ: парный интерфейс строго из FIFO (adc1_buffers/adc2_buffers),
// игнорирует ADC_USB_STAGE_ENABLE (usb_stage_bufA). Нужен для lossless ROI/AVG.
uint8_t adc_get_frame_pair_fifo(uint16_t **ch1, uint16_t **ch2, uint16_t *samples, uint32_t *seq_out);

// LOSSLESS: peek текущей пары FIFO без продвижения frame_rd_seq.
// Используйте вместе с adc_consume_frame_pair_fifo() после успешной обработки кадра.
uint8_t adc_peek_frame_pair_fifo(uint16_t **ch1, uint16_t **ch2, uint16_t *samples, uint32_t *seq_out);

// LOSSLESS: подтвердить (consume) пару FIFO, ранее полученную через adc_peek_frame_pair_fifo.
uint8_t adc_consume_frame_pair_fifo(uint32_t seq);

// НОВОЕ: peek последнего опубликованного кадра (FIFO), НЕ двигает frame_rd_seq.
// Возвращает самый свежий кадр: seq = frame_wr_seq-1.
// ВАЖНО: указатели валидны пока соответствующий FIFO слот не будет перезаписан.
uint8_t adc_peek_latest_frame_pair_fifo(uint16_t **ch1, uint16_t **ch2, uint16_t *samples, uint32_t *seq_out);
void adc_stream_get_debug(adc_stream_debug_t *out);

// Получить parity (чётность) буфера по seq (0=even, 1=odd) для 400Hz режима
uint8_t adc_get_buffer_parity(uint32_t seq);

// Одноразово инвертировать локальную полярность фазы (PA3/паритет) без изменения DMA/USB логики
void adc_stream_invert_phase_polarity(void);

// DEBUG: вывести trace записей в s_buffer_parity[] (вызывать из non-ISR)
void adc_dump_buffer_trace(void);

// DEBUG: вывести sample[95] для каждого из FIFO_FRAMES буферов (в одну строку)
void adc_stream_print_sample95_all_buffers(void);

// Хук: вызывается из ISR (ADC1 half/full) с количеством добавленных кадров FIFO (frames_added)
void adc_stream_on_new_frames(uint32_t frames_added);

/* Поллинг PD5 (SYNC_IN) по завершению буфера и мягкая подстройка TIM15 */
void adc_sync_pd5_apply_adjustment(void);

// Вотчдог: вызывать периодически из main-loop. Если нет DMA Full длительное время — перезапустить ADC/DMA.
// (now_ms захватывается внутри; параметр удалён для предотвращения рассинхронизации тиков)
void adc_stream_watchdog(void);

// Переключение буферов по TIM2 (вызывается из HAL_TIM_PeriodElapsedCallback)
// Вызов в начале цикла (0→1 TIM2) перед началом заполнения нового буфера
void adc_stream_tim2_switch_buffers(void);

// Внешняя синхронизация: обработка фронта (slave) для перезапуска DMA
void adc_stream_sync_edge(void);

// Fine frequency tuning: установка buf_rate в диапазоне 200-210 Hz
void adc_stream_set_buf_rate_fine(uint16_t buf_rate_hz);

// Внешняя синхронизация: установка buf_rate по входным импульсам (без ограничения marker_hz)
void adc_stream_set_buf_rate_external(uint16_t buf_rate_hz);
void adc_stream_clear_buf_rate_override(void);

// Считывание 32-битного счетчика TIM5 (для точных измерений времени)
static inline uint32_t adc_get_tim5_counter(void) {
    extern TIM_HandleTypeDef htim5;
    return htim5.Instance->CNT;
}

// Вычисление разницы между двумя значениями 32-битного счетчика с учетом переполнения
static inline uint32_t adc_tim5_diff(uint32_t start, uint32_t end) {
    return end - start;  // Для uint32_t переполнение работает автоматически
}

#ifdef __cplusplus
}
#endif

#endif // ADC_STREAM_H
