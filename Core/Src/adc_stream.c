/* Прежде чем использовать типы/API — подключаем необходимые заголовки */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include "main.h"
#include "adc_stream.h"

/* Управление логированием этого модуля: по умолчанию выключено, чтобы не спамить из ISR */
#ifndef ADC_LOG_ENABLE
#define ADC_LOG_ENABLE 0
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
#define ADC2_DISABLE_DMA_IRQS 0
#endif

static volatile uint32_t dbg_dma1_half_count = 0, dbg_dma1_full_count = 0;
extern DMA_HandleTypeDef hdma_adc1; /* из auto-generated кода */
extern DMA_HandleTypeDef hdma_adc2;

// --- Профили ---
static const adc_stream_profile_t g_profiles[ADC_PROFILE_COUNT] = {
    { .samples_per_buf = 1360, .buf_rate_hz = 200, .fs_hz = 1360u * 200u }, // A: 200 Hz, 1360 samples
    { .samples_per_buf = 912, .buf_rate_hz = 300, .fs_hz = 912u * 300u },  // B default
    { .samples_per_buf = 944, .buf_rate_hz = 300, .fs_hz = 944u * 300u },  // C high
    { .samples_per_buf = 976, .buf_rate_hz = 300, .fs_hz = 976u * 300u },  // D max
};
static uint8_t g_active_profile = ADC_PROFILE_B_DEFAULT;
static uint16_t g_active_samples = 912; // runtime N

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
    /* Сброс новой синхронизации пар */
    for (unsigned i = 0; i < FIFO_FRAMES; ++i) s_pair_ready_mask[i] = 0;
    s_pair_ready_idx = 0;
    s_next_ring_index = 2 % FIFO_FRAMES; // M0->buf0, M1->buf1 уже заняты при старте; начнём с 2
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
        /* Включаем Double-Buffer Mode (DBM) и задаём второй банк M1 на buf[1] */
        {
            DMA_Stream_TypeDef *st = (DMA_Stream_TypeDef*)hdma_adc1.Instance;
            st->M1AR = (uint32_t)adc1_buffers[1];
            st->CR  |= (uint32_t)(1u<<18); /* DBM */
            /* Отключаем HTIE (half) — оставляем только TC */
            st->CR &= ~((uint32_t)(1u<<3));
        }
        #if !DIAG_SINGLE_ADC1
        {
            DMA_Stream_TypeDef *st = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
            st->M1AR = (uint32_t)adc2_buffers[1];
            st->CR  |= (uint32_t)(1u<<18); /* DBM */
            /* Для ADC2 все IRQ уже выключены выше */
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
    out->active_samples = g_active_samples;
    out->reserved = 0;
}

// Weak hook (can be overridden in higher-level module, e.g. USB)
void __attribute__((weak)) adc_stream_on_new_frames(uint32_t frames_added) { (void)frames_added; }

// --- HAL callbacks ---
void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef* hadc) {
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
}

void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef* hadc) {
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

            /* Продвинем адрес свободного банка DMA на следующий слот кольца — для ADC1 и ADC2.
               В DBM разрешено писать в неактивный банк: определяем по биту CT (CR[19]). */
            uint32_t idx = s_next_ring_index; // выбрать следующий буфер для назначения в свободный банк
            if (idx >= FIFO_FRAMES) idx &= (FIFO_FRAMES-1u);
            if (cr1 & (1u<<19)) {
                /* CT=1 => сейчас активен M1, значит завершился M0 -> переадресуем M0 на следующий */
                st1->M0AR = (uint32_t)adc1_buffers[idx];
            } else {
                /* CT=0 => активен M0, завершился M1 */
                st1->M1AR = (uint32_t)adc1_buffers[idx];
            }
            #if !DIAG_SINGLE_ADC1
            DMA_Stream_TypeDef *st2 = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
            uint32_t cr2 = st2->CR;
            if (cr2 & (1u<<19)) {
                st2->M0AR = (uint32_t)adc2_buffers[idx];
            } else {
                st2->M1AR = (uint32_t)adc2_buffers[idx];
            }
            #endif
            s_next_ring_index = (idx + 1u) & (FIFO_FRAMES - 1u);
        } while(0);
        /* Попробуем опубликовать готовые подряд пары */
        adc_mark_ready_and_publish(0x01);
    } else if (hadc->Instance == (s_adc2 ? s_adc2->Instance : NULL)) {
        dma_full1++;
        adc_last_full1_ms = HAL_GetTick();
        /* Определим, какой банк у ADC2 завершился, отметим готовность для CH1, и попробуем опубликовать пару */
        DMA_Stream_TypeDef *st2 = (DMA_Stream_TypeDef*)hdma_adc2.Instance;
        uint32_t cr2 = st2->CR;
        uint32_t ct2 = (cr2 >> 19) & 1u;
        uint32_t done_addr2 = ct2 ? st2->M0AR : st2->M1AR;
        uint32_t done_idx2 = adc_addr_to_index(done_addr2, adc2_buffers);
        if (done_idx2 < FIFO_FRAMES) { s_pair_ready_mask[done_idx2] |= 0x02u; }
        adc_mark_ready_and_publish(0x02);
    }
}