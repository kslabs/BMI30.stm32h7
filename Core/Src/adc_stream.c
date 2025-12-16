/* Прежде чем использовать типы/API — подключаем необходимые заголовки */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include "main.h"
#include "adc_stream.h"

/* Управление логированием этого модуля: по умолчанию выключено, чтобы не спамить из ISR */
#ifndef ADC_LOG_ENABLE
#define ADC_LOG_ENABLE 1
#endif
#if ADC_LOG_ENABLE
#define ADC_LOGF(...)    printf(__VA_ARGS__)
#else
#define ADC_LOGF(...)    do { } while (0)
#endif

/* Ранние forward-declare для локальных (в этом модуле) переменных,
   используемых в вспомогательных функциях ниже */
extern ADC_HandleTypeDef* s_adc1;
extern ADC_HandleTypeDef* s_adc2;
extern volatile uint32_t s_next_ring_index;

// Выводит count семплов из последнего доступного кадра в терминал (ch2 — если true, то второй канал)
void adc_stream_print_samples(uint32_t count, bool ch2) {
    ADC_LOGF("Старт вывода семплов\r\n");
    uint32_t seq = frame_wr_seq ? (frame_wr_seq - 1) : 0;
    uint32_t index = seq & (FIFO_FRAMES - 1u);
    #if ADC_USB_STAGE_ENABLE
    uint16_t *buf = ch2 ? adc2_buffers[index] : usb_stage_bufA;
    #else
    uint16_t *buf = ch2 ? adc2_buffers[index] : adc1_buffers[index];
    #endif
    ADC_LOGF("[ADC][SAMPLES] ch%d seq=%lu index=%lu: ", ch2 ? 2 : 1, (unsigned long)seq, (unsigned long)index);
    uint16_t max = adc_stream_get_active_samples();
    for (uint32_t i = 0; i < count && i < max; ++i) {
        ADC_LOGF("%u ", buf[i]);
    }
    ADC_LOGF("\r\n");
}
// Остановка стрима ADC: корректно останавливает DMA и ADC, сбрасывает буферы
void adc_stream_stop(void) {
    if (s_adc1) {
        HAL_ADC_Stop_DMA(s_adc1);
        HAL_ADC_Stop(s_adc1);
    }
    if (s_adc2) {
        HAL_ADC_Stop_DMA(s_adc2);
        HAL_ADC_Stop(s_adc2);
    }
    frame_wr_seq = frame_rd_seq = 0;
    frame_overflow_drops = 0;
    frame_backlog_max = 0;
    s_next_ring_index = 0;
    ADC_LOGF("[ADC][STOP] DMA и ADC остановлены, буферы сброшены\r\n");
}

// Debug: DMA event counters

#include <string.h>

/* Диагностический флаг: отключить фактический запуск DMA для проверки, что зависание
    основного цикла вызвано штормом прерываний DMA (IRQ 11 = DMA1_Stream0). */
#ifndef DIAG_DISABLE_ADC_DMA
#define DIAG_DISABLE_ADC_DMA 0   /* включаем DMA, чтобы публиковать кадры */
#endif

/* Запускать только ADC1 (вторая DMA не стартует) — 0 = использовать оба АЦП.
    Для нормальной работы нужно 0; установить 1 только при диагностике. */
#ifndef DIAG_SINGLE_ADC1
#define DIAG_SINGLE_ADC1 0  /* Норма: используем оба АЦП, чтобы не замедлять поток */
#endif

/* Включить промежуточный буфер для USB (staging) чтобы исключить гонку DMA↔USB */
#ifndef ADC_USB_STAGE_ENABLE
#define ADC_USB_STAGE_ENABLE 0  // DISABLED: memcpy + CRC + flush @ 200Hz = massive overhead (1.92 MB/sec copy + CRC calculation)
#endif

/* Диагностический режим: выключить ADC/DMA и публиковать статический staging с маркерами */
#ifndef DIAG_STATIC_STAGE_MODE
#define DIAG_STATIC_STAGE_MODE 0
#endif

/* Диагностический лимит количества half/full callback (для отлова runaway).
    0 = отключено (не останавливать DMA). При необходимости теста задать >0. */
#ifndef DIAG_DMA_CALLBACK_LIMIT
#define DIAG_DMA_CALLBACK_LIMIT 0u
#endif

/* Сокращение IRQ: не обрабатывать Half-Complete для ADC1 (добавлять кадры только по Full).
    1 = включено (уменьшить IRQ ADC1 в 2 раза), 0 = учитывать half + full как раньше. */
#ifndef ADC1_DISABLE_HALF_IRQ
#define ADC1_DISABLE_HALF_IRQ 1
#endif

/* Сокращение IRQ: отключить прерывания HT/TC у DMA потока ADC2 целиком, DMA продолжается.
    1 = выключить IRQ ADC2, 0 = оставить как есть.
   ВАЖНО: для корректной синхронизации пар A/B оставляем IRQ ВКЛЮЧЕННЫМИ (0). */
#ifndef ADC2_DISABLE_DMA_IRQS
#define ADC2_DISABLE_DMA_IRQS 0  /* ВКЛЮЧАЕМ IRQ ADC2: оба TC используются для синхронизации завершения кадра */
#endif

/* Включить крайне редкий вывод первых 8 значений из буфера ADC2 прямо в ISR ADC1.
    ВНИМАНИЕ: Любой printf в ISR может привести к стопору потока, поэтому по умолчанию ВЫКЛ. */
#ifndef ADC2_DBG_FIRST8
#define ADC2_DBG_FIRST8 0  /* печать в ISR отключена для стабильности */
#endif

static volatile uint32_t dbg_dma1_half_count = 0, dbg_dma1_full_count = 0;
extern DMA_HandleTypeDef hdma_adc1; /* из auto-generated кода */
extern DMA_HandleTypeDef hdma_adc2;

/* Диагностический дамп регистров B-пути (DMA/ADC/TIM) для расследования застойных ситуаций ADC2 */
static void dump_b_path_regs(uint32_t ndtrA, uint32_t ndtrB)
{
    DMA_Stream_TypeDef *dma1_stream = (DMA_Stream_TypeDef*)hdma_adc1.Instance;
    DMA_Stream_TypeDef *dma2_stream = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
    /* DMA */
    ADC_LOGF("[ADC][WDDBG] DMA1_S0: CR=0x%08lX NDTR=%lu M0AR=0x%08lX FCR=0x%08lX | DMA1_S1: CR=0x%08lX NDTR=%lu M0AR=0x%08lX FCR=0x%08lX\r\n",
        (unsigned long)dma1_stream->CR, (unsigned long)ndtrA, (unsigned long)dma1_stream->M0AR, (unsigned long)dma1_stream->FCR,
        (unsigned long)dma2_stream->CR, (unsigned long)ndtrB, (unsigned long)dma2_stream->M0AR, (unsigned long)dma2_stream->FCR);
    /* ADC2 ключевые регистры */
    ADC_LOGF("[ADC][WDDBG] ADC2: CR=0x%08lX CFGR=0x%08lX ISR=0x%08lX\r\n",
        (unsigned long)ADC2->CR, (unsigned long)ADC2->CFGR, (unsigned long)ADC2->ISR);
    /* TIM15 (источник TRGO) */
    ADC_LOGF("[ADC][WDDBG] TIM15: CR1=0x%08lX CR2=0x%08lX SMCR=0x%08lX SR=0x%08lX CNT=%lu PSC=%lu ARR=%lu\r\n",
        (unsigned long)TIM15->CR1, (unsigned long)TIM15->CR2, (unsigned long)TIM15->SMCR,
        (unsigned long)TIM15->SR, (unsigned long)TIM15->CNT, (unsigned long)TIM15->PSC, (unsigned long)TIM15->ARR);
}

// --- Профили ---
static const adc_stream_profile_t g_profiles[ADC_PROFILE_COUNT] = {
    { .samples_per_buf = 1200, .buf_rate_hz = 200, .fs_hz = 1200u * 200u }, // 0: 1200 samples @ 200Hz (ORIGINAL: 160fps @ 450KB/s)
    { .samples_per_buf = 912,  .buf_rate_hz = 300, .fs_hz = 912u  * 300u }, // 1: balanced (higher pair rate)
    { .samples_per_buf = 944,  .buf_rate_hz = 300, .fs_hz = 944u  * 300u }, // 2: high Fs
    { .samples_per_buf = 976,  .buf_rate_hz = 300, .fs_hz = 976u  * 300u }, // 3: max Fs (near USB limit test)
    { .samples_per_buf = 680,  .buf_rate_hz = 400, .fs_hz = 680u  * 400u }, // 4: HIGH-FPS (smaller frames)
};
static uint8_t g_active_profile = 0;  // Default profile 0: 1200 samples @ 200Hz
static uint16_t g_active_samples = 1200; // runtime N для GATED mode

// --- Guarded DMA-буферы ---
#define ADC_GUARD_WORDS 8u
#define ADC_GUARD_BASE  0x0000u /* тестовый маркер для выявления утечек в полезную нагрузку */
__attribute__((aligned(32))) static uint16_t adc1_raw[FIFO_FRAMES][MAX_FRAME_SAMPLES + 2u * ADC_GUARD_WORDS];
__attribute__((aligned(32))) static uint16_t adc2_raw[FIFO_FRAMES][MAX_FRAME_SAMPLES + 2u * ADC_GUARD_WORDS];
// Внешний вид остаётся 2D-массивом выборок; гварды спрятаны слева/справа
uint16_t (*adc1_buffers)[MAX_FRAME_SAMPLES] = (uint16_t (*)[MAX_FRAME_SAMPLES])(&adc1_raw[0][ADC_GUARD_WORDS]);
uint16_t (*adc2_buffers)[MAX_FRAME_SAMPLES] = (uint16_t (*)[MAX_FRAME_SAMPLES])(&adc2_raw[0][ADC_GUARD_WORDS]);

static inline uint16_t* adc_guard_pre(uint16_t raw[][MAX_FRAME_SAMPLES + 2u * ADC_GUARD_WORDS], uint32_t idx) {
    return &raw[idx][0];
}
static inline uint16_t* adc_guard_post(uint16_t raw[][MAX_FRAME_SAMPLES + 2u * ADC_GUARD_WORDS], uint32_t idx) {
    return &raw[idx][ADC_GUARD_WORDS + MAX_FRAME_SAMPLES];
}
static inline uint16_t adc_guard_pattern(uint32_t idx, uint32_t pos) {
    return (uint16_t)(ADC_GUARD_BASE ^ (idx << 4) ^ pos);
}

// TEMPORARILY DISABLED for performance testing
#if 0
static void adc_fill_guard_for_idx(uint32_t idx) {
    for (uint32_t i = 0; i < ADC_GUARD_WORDS; ++i) {
        adc_guard_pre(adc1_raw, idx)[i]  = adc_guard_pattern(idx, i);
        adc_guard_post(adc1_raw, idx)[i] = adc_guard_pattern(idx, ADC_GUARD_WORDS + i);
        adc_guard_pre(adc2_raw, idx)[i]  = adc_guard_pattern(idx, 0x20u + i);
        adc_guard_post(adc2_raw, idx)[i] = adc_guard_pattern(idx, 0x20u + ADC_GUARD_WORDS + i);
    }
}
#endif

static void adc_fill_all_guards(void) {
    for (uint32_t idx = 0; idx < FIFO_FRAMES; ++idx) {
        // adc_fill_guard_for_idx(idx);  // DISABLED: function commented out
    }
}
static volatile uint8_t adc_guard_bad[FIFO_FRAMES];
#ifndef ADC_GUARD_DROP_ON_MISMATCH
#define ADC_GUARD_DROP_ON_MISMATCH 1 /* 1=строгий drop повреждённого кадра, 0=публиковать несмотря на guard */
#endif
static volatile uint32_t adc_guard_bypass = 0; /* сколько кадров выпустили несмотря на guard */

// TEMPORARILY DISABLED for performance testing (guard check overhead: 19200 ops/sec)
#if 0
static uint8_t adc_check_guard_idx(uint32_t idx, const char *phase) {
    bool bad = false;
    static uint32_t guard_log_last_ms = 0;
    static uint32_t guard_log_budget = 0;
    uint32_t now_ms = HAL_GetTick();
    if (now_ms - guard_log_last_ms >= 1000U) {
        guard_log_last_ms = now_ms;
        guard_log_budget = 6; /* макс 6 логов в секунду, чтобы не душить UART */
    }
    for (uint32_t i = 0; i < ADC_GUARD_WORDS; ++i) {
        if (adc_guard_pre(adc1_raw, idx)[i] != adc_guard_pattern(idx, i) ||
            adc_guard_post(adc1_raw, idx)[i] != adc_guard_pattern(idx, ADC_GUARD_WORDS + i)) {
            bad = true;
        }
        if (adc_guard_pre(adc2_raw, idx)[i] != adc_guard_pattern(idx, 0x20u + i) ||
            adc_guard_post(adc2_raw, idx)[i] != adc_guard_pattern(idx, 0x20u + ADC_GUARD_WORDS + i)) {
            bad = true;
        }
    }
    if (bad) {
        adc_guard_bad[idx] = 1;
        if (guard_log_budget == 0) {
            adc_fill_guard_for_idx(idx);
            return 1u; /* лимит логов на этот интервал исчерпан */
        }
        guard_log_budget--;
        /* Логируем соседние данные для анализа смещения/переписывания */
        uint16_t *a = adc1_buffers[idx];
        uint16_t *b = adc2_buffers[idx];
        uint16_t a0=a[0], a1=a[1], a2=a[2], a3=a[3];
        uint16_t b0=b[0], b1=b[1], b2=b[2], b3=b[3];
        uint16_t a_last3=0, a_last2=0, a_last1=0, a_last0=0;
        uint16_t b_last3=0, b_last2=0, b_last1=0, b_last0=0;
        if(g_active_samples >= 4){
            uint32_t last = (uint32_t)g_active_samples;
            a_last3 = a[last-4]; a_last2 = a[last-3]; a_last1 = a[last-2]; a_last0 = a[last-1];
            b_last3 = b[last-4]; b_last2 = b[last-3]; b_last1 = b[last-2]; b_last0 = b[last-1];
        }
        // ОТКЛЮЧЕНО: избыточный UART вывод тормозит USB @ 200Hz
        // printf("[ADC_WARN] DMA guard mismatch idx=%lu phase=%s preA=%04x postA=%04x preB=%04x postB=%04x...\r\n", ...);
        // dump_b_path_regs(...);
        adc_fill_guard_for_idx(idx); // восстановить паттерн, чтобы последующие проверки были валидны
        return 1u;
    }
    adc_guard_bad[idx] = 0;
    return 0u;
}
#endif

// Убраны тестовые переменные - используем только реальный ADC+DMA

volatile uint32_t frame_wr_seq = 0;      // сколько кадров записано (ISR)
volatile uint32_t frame_rd_seq = 0;      // сколько кадров прочитано потребителем
volatile uint32_t frame_overflow_drops = 0; // отброшено при переполнении
volatile uint32_t frame_sent_seq = 0;    // успешно отправлено по USB (увеличивается вызывающим кодом)
volatile uint32_t frame_backlog_max = 0; // максимальный (wr-rd)
volatile uint32_t adc_last_full0_ms = 0; // время последнего полного DMA ADC1
volatile uint32_t adc_last_full1_ms = 0; // время последнего полного DMA ADC2
// Новые метрики публикации и перезапусков (v4 debug)
volatile uint32_t adc_publish_count = 0;      // число инкрементов frame_wr_seq (парных публикаций)
volatile uint32_t adc_last_publish_ms = 0;     // метка времени последней публикации пары
volatile uint32_t adc_restart_attempts = 0;    // суммарные попытки перезапуска через внешний вотчдог
volatile uint32_t adc_restart_success = 0;     // успешные перезапуски (apply_profile OK)

// Новые независимые счётчики по каналам (A=0, B=1)
volatile uint32_t adc_ch_wr_seq[2] = {0,0};     // записано буферов (ISR) по каждому каналу
volatile uint32_t adc_ch_rd_seq[2] = {0,0};     // выдано потребителю по каналу
volatile uint32_t adc_ch_overflow_drops[2] = {0,0}; // переполнения по каналу

// ДИАГНОСТИКА: счётчики нулевых буферов (детектируем проблемы с ADC/триггером)
volatile uint32_t adc_ch_zero_buffers[2] = {0,0}; // количество буферов с нулевым содержимым (по каналу A/B)

/* Диагностика амплитуд: последний минимум/максимум и индекс максимума по кадру (обновляется каждый буфер) */
volatile uint16_t adc_last_min[2] = {0,0};
volatile uint16_t adc_last_max[2] = {0,0};
volatile uint16_t adc_last_max_idx[2] = {0,0};
volatile uint32_t adc_last_max_tick[2] = {0,0};

// Debug: DMA event counters
static volatile uint32_t dma_half0 = 0, dma_full0 = 0, dma_half1 = 0, dma_full1 = 0;
/* Одноразовый дамп первых 8 значений из первого завершенного буфера ADC2 после селективного рестарта.
    Включается флагом из adc_restart_channel_b() и печатается в следующем TC IRQ канала B. */
static volatile uint8_t s_dbg_dump_b_first8_once = 0;

// Индекс следующего буфера в кольце, который будет назначен в свободный банк DMA (DBM)
volatile uint32_t s_next_ring_index = 0; // всегда < FIFO_FRAMES
static volatile uint32_t s_last_started_idx = 0; // индекс буфера, на который запущен DMA сейчас
static volatile uint8_t  s_tc_mask = 0;        // bit0=ADC1 TC seen, bit1=ADC2 TC seen

ADC_HandleTypeDef* s_adc1 = NULL;
ADC_HandleTypeDef* s_adc2 = NULL;

/* Новый механизм: считаем кадр готовым (frame_wr_seq++) только когда ОДИНАКОВЫЙ индекс
   в кольце завершён у обоих АЦП. Для этого отмечаем готовность по индексам:
   bit0=ADC1 complete, bit1=ADC2 complete. Продвигаем «готовый» индекс строго по порядку. */
static volatile uint8_t s_pair_ready_mask[FIFO_FRAMES];
static volatile uint32_t s_pair_ready_idx = 0; /* следующий индекс, который ждём к публикации */
static const uint8_t READY_MASK_FULL = DIAG_SINGLE_ADC1 ? 0x01u : 0x03u;
// static const uint8_t TC_REQUIRED_MASK = DIAG_SINGLE_ADC1 ? 0x01u : 0x03u;  // UNUSED

#if ADC_USB_STAGE_ENABLE
__attribute__((aligned(32))) uint16_t usb_stage_bufA[MAX_FRAME_SAMPLES];
__attribute__((aligned(32))) uint16_t usb_stage_bufB[MAX_FRAME_SAMPLES];
volatile uint16_t adc_stage_crc_a = 0;
volatile uint32_t adc_stage_crc_seq = 0;
#endif

/* Флаг активного статического режима (без реального DMA) */
static uint8_t s_static_stage_mode = 0;

/* Вспомогательная функция: вычислить индекс кольца по адресу M0AR/M1AR */
static inline uint32_t adc_addr_to_index(uint32_t addr, uint16_t buf[FIFO_FRAMES][MAX_FRAME_SAMPLES])
{
    uint32_t base = (uint32_t)&buf[0][0];
    uint32_t stride = (uint32_t)(MAX_FRAME_SAMPLES * sizeof(uint16_t));
    if(addr < base) return 0;
    uint32_t diff = addr - base;
    return (diff / stride) & (FIFO_FRAMES - 1u);
}

/* Отметить готовность канала и, если пара на очередном индексе готова, опубликовать её */
static inline void adc_mark_ready_and_publish(uint8_t ch_bit)
{
    /* Попробуем публиковать подряд готовые пары (в правильном порядке) */
    while (s_pair_ready_mask[s_pair_ready_idx] == READY_MASK_FULL) {
        /* Очередная пара полностью готова */
        s_pair_ready_mask[s_pair_ready_idx] = 0;
        s_pair_ready_idx = (s_pair_ready_idx + 1u) & (FIFO_FRAMES - 1u);
        /* публикуем + уведомляем верхний уровень */
        frame_wr_seq += 1u;
        adc_publish_count++;
        adc_last_publish_ms = HAL_GetTick();
        uint32_t backlog = frame_wr_seq - frame_rd_seq;
        if (backlog > frame_backlog_max) frame_backlog_max = backlog;
        if (backlog > FIFO_FRAMES) {
            uint32_t excess = backlog - FIFO_FRAMES;
            frame_overflow_drops += excess;
            frame_rd_seq += excess;
        }
        adc_stream_on_new_frames(1u);
    }
    (void)ch_bit; /* параметр оставлен на будущее для расширенной диагностики */
}

/* Быстрый проход по буферу для min/max и индекса максимума */
static inline void adc_scan_minmax(const uint16_t *buf, uint16_t samples, uint16_t *out_min, uint16_t *out_max, uint16_t *out_max_idx)
{
    uint16_t vmin = 0xFFFFu; uint16_t vmax = 0; uint16_t imax = 0;
    for(uint16_t i=0;i<samples;i++){
        uint16_t v = buf[i];
        if(v < vmin) vmin = v;
        if(v > vmax){ vmax = v; imax = i; }
    }
    *out_min = vmin; *out_max = vmax; *out_max_idx = imax;
}

/* Инвалидация D-Cache для буфера DMA (STM32H7: адрес и длина должны быть кратны 32 байтам) */
static inline void adc_invalidate_cache_for_buffer(void *buf, uint32_t samples)
{
#if 0 // DISABLED for test: 400 calls/sec @ 80 cache lines = 32000 invalidate ops/sec
#if defined (SCB_InvalidateDCache_by_Addr)
    uintptr_t addr = (uintptr_t)buf;
    uintptr_t start = addr & ~(uintptr_t)31u; /* выравнивание вниз до 32 байт */
    uint32_t bytes = samples * (uint32_t)sizeof(uint16_t);
    uint32_t extra = (uint32_t)(addr - start);
    uint32_t total = bytes + extra;
    uint32_t total_aligned = (total + 31u) & ~31u; /* кратность 32 */
    SCB_InvalidateDCache_by_Addr((uint32_t*)start, (int32_t)total_aligned);
#else
    (void)buf; (void)samples;
#endif
#endif
    (void)buf; (void)samples; // Suppress warnings when disabled
}

static inline void adc_flush_cache_for_buffer(void *buf, uint32_t samples)
{
#if 0 // DISABLED for test: 400 calls/sec @ 80 cache lines = 32000 cache ops/sec
#if defined (SCB_CleanDCache_by_Addr)
    uintptr_t addr = (uintptr_t)buf;
    uintptr_t start = addr & ~(uintptr_t)31u; /* выравнивание вниз до 32 байт */
    uint32_t bytes = samples * (uint32_t)sizeof(uint16_t);
    uint32_t extra = (uint32_t)(addr - start);
    uint32_t total = bytes + extra;
    uint32_t total_aligned = (total + 31u) & ~31u; /* кратность 32 */
    SCB_CleanDCache_by_Addr((uint32_t*)start, (int32_t)total_aligned);
#else
    (void)buf; (void)samples;
#endif
#endif
    (void)buf; (void)samples; // Suppress warnings when disabled
}

#if ADC_USB_STAGE_ENABLE
/* CRC16-CCITT (0x1021, init 0xFFFF) по лоу-байту/хи-байту выборок */
static uint16_t adc_crc16_le(const uint16_t *data, uint16_t samples)
{
    uint16_t crc = 0xFFFFu;
    for (uint16_t i = 0; i < samples; ++i) {
        uint16_t v = data[i];
        uint8_t bytes[2] = { (uint8_t)(v & 0xFFu), (uint8_t)(v >> 8) };
        for (int b = 0; b < 2; ++b) {
            crc ^= (uint16_t)bytes[b] << 8;
            for (uint8_t bit = 0; bit < 8; ++bit) {
                crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}
#endif

#if ADC_USB_STAGE_ENABLE
/* Единая установка маркеров в staging-буфер */
void adc_stage_write_markers(uint16_t *buf, uint16_t samples, uint16_t buf_idx)
{
    (void)buf; (void)samples; (void)buf_idx; /* маркеры отключены */
}
#endif

uint8_t adc_stream_static_mode_enabled(void){ return s_static_stage_mode; }

// Публичные функции профиля
uint8_t adc_stream_get_profile(void) { return g_active_profile; }
uint16_t adc_stream_get_active_samples(void) { return g_active_samples; }
uint16_t adc_stream_get_buf_rate(void) { return g_profiles[g_active_profile].buf_rate_hz; }
uint32_t adc_stream_get_fs(void) { return g_profiles[g_active_profile].fs_hz; }

static HAL_StatusTypeDef adc_stream_apply_profile(void) {
    if (!s_adc1 || (!DIAG_SINGLE_ADC1 && !s_adc2)) {
        ADC_LOGF("[ADC][APPLY_PROFILE] ERROR: s_adc1/s_adc2 не инициализированы!\r\n");
        return HAL_ERROR;
    }
    uint32_t total_samples = (uint32_t)g_active_samples;
    ADC_LOGF("[ADC][APPLY_PROFILE] profile=%u samples=%u\r\n", (unsigned)g_active_profile, (unsigned)g_active_samples);
    // Остановить DMA перед запуском с новым размером
        HAL_ADC_Stop_DMA(s_adc1);
        /* NDTR страховка перед стартом: указываем фактическое N */
        ((DMA_Stream_TypeDef*)s_adc1->DMA_Handle->Instance)->NDTR = total_samples;
        HAL_ADC_Stop_DMA(s_adc2);
        ((DMA_Stream_TypeDef*)s_adc2->DMA_Handle->Instance)->NDTR = total_samples;
    ADC_LOGF("[ADC][APPLY_PROFILE] DMA остановлен, подготовка к запуску\r\n");
    frame_wr_seq = frame_rd_seq = 0;
    frame_overflow_drops = 0;
    frame_backlog_max = 0;
    adc_fill_all_guards();
    /* Сброс независимых счётчиков по каналам */
    adc_ch_wr_seq[0] = adc_ch_wr_seq[1] = 0;
    adc_ch_rd_seq[0] = adc_ch_rd_seq[1] = 0;
    adc_ch_overflow_drops[0] = adc_ch_overflow_drops[1] = 0;
    adc_ch_zero_buffers[0] = adc_ch_zero_buffers[1] = 0; // ДИАГНОСТИКА: сброс счётчиков нулевых буферов
    /* Сброс новой синхронизации пар */
    for (unsigned i = 0; i < FIFO_FRAMES; ++i) s_pair_ready_mask[i] = 0;
    s_pair_ready_idx = 0;
    s_next_ring_index = 0;  // Начинаем с buf[0], после TC перейдём на buf[1], потом buf[2]... buf[31]→buf[0]
    #if ADC_USB_STAGE_ENABLE
    adc_stage_crc_a = 0; adc_stage_crc_seq = 0;
    #endif

    /* Активируем статический режим, если задан компиляционным флагом */
    s_static_stage_mode = DIAG_STATIC_STAGE_MODE ? 1u : 0u;
    if(s_static_stage_mode){
        uint32_t stage_ns = total_samples; if(stage_ns > MAX_FRAME_SAMPLES) stage_ns = MAX_FRAME_SAMPLES;
        #if ADC_USB_STAGE_ENABLE
        memset(usb_stage_bufA, 0, stage_ns * sizeof(uint16_t));
        adc_stage_crc_a = adc_crc16_le(usb_stage_bufA, (uint16_t)stage_ns);
        adc_stage_crc_seq = 1u;
        adc_flush_cache_for_buffer(usb_stage_bufA, (uint32_t)stage_ns);
        #endif
        if(!DIAG_SINGLE_ADC1){ memset(adc2_buffers[0], 0, stage_ns * sizeof(uint16_t)); adc_flush_cache_for_buffer(adc2_buffers[0], (uint32_t)stage_ns); }
        ADC_LOGF("[ADC][STATIC] mode: DMA disabled, staged markers cleared (samples=%lu)\r\n", (unsigned long)stage_ns);
        return HAL_OK;
    }
    
    #if DIAG_DISABLE_ADC_DMA
        ADC_LOGF("[ADC][DIAG] DMA start suppressed (DIAG_DISABLE_ADC_DMA=1) total_samples=%lu\r\n", (unsigned long)total_samples);
        return HAL_OK;
        s_last_started_idx = 0;
        s_last_started_idx = 0;
        s_tc_mask = 0;
    #else
    // Старт ADC1 DMA на буфер[0] длиной N
    HAL_StatusTypeDef rc1 = HAL_ADC_Start_DMA(s_adc1, (uint32_t*)adc1_buffers[0], total_samples);
    ADC_LOGF("[ADC][APPLY_PROFILE] HAL_ADC_Start_DMA ADC1 rc=%d\r\n", (int)rc1);
    if (rc1 != HAL_OK) return HAL_ERROR;
        #if !DIAG_SINGLE_ADC1
    HAL_StatusTypeDef rc2 = HAL_ADC_Start_DMA(s_adc2, (uint32_t*)adc2_buffers[0], total_samples);
    ADC_LOGF("[ADC][APPLY_PROFILE] HAL_ADC_Start_DMA ADC2 rc=%d\r\n", (int)rc2);
    if (rc2 != HAL_OK) return HAL_ERROR;
        #if ADC2_DISABLE_DMA_IRQS
            /* (не используется) */
        #else
            /* Убедимся, что IRQ для ADC2 DMA включены (Half/Full не требуем, достаточно TC) */
            HAL_NVIC_EnableIRQ(DMA1_Stream1_IRQn);
        #endif
        #endif
        /* DBM НЕ используется - DMA перезапускается вручную в callback на новый буфер.
           Отключаем Half Transfer IRQ (оставляем только Transfer Complete) */
        {
            DMA_Stream_TypeDef *st = (DMA_Stream_TypeDef*)hdma_adc1.Instance;
            st->CR &= ~((uint32_t)(1u<<3));  /* Отключаем HTIE */
        }
        #if !DIAG_SINGLE_ADC1
        {
            /* Отключаем HTIE и для ADC2, оставляя только TC */
            DMA_Stream_TypeDef *st2 = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
            st2->CR &= ~((uint32_t)(1u<<3));
        }
        #endif
        /* Одноразовый вывод регистров DMA для ADC1 */
        {
            DMA_Stream_TypeDef *st = (DMA_Stream_TypeDef*)hdma_adc1.Instance;
            ADC_LOGF("[ADC][DMA1S0] CR=0x%08lX NDTR=%lu PAR=0x%08lX M0AR=0x%08lX FCR=0x%08lX single=%u\r\n",
                   (unsigned long)st->CR,
                   (unsigned long)st->NDTR,
                   (unsigned long)st->PAR,
                   (unsigned long)st->M0AR,
                   (unsigned long)st->FCR,
                   (unsigned)DIAG_SINGLE_ADC1);
        }
        /* Одноразовый вывод регистров DMA для ADC2 */
        #if !DIAG_SINGLE_ADC1
        {
            DMA_Stream_TypeDef *st2 = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
            ADC_LOGF("[ADC][DMA1S1] CR=0x%08lX NDTR=%lu PAR=0x%08lX M0AR=0x%08lX FCR=0x%08lX\r\n",
                   (unsigned long)st2->CR,
                   (unsigned long)st2->NDTR,
                   (unsigned long)st2->PAR,
                   (unsigned long)st2->M0AR,
                   (unsigned long)st2->FCR);
        }
        #endif
    #endif
    return HAL_OK;
}

int adc_stream_set_profile(uint8_t prof_id) {
    if (prof_id >= ADC_PROFILE_COUNT) return -1;
    if (prof_id == g_active_profile) {
        ADC_LOGF("[ADC][PROF] prof=%u ALREADY active, g_active_samples=%u\r\n", prof_id, g_active_samples);
        return 0; // уже
    }
    g_active_profile = prof_id;
    g_active_samples = g_profiles[prof_id].samples_per_buf;
    ADC_LOGF("[ADC][PROF] SET prof=%u -> g_active_samples=%u, adc_ready=%d\r\n", prof_id, g_active_samples, (s_adc1 && s_adc2)?1:0);
    if (s_adc1 && s_adc2) {
        if (adc_stream_apply_profile() != HAL_OK) {
            ADC_LOGF("[ADC][PROF] apply_profile FAILED!\r\n");
            return -2;
        }
        ADC_LOGF("[ADC][PROF] apply_profile OK\r\n");
    }
    return 0;
}

void adc_stream_init(void) {
    frame_wr_seq = frame_rd_seq = 0;
    frame_overflow_drops = 0;
    frame_backlog_max = 0;
    adc_fill_all_guards();
}

HAL_StatusTypeDef adc_stream_start(ADC_HandleTypeDef* a1, ADC_HandleTypeDef* a2) {
    s_adc1 = a1; s_adc2 = a2;
    
    // КРИТИЧЕСКИ ВАЖНО: Калибровка ADC перед запуском DMA для точности данных
    ADC_LOGF("[ADC][CALIB] Starting ADC1 calibration...\r\n");
    if (HAL_ADCEx_Calibration_Start(a1, ADC_CALIB_OFFSET, ADC_SINGLE_ENDED) != HAL_OK) {
        ADC_LOGF("[ADC][CALIB] ADC1 calibration FAILED!\r\n");
        return HAL_ERROR;
    }
    ADC_LOGF("[ADC][CALIB] ADC1 calibration OK\r\n");
    
    #if !DIAG_SINGLE_ADC1
    ADC_LOGF("[ADC][CALIB] Starting ADC2 calibration...\r\n");
    if (HAL_ADCEx_Calibration_Start(a2, ADC_CALIB_OFFSET, ADC_SINGLE_ENDED) != HAL_OK) {
        ADC_LOGF("[ADC][CALIB] ADC2 calibration FAILED!\r\n");
        return HAL_ERROR;
    }
    ADC_LOGF("[ADC][CALIB] ADC2 calibration OK\r\n");
    #endif
    
    /* Не переустанавливаем профиль по умолчанию здесь.
       Используем текущий g_active_profile (может быть задан хостом через SET_PROFILE до START).
       При инициализации по умолчанию он уже установлен в ADC_PROFILE_B_DEFAULT. */
    g_active_samples = g_profiles[g_active_profile].samples_per_buf;
    adc_stream_init();
    ADC_LOGF("[ADC][START] profile=%u samples=%u\r\n", (unsigned)g_active_profile, (unsigned)g_active_samples);
    HAL_StatusTypeDef rc = adc_stream_apply_profile();
    ADC_LOGF("[ADC][START] adc_stream_apply_profile rc=%d\r\n", (int)rc);
    return rc;
}

HAL_StatusTypeDef adc_stream_restart(ADC_HandleTypeDef* a1, ADC_HandleTypeDef* a2) {
    if (a1) s_adc1 = a1;
    if (a2) s_adc2 = a2;
    return adc_stream_apply_profile();
}

// Проверка буфера на нулевое содержимое (для диагностики проблем ADC)
static inline uint8_t is_buffer_all_zeros(uint16_t *buf, uint16_t samples) {
    for (uint16_t i = 0; i < samples; i++) {
        if (buf[i] != 0) return 0; // найден ненулевой элемент
    }
    return 1; // все нули
}

// Получить один кадр конкретного канала (независимая модель). Возвращает 1 если кадр получен.
uint8_t adc_get_frame_ch(uint8_t ch, uint16_t **buf, uint16_t *samples, uint32_t *seq_out) {
    if (ch > 1 || !buf || !samples) return 0;
    
    __disable_irq();
    if (adc_ch_rd_seq[ch] == adc_ch_wr_seq[ch]) {
        __enable_irq();
        return 0; // нет новых
    }
    uint32_t seq = adc_ch_rd_seq[ch]++;
    __enable_irq();
    if (seq_out) { *seq_out = seq; }

    /* НОВАЯ СХЕМА: TIM2 callback пишет маркеры в prev_idx,
       который соответствует seq (без сдвига). USB читает тот же буфер. */
    uint32_t index = seq & (FIFO_FRAMES - 1u);
    #if ADC_USB_STAGE_ENABLE
    *buf = (ch==0) ? usb_stage_bufA : usb_stage_bufB;
    #else
    *buf = (ch==0) ? adc1_buffers[index] : adc2_buffers[index];
    #endif
    *samples = g_active_samples;
    
    // DEBUG: Проверка лестницы отключена
    #if 0  // ОТКЛЮЧЕНО: маркеры убраны
    static uint32_t last_read_check_ms = 0;
    static uint32_t read_counter = 0;
    uint32_t now_read_ms = HAL_GetTick();
    if (now_read_ms - last_read_check_ms >= 1000 && ch == 0) {  // Только для канала A
        last_read_check_ms = now_read_ms;
        read_counter++;
        uint16_t *pbuf = *buf;
        uint16_t r0 = (g_active_samples > 0) ? pbuf[0] : 0;
        uint16_t r100 = (g_active_samples > 100) ? pbuf[100] : 0;
        uint16_t r200 = (g_active_samples > 200) ? pbuf[200] : 0;
        uint16_t r300 = (g_active_samples > 300) ? pbuf[300] : 0;
        uint16_t r400 = (g_active_samples > 400) ? pbuf[400] : 0;
        printf("[MCU_READ#%lu] CH%u idx=%lu: [0]=%u [100]=%u [200]=%u [300]=%u [400]=%u\r\n",
               (unsigned long)read_counter, ch, (unsigned long)index, r0, r100, r200, r300, r400);
    }
    #endif
    
    // ДИАГНОСТИКА: проверяем буфер на нулевое содержимое
    if (is_buffer_all_zeros(*buf, *samples)) {
        adc_ch_zero_buffers[ch]++;
        ADC_LOGF("[ADC][DIAG] CH%u: zero buffer detected! seq=%lu idx=%lu zeros_total=%lu\r\n", 
                 ch, (unsigned long)seq, (unsigned long)index, (unsigned long)adc_ch_zero_buffers[ch]);
    }
    
    return 1;
}

uint8_t adc_get_frame(uint16_t **ch1, uint16_t **ch2, uint16_t *samples) {
    if (!ch1 || !ch2 || !samples) {
        ADC_LOGF("[ADC][GET_FRAME] ERROR: ch1/ch2/samples NULL\r\n");
        return 0;
    }
    __disable_irq();
    if (frame_rd_seq == frame_wr_seq) {
        __enable_irq();
        ADC_LOGF("[ADC][GET_FRAME] Нет новых кадров: frame_wr_seq=%lu frame_rd_seq=%lu\r\n", (unsigned long)frame_wr_seq, (unsigned long)frame_rd_seq);
        return 0;
    }
    uint32_t seq = frame_rd_seq++;
    __enable_irq();
    uint32_t index = seq & (FIFO_FRAMES - 1u);
    #if ADC_USB_STAGE_ENABLE
    *ch1 = usb_stage_bufA;
    *ch2 = adc2_buffers[index];
    #else
    *ch1 = adc1_buffers[index];
    *ch2 = adc2_buffers[index];
    #endif
    *samples = g_active_samples;
    ADC_LOGF("[ADC][GET_FRAME] OK: seq=%lu index=%lu samples=%u\r\n", (unsigned long)seq, (unsigned long)index, (unsigned)g_active_samples);
    return 1;
}

void adc_stream_get_debug(adc_stream_debug_t *out) {
    if (!out) return;
    out->frame_wr_seq = frame_wr_seq;
    out->frame_rd_seq = frame_rd_seq;
    out->frame_overflow_drops = frame_overflow_drops;
    out->frame_backlog_max = frame_backlog_max;
    out->dma_half0 = dma_half0; out->dma_full0 = dma_full0;
    out->dma_half1 = dma_half1; out->dma_full1 = dma_full1;
    out->ch_wr_seq[0] = adc_ch_wr_seq[0];
    out->ch_wr_seq[1] = adc_ch_wr_seq[1];
    out->ch_rd_seq[0] = adc_ch_rd_seq[0];
    out->ch_rd_seq[1] = adc_ch_rd_seq[1];
    out->ch_overflow_drops[0] = adc_ch_overflow_drops[0];
    out->ch_overflow_drops[1] = adc_ch_overflow_drops[1];
    out->ch_zero_buffers[0] = adc_ch_zero_buffers[0];
    out->ch_zero_buffers[1] = adc_ch_zero_buffers[1];
    out->last_full0_ms = adc_last_full0_ms;
    out->publish_count = adc_publish_count;
    out->last_publish_ms = adc_last_publish_ms;
    out->restart_attempts = adc_restart_attempts;
    out->restart_success = adc_restart_success;
    out->active_samples = g_active_samples;
    out->reserved = 0;
}

// Weak hook (can be overridden in higher-level module, e.g. USB)
void __attribute__((weak)) adc_stream_on_new_frames(uint32_t frames_added) { (void)frames_added; }

// --- HAL callbacks ---
/* Общий обработчик TC: ждём завершения обоих DMA, затем публикуем кадр и стартуем следующий. */
static void adc_handle_tc(uint8_t tc_bit) {
    /* TIM2 выполняет переключение, TC не трогаем */
    (void)tc_bit;
    return;
}

void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef* hadc) {
    (void)hadc; // Half не используем
    return;
    
    #if 0  // Старый код с обработкой Half Transfer (не нужен в NORMAL mode)
    if (hadc->Instance == (s_adc1 ? s_adc1->Instance : NULL)) {
    dma_half0++; dbg_dma1_half_count++;
#if DIAG_DMA_CALLBACK_LIMIT
    if(dbg_dma1_half_count > DIAG_DMA_CALLBACK_LIMIT){
        HAL_ADC_Stop_DMA(s_adc1);
        printf("[ADC][WARN] HALF limit reached -> stop DMA (half=%lu full=%lu)\r\n",
           (unsigned long)dbg_dma1_half_count,
           (unsigned long)dbg_dma1_full_count);
        return; /* не продолжаем обработку */
    }
#endif
        /* Half-Complete игнорируем (IRQ отключён), оставлено для счётчиков */
        (void)0;
    } else if (hadc->Instance == (s_adc2 ? s_adc2->Instance : NULL)) {
        dma_half1++; // используем только для диагностики
    }
    #endif  // #if 0
}

/* ========================================================================
   TIM2 callback: переключение буферов строго по окну TIM2 (CH1 FALL)
   ======================================================================== */
void adc_stream_tim2_switch_buffers(void) {
    if (!s_adc1 || (!DIAG_SINGLE_ADC1 && !s_adc2)) return;

    DMA_Stream_TypeDef *dma1 = (DMA_Stream_TypeDef*)s_adc1->DMA_Handle->Instance;
    #if !DIAG_SINGLE_ADC1
    DMA_Stream_TypeDef *dma2 = (DMA_Stream_TypeDef*)s_adc2->DMA_Handle->Instance;
    #endif

    const uint32_t done_idx = s_next_ring_index;
    const uint32_t next_idx = (done_idx + 1u) & (FIFO_FRAMES - 1u);
    const uint32_t total_samples = (uint32_t)g_active_samples;
    
    /* Дожидаемся полного завершения текущей передачи перед переключением */
    if (dma1->NDTR != 0) return;
    #if !DIAG_SINGLE_ADC1
    if (dma2->NDTR != 0) return;
    #endif

    // ВРЕМЕННО ОТКЛЮЧЕНО: guard проверка и заполнение тормозят @ 200Hz
    uint8_t guard_bad_done = 0; // adc_check_guard_idx(done_idx, "tim2-done");
    // adc_check_guard_idx(next_idx, "tim2-pre");
    // adc_fill_guard_for_idx(next_idx);  // 32 записи × 200Hz = 6400 операций/сек

    /* Останавливаем и перезапускаем DMA на следующий буфер в такт TIM2 */
    HAL_ADC_Stop_DMA(s_adc1);
    /* NDTR страховка: программируем фактическое N только перед новым стартом */
    dma1->NDTR = total_samples;
    HAL_ADC_Start_DMA(s_adc1, (uint32_t*)adc1_buffers[next_idx], total_samples);
    #if !DIAG_SINGLE_ADC1
    HAL_ADC_Stop_DMA(s_adc2);
    dma2->NDTR = total_samples;
    HAL_ADC_Start_DMA(s_adc2, (uint32_t*)adc2_buffers[next_idx], total_samples);
    #endif

    s_last_started_idx = next_idx;
    s_next_ring_index = next_idx;
    s_tc_mask = 0;
    adc_last_full0_ms = HAL_GetTick();
    adc_last_full1_ms = adc_last_full0_ms;

    if (guard_bad_done && ADC_GUARD_DROP_ON_MISMATCH) {
        frame_overflow_drops++;
        // ОТКЛЮЧЕНО: printf тормозит @ 200Hz
        // printf("[ADC_DROP] guard mismatch idx=%lu -> drop frame\r\n", (unsigned long)done_idx);
    }
    if (guard_bad_done && !ADC_GUARD_DROP_ON_MISMATCH) {
        adc_guard_bypass++;
        // ОТКЛЮЧЕНО: printf тормозит @ 200Hz
        // printf("[ADC_WARN] guard mismatch idx=%lu -> publish anyway\r\n", (unsigned long)done_idx);
    }

    /* Обновляем поканальные счётчики (для USB таска) с контролем переполнения очереди */
    adc_ch_wr_seq[0]++;
    uint32_t backlogA = adc_ch_wr_seq[0] - adc_ch_rd_seq[0];
    if (backlogA > FIFO_FRAMES) {
        uint32_t excess = backlogA - FIFO_FRAMES;
        adc_ch_overflow_drops[0] += excess;
        adc_ch_rd_seq[0] += excess;
    }
    #if !DIAG_SINGLE_ADC1
    adc_ch_wr_seq[1]++;
    uint32_t backlogB = adc_ch_wr_seq[1] - adc_ch_rd_seq[1];
    if (backlogB > FIFO_FRAMES) {
        uint32_t excess = backlogB - FIFO_FRAMES;
        adc_ch_overflow_drops[1] += excess;
        adc_ch_rd_seq[1] += excess;
    }
    #endif

    adc_invalidate_cache_for_buffer(adc1_buffers[done_idx], total_samples);
    #if !DIAG_SINGLE_ADC1
    adc_invalidate_cache_for_buffer(adc2_buffers[done_idx], total_samples);
    #endif

    // PERFORMANCE CRITICAL: minmax scan отключён (480k ops/sec @ 200Hz × 1200 samples × 2 channels)
    // Эта проверка тормозила систему, снижая FPS со 160 до 125
    #if 0
    /* Диагностика: вычисляем min/max и индекс max для кадра (помогает локализовать всплески) */
    uint16_t a_min=0, a_max=0, a_imax=0, b_min=0, b_max=0, b_imax=0;
    adc_scan_minmax(adc1_buffers[done_idx], (uint16_t)total_samples, &a_min, &a_max, &a_imax);
    #if !DIAG_SINGLE_ADC1
    adc_scan_minmax(adc2_buffers[done_idx], (uint16_t)total_samples, &b_min, &b_max, &b_imax);
    #else
    b_min = b_max = b_imax = 0;
    #endif

    /* Жёстко отбрасываем кадр, если в нём есть выброс > 12 бит (признак повреждения памяти) */
    if ((a_max > 4095u) || (b_max > 4095u)) {
        frame_overflow_drops++;
        // ОТКЛЮЧЕНО: printf тормозит @ 200Hz
        // printf("[ADC_DROP] spike>4095 idx=%lu Amax=%u Bmax=%u\r\n",
        //        (unsigned long)done_idx, (unsigned)a_max, (unsigned)b_max);
    }
    uint32_t spike_log_now = HAL_GetTick();
    adc_last_min[0] = a_min; adc_last_max[0] = a_max; adc_last_max_idx[0] = a_imax; adc_last_max_tick[0] = spike_log_now;
    adc_last_min[1] = b_min; adc_last_max[1] = b_max; adc_last_max_idx[1] = b_imax; adc_last_max_tick[1] = spike_log_now;
    
    // ОТКЛЮЧЕНО: spike logging тормозит @ 200Hz (каждый кадр с max>4000 вызывает ~200ms printf)
    #if 0
    /* Лимитированный лог крупных всплесков для локализации артефактов */
    if((a_max > 4000u) || (b_max > 4000u)){
        static uint32_t last_spike_log_ms = 0;
        if(spike_log_now - last_spike_log_ms > 500u){
            last_spike_log_ms = spike_log_now;
            uint16_t a_win[9] = {0}; uint16_t b_win[9] = {0};
            uint16_t ia0 = (a_imax>4)? (uint16_t)(a_imax-4) : 0;
            uint16_t ib0 = (b_imax>4)? (uint16_t)(b_imax-4) : 0;
            for(uint16_t k=0;k<9;k++){
                uint16_t ia = (uint16_t)(ia0 + k); if(ia < total_samples) a_win[k] = adc1_buffers[done_idx][ia];
                uint16_t ib = (uint16_t)(ib0 + k); if(ib < total_samples) b_win[k] = adc2_buffers[done_idx][ib];
            }
            printf("[ADC_SPIKE] idx=%lu A_max=%u@%u B_max=%u@%u samples=%lu Awin=%u,%u,%u,%u,%u,%u,%u,%u,%u Bwin=%u,%u,%u,%u,%u,%u,%u,%u,%u\r\n",
                   (unsigned long)done_idx, (unsigned)a_max, (unsigned)a_imax,
                   (unsigned)b_max, (unsigned)b_imax, (unsigned long)total_samples,
                   (unsigned)a_win[0],(unsigned)a_win[1],(unsigned)a_win[2],(unsigned)a_win[3],(unsigned)a_win[4],(unsigned)a_win[5],(unsigned)a_win[6],(unsigned)a_win[7],(unsigned)a_win[8],
                   (unsigned)b_win[0],(unsigned)b_win[1],(unsigned)b_win[2],(unsigned)b_win[3],(unsigned)b_win[4],(unsigned)b_win[5],(unsigned)b_win[6],(unsigned)b_win[7],(unsigned)b_win[8]);
            /* Проверим guard слова текущего буфера на случай утечки защитного шаблона в полезную область */
            printf("[ADC_SPIKE_GUARD] idx=%lu preA=%04x postA=%04x preB=%04x postB=%04x\r\n",
                   (unsigned long)done_idx,
                   (unsigned)adc_guard_pre(adc1_raw, done_idx)[0], (unsigned)adc_guard_post(adc1_raw, done_idx)[0],
                   (unsigned)adc_guard_pre(adc2_raw, done_idx)[0], (unsigned)adc_guard_post(adc2_raw, done_idx)[0]);
        }
    }
    #endif
    #endif  // minmax scan disabled

#if ADC_USB_STAGE_ENABLE
    memcpy(usb_stage_bufA, adc1_buffers[done_idx], total_samples * sizeof(uint16_t));
    memcpy(usb_stage_bufB, adc2_buffers[done_idx], total_samples * sizeof(uint16_t));
    adc_stage_crc_a = adc_crc16_le(usb_stage_bufA, (uint16_t)total_samples);
    adc_stage_crc_seq = frame_wr_seq + 1u;
    adc_flush_cache_for_buffer(usb_stage_bufA, total_samples);
    adc_flush_cache_for_buffer(usb_stage_bufB, total_samples);
#else
    adc_flush_cache_for_buffer(adc1_buffers[done_idx], total_samples);
#endif
    #if !DIAG_SINGLE_ADC1
    adc_flush_cache_for_buffer(adc2_buffers[done_idx], total_samples);
    #endif

    s_pair_ready_mask[done_idx] = READY_MASK_FULL;
    adc_mark_ready_and_publish(READY_MASK_FULL);
}

/* ========================================================================
   СТАРАЯ СХЕМА: DMA TC callback (теперь не используется для переключения)
   ======================================================================== */
void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef* hadc) {
    /* TC любого канала: ждём завершения обоих DMA, затем обрабатываем кадр. */
    if (!hadc) return;
    if (hadc->Instance == (s_adc1 ? s_adc1->Instance : NULL)) {
        adc_handle_tc(0x1u);
    } else if (hadc->Instance == (s_adc2 ? s_adc2->Instance : NULL)) {
        adc_handle_tc(0x2u);
    }
    return;

#if 0  /* СТАРАЯ ЛОГИКА (оставлено как справка, не используется) */
    
    /* В CIRCULAR mode HAL может снова включить HTIE при перезапуске - отключаем его.
       В CIRCULAR DMA автоматически перезапускается, но мы хотим только TC IRQ, не HT. */
    DMA_Stream_TypeDef *st_adc1 = (DMA_Stream_TypeDef*)hdma_adc1.Instance;
    st_adc1->CR &= ~((uint32_t)(1u<<3));  /* Отключаем HTIE каждый раз */
    
    if (hadc->Instance == (s_adc1 ? s_adc1->Instance : NULL)) {
    dma_full0++; dbg_dma1_full_count++;
#if DIAG_DMA_CALLBACK_LIMIT
    if(dbg_dma1_full_count > DIAG_DMA_CALLBACK_LIMIT){
        HAL_ADC_Stop_DMA(s_adc1);
        printf("[ADC][WARN] FULL limit reached -> stop DMA (half=%lu full=%lu)\r\n",
           (unsigned long)dbg_dma1_half_count,
           (unsigned long)dbg_dma1_full_count);
        return;
    }
#endif
    adc_last_full0_ms = HAL_GetTick();
    
    /* Один общий callback обслуживает ОБА канала: считаем, что по завершению DMA ADC1
       готов кадр и по ADC1, и по ADC2 с одним и тем же индексом. 
       КРИТИЧНО: СНАЧАЛА перезапускаем ADC+DMA, ПОТОМ обрабатываем done_idx! */
    uint32_t done_idx = s_next_ring_index;
    uint32_t next_idx = (s_next_ring_index + 1u) & (FIFO_FRAMES - 1u);
    uint32_t total_samples = (uint32_t)g_active_samples;
    
    /* ====== ПРИОРИТЕТ 1: RESTART ADC+DMA БЕЗ ЗАДЕРЖЕК ====== */
    /* В NORMAL mode после TC нужен Stop → Start цикл, но БЕЗ polling.
       Stop сбрасывает состояние HAL, Start конфигурирует новый буфер.
       HAL внутри проверяет флаги и не требует ожидания - это атомарные операции. */
    
    /* ADC1: Stop (мгновенный сброс HAL state) → Start (новый буфер) */
    HAL_ADC_Stop_DMA(s_adc1);
    HAL_StatusTypeDef st1 = HAL_ADC_Start_DMA(s_adc1, (uint32_t*)adc1_buffers[next_idx], total_samples);
    if (st1 != HAL_OK) {
        ADC_LOGF("[ADC][ERR] ADC1 Start_DMA failed: %d\r\n", st1);
    }
    
    #if !DIAG_SINGLE_ADC1
    /* ADC2: Stop → Start */
    HAL_ADC_Stop_DMA(s_adc2);
    HAL_StatusTypeDef st2 = HAL_ADC_Start_DMA(s_adc2, (uint32_t*)adc2_buffers[next_idx], total_samples);
    if (st2 != HAL_OK) {
        ADC_LOGF("[ADC][ERR] ADC2 Start_DMA failed: %d\r\n", st2);
    }
    #endif
    
    s_next_ring_index = next_idx;
    
    /* КРИТИЧНО: Используем ПРЕПОСЛЕДНИЙ буфер для маркеров!
       done_idx - это буфер, который только что завершился DMA TC.
       Но пока мы в прерывании, TIM15 @ 275kHz продолжает слать триггеры в ADC,
       который уже перезапущен на next_idx. Если обработка callback затянется,
       done_idx может быть частично перезаписан новыми данными АЦП.
       
       Поэтому пишем маркеры в (done_idx - 1), который точно не используется! */
    uint32_t marker_idx = (done_idx + FIFO_FRAMES - 1u) & (FIFO_FRAMES - 1u);  // done_idx - 1 с wrap
        
    /* Обрабатываем done_idx — публикуем в очередь USB */
    if (done_idx < FIFO_FRAMES) {
        /* Обновляем счётчики по каналу A */
        adc_ch_wr_seq[0]++;
        uint32_t backlogA = adc_ch_wr_seq[0] - adc_ch_rd_seq[0];
        if (backlogA > FIFO_FRAMES) {
            uint32_t excess = backlogA - FIFO_FRAMES;
            adc_ch_overflow_drops[0] += excess;
            adc_ch_rd_seq[0] += excess;
        }
        
        /* Invalidate cache перед чтением done_idx (DMA писал туда данные) */
        adc_invalidate_cache_for_buffer(adc1_buffers[done_idx], g_active_samples);
        
#if 0  // MARKERS DISABLED
        // DEBUG: Создаём "лестницу" маркеров в ПРЕПОСЛЕДНЕМ буфере (marker_idx)
        // Это безопасно — marker_idx гарантированно не используется ни DMA, ни USB
        adc_invalidate_cache_for_buffer(adc1_buffers[marker_idx], g_active_samples);
        for (uint16_t i = 0; i < g_active_samples; i += 100) {
            uint16_t value = (i / 100) * 500;  // 0, 500, 1000, 1500, 2000, ...
            if (value > 4095) value = 4095;
            adc1_buffers[marker_idx][i] = value;
        }
        adc_flush_cache_for_buffer(adc1_buffers[marker_idx], g_active_samples);
        
        // DEBUG: Проверка лестницы сразу после записи (каждую секунду)
        // ВНИМАНИЕ: Проверяем marker_idx, где реально записали маркеры!
        static uint32_t last_check_ms = 0;
        static uint32_t check_counter = 0;
        uint32_t now_ms = HAL_GetTick();
        if (now_ms - last_check_ms >= 1000) {
            last_check_ms = now_ms;
            check_counter++;
            // Проверяем marker_idx (препоследний буфер с маркерами)
            uint16_t s0 = adc1_buffers[marker_idx][0];
            uint16_t s100 = adc1_buffers[marker_idx][100];
            uint16_t s200 = adc1_buffers[marker_idx][200];
            uint16_t s300 = adc1_buffers[marker_idx][300];
            uint16_t s400 = adc1_buffers[marker_idx][400];
            printf("[MCU_TX#%lu] A marker_idx=%lu: [0]=%u [100]=%u [200]=%u [300]=%u [400]=%u\r\n", 
                   (unsigned long)check_counter, (unsigned long)marker_idx, s0, s100, s200, s300, s400);
            
            // DEBUG: Проверка лестницы B в том же marker_idx
            uint16_t s0_b = adc2_buffers[marker_idx][0];
            uint16_t s100_b = adc2_buffers[marker_idx][100];
            uint16_t s200_b = adc2_buffers[marker_idx][200];
            uint16_t s300_b = adc2_buffers[marker_idx][300];
            uint16_t s400_b = adc2_buffers[marker_idx][400];
            printf("[MCU_TX#%lu] B marker_idx=%lu: [0]=%u [100]=%u [200]=%u [300]=%u [400]=%u\r\n", 
                   (unsigned long)check_counter, (unsigned long)marker_idx, s0_b, s100_b, s200_b, s300_b, s400_b);
        }
#endif  // MARKERS DISABLED

        /* Обновляем счётчики по каналу B */
        adc_ch_wr_seq[1]++;
        uint32_t backlogB = adc_ch_wr_seq[1] - adc_ch_rd_seq[1];
        if (backlogB > FIFO_FRAMES) {
            uint32_t excess = backlogB - FIFO_FRAMES;
            adc_ch_overflow_drops[1] += excess;
            adc_ch_rd_seq[1] += excess;
        }
        
        adc_invalidate_cache_for_buffer(adc2_buffers[done_idx], g_active_samples);
        
#if 0  // MARKERS DISABLED
        // DEBUG: Аналогичная лестница для канала B в ПРЕПОСЛЕДНЕМ буфере (marker_idx)
        adc_invalidate_cache_for_buffer(adc2_buffers[marker_idx], g_active_samples);
        for (uint16_t i = 0; i < g_active_samples; i += 100) {
            uint16_t value = (i / 100) * 500;
            if (value > 4095) value = 4095;
            adc2_buffers[marker_idx][i] = value;
        }
        adc_flush_cache_for_buffer(adc2_buffers[marker_idx], g_active_samples);
        
        // Flush cache после записи маркеров для текущего done_idx (если нужно)
        adc_flush_cache_for_buffer(adc1_buffers[done_idx], g_active_samples);
        adc_flush_cache_for_buffer(adc2_buffers[done_idx], g_active_samples);
#endif  // MARKERS DISABLED

        /* Отмечаем пару как готовую и сразу публикуем */
        s_pair_ready_mask[done_idx] = 0x03u;
        adc_mark_ready_and_publish(0x03);
    }
} else if (hadc->Instance == (s_adc2 ? s_adc2->Instance : NULL)) {
        /* ADC2 callback НЕ ДОЛЖЕН вызываться при ADC2_DISABLE_DMA_IRQS=1
           Если всё же вызвался - это ошибка конфигурации */
        ADC_LOGF("[ADC][ERR] ADC2 callback unexpected! IRQ should be disabled\r\n");
    }
#endif  /* ОСНОВНОЙ DMA CALLBACK - ВКЛЮЧЁН */
}

/* Периодический вотчдог: если давно не было DMA Full от ADC1, считаем поток зависшим и мягко перезапускаем.
   Это устраняет зависания, когда цепочка ADC/DMA перестаёт генерировать события (см. STALL_WARN: ADC_IDLE). */
/* Беззнаковая разность тиков (учёт переполнения 32-битного HAL_GetTick()) */
static inline uint32_t tick_diff32(uint32_t now, uint32_t before){ return (now >= before) ? (now - before) : (0xFFFFFFFFu - before + 1u + now); }

/* Селективный перезапуск канала B полностью отключён в рабочей конфигурации.
   Исторически пытались мягко перезапускать только B, но это давало нестабильность
   и лишний шум в логах. Сейчас при проблемах с потоком полагаемся только на общий
   adc_stream_restart(NULL, NULL) по тайм-ауту A. Оставляем заглушку под #if 0
   на случай возврата к экспериментам. */
#if 0
static int adc_restart_channel_b(void){
    (void)g_active_samples;
    (void)s_next_ring_index;
    if(!s_adc2) return 0;
    ADC_LOGF("[ADC][WD] adc_restart_channel_b() called (EXPERIMENTAL)\r\n");
    return 0;
}
#endif

void adc_stream_watchdog(void)
{
    /* Локальный захват текущего тика (исключаем рассинхронизацию перед вызовом) */
    uint32_t now_ms = HAL_GetTick();
    if(s_adc1 == NULL || (!DIAG_SINGLE_ADC1 && s_adc2 == NULL)) return; /* ещё не инициализированы */
    if(adc_stream_static_mode_enabled()) return; /* статический режим: DMA не запускается */
    uint32_t lastA = adc_last_full0_ms;
    uint32_t lastB = adc_last_full1_ms;
    if(lastA == 0) return; /* поток ещё не стартовал (нет ни одного полного завершения) */

    /* Защита от ложных "будущих" меток lastA/lastB (гонка или порча памяти):
       если метка немного впереди now ( <100000 мс ), считаем dt=0, игнорируем. */
    if(lastA > now_ms){ uint32_t ahead = lastA - now_ms; if(ahead < 100000u){ lastA = now_ms; } }
    if(lastB > now_ms){ uint32_t ahead = lastB - now_ms; if(ahead < 100000u){ lastB = now_ms; } }

    const uint32_t ADC_WD_TIMEOUT_MS = 500u; /* общий таймаут полного простоя */
    uint32_t dtA = tick_diff32(now_ms, lastA);
    uint32_t dtB_now = (lastB==0) ? 0 : tick_diff32(now_ms, lastB);

    /* Диагностика NDTR и эскалация при застывшем NDTRB (DMA не стартовал) */
    static uint32_t last_dbg_ms = 0;
    static uint32_t last_summary_ms = 0;   /* для агрегированной статистики раз в 10 сек */
    static uint32_t last_ndtrB = 0xFFFFFFFFu;
    static uint32_t last_ndtrB_change_ms = 0;
    static uint8_t  b_stuck_strikes = 0; /* фиксируем повторные застои Б, чтобы не спамить рестартами */
    
    /* Агрегированная статистика за 10 секунд */
    if(now_ms - last_summary_ms >= 10000u){
        last_summary_ms = now_ms;
        extern volatile uint32_t frame_wr_seq;  /* Общий счётчик завершённых пар A+B */
        static uint32_t prev_wr_seq = 0;
        uint32_t bufs_done = (frame_wr_seq > prev_wr_seq) ? (frame_wr_seq - prev_wr_seq) : 0;
        prev_wr_seq = frame_wr_seq;
        float bufHz = (float)bufs_done / 10.0f;

        /* Раздельная статистика по каналам A/B */
        static uint32_t prev_ch_wr[2] = {0,0};
        uint32_t chA_done = (adc_ch_wr_seq[0] > prev_ch_wr[0]) ? (adc_ch_wr_seq[0] - prev_ch_wr[0]) : 0;
        uint32_t chB_done = (adc_ch_wr_seq[1] > prev_ch_wr[1]) ? (adc_ch_wr_seq[1] - prev_ch_wr[1]) : 0;
        prev_ch_wr[0] = adc_ch_wr_seq[0];
        prev_ch_wr[1] = adc_ch_wr_seq[1];
        float hzA = (float)chA_done / 10.0f;
        float hzB = (float)chB_done / 10.0f;

        ADC_LOGF("[ADC][STAT_10s] buffers=%lu (%.1f Hz) restarts=%lu/%lu dtA=%lums dtB=%lums samples=%u\r\n",
                 (unsigned long)bufs_done, bufHz,
                 (unsigned long)adc_restart_attempts, (unsigned long)adc_restart_success,
                 (unsigned long)dtA, (unsigned long)((lastB==0)?0:tick_diff32(now_ms,lastB)),
                 (unsigned)g_active_samples);
        ADC_LOGF("[ADC][STAT_CH] A=%lu (%.1f Hz) B=%lu (%.1f Hz) pairs=%lu (%.1f Hz)\r\n",
                 (unsigned long)chA_done, hzA,
                 (unsigned long)chB_done, hzB,
                 (unsigned long)bufs_done, bufHz);
    }
    
    if(now_ms - last_dbg_ms >= 250u){
        last_dbg_ms = now_ms;
        DMA_Stream_TypeDef *dma1_stream = (DMA_Stream_TypeDef*)hdma_adc1.Instance;
        DMA_Stream_TypeDef *dma2_stream = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
        uint32_t ndtrA = dma1_stream->NDTR;
        uint32_t ndtrB = dma2_stream->NDTR;
        /* Убрали частый лог WDDBG — данные теперь агрегируются раз в 10 сек */
        /* Отслеживаем изменения NDTRB */
        if(ndtrB != last_ndtrB){ last_ndtrB = ndtrB; last_ndtrB_change_ms = now_ms; b_stuck_strikes = 0; }
        else {
            uint32_t stuck_ms = now_ms - last_ndtrB_change_ms;
            if(stuck_ms > 200u && ndtrB == (uint32_t)g_active_samples && dtA < 400u){
                b_stuck_strikes++;
                ADC_LOGF("[ADC][WD] CH_B DMA not advancing: ndtrB=%u unchanged %lums (strike=%u). dtA=%lu dtB=%lu -> restart BOTH\r\n",
                         (unsigned)ndtrB, (unsigned long)stuck_ms, (unsigned)b_stuck_strikes, (unsigned long)dtA, (unsigned long)dtB_now);
                dump_b_path_regs(ndtrA, ndtrB);
                adc_restart_attempts++;
                if(adc_stream_restart(NULL, NULL) == HAL_OK){
                    adc_restart_success++;
                    adc_last_full0_ms = adc_last_full1_ms = HAL_GetTick();
                    b_stuck_strikes = 0;
                } else {
                    ADC_LOGF("[ADC][WD] full restart after B-DMA-stuck FAILED\r\n");
                }
                return;
            }
        }
    }

    if(dtA > ADC_WD_TIMEOUT_MS){
        adc_restart_attempts++;
        ADC_LOGF("[ADC][WD] No DMA Full A for %lu ms -> restart BOTH\r\n", (unsigned long)dtA);
        if(adc_stream_restart(NULL, NULL) == HAL_OK){
            adc_restart_success++; adc_last_full0_ms = HAL_GetTick(); adc_last_full1_ms = adc_last_full0_ms; /* синхронизация */
        } else {
            ADC_LOGF("[ADC][WD] full restart FAILED\r\n");
        }
        return; /* после общего рестарта не выполняем частный контроль B */
    }
     /* Дополнительный селективный контроль канала B отключён как
         нестабильный и шумный. Если когда-нибудь понадобится вернуть эксперименты,
         здесь можно восстановить логику dtB/dtA и выборочные рестарты. */
}

// Тестовые функции удалены - используем только реальный ADC+DMA