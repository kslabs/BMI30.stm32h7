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
    uint16_t *buf = ch2 ? adc2_buffers[index] : adc1_buffers[index];
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
#define DIAG_DISABLE_ADC_DMA 0   /* включаем DMA */
#endif

/* Запускать только ADC1 (вторая DMA не стартует) — 0 = использовать оба АЦП.
    Для нормальной работы нужно 0; установить 1 только при диагностике. */
#ifndef DIAG_SINGLE_ADC1
#define DIAG_SINGLE_ADC1 0
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
#define ADC2_DISABLE_DMA_IRQS 1  /* ОТКЛЮЧАЕМ IRQ ADC2: обрабатываем оба канала в прерывании ADC1 для минимальной нагрузки */
#endif

/* Включить крайне редкий вывод первых 8 значений из буфера ADC2 прямо в ISR ADC1.
    ВНИМАНИЕ: Любой printf в ISR может привести к стопору потока, поэтому по умолчанию ВЫКЛ. */
#ifndef ADC2_DBG_FIRST8
#define ADC2_DBG_FIRST8 0
#endif

static volatile uint32_t dbg_dma1_half_count = 0, dbg_dma1_full_count = 0;
extern DMA_HandleTypeDef hdma_adc1; /* из auto-generated кода */
extern DMA_HandleTypeDef hdma_adc2;

// --- Профили ---
static const adc_stream_profile_t g_profiles[ADC_PROFILE_COUNT] = {
    { .samples_per_buf = 1360, .buf_rate_hz = 200, .fs_hz = 1360u * 200u }, // A: legacy large frame @200Hz
    { .samples_per_buf = 912,  .buf_rate_hz = 300, .fs_hz = 912u  * 300u }, // B: default balanced (higher pair rate)
    { .samples_per_buf = 944,  .buf_rate_hz = 300, .fs_hz = 944u  * 300u }, // C: high Fs
    { .samples_per_buf = 976,  .buf_rate_hz = 300, .fs_hz = 976u  * 300u }, // D: max Fs (near USB limit test)
    { .samples_per_buf = 680,  .buf_rate_hz = 400, .fs_hz = 680u  * 400u }, // E: HIGH-FPS (same Fs≈272kHz, smaller frames faster cadence)
};
static uint8_t g_active_profile = ADC_PROFILE_B_DEFAULT;  // Дефолт теперь профиль B (300 Hz, 912 samples)
static uint16_t g_active_samples = 912; // runtime N для профиля B

// Выравнивание по линии кэша для снижения побочных эффектов DCache (32 байт)
__attribute__((aligned(32))) uint16_t adc1_buffers[FIFO_FRAMES][MAX_FRAME_SAMPLES];
__attribute__((aligned(32))) uint16_t adc2_buffers[FIFO_FRAMES][MAX_FRAME_SAMPLES];

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

// Debug: DMA event counters
static volatile uint32_t dma_half0 = 0, dma_full0 = 0, dma_half1 = 0, dma_full1 = 0;

// Индекс следующего буфера в кольце, который будет назначен в свободный банк DMA (DBM)
volatile uint32_t s_next_ring_index = 0; // всегда < FIFO_FRAMES

ADC_HandleTypeDef* s_adc1 = NULL;
ADC_HandleTypeDef* s_adc2 = NULL;

/* Новый механизм: считаем кадр готовым (frame_wr_seq++) только когда ОДИНАКОВЫЙ индекс
   в кольце завершён у обоих АЦП. Для этого отмечаем готовность по индексам:
   bit0=ADC1 complete, bit1=ADC2 complete. Продвигаем «готовый» индекс строго по порядку. */
static volatile uint8_t s_pair_ready_mask[FIFO_FRAMES];
static volatile uint32_t s_pair_ready_idx = 0; /* следующий индекс, который ждём к публикации */

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
    while (s_pair_ready_mask[s_pair_ready_idx] == 0x3u) {
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

// Публичные функции профиля
uint8_t adc_stream_get_profile(void) { return g_active_profile; }
uint16_t adc_stream_get_active_samples(void) { return g_active_samples; }
uint16_t adc_stream_get_buf_rate(void) { return g_profiles[g_active_profile].buf_rate_hz; }
uint32_t adc_stream_get_fs(void) { return g_profiles[g_active_profile].fs_hz; }

static HAL_StatusTypeDef adc_stream_apply_profile(void) {
    if (!s_adc1 || !s_adc2) {
        ADC_LOGF("[ADC][APPLY_PROFILE] ERROR: s_adc1/s_adc2 не инициализированы!\r\n");
        return HAL_ERROR;
    }
    uint32_t total_samples = (uint32_t)g_active_samples;
    ADC_LOGF("[ADC][APPLY_PROFILE] profile=%u samples=%u\r\n", (unsigned)g_active_profile, (unsigned)g_active_samples);
    // Остановить DMA перед запуском с новым размером
    HAL_ADC_Stop_DMA(s_adc1);
    HAL_ADC_Stop_DMA(s_adc2);
    ADC_LOGF("[ADC][APPLY_PROFILE] DMA остановлен, подготовка к запуску\r\n");
    frame_wr_seq = frame_rd_seq = 0;
    frame_overflow_drops = 0;
    frame_backlog_max = 0;
    /* Сброс независимых счётчиков по каналам */
    adc_ch_wr_seq[0] = adc_ch_wr_seq[1] = 0;
    adc_ch_rd_seq[0] = adc_ch_rd_seq[1] = 0;
    adc_ch_overflow_drops[0] = adc_ch_overflow_drops[1] = 0;
    adc_ch_zero_buffers[0] = adc_ch_zero_buffers[1] = 0; // ДИАГНОСТИКА: сброс счётчиков нулевых буферов
    /* Сброс новой синхронизации пар */
    for (unsigned i = 0; i < FIFO_FRAMES; ++i) s_pair_ready_mask[i] = 0;
    s_pair_ready_idx = 0;
    s_next_ring_index = 0;  // Начинаем с buf[0], после TC перейдём на buf[1], потом buf[2]... buf[31]→buf[0]
    #if DIAG_DISABLE_ADC_DMA
        ADC_LOGF("[ADC][DIAG] DMA start suppressed (DIAG_DISABLE_ADC_DMA=1) total_samples=%lu\r\n", (unsigned long)total_samples);
        return HAL_OK;
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
            /* При включённом ADC2_DISABLE_DMA_IRQS — прерывания отключаются (не рекомендуется) */
            do {
                DMA_Stream_TypeDef *st2 = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
                /* CR: DMEIE(1) | TEIE(2) | HTIE(3) | TCIE(4) */
                st2->CR &= ~((uint32_t)(1u<<1) | (uint32_t)(1u<<2) | (uint32_t)(1u<<3) | (uint32_t)(1u<<4));
                /* FCR: FEIE(7) */
                st2->FCR &= ~((uint32_t)(1u<<7));
                /* NVIC: Полностью выключаем IRQ DMA1_Stream1 */
                HAL_NVIC_DisableIRQ(DMA1_Stream1_IRQn);
            } while (0);
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
            /* Для ADC2 все IRQ уже выключены выше при ADC2_DISABLE_DMA_IRQS=1 */
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
    #endif
    return HAL_OK;
}

int adc_stream_set_profile(uint8_t prof_id) {
    if (prof_id >= ADC_PROFILE_COUNT) return -1;
    if (prof_id == g_active_profile) return 0; // уже
    g_active_profile = prof_id;
    g_active_samples = g_profiles[prof_id].samples_per_buf;
    if (s_adc1 && s_adc2) {
        if (adc_stream_apply_profile() != HAL_OK) return -2;
    }
    return 0;
}

void adc_stream_init(void) {
    frame_wr_seq = frame_rd_seq = 0;
    frame_overflow_drops = 0;
    frame_backlog_max = 0;
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
    
    ADC_LOGF("[ADC][CALIB] Starting ADC2 calibration...\r\n");
    if (HAL_ADCEx_Calibration_Start(a2, ADC_CALIB_OFFSET, ADC_SINGLE_ENDED) != HAL_OK) {
        ADC_LOGF("[ADC][CALIB] ADC2 calibration FAILED!\r\n");
        return HAL_ERROR;
    }
    ADC_LOGF("[ADC][CALIB] ADC2 calibration OK\r\n");
    
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
uint8_t adc_get_frame_ch(uint8_t ch, uint16_t **buf, uint16_t *samples) {
    if (ch > 1 || !buf || !samples) return 0;
    __disable_irq();
    if (adc_ch_rd_seq[ch] == adc_ch_wr_seq[ch]) {
        __enable_irq();
        return 0; // нет новых
    }
    uint32_t seq = adc_ch_rd_seq[ch]++;
    __enable_irq();
    uint32_t index = seq & (FIFO_FRAMES - 1u);
    *buf = (ch==0) ? adc1_buffers[index] : adc2_buffers[index];
    *samples = g_active_samples;
    
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
    *ch1 = adc1_buffers[index];
    *ch2 = adc2_buffers[index];
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
void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef* hadc) {
    /* В режиме DMA_NORMAL с ручным перезапуском Half Transfer не используется.
       Игнорируем этот callback полностью. */
    (void)hadc;
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

void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef* hadc) {
    static uint32_t callback_entry_count = 0;  // ДИАГНОСТИКА: счётчик входов в callback
    callback_entry_count++;
    
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
        /* Определим, какой банк у ADC1 только что завершился, и отметим индекс кольца как готовый для CH0. */
        do {
            DMA_Stream_TypeDef *st1 = (DMA_Stream_TypeDef*)hdma_adc1.Instance;
            uint32_t cr1 = st1->CR;
            uint32_t ct1 = (cr1 >> 19) & 1u; /* текущий таргет */
            /* завершился банк противоположный текущему */
            uint32_t done_addr1 = ct1 ? st1->M0AR : st1->M1AR;
            uint32_t done_idx1 = adc_addr_to_index(done_addr1, adc1_buffers);
            if (done_idx1 < FIFO_FRAMES) { s_pair_ready_mask[done_idx1] |= 0x01u; }

            // СИНХРОННАЯ публикация обоих каналов A и B (триггер TIM2 200Hz запускает оба ADC одновременно)
            if (done_idx1 < FIFO_FRAMES) {
                // Канал A
                adc_ch_wr_seq[0]++;
                uint32_t backlogA = adc_ch_wr_seq[0] - adc_ch_rd_seq[0];
                if (backlogA > FIFO_FRAMES) {
                    uint32_t excess = backlogA - FIFO_FRAMES;
                    adc_ch_overflow_drops[0] += excess;
                    adc_ch_rd_seq[0] += excess; // drop oldest
                }
                
                #if !DIAG_SINGLE_ADC1
                // Канал B обрабатываем СИНХРОННО с A (оба ADC от TIM2 200Hz)
                // Определяем индекс буфера для ADC2 (синхронен с ADC1)
                DMA_Stream_TypeDef *st2 = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
                uint32_t cr2 = st2->CR;
                uint32_t ct2 = (cr2 >> 19) & 1u;
                uint32_t done_addr2 = ct2 ? st2->M0AR : st2->M1AR;
                uint32_t done_idx2 = adc_addr_to_index(done_addr2, adc2_buffers);
                
                if (done_idx2 < FIFO_FRAMES) {
                    adc_ch_wr_seq[1]++;
                    uint32_t backlogB = adc_ch_wr_seq[1] - adc_ch_rd_seq[1];
                    if (backlogB > FIFO_FRAMES) {
                        uint32_t excess = backlogB - FIFO_FRAMES;
                        adc_ch_overflow_drops[1] += excess;
                        adc_ch_rd_seq[1] += excess;
                    }
                    s_pair_ready_mask[done_idx2] |= 0x02u;  // Помечаем канал B готовым
                    dma_full1++;  // Увеличиваем счётчик B синхронно с A
                    #if ADC2_DBG_FIRST8
                    /* Диагностика: первые 8 значений буфера канала B (ОСТОРОЖНО: печать в ISR!) */
                    do {
                        uint16_t *bbuf = adc2_buffers[done_idx2];
                        uint8_t all_zero = 1;
                        for (unsigned i = 0; i < 8; ++i) { if (bbuf[i] != 0) { all_zero = 0; break; } }
                        static uint32_t dbg_print_ctr = 0; dbg_print_ctr++;
                        if (dbg_print_ctr % 64 == 1) { /* печатать не каждый кадр, чтобы не перегружать UART */
                            if (all_zero) {
                                ADC_LOGF("[ADC][DBG] CH2 first8 ZERO idx=%lu total_wr=%lu\r\n", (unsigned long)done_idx2, (unsigned long)adc_ch_wr_seq[1]);
                            } else {
                                ADC_LOGF("[ADC][DBG] CH2 first8 idx=%lu v=%u,%u,%u,%u,%u,%u,%u,%u total_wr=%lu\r\n", (unsigned long)done_idx2,
                                    bbuf[0], bbuf[1], bbuf[2], bbuf[3], bbuf[4], bbuf[5], bbuf[6], bbuf[7], (unsigned long)adc_ch_wr_seq[1]);
                            }
                        }
                    } while(0);
                    #endif
                }
                #endif
            }

            /* После TC прерывания перезапускаем DMA на следующий буфер в кольце.
               В DMA_NORMAL mode DMA останавливается после TC.
               Напрямую переназначаем адрес и перезапускаем DMA stream. */
            
            uint32_t next_idx = (s_next_ring_index + 1u) & (FIFO_FRAMES - 1u);
            uint32_t total_samples = (uint32_t)g_active_samples;
            
            /* Прямое управление DMA без HAL (быстрее и безопаснее в ISR) */
            DMA_Stream_TypeDef *dma1_stream = (DMA_Stream_TypeDef*)hdma_adc1.Instance;
            
            // Отключаем DMA stream
            dma1_stream->CR &= ~DMA_SxCR_EN;
            // Ждём пока DMA остановится
            while(dma1_stream->CR & DMA_SxCR_EN);
            
            // Переназначаем адрес памяти на новый буфер
            dma1_stream->M0AR = (uint32_t)adc1_buffers[next_idx];
            // Перезагружаем счётчик
            dma1_stream->NDTR = total_samples;
            // Очищаем флаги прерываний для STM32H7
            __HAL_DMA_CLEAR_FLAG(&hdma_adc1, __HAL_DMA_GET_TC_FLAG_INDEX(&hdma_adc1));
            __HAL_DMA_CLEAR_FLAG(&hdma_adc1, __HAL_DMA_GET_HT_FLAG_INDEX(&hdma_adc1));
            __HAL_DMA_CLEAR_FLAG(&hdma_adc1, __HAL_DMA_GET_TE_FLAG_INDEX(&hdma_adc1));
            // Включаем DMA stream заново
            dma1_stream->CR |= DMA_SxCR_EN;
            
            #if !DIAG_SINGLE_ADC1
            /* Аналогично для ADC2 */
            DMA_Stream_TypeDef *dma2_stream = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
            dma2_stream->CR &= ~DMA_SxCR_EN;
            while(dma2_stream->CR & DMA_SxCR_EN);
            dma2_stream->M0AR = (uint32_t)adc2_buffers[next_idx];
            dma2_stream->NDTR = total_samples;
            __HAL_DMA_CLEAR_FLAG(&hdma_adc2, __HAL_DMA_GET_TC_FLAG_INDEX(&hdma_adc2));
            __HAL_DMA_CLEAR_FLAG(&hdma_adc2, __HAL_DMA_GET_HT_FLAG_INDEX(&hdma_adc2));
            __HAL_DMA_CLEAR_FLAG(&hdma_adc2, __HAL_DMA_GET_TE_FLAG_INDEX(&hdma_adc2));
            dma2_stream->CR |= DMA_SxCR_EN;
            #endif
            
            s_next_ring_index = next_idx;
        } while(0);
        /* Попробуем опубликовать готовые подряд пары (0x03 = оба канала A|B готовы синхронно) */
        adc_mark_ready_and_publish(0x03);
    } else if (hadc->Instance == (s_adc2 ? s_adc2->Instance : NULL)) {
        /* ADC2 callback НЕ ДОЛЖЕН вызываться при ADC2_DISABLE_DMA_IRQS=1
           Если всё же вызвался - это ошибка конфигурации */
        ADC_LOGF("[ADC][ERR] ADC2 callback unexpected! IRQ should be disabled\r\n");
    }
}