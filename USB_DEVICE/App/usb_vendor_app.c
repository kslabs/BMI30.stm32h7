/* Vendor streaming application (cleaned: duplicates removed) */
#include <stdint.h>
#include "usbd_cdc_custom.h"
#include "usb_device.h"
#include "usbd_core.h"
#include "usb_vendor_app.h"
#include <string.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include "adc_stream.h"
#include "main.h"
#include <stddef.h> /* offsetof для отладочного вывода */
#include "stm32h7xx_hal.h" /* для SCB_InvalidateDCache_by_Addr (cache coherency fix) */
/* Для дублирования фрагментов кадров в CDC (Virtual COM) */
#include "usbd_cdc_if.h"
#include "usbd_cdc_custom.h" /* для USBD_VND_RequestSoftReset/DeepReset (объявления находятся в .c) */
#include "vnd_testgen.h"

/* Forward declaration (реализация ниже) для предотвращения implicit-function-warning при раннем вызове */
void vnd_diag_log_possible_stall(void);
static void cdc_logf(const char *fmt, ...);

/* Управление дублированием данных кадров в CDC (COM-порт):
 *  0 — отключено (оставляем только события START/STOP и 1 Гц статистику)
 *  1 — включено (превью первых 64 сэмплов раз в ≤10 Гц)
 */
#ifndef VND_CDC_PREVIEW_ENABLE
#define VND_CDC_PREVIEW_ENABLE 0
#endif

#ifndef VND_DIAG_BUFFER_ID_MODE
#define VND_DIAG_BUFFER_ID_MODE 1  /* Режим отладки: использовать ID буфера вместо seq */
#endif

#ifndef VND_ENABLE_LOG
#define VND_ENABLE_LOG 0 /* ОТКЛЮЧЕНО: логи засоряют терминал */
#endif
#if VND_ENABLE_LOG
#define VND_LOG(...) do { printf("[VND] " __VA_ARGS__); printf("\r\n"); } while(0)
#else
#define VND_LOG(...) do{}while(0)
#endif

extern USBD_HandleTypeDef hUsbDeviceHS;

/* В диагностическом режиме упростим логику: отправлять только A-кадр (без обязательного B).
    Это помогает подтвердить транспорт USB IN и исключает потенциальные блокировки на pending_B. */
#ifndef VND_DIAG_SEND_A_ONLY
#define VND_DIAG_SEND_A_ONLY 0
#endif

/* Полностью отключить тестовые кадры (TEST) и связанную с ними
    стартовую/keepalive логику, чтобы исключить гонки и зависания EP
    на некоторых хостах. При включении — начинаем сразу с A/B. */
#ifndef VND_DISABLE_TEST
#define VND_DISABLE_TEST 1
#endif

/* Команды */
#define VND_CMD_START_STREAM   0x20u
#define VND_CMD_STOP_STREAM    0x21u
#define VND_CMD_DEVICE_RESET   0x22u  /* ПОЛНЫЙ RESET MCU (для отладки) */
#define VND_CMD_GET_STATUS     0x30u
/* Диагностический статус: отправить STAT немедленно даже при pending_B (для отладки зависаний) */
#define VND_CMD_GET_STATUS_IMM 0x31u
/* Отладка фаз/буферов: инверсия тестового выхода PA2 (TIM2_CH3) */
#define VND_CMD_TOGGLE_TIM2CH3_INV 0x32u
/* ДОБАВЛЕНО: управление окнами/частотой */
#define VND_CMD_SET_WINDOWS    0x10u /* payload: start0,len0,start1,len1 (LE, u16) */
#define VND_CMD_SET_BLOCK_HZ   0x11u /* payload: u16 hz (20..100) или 0xFFFF=макс (100) */
/* Новая команда: установка ограничения числа выборок на канал в рабочем кадре */
#define VND_CMD_SET_TRUNC_SAMPLES 0x16u /* payload: u16 samples (0=отключить усечение) */
/* Новая команда: явная установка samples_per_frame для управления FPS (пара A+B ≈ Fs/samples) */
#define VND_CMD_SET_FRAME_SAMPLES 0x17u /* payload: u16 samples_per_frame (на канал) */
/* Новый режим: асинхронная передача A/B — без строгого чередования A→B; какой готов, тот и уходит */
#define VND_CMD_SET_ASYNC_MODE   0x18u /* payload: u8 mode (0=pairing/strict A->B, 1=async A/B independent) */
/* Новый режим выбора каналов: 0=A-only, 1=B-only, 2=both */
#define VND_CMD_SET_CHMODE       0x19u /* payload: u8 mode (0=A-only, 1=B-only, 2=both) */

/* Параметры */
#define VND_DEFAULT_TEST_SAMPLES   80u
#define VND_DEBUG_FORCE_STAT_INTERVAL_MS 200u // было 100
#define VND_DEBUG_RAW_STAT_INTERVAL_MS   100u

/* ---------------- Глобальные переменные состояния (централизовано) ---------------- */
/* Видимая снаружи (CDC) метка стриминга */
volatile uint8_t streaming = 0;
/* Последовательность пар (инкремент только после успешного завершения B) */
volatile uint32_t stream_seq = 0;
/* Фактически зафиксированный размер кадров и ожидаемый размер байт */
volatile uint16_t cur_samples_per_frame = 0;
volatile uint16_t cur_expected_frame_size = 0;
/* Служебные отметки старта/ошибок */
volatile uint32_t start_cmd_ms = 0;
volatile uint32_t vnd_last_error = 0;
/* Отложенный (main-loop) перезапуск ADC/DMA по команде START.
    Важно: USBD_VND_DataReceived может вызываться из USB IRQ, поэтому тяжёлые действия
    (останов/запуск DMA, cache maintenance) выполняем в Vendor_Stream_Task(). */
static volatile uint8_t vnd_adc_restart_request = 0;
/* Флаги канала передачи */
static volatile uint8_t  vnd_ep_busy = 0;     /* EP IN занят */
static volatile uint8_t  vnd_inflight = 0;    /* есть незавершённая передача */
/* Тестовый кадр (разрешён один раз на START, для keepalive) */
static volatile uint8_t  test_sent = 0;
static volatile uint8_t  test_in_flight = 0;
/* Отладочные счётчики/статусы */
volatile uint32_t dbg_produced_seq = 0;
volatile uint32_t dbg_sent_seq_adc0 = 0;
volatile uint32_t dbg_sent_seq_adc1 = 0;
volatile uint32_t dbg_partial_frame_abort = 0;
volatile uint32_t dbg_size_mismatch = 0;
volatile uint32_t dbg_resend_blocked = 0;
/* (Определения vnd_diag_prepare_pair и vnd_diag_try_tx перенесены ниже, после объявлений типов/буферов) */
static volatile uint32_t dbg_tx_attempt = 0;
static volatile uint32_t dbg_tx_reject = 0;
static volatile uint32_t dbg_tx_sent = 0;
static volatile uint32_t dbg_status_sent = 0;
volatile uint32_t dbg_sent_ch0_total = 0; // новые счётчики по каналам
volatile uint32_t dbg_sent_ch1_total = 0;
/* Глобальный счётчик завершений передачи (используется в статусе и диагностике) */
volatile uint32_t dbg_tx_cplt = 0;
/* Следующая метка последовательности для назначения готовящимся парам (может опережать stream_seq,
   который инкрементируется только по завершении B). */
static volatile uint32_t next_seq_to_assign = 0;

/* Добавлено: счётчик ошибок и отметка последнего TXCPLT */
static volatile uint32_t vnd_error_counter = 0;      /* ++ при BUSY/REJECT */
static volatile uint32_t vnd_last_txcplt_ms = 0;      /* время последнего успешного завершения передачи */
/* Счётчик переданных семплов (оба канала суммарно) */
static volatile uint64_t vnd_total_tx_samples = 0ULL;
/* Новые маркеры состояния запуска потока */

/* Режим смягчения команды DEVICE_RESET: вместо NVIC_SystemReset выполнить мягкое USB восстановление
    через флаг need_recovery, если включено. Это предотвращает полное исчезновение устройства с шины
    при частых запросах 0x22 и снижает влияние на отладку. */
#ifndef VND_DEVICE_RESET_SOFT_ONLY
/* ВРЕМЕННО: разрешаем полный аппаратный reset по команде 0x22 (через флаг need_hard_reset в main) */
#define VND_DEVICE_RESET_SOFT_ONLY 0
#endif

/* Флаг восстановления (определён в main.c) */
extern volatile uint8_t need_recovery;
static volatile uint8_t  vnd_pending_init = 0;  /* 1 после START пока нет ни одного кадра A/B */
static volatile uint8_t  vnd_stream_active = 0; /* 1 после первой успешной передачи A или B */
/* Init-pump диагностика */
static volatile uint32_t init_pump_attempts = 0;    /* сколько циклов быстрой попытки отправки первой A */
static volatile uint32_t init_pump_a_sent  = 0;     /* 1+ если хотя бы одна A ушла через init-pump */

/* Временные метки/диагностика */
static volatile uint32_t first_test_sent_ms = 0;
static uint32_t dma_snapshot_full0 = 0;
static uint32_t dma_snapshot_full1 = 0;
static uint8_t no_dma_status_sent = 0;
static uint32_t dbg_last_forced_stat_ms = 0;
static uint8_t dbg_printed_sizes = 0;
static uint8_t dbg_any_valid_frame = 0;

/* Хостовые параметры для вывода на LCD */
static uint8_t host_profile = 0; /* последний профиль, присланный хостом (как есть) */

/* ДИАГНОСТИКА: отслеживание последней принятой команды START/STOP */
static volatile uint8_t  last_cmd_received = 0;     /* 0x20=START, 0x21=STOP, 0=none */
static volatile uint32_t last_cmd_timestamp_ms = 0; /* HAL_GetTick() когда команда получена */
static volatile uint32_t cmd_start_count = 0;       /* счётчик принятых START */
static volatile uint32_t cmd_stop_count = 0;        /* счётчик принятых STOP */

/* TX диагностика */
static volatile uint8_t  vnd_tx_ready = 1;      /* готовность к новой передаче */
static volatile uint16_t vnd_last_tx_len = 0;   /* длина последней передачи */
static volatile uint32_t vnd_last_tx_start_ms = 0; /* время начала последней передачи */
/* Резерв: последний отправленный буфер (для fallback классификации при проблемах с meta-FIFO) */
static volatile uint8_t  last_tx_is_frame = 0;
static volatile uint8_t  last_tx_flags = 0;     /* 0x01=A, 0x02=B, 0x80=TEST */
static volatile uint32_t last_tx_seq = 0;
/* Простой «истинный» маркер текущей передачи (в полёте) — не зависит от очереди метаданных */
static volatile uint8_t  inflight_is_frame = 0;
static volatile uint8_t  inflight_flags = 0; /* 0x01/0x02/0x80 */
static volatile uint32_t inflight_seq = 0;
/* Режим полноценный/диагностический */
static volatile uint8_t full_mode = 1;          /* 1 = слать рабочие ADC кадры, 0 = только тест/статус */
static volatile uint64_t vnd_total_tx_bytes = 0ULL;
/* Ограничение выборок (0 = без ограничения -> берём полный буфер профиля) */
static volatile uint16_t vnd_trunc_samples = 0;
/* Параметры кадровой частоты */
static volatile uint16_t vnd_frame_samples_req = 0; /* если 0 — вычислять по умолчанию под ~20 FPS */
static volatile uint16_t vnd_pair_period_ms = 50;   /* целевой период пары A+B в миллисекундах */
static volatile uint32_t vnd_next_pair_ms = 0;      /* следующий момент разрешения отправки A новой пары */
/* Флаг: первая полноценная пара (A->B) завершена. До этого STAT между парами стараемся не слать. */
static volatile uint8_t first_pair_done = 0;
/* Базовая отметка суммарных байт на момент START для вычисления дельты к STOP */
static volatile uint64_t vnd_tx_bytes_at_start = 0ULL;
/* Флаг для продолжения передачи из main/таска после TXCPLT */
volatile uint8_t vnd_tx_kick = 0;

/* Асинхронный режим передачи: 0 = строгие пары A->B (по умолчанию), 1 = A/B независимы */
static volatile uint8_t async_mode = 0;
/* Режим каналов: 0=A-only, 1=B-only, 2=both (default) */
static volatile uint8_t vnd_ch_mode = 2;

/* Дефолтный размер кадра в полном режиме (семплов на канал) */
#ifndef VND_FULL_DEFAULT_SAMPLES
#define VND_FULL_DEFAULT_SAMPLES 300
#endif

/* Тестовый режим: генерация пилообразного сигнала вместо реальных данных ADC */
#define USE_TEST_SAWTOOTH 0 /* Отключаем полную замену пайплайна тестовым источником */
/* Новый мягкий режим: подменяем полезную нагрузку на пилу, но сохраняем ADC/DMA и async. */
#ifndef TEST_OVERLAY_SAWTOOTH
#define TEST_OVERLAY_SAWTOOTH 0  /* DISABLED: Testing real ADC data with forced EXTSEL/EXTEN */
#endif
/* реализация генератора тестового сигнала вынесена в vnd_testgen.* */
/* Разрешение на отправку STAT в стриме: 0=запрещено, 1=разрешён один STAT */
volatile uint8_t vnd_status_permit_once = 0;
/* Отложенная отправка тестового кадра после ACK-STAT */
static volatile uint8_t test_pending = 0;
/* Управление стартовым STAT (ACK на START) и единоразовым тестом */
static volatile uint8_t start_stat_planned = 0;   /* устаревший флаг, не используем для новой логики */
static volatile uint8_t start_stat_inflight = 0;  /* Стартовый STAT начат, ждём его завершения */
/* Флаг «ACK на START завершён» — разрешает единовременную отправку тестового кадра из таска */
static volatile uint8_t start_ack_done = 0;
/* Новые флаги пошаговой последовательности */
static volatile uint8_t status_ack_pending = 0;   /* Нужно отправить ACK-STAT на START (только из таска) */
static volatile uint8_t stop_request = 0;         /* Запрошен STOP (нужно отправить STAT, затем остановить) */
static volatile uint8_t stop_stat_inflight = 0;   /* Сейчас в полёте STAT как ACK на STOP */
/* Диагностика подготовки пар */
static volatile uint32_t dbg_prepare_calls = 0;
static volatile uint32_t dbg_prepare_ok = 0;
static volatile uint32_t dbg_task_calls = 0; /* сколько раз заходили в Vendor_Stream_Task */
/* Счётчик пропущенных кадров (last-buffer-wins): сколько кадров FIFO было перескочено */
static volatile uint32_t dbg_skipped_frames = 0;

/* Состояния передачи пары */
static uint8_t channel0_sent_curseq = 0;
static uint8_t channel1_sent_curseq = 0;
static uint32_t __attribute__((unused)) last_sent_seq_adc0 = 0xFFFFFFFFu;
static uint32_t __attribute__((unused)) last_sent_seq_adc1 = 0xFFFFFFFFu;

/* Буфер статуса */
#define VND_STATUS_MAX 96 /* v1=64B, v2=76B, v3=84B, v4=96B (добавлены ADC тайминги) */
static uint8_t status_buf[VND_STATUS_MAX];
static vnd_status_v1_t g_status;
static volatile uint8_t pending_status = 0; /* требуется отправить STAT при освобождении EP */
/* Новые этапы (stage) времени старта потока/интерфейса */
volatile uint32_t vnd_stage_alt1_ms = 0;      /* фиксируется при SET_INTERFACE alt=1 */
volatile uint32_t vnd_stage_first_frame_ms = 0; /* фиксируется при первом успешном TXCPLT A/B */

/* Состояние фейковых кадров */
static __attribute__((unused)) uint8_t fake_inflight = 0;   /* reserved for diag */
static uint8_t diag_mode_active = 0; /* 1 = слать диагностические пары постоянно */
static uint16_t diag_hz = 60;        /* частота кадров-пар, Гц (20..100) (информативно) */
static uint32_t diag_period_ms = 0;  /* не используется для темпирования (макс. скорость) */
static uint32_t diag_next_ms = 0;    /* не используется для темпирования (сохранено для совместимости) */
static uint16_t diag_samples = VND_DEFAULT_TEST_SAMPLES; /* сэмплов на канал в диагностическом режиме */
static uint16_t diag_frame_len = 0;  /* общий размер кадра (hdr+payload) в диагностике */
/* Буферы диагностических кадров A/B (живут до завершения передачи) */
static uint8_t diag_a_buf[VND_FRAME_MAX_SIZE];
static uint8_t diag_b_buf[VND_FRAME_MAX_SIZE];
/* Последовательно подготовленная пара для текущего stream_seq в DIAG: */
static uint32_t diag_prepared_seq = 0xFFFFFFFFu;
static uint32_t diag_current_pair_seq = 0xFFFFFFFFu;
static uint16_t win_start0 = 0, win_len0 = 0, win_start1 = 0, win_len1 = 0;

/* === FPS измерение === */
static uint32_t fps_pair_count = 0;       /* Кол-во завершённых пар с момента старта измерения */
static uint32_t fps_frame_a_count = 0;    /* Кол-во отправленных A-кадров */
static uint32_t fps_frame_b_count = 0;    /* Кол-во отправленных B-кадров */
static uint32_t fps_prepare_count = 0;    /* Кол-во вызовов vnd_prepare_pair */
static uint32_t fps_measurement_start_ms = 0; /* Время начала измерения */
static uint32_t fps_last_report_ms = 0;   /* Время последнего отчёта */

/* === Профилирование производительности === */
typedef struct {
    uint32_t prepare_total_us;    /* Суммарное время подготовки пар (мкс) */
    uint32_t prepare_count;       /* Кол-во вызовов prepare */
    uint32_t transmit_total_us;   /* Суммарное время transmit (мкс) */
    uint32_t transmit_count;      /* Кол-во вызовов transmit */
    uint32_t txcplt_total_us;     /* Суммарное время TxCplt (мкс) */
    uint32_t txcplt_count;        /* Кол-во вызовов TxCplt */
    uint32_t last_pair_complete_ms; /* Время последнего завершения пары */
    uint32_t min_pair_interval_ms;  /* Минимальный интервал между парами */
    uint32_t max_pair_interval_ms;  /* Максимальный интервал между парами */
} perf_stats_t;

static perf_stats_t perf_stats = {0};

/* Вспомогательная функция для получения времени в микросекундах (используем HAL_GetTick с умножением) */
static inline uint32_t get_us_approx(void) {
    /* Приблизительно: 1 мс = 1000 мкс. Для более точного измерения нужен DWT, 
       но HAL_GetTick безопаснее и достаточно точен для наших целей */
    static uint32_t last_tick = 0;
    static uint32_t us_offset = 0;
    uint32_t tick = HAL_GetTick();
    if(tick != last_tick){
        us_offset = 0;
        last_tick = tick;
    }
    return tick * 1000 + us_offset++;
}

/* Инвалидация D-Cache для DMA-буфера (копия функции из adc_stream.c) */
static inline void vnd_invalidate_cache_for_buffer(void *buf, uint32_t samples)
{
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
}

/* Быстрый санитайзер: заменяет явно повреждённые (>12 бит) значения последним валидным */
static __attribute__((unused)) uint32_t vnd_sanitize_samples(uint16_t *buf, uint16_t samples, const char *tag)
{
    uint32_t fixed = 0;
    uint16_t prev = 0;
    uint8_t have_prev = 0;
    uint16_t first_bad = 0, first_idx = 0;
    for(uint16_t i = 0; i < samples; ++i){
        uint16_t v = buf[i];
        if(v > 4095u){
            if(fixed == 0){ first_bad = v; first_idx = i; }
            buf[i] = have_prev ? prev : 0;
            fixed++;
        } else {
            prev = v; have_prev = 1;
        }
    }
    if(fixed){
        static uint32_t last_log_ms = 0;
        uint32_t now = HAL_GetTick();
        if(now - last_log_ms >= 1000u){
            last_log_ms = now;
            printf("[VND_SAN] %s fixed=%lu first_idx=%u first_bad=%u prev=%u\r\n",
                   tag, (unsigned long)fixed, (unsigned)first_idx, (unsigned)first_bad, (unsigned)(have_prev ? prev : 0));
        }
    }
    return fixed;
}
/* Подсчёт выбросов >4095 (опционально, если включить санитайзер) */
static volatile uint32_t dbg_gt4095_ch[2] = {0,0};
#ifndef VND_ENABLE_ADC_SANITIZE
#define VND_ENABLE_ADC_SANITIZE 0 /* по умолчанию не трогаем данные ADC */
#endif

#ifndef VND_SPIKE_CLIP_ENABLE
#define VND_SPIKE_CLIP_ENABLE 0 /* теперь не клипуем, только детектируем */
#endif
#ifndef VND_SPIKE_THRESHOLD
#define VND_SPIKE_THRESHOLD 3000u
#endif
#ifndef VND_SPIKE_MIN_LEN
#define VND_SPIKE_MIN_LEN 6u
#endif

/* Детектор/клиппер блоков высокой амплитуды (короткие пачки ≈10 семплов) */
static __attribute__((unused)) uint32_t vnd_detect_and_clip_spikes(uint16_t *buf, uint16_t samples, const char *tag)
{
    if(!buf || samples == 0) return 0;
    uint32_t events = 0;
    uint16_t thr = VND_SPIKE_THRESHOLD;
    uint16_t last_good = 0;
    uint8_t have_last = 0;
    static uint32_t last_log_ms = 0;
    for(uint16_t i=0; i<samples; ){
        uint16_t v = buf[i];
        if(v <= thr){
            last_good = v; have_last = 1; i++; continue;
        }
        /* найден старт блока выше порога */
        uint16_t start = i;
        uint16_t maxv = v;
        while(i < samples && buf[i] > thr){
            if(buf[i] > maxv) maxv = buf[i];
            i++;
        }
        uint16_t len = (uint16_t)(i - start);
        if(len >= VND_SPIKE_MIN_LEN){
            events++;
            /* опционально клипуем весь блок на последнее валидное значение */
            if(VND_SPIKE_CLIP_ENABLE){
                uint16_t fill = have_last ? last_good : 0;
                for(uint16_t k=start; k<start+len && k<samples; ++k){ buf[k] = fill; }
            }
            uint32_t now = HAL_GetTick();
            if(now - last_log_ms >= 500u){
                last_log_ms = now;
                printf("[VND_SPIKE] %s idx=%u len=%u max=%u thr=%u clip=%u\r\n",
                       tag, (unsigned)start, (unsigned)len, (unsigned)maxv,
                       (unsigned)thr, (unsigned)VND_SPIKE_CLIP_ENABLE);
            }
        }
    }
    return events;
}

typedef struct {
    uint16_t idx;
    uint16_t len;
    uint16_t maxv;
    uint8_t  ch;
} spike_info_t;

static __attribute__((unused)) void vnd_log_spike_window(const spike_info_t *si, const uint16_t *buf, uint16_t samples, uint32_t seq)
{
    if(!si || !buf) return;
    uint16_t start = si->idx;
    uint16_t end = (uint16_t)(start + si->len);
    uint16_t w0 = (start > 8) ? (uint16_t)(start - 8) : 0;
    uint16_t w1 = end + 8; if(w1 > samples) w1 = samples;
    /* Логируем только до 16 значений, чтобы не шуметь CDC */
    char line[256];
    int pos = snprintf(line, sizeof(line), "[SPIKE_DUMP] CH=%c seq=%lu idx=%u len=%u max=%u win=%u..%u vals=",
                       si->ch ? 'B' : 'A', (unsigned long)seq, (unsigned)si->idx, (unsigned)si->len, (unsigned)si->maxv,
                       (unsigned)w0, (unsigned)w1);
    uint16_t printed = 0;
    for(uint16_t i = w0; i < w1 && pos < (int)sizeof(line)-8; ++i){
        pos += snprintf(line+pos, sizeof(line)-pos, "%u,", (unsigned)buf[i]);
        if(++printed >= 16) break;
    }
    if(pos > 0 && pos < (int)sizeof(line)) line[pos-1] = 0; /* убрать последнюю запятую */
    cdc_logf("%s", line);
}

static __attribute__((unused)) uint8_t vnd_detect_spike_info(uint16_t *buf, uint16_t samples, uint16_t thr, uint16_t min_len, uint8_t ch, spike_info_t *out)
{
    if(!buf || samples < min_len || !out) return 0;
    for(uint16_t i=0; i<samples; ){
        uint16_t v = buf[i];
        if(v <= thr){ i++; continue; }
        uint16_t start = i;
        uint16_t maxv = v;
        while(i < samples && buf[i] > thr){ if(buf[i] > maxv) maxv = buf[i]; i++; }
        uint16_t len = i - start;
        if(len >= min_len){
            out->idx = start; out->len = len; out->maxv = maxv; out->ch = ch;
            return 1;
        }
    }
    return 0;
}

/* Локальная утилита: обновить LCD параметрами, присланными хостом */
/* ДИАГНОСТИКА: обновление индикации последней принятой команды на LCD и UART */
static void vnd_update_cmd_indicator(void)
{
    char cmd_str[16];
    uint32_t now = HAL_GetTick();
    uint32_t elapsed_sec = (now >= last_cmd_timestamp_ms) ? ((now - last_cmd_timestamp_ms) / 1000) : 0;
    
    /* Компактный формат для размещения после "USB:CFG" на верхней строке LCD:
       Показываем последнюю команду и сколько секунд назад она была получена */
    if (last_cmd_received == 0x20) {
        snprintf(cmd_str, sizeof(cmd_str), "ST:%lu", (unsigned long)cmd_start_count);
    } else if (last_cmd_received == 0x21) {
        snprintf(cmd_str, sizeof(cmd_str), "SP:%lu", (unsigned long)cmd_stop_count);
    } else {
        snprintf(cmd_str, sizeof(cmd_str), "--:0");
    }
    
    /* Вывод на LCD строка 0 (y=0), позиция x=80 пикселей (между USB:CFG и T2:) */
    LCD_ShowString_Size(80, 0, cmd_str, 16, YELLOW, BLACK);
    
    /* Дополнительно вывод в UART для отладки */
    if (last_cmd_received == 0x20) {
        printf("[CMD_IND] START received #%lu (at t=%lu ms, %lu sec ago)\r\n", 
               (unsigned long)cmd_start_count, (unsigned long)last_cmd_timestamp_ms, (unsigned long)elapsed_sec);
    } else if (last_cmd_received == 0x21) {
        printf("[CMD_IND] STOP received #%lu (at t=%lu ms, %lu sec ago)\r\n", 
               (unsigned long)cmd_stop_count, (unsigned long)last_cmd_timestamp_ms, (unsigned long)elapsed_sec);
    }
}

static void vnd_update_lcd_params(void)
{
    /* Частота блоков (пар кадров A+B): в FULL берём из периода пары, в DIAG — diag_hz */
    uint16_t block_hz = 0;
    if(full_mode) {
        uint16_t pp = vnd_pair_period_ms;
        block_hz = (pp > 0) ? (uint16_t)(1000u / pp) : 0u;
    } else {
        block_hz = diag_hz;
    }

    /* Кол-во сэмплов на канал в кадре: если явно задано командой — используем его, иначе активное */
    uint16_t frame_samples = (vnd_frame_samples_req != 0) ? vnd_frame_samples_req
                                : ((cur_samples_per_frame != 0) ? cur_samples_per_frame
                                                                : adc_stream_get_active_samples());

    /* Обновить LCD (только при изменениях внутри функции отображения) */
    // stream_display_update_host_params(
    //     host_profile,
    //     frame_samples,
    //     block_hz,
    //     win_start0, win_len0,
    //     win_start1, win_len1,
    //     (uint8_t)full_mode
    // );
}

/* --- CDC дублирование: отправляем компактную ASCII строку с первыми 64 сэмплами --- */
static __attribute__((unused)) uint32_t cdc_last_send_ms = 0;       /* для троттлинга */
static __attribute__((unused)) char     cdc_line_buf[1024];         /* статический буфер для передачи */
static __attribute__((unused)) uint16_t rd_le16(const uint8_t *p){ return (uint16_t)(p[0] | (p[1] << 8)); }
static __attribute__((unused)) uint32_t rd_le32(const uint8_t *p){ return (uint32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24)); }
static void vnd_cdc_duplicate_preview(const uint8_t *buf, uint16_t len, const char *tag)
{
    /* При отключённом превью не выводим копию потока в CDC */
#if !VND_CDC_PREVIEW_ENABLE
    (void)buf; (void)len; (void)tag;
    return;
#else
    /* Троттлинг, чтобы не забивать CDC: не чаще 10 Гц */
    uint32_t now = HAL_GetTick();
    /* Периодический INIT_DBG до первой реальной кадра: каждые ~20мс выводим состояние счётчиков каналов и режимов. */
    if(streaming && vnd_stage_first_frame_ms == 0){
        static uint32_t last_init_dbg_ms = 0;
        if(now - last_init_dbg_ms >= 20){
            last_init_dbg_ms = now;
            extern volatile uint32_t adc_ch_wr_seq[2];
            extern volatile uint32_t adc_ch_rd_seq[2];
            cdc_logf("INIT_DBG t=%lu pend_init=%u wrA=%lu wrB=%lu rdA=%lu rdB=%lu ch_mode=%u async=%u ep_busy=%u",
                     (unsigned long)now,
                     (unsigned)vnd_pending_init,
                     (unsigned long)adc_ch_wr_seq[0], (unsigned long)adc_ch_wr_seq[1],
                     (unsigned long)adc_ch_rd_seq[0], (unsigned long)adc_ch_rd_seq[1],
                     (unsigned)vnd_ch_mode, (unsigned)async_mode, (unsigned)vnd_ep_busy);
        }
        /* Fallback отключён: всегда держим два канала, даже если B задерживается. */
    }
    if ((now - cdc_last_send_ms) < 100) return;
    if (!buf || len < VND_FRAME_HDR_SIZE) return;
    /* Парсим вручную поля заголовка (LE) */
    uint16_t magic = rd_le16(buf + 0);
    if (magic != 0xA55A) return;
    uint32_t seq = rd_le32(buf + 4);
    uint16_t ns  = rd_le16(buf + 12);
    if (ns == 0) return;
    if ((uint32_t)VND_FRAME_HDR_SIZE + (uint32_t)ns*2u > (uint32_t)len) return;
    unsigned off = 0;
    const char *chan = tag ? tag : "?";
    off += (unsigned)snprintf(cdc_line_buf + off, sizeof(cdc_line_buf) - off,
                              "[%s] seq=%lu n=%u first64:", chan, (unsigned long)seq, (unsigned)ns);
    uint16_t show = (ns > 64u) ? 64u : ns;
    for (uint16_t i = 0; i < show && off + 8 < sizeof(cdc_line_buf); i++)
    {
        uint16_t v = rd_le16(buf + VND_FRAME_HDR_SIZE + 2u*i);
        off += (unsigned)snprintf(cdc_line_buf + off, sizeof(cdc_line_buf) - off, " %u", (unsigned)v);
    }
    if (off + 2 < sizeof(cdc_line_buf)) {
        cdc_line_buf[off++] = '\r';
        cdc_line_buf[off++] = '\n';
    }
    uint8_t rc = CDC_Transmit_HS((uint8_t*)cdc_line_buf, (uint16_t)off);
    if (rc == USBD_OK) {
        cdc_last_send_ms = now;
    }
#endif
}

/* --- CDC события/статистика (COM-порт): START/STOP и периодическая скорость --- */
static uint32_t cdc_stats_last_ms = 0;         /* последняя отметка отправки статистики */
static uint64_t cdc_stats_prev_bytes = 0ULL;   /* предыдущее значение счётчика байт */
static char     cdc_evt_buf[160];              /* буфер форматирования событий */

static void cdc_logf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(cdc_evt_buf, sizeof(cdc_evt_buf) - 2, fmt, ap);
    va_end(ap);
    if(n < 0) return;
    if(n > (int)sizeof(cdc_evt_buf) - 2) n = (int)sizeof(cdc_evt_buf) - 2;
    cdc_evt_buf[n++] = '\r';
    cdc_evt_buf[n++] = '\n';
    /* неблокирующая попытка: если CDC занят — событие может быть пропущено */
    (void)CDC_Transmit_HS((uint8_t*)cdc_evt_buf, (uint16_t)n);
}

static void vnd_cdc_periodic_stats(uint32_t now_ms)
{
    /* В диагностическом режиме не трогаем CDC вовсе — уменьшаем накладные расходы */
    if(diag_mode_active){ return; }
    if(now_ms - cdc_stats_last_ms < 1000) return; /* не чаще 1 Гц */
    cdc_stats_last_ms = now_ms;
    uint64_t cur = vnd_total_tx_bytes;
    uint64_t d   = (cur >= cdc_stats_prev_bytes) ? (cur - cdc_stats_prev_bytes) : 0ULL;
    cdc_stats_prev_bytes = cur;
    uint32_t bps = (uint32_t)d; /* за ~1 секунду */
    /* Добавляем количество переданных кадров A/B для сравнения с host RX */
    /* Новая расширенная строка: выводим попытки/отказы TX и частичные abort'ы для анализа стабильности */
    extern volatile uint32_t dbg_tx_attempt; /* объявлены выше */
    extern volatile uint32_t dbg_tx_reject;
    extern volatile uint32_t dbg_partial_frame_abort;
    extern volatile uint32_t dbg_size_mismatch;
    uint32_t tx_attempt = dbg_tx_attempt;
    uint32_t tx_reject  = dbg_tx_reject;
    uint32_t tx_ok      = dbg_tx_cplt; /* число завершённых передач (может включать STAT/TEST) */
    uint32_t frame_abort = dbg_partial_frame_abort;
    uint32_t size_mism   = dbg_size_mismatch;
    /* Оценка потока в Мбит/с (грубая: только пользовательские байты за 1с *8/1e6) */
    float mbps = (float)(bps * 8ULL) / 1000000.0f;
    cdc_logf("STAT bytes_total=%llu bps=%lu (%.2f Mbps) streaming=%u diag=%u sentA=%lu sentB=%lu seq=%lu tx_attempt=%lu tx_reject=%lu tx_cplt=%lu abort=%lu sz_mm=%lu",
             (unsigned long long)cur, (unsigned long)bps, (double)mbps,
             (unsigned)streaming, (unsigned)diag_mode_active,
             (unsigned long)dbg_sent_ch0_total, (unsigned long)dbg_sent_ch1_total, (unsigned long)stream_seq,
             (unsigned long)tx_attempt, (unsigned long)tx_reject, (unsigned long)tx_ok,
             (unsigned long)frame_abort, (unsigned long)size_mism);
}

/* CRC16-CCITT (0x1021, init 0xFFFF) по байтам полезной нагрузки */
static uint16_t vnd_crc16_ccitt(const uint8_t *data, uint32_t len)
{
    uint16_t crc = 0xFFFFu;
    for(uint32_t i = 0; i < len; ++i){
        crc ^= (uint16_t)data[i] << 8;
        for(uint8_t b = 0; b < 8; ++b){
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

/* Заголовок кадра */
/*
 * Формат под спецификацию хоста (ровно 32 байта, LE):
 *   [0..1] magic = 0xA55A -> 5A A5
 *   [2]    ver   = 0x01
 *   [3]    flags: 0x01=ADC0, 0x02=ADC1, 0x80=TEST, +0x04 если есть CRC16 (сейчас 0)
 *   [4..7] seq (u32 LE) — общий для пары
 *   [8..11] timestamp (u32 LE) — одинаковый в паре
 *   [12..13] total_samples (u16 LE)
 *   [14..15] zone_count=0
 *   [16..19] zone1_offset=0
 *   [20..23] zone1_length=0
 *   [24..27] reserved=0
 *   [28..29] reserved2=0
 *   [30..31] crc16=0 (флаг 0x04 не используется)
 */
typedef struct __attribute__((packed)) {
    uint16_t magic;           /* 0xA55A */
    uint8_t  ver;             /* 0x01 */
    uint8_t  flags;           /* см. описание выше */
    uint32_t seq;             /* номер логической последовательности (пары) */
    uint32_t timestamp;       /* HAL_GetTick */
    uint16_t total_samples;   /* кол-во сэмплов */
    uint16_t zone_count;      /* 0 */
    uint32_t zone1_offset;    /* 0 */
    uint32_t zone1_length;    /* 0 */
    uint32_t reserved;        /* 0 */
    uint16_t reserved2;       /* 0 */
    uint16_t crc16;           /* 0, пока CRC не используется */
} vnd_frame_hdr_t;
_Static_assert(sizeof(vnd_frame_hdr_t)==32, "vnd_frame_hdr_t must be 32 bytes (PACKING ERROR)");

/* Состояние кадра */
typedef enum { FB_FILL=0, FB_READY=1, FB_SENDING=2 } frame_state_t;

typedef struct {
    volatile frame_state_t st;
    uint16_t samples;
    uint8_t  flags;
    uint16_t frame_size;
    uint32_t seq;
    uint32_t dma_seq;
    uint16_t crc16;
    uint32_t crc_seq;
    uint8_t  buf[VND_FRAME_MAX_SIZE];
} ChanFrame;

#define VND_PAIR_BUFFERS 8  /* Возврат к 8 для стабильности */
static ChanFrame g_frames[VND_PAIR_BUFFERS][2];
static uint8_t pair_fill_idx = 0;
static uint8_t pair_send_idx = 0;
static uint8_t sending_channel = 0xFF; /* 0 /1 когда активна передача */
/* Простой режим управления отправкой: только из таска */
static uint8_t simple_tx_mode = 1;
/* Флаги машины состояний по требованию хоста */
static volatile uint8_t pending_B = 0;  /* 1 = нужно отправить B после A */
/* Диагностика зависаний между A и B */
static uint32_t pending_B_since_ms = 0; /* время, когда завершилась передача A и мы начали ждать B */

/* Прототипы */
static void vnd_reset_buffers(void);
// static void vnd_send_test_frame(void); // удален, не используется
static void vnd_prepare_pair(void);
static void vnd_build_frame(ChanFrame *cf);
static void vnd_try_start_tx(void);
static int  vnd_validate_frame(const uint8_t *buf, uint16_t len, uint8_t expect_test, uint8_t allow_zero_samples);
static USBD_StatusTypeDef vnd_transmit_frame(uint8_t *buf, uint16_t len, uint8_t is_test, uint8_t allow_zero_samples, const char *tag);
/* Диагностика: подготовка и отправка A/B тестовой пары (упрощённой) */
static void vnd_diag_prepare_pair(uint32_t seq, uint16_t samples);
static int  vnd_diag_try_tx(void);
/* Классификация последнего отправленного буфера для корректного разбора в TxCplt */
typedef struct {
    uint8_t  is_frame;      /* 1 = кадр с заголовком */
    uint8_t  flags;         /* исходные flags из hdr (0x01/0x02/0x80) */
    uint32_t seq_field;     /* seq из hdr на момент отправки */
    uint32_t push_tick;     /* HAL_GetTick() при постановке в FIFO */
} vnd_tx_meta_t;
#define VND_TX_META_FIFO 8
static vnd_tx_meta_t vnd_tx_meta_fifo[VND_TX_META_FIFO];
static uint8_t vnd_tx_meta_head = 0; /* push */
static uint8_t vnd_tx_meta_tail = 0; /* pop */
/* Диагностические счётчики метаданных */
static uint32_t meta_push_total = 0;
static uint32_t meta_pop_total = 0;
static uint32_t meta_empty_events = 0;
static uint32_t meta_overflow_events = 0;
static inline uint8_t vnd_tx_meta_depth(void){
    uint8_t h = vnd_tx_meta_head, t = vnd_tx_meta_tail;
    if(h>=t) return (uint8_t)(h - t);
    return (uint8_t)(VND_TX_META_FIFO - (t - h));
}
static inline void vnd_tx_meta_push(uint8_t is_frame, uint8_t flags, uint32_t seq_field){
    uint8_t next = (uint8_t)((vnd_tx_meta_head + 1u) % VND_TX_META_FIFO);
    if(next == vnd_tx_meta_tail){
        /* overflow - drop oldest */
        vnd_tx_meta_tail = (uint8_t)((vnd_tx_meta_tail + 1u) % VND_TX_META_FIFO);
        meta_overflow_events++;
        VND_LOG("WARN META_FIFO_OVF depth_before=%u", (unsigned)vnd_tx_meta_depth());
    }
    vnd_tx_meta_fifo[vnd_tx_meta_head].is_frame = is_frame;
    vnd_tx_meta_fifo[vnd_tx_meta_head].flags = flags;
    vnd_tx_meta_fifo[vnd_tx_meta_head].seq_field = seq_field;
    vnd_tx_meta_fifo[vnd_tx_meta_head].push_tick = HAL_GetTick();
    vnd_tx_meta_head = next;
    meta_push_total++;
    /* Умеренный лог только для рабочих кадров (A/B/TEST); STAT слишком часты не будут */
    if(is_frame){
        VND_LOG("META_PUSH fl=0x%02X seq=%lu depth=%u", (unsigned)flags, (unsigned long)seq_field, (unsigned)vnd_tx_meta_depth());
    }
}
static inline int vnd_tx_meta_pop(vnd_tx_meta_t *out){
    if(vnd_tx_meta_tail == vnd_tx_meta_head){ meta_empty_events++; return 0; } /* empty */
    *out = vnd_tx_meta_fifo[vnd_tx_meta_tail];
    vnd_tx_meta_tail = (uint8_t)((vnd_tx_meta_tail + 1u) % VND_TX_META_FIFO);
    meta_pop_total++;
    return 1;
}
/* Унифицированная фиксация метаданных после успешного запуска передачи */
static inline void vnd_tx_meta_after(uint8_t *buf, uint16_t len){
    uint8_t is_frame = 0, flags = 0; uint32_t seq_field = 0;
    if(len >= VND_FRAME_HDR_SIZE){
        const vnd_frame_hdr_t *h = (const vnd_frame_hdr_t*)buf;
        if(h->magic == 0xA55A){ is_frame = 1; flags = h->flags; seq_field = h->seq; }
    }
    /* Сохраняем последнюю отправку для fallback-классификации */
    last_tx_is_frame = is_frame; last_tx_flags = flags; last_tx_seq = seq_field;
    vnd_tx_meta_push(is_frame, flags, seq_field);
}
/* Нейтрализовать «застрявшую» запись в meta-FIFO (например, после ForceTxIdle),
   чтобы последующий TxCplt не принял её за реальный кадр и не исказил порядок. */
static void vnd_meta_neutralize(uint8_t flags_mask, uint32_t seq_field)
{
    uint8_t t = vnd_tx_meta_tail;
    while(t != vnd_tx_meta_head){
        vnd_tx_meta_t *m = &vnd_tx_meta_fifo[t];
        if(m->is_frame && (m->flags == flags_mask) && (m->seq_field == seq_field)){
            m->is_frame = 0; m->flags = 0; /* превратить в служебный */
            VND_LOG("META_NEUTRALIZE fl=0x%02X seq=%lu", (unsigned)flags_mask, (unsigned long)seq_field);
            break;
        }
        t = (uint8_t)((t + 1u) % VND_TX_META_FIFO);
    }
}
static __attribute__((unused)) void vnd_debug_force_status_tick(void);
static void vnd_debug_raw_stat_tick(void);
static void __attribute__((unused)) vnd_send_fake_pair(void); // удалим позже; сейчас заглушка ниже
static void vnd_log_hdr_layout(void);
static void vnd_try_send_pending_status_from_task(void);
static void vnd_try_send_test_from_task(void);
/* Быстрый пайплайн: немедленная отправка следующего кадра из TxCplt */
static int vnd_try_send_B_immediate(void);
static int vnd_try_send_A_nextpair_immediate(void);
/* Заглушки для отключённых диагностик в static-mode */
static __attribute__((unused)) void vnd_debug_raw_stat_tick(void){ }
static void vnd_send_fake_pair(void){ }
static __attribute__((unused)) void vnd_debug_force_status_tick(void){ }
static __attribute__((unused)) void vnd_try_start_tx(void){ }
/* Аварийный keepalive: если совсем нет успешных завершений передач первые секунды */
static void vnd_emergency_keepalive(uint32_t now_ms);
/* Сервис: асинхронная обработка команд управления (EP0 SOFT/DEEP RESET) */
extern void USBD_VND_ProcessControlRequests(void);
/* Асинхронный планировщик A/B */
static int vnd_async_try_tx(void);
static int vnd_find_pair_by_seq(uint32_t seq);
/* Вычисление периода пары по требуемым samples_per_frame и текущему профилю ADC (buf_rate_hz) */
static void vnd_recompute_pair_timing(uint16_t samples_per_frame)
{
    /* Используем частоту буферов (Fs блоков/с), а не абсолютную частоту сэмплов */
    extern uint16_t adc_stream_get_buf_rate(void);
    uint16_t buf_rate = adc_stream_get_buf_rate();
    if(buf_rate == 0) buf_rate = 20; /* защита от деления на ноль */
    if(samples_per_frame == 0) samples_per_frame = 1;
    /* period_ms ≈ 1000 * samples_per_frame / buf_rate (округление) */
    uint32_t num = (uint32_t)samples_per_frame * 1000u + (uint32_t)(buf_rate/2u);
    uint32_t ms  = num / (uint32_t)buf_rate;
    if(ms == 0) ms = 1;
    vnd_pair_period_ms = (uint16_t)((ms > 1000u) ? 1000u : ms);
    /* Примечание: период больше не используется как задержка — передаём сразу при готовности данных.
       Оставляем расчёт только для информационных целей. */
    VND_LOG("PAIR_TIMING(info): samples=%u buf_rate=%u -> period≈%u ms",
        (unsigned)samples_per_frame, (unsigned)buf_rate, (unsigned)vnd_pair_period_ms);
}

/* Публичная функция: полный сброс/останов пайплайна */
void vnd_pipeline_stop_reset(int deep)
{
    /* Остановить передачу и внутренние состояния */
    streaming = 0; diag_mode_active = 0; full_mode = 1;
    stop_request = 0; pending_status = 0; start_stat_inflight = 0; status_ack_pending = 0; start_ack_done = 1;
    vnd_ep_busy = 0; vnd_tx_ready = 1; vnd_inflight = 0; sending_channel = 0xFF; pending_B = 0; pending_B_since_ms = 0;
    test_sent = 0; test_in_flight = 0; vnd_tx_kick = 1;
    /* Очистить мета-FIFO и счётчики */
    vnd_tx_meta_head = vnd_tx_meta_tail = 0; meta_push_total = meta_pop_total = meta_empty_events = meta_overflow_events = 0;
    stream_seq = 0; next_seq_to_assign = 0; dbg_produced_seq = 0; first_pair_done = 0;
    cur_samples_per_frame = 0; cur_expected_frame_size = 0; dbg_any_valid_frame = 0;
    vnd_reset_buffers();
    /* Остановить источник данных/ADC DMA при глубоком сбросе */
    if(deep){ extern void adc_stream_stop(void); adc_stream_stop(); }
    /* Индикация */
    HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_RESET);
}

/* Реализация ранее отсутствовавшей функции */
/* Форсированное завершение зависшего тестового кадра без прихода TxCplt.
   Условия: test_in_flight == 0 (мы уже вручную сняли busy по таймауту), test_sent == 0 (ещё не засчитан),
   в FIFO присутствует meta с flags=0x80 и возраст > 60 мс.
   Вместо удаления элемента (что может рассинхронизировать последующие TxCplt), мы помечаем его как служебный:
     is_frame=0; flags=0; — таким образом последующий TxCplt безопасно извлекёт и проигнорирует.
   Гейтинг отправки A перестанет видеть TEST meta (по flags) и разрешит прогресс. */
static void vnd_force_complete_test_meta_if_stale(void)
{
#if VND_DISABLE_TEST
    /* В режиме без TEST: убедимся, что meta-FIFO не содержит блокирующих TEST записей */
    uint8_t t = vnd_tx_meta_tail;
    while(t != vnd_tx_meta_head){
        vnd_tx_meta_t *m = &vnd_tx_meta_fifo[t];
        if(m->is_frame && m->flags == 0x80){ m->is_frame = 0; m->flags = 0; }
        t = (uint8_t)((t + 1u) % VND_TX_META_FIFO);
    }
    return;
#else
    if(test_in_flight) return;
    uint8_t t = vnd_tx_meta_tail;
    uint32_t now = HAL_GetTick();
    while(t != vnd_tx_meta_head){
        vnd_tx_meta_t *m = &vnd_tx_meta_fifo[t];
        if(m->is_frame && m->flags == 0x80){
            uint32_t age = now - m->push_tick;
            if(age > 60){
                /* Форсируем */
                m->is_frame = 0; m->flags = 0; /* превращаем в служебный */
                if(!test_sent){ test_sent = 1; first_test_sent_ms = HAL_GetTick(); }
                VND_LOG("FORCE_TEST_META age=%lums depth=%u", (unsigned long)age, (unsigned)vnd_tx_meta_depth());
            }
            break; /* обрабатываем только первый TEST */
        }
        t = (uint8_t)((t + 1u) % VND_TX_META_FIFO);
    }
#endif
}

/* === Функция вывода FPS статистики и профилирования по CDC === */
void vnd_report_fps_stats(void)
{
    uint32_t now_ms = HAL_GetTick();
    uint32_t elapsed_ms = now_ms - fps_measurement_start_ms;
    
    if(elapsed_ms < 100) return; /* Слишком рано для измерения */
    
    /* Вычисляем FPS */
    float elapsed_sec = (float)elapsed_ms / 1000.0f;
    float pair_fps = (float)fps_pair_count / elapsed_sec;
    float frame_a_fps = (float)fps_frame_a_count / elapsed_sec;
    float frame_b_fps = (float)fps_frame_b_count / elapsed_sec;
    float prepare_fps = (float)fps_prepare_count / elapsed_sec;
    
    /* Вычисляем средние времена */
    uint32_t avg_prepare_us = (perf_stats.prepare_count > 0) ? 
        (perf_stats.prepare_total_us / perf_stats.prepare_count) : 0;
    uint32_t avg_transmit_us = (perf_stats.transmit_count > 0) ?
        (perf_stats.transmit_total_us / perf_stats.transmit_count) : 0;
    uint32_t avg_txcplt_us = (perf_stats.txcplt_count > 0) ?
        (perf_stats.txcplt_total_us / perf_stats.txcplt_count) : 0;
    
    cdc_logf("FPS pairs=%.1f A=%.1f B=%.1f prep=%.1f (%.1fs)",
        pair_fps, frame_a_fps, frame_b_fps, prepare_fps, elapsed_sec);
    cdc_logf("PERF prepare=%luus tx=%luus txcplt=%luus pair_int=%lu..%lums",
        (unsigned long)avg_prepare_us, (unsigned long)avg_transmit_us, 
        (unsigned long)avg_txcplt_us,
        (unsigned long)perf_stats.min_pair_interval_ms,
        (unsigned long)perf_stats.max_pair_interval_ms);
    
    fps_last_report_ms = now_ms;
}

/* === Функция вывода детального профилирования по запросу === */
void vnd_print_perf_stats(void)
{
    uint32_t now_ms = HAL_GetTick();
    uint32_t elapsed_ms = now_ms - fps_measurement_start_ms;
    float elapsed_sec = (elapsed_ms > 0) ? ((float)elapsed_ms / 1000.0f) : 0.001f;
    
    /* FPS */
    float pair_fps = (float)fps_pair_count / elapsed_sec;
    float frame_a_fps = (float)fps_frame_a_count / elapsed_sec;
    float frame_b_fps = (float)fps_frame_b_count / elapsed_sec;
    
    /* Средние времена */
    uint32_t avg_prepare_us = (perf_stats.prepare_count > 0) ? 
        (perf_stats.prepare_total_us / perf_stats.prepare_count) : 0;
    uint32_t avg_transmit_us = (perf_stats.transmit_count > 0) ?
        (perf_stats.transmit_total_us / perf_stats.transmit_count) : 0;
    uint32_t avg_txcplt_us = (perf_stats.txcplt_count > 0) ?
        (perf_stats.txcplt_total_us / perf_stats.txcplt_count) : 0;
    
    cdc_logf("=== PERFORMANCE STATS (%.1fs) ===", elapsed_sec);
    cdc_logf("FPS: pairs=%.1f A=%.1f B=%.1f", pair_fps, frame_a_fps, frame_b_fps);
    cdc_logf("COUNTS: pairs=%lu A=%lu B=%lu prep=%lu",
        (unsigned long)fps_pair_count, (unsigned long)fps_frame_a_count,
        (unsigned long)fps_frame_b_count, (unsigned long)fps_prepare_count);
    cdc_logf("TIMING: prepare=%luus tx=%luus txcplt=%luus",
        (unsigned long)avg_prepare_us, (unsigned long)avg_transmit_us,
        (unsigned long)avg_txcplt_us);
    cdc_logf("INTERVAL: min=%lums max=%lums",
        (unsigned long)perf_stats.min_pair_interval_ms,
        (unsigned long)perf_stats.max_pair_interval_ms);
    
    /* Диагностика ADC */
    extern volatile uint32_t frame_wr_seq, frame_rd_seq;
    cdc_logf("ADC: wr=%lu rd=%lu skipped=%lu",
        (unsigned long)frame_wr_seq, (unsigned long)frame_rd_seq,
        (unsigned long)dbg_skipped_frames);
}

/* Отправка отложенного STAT только из таска */
static void vnd_try_send_pending_status_from_task(void)
{
    /* В диагностическом режиме полностью запрещаем любые STAT по bulk-IN,
       чтобы исключить окна между A и B. Для статуса используйте EP0 (ctrl).
       Также ACK-STOP в DIAG не отправляем через bulk (см. обработчик STOP). */
    if(diag_mode_active){ return; }
    if(!pending_status) return;
    /* Нельзя отправлять STAT, если есть незавершённая передача (даже если busy временно сброшен) */
    if(vnd_ep_busy || vnd_inflight) return;
    uint16_t l = vnd_build_status((uint8_t*)status_buf, sizeof(status_buf));
        if(!l) { pending_status = 0; return; } /* (очистка дублирующего кода STAT уже выполнена выше) */
    vnd_status_permit_once = 1;
    vnd_tx_ready = 0; vnd_ep_busy = 1; vnd_last_tx_len = l; vnd_last_tx_start_ms = HAL_GetTick();
    if(USBD_VND_Transmit(&hUsbDeviceHS, (uint8_t*)status_buf, l) == USBD_OK){
        vnd_tx_meta_after((uint8_t*)status_buf, l);
        VND_LOG("STAT_TX pending(task) len=%u depth=%u", l, (unsigned)vnd_tx_meta_depth());
        if(stop_request){ stop_stat_inflight = 1; }
        pending_status = 0;
    } else {
        VND_LOG("STAT_TX pending(task) busy/fail");
        vnd_tx_ready = 1; vnd_ep_busy = 0;
    }
}

/* Тик от таймера */
static volatile uint8_t vnd_tick_flag = 0;
void usb_vendor_periodic_tick(void){ vnd_tick_flag = 1; }

/*
 * ВАЖНО:
 * - В on-wire протоколе поле hdr.seq должно отражать логическую последовательность кадра,
 *   привязанную к данным DMA (а не к счётчику отправок).
 * - Хостовый GUI использует (seq & 1) как even/odd, поэтому seq НЕ должен быть пер-канальным
 *   счётчиком передачи.
 */

/* ---------------- Вспомогательные ---------------- */
static void vnd_reset_buffers(void){
    for(uint8_t p=0;p<VND_PAIR_BUFFERS;p++) for(uint8_t c=0;c<2;c++){ g_frames[p][c].st=FB_FILL; g_frames[p][c].samples=0; g_frames[p][c].flags = c?VND_FLAGS_ADC1:VND_FLAGS_ADC0; g_frames[p][c].frame_size=0; g_frames[p][c].seq=0; g_frames[p][c].dma_seq=0; g_frames[p][c].crc16=0; g_frames[p][c].crc_seq=0; memset(g_frames[p][c].buf,0xCC,sizeof(g_frames[p][c].buf)); }
    pair_fill_idx=pair_send_idx=0; sending_channel=0xFF; channel0_sent_curseq=channel1_sent_curseq=0; pending_B = 0; pending_B_since_ms = 0;
}

uint16_t vnd_build_status(uint8_t *dst, uint16_t max_len){
    if(max_len < 64) return 0; /* требуется минимум v1 */
    memset(&g_status,0,sizeof(g_status));
    /* Сигнатура 'STAT' в первых 4 байтах */
    g_status.sig[0] = 'S';
    g_status.sig[1] = 'T';
    g_status.sig[2] = 'A';
    g_status.sig[3] = 'T';
    g_status.version = 4; /* v4: добавлены now_ms и last_full[0/1]_ms для диагностики ADC тайминга */
    g_status.cur_samples = cur_samples_per_frame;
    g_status.frame_bytes = (uint16_t)(VND_FRAME_HDR_SIZE + cur_samples_per_frame*2u);
    g_status.test_frames = test_sent ? 1u : 0u;
    g_status.produced_seq = dbg_produced_seq;
    g_status.sent0 = dbg_sent_ch0_total;
    g_status.sent1 = dbg_sent_ch1_total;
    g_status.dbg_tx_cplt = dbg_tx_cplt;
    g_status.dbg_partial_frame_abort = dbg_partial_frame_abort;
    g_status.dbg_size_mismatch = dbg_size_mismatch;
    /* Получим DMA счётчики */
    adc_stream_debug_t d; adc_stream_get_debug(&d);
    g_status.dma_done0 = d.dma_full0;
    g_status.dma_done1 = d.dma_full1;
    g_status.frame_wr_seq = d.frame_wr_seq;
    /* Дополнение: сохранить независимые seq по каналам в резервных полях для отладки (LSW) */
    /* Используем zone_count/zone1_offset временно, НЕ нарушая сигнатуру first 4 bytes (для старого парсера останутся нулями, если не читает эти поля) */
    /* Предполагается, что хост обновит парсер позже; сейчас это поле диагностическое. */
    /* ВНИМАНИЕ: если host строго ожидает zone_count==0, можно отключить. */
    /* Здесь пока не трогаем формат кадра; STAT расширяем безопасно. */
    if(streaming) g_status.flags_runtime |= VND_STFLAG_STREAMING;
    if(diag_mode_active) g_status.flags_runtime |= VND_STFLAG_DIAG_ACTIVE;
    if(vnd_pending_init) g_status.flags_runtime |= VND_STFLAG_PENDING_INIT;
    if(vnd_stream_active) g_status.flags_runtime |= VND_STFLAG_STREAM_ACTIVE;
    /* Новые поля диагностики */
    uint16_t f2 = 0;
    /* Бит0 = занятость IN EP: локальная (vnd_ep_busy) ИЛИ низкоуровневая (LL vnd_tx_busy) */
    {
        extern uint8_t USBD_VND_TxIsBusy(void);
        uint8_t ll_busy = USBD_VND_TxIsBusy();
        if(vnd_ep_busy || ll_busy) f2 |= 1u<<0;
    }
    if(vnd_tx_ready)         f2 |= 1u<<1;
    if(pending_B)            f2 |= 1u<<2;
    if(test_in_flight)       f2 |= 1u<<3;
    if(start_ack_done)       f2 |= 1u<<4;
    if(start_stat_inflight)  f2 |= 1u<<5;
    if(start_stat_planned)   f2 |= 1u<<6;
    if(pending_status)       f2 |= 1u<<7;
    if(simple_tx_mode)       f2 |= 1u<<8;
    if(diag_mode_active)     f2 |= 1u<<9;
    if(first_pair_done)      f2 |= 1u<<10; /* переместим ниже биты READY/SENDING */
    /* Доп. диагностика: наличие готовых кадров в g_frames[0] */
    {
        ChanFrame *fa = &g_frames[0][0];
        ChanFrame *fb = &g_frames[0][1];
        if (fa->st == FB_READY) f2 |= 1u<<11;
        if (fb->st == FB_READY) f2 |= 1u<<12;
        /* Новые биты: состояние SENDING для A/B чтобы различать READY и активную передачу */
        if (fa->st == FB_SENDING) f2 |= 1u<<13;
        if (fb->st == FB_SENDING) f2 |= 1u<<14;
    }
    g_status.flags2 = f2;
    g_status.sending_ch = sending_channel;
    g_status.pair_idx = 0; /* В single-slot режиме всегда 0 */
    g_status.last_tx_len = vnd_last_tx_len;
    g_status.cur_stream_seq = stream_seq;
     /* Переиспользуем резервные поля для отладки на хосте (совместимо с parser'ом):
        reserved0 = LSB dbg_prepare_calls, reserved2(low) = LSB dbg_prepare_ok,
        reserved3 = LSW frame_rd_seq (для сравнения с frame_wr_seq). */
     /* Упаковываем в reserved0: нижние 4 бита = LSB dbg_prepare_calls, старшие 4 = lastTxRC (LL) */
     do {
         extern uint8_t USBD_VND_LastTxRC(void);
         uint8_t rc = USBD_VND_LastTxRC();
         uint8_t lsb = (uint8_t)(dbg_prepare_calls & 0x0Fu);
         g_status.reserved0 = (uint8_t)((rc << 4) | lsb);
     } while(0);
      /* reserved2 переупаковка:
          низшие 4 бита = LSB dbg_prepare_ok,
          старшие 4 бита = (init_pump_attempts & 0x0F) (диагностика старта).
          Факт отправки A через pump отражаем также в flags2 бит15. */
      uint8_t prep_lsb = (uint8_t)(dbg_prepare_ok & 0x0Fu);
      uint8_t pump_att = (uint8_t)(init_pump_attempts & 0x0Fu);
      g_status.reserved2 = (uint8_t)((pump_att << 4) | prep_lsb);
     extern volatile uint32_t frame_rd_seq; /* из adc_stream.c */
    g_status.reserved3 = (uint16_t)(frame_rd_seq & 0xFFFFu);
    /* Отразим факт успешной отправки A через init-pump в flags2 бит15 ("A_fill READY" ранее не использовался) */
    if(init_pump_a_sent){ g_status.flags2 |= (uint16_t)(1u<<15); }
    /* v2 расширение: заполняем метки стадий (если доступны) */
    g_status.stage_alt1_ms = vnd_stage_alt1_ms;
    g_status.stage_start_ms = start_cmd_ms;
    g_status.stage_first_frame_ms = vnd_stage_first_frame_ms;
    
    /* v3 расширение: ДИАГНОСТИКА счётчиков нулевых буферов ADC */
    g_status.ch_zero_buffers_A = d.ch_zero_buffers[0];
    g_status.ch_zero_buffers_B = d.ch_zero_buffers[1];
    /* v4 расширение: текущий tick и метки последнего полного DMA кадра по каналам */
    do {
        uint32_t now_ms = HAL_GetTick();
        extern volatile uint32_t adc_last_full0_ms;
        extern volatile uint32_t adc_last_full1_ms;
        g_status.now_ms = now_ms;
        g_status.last_full0_ms = adc_last_full0_ms;
        g_status.last_full1_ms = adc_last_full1_ms;
    } while(0);
     /* Хак: инкремент dbg_skipped_frames отображаем в sent0/sent1 дельтах, но здесь добавим только
        косвенную диагностику: если skips растут, host увидит разницу produced_seq - sent*. Дополнительно
        можно временно печатать в CDC при отладке (сейчас лог выключен для скорости). */
    /* Выход: если буфер маленький (например EP0 64B) — отдадим только v1-часть и версию=1 */
    if(max_len < sizeof(vnd_status_v1_t)){
        g_status.version = 1;
        memcpy(dst, &g_status, 64);
        return 64;
    }
    memcpy(dst,&g_status,sizeof(g_status));
    return (uint16_t)sizeof(g_status);
}

uint8_t vnd_is_streaming(void){ return streaming; }

/* функция vnd_generate_test_sawtooth() реализована в vnd_testgen.c */

static void vnd_prepare_pair(void)
{
    uint32_t t_start = get_us_approx(); /* Начало измерения */
    
    
    dbg_prepare_calls++;
    /* РАСШИРЕНИЕ: используем кольцо пар (multi-slot) для минимизации потерь.
       Перебираем следующий свободный slot, если текущий уже READY/SENDING. */
    uint8_t active_slot = pair_fill_idx;
    for(uint8_t i=0;i<VND_PAIR_BUFFERS;i++){
        uint8_t s = (uint8_t)((pair_fill_idx + i) % VND_PAIR_BUFFERS);
        if(g_frames[s][0].st == FB_FILL && g_frames[s][1].st == FB_FILL){ active_slot = s; break; }
    }
    
    /* Убран подробный лог PREPARE_PAIR — счётчики агрегируются в периодическую статистику */
    uint16_t *ch1 = NULL, *ch2 = NULL; uint16_t samples = 0;
    (void)ch1; (void)ch2; /* могут быть неиспользованы в режиме overlay */
    uint16_t crc_a = 0; uint32_t crc_seq = 0;
    uint32_t pair_seq = 0;
    /* Локальный снапшот staging-буфера, чтобы TC не перезаписал usb_stage_bufA во время копирования */
    static uint16_t stage_copy[MAX_FRAME_SAMPLES];
    uint16_t stage_samples = 0;
    
#if USE_TEST_SAWTOOTH
    /* Тестовый режим: используем последнюю сгенерированную пару буферов */
    {
        /* Генерируем свежий буфер-пилу (1..N) перед выборкой */
        vnd_generate_test_sawtooth();
        uint16_t *tb0 = NULL, *tb1 = NULL; uint16_t avail = 0;
        if(!vnd_testgen_try_consume_latest(&tb0, &tb1, &avail)){
            return; /* нет новых данных */
        }
        ch1 = tb0; ch2 = tb1;
        samples = (avail != 0) ? avail : 302;  /* МАРКЕР #2 - fallback если avail=0 */
        (void)ch1; (void)ch2; /* используем в тестовом режиме, чтобы подавить предупреждения */
        (void)stage_samples; (void)seq; (void)static_mode; (void)static_seq;
        static uint32_t test_seq = 0; pair_seq = ++test_seq;
        /* Зафиксировать размер кадра, если ещё не задан, чтобы отправка не зависела от профиля АЦП */
        if(cur_samples_per_frame == 0){
            uint16_t eff = samples;
            if(eff > VND_MAX_SAMPLES) eff = VND_MAX_SAMPLES;
            cur_samples_per_frame = eff;
            cur_expected_frame_size = (uint16_t)(VND_FRAME_HDR_SIZE + (uint32_t)eff * 2u);
        }
    }
#else
     /* ПОЛИТИКА last-buffer-wins: выбираем САМЫЙ ПОСЛЕДНИЙ полный кадр и пропускаем старые.
         Это повышает визуальную частоту при ограниченной пропускной способности USB. */
    {
        // Читаем s_next_ring_index и отслеживаем изменения
        extern volatile uint32_t s_next_ring_index;
        static uint32_t last_current = 0xFFFFFFFF;
        static uint32_t local_seq = 0;
        
        __disable_irq();
        uint32_t current = s_next_ring_index;
        __enable_irq();
        
        // Отправляем данные ТОЛЬКО когда s_next_ring_index изменился
        if (current != last_current) {
            last_current = current;
            local_seq++;  // Инкрементируем счётчик кадров
        } else {
            return;  // Нет новых данных - пропускаем
        }
        
        // Вычисляем индекс последнего заполненного буфера
        uint32_t idx = (current + FIFO_FRAMES - 1) & (FIFO_FRAMES - 1);
        
        if (idx >= FIFO_FRAMES) return;
        
        pair_seq = local_seq;
        #if ADC_USB_STAGE_ENABLE
        __disable_irq();
        stage_samples = adc_stream_get_active_samples();
        if(stage_samples > MAX_FRAME_SAMPLES) stage_samples = MAX_FRAME_SAMPLES;
        memcpy(stage_copy, usb_stage_bufA, stage_samples * sizeof(uint16_t));
        crc_a = adc_stage_crc_a; crc_seq = adc_stage_crc_seq;
        __enable_irq();
        ch1 = stage_copy;
        #else
        // D-cache отключен глобально - можем читать напрямую
        ch1 = adc1_buffers[idx];
        #endif
        ch2 = adc2_buffers[idx];
        /* Используем актуальное значение семплов активного профиля */
        samples = adc_stream_get_active_samples();
    }
#endif
    if(samples == 0){
        /* Нет новых данных от АЦП — ничего не отправляем */
        return;
    }
    /* Выбор целевого размера кадра: профиль ADC с учётом ограничений от хоста */
    uint16_t profile_ns = adc_stream_get_active_samples();
    uint16_t target_ns = profile_ns;
    if(vnd_frame_samples_req && vnd_frame_samples_req < target_ns) target_ns = vnd_frame_samples_req;
    if(vnd_trunc_samples && vnd_trunc_samples < target_ns) target_ns = vnd_trunc_samples;
    if(target_ns > VND_MAX_SAMPLES) target_ns = VND_MAX_SAMPLES;
    /* Фиксируем формат только на target_ns. Никогда не локаемся на меньшем значении. */
    if(cur_samples_per_frame == 0){
        /* Если данных текущего кадра меньше чем target_ns, подождём следующую итерацию. */
        if(samples < target_ns){
            VND_LOG("WAIT_FULL: have=%u need=%u (profile=%u)", samples, target_ns, profile_ns);
            return;
        }
        cur_samples_per_frame = target_ns;
        cur_expected_frame_size = (uint16_t)(VND_FRAME_HDR_SIZE + (uint32_t)cur_samples_per_frame * 2u);
        VND_LOG("SIZE_LOCK %u (raw=%u req=%u trunc=%u)", cur_samples_per_frame, samples, vnd_frame_samples_req, vnd_trunc_samples);
        cdc_logf("SIZE_LOCK raw=%u req=%u trunc=%u -> lock=%u", samples, vnd_frame_samples_req, vnd_trunc_samples, cur_samples_per_frame);
    }
    /* После фиксации не отправляем неполные кадры */
    if(samples < cur_samples_per_frame){
        VND_LOG("SKIP_PARTIAL: have=%u locked=%u", samples, cur_samples_per_frame);
        dbg_partial_frame_abort++;
        return;
    }
    ChanFrame *f0 = &g_frames[active_slot][0];
    ChanFrame *f1 = &g_frames[active_slot][1];
    /* Если пара уже подготовлена (оба READY или SENDING), не переписываем */
    if((f0->st == FB_READY || f0->st == FB_SENDING) && (f1->st == FB_READY || f1->st == FB_SENDING)) return;
    /* Сбросим оба кадра в начальное состояние для заполнения */
    memset(f0->buf, 0, sizeof(f0->buf)); memset(f1->buf, 0, sizeof(f1->buf));
    f0->crc16 = f1->crc16 = 0; f0->crc_seq = f1->crc_seq = 0;
    if(crc_a){ f0->crc16 = crc_a; f0->crc_seq = crc_seq; }
    uint32_t pair_timestamp = HAL_GetTick();
    /* подробный лог пары убран для снижения нагрузки */
    /* Применяем усечение, если задано и меньше доступного */
    uint16_t use_samples = cur_samples_per_frame; /* уже определено и проверено */
    uint16_t stamp_idx = 0, stamp_seq16 = 0; /* диагностические метки в payload */
    for(uint16_t i = 0; i < use_samples; i++){
#if USE_TEST_SAWTOOTH
        /* Полный тестовый режим (путь был основной ранее, оставлен для совместимости). */
        uint16_t a = (uint16_t)(i + 1);
        uint16_t b = (uint16_t)(i + 1);
#else
    #if TEST_OVERLAY_SAWTOOTH
        /* Мягкая подмена: сохраняем ADC/DMA и async, но данные делаем детерминированной пилой. */
        uint16_t a = (uint16_t)(i + 1);
        uint16_t b = (uint16_t)(i + 1);
    #else
        uint16_t a = ch1[i];
        uint16_t b = ch2[i];
    #endif
#endif
        uint8_t *p0 = f0->buf + VND_FRAME_HDR_SIZE + 2 * i; p0[0] = (uint8_t)(a & 0xFF); p0[1] = (uint8_t)(a >> 8);
        uint8_t *p1 = f1->buf + VND_FRAME_HDR_SIZE + 2 * i; p1[0] = (uint8_t)(b & 0xFF); p1[1] = (uint8_t)(b >> 8);
    }
    f0->samples = f1->samples = use_samples; 
    /* Сохраняем индекс DMA (seq/rd) для привязки отображения на хосте */
    f0->dma_seq = f1->dma_seq = pair_seq;
    /* CRC для контроля целостности (A — из staging, если доступно; B — пересчитываем из копии) */
    #if !ADC_USB_STAGE_ENABLE
    f0->crc16 = vnd_crc16_ccitt(f0->buf + VND_FRAME_HDR_SIZE, (uint32_t)use_samples * 2u);
    f0->crc_seq = frame_rd_seq; /* ближайшее доступное значение */
    #endif
    f1->crc16 = vnd_crc16_ccitt(f1->buf + VND_FRAME_HDR_SIZE, (uint32_t)use_samples * 2u);
    f1->crc_seq = f0->crc_seq;
    /* seq в заголовке привязан к последовательности DMA (pair_seq), одинаковый для A и B */
    f0->seq = pair_seq;
    f1->seq = pair_seq;
    vnd_frame_hdr_t *h0 = (vnd_frame_hdr_t*)f0->buf; h0->timestamp = pair_timestamp;
    vnd_frame_hdr_t *h1 = (vnd_frame_hdr_t*)f1->buf; h1->timestamp = pair_timestamp;
    vnd_build_frame(f0); vnd_build_frame(f1);

     /* ВАЖНО: reserved2 на хосте используется как buffer_index (0-7).
         stamp_idx оставляем для payload-диагностики, но в заголовок пишем ID буфера. */
     uint16_t buf_idx = (uint16_t)(adc_get_buffer_parity(pair_seq) & 0x07u);
     h0->reserved2 = buf_idx;
     h1->reserved2 = buf_idx;
    if(f0->st == FB_FILL || f1->st == FB_FILL){ dbg_partial_frame_abort++; VND_LOG("build failed"); return; }
    /* Продвигаем индекс заполнения, если кадр успешно подготовлен */
    pair_fill_idx = (uint8_t)((active_slot + 1u) % VND_PAIR_BUFFERS);
    dbg_prepare_ok++;
    /* FPS: счётчик подготовленных пар */
    fps_prepare_count++;
    
    /* Измерение времени prepare */
    uint32_t t_end = get_us_approx();
    uint32_t duration = t_end - t_start;
    perf_stats.prepare_total_us += duration;
    perf_stats.prepare_count++;
}

static void vnd_build_frame(ChanFrame *cf)
{
    if(cf->samples == 0){ cf->st = FB_FILL; return; }
    uint32_t payload_len = (uint32_t)cf->samples * 2u;
    uint32_t total = VND_FRAME_HDR_SIZE + payload_len;
    vnd_frame_hdr_t *h = (vnd_frame_hdr_t*)cf->buf;
    
    h->magic = 0xA55A; h->ver = 0x01; h->flags = (cf->flags & VND_FLAGS_ADC0) ? 0x01 : 0x02; h->seq = cf->seq; h->total_samples = (uint16_t)cf->samples;
    VND_LOG("BUILD_FRAME cf_seq=%lu flags=0x%02X samples=%u", (unsigned long)cf->seq, (unsigned)h->flags, (unsigned)cf->samples);
    h->zone_count = 0; h->zone1_offset = 0; h->zone1_length = 0; h->reserved = cf->dma_seq;
    
     /* reserved2: buffer_index (0-7) для GUI. Даже/нечёт можно получить как (buffer_index & 1). */
     h->reserved2 = (uint16_t)(adc_get_buffer_parity(cf->dma_seq) & 0x07u);
    
    uint16_t crc = cf->crc16;
    if(crc == 0 && payload_len){ crc = vnd_crc16_ccitt(cf->buf + VND_FRAME_HDR_SIZE, payload_len); }
    h->crc16 = crc;
    if(crc) h->flags |= 0x04; else h->flags &= (uint8_t)~0x04u;
    cf->frame_size = (uint16_t)total;
    if(cur_expected_frame_size && cf->frame_size != cur_expected_frame_size) dbg_size_mismatch++;
    dbg_any_valid_frame = 1; cf->st = FB_READY;
}

/* Поиск индекса пары по seq (линейный поиск по короткому кольцу) */
static int __attribute__((unused)) vnd_find_pair_by_seq(uint32_t seq)
{
    for(uint8_t i=0;i<VND_PAIR_BUFFERS;i++){
        if(g_frames[i][0].st != FB_FILL && g_frames[i][0].seq == seq) return (int)i;
        if(g_frames[i][1].st != FB_FILL && g_frames[i][1].seq == seq) return (int)i;
    }
    return -1;
}

/* Асинхронный выбор и отправка одного готового кадра (A или B) */
/* Режим строгой парности по желанию: 0 — независимые каналы, 1 — строгая пара A&B на один seq */
static uint8_t vnd_strict_pairing = 0;  /* ОТКЛЮЧЕНО: каналы независимы, отправляем как только готов */

static int vnd_async_try_tx(void)
{
#if USE_TEST_SAWTOOTH
    /* В тестовом режиме не используем async-путь, чтобы не ждать ADC/DMA */
    return 0;
#endif
    /* ПРОФИЛИРОВАНИЕ: замер времени выполнения */
    static uint32_t call_count = 0;
    static uint32_t total_time_us = 0;
    static uint32_t max_time_us = 0;
    static uint32_t last_profile_log = 0;
    uint32_t start_cyc = DWT->CYCCNT;
    
    /* Новый вариант async: не полагается на заранее собранную пару.
       Используем per-channel API adc_get_frame_ch() и локальные временные буферы построения кадров.
       Сохраняем on-wire формат (общий seq = stream_seq). */
    if(vnd_ep_busy) return 0;
    if(!full_mode) return 0;
    static uint8_t last_sent_ch = 1; /* чередование при наличии обоих */

    /* Глобальные счетчики текущей пары (для async строгой парности): бит0=A, бит1=B */
    static uint8_t seq_mask = 0;            /* какие каналы уже отправлены для текущего stream_seq */
    static uint32_t seq_mask_first_ms = 0;  /* время первого кадра неполной пары */
    static uint8_t seq_partial_cnt = 0;     /* сколько кадров отправлено без полной пары */

    /* Локальные статические рабочие буферы кадров для независимого построения */
    typedef struct { uint8_t valid; uint8_t ch; uint16_t samples; uint32_t seq; uint32_t dma_seq; uint16_t frame_len; uint8_t buf[VND_FRAME_MAX_SIZE]; } tmp_frame_t;
    static tmp_frame_t tf[2];

    /* Обновить / заполнить структуру для канала, если данных ещё нет (valid=0) */
    for(int ch=0; ch<2; ++ch){
        if(ch==1 && vnd_ch_mode==0) continue; /* A-only */
        if(ch==0 && vnd_ch_mode==1) continue; /* B-only */
        if(tf[ch].valid) continue; /* уже подготовлен */
        uint16_t *abuf = NULL; uint16_t samples = 0; uint32_t dma_seq = 0; uint8_t synth = 0;
        /* Снапшоты для staging, чтобы TC не перезаписал во время копирования */
        static uint16_t stage_copy_async[MAX_FRAME_SAMPLES];
        static uint16_t stage_copy_async_b[MAX_FRAME_SAMPLES];
        if(adc_get_frame_ch((uint8_t)ch, &abuf, &samples, &dma_seq)){
            synth = 0;
        } else if(TEST_OVERLAY_SAWTOOTH){
            /* Если реальный буфер временно не готов, синтезируем кадр, чтобы гарантировать 2 канала. */
            samples = adc_stream_get_active_samples();
            if(samples == 0) samples = VND_FULL_DEFAULT_SAMPLES;
            synth = 1;
        }
        if(samples){
            if(cur_samples_per_frame == 0){
                /* Лочим текущий формат по первому пришедшему размеру (реальному или синтетическому) */
                uint16_t eff = samples;
                if(vnd_frame_samples_req && vnd_frame_samples_req < eff) eff = vnd_frame_samples_req;
                if(vnd_trunc_samples && vnd_trunc_samples < eff) eff = vnd_trunc_samples;
                if(eff > VND_MAX_SAMPLES) eff = VND_MAX_SAMPLES;
                cur_samples_per_frame = eff;
                cur_expected_frame_size = (uint16_t)(VND_FRAME_HDR_SIZE + (uint32_t)eff*2u);
                cdc_logf("FRAME_LOCK ch=%u adc_samp=%u req=%u trunc=%u -> eff=%u", ch, samples, vnd_frame_samples_req, vnd_trunc_samples, eff);
            }
            uint16_t eff = cur_samples_per_frame;
            if(!synth && eff > samples) eff = samples; /* защита, если размер профиля уменьшился внезапно */
            if(eff > VND_MAX_SAMPLES) eff = VND_MAX_SAMPLES;
            
            /* ДИАГНОСТИКА: проверяем последние 100 сэмплов DMA буфера на нули (только реальный буфер) */
            static uint32_t zero_check_count = 0; (void)zero_check_count;
            static uint32_t last_zero_check_ms = 0;
            uint32_t now_check = HAL_GetTick();
            if(!synth && now_check - last_zero_check_ms >= 5000 && eff >= 100){  /* Каждые 5 сек */
                uint16_t zeros_found = 0;
                for(uint16_t i = eff - 100; i < eff; i++){
                    if(abuf[i] == 0) zeros_found++;
                }
                cdc_logf("[USB_DIAG] CH%u: last 100 samples, %u zeros. [%u..%u]=[%u,%u,%u,%u,%u]",
                         ch, zeros_found, eff-100, eff-1,
                         abuf[eff-100], abuf[eff-50], abuf[eff-25], abuf[eff-10], abuf[eff-1]);
                last_zero_check_ms = now_check;
            }
            
            /* Если это staging A, скопируем под защитой IRQ в локальный снапшот */
            if(!synth && ADC_USB_STAGE_ENABLE){
                __disable_irq();
                uint16_t stage_ns = samples; if(stage_ns > MAX_FRAME_SAMPLES) stage_ns = MAX_FRAME_SAMPLES;
                if(ch==0){
                    memcpy(stage_copy_async, abuf, stage_ns * sizeof(uint16_t));
                    abuf = stage_copy_async;
                } else {
                    memcpy(stage_copy_async_b, abuf, stage_ns * sizeof(uint16_t));
                    abuf = stage_copy_async_b;
                }
                __enable_irq();
            }
            /* Построить payload: overlay off = реальные ADC; overlay on = детерминированные пилы */
            for(uint16_t i=0;i<eff;i++){
#if TEST_OVERLAY_SAWTOOTH
                uint16_t v = (uint16_t)(i + 1);
                if(ch == 1){ v = (uint16_t)(((i + 1u) * 2u + 512u) & 0x0FFFu); }
#else
                uint16_t v = synth ? (uint16_t)(i + 1) : abuf[i];
#if VND_ENABLE_ADC_SANITIZE
                if(!synth){
                    if(v > 4095u){ dbg_gt4095_ch[ch]++; /* считаем, но не правим */ }
                }
#endif
#endif
                uint8_t *p = tf[ch].buf + VND_FRAME_HDR_SIZE + 2u*i; p[0]=(uint8_t)(v & 0xFF); p[1]=(uint8_t)(v>>8);
            }
            /* Заголовок */
            vnd_frame_hdr_t *h = (vnd_frame_hdr_t*)tf[ch].buf;
            h->magic = 0xA55A; h->ver = 0x01; h->flags = (ch==0)?0x01:0x02; 
            h->seq = dma_seq;  /* seq привязан к DMA-кадру */
            h->timestamp = HAL_GetTick(); h->total_samples = eff; h->zone_count=0; h->zone1_offset=0; h->zone1_length=0;
            /* Пробрасываем индекс DMA-буфера канала, чтобы хост мог отрисовывать строго в порядке заполнения DMA */
            h->reserved  = dma_seq;
            
            /* reserved2 несёт buffer_index (0..7) */
            h->reserved2 = (uint16_t)(adc_get_buffer_parity(dma_seq) & 0x07u);
            
            h->crc16=0;
            uint16_t crc = vnd_crc16_ccitt(tf[ch].buf + VND_FRAME_HDR_SIZE, (uint32_t)eff * 2u);
            h->crc16 = crc; if(crc) h->flags |= 0x04; else h->flags &= (uint8_t)~0x04u;
            tf[ch].samples = eff; tf[ch].seq = stream_seq; tf[ch].dma_seq = dma_seq; tf[ch].frame_len = (uint16_t)(VND_FRAME_HDR_SIZE + eff*2u); tf[ch].ch = (uint8_t)ch; tf[ch].valid = 1;
        }
    }

    /* Выбор канала:
       1) Если валиден только один — берём его.
       2) Если оба — чередуем относительно last_sent_ch.
       3) Если ни одного — return 0. */
    int haveA = tf[0].valid ? 1:0;
    int haveB = tf[1].valid ? 1:0;
    int pick = -1;
    if(vnd_ch_mode == 2 && vnd_strict_pairing){
        /* Строгая парность: если оба готовы — чередуем, если готов только один и уже отправляли его для текущего seq — ждём второй */
        if(haveA && haveB){ pick = (last_sent_ch==0)?1:0; }
        else if(haveA && !haveB){ if(seq_mask == 0x01) return 0; pick = 0; }
        else if(haveB && !haveA){ if(seq_mask == 0x02) return 0; pick = 1; }
        else return 0;
    } else {
        /* Независимые каналы (дефолт): отправляем что есть, при наличии обоих — чередуем */
        if(haveA && !haveB) pick = 0;
        else if(haveB && !haveA) pick = 1;
        else if(haveA && haveB){ pick = (last_sent_ch==0)?1:0; }
        else return 0;
    }

    tmp_frame_t *sel = &tf[pick];
    if(!sel->valid) return 0;
    
    /* Гарантируем согласованность метаданных заголовка с DMA-кадром */
    vnd_frame_hdr_t *h = (vnd_frame_hdr_t*)sel->buf;
    h->seq = h->reserved; /* seq = dma_seq (32-битное значение) */
    h->reserved2 = (uint16_t)(adc_get_buffer_parity(h->reserved) & 0x07u);
    
    if(vnd_transmit_frame(sel->buf, sel->frame_len, 0, 0, pick==0?"ADC0-ASY2":"ADC1-ASY2") == USBD_OK){
        /* Учёт статистики предварительно (окончательная фиксация в TxCplt) */
        if(pick==0) dbg_sent_ch0_total++; else dbg_sent_ch1_total++;
        sending_channel = (uint8_t)pick; sel->valid = 0; last_sent_ch = (uint8_t)pick;
        /* Логика продвижения общего seq в async режиме.
           Базово: ждём кадры A и B (seq_mask==0x3) -> инкремент.
           Проблема: если один канал (например B) долго не приходит, мы зацикливаемся на одном seq.
           Решение: fallback по таймауту или числу односторонних кадров. */
        uint32_t now_ms_local = HAL_GetTick();
        if(vnd_ch_mode==2 && vnd_strict_pairing){
            seq_mask |= (uint8_t)(1u<<pick);
            if(seq_mask_first_ms == 0) seq_mask_first_ms = now_ms_local;
            if(seq_mask == 0x3){
                /* Оба канала отправили по кадру текущего seq — продвигаем */
                seq_mask = 0; seq_mask_first_ms = 0; seq_partial_cnt = 0;
                stream_seq++; dbg_produced_seq++;
            } else {
                /* Строгая парность: не продвигаем seq, пока не отправлены оба канала. */
                seq_partial_cnt++;
                uint32_t dt_ms = (now_ms_local >= seq_mask_first_ms) ? (now_ms_local - seq_mask_first_ms) : (0xFFFFFFFFu - seq_mask_first_ms + 1u + now_ms_local);
                if(dt_ms > 1000 && (seq_partial_cnt % 8) == 0){
                    VND_LOG("ASYNC_WAIT_PAIR seq=%lu mask=0x%02X cnt=%u dt=%lums", (unsigned long)stream_seq, (unsigned)seq_mask, (unsigned)seq_partial_cnt, (unsigned long)dt_ms);
                }
            }
        } else if (vnd_ch_mode==2 && !vnd_strict_pairing){
            /* Независимые каналы: продвигаем seq каждый кадр, сохраняя общую временную шкалу */
            stream_seq++; dbg_produced_seq++;
            seq_mask = 0; seq_mask_first_ms = 0; seq_partial_cnt = 0; /* сброс */
        } else {
            /* В режиме single-channel seq просто инкрементируется каждый кадр */
            stream_seq++; dbg_produced_seq++;
            seq_mask = 0; seq_mask_first_ms = 0; seq_partial_cnt = 0; /* сброс */
        }
        /* ПРОФИЛИРОВАНИЕ: успешная отправка */
        uint32_t end_cyc = DWT->CYCCNT;
        uint32_t elapsed_cyc = (end_cyc >= start_cyc) ? (end_cyc - start_cyc) : (0xFFFFFFFFu - start_cyc + end_cyc + 1);
        uint32_t elapsed_us = elapsed_cyc / (SystemCoreClock / 1000000u);
        call_count++;
        total_time_us += elapsed_us;
        if(elapsed_us > max_time_us) max_time_us = elapsed_us;
        
        uint32_t now_profile = HAL_GetTick();
        if(now_profile - last_profile_log >= 5000){
            uint32_t avg_us = call_count ? (total_time_us / call_count) : 0;
            printf("[PROF_TX] calls=%lu avg=%lu.%luus max=%lu.%luus\r\n",
                   call_count, avg_us, (elapsed_us%10), max_time_us, (max_time_us%10));
            call_count = 0; total_time_us = 0; max_time_us = 0;
            last_profile_log = now_profile;
        }
        return 1;
    }
    
    /* ПРОФИЛИРОВАНИЕ: не отправлено (нет данных) */
    uint32_t end_cyc = DWT->CYCCNT;
    uint32_t elapsed_cyc = (end_cyc >= start_cyc) ? (end_cyc - start_cyc) : (0xFFFFFFFFu - start_cyc + end_cyc + 1);
    uint32_t elapsed_us = elapsed_cyc / (SystemCoreClock / 1000000u);
    call_count++;
    total_time_us += elapsed_us;
    if(elapsed_us > max_time_us) max_time_us = elapsed_us;
    
    return 0;
}

/* allow_zero_samples используется как флаги:
 *  bit0 (1): разрешить total_samples==0
 *  bit1 (2): разрешить длину >= ожидаемой и кратную 64 (для паддинга до MPS)
 */
static int vnd_validate_frame(const uint8_t *buf, uint16_t len, uint8_t expect_test, uint8_t allow_flags)
{
    (void)expect_test;
    if (!buf || len < VND_FRAME_HDR_SIZE)
        return 0;
    const vnd_frame_hdr_t *h = (const vnd_frame_hdr_t*)buf;
    if (h->magic != 0xA55A)
        return 0;
    if (h->total_samples > VND_MAX_SAMPLES)
        return 0;
    if (!(allow_flags & 0x01) && h->total_samples == 0)
        return 0;
    {
        uint16_t expected = (uint16_t)(VND_FRAME_HDR_SIZE + h->total_samples * 2u);
        if (len != expected) {
            /* Разрешаем «припадиненные» кадры: длина >= expected и кратна 64 байтам (FS/HS совместимо) */
            if ((allow_flags & 0x02) == 0) return 0;
            if (len < expected) return 0;
            if ((len % 64u) != 0u) return 0;
        }
    }
    return 1;
}

static USBD_StatusTypeDef __attribute__((unused)) vnd_transmit_frame(uint8_t *buf, uint16_t len, uint8_t is_test, uint8_t allow_zero_samples, const char *tag)
{
    (void)is_test; dbg_tx_attempt++;
    if(!vnd_validate_frame(buf, len, is_test, allow_zero_samples)){ dbg_tx_reject++; vnd_error_counter++; if(vnd_last_error == 0) vnd_last_error = 3; VND_LOG("TX_REJECT %s", tag ? tag : "?"); return USBD_FAIL; }
    if(!vnd_tx_ready || vnd_ep_busy || vnd_inflight){ vnd_error_counter++; VND_LOG("TX_SKIP busy/inflight tag=%s", tag ? tag : "?"); return USBD_BUSY; }
    vnd_tx_ready = 0; vnd_ep_busy = 1; vnd_inflight = 1; vnd_last_tx_len = len; vnd_last_tx_start_ms = HAL_GetTick();

    /* Фиксируем метаданные кадра; НЕ переписываем seq перед отправкой.
       Последовательность пар контролируется строго: seq фиксируется при сборке пары,
       а инкремент выполняется только по завершению B (TxCplt). Это исключает случаи,
       когда задержавшийся B получает «будущий» seq. */
    uint8_t is_frame=0, flags=0; uint32_t seq_field=0; int rewrote_seq = 0;
    if(len >= VND_FRAME_HDR_SIZE){
        vnd_frame_hdr_t *hh = (vnd_frame_hdr_t*)buf;
        if(hh->magic == 0xA55A){ is_frame = 1; flags = hh->flags; seq_field = hh->seq; }
    }

    /* Зафиксируем точный тип текущего кадра в полёте */
    if(len >= VND_FRAME_HDR_SIZE){ const vnd_frame_hdr_t *hh = (const vnd_frame_hdr_t*)buf; if(hh->magic==0xA55A){ inflight_is_frame = 1; inflight_flags = hh->flags; inflight_seq = hh->seq; } else { inflight_is_frame = 0; inflight_flags = 0; inflight_seq = 0; } } else { inflight_is_frame = 0; inflight_flags = 0; inflight_seq = 0; }
    (void)flags; (void)seq_field; /* для сборок с отключёнными логами */
    USBD_StatusTypeDef rc = USBD_VND_Transmit(&hUsbDeviceHS, buf, len);
    if(rc == USBD_BUSY){
        dbg_resend_blocked++; vnd_error_counter++; if(vnd_last_error == 0) vnd_last_error = 4;
        /* Диагностика LL: получим last rc/len и флаг занятости */
        extern uint8_t USBD_VND_TxIsBusy(void);
        extern uint8_t USBD_VND_LastTxRC(void);
        extern uint16_t USBD_VND_LastTxLen(void);
        uint8_t ll_busy = USBD_VND_TxIsBusy(); uint8_t ll_rc = USBD_VND_LastTxRC(); uint16_t ll_len = USBD_VND_LastTxLen();
        (void)ll_busy; (void)ll_rc; (void)ll_len;
        VND_LOG("TX_BUSY tag=%s len=%u ll_busy=%u last_rc=%u last_len=%u", tag?tag:"?", (unsigned)len, (unsigned)ll_busy, (unsigned)ll_rc, (unsigned)ll_len);
        vnd_tx_ready = 1; vnd_ep_busy = 0; vnd_inflight = 0;
    }
    else {
        /* Фиксируем метаданные ТОЛЬКО после успешного запуска передачи, иначе не сместим FIFO зря */
        vnd_tx_meta_after(buf, len);
        if(is_frame){
            const vnd_frame_hdr_t *lh = (const vnd_frame_hdr_t*)buf;
            (void)lh;
            if(rewrote_seq){
                VND_LOG("SEND tag=%s hdr.seq=%lu (rewrote) flags=0x%02X cur_stream_seq=%lu len=%u", tag ? tag : "?", (unsigned long)lh->seq, (unsigned)lh->flags, (unsigned long)stream_seq, len);
            } else {
                VND_LOG("SEND tag=%s hdr.seq=%lu flags=0x%02X cur_stream_seq=%lu len=%u", tag ? tag : "?", (unsigned long)lh->seq, (unsigned)lh->flags, (unsigned long)stream_seq, len);
            }
        } else {
            VND_LOG("SEND tag=%s (no-hdr) cur_stream_seq=%lu len=%u", tag ? tag : "?", (unsigned long)stream_seq, len);
        }
        /* Дублирование в CDC (работает для кадров ADC0/ADC1 и диагностических) */
        vnd_cdc_duplicate_preview(buf, len, tag);
    }
    return rc;
}

/* Упрощённая диагностическая пара A/B: подготовка буферов по текущему cur_samples_per_frame */
static void vnd_diag_prepare_pair(uint32_t seq, uint16_t samples)
{
    if(samples == 0) samples = VND_DEFAULT_TEST_SAMPLES;
    if(samples > VND_MAX_SAMPLES) samples = VND_MAX_SAMPLES;
    uint16_t base_len = (uint16_t)(VND_FRAME_HDR_SIZE + (uint32_t)samples*2u);
    /* Паддинг до кратности 512 (HS max packet); кратность 64 обеспечивает совместимость и для FS */
    uint16_t pad_unit = 512u;
    uint16_t padded = (uint16_t)(((uint32_t)(base_len + (pad_unit-1u)) / pad_unit) * pad_unit);
    if (padded < base_len) padded = base_len; /* защита от переполнения (не ожидается) */
    diag_frame_len = padded;
    /* A */
    memset(diag_a_buf, 0, diag_frame_len);
    vnd_frame_hdr_t *ha = (vnd_frame_hdr_t*)diag_a_buf;
    ha->magic = 0xA55A; ha->ver = 0x01; ha->flags = 0x01; ha->seq = seq; ha->timestamp = HAL_GetTick(); ha->total_samples = samples;
    for(uint16_t i=0;i<samples;i++){ uint16_t v=i; diag_a_buf[VND_FRAME_HDR_SIZE+2*i]=(uint8_t)(v & 0xFF); diag_a_buf[VND_FRAME_HDR_SIZE+2*i+1]=(uint8_t)(v>>8); }
    /* B */
    memset(diag_b_buf, 0, diag_frame_len);
    vnd_frame_hdr_t *hb = (vnd_frame_hdr_t*)diag_b_buf;
    hb->magic = 0xA55A; hb->ver = 0x01; hb->flags = 0x02; hb->seq = seq; hb->timestamp = ha->timestamp; hb->total_samples = samples;
    for(uint16_t i=0;i<samples;i++){ uint16_t v=0x0100u+i; diag_b_buf[VND_FRAME_HDR_SIZE+2*i]=(uint8_t)(v & 0xFF); diag_b_buf[VND_FRAME_HDR_SIZE+2*i+1]=(uint8_t)(v>>8); }
    diag_current_pair_seq = seq; /* зафиксируем seq текущей пары для гарантии совпадения A/B */
}

/* Попытка отправки диагностического кадра: A, затем (если не A-only) B */
static int vnd_diag_try_tx(void)
{
    if(vnd_ep_busy) return 0;
    if(diag_frame_len == 0) return 0;
#if VND_DIAG_SEND_A_ONLY
    if(!vnd_validate_frame(diag_a_buf, diag_frame_len, 0, 0x02)) return 0; /* allow padding */
    if(vnd_transmit_frame(diag_a_buf, diag_frame_len, 0, 0, "ADC0") == USBD_OK){
        sending_channel = 0; /* для корректной статистики */
        return 1;
    } else { return 0; }
#else
    /* В DIAG режиме используем pending_B как главный флаг: если он установлен — шлём B, иначе A */
    if(pending_B)
    {
        if(!vnd_validate_frame(diag_b_buf, diag_frame_len, 0, 0x02)) return 0; /* allow padding */
        /* Прямое копирование полей из заголовка A: seq/timestamp/ns всегда совпадают в паре */
        if(diag_frame_len >= VND_FRAME_HDR_SIZE){
            vnd_frame_hdr_t *hb = (vnd_frame_hdr_t*)diag_b_buf;
            const vnd_frame_hdr_t *ha = (const vnd_frame_hdr_t*)diag_a_buf;
            if(hb->magic == 0xA55A && ha->magic == 0xA55A){
                hb->seq = ha->seq;
                hb->timestamp = ha->timestamp;
                hb->total_samples = ha->total_samples;
            }
        }
        if(vnd_transmit_frame(diag_b_buf, diag_frame_len, 0, 0x02, "ADC1") == USBD_OK){
            sending_channel = 1; /* информативно */
            /* печать в CDC отключена для максимальной скорости */
            return 1;
        } else { return 0; }
    }
    /* Иначе шлём A, когда EP свободен */
    /* Allow padded A-frames as well (len >= expected and multiple of 64/512) */
    if(!vnd_validate_frame(diag_a_buf, diag_frame_len, 0, 0x02)) return 0; /* allow padding */
    /* Безопасная синхронизация seq для A: если по какой-то причине новая пара
       ещё не была собрана, принудительно проставим актуальный stream_seq в hdr */
    if(diag_frame_len >= VND_FRAME_HDR_SIZE){
        vnd_frame_hdr_t *ha = (vnd_frame_hdr_t*)diag_a_buf;
        if(ha->magic == 0xA55A){
            if(ha->seq != stream_seq){
                ha->seq = stream_seq;
            }
            /* Всегда фиксируем текущий seq A как seq пары для последующего B */
            diag_current_pair_seq = ha->seq;
        }
    }
    if(vnd_transmit_frame(diag_a_buf, diag_frame_len, 0, 0x02, "ADC0") == USBD_OK){
        sending_channel = 0; /* ожидаем B после TxCplt A */
        /* Закрываем STAT-окно между A и B: сразу помечаем ожидание B */
        pending_B = 1; pending_B_since_ms = HAL_GetTick();
        /* печать в CDC отключена для максимальной скорости */
        return 1;
    } else { return 0; }
#endif
}

/* === Немедленная отправка B после завершения A (внутри TxCplt) === */
static int vnd_try_send_B_immediate(void)
{
    if(vnd_ep_busy) return 0;
    /* DIAG режим: используем заранее подготовленный diag_b_buf с текущим seq */
    if(diag_mode_active){
        if(!pending_B) return 0;
        if(diag_frame_len == 0) return 0;
        if(diag_frame_len >= VND_FRAME_HDR_SIZE){
            vnd_frame_hdr_t *hb = (vnd_frame_hdr_t*)diag_b_buf;
            const vnd_frame_hdr_t *ha = (const vnd_frame_hdr_t*)diag_a_buf;
            /* В DIAG заголовок B копируем из A для гарантированной идентичности пары */
            if(hb->magic == 0xA55A && ha->magic == 0xA55A){
                hb->seq = ha->seq;
                hb->timestamp = ha->timestamp;
                hb->total_samples = ha->total_samples;
            }
        }
        if(!vnd_validate_frame(diag_b_buf, diag_frame_len, 0, 0x02)) return 0;
        if(vnd_transmit_frame(diag_b_buf, diag_frame_len, 0, 0x02, "ADC1-IMM") == USBD_OK){
            sending_channel = 1; /* B в полёте */
            return 1;
        }
        return 0;
    }
    /* Полный режим: отправляем B из g_frames[0], если READY */
    ChanFrame *fB = &g_frames[0][1];
    if(fB->st != FB_READY) return 0;
    if(vnd_transmit_frame(fB->buf, fB->frame_size, 0, 0, "ADC1-IMM") == USBD_OK){
        fB->st = FB_SENDING; sending_channel = 1;
        return 1;
    }
    return 0;
}

/* === Немедленная отправка A следующей пары после завершения B (внутри TxCplt) === */
static int vnd_try_send_A_nextpair_immediate(void)
{
    if(vnd_ep_busy) return 0;
    /* После B мы уже сдвинули pair_send_idx/seq во внешней логике — тут пытаемся сразу выстрелить A новой пары */
    if(diag_mode_active){
        /* Подготовим следующую пару под новый stream_seq и сразу пошлём A */
        vnd_diag_prepare_pair(stream_seq, cur_samples_per_frame ? cur_samples_per_frame : diag_samples);
        if(!vnd_validate_frame(diag_a_buf, diag_frame_len, 0, 0x02)) return 0;
        if(vnd_transmit_frame(diag_a_buf, diag_frame_len, 0, 0x02, "ADC0-IMM") == USBD_OK){
            sending_channel = 0; pending_B = 1; pending_B_since_ms = HAL_GetTick();
            return 1;
        }
        return 0;
    }
    /* Полный режим: убедимся, что в буфере подготовки есть готовый A; если нет — попробуем собрать */
    ChanFrame *fA = &g_frames[0][0];
    if(fA->st != FB_READY){
        vnd_prepare_pair();
        fA = &g_frames[0][0];
        if(fA->st != FB_READY) return 0;
    }
    if(vnd_transmit_frame(fA->buf, fA->frame_size, 0, 0, "ADC0-IMM") == USBD_OK){
        fA->st = FB_SENDING; sending_channel = 0; pending_B = 1; pending_B_since_ms = HAL_GetTick();
        return 1;
    }
    return 0;
}

/* Лог структуры заголовка кадра для отладки */
static void vnd_log_hdr_layout(void){
#if VND_ENABLE_LOG
    VND_LOG("HDR sz=%u off.magic=%u off.seq=%u off.timestamp=%u off.total=%u", (unsigned)sizeof(vnd_frame_hdr_t),
            (unsigned)offsetof(vnd_frame_hdr_t,magic), (unsigned)offsetof(vnd_frame_hdr_t,seq),
            (unsigned)offsetof(vnd_frame_hdr_t,timestamp), (unsigned)offsetof(vnd_frame_hdr_t,total_samples));
#endif
}

/* Экстренный keepalive: формирует короткий тестовый кадр даже если test_sent уже помечен по FALLTHRU,
   при условии что dbg_tx_cplt==0 (ни одного подтверждённого TX) и EP свободен. */
static void __attribute__((unused)) vnd_emergency_keepalive(uint32_t now_ms)
{
    if(vnd_ep_busy) return;
    if(dbg_tx_cplt != 0) return; /* уже что-то передали успешно */
    /* Не чаще чем раз в 40 мс */
    static uint32_t last_emerg_ms = 0;
    if(now_ms - last_emerg_ms < 40) return;
    last_emerg_ms = now_ms;
    uint8_t tbuf[32+16]; memset(tbuf,0,sizeof(tbuf));
    vnd_frame_hdr_t *h = (vnd_frame_hdr_t*)tbuf;
    h->magic = 0xA55A; h->ver = 0x01; h->flags = 0x80; h->seq = 0; h->timestamp = HAL_GetTick(); h->total_samples = 8;
    for(uint16_t i=0;i<8;i++){ tbuf[32+2*i]=(uint8_t)i; tbuf[32+2*i+1]=(uint8_t)(i>>8); }
    vnd_tx_ready = 0; vnd_ep_busy = 1; vnd_last_tx_len = sizeof(tbuf); vnd_last_tx_start_ms = HAL_GetTick();
    if(USBD_VND_Transmit(&hUsbDeviceHS, tbuf, sizeof(tbuf)) == USBD_OK){
        vnd_tx_meta_after(tbuf, (uint16_t)sizeof(tbuf));
        test_in_flight = 1; VND_LOG("EMERG_TEST_TX (no TXCPLT yet) depth=%u", (unsigned)vnd_tx_meta_depth());
    } else { vnd_tx_ready = 1; vnd_ep_busy = 0; VND_LOG("EMERG_TEST_BUSY"); }
}

/* Отправка единственного тестового кадра (строго из таска) */
static void vnd_try_send_test_from_task(void)
{
#if VND_DISABLE_TEST
    /* Тестовые кадры запрещены — ничего не делаем */
    return;
#endif
    if(!streaming) return;
    if(diag_mode_active) return;
    /* Как только начали готовить/слать реальные кадры — больше не шлём TEST */
    if(dbg_any_valid_frame) return;
    if(test_sent || test_in_flight) return;
    if(vnd_ep_busy) return;
    /* Бэкофф: не пытаться слать тест чаще, чем раз в 50 мс */
    static uint32_t last_try_ms = 0;
    uint32_t now = HAL_GetTick();
    if (now - last_try_ms < 50) return;
    last_try_ms = now;
    uint8_t tbuf[32+16]; memset(tbuf,0,sizeof(tbuf));
    vnd_frame_hdr_t *h = (vnd_frame_hdr_t*)tbuf;
    h->magic = 0xA55A; h->ver = 0x01; h->flags = 0x80; h->seq = 0; h->timestamp = HAL_GetTick(); h->total_samples = 8;
    for(uint16_t i=0;i<8;i++){ tbuf[32+2*i]=(uint8_t)i; tbuf[32+2*i+1]=(uint8_t)(i>>8); }
    vnd_tx_ready = 0; vnd_ep_busy = 1; vnd_last_tx_len = sizeof(tbuf); vnd_last_tx_start_ms = HAL_GetTick();
    if(USBD_VND_Transmit(&hUsbDeviceHS, tbuf, sizeof(tbuf)) == USBD_OK){
        vnd_tx_meta_after(tbuf, (uint16_t)sizeof(tbuf));
        test_in_flight = 1;
        VND_LOG("TEST_TX from task depth=%u", (unsigned)vnd_tx_meta_depth());
        /* Не пытаемся сразу слать рабочий кадр — ждём завершение TEST,
           чтобы не попасть на BUSY/ZLP гонки. Далее обычная логика отправит A/B. */
    } else {
        VND_LOG("TEST_TX busy/fail");
        vnd_tx_ready = 1; vnd_ep_busy = 0;
    }
}

void __attribute__((unused)) vnd_diag_send64_once(void)
{
    static uint8_t sent = 0;
    if(sent) return;
    if(hUsbDeviceHS.dev_state != USBD_STATE_CONFIGURED) return; /* ждём конфигурации */
    if(streaming) return; /* чтобы не мешать основной логике */
    if(vnd_ep_busy) return; /* подождём освобождения */
    uint8_t diag[64];
    for(int i = 0; i < 64; i++) diag[i] = (uint8_t)i;
    memcpy(diag, "STAT", 4); /* чтобы на хосте легко найти */
    diag[4] = 0x42;           /* тестовая версия */
    vnd_tx_ready = 0; vnd_ep_busy = 1; vnd_last_tx_len = sizeof(diag); vnd_last_tx_start_ms = HAL_GetTick();
    USBD_StatusTypeDef rc = USBD_VND_Transmit(&hUsbDeviceHS, diag, (uint16_t)sizeof(diag));
    if(rc == USBD_OK){
        /* Чтобы USBD_VND_TxCplt() не получил пустую мету — положим служебную запись */
        vnd_tx_meta_after(diag, (uint16_t)sizeof(diag)); /* is_frame=0 */
        sent = 1;
        VND_LOG("DIAG64 rc=OK");
    } else {
        VND_LOG("DIAG64 rc=%d", rc);
        vnd_tx_ready = 1; vnd_ep_busy = 0; /* откатим флаги при неудаче */
    }
}

/* Основной периодический таск */
void __attribute__((unused)) Vendor_Stream_Task(void)
{
    dbg_task_calls++;
    /* Сервис EP0: выполняем отложенные SOFT/DEEP RESET без блокировки SETUP */
    USBD_VND_ProcessControlRequests();
    /* ПРИОРИТЕТ 0: если не сконфигурировано стримингом — обслуживаем оффлайн-STAT */
    if(!streaming)
    {
    if(!vnd_ep_busy && !vnd_inflight){ vnd_try_send_pending_status_from_task(); }
        vnd_tick_flag = 0;
        return;
    }
    /* подавляем частый шум лога при каждом заходе в таск */
    if(!dbg_printed_sizes)
    {
        vnd_log_hdr_layout();
        dbg_printed_sizes = 1;
    }

    /* DEBUG: раз в секунду печатаем sample[95] по всем 32 DMA буферам (A и B) */
    {
        static uint32_t last_s95_ms = 0;
        uint32_t now_ms = HAL_GetTick();
        if (now_ms - last_s95_ms >= 1000u) {
            last_s95_ms = now_ms;
            adc_stream_print_sample95_all_buffers();
        }
    }

    /* Если по START запросили перезапуск ADC/DMA — делаем это здесь (в main-loop),
       до любых попыток передачи по USB. */
    if (vnd_adc_restart_request) {
        vnd_adc_restart_request = 0;
        HAL_StatusTypeDef rrc = adc_stream_restart(NULL, NULL);
        /* Обновим снапшот DMA после перезапуска, чтобы таймауты/диагностика не срабатывали по старым значениям */
        {
            adc_stream_debug_t dbg;
            adc_stream_get_debug(&dbg);
            dma_snapshot_full0 = dbg.dma_full0;
            dma_snapshot_full1 = dbg.dma_full1;
        }
        cdc_logf("EVT ADC_RESTART on START rc=%d", (int)rrc);
        /* Попросим немедленный kick TX после реинициализации */
        vnd_tx_kick = 1;
    }
    /* СУПЕР-ПРИОРИТЕТ: если пришёл STOP — разрешаем только ACK-STAT, полностью блокируем стрим */
    if (stop_request) {
        if (!vnd_ep_busy) {
            if (!pending_status) pending_status = 1; /* гарантируем наличие отложенного STAT */
            vnd_try_send_pending_status_from_task();
        }
        if (vnd_tick_flag) vnd_tick_flag = 0;
        /* Логируем попытки передачи после STOP */
        if (pending_B || test_sent) {
            VND_LOG("BLOCK: попытка передачи после STOP (pending_B=%d, test_sent=%d)", pending_B, test_sent);
            pending_B = 0; test_sent = 0; sending_channel = 0xFF;
        }
        return; /* ждём TxCplt ACK-STOP */
    }

    /* Универсальная антиклин‑разблокировка EP: если IN висит >200 мс — принудительно снимаем busy */
    do {
        uint32_t now_ms = HAL_GetTick();
        extern uint8_t USBD_VND_TxIsBusy(void);
        uint8_t vbusy = USBD_VND_TxIsBusy();
        if ( (vnd_ep_busy || vbusy) && vnd_last_tx_start_ms != 0 && (now_ms - vnd_last_tx_start_ms) > 200) {
            extern void USBD_VND_ForceTxIdle(void);
            USBD_VND_ForceTxIdle();
            vnd_ep_busy = 0; vnd_tx_ready = 1;
            VND_LOG("EP_UNSTUCK after %lums (len=%u) vbusy=%u", (unsigned long)(now_ms - vnd_last_tx_start_ms), (unsigned)vnd_last_tx_len, (unsigned)vbusy);
        }
    } while(0);

    /* ACK-STAT на START: отключено, чтобы не занимать Vendor IN перед первой парой.
       Хост может опрашивать состояние через GET_STATUS (EP0). */
    if(status_ack_pending){ start_ack_done = 1; status_ack_pending = 0; }

    uint32_t now = HAL_GetTick();
    /* Fallback: если ACK так и не ушёл в полёт (status_ack_pending держится),
       не ждём бесконечно — считаем ACK выполненным и продолжаем к TEST. */
    if(!test_sent && !start_ack_done && status_ack_pending){
        if(now - start_cmd_ms > 200){
            start_ack_done = 1; status_ack_pending = 0;
            vnd_ep_busy = 0; vnd_tx_ready = 1;
            extern void USBD_VND_ForceTxIdle(void); USBD_VND_ForceTxIdle();
            VND_LOG("ACK_FALLBACK(no inflight) -> allow TEST");
            if(!vnd_ep_busy){ vnd_try_send_test_from_task(); }
        }
    }

    if(!test_sent && start_stat_inflight) {
        if(now - vnd_last_tx_start_ms > 80) {
            /* На некоторых хостах ACK-STAT может не завершиться DataIn/ZLP. Разблокируем вручную. */
            start_stat_inflight = 0; start_ack_done = 1; vnd_ep_busy = 0; vnd_tx_ready = 1;
            extern void USBD_VND_ForceTxIdle(void); USBD_VND_ForceTxIdle();
            VND_LOG("ACK_TIMEOUT -> unlock test");
            /* Сразу отдадим ещё один STAT (если был queued) и попробуем отправить TEST */
            if(pending_status && !vnd_ep_busy){
                vnd_try_send_pending_status_from_task();
            }
            if(!vnd_ep_busy){
#if !VND_DISABLE_TEST
                vnd_try_send_test_from_task();
#endif
            }
        }
    }
    /* Аварийный обход: если тест не ушёл за разумное время после START — считаем его выполненным и продолжаем */
    if(!test_sent && (now - start_cmd_ms) > 160) {
        test_in_flight = 0;
        test_sent = 1;
        start_ack_done = 1;
        VND_LOG("TEST_FALLTHRU after %lums -> proceed to A/B", (unsigned long)(now - start_cmd_ms));
    }

    /* ASYNC MODE: используем только vnd_async_try_tx() (без legacy prepare_pair/pump).
       Иначе старый парный путь может потреблять/копировать ADC и перегружать CPU/логи,
       что приводит к остановке/редким кадрам на хосте после первых нескольких фреймов. */
#if !USE_TEST_SAWTOOTH
    if(async_mode && full_mode && !diag_mode_active){
        if(!vnd_ep_busy){ (void)vnd_async_try_tx(); }
        if(vnd_tick_flag) vnd_tick_flag = 0;
        vnd_cdc_periodic_stats(now);
        return;
    }
#endif
    /* ВАЖНО: сначала попробуем подготовить пару A/B, чтобы не зациклиться на ранних STAT.
       Подготовка пары не зависит от занятости EP, поэтому убираем лишний гейтинг по vnd_ep_busy. */
    {
        ChanFrame *fA0 = &g_frames[0][0];
        if(fA0->st != FB_READY){ vnd_prepare_pair(); }
    }
    /* Раннее окно для GET_STATUS до первой пары — отключено: STAT по IN только между парами. */
    /* Подавляем TEST в фазе pending_init: сосредоточиться на ускорении первой A пары */
    if(vnd_pending_init){
        /* Быстрый init-pump: до 200 мс после START каждые ~2 мс пробуем подготовить и отправить A
           Расширено: используем текущий pair_fill_idx вместо жёсткого [0], добавлен ранний STAT и ослабленный fresh gating. */
        static uint32_t last_pump_ms = 0;
        extern volatile uint32_t frame_wr_seq;
        static uint32_t start_wr_seq_snapshot = 0; /* первый wr_seq на момент START */
        if(start_wr_seq_snapshot == 0){ start_wr_seq_snapshot = frame_wr_seq; }
        if(start_cmd_ms && (now - start_cmd_ms) < 200){
            if(now - last_pump_ms > 2){
                last_pump_ms = now;
                init_pump_attempts++;
                ChanFrame *fAfill = &g_frames[pair_fill_idx][0];
                if(fAfill->st != FB_READY){ vnd_prepare_pair(); fAfill = &g_frames[pair_fill_idx][0]; }
                     /* Ранний relax: первые 20 мс достаточно одного инкремента wr_seq, далее требуем +2.
                         Доп. послабление: после 100 мс позволяем отправить первую A даже без прироста wr_seq
                         (на случай, если ADC успел подготовить кадр, но счётчик ещё не вырос). */
                 uint32_t age_ms = now - start_cmd_ms;
                 #if USE_TEST_SAWTOOTH
                     uint8_t fresh_ok = 1u; /* в тестовом режиме не ждём рост wr_seq от ADC/DMA */
                 #else
                     uint8_t fresh_ok = (age_ms < 20) ? (frame_wr_seq > start_wr_seq_snapshot ? 1u : 0u)
                                                        : (frame_wr_seq > start_wr_seq_snapshot + 1 ? 1u : 0u);
                 #endif
                /* Защитная инициализация размера кадра если ADC уже дал конфигурацию, но SIZE_LOCK не прошёл */
                if(cur_samples_per_frame == 0 && age_ms > 25){
                    uint16_t prof_s = adc_stream_get_active_samples();
                    uint16_t eff_s = prof_s;
                    if(vnd_frame_samples_req && vnd_frame_samples_req < eff_s) eff_s = vnd_frame_samples_req;
                    if(vnd_trunc_samples && vnd_trunc_samples < eff_s) eff_s = vnd_trunc_samples;
                    if(eff_s > VND_MAX_SAMPLES) eff_s = VND_MAX_SAMPLES;
                    if(eff_s){
                        cur_samples_per_frame = eff_s;
                        cur_expected_frame_size = (uint16_t)(VND_FRAME_HDR_SIZE + (uint32_t)cur_samples_per_frame*2u);
                    }
                }
                /* Ещё один страховочный замок: после 60 мс если размер всё ещё 0 — попробуем взять текущий active_samples и вывести диагностику */
                if(cur_samples_per_frame == 0 && age_ms == 60){
                    uint16_t prof_s2 = adc_stream_get_active_samples();
                    uint16_t eff_s2 = prof_s2;
                    if(vnd_frame_samples_req && vnd_frame_samples_req < eff_s2) eff_s2 = vnd_frame_samples_req;
                    if(vnd_trunc_samples && vnd_trunc_samples < eff_s2) eff_s2 = vnd_trunc_samples;
                    if(eff_s2 > VND_MAX_SAMPLES) eff_s2 = VND_MAX_SAMPLES;
                    if(eff_s2){
                        cur_samples_per_frame = eff_s2;
                        cur_expected_frame_size = (uint16_t)(VND_FRAME_HDR_SIZE + (uint32_t)cur_samples_per_frame*2u);
                    }
                    cdc_logf("INIT_DBG age=%lu wr=%lu fA_st=%u cur_s=%u ep_busy=%u",
                             (unsigned long)age_ms, (unsigned long)frame_wr_seq, (unsigned)fAfill->st,
                             (unsigned)cur_samples_per_frame, (unsigned)vnd_ep_busy);
                }
                /* ======================================================================
                 * КРИТИЧНО: ранний STAT для инициации хендшейка с хостом.
                 * Условие: ТОЛЬКО если streaming=1 (значит START уже был получен) И
                 * ещё не отправлена первая пара (first_pair_done=0).
                 * Защита от RACE: проверяем streaming=1, что гарантирует что STOP не был вызван.
                 * ====================================================================== */
                if(streaming && !vnd_stream_active && !pending_status && age_ms > 30 && age_ms < 150 && !first_pair_done){
                    pending_status = 1;
                    vnd_status_permit_once = 1;
                    if(!vnd_ep_busy && !vnd_inflight){ vnd_try_send_pending_status_from_task(); }
                }
                /* Разрешаем первую A, если кадр готов и либо fresh_ok, либо прошло уже >100 мс (fallback) */
                if(fAfill->st == FB_READY && (fresh_ok || age_ms > 100) && !vnd_ep_busy){
                    if(vnd_transmit_frame(fAfill->buf, fAfill->frame_size, 0, 0, "ADC0-PUMP") == USBD_OK){
                        fAfill->st = FB_SENDING; sending_channel = 0;
                        pending_B = (vnd_ch_mode == 0)?0:1; if(pending_B) pending_B_since_ms = HAL_GetTick();
                        vnd_pending_init = 0; vnd_stream_active = 1; init_pump_a_sent++;
                        VND_LOG("INIT_PUMP_A queued size=%u wr_seq=%lu age=%lums fresh=%u", (unsigned)fAfill->frame_size, (unsigned long)frame_wr_seq, (unsigned long)age_ms, (unsigned)fresh_ok);
                        /* После первой A выходим из init-pump */
                    }
                }
            }
        }
    } else {
        /* Старое окно отправки TEST переносим сюда (вне pending_init), если TEST разрешён */
        if(!test_sent && !test_in_flight){
            if(!vnd_ep_busy && (now - start_cmd_ms) > 50){
#if !VND_DISABLE_TEST
                vnd_try_send_test_from_task();
#endif
                if(vnd_ep_busy){ if(vnd_tick_flag) vnd_tick_flag = 0; return; }
            }
        }
    }
    /* CRITICAL WATCHDOG: Если TxCplt не пришёл > 500ms, принудительно сбрасываем inflight.
       Причина: на Windows хост может "забыть" забрать данные или ZLP не отправился.
       Без этого устройство зависает с vnd_inflight=1 навсегда. */
    if(vnd_inflight){
        uint32_t dt_ms = (now >= vnd_last_tx_start_ms) ? (now - vnd_last_tx_start_ms) : (0xFFFFFFFFu - vnd_last_tx_start_ms + 1u + now);
        if(dt_ms > 500){
            VND_LOG("WATCHDOG: vnd_inflight stuck for %lums, force reset", (unsigned long)dt_ms);
        cdc_logf("!!! WATCHDOG: TX stuck, force reset (dt=%lu ms)", (unsigned long)dt_ms);
        vnd_inflight = 0;
        vnd_ep_busy = 0;
        vnd_tx_ready = 1;
        sending_channel = 0xFF;
        /* Пытаемся восстановить передачу */
        vnd_tx_kick = 1;
        }
    }
    
    if(test_in_flight && (now - vnd_last_tx_start_ms) > 100){
        /* На некоторых хостах (FS/RPi) DataIn может не вызваться после короткого TEST.
           Чтобы не зависнуть с занятым EP, снимаем busy и продолжаем. */
    test_in_flight = 0;
    test_sent = 1;
    vnd_ep_busy = 0;
    vnd_tx_ready = 1;
    extern void USBD_VND_ForceTxIdle(void); USBD_VND_ForceTxIdle();
        vnd_tx_kick = 1;
        VND_LOG("TEST_TIMEOUT -> unlock EP");
    }
    if(!test_sent && !vnd_pending_init){
        if(!vnd_ep_busy){
#if !VND_DISABLE_TEST
            vnd_try_send_test_from_task();
#endif
        }
        /* Не выходим раньше времени: позволим подготовку/отправку A/B идти параллельно. */
    }

    if(diag_mode_active){
        /* Зафиксируем размер один раз */
        if(cur_samples_per_frame == 0){
            uint16_t s = diag_samples; if(s > VND_MAX_SAMPLES) s = VND_MAX_SAMPLES;
            cur_samples_per_frame = s;
            cur_expected_frame_size = (uint16_t)(VND_FRAME_HDR_SIZE + cur_samples_per_frame*2u);
        }
        /* Подготовить пару для текущего stream_seq, если ещё не подготовлена и не идёт передача */
        if(sending_channel == 0xFF && !pending_B && diag_prepared_seq != stream_seq){
            vnd_diag_prepare_pair(stream_seq, cur_samples_per_frame);
            diag_prepared_seq = stream_seq;
        }
        /* В DIAG STAT через bulk полностью заблокирован (см. vnd_try_send_pending_status_from_task) */
        /* Отправка диагностических кадров без темпирования: A затем B */
        if(!vnd_ep_busy){ (void)vnd_diag_try_tx(); }
        if(vnd_tick_flag) vnd_tick_flag = 0;
        vnd_cdc_periodic_stats(now);
        return;
    }
    
    if(!full_mode){ if(vnd_tick_flag) vnd_tick_flag = 0; return; }

    /* Новый упрощённый путь: асинхронная передача A/B без ожидания пары.
       В тестовом режиме (USE_TEST_SAWTOOTH) async отключаем, чтобы не выйти
       из таска без фактической отправки кадров. */
#if !USE_TEST_SAWTOOTH
    if(async_mode){
        /* Всегда стараться иметь подготовленные пары */
        ChanFrame *fa0 = &g_frames[pair_fill_idx][0];
        if(fa0->st != FB_READY){ vnd_prepare_pair(); }
        if(!vnd_ep_busy){ (void)vnd_async_try_tx(); }
        if(vnd_tick_flag) vnd_tick_flag = 0;
        vnd_cdc_periodic_stats(now);
        return;
    }
#endif

    /* Упреждающая подготовка пары: когда TEST уже завершён и B не ожидается. */
    if(test_sent && !pending_B){
        ChanFrame *fa_chk = &g_frames[pair_send_idx][0];
        if(fa_chk->st != FB_READY){ vnd_prepare_pair(); }
    }

    /* Окно для GET_STATUS: отправлять STAT строго между парами, чтобы не разрывать A/B. */
    if(!vnd_ep_busy && !vnd_inflight && pending_status){
        if (test_sent && !pending_B && first_pair_done && sending_channel == 0xFF) {
            vnd_try_send_pending_status_from_task();
            if(vnd_ep_busy){ if(vnd_tick_flag) vnd_tick_flag = 0; return; }
        }
    }

    if(vnd_tx_kick) vnd_tx_kick = 0;
    
    /* Разрешаем подготовку следующей пары даже если EP занят (параллельная заполнение буферов) */
    /* if(vnd_ep_busy){ if(vnd_tick_flag) vnd_tick_flag = 0; return; } */

    /* Если TEST уже логически завершён, но его мета застряла в FIFO (нет TxCplt) —
       через ~60 мс превращаем её в служебную, чтобы не блокировать отправку A. */
    vnd_force_complete_test_meta_if_stale();

    static __attribute__((unused)) uint8_t first_pair_logged = 0; /* диагностический лог первой пары */

    static uint8_t first_bq_logged = 0; /* однократный лог первой постановки B */
    if(pending_B){
        /* В A-only режиме не ждём B, сразу продолжим к следующему A */
        if(vnd_ch_mode == 0){
            ChanFrame *f0 = &g_frames[pair_send_idx][0];
            ChanFrame *f1 = &g_frames[pair_send_idx][1];
            f0->st = f1->st = FB_FILL;
            pair_send_idx = (uint8_t)((pair_send_idx + 1u) % VND_PAIR_BUFFERS);
            stream_seq++; dbg_produced_seq++;
            pending_B = 0; pending_B_since_ms = 0; sending_channel = 0xFF;
            /* Попробуем запланировать немедленную отправку следующего A */
            ChanFrame *fA2 = &g_frames[pair_send_idx][0];
            if(fA2->st != FB_READY){ vnd_prepare_pair(); }
            if(fA2->st == FB_READY && !vnd_ep_busy){
                if (vnd_transmit_frame(fA2->buf, fA2->frame_size, 0, 0, "ADC0-IMM-AONLY") == USBD_OK){
                    fA2->st = FB_SENDING; sending_channel = 0; return; }
            }
            /* если не получилось — просто продолжим общий цикл */
        }
        /* Гарантируем, что текущая пара действительно подготовлена: если A ещё не готов (FB_FILL) — соберём пару сейчас. */
        ChanFrame *fA_pre = &g_frames[0][0];
        if(fA_pre->st == FB_FILL && !vnd_ep_busy){ vnd_prepare_pair(); }
        ChanFrame *fB = &g_frames[0][1];
        if(fB->st == FB_READY){
            USBD_StatusTypeDef rcB = vnd_transmit_frame(fB->buf, fB->frame_size, 0, 0, "ADC1");
            if (rcB == USBD_OK) {
                if(!first_bq_logged){ first_bq_logged = 1; VND_LOG("FIRST_B queued size=%u", (unsigned)fB->frame_size); }
                fB->st = FB_SENDING; sending_channel = 1; return;
            } else if (rcB == USBD_BUSY) {
                static uint32_t b_busy_retry = 0; b_busy_retry++;
                if (b_busy_retry == 1 || (b_busy_retry % 10) == 0) {
                    VND_LOG("B_BUSY_RETRY cnt=%lu", (unsigned long)b_busy_retry);
                }
                /* оставляем кадр в READY и попробуем позже */
            } else {
                VND_LOG("B_TX_FAIL rc=%d", (int)rcB);
            }
        } else {
            /* Диагностируем, почему ждём B: выводим периодически и в CDC (1 Гц) текущее состояние */
            static uint32_t last_log_ms = 0;
            static uint32_t last_cdc_ms = 0;
            uint32_t now_ms = HAL_GetTick();
            if(now_ms - last_log_ms > 200){
                VND_LOG("WAIT_B st=%u seq=%lu cur_seq=%lu", (unsigned)fB->st, (unsigned long)fB->seq, (unsigned long)stream_seq);
                last_log_ms = now_ms;
            }
            if(now_ms - last_cdc_ms > 1000){
                extern uint8_t USBD_VND_TxIsBusy(void);
                uint8_t ll_busy = USBD_VND_TxIsBusy();
                const char *stA = (g_frames[0][0].st==FB_READY?"READY":(g_frames[0][0].st==FB_SENDING?"SENDING":"FILL"));
                const char *stB = (g_frames[0][1].st==FB_READY?"READY":(g_frames[0][1].st==FB_SENDING?"SENDING":"FILL"));
                uint32_t age_ms = pending_B_since_ms? (now_ms - pending_B_since_ms) : 0;
                cdc_logf("DBG WAIT_B age=%lums A=%s B=%s ep_busy=%u ll_busy=%u metaDepth=%u lastTX=%u", (unsigned long)age_ms, stA, stB, (unsigned)vnd_ep_busy, (unsigned)ll_busy, (unsigned)vnd_tx_meta_depth(), (unsigned)vnd_last_tx_len);
                last_cdc_ms = now_ms;
            }
            /* Watchdog B: если B уже в полёте и нет TxCplt слишком долго — форсируем завершение пары */
            if(fB->st == FB_SENDING && (now_ms - vnd_last_tx_start_ms) > 150){
                /* Не закрываем пару! Снимаем busy, нейтрализуем старую мета и переотправляем B */
                extern void USBD_VND_ForceTxIdle(void); USBD_VND_ForceTxIdle();
                vnd_ep_busy = 0; vnd_tx_ready = 1; vnd_inflight = 0;
                vnd_meta_neutralize(0x02, g_frames[0][1].seq);
                g_frames[0][1].st = FB_READY; sending_channel = 0xFF;
                VND_LOG("B_TXCPLT_WD (>150ms) -> retry B seq=%lu", (unsigned long)g_frames[0][1].seq);
                /* Попробуем сразу переотправить */
                ChanFrame *fB2 = &g_frames[0][1];
                if(!vnd_ep_busy && fB2->st == FB_READY){
                    if(vnd_transmit_frame(fB2->buf, fB2->frame_size, 0, 0, "ADC1-RETRY") == USBD_OK){ fB2->st = FB_SENDING; sending_channel = 1; return; }
                }
            }
            /* Не синтезируем B: ждём реальные данные, пока EP свободен */
        }
        /* Дополнительный watchdog зависшего pending_B, даже если fB->st перешёл из READY в FILL из-за сброса */
        do {
            uint32_t now_ms2 = HAL_GetTick();
            if(!vnd_ep_busy && sending_channel == 0xFF && (now_ms2 - vnd_last_txcplt_ms) > 40){
                ChanFrame *fBchk = &g_frames[0][1];
                ChanFrame *fAchk = &g_frames[0][0];
                if(fAchk->st != FB_SENDING && fBchk->st != FB_SENDING){
                    if(fBchk->st == FB_READY){
                        if (vnd_transmit_frame(fBchk->buf, fBchk->frame_size, 0, 0, "ADC1-WDG") == USBD_OK){
                            fBchk->st = FB_SENDING; sending_channel = 1; VND_LOG("PEND_B_WDG_RETRY len=%u", (unsigned)fBchk->frame_size); return; }
                    }
                    /* Строгий порядок A→B: НЕ сбрасываем pending_B.
                       Ждём или синтезируем B выше (см. B_SYNTH_READY), чтобы закрыть пару. */
                    if(fBchk->st != FB_READY){
                        VND_LOG("PEND_B_WDG_WAIT (a_st=%u b_st=%u seq=%lu)", (unsigned)fAchk->st, (unsigned)fBchk->st, (unsigned long)stream_seq);
                    }
                }
            }
        } while(0);
    } else {
    ChanFrame *fA = &g_frames[0][0];
        /* Watchdog: если A завис в SENDING и долго нет TxCplt — считаем A завершённым и переходим к B */
        do {
            uint32_t now_ms = HAL_GetTick();
            if (fA->st == FB_SENDING && (now_ms - vnd_last_tx_start_ms) > 120) {
                /* Не считаем A завершённым — лишь снимаем busy, нейтрализуем старую мета и открываем ожидание B */
                VND_LOG("A_TXCPLT_WD (>120ms) -> open pending_B, neutralize A meta, continue");
                extern void USBD_VND_ForceTxIdle(void); USBD_VND_ForceTxIdle();
                vnd_ep_busy = 0; vnd_tx_ready = 1; vnd_inflight = 0; sending_channel = 0xFF;
                vnd_meta_neutralize(0x01, g_frames[0][0].seq);
                pending_B = 1; pending_B_since_ms = now_ms;
            }
        } while(0);
        if(fA->st != FB_READY){ 
            vnd_prepare_pair(); 
            /* В single-slot режиме всегда работаем с g_frames[0] */
            fA = &g_frames[0][0]; 
        }
    if(fA->st == FB_READY){
            /* Искусственных задержек между кадрами нет: отправляем A сразу при готовности EP и данных */
            /* Отправляем A: в режиме без TEST не проверяем test_in_flight вовсе */
#if VND_DISABLE_TEST
            VND_LOG("TRY_A len=%u hdr_seq=%lu", (unsigned)fA->frame_size, (unsigned long)((vnd_frame_hdr_t*)fA->buf)->seq);
            if (vnd_transmit_frame(fA->buf, fA->frame_size, 0, 0, "ADC0") == USBD_OK) {
                static uint8_t first_a_logged = 0;
                if(!first_a_logged){ first_a_logged = 1; VND_LOG("FIRST_A queued size=%u", (unsigned)fA->frame_size); }
                fA->st = FB_SENDING; sending_channel = 0;
                /* В режиме A-only не ожидаем B, иначе помечаем ожидание B */
                if(vnd_ch_mode == 0){
                    pending_B = 0;
                } else {
                    pending_B = 1; pending_B_since_ms = HAL_GetTick();
                }
                return;
            } else {
                /* ДИАГНОСТИКА: почему не удалось отправить A */
                static uint32_t last_tx_fail_log = 0;
                if((now - last_tx_fail_log) > 500){
                    last_tx_fail_log = now;
                    extern uint8_t USBD_VND_TxIsBusy(void);
                    cdc_logf("FAIL_TX_A ep_busy=%u ll_busy=%u inflight=%u ready=%u",
                        (unsigned)vnd_ep_busy, (unsigned)USBD_VND_TxIsBusy(), (unsigned)vnd_inflight, (unsigned)vnd_tx_ready);
                }
            }
#else
            /* Отправляем A только если нет теста в полёте и нет необработанного TEST в FIFO */
            if(!test_in_flight){
                if (vnd_transmit_frame(fA->buf, fA->frame_size, 0, 0, "ADC0") == USBD_OK) {
                    static uint8_t first_a_logged = 0;
                    if(!first_a_logged){ first_a_logged = 1; VND_LOG("FIRST_A queued size=%u", (unsigned)fA->frame_size); }
                    fA->st = FB_SENDING; sending_channel = 0;
                    /* Ранний запрет STAT между A и B: сразу помечаем ожидание B */
                    pending_B = 1; pending_B_since_ms = HAL_GetTick();
                    return;
                }
            }
#endif
        }
    }
    if(vnd_tick_flag) vnd_tick_flag = 0;
    if(cur_samples_per_frame == 0 && start_cmd_ms && (now - start_cmd_ms) > VND_DMA_TIMEOUT_MS && !no_dma_status_sent){
        adc_stream_debug_t dbg; adc_stream_get_debug(&dbg);
        if(dbg.dma_full0 == dma_snapshot_full0 && dbg.dma_full1 == dma_snapshot_full1){ no_dma_status_sent = 1; if(vnd_last_error == 0) vnd_last_error = 1; VND_LOG("ERR DMA_TIMEOUT"); }
    }
    /* Периодическая CDC-статистика по байтам/скорости */
    vnd_cdc_periodic_stats(now);
    
    /* ДИАГНОСТИКА: детальное состояние передачи каждые 2 секунды */
    {
        static uint32_t last_diag_detail_ms = 0;
        if(streaming && (now - last_diag_detail_ms) > 2000){
            last_diag_detail_ms = now;
            extern uint8_t USBD_VND_TxIsBusy(void);
            uint8_t ll_busy = USBD_VND_TxIsBusy();
            ChanFrame *fA = &g_frames[0][0];
            ChanFrame *fB = &g_frames[0][1];
            const char *stA = (fA->st==FB_READY?"RDY":(fA->st==FB_SENDING?"SND":"FIL"));
            const char *stB = (fB->st==FB_READY?"RDY":(fB->st==FB_SENDING?"SND":"FIL"));
            cdc_logf("DBG ep_busy=%u ll_busy=%u ch=%u pendB=%u A_st=%s B_st=%s metaD=%u",
                (unsigned)vnd_ep_busy, (unsigned)ll_busy, (unsigned)sending_channel, (unsigned)pending_B,
                stA, stB, (unsigned)vnd_tx_meta_depth());
        }
    }
    
    /* Периодическое обновление дисплея LCD с информацией о потоке */
    // stream_display_periodic_update();
    /* Раньше здесь запрашивали мягкий ресет класса при отсутствии TXCPLT >1.5s.
       Это приводило к незаметной для хоста остановке стрима (streaming=0) и последующим тайм‑ауторам.
       Вместо софт‑ресета делаем бережный kick: снимаем busy и пробуем продолжить передачу.
       Более глубокий kick есть ниже (WDG_KICK >3s). */
    if((now - vnd_last_txcplt_ms) > 1500){
        extern void USBD_VND_ForceTxIdle(void);
        USBD_VND_ForceTxIdle();
        vnd_ep_busy = 0; vnd_tx_ready = 1; vnd_inflight = 0; sending_channel = 0xFF;
        /* не трогаем streaming/test/pending_B — даём пайплайну восстановиться */
        vnd_last_txcplt_ms = now; /* предотвратить лавину */
        VND_LOG("WDG_SOFT_RESET_BYPASS -> force idle");
    }
    /* Аварийный keepalive тестом — только в диагностике; в полном режиме не посылаем TEST повторно */
    if(!full_mode){
        /* В DIAG режиме можно слать keepalive TEST — оставляем как было. */
    if(dbg_tx_cplt == 0 && (now - start_cmd_ms) > 150 && !vnd_ep_busy){
#if !VND_DISABLE_TEST
        vnd_emergency_keepalive(now);
#endif
    }
    }
    /* Периодический диагностический лог ранней стадии: пока нет ни одного TXCPLT или отсутствует прогресс */
    do {
        static uint32_t last_diag_ms = 0;
        static uint32_t last_diag_txcplt = 0;
        if(now - last_diag_ms > 200){
            if(dbg_tx_cplt == 0 || dbg_tx_cplt != last_diag_txcplt){
                /* Получим отладочные счётчики DMA, если доступны */
                VND_LOG("DIAG txcplt=%lu test_sent=%u test_in_flight=%u pendB=%u ep_busy=%u inflight=%u ch=%u ackPend=%u seq=%lu prod=%lu sent0=%lu sent1=%lu wr=%lu rd=%lu metaDepth=%u", (unsigned long)dbg_tx_cplt, (unsigned)test_sent, (unsigned)test_in_flight, (unsigned)pending_B, (unsigned)vnd_ep_busy, (unsigned)vnd_inflight, (unsigned)sending_channel, (unsigned)status_ack_pending, (unsigned long)stream_seq, (unsigned long)dbg_produced_seq, (unsigned long)dbg_sent_ch0_total, (unsigned long)dbg_sent_ch1_total, (unsigned long)frame_wr_seq, (unsigned long)frame_rd_seq, (unsigned)vnd_tx_meta_depth());
                last_diag_txcplt = dbg_tx_cplt;
            }
            last_diag_ms = now;
        }
    } while(0);
    
    /* === FPS отчёт каждые 2 секунды === */
    if(streaming && (now - fps_last_report_ms) > 2000){
        vnd_report_fps_stats();
        fps_last_report_ms = now;
    }

    /* Ускоренный watchdog: считаем зависанием при > 3000мс без TXCPLT и мягко пинаем TX */
    if(streaming && (now - vnd_last_txcplt_ms) > 3000){
        VND_LOG("WDG_KICK (no TXCPLT >3s) try requeue");
        /* Не делаем глубокий ресет, только разрешаем передачу и инициируем попытку */
        vnd_ep_busy = 0; vnd_tx_ready = 1; vnd_inflight = 0; sending_channel = 0xFF;
        test_sent = 1; test_in_flight = 0; start_ack_done = 1; status_ack_pending = 0;
        vnd_last_txcplt_ms = now;
        vnd_tx_kick = 1;
        (void)vnd_async_try_tx();
    }

    /* Fallback стартовой инициализации: если после START прошло >20 мс и ни одного рабочего кадра не ушло,
       пытаемся принудительно подготовить и отправить A кадр первой пары. */
    if(streaming && vnd_pending_init && !vnd_stream_active){
        if(start_cmd_ms && (now - start_cmd_ms) > 20){
            ChanFrame *fA = &g_frames[0][0];
            if(fA->st != FB_READY){ vnd_prepare_pair(); fA = &g_frames[0][0]; }
            if(fA->st == FB_READY && !vnd_ep_busy){
                if(vnd_transmit_frame(fA->buf, fA->frame_size, 0, 0, "ADC0-KICK") == USBD_OK){
                    fA->st = FB_SENDING; sending_channel = 0;
                    pending_B = (vnd_ch_mode == 0) ? 0 : 1; if(pending_B) pending_B_since_ms = HAL_GetTick();
                    vnd_pending_init = 0; /* считаем инициализацию выполненной */
                    vnd_stream_active = 1; /* поток активен */
                    VND_LOG("START_FALLBACK_KICK A queued size=%u", (unsigned)fA->frame_size);
                }
            }
        }
    }

    /* Если нет прогресса — не синтезируем кадры; ждём реальные данные от АЦП */
}

/* Обработчик завершения передачи */
void USBD_VND_TxCplt(void)
{
    uint8_t prev_sending = sending_channel;
    dbg_tx_cplt++;
    vnd_tx_ready = 1;
    vnd_ep_busy = 0;
    vnd_inflight = 0;
    
    /* ПРОФИЛИРОВАНИЕ: интервал между TxCplt */
    static uint32_t last_txcplt_time = 0;
    static uint32_t txcplt_interval_sum = 0;
    static uint32_t txcplt_interval_count = 0;
    static uint32_t txcplt_interval_max = 0;
    static uint32_t last_txcplt_log = 0;
    
    uint32_t now_txcplt = HAL_GetTick();
    if(last_txcplt_time > 0){
        uint32_t interval = now_txcplt - last_txcplt_time;
        txcplt_interval_sum += interval;
        txcplt_interval_count++;
        if(interval > txcplt_interval_max) txcplt_interval_max = interval;
        
        if(now_txcplt - last_txcplt_log >= 5000){
            uint32_t avg = txcplt_interval_count ? (txcplt_interval_sum / txcplt_interval_count) : 0;
            printf("[PROF_TxCplt] cnt=%lu avg=%lums max=%lums (rate=%.1fHz)\r\n",
                   txcplt_interval_count, avg, txcplt_interval_max,
                   avg > 0 ? (1000.0f / avg) : 0.0f);
            txcplt_interval_sum = 0;
            txcplt_interval_count = 0;
            txcplt_interval_max = 0;
            last_txcplt_log = now_txcplt;
        }
    }
    last_txcplt_time = now_txcplt;
    
    vnd_last_txcplt_ms = now_txcplt;
    /* Убран подробный лог TXCPLT — используется агрегированная статистика раз в 10 сек */
    vnd_total_tx_bytes += vnd_last_tx_len; /* учитывать и тестовые, и статусные, и рабочие */
    /* Зафиксировать завершение стартового ACK (если был) */
    if(start_stat_inflight){ start_stat_inflight = 0; start_ack_done = 1; }

    /* Надёжная классификация завершившегося буфера (приоритет inflight_* затем meta FIFO) */
    vnd_tx_meta_t meta; int have_meta = vnd_tx_meta_pop(&meta);
    uint8_t eff_is_frame = 0; uint8_t eff_flags = 0; uint32_t eff_seq = 0;
    if(inflight_is_frame){ eff_is_frame = 1; eff_flags = inflight_flags; eff_seq = inflight_seq; }
    else if(have_meta && meta.is_frame){ eff_is_frame = 1; eff_flags = meta.flags; eff_seq = meta.seq_field; }
    else if(have_meta){ eff_is_frame = 0; }
    else { eff_is_frame = last_tx_is_frame; eff_flags = last_tx_flags; eff_seq = last_tx_seq; }
    inflight_is_frame = 0; inflight_flags = 0; inflight_seq = 0;
    /* Убран подробный лог TXCPLT_CLASS — используется агрегированная статистика */

    /* Если это был ACK на STOP — после него переводим систему в остановленное состояние */
    if(stop_stat_inflight){
        stop_stat_inflight = 0;
        stop_request = 0;
        if(streaming){ streaming = 0; VND_LOG("STOP_STREAM after STAT"); }
        diag_mode_active = 0;
        vnd_reset_buffers();
        sending_channel = 0xFF; pending_B = 0; test_sent = 0; test_in_flight = 0; vnd_inflight = 0;
        /* Останавливаем DMA и сбрасываем буферы */
        extern void adc_stream_stop(void);
        adc_stream_stop();
        /* Индикация STOP: погасить пин Data_ready и вывести CDC-событие */
        HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_RESET);
        {
            uint64_t cur = vnd_total_tx_bytes;
            uint64_t delta = (cur >= vnd_tx_bytes_at_start) ? (cur - vnd_tx_bytes_at_start) : 0ULL;
            cdc_logf("EVT STOP total=%llu delta=%llu", (unsigned long long)cur, (unsigned long long)delta);
        }
        vnd_tx_kick = 1; /* пнуть таск на всякий случай */
        return;
    }
    if(test_in_flight)
    {
        test_in_flight = 0;
        test_sent = 1; /* помечаем тест выполненным ровно один раз, не сбрасывать вне START */
        first_test_sent_ms = HAL_GetTick();
        VND_LOG("TEST_TXCPLT");
        return;
    }
    if(!streaming){ vnd_tx_kick = 1; return; }

    /* Первая успешная рабочая передача (A или B): зафиксировать метку, если ещё не установлена */
    if(vnd_stage_first_frame_ms == 0 && eff_is_frame && (eff_flags == 0x01 || eff_flags == 0x02)){
        vnd_stage_first_frame_ms = HAL_GetTick();
    }
    /* НЕМЕДЛЕННАЯ ПОПЫТКА ОТПРАВКИ: если есть данные и EP свободен — отправить сразу.
       Это обеспечивает максимальную скорость USB без ожидания periodic task. */
    if(streaming && !vnd_ep_busy){
        (void)vnd_async_try_tx();
    }
    
    /* Асинхронный режим: считаем канал по eff_flags, закрываем соответствующий подкадр.
       В A-only/B-only режиме закрываем сразу всю пару и сдвигаем seq. */
    if(async_mode && streaming && full_mode){
        if(eff_is_frame && (eff_flags == 0x01 || eff_flags == 0x02)){
            int ch = (eff_flags == 0x01) ? 0 : 1;
            if(vnd_pending_init){ vnd_pending_init = 0; }
            vnd_stream_active = 1;
            /* Учёт статистики по каналам (оценка сэмплов по текущему размеру кадра) */
            /* Статистика: если размер ещё не зафиксирован, используем активный профиль с учётом ограничений */
            uint16_t ns = cur_samples_per_frame;
            if(ns == 0){
                uint16_t eff = adc_stream_get_active_samples();
                if(eff == 0) eff = 1;
                if(vnd_frame_samples_req && vnd_frame_samples_req < eff) eff = vnd_frame_samples_req;
                if(vnd_trunc_samples && vnd_trunc_samples < eff) eff = vnd_trunc_samples;
                if(eff > VND_MAX_SAMPLES) eff = VND_MAX_SAMPLES;
                ns = eff;
            }
            if(ch==0){ dbg_tx_sent++; dbg_sent_ch0_total++; dbg_sent_seq_adc0++; vnd_total_tx_samples += (uint64_t)ns; fps_frame_a_count++; }
            else      { dbg_tx_sent++; dbg_sent_ch1_total++; dbg_sent_seq_adc1++; vnd_total_tx_samples += (uint64_t)ns; fps_frame_b_count++; }
            /* Отпускаем канал */
            sending_channel = 0xFF;
            if(vnd_stage_first_frame_ms == 0){ vnd_stage_first_frame_ms = HAL_GetTick(); cdc_logf("EVT FIRST_FRAME ch=%c", ch==0?'A':'B'); }
        }
        return;
    }

    /* Диагностический режим: используем eff_flags для точной классификации (устраняет гонку по sending_channel) */
    if(diag_mode_active){
        if(!eff_is_frame){
            /* STAT/ZLP — просто продолжим */
            vnd_tx_kick = 1; return;
        }
        if(eff_flags == 0x01){
            /* Завершился A: считаем и просим отправить B */
            dbg_tx_sent++; dbg_sent_ch0_total++; dbg_sent_seq_adc0++;
            /* В DIAG считаем семплы по текущему размеру кадра */
            vnd_total_tx_samples += (uint64_t)((cur_samples_per_frame != 0) ? cur_samples_per_frame : diag_samples);
            sending_channel = 0; /* завершили A */
            pending_B = 1; pending_B_since_ms = HAL_GetTick();
            /* Зафиксируем seq этой пары для B */
            diag_current_pair_seq = eff_seq;
            /* Немедленно пытаемся отправить B, чтобы убрать паузу между A и B */
            if(!vnd_try_send_B_immediate()){
                /* печать в CDC отключена для максимальной скорости */
                vnd_tx_kick = 1; return;
            } else {
                /* B пошёл в полёт — дальше обычная обработка после его завершения */
                return;
            }
        } else if(eff_flags == 0x02){
            /* Завершился B: закрываем пару, двигаем seq */
            dbg_tx_sent++; dbg_sent_ch1_total++; dbg_sent_seq_adc1++;
            vnd_total_tx_samples += (uint64_t)((cur_samples_per_frame != 0) ? cur_samples_per_frame : diag_samples);
            stream_seq++; dbg_produced_seq++;
            pending_B = 0; pending_B_since_ms = 0; sending_channel = 0xFF;
            diag_prepared_seq = 0xFFFFFFFFu; /* заставим подготовить новую пару */
            if(!first_pair_done){ first_pair_done = 1; }
            /* Сразу пытаемся отправить следующий A новой пары */
            if(!vnd_try_send_A_nextpair_immediate()){
                /* печать в CDC отключена для максимальной скорости */
                vnd_tx_kick = 1; return;
            } else {
                return;
            }
        } else if(eff_flags == 0x80){
            /* TEST */
            sending_channel = 0xFF;
            vnd_tx_kick = 1; return;
        } else {
            /* неизвестный флаг — игнорируем */
            vnd_tx_kick = 1; return;
        }
    }

    /* Ниже — обычная ветка для полнофункционального режима */
    if(!eff_is_frame){
        /* STAT или иной служебный пакет — используем предыдущее состояние канала как подсказку */
        if(prev_sending == 0){
            if(!pending_B){ pending_B = 1; pending_B_since_ms = HAL_GetTick(); VND_LOG("GUARD(NON-FRAME): pending_B"); }
            sending_channel = 0xFF; vnd_tx_kick = 1; return;
        } else if(prev_sending == 1){
            /* Считаем, что завершился B: закрываем пару безопасно */
        ChanFrame *f0 = &g_frames[pair_send_idx][0];
        ChanFrame *f1 = &g_frames[pair_send_idx][1];
        f0->st = f1->st = FB_FILL;
        pair_send_idx = (pair_send_idx + 1u) % VND_PAIR_BUFFERS;
        stream_seq++; dbg_produced_seq++;
        pending_B = 0; pending_B_since_ms = 0; sending_channel = 0xFF;
    /* Не планируем задержку следующей пары: передавать сразу при готовности */
            if(!first_pair_done){ first_pair_done = 1; }
            VND_LOG("GUARD(NON-FRAME): assume B done -> advance seq=%lu", (unsigned long)stream_seq);
            vnd_tx_kick = 1; return;
        } else {
            sending_channel = 0xFF; vnd_tx_kick = 1; return;
        }
    }
    uint8_t fl = eff_flags;
    if(fl == 0x80){
        /* TEST */
        sending_channel = 0xFF; /* тест одиночный */
        vnd_tx_kick = 1; return;
    }
    if(fl == 0x01){
        /* Это канал A */
        if(pending_B){ VND_LOG("WARN A_WHILE_PENDING_B seq=%lu hdr.seq=%lu", (unsigned long)stream_seq, (unsigned long)eff_seq); }
        if(eff_seq != stream_seq){
            VND_LOG("WARN A_SEQ_MISMATCH hdr=%lu stream_seq=%lu", (unsigned long)eff_seq, (unsigned long)stream_seq);
        }
        static uint8_t first_a_txcplt_logged = 0; if(!first_a_txcplt_logged){ first_a_txcplt_logged = 1; VND_LOG("FIRST_A txcplt seq=%lu", (unsigned long)eff_seq); }
        dbg_tx_sent++; dbg_sent_ch0_total++; dbg_sent_seq_adc0++;
        /* Добавим число сэмплов канала A из текущей пары */
        vnd_total_tx_samples += (uint64_t)g_frames[pair_send_idx][0].samples;
        if(vnd_ch_mode == 0){
            /* A-only: закрываем пару сразу */
            g_frames[pair_send_idx][0].st = FB_FILL;
            g_frames[pair_send_idx][1].st = FB_FILL;
            sending_channel = 0xFF;
            pending_B = 0; pending_B_since_ms = 0;
            pair_send_idx = (uint8_t)((pair_send_idx + 1u) % VND_PAIR_BUFFERS);
            stream_seq++; dbg_produced_seq++; if(!first_pair_done){ first_pair_done = 1; }
            /* Пытаемся немедленно отправить следующий A */
            if(!vnd_try_send_A_nextpair_immediate()){ vnd_tx_kick = 1; return; } else { return; }
        } else {
            /* пометим A как завершённый для наглядности статуса */
            g_frames[pair_send_idx][0].st = FB_FILL;
            sending_channel = 0xFF;
            /* Запускаем ожидание B ровно здесь */
            pending_B = 1; pending_B_since_ms = HAL_GetTick();
            /* Попытаемся немедленно отправить B, чтобы не ждать захода таска */
            if(!vnd_try_send_B_immediate()){
                vnd_tx_kick = 1; return;
            } else { return; }
        }
    } else if(fl == 0x02){
    /* Канал B завершён — закрываем пару */
        if(!pending_B){ VND_LOG("WARN B_WITHOUT_PENDING seq=%lu hdr.seq=%lu", (unsigned long)stream_seq, (unsigned long)eff_seq); }
        if(eff_seq != stream_seq){
            VND_LOG("WARN B_SEQ_MISMATCH hdr=%lu stream_seq=%lu", (unsigned long)eff_seq, (unsigned long)stream_seq);
        }
        static uint8_t first_b_logged = 0; if(!first_b_logged){ first_b_logged = 1; VND_LOG("FIRST_B txcplt seq=%lu", (unsigned long)eff_seq); }
    dbg_tx_sent++; dbg_sent_ch1_total++; dbg_sent_seq_adc1++;
    vnd_total_tx_samples += (uint64_t)g_frames[pair_send_idx][1].samples;
        ChanFrame *f0 = &g_frames[pair_send_idx][0];
        ChanFrame *f1 = &g_frames[pair_send_idx][1];
        f0->st = f1->st = FB_FILL;
        pair_send_idx = (pair_send_idx + 1u) % VND_PAIR_BUFFERS;
        stream_seq++; dbg_produced_seq++;
        pending_B = 0; pending_B_since_ms = 0; sending_channel = 0xFF;
        if(!first_pair_done){ first_pair_done = 1; }
        /* Сразу пытаемся отправить следующий A новой пары (если готов) */
        if(!vnd_try_send_A_nextpair_immediate()){
            /* Без планирования задержек: следующая пара начнётся как только готова */
            vnd_tx_kick = 1; return;
        } else { return; }
    } else {
        VND_LOG("WARN UNKNOWN FLAGS 0x%02X in TxCplt", (unsigned)fl);
        sending_channel = 0xFF;
        /* РЕЗЕРВ: если классификация не распознала, но прямо перед этим слали B — закроем пару */
        if(pending_B && (prev_sending == 1 || last_tx_flags == 0x02)){
            ChanFrame *f0 = &g_frames[pair_send_idx][0];
            ChanFrame *f1 = &g_frames[pair_send_idx][1];
            f0->st = f1->st = FB_FILL;
            pair_send_idx = (pair_send_idx + 1u) % VND_PAIR_BUFFERS;
            stream_seq++; dbg_produced_seq++;
            pending_B = 0; pending_B_since_ms = 0; sending_channel = 0xFF;
            /* Без планирования задержек */
            VND_LOG("FALLBACK_CLOSE_PAIR after UNKNOWN meta (assume B)");
            vnd_tx_kick = 1; return;
        }
        vnd_tx_kick = 1; return;
    }
}

/* Приём команд */
void USBD_VND_DataReceived(const uint8_t *data, uint32_t len)
{
    if(!len) return;
    uint8_t cmd = data[0];
    static uint32_t rcv_count = 0;
    rcv_count++;
    printf("[VND_RCV] #%lu CMD 0x%02X len=%lu\r\n", rcv_count, cmd, (unsigned long)len);
    VND_LOG("CMD 0x%02X len=%lu", cmd, (unsigned long)len);
    switch(cmd)
    {
        case VND_CMD_START_STREAM:
        {
            /* ДИАГНОСТИКА: фиксируем прием команды START */
            last_cmd_received = 0x20;
            last_cmd_timestamp_ms = HAL_GetTick();
            cmd_start_count++;
            vnd_update_cmd_indicator();
            
            /* Разрешаем START в любое время: мягко перезапускаем поток */
                VND_LOG("START_STREAM received");
                vnd_reset_buffers();
                pair_send_idx = 0; pair_fill_idx = 0; sending_channel = 0xFF; pending_B = 0; pending_B_since_ms = 0;
                /* Сброс фиксации размера и планировщика */
                cur_samples_per_frame = 0; cur_expected_frame_size = 0;
                vnd_next_pair_ms = 0;
                /* Состояние теста/ACK */
                test_in_flight = 0; test_pending = 0;
#if VND_DISABLE_TEST
                test_sent = 1; /* тест отключён: считать выполненным */
#else
                test_sent = 0;
#endif
                start_stat_planned = 0; start_stat_inflight = 0; start_ack_done = 1; /* ACK считаем выполненным логически */
                pending_status = 0; status_ack_pending = 0; /* не пытаться слать STAT через IN */
                vnd_error_counter = 0;
                /* Синхронизация последовательностей пар */
                stream_seq = 0; next_seq_to_assign = 0; dbg_produced_seq = 0;
                first_pair_done = 0;
                dbg_sent_ch0_total = 0; dbg_sent_ch1_total = 0;
                start_cmd_ms = HAL_GetTick();
                /* Помечаем состояние ожидания первой передачи */
                vnd_pending_init = 1;
                vnd_stream_active = 0;
                /* Снимем DMA снапшот для контроля таймаута */
                adc_stream_debug_t dbg; adc_stream_get_debug(&dbg);
                dma_snapshot_full0 = dbg.dma_full0; dma_snapshot_full1 = dbg.dma_full1;
                /* Зафиксировать размер кадра для полного режима из vnd_frame_samples_req (если не задан хостом, используем дефолт) */
                if (full_mode) {
                    uint16_t before = vnd_frame_samples_req;
                    /* Если хост не задал vnd_frame_samples_req через SET_FRAME_SAMPLES, используем дефолт */
                    if(vnd_frame_samples_req == 0) {
                        /* Если хост не задал размер кадра, используем активный размер профиля ADC */
                        uint16_t prof_ns = adc_stream_get_active_samples();
                        if(prof_ns == 0) prof_ns = 1; /* защита от нуля */
                        vnd_frame_samples_req = prof_ns;
                    }
                    VND_LOG("START_CFG req=%u trunc=%u", vnd_frame_samples_req, vnd_trunc_samples);
                    cdc_logf("START_CFG full_mode req_before=%u req_after=%u trunc=%u", before, vnd_frame_samples_req, vnd_trunc_samples);
                    vnd_recompute_pair_timing(vnd_frame_samples_req);
                    cur_samples_per_frame = 0; /* снять lock, чтобы применилось немедленно */
                    cur_expected_frame_size = 0;
                }
                /* ПРОАКТИВНО: очистим возможный "хвост" занятости IN EP с прошлой сессии */
                do {
                    extern void USBD_VND_ForceTxIdle(void);
                    USBD_VND_ForceTxIdle();
                    vnd_ep_busy = 0; vnd_tx_ready = 1; vnd_inflight = 0;
                    vnd_last_tx_start_ms = 0; /* чтобы WDG не сработал по старой метке */
                    /* Полностью очистим meta-FIFO для корректной классификации первой пары */
                    vnd_tx_meta_head = vnd_tx_meta_tail = 0;
                    meta_push_total = meta_pop_total = meta_empty_events = meta_overflow_events = 0;
                } while(0);
                streaming = 1;
                     /* ГАРАНТИЯ повторного старта:
                         после STOP/alt-reset хост ожидает «чистый» старт. Перезапускаем ADC/DMA и сбрасываем
                         внутренние rd/wr seq в adc_stream (иначе возможна деградация FPS на последующих запусках).
                         ВАЖНО: делаем это отложенно в main-loop (Vendor_Stream_Task), а не из USB RX. */
                     vnd_adc_restart_request = 1;
                /* Включаем async декуплинг по умолчанию в полном режиме.
                   В тестовом режиме принудительно держим async=0, чтобы использовать
                   парный пайплайн с синтетической пилой. */
#if USE_TEST_SAWTOOTH
                async_mode = 0;
                vnd_strict_pairing = 0;
                cdc_logf("START: TEST mode forces async=0 (pair pipeline)");
#else
                async_mode = 1;
                /* Ранее мы временно отключали async ради диагностики. Возвращаем по умолчанию async=1
                    для максимальной скорости. Для отладки можно принудительно выключить через команду
                    SET_ASYNC_MODE(0) с хоста. */
#endif
                dbg_last_forced_stat_ms = start_cmd_ms;
                vnd_tx_ready = 1; vnd_ep_busy = 0; vnd_inflight = 0;
                vnd_last_txcplt_ms = HAL_GetTick();
                /* Разрешим STAT только после первой завершённой пары */
                first_pair_done = 0; pending_status = 0; vnd_status_permit_once = 0;
                /* Инициализация FPS измерения */
                fps_measurement_start_ms = HAL_GetTick();
                fps_last_report_ms = fps_measurement_start_ms;
                fps_pair_count = 0;
                fps_frame_a_count = 0;
                fps_frame_b_count = 0;
                fps_prepare_count = 0;
                /* Инициализация профилирования */
                memset(&perf_stats, 0, sizeof(perf_stats));
                /* Индикация START */
                vnd_tx_bytes_at_start = vnd_total_tx_bytes;
                HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_SET);
                     /* ADC/DMA: для надёжного STOP→START делаем restart на каждый START (отложенно). */
                     VND_LOG("START_STREAM: transmission enabled (ADC restart pending)");
                /* ДИАГНОСТИКА: выводим активный профиль и параметры */
                {
                    uint8_t prof = adc_stream_get_profile();
                    uint16_t samp = adc_stream_get_active_samples();
                    uint16_t rate = adc_stream_get_buf_rate();
                    cdc_logf("EVT START t=%lu profile=%u samples=%u rate=%u Hz bytes=%llu", 
                             (unsigned long)start_cmd_ms, prof, samp, rate, (unsigned long long)vnd_tx_bytes_at_start);
                    
                    /* Обновляем дисплей с параметрами потока */
                    // stream_info_t stream_info = {
                    //     .frequency_hz = rate,
                    //     .start_sample = 0,
                    //     .sample_count = samp,
                    //     .frames_sent = 0,
                    //     .is_streaming = 1
                    // };
                    // stream_display_update(&stream_info);
                    /* Параметры хоста (профиль/окна/частоты) */
                    vnd_update_lcd_params();
                    cdc_logf("INIT_DBG ASYNC_EN=1 pending_init=%u seq=%lu", (unsigned)vnd_pending_init, (unsigned long)stream_seq);
                }
                if(!full_mode){ diag_mode_active = 1; }
                VND_LOG("START_STREAM");
                /* Не отправляем ACK-STAT через IN на старте — позволим сразу начать A/B. */
                /* Не формируем синтетическую первую пару: ждём реальные данные */
#if !VND_DISABLE_TEST
                if(!test_sent && !test_in_flight && !vnd_ep_busy){ vnd_try_send_test_from_task(); }
#endif
                /* Важно: не начинаем передачу до выполнения отложенного adc_stream_restart(). */
                cdc_logf("START: defer initial TX (adc_restart_request=%u)", (unsigned)vnd_adc_restart_request);
                vnd_tx_kick = 1;
                /* Диагностический режим: подготовка и первая отправка */
                if(diag_mode_active){
                    if(cur_samples_per_frame == 0){
                        /* если хост задал samples_per_frame — используем его для DIAG */
                        uint16_t ds = (vnd_frame_samples_req != 0) ? vnd_frame_samples_req : diag_samples;
                        if(ds > VND_MAX_SAMPLES) ds = VND_MAX_SAMPLES;
                        diag_samples = ds;
                        cur_samples_per_frame = diag_samples;
                        cur_expected_frame_size = (uint16_t)(VND_FRAME_HDR_SIZE + cur_samples_per_frame*2u);
                    }
                    diag_prepared_seq = 0xFFFFFFFFu; diag_current_pair_seq = 0xFFFFFFFFu;
                    vnd_diag_prepare_pair(stream_seq, cur_samples_per_frame);
                    diag_prepared_seq = stream_seq; diag_current_pair_seq = stream_seq;
                    if(!vnd_ep_busy){ (void)vnd_diag_try_tx(); }
                }
                /* Передача стартует из Vendor_Stream_Task после ADC restart */
        }
        break;
        case VND_CMD_SET_FRAME_SAMPLES:
            if(len >= 3){
                uint16_t ns = (uint16_t)(data[1] | (data[2] << 8));
                if(ns > VND_MAX_SAMPLES) ns = VND_MAX_SAMPLES;
                vnd_frame_samples_req = ns; /* Использовать значение хоста напрямую */
                /* Применим к диагностике сразу, чтобы DIAG шёл с нужным размером */
                diag_samples = (ns != 0) ? ns : diag_samples;
                vnd_recompute_pair_timing(vnd_frame_samples_req);
                /* Снимем фиксацию размера, чтобы применилось при следующем build */
                cur_samples_per_frame = 0; cur_expected_frame_size = 0;
                VND_LOG("SET_FRAME_SAMPLES %u -> period=%ums", (unsigned)vnd_frame_samples_req, (unsigned)vnd_pair_period_ms);
                cdc_logf("EVT SET_FRAME_SAMPLES %u", (unsigned)vnd_frame_samples_req);
                vnd_update_lcd_params();
            }
            break;
        case VND_CMD_STOP_STREAM:
        {
            /* ДИАГНОСТИКА: фиксируем прием команды STOP, сбрасываем счётчик START */
            last_cmd_received = 0x21;
            last_cmd_timestamp_ms = HAL_GetTick();
            cmd_stop_count++;
            cmd_start_count = 0;  /* Сброс счётчика START по команде STOP */
            vnd_update_cmd_indicator();
            
            /* В полном режиме: STOP с ACK-STAT между парами; в DIAG — немедленная остановка без STAT по bulk */
            if(diag_mode_active){
                /* Мгновенно останавливаем стрим без ACK-STAT в bulk, чтобы не нарушать DIAG поток */
                stop_request = 0; pending_status = 0;
                if(streaming){ streaming = 0; VND_LOG("STOP_STREAM (diag, immediate)"); }
                diag_mode_active = 0;
                vnd_reset_buffers();
                sending_channel = 0xFF; pending_B = 0; pending_B_since_ms = 0; test_sent = 0; test_in_flight = 0; vnd_inflight = 0;
                /* КРИТИЧНО: полный сброс состояния для возможности повторного START */
                vnd_pending_init = 0;
                vnd_stream_active = 0;
                vnd_ep_busy = 0;
                vnd_tx_ready = 1;
                /* ADC/DMA продолжают работать в фоне, STOP только выключает передачу по USB */
                HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_RESET);
                {
                    uint64_t cur = vnd_total_tx_bytes;
                    uint64_t delta = (cur >= vnd_tx_bytes_at_start) ? (cur - vnd_tx_bytes_at_start) : 0ULL;
                    cdc_logf("EVT STOP total=%llu delta=%llu", (unsigned long long)cur, (unsigned long long)delta);
                    
                    /* Обновляем дисплей: поток остановлен */
                    // stream_info_t stream_info = {
                    //     .frequency_hz = 0,
                    //     .start_sample = 0,
                    //     .sample_count = 0,
                    //     .frames_sent = dbg_sent_ch0_total + dbg_sent_ch1_total,
                    //     .is_streaming = 0
                    // };
                    // stream_display_update(&stream_info);
                    vnd_update_lcd_params();
                }
                vnd_tx_kick = 1; /* пнуть таск на всякий случай */
            } else {
                /* STOP в полном режиме: мгновенная остановка, полный сброс ВСЕХ флагов */
                streaming = 0;  /* Главное: остановить передачу */
                VND_LOG("STOP_STREAM (full mode)");
                
                /* Сброс запросов на STAT — они больше не нужны */
                stop_request = 0;
                pending_status = 0;
                status_ack_pending = 0;
                stop_stat_inflight = 0;
                
                /* Сброс состояния передачи для разрешения следующего START */
                vnd_pending_init = 0;
                vnd_stream_active = 0;
                vnd_ep_busy = 0;
                vnd_tx_ready = 1;
                vnd_inflight = 0;
                vnd_tx_kick = 0;  /* Сбросить флаг kick */
                
                /* Сброс флагов пар и статусов */
                first_pair_done = 0;
                start_ack_done = 1;
                vnd_status_permit_once = 0;
                
                /* КРИТИЧНО: НЕ сбрасываем async_mode и full_mode - они устанавливаются START */
                /* async_mode должен остаться 1 после первого START */
                
                HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_RESET);
                cdc_logf("EVT STOP t=%lu async=%d full=%d ep_busy=%d", 
                         (unsigned long)HAL_GetTick(), async_mode, full_mode, vnd_ep_busy);
            }
        }
        break;
        
        case VND_CMD_DEVICE_RESET:
        {
            VND_LOG("DEVICE_RESET commanded!");
#if VND_DEVICE_RESET_SOFT_ONLY
            /* Мягкий путь: просим main выполнить soft USB recovery без полного MCU reset */
            cdc_logf("[VND] soft USB recovery requested by 0x22 at t=%lu ms", (unsigned long)HAL_GetTick());
            need_recovery = 1;
#else
            /* Жёсткий путь: ставим флаг для main-loop (выполнит корректный disconnect+NVIC_SystemReset) */
            extern volatile uint8_t need_hard_reset;
            cdc_logf("!!! HARD_RESET scheduled by host 0x22 at t=%lu ms", (unsigned long)HAL_GetTick());
            need_hard_reset = 1;
#endif
        }
        break;
        
        case VND_CMD_GET_STATUS:
        {
            /* GET_STATUS всегда допускается: во время стрима — только между парами */
            /* Диагностическая вставка: классификация паузы при запросе статуса (редкая, чтобы не тормозить) */
            vnd_diag_log_possible_stall();

            /* Автокик: если во время стрима ничего не летит (EP свободен, нет inflight и пустая meta-очередь),
               попробуем мягко сдвинуть пайплайн. Это лечит зависания вида ADC_IDLE/A_READY_NOT_TX. */
            if(streaming && !vnd_ep_busy && !vnd_inflight && (vnd_tx_meta_depth() == 0))
            {
                int kicked = 0;
                if(async_mode){
                    kicked = vnd_async_try_tx();
                } else {
                    if(pending_B){ kicked = vnd_try_send_B_immediate(); }
                    if(!kicked){ kicked = vnd_try_send_A_nextpair_immediate(); }
                    if(!kicked){ vnd_prepare_pair(); kicked = vnd_try_send_A_nextpair_immediate(); }
                }
                if(kicked){ VND_LOG("KICK_TX by GET_STATUS"); }
            }

            if(streaming){
                /* В DIAG-режиме исключаем любые STAT в bulk-потоке: используйте EP0 (ctrl) */
                if(diag_mode_active){ VND_LOG("GET_STATUS bulk ignored in DIAG (use EP0)"); break; }
                pending_status = 1; VND_LOG("GET_STATUS queued"); break;
            }
            if(!vnd_ep_busy)
            {
                vnd_status_permit_once = 1;
                uint16_t l = vnd_build_status((uint8_t*)status_buf, sizeof(status_buf));
                if(l)
                {
                    vnd_tx_ready = 0; vnd_ep_busy = 1; vnd_last_tx_len = l; vnd_last_tx_start_ms = HAL_GetTick();
                    if(USBD_VND_Transmit(&hUsbDeviceHS, (uint8_t*)status_buf, l) == USBD_OK)
                        VND_LOG("STAT_TX req len=%u", l);
                    else { VND_LOG("STAT_BUSY_FAIL"); vnd_tx_ready = 1; vnd_ep_busy = 0; }
                }
            } else {
                pending_status = 1; VND_LOG("STAT_PENDING on GET_STATUS");
            }
        }
        break;

        case VND_CMD_TOGGLE_TIM2CH3_INV:
        {
            /* Переключаем полярность CH3 на лету (PA2 = TIM2_CH3). */
            uint32_t ccer = TIM2->CCER;
            ccer ^= TIM_CCER_CC3P;
            TIM2->CCER = ccer;
            uint8_t inv = (ccer & TIM_CCER_CC3P) ? 1u : 0u;
            printf("[VND] TIM2_CH3 invert=%u CCER=0x%08lX\r\n", (unsigned)inv, (unsigned long)ccer);
            cdc_logf("EVT TIM2_CH3_INV=%u", (unsigned)inv);
        }
        break;

        case VND_CMD_SET_WINDOWS:
            if(len >= 9)
            {
                /* 1 + 8 байт */
                win_start0 = (uint16_t)(data[1] | (data[2] << 8));
                win_len0   = (uint16_t)(data[3] | (data[4] << 8));
                win_start1 = (uint16_t)(data[5] | (data[6] << 8));
                win_len1   = (uint16_t)(data[7] | (data[8] << 8));
                VND_LOG("SET_WINDOWS s0=%u l0=%u s1=%u l1=%u", win_start0, win_len0, win_start1, win_len1);
                vnd_update_lcd_params();
            }
            break;
        case VND_CMD_SET_BLOCK_HZ:
            if(len >= 3)
            {
                uint16_t hz = (uint16_t)(data[1] | (data[2] << 8));
                if(hz == 0xFFFF) hz = 100;
                if(hz < 20) hz = 20;
                if(hz > 400) hz = 400;
                diag_hz = hz;
                diag_period_ms = 1000 / diag_hz;
                VND_LOG("SET_BLOCK_HZ %u", diag_hz);
                cdc_logf("EVT SET_BLOCK_HZ %u", (unsigned)diag_hz);
                /* NEW: авто-подбор профиля под желаемую частоту блоков, если мы в полном режиме (full_mode) */
                if(full_mode){
                    /* Целевые buf_rate_hz профилей: 200(A), 300(B/C/D), 400(E) */
                    uint16_t target = hz; /* запрос хоста: кадров в секунду */
                    uint8_t new_prof = adc_stream_get_profile();
                    if(target >= 380){ new_prof = ADC_PROFILE_E_400HZ; }
                    else if(target >= 250){ new_prof = ADC_PROFILE_B_DEFAULT; }
                    else { new_prof = ADC_PROFILE_A_200HZ; }
                    if(new_prof != adc_stream_get_profile()){
                        int rc = adc_stream_set_profile(new_prof);
                        VND_LOG("AUTO_PROFILE_BY_BLOCK_HZ hz=%u -> prof=%u rc=%d", target, new_prof, rc);
                        if(rc == 0){
                            uint16_t cur_samples = adc_stream_get_active_samples();
                            uint16_t cur_rate = adc_stream_get_buf_rate();
                            cdc_logf("EVT AUTO_PROFILE hz=%u prof=%u samples=%u rate=%u", (unsigned)target, (unsigned)new_prof, (unsigned)cur_samples, (unsigned)cur_rate);
                            /* Сброс lock размера, чтобы новые параметры применились */
                            cur_samples_per_frame = 0; cur_expected_frame_size = 0;
                        }
                    }
                }
                vnd_update_lcd_params();
            }
            break;
        case VND_CMD_SET_ASYNC_MODE:
            if(len >= 2){
                uint8_t mode = data[1];
#if USE_TEST_SAWTOOTH
                /* Игнорируем запросы async в тестовом режиме — нужен парный путь */
                async_mode = 0; vnd_strict_pairing = 0;
                cdc_logf("EVT SET_ASYNC ignored (test mode)");
#else
                async_mode = (mode & 0x01) ? 1 : 0;
                /* bit7 включает строгую парность (A&B на один seq); по умолчанию 0 = независимые каналы */
                vnd_strict_pairing = (mode & 0x80) ? 1 : 0;
                /* Запретить async при одноканальном режиме (A-only/B-only) для стабильности */
                if(vnd_ch_mode != 2 && async_mode){ async_mode = 0; }
                VND_LOG("SET_ASYNC_MODE async=%u strict_pair=%u", (unsigned)async_mode, (unsigned)vnd_strict_pairing);
                cdc_logf("EVT SET_ASYNC async=%u strict=%u", (unsigned)async_mode, (unsigned)vnd_strict_pairing);
                /* Сбросим ожидания и канал передачи */
                pending_B = 0; sending_channel = 0xFF;
#endif
            }
            break;
        case VND_CMD_SET_CHMODE:
            if(len >= 2){
                uint8_t m = data[1];
#if USE_TEST_SAWTOOTH
                /* В тестовом режиме принудительно оба канала */
                vnd_ch_mode = 2; async_mode = 0;
                cdc_logf("EVT SET_CHMODE forced BOTH (test mode)");
#else
                if(m > 2) m = 2; /* default both */
                vnd_ch_mode = m;
                /* При A-only/B-only принудительно выключаем async */
                if(vnd_ch_mode != 2 && async_mode){ async_mode = 0; }
                VND_LOG("SET_CHMODE %u", (unsigned)vnd_ch_mode);
                cdc_logf("EVT SET_CHMODE %u", (unsigned)vnd_ch_mode);
#endif
                /* Переключение режимов на лету: сброс ожиданий B, чтобы не зависать */
                pending_B = 0; sending_channel = 0xFF;
            }
            break;
        case VND_CMD_SET_FULL_MODE:
            if(len >= 2)
            {
                uint8_t full = data[1] ? 1 : 0;
                full_mode = full;
                VND_LOG("SET_FULL_MODE %u", full_mode);
                cdc_logf("EVT SET_FULL_MODE %u", (unsigned)full_mode);
                if(streaming){
                    /* Переключение режима на лету: сброс пары и разрешение TEST не требуется */
                    pending_B = 0; sending_channel = 0xFF;
                }
                if(full_mode){
                    /* Возврат к нормальному режиму ADC */
                    diag_mode_active = 0; diag_prepared_seq = 0xFFFFFFFFu;
                    /* При входе в полный режим – использовать фактический размер профиля ADC, снять lock и пересчитать период */
                    vnd_frame_samples_req = 0; /* 0 = использовать g_active_samples из профиля */
                    cur_samples_per_frame = 0;
                    cur_expected_frame_size = 0;
                    vnd_recompute_pair_timing(vnd_frame_samples_req);
                } else {
                    /* Включаем диагностический режим (пила) */
                    diag_mode_active = 1;
                    diag_samples = (cur_samples_per_frame != 0) ? cur_samples_per_frame : VND_DEFAULT_TEST_SAMPLES;
                    if(diag_samples > VND_MAX_SAMPLES) diag_samples = VND_MAX_SAMPLES;
                    cur_samples_per_frame = diag_samples;
                    cur_expected_frame_size = (uint16_t)(VND_FRAME_HDR_SIZE + cur_samples_per_frame*2u);
                    /* Разрешаем немедленную отправку диагностических кадров */
                    diag_next_ms = HAL_GetTick(); diag_prepared_seq = 0xFFFFFFFFu;
                }
                vnd_update_lcd_params();
            }
            break;
        case VND_CMD_SET_TRUNC_SAMPLES:
            if(len >= 3){
                uint16_t ns = (uint16_t)(data[1] | (data[2] << 8));
                vnd_trunc_samples = ns;
                VND_LOG("SET_TRUNC_SAMPLES %u", (unsigned)vnd_trunc_samples);
                cdc_logf("EVT SET_TRUNC %u", (unsigned)vnd_trunc_samples);
                /* Сбросим текущий lock размера, чтобы статус отразил новые размеры, применится при следующем кадре */
                cur_samples_per_frame = 0;
                cur_expected_frame_size = 0;
            }
            break;
        case VND_CMD_SET_PROFILE:
            if(len >= 2)
            {
                uint8_t profile = data[1];
                uint8_t prof_id = ADC_PROFILE_B_DEFAULT;
                // Маппинг host profile -> firmware profile ID:
                // 0 -> ADC_PROFILE_A_200HZ (1360 samples @ 200Hz)
                // 1 -> ADC_PROFILE_B_DEFAULT (912 samples @ 300Hz)
                // 2 -> ADC_PROFILE_C_HIGH (944 samples @ 300Hz)
                // 3 -> ADC_PROFILE_D_MAX (976 samples @ 300Hz)
                // 4 -> ADC_PROFILE_E_400HZ (680 samples @ 400Hz HIGH-FPS)
                if(profile == 0) prof_id = ADC_PROFILE_A_200HZ;
                else if(profile == 1) prof_id = ADC_PROFILE_B_DEFAULT;
                else if(profile == 2) prof_id = ADC_PROFILE_C_HIGH;
                else if(profile == 3) prof_id = ADC_PROFILE_D_MAX;
                else if(profile == 4) prof_id = ADC_PROFILE_E_400HZ;
                int rc = adc_stream_set_profile(prof_id);
                VND_LOG("SET_PROFILE %u -> prof_id=%u rc=%d", profile, prof_id, rc);
                /* ДИАГНОСТИКА: вывести текущее состояние после смены профиля */
                if(rc == 0) {
                    host_profile = profile; /* запомним для LCD ровно то, что прислал хост */
                    uint16_t cur_samples = adc_stream_get_active_samples();
                    uint16_t cur_rate = adc_stream_get_buf_rate();
                    cdc_logf("EVT SET_PROFILE p=%u samples=%u rate=%u Hz", profile, cur_samples, cur_rate);
                    vnd_update_lcd_params();
                }
            }
            break;
        case VND_CMD_SET_ROI_US:
            if(len >= 5)
            {
                uint32_t us = (uint32_t)(data[1] | (data[2] << 8) | (data[3] << 16) | (data[4] << 24));
                (void)us;
                /* TODO: применить ROI к цепочке выборки */
                VND_LOG("SET_ROI_US %lu", (unsigned long)us);
            }
            break;
        default:
            VND_LOG("IGN %02X", cmd);
            break;
    }
}

/* Duplicate vnd_diag_send64_once removed */
uint32_t vnd_get_last_txcplt_ms(void)
{
    return vnd_last_txcplt_ms;
}

/* Общее число переданных байт (все передачи) */
uint64_t vnd_get_total_tx_bytes(void)
{
    return vnd_total_tx_bytes;
}

/* Общее число переданных сэмплов (оба канала суммарно) */
uint64_t vnd_get_total_tx_samples(void)
{
    return vnd_total_tx_samples;
}

/* Общая диагностическая функция: при обращении хоста за статусом проверяет признаки паузы
   и печатает STALL_WARN не чаще чем раз в 2 секунды. Вызывается как из bulk-пути, так и из EP0. */
/* Беззнаковая разность тиков (обработка переполнения 32-бит HAL_GetTick()). */
static inline uint32_t tick_diff32(uint32_t now, uint32_t before){
    return (now >= before) ? (now - before) : (0xFFFFFFFFu - before + 1u + now);
}

void vnd_diag_log_possible_stall(void)
{
    static uint32_t last_stall_warn_ms = 0;
    if(!streaming) return;
    uint32_t now_ms = HAL_GetTick();
    uint32_t dt = tick_diff32(now_ms, vnd_last_txcplt_ms);
    if(dt <= 600 || dt >= 3000) return;
    if(last_stall_warn_ms != 0 && (now_ms - last_stall_warn_ms) <= 2000) return;
    extern uint8_t USBD_VND_TxIsBusy(void);
    uint8_t ll_busy = USBD_VND_TxIsBusy();
    const char *stA = (g_frames[0][0].st==FB_READY?"READY":(g_frames[0][0].st==FB_SENDING?"SENDING":"FILL"));
    const char *stB = (g_frames[0][1].st==FB_READY?"READY":(g_frames[0][1].st==FB_SENDING?"SENDING":"FILL"));
    adc_stream_debug_t dbg; adc_stream_get_debug(&dbg);
    const char *stall_class = "UNKNOWN";
    uint8_t have_meta = (vnd_tx_meta_depth() > 0);
    uint32_t wr = frame_wr_seq, rd = frame_rd_seq;
    if(!have_meta && !pending_B && !ll_busy && !vnd_ep_busy){
        stall_class = "ADC_IDLE";
    } else if((vnd_ep_busy || ll_busy) && vnd_inflight){
        stall_class = "USB_BUSY";
    } else if(pending_B){
        if(g_frames[0][1].st!=FB_READY && g_frames[0][1].st!=FB_SENDING){ stall_class = "WAIT_B_FILL"; }
        else if(g_frames[0][1].st==FB_READY && !vnd_ep_busy){ stall_class = "B_READY_NOT_TX"; }
        else if(g_frames[0][1].st==FB_SENDING && (now_ms - vnd_last_tx_start_ms) > 120){ stall_class = "B_INFLIGHT_LONG"; }
        else stall_class = "PEND_B";
    } else if(!pending_B && g_frames[0][0].st==FB_READY && !vnd_ep_busy){
        stall_class = "A_READY_NOT_TX";
    } else if(have_meta && !vnd_inflight && !vnd_ep_busy){
        stall_class = "META_WAIT";
    }
    static uint32_t last_wr_seq = 0; static uint32_t last_wr_ms = 0;
    if(wr != last_wr_seq){ last_wr_seq = wr; last_wr_ms = now_ms; }
    else if(tick_diff32(now_ms, last_wr_ms) > 300 && strcmp(stall_class,"ADC_IDLE")!=0){ stall_class = "ADC_STALL"; }
    cdc_logf("STALL_WARN class=%s dt=%lums ep_busy=%u ll_busy=%u inflight=%u pendB=%u ch=%u A=%s B=%s metaD=%u wr=%lu rd=%lu prod=%lu sentA=%lu sentB=%lu dmaF0=%lu dmaF1=%lu",
        stall_class,
        (unsigned long)dt,
        (unsigned)vnd_ep_busy,
        (unsigned)ll_busy,
        (unsigned)vnd_inflight,
        (unsigned)pending_B,
        (unsigned)sending_channel,
        stA, stB,
        (unsigned)vnd_tx_meta_depth(),
        (unsigned long)wr,
        (unsigned long)rd,
        (unsigned long)dbg_produced_seq,
        (unsigned long)dbg_sent_ch0_total,
        (unsigned long)dbg_sent_ch1_total,
        (unsigned long)dbg.dma_full0,
        (unsigned long)dbg.dma_full1);
    VND_LOG("STALL_WARN class=%s dt=%lums ep_busy=%u ll_busy=%u pendB=%u A=%s B=%s metaD=%u wr=%lu rd=%lu",
        stall_class,
        (unsigned long)dt,
        (unsigned)vnd_ep_busy,
        (unsigned)ll_busy,
        (unsigned)pending_B,
        stA, stB,
        (unsigned)vnd_tx_meta_depth(),
        (unsigned long)wr,
        (unsigned long)rd);
    last_stall_warn_ms = now_ms;
}

/* Хук от цепочки ADC: когда появились новые кадры — пинаем таск */
void adc_stream_on_new_frames(uint32_t frames_added)
{
    (void)frames_added;
    /* минимальный kick: если не заняты и идёт стрим — дать шанс таску отправить */
    if(streaming){ vnd_tx_kick = 1; }
}

/* Получить текущие min/max АЦП значения из последних отправленных кадров */
void vnd_get_adc_minmax(int16_t *adc0_min, int16_t *adc0_max, 
                        int16_t *adc1_min, int16_t *adc1_max)
{
    static int16_t cached_adc0_min = 0;
    static int16_t cached_adc0_max = 0;
    static int16_t cached_adc1_min = 0;
    static int16_t cached_adc1_max = 0;
    static uint32_t cached_seq = 0;
    static uint32_t log_count = 0;
    
    if(!adc0_min || !adc0_max || !adc1_min || !adc1_max) {
        return;
    }
    
    /* Пытаемся найти последний готовый кадр для получения min/max */
    uint8_t idx = (pair_send_idx > 0) ? (pair_send_idx - 1) : (VND_PAIR_BUFFERS - 1);
    
    /* Логируем состояние буферов каждые 50 вызовов */
    if(log_count++ % 50 == 0) {
        printf("[VND_GET_MINMAX] idx=%u seq_cached=%lu seq_current=%lu st=%u samples0=%u samples1=%u\r\n",
               idx, cached_seq, g_frames[idx][0].seq, g_frames[idx][0].st,
               g_frames[idx][0].samples, g_frames[idx][1].samples);
    }
    
    /* Проверяем, был ли кадр уже обработан */
    if(g_frames[idx][0].seq != cached_seq && g_frames[idx][0].st != FB_FILL) {
        cached_seq = g_frames[idx][0].seq;
        printf("[VND_GET_MINMAX] Processing frame seq=%lu\r\n", cached_seq);
        
        /* Декодируем min/max из кадра ADC0 */
        const uint8_t *buf = g_frames[idx][0].buf;
        uint16_t samples = g_frames[idx][0].samples;
        
        if(samples > 0 && g_frames[idx][0].frame_size >= 32 + 2*samples) {
            const int16_t *data = (const int16_t *)(buf + 32);
            cached_adc0_min = data[0];
            cached_adc0_max = data[0];
            
            for(uint16_t i = 1; i < samples; i++) {
                if(data[i] < cached_adc0_min) cached_adc0_min = data[i];
                if(data[i] > cached_adc0_max) cached_adc0_max = data[i];
            }
        }
        
        /* Декодируем min/max из кадра ADC1 */
        buf = g_frames[idx][1].buf;
        samples = g_frames[idx][1].samples;
        
        if(samples > 0 && g_frames[idx][1].frame_size >= 32 + 2*samples) {
            const int16_t *data = (const int16_t *)(buf + 32);
            cached_adc1_min = data[0];
            cached_adc1_max = data[0];
            
            for(uint16_t i = 1; i < samples; i++) {
                if(data[i] < cached_adc1_min) cached_adc1_min = data[i];
                if(data[i] > cached_adc1_max) cached_adc1_max = data[i];
            }
        }
        
        printf("[VND_GET_MINMAX] Decoded: CH0=%d..%d CH1=%d..%d\r\n",
               cached_adc0_min, cached_adc0_max, cached_adc1_min, cached_adc1_max);
    }
    
    *adc0_min = cached_adc0_min;
    *adc0_max = cached_adc0_max;
    *adc1_min = cached_adc1_min;
    *adc1_max = cached_adc1_max;
}

/* EOF (clean version) */
