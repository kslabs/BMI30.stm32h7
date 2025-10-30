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
/* Для дублирования фрагментов кадров в CDC (Virtual COM) */
#include "usbd_cdc_if.h"
#include "usbd_cdc_custom.h" /* для USBD_VND_RequestSoftReset/DeepReset (объявления находятся в .c) */
#include "vnd_testgen.h"

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
#define VND_ENABLE_LOG 0 /* уменьшили шум лога для стабильности */
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
#define VND_CMD_GET_STATUS     0x30u
/* ДОБАВЛЕНО: управление окнами/частотой */
#define VND_CMD_SET_WINDOWS    0x10u /* payload: start0,len0,start1,len1 (LE, u16) */
#define VND_CMD_SET_BLOCK_HZ   0x11u /* payload: u16 hz (20..100) или 0xFFFF=макс (100) */
/* Новая команда: установка ограничения числа выборок на канал в рабочем кадре */
#define VND_CMD_SET_TRUNC_SAMPLES 0x16u /* payload: u16 samples (0=отключить усечение) */
/* Новая команда: явная установка samples_per_frame для управления FPS (пара A+B ≈ Fs/samples) */
#define VND_CMD_SET_FRAME_SAMPLES 0x17u /* payload: u16 samples_per_frame (на канал) */

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

/* Дефолтный размер кадра в полном режиме (семплов на канал) */
#ifndef VND_FULL_DEFAULT_SAMPLES
#define VND_FULL_DEFAULT_SAMPLES 300
#endif

/* Тестовый режим: генерация пилообразного сигнала вместо реальных данных ADC */
#define USE_TEST_SAWTOOTH 1
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
#define VND_STATUS_MAX 64
static uint8_t status_buf[VND_STATUS_MAX];
static vnd_status_v1_t g_status;
static volatile uint8_t pending_status = 0; /* требуется отправить STAT при освобождении EP */

/* Состояние фейковых кадров */
static uint8_t fake_inflight = 0;   /* reserved for diag */
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

/* Локальная утилита: обновить LCD параметрами, присланными хостом */
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
static uint32_t cdc_last_send_ms = 0;       /* для троттлинга */
static char     cdc_line_buf[1024];         /* статический буфер для передачи */
static uint16_t rd_le16(const uint8_t *p){ return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t rd_le32(const uint8_t *p){ return (uint32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24)); }
static void vnd_cdc_duplicate_preview(const uint8_t *buf, uint16_t len, const char *tag)
{
    /* При отключённом превью не выводим копию потока в CDC */
#if !VND_CDC_PREVIEW_ENABLE
    (void)buf; (void)len; (void)tag;
    return;
#else
    /* Троттлинг, чтобы не забивать CDC: не чаще 10 Гц */
    uint32_t now = HAL_GetTick();
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
    cdc_logf("STAT bytes_total=%llu bps=%lu streaming=%u diag=%u",
             (unsigned long long)cur, (unsigned long)bps, (unsigned)streaming, (unsigned)diag_mode_active);
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
    uint8_t  buf[VND_FRAME_MAX_SIZE];
} ChanFrame;

#define VND_PAIR_BUFFERS 8  /* Увеличено для избежания перезаписи во время USB-передачи */
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
static void vnd_debug_force_status_tick(void);
static void vnd_debug_raw_stat_tick(void);
static void __attribute__((unused)) vnd_send_fake_pair(void); // удалим позже; сейчас заглушка ниже
static void vnd_log_hdr_layout(void);
static void vnd_try_send_pending_status_from_task(void);
static void vnd_try_send_test_from_task(void);
/* Быстрый пайплайн: немедленная отправка следующего кадра из TxCplt */
static int vnd_try_send_B_immediate(void);
static int vnd_try_send_A_nextpair_immediate(void);
/* Аварийный keepalive: если совсем нет успешных завершений передач первые секунды */
static void vnd_emergency_keepalive(uint32_t now_ms);
/* Сервис: асинхронная обработка команд управления (EP0 SOFT/DEEP RESET) */
extern void USBD_VND_ProcessControlRequests(void);
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

/* ---------------- Вспомогательные ---------------- */
static void vnd_reset_buffers(void){
    for(uint8_t p=0;p<VND_PAIR_BUFFERS;p++) for(uint8_t c=0;c<2;c++){ g_frames[p][c].st=FB_FILL; g_frames[p][c].samples=0; g_frames[p][c].flags = c?VND_FLAGS_ADC1:VND_FLAGS_ADC0; g_frames[p][c].frame_size=0; g_frames[p][c].seq=0; memset(g_frames[p][c].buf,0xCC,sizeof(g_frames[p][c].buf)); }
    pair_fill_idx=pair_send_idx=0; sending_channel=0xFF; channel0_sent_curseq=channel1_sent_curseq=0; pending_B = 0; pending_B_since_ms = 0; }

uint16_t vnd_build_status(uint8_t *dst, uint16_t max_len){
    if(max_len < sizeof(vnd_status_v1_t)) return 0;
    memset(&g_status,0,sizeof(g_status));
    /* Сигнатура 'STAT' в первых 4 байтах */
    g_status.sig[0] = 'S';
    g_status.sig[1] = 'T';
    g_status.sig[2] = 'A';
    g_status.sig[3] = 'T';
    g_status.version = 1;
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
    if(streaming) g_status.flags_runtime |= VND_STFLAG_STREAMING;
    if(diag_mode_active) g_status.flags_runtime |= VND_STFLAG_DIAG_ACTIVE;
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
    /* Доп. диагностика: наличие готовых кадров в текущей паре */
    {
        ChanFrame *fa = &g_frames[pair_send_idx][0];
        ChanFrame *fb = &g_frames[pair_send_idx][1];
        if (fa->st == FB_READY) f2 |= 1u<<11;
        if (fb->st == FB_READY) f2 |= 1u<<12;
        /* Новые биты: состояние SENDING для A/B чтобы различать READY и активную передачу */
        if (fa->st == FB_SENDING) f2 |= 1u<<13;
        if (fb->st == FB_SENDING) f2 |= 1u<<14;
        /* ДОБАВЛЕНО: наличие готовых кадров в буфере подготовки (pair_fill_idx) */
        ChanFrame *fa_fill = &g_frames[pair_fill_idx][0];
        ChanFrame *fb_fill = &g_frames[pair_fill_idx][1];
    if (fa_fill->st == FB_READY) f2 |= 1u<<15;
    /* Места под отдельный бит для B_fill нет в v1: пропускаем, чтобы не конфликтовать с битом0 */
    }
    g_status.flags2 = f2;
    g_status.sending_ch = sending_channel;
    g_status.pair_idx = (uint16_t)(((uint16_t)pair_fill_idx << 8) | (uint16_t)pair_send_idx);
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
     /* reserved2: младший байт dbg_prepare_ok */
     g_status.reserved2 = (uint8_t)(dbg_prepare_ok & 0xFFu);
     extern volatile uint32_t frame_rd_seq; /* из adc_stream.c */
     g_status.reserved3 = (uint16_t)(frame_rd_seq & 0xFFFFu);
     /* Хак: инкремент dbg_skipped_frames отображаем в sent0/sent1 дельтах, но здесь добавим только
        косвенную диагностику: если skips растут, host увидит разницу produced_seq - sent*. Дополнительно
        можно временно печатать в CDC при отладке (сейчас лог выключен для скорости). */
    memcpy(dst,&g_status,sizeof(g_status));
    return (uint16_t)sizeof(g_status);
}

uint8_t vnd_is_streaming(void){ return streaming; }

/* функция vnd_generate_test_sawtooth() реализована в vnd_testgen.c */

static void vnd_prepare_pair(void)
{
    dbg_prepare_calls++;
    VND_LOG("PREPARE_PAIR called (fill_idx=%u)", (unsigned)pair_fill_idx);
    uint16_t *ch1 = NULL, *ch2 = NULL; uint16_t samples = 0;
    
#if USE_TEST_SAWTOOTH
    /* Тестовый режим: используем последнюю сгенерированную пару буферов */
    {
        uint16_t *tb0 = NULL, *tb1 = NULL; uint16_t avail = 0;
        if(!vnd_testgen_try_consume_latest(&tb0, &tb1, &avail)){
            return; /* нет новых данных */
        }
        ch1 = tb0; ch2 = tb1;
        samples = (avail != 0) ? avail : VND_FULL_DEFAULT_SAMPLES;
    }
#else
    /* last-buffer-wins: берём последний доступный кадр; если накопилась очередь >1, пропускаем старые */
    /* Локальная логика: забрать последний кадр из ADC FIFO, безопасно по отношению к ISR */
    {
        uint32_t wr, rd, backlog, seq;
        __disable_irq();
        wr = frame_wr_seq; rd = frame_rd_seq;
        if (wr == rd) { __enable_irq(); return; }
        backlog = wr - rd;
        if (backlog > 1u) {
            /* Перескочить на последний полный кадр и пометить все промежуточные как пропущенные */
            seq = wr - 1u;
            dbg_skipped_frames += (backlog - 1u);
            frame_rd_seq = wr; /* потребили все до последнего */
        } else {
            seq = rd; frame_rd_seq = rd + 1u;
        }
        __enable_irq();
        uint32_t index = (uint32_t)(seq & (FIFO_FRAMES - 1u));
        ch1 = adc1_buffers[index];
        ch2 = adc2_buffers[index];
        /* ИСПРАВЛЕНИЕ: использовать глобальный getter вместо внутреннего debug поля,
           чтобы получить актуальное значение samples после смены профиля */
        samples = adc_stream_get_active_samples();
    }
#endif
    if(samples == 0){
        /* Нет новых данных от АЦП — ничего не отправляем */
        return;
    }
    /* Применяем усечение до блокировки формата */
    uint16_t effective = samples;
    /* Применим явный лимит от хоста (samples_per_frame) если задан */
    if(vnd_frame_samples_req && vnd_frame_samples_req < effective) effective = vnd_frame_samples_req;
    if(vnd_trunc_samples && vnd_trunc_samples < effective) effective = vnd_trunc_samples;
    if(cur_samples_per_frame == 0){
        if(effective > VND_MAX_SAMPLES) effective = VND_MAX_SAMPLES;
        cur_samples_per_frame = effective;
        cur_expected_frame_size = (uint16_t)(VND_FRAME_HDR_SIZE + (uint32_t)cur_samples_per_frame * 2u);
    VND_LOG("SIZE_LOCK %u (raw=%u trunc=%u)", cur_samples_per_frame, samples, vnd_trunc_samples);
    /* Не меняем stream_seq здесь: seq инкрементируется только после завершения кадра B (TxCplt) */
    }
    if(effective != cur_samples_per_frame){
        VND_LOG("SIZE_MISMATCH: eff=%u cur=%u raw=%u", effective, cur_samples_per_frame, samples);
        dbg_partial_frame_abort++;
        return;
    }
    ChanFrame *f0 = &g_frames[pair_fill_idx][0];
    ChanFrame *f1 = &g_frames[pair_fill_idx][1];
    if(f0->st != FB_FILL || f1->st != FB_FILL) return;
    memset(f0->buf, 0, sizeof(f0->buf)); memset(f1->buf, 0, sizeof(f1->buf));
    uint32_t pair_timestamp = HAL_GetTick();
    /* подробный лог пары убран для снижения нагрузки */
    /* Применяем усечение, если задано и меньше доступного */
    uint16_t use_samples = cur_samples_per_frame; /* уже определено и проверено */
    for(uint16_t i = 0; i < use_samples; i++){
#if USE_TEST_SAWTOOTH
        /* В тестовом режиме гарантируем детерминированный шаблон 1..N
           независимо от источника буферов, чтобы упростить верификацию хостом. */
        uint16_t a = (uint16_t)(i + 1);
        uint16_t b = (uint16_t)(i + 1);
#else
        uint16_t a = ch1[i];
        uint16_t b = ch2[i];
#endif
        uint8_t *p0 = f0->buf + VND_FRAME_HDR_SIZE + 2 * i; p0[0] = (uint8_t)(a & 0xFF); p0[1] = (uint8_t)(a >> 8);
        uint8_t *p1 = f1->buf + VND_FRAME_HDR_SIZE + 2 * i; p1[0] = (uint8_t)(b & 0xFF); p1[1] = (uint8_t)(b >> 8);
    }
    f0->samples = f1->samples = use_samples; 
    /* Incrementing seq counter для каждой пары */
    static volatile uint32_t real_pair_seq = 1;  /* Начинаем с 1, чтобы избежать путаницы с инициализацией (=0) */
    f0->seq = f1->seq = real_pair_seq++;
    vnd_frame_hdr_t *h0 = (vnd_frame_hdr_t*)f0->buf; h0->timestamp = pair_timestamp;
    vnd_frame_hdr_t *h1 = (vnd_frame_hdr_t*)f1->buf; h1->timestamp = pair_timestamp;
    vnd_build_frame(f0); vnd_build_frame(f1);
    if(f0->st == FB_FILL || f1->st == FB_FILL){ dbg_partial_frame_abort++; VND_LOG("build failed"); return; }
    /* VND_LOG("Pair prepared, fill_idx=%u", pair_fill_idx); */
    pair_fill_idx = (pair_fill_idx + 1u) % VND_PAIR_BUFFERS;
    dbg_prepare_ok++;
}

static void vnd_build_frame(ChanFrame *cf)
{
    if(cf->samples == 0){ cf->st = FB_FILL; return; }
    uint32_t payload_len = (uint32_t)cf->samples * 2u;
    uint32_t total = VND_FRAME_HDR_SIZE + payload_len;
    vnd_frame_hdr_t *h = (vnd_frame_hdr_t*)cf->buf;
    
    h->magic = 0xA55A; h->ver = 0x01; h->flags = (cf->flags & VND_FLAGS_ADC0) ? 0x01 : 0x02; h->seq = cf->seq; h->total_samples = (uint16_t)cf->samples;
    VND_LOG("BUILD_FRAME cf_seq=%lu flags=0x%02X samples=%u", (unsigned long)cf->seq, (unsigned)h->flags, (unsigned)cf->samples);
    h->zone_count = 0; h->zone1_offset = 0; h->zone1_length = 0; h->reserved = 0; h->reserved2 = 0; h->crc16 = 0;
    cf->frame_size = (uint16_t)total;
    if(cur_expected_frame_size && cf->frame_size != cur_expected_frame_size) dbg_size_mismatch++;
    dbg_any_valid_frame = 1; cf->st = FB_READY;
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
    USBD_StatusTypeDef rc = USBD_VND_Transmit(&hUsbDeviceHS, buf, len);
    if(rc == USBD_BUSY){
        dbg_resend_blocked++; vnd_error_counter++; if(vnd_last_error == 0) vnd_last_error = 4;
        /* Диагностика LL: получим last rc/len и флаг занятости */
        extern uint8_t USBD_VND_TxIsBusy(void);
        extern uint8_t USBD_VND_LastTxRC(void);
        extern uint16_t USBD_VND_LastTxLen(void);
        uint8_t ll_busy = USBD_VND_TxIsBusy(); uint8_t ll_rc = USBD_VND_LastTxRC(); uint16_t ll_len = USBD_VND_LastTxLen();
        VND_LOG("TX_BUSY tag=%s len=%u ll_busy=%u last_rc=%u last_len=%u", tag?tag:"?", (unsigned)len, (unsigned)ll_busy, (unsigned)ll_rc, (unsigned)ll_len);
        vnd_tx_ready = 1; vnd_ep_busy = 0; vnd_inflight = 0;
    }
    else {
        /* Фиксируем метаданные ТОЛЬКО после успешного запуска передачи, иначе не сместим FIFO зря */
        vnd_tx_meta_after(buf, len);
        if(is_frame){
            const vnd_frame_hdr_t *lh = (const vnd_frame_hdr_t*)buf;
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
    /* Полный режим: отправляем B из текущего pair_send_idx, если READY */
    ChanFrame *fB = &g_frames[pair_send_idx][1];
    if(fB->st != FB_READY) return 0;
    /* Корректируем seq при необходимости (безопасно) */
    if(fB->frame_size >= VND_FRAME_HDR_SIZE){
        vnd_frame_hdr_t *hb = (vnd_frame_hdr_t*)fB->buf;
        if(hb->magic == 0xA55A && hb->seq != stream_seq){ hb->seq = stream_seq; fB->seq = stream_seq; }
    }
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
    ChanFrame *fA = &g_frames[pair_send_idx][0];
    if(fA->st != FB_READY){
        vnd_prepare_pair();
        fA = &g_frames[pair_send_idx][0];
        if(fA->st != FB_READY) return 0;
    }
    /* Принудительно синхронизируем seq A с текущим stream_seq для консистентности пары */
    if(fA->frame_size >= VND_FRAME_HDR_SIZE){
        vnd_frame_hdr_t *ha = (vnd_frame_hdr_t*)fA->buf;
        if(ha->magic == 0xA55A && ha->seq != stream_seq){ ha->seq = stream_seq; fA->seq = stream_seq; }
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
static void vnd_emergency_keepalive(uint32_t now_ms)
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
    /* ВАЖНО: сначала попробуем подготовить пару A/B, чтобы не зациклиться на ранних STAT.
       Подготовка пары не зависит от занятости EP, поэтому убираем лишний гейтинг по vnd_ep_busy. */
    {
        ChanFrame *fA0 = &g_frames[pair_send_idx][0];
        if(fA0->st != FB_READY){ vnd_prepare_pair(); }
    }
    /* Раннее окно для GET_STATUS до первой пары — отключено: STAT по IN только между парами. */
    /* Дополнительный ранний запуск TEST: если после START прошло >50 мс и EP свободен */
    if(!test_sent && !test_in_flight) {
        if(!vnd_ep_busy){
            if (now - start_cmd_ms > 50) {
                vnd_try_send_test_from_task();
                if(vnd_ep_busy){ if(vnd_tick_flag) vnd_tick_flag = 0; return; }
            }
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
    if(!test_sent){
        if(!vnd_ep_busy){
#if !VND_DISABLE_TEST
            vnd_try_send_test_from_task();
#endif
        }
        /* Не выходим раньше времени: позволим подготовку/отправку A/B идти параллельно,
           чтобы не блокироваться на тестовом кадре. */
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

    static uint8_t first_pair_logged = 0; /* диагностический лог первой пары */

    static uint8_t first_bq_logged = 0; /* однократный лог первой постановки B */
    if(pending_B){
        /* Гарантируем, что текущая пара действительно подготовлена: если A ещё не готов (FB_FILL) — соберём пару сейчас. */
        ChanFrame *fA_pre = &g_frames[pair_send_idx][0];
        if(fA_pre->st == FB_FILL && !vnd_ep_busy){ vnd_prepare_pair(); }
        ChanFrame *fB = &g_frames[pair_send_idx][1];
        if(fB->st == FB_READY){
            /* Перед отправкой B корректируем seq, если он отличается от ожидаемого stream_seq */
            if(fB->frame_size >= VND_FRAME_HDR_SIZE){
                vnd_frame_hdr_t *hb = (vnd_frame_hdr_t*)fB->buf;
                if(hb->magic == 0xA55A && hb->seq != stream_seq){
                    VND_LOG("PATCH_B_SEQ hdr=%lu -> %lu", (unsigned long)hb->seq, (unsigned long)stream_seq);
                    hb->seq = stream_seq; fB->seq = stream_seq;
                }
            }
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
            /* Диагностируем, почему ждём B: выводим однократно переход в нестандартное состояние */
            static uint32_t last_log_ms = 0;
            uint32_t now_ms = HAL_GetTick();
            if(now_ms - last_log_ms > 200){
                VND_LOG("WAIT_B st=%u pair_send=%u fill_idx=%u seq=%lu cur_seq=%lu", (unsigned)fB->st, (unsigned)pair_send_idx, (unsigned)pair_fill_idx, (unsigned long)fB->seq, (unsigned long)stream_seq);
                last_log_ms = now_ms;
            }
            /* Watchdog B: если B уже в полёте и нет TxCplt слишком долго — форсируем завершение пары */
            if(fB->st == FB_SENDING && (now_ms - vnd_last_tx_start_ms) > 150){
                /* Не закрываем пару! Снимаем busy, нейтрализуем старую мета и переотправляем B */
                extern void USBD_VND_ForceTxIdle(void); USBD_VND_ForceTxIdle();
                vnd_ep_busy = 0; vnd_tx_ready = 1; vnd_inflight = 0;
                vnd_meta_neutralize(0x02, g_frames[pair_send_idx][1].seq);
                g_frames[pair_send_idx][1].st = FB_READY; sending_channel = 0xFF;
                VND_LOG("B_TXCPLT_WD (>150ms) -> retry B seq=%lu", (unsigned long)g_frames[pair_send_idx][1].seq);
                /* Попробуем сразу переотправить */
                ChanFrame *fB2 = &g_frames[pair_send_idx][1];
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
                ChanFrame *fBchk = &g_frames[pair_send_idx][1];
                ChanFrame *fAchk = &g_frames[pair_send_idx][0];
                if(fAchk->st != FB_SENDING && fBchk->st != FB_SENDING){
                    if(fBchk->st == FB_READY){
                        /* Перед отправкой по вотчдогу также поправим seq при необходимости */
                        if(fBchk->frame_size >= VND_FRAME_HDR_SIZE){
                            vnd_frame_hdr_t *hb2 = (vnd_frame_hdr_t*)fBchk->buf;
                            if(hb2->magic == 0xA55A && hb2->seq != stream_seq){ hb2->seq = stream_seq; fBchk->seq = stream_seq; VND_LOG("PATCH_B_SEQ_WDG->%lu", (unsigned long)stream_seq); }
                        }
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
    ChanFrame *fA = &g_frames[pair_send_idx][0];
        /* Watchdog: если A завис в SENDING и долго нет TxCplt — считаем A завершённым и переходим к B */
        do {
            uint32_t now_ms = HAL_GetTick();
            if (fA->st == FB_SENDING && (now_ms - vnd_last_tx_start_ms) > 120) {
                /* Не считаем A завершённым — лишь снимаем busy, нейтрализуем старую мета и открываем ожидание B */
                VND_LOG("A_TXCPLT_WD (>120ms) -> open pending_B, neutralize A meta, continue");
                extern void USBD_VND_ForceTxIdle(void); USBD_VND_ForceTxIdle();
                vnd_ep_busy = 0; vnd_tx_ready = 1; vnd_inflight = 0; sending_channel = 0xFF;
                vnd_meta_neutralize(0x01, g_frames[pair_send_idx][0].seq);
                pending_B = 1; pending_B_since_ms = now_ms;
            }
        } while(0);
        if(fA->st != FB_READY){ vnd_prepare_pair(); fA = &g_frames[pair_send_idx][0]; }
    if(fA->st == FB_READY){
            /* Искусственных задержек между кадрами нет: отправляем A сразу при готовности EP и данных */
            /* Отправляем A: в режиме без TEST не проверяем test_in_flight вовсе */
#if VND_DISABLE_TEST
            /* Перед отправкой A — также синхронизируем seq с текущим stream_seq */
            if(fA->frame_size >= VND_FRAME_HDR_SIZE){
                vnd_frame_hdr_t *ha = (vnd_frame_hdr_t*)fA->buf;
                if(ha->magic == 0xA55A && ha->seq != stream_seq){ ha->seq = stream_seq; fA->seq = stream_seq; }
            }
            VND_LOG("TRY_A len=%u hdr_seq=%lu", (unsigned)fA->frame_size, (unsigned long)((vnd_frame_hdr_t*)fA->buf)->seq);
            if (vnd_transmit_frame(fA->buf, fA->frame_size, 0, 0, "ADC0") == USBD_OK) {
                static uint8_t first_a_logged = 0;
                if(!first_a_logged){ first_a_logged = 1; VND_LOG("FIRST_A queued size=%u", (unsigned)fA->frame_size); }
                fA->st = FB_SENDING; sending_channel = 0;
                /* Ранний запрет STAT между A и B: сразу помечаем ожидание B */
                pending_B = 1; pending_B_since_ms = HAL_GetTick();
                return;
            }
#else
            /* Отправляем A только если нет теста в полёте и нет необработанного TEST в FIFO */
            if(!test_in_flight){
                /* Синхронизируем seq A и в ветке с тестом на всякий случай */
                if(fA->frame_size >= VND_FRAME_HDR_SIZE){
                    vnd_frame_hdr_t *ha = (vnd_frame_hdr_t*)fA->buf;
                    if(ha->magic == 0xA55A && ha->seq != stream_seq){ ha->seq = stream_seq; fA->seq = stream_seq; }
                }
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
    /* Периодическое обновление дисплея LCD с информацией о потоке */
    // stream_display_periodic_update();
    /* Небольшой NAK-watchdog: если давно не было завершений — попросим мягкий ресет класса.
       Он выполнится асинхронно и не блокирует EP0. */
    if((now - vnd_last_txcplt_ms) > 1500){
        extern void USBD_VND_RequestSoftReset(void);
        USBD_VND_RequestSoftReset();
        vnd_last_txcplt_ms = now; /* предотвратить лавину запросов */
        VND_LOG("WDG_SOFT_RESET_REQ");
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

    /* Ускоренный watchdog: 600мс без завершений передачи считаем зависанием */
    if(streaming && (now - vnd_last_txcplt_ms) > 600){
        VND_LOG("WDG_RESTART (no TXCPLT >600ms) reset test/pendingB");
        /* Полный мягкий сброс внутренней машины, без остановки DMA */
        stream_seq = 0; dbg_produced_seq = 0;
        /* ВАЖНО: НЕ сбрасываем cur_samples_per_frame/cur_expected_frame_size,
           чтобы сохранить зафиксированный размер кадра (например, 300 семплов)
           и избежать периодов с cur_samples=0 в STAT после быстрого рестарта. */
        vnd_ep_busy = 0; vnd_tx_ready = 1; vnd_inflight = 0; sending_channel = 0xFF; pending_B = 0; pending_B_since_ms = 0;
        /* TEST всегда считаем выполненным, даже если VND_DISABLE_TEST=0, чтобы не блокировать A/B */
        test_sent = 1; test_in_flight = 0;
        start_ack_done = 1; status_ack_pending = 0;
        vnd_last_txcplt_ms = now;
        vnd_tx_meta_head = vnd_tx_meta_tail = 0; meta_push_total = meta_pop_total = meta_empty_events = meta_overflow_events = 0; /* clear FIFO */
        /* Сбросить все пары в g_frames в FB_FILL состояние */
        for(unsigned i = 0; i < VND_PAIR_BUFFERS; i++){
            g_frames[i][0].st = FB_FILL;
            g_frames[i][1].st = FB_FILL;
        }
        pair_fill_idx = 0;
        pair_send_idx = 0;
        /* Разрешаем немедленный запуск следующей пары и готовим её прямо сейчас */
        next_seq_to_assign = stream_seq;
        vnd_next_pair_ms = now; /* не ждать периода */
        vnd_prepare_pair();
        vnd_tx_kick = 1;
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
    vnd_last_txcplt_ms = HAL_GetTick();
    VND_LOG("TXCPLT len=%u dt=%lums depth=%u push=%lu pop=%lu empty=%lu ovf=%lu", (unsigned)vnd_last_tx_len,
        (unsigned long)(HAL_GetTick() - vnd_last_tx_start_ms), (unsigned)vnd_tx_meta_depth(),
        (unsigned long)meta_push_total, (unsigned long)meta_pop_total, (unsigned long)meta_empty_events, (unsigned long)meta_overflow_events);
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
    VND_LOG("TXCPLT_CLASS is_frame=%u fl=0x%02X seq=%lu depth_now=%u (meta_have=%d)", (unsigned)eff_is_frame, (unsigned)eff_flags, (unsigned long)eff_seq, (unsigned)vnd_tx_meta_depth(), have_meta);

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
        /* пометим A как завершённый для наглядности статуса */
        g_frames[pair_send_idx][0].st = FB_FILL;
        sending_channel = 0xFF;
        /* Запускаем ожидание B ровно здесь */
        pending_B = 1; pending_B_since_ms = HAL_GetTick();
        /* Попытаемся немедленно отправить B, чтобы не ждать захода таска */
        if(!vnd_try_send_B_immediate()){
            vnd_tx_kick = 1; return;
        } else { return; }
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
                /* Снимем DMA снапшот для контроля таймаута */
                adc_stream_debug_t dbg; adc_stream_get_debug(&dbg);
                dma_snapshot_full0 = dbg.dma_full0; dma_snapshot_full1 = dbg.dma_full1;
                /* Зафиксировать размер кадра по умолчанию для полного режима (300 семплов) */
                if (full_mode) {
                    vnd_frame_samples_req = VND_FULL_DEFAULT_SAMPLES;
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
                dbg_last_forced_stat_ms = start_cmd_ms;
                vnd_tx_ready = 1; vnd_ep_busy = 0; vnd_inflight = 0;
                vnd_last_txcplt_ms = HAL_GetTick();
                /* Разрешим STAT только после первой завершённой пары */
                first_pair_done = 0; pending_status = 0; vnd_status_permit_once = 0;
                /* Индикация START */
                vnd_tx_bytes_at_start = vnd_total_tx_bytes;
                HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_SET);
                /* КРИТИЧНО: перезапускаем ADC/DMA после STOP */
                {
                    extern ADC_HandleTypeDef hadc1, hadc2;
                    extern HAL_StatusTypeDef adc_stream_start(ADC_HandleTypeDef *adc1, ADC_HandleTypeDef *adc2);
                    HAL_StatusTypeDef adc_st = adc_stream_start(&hadc1, &hadc2);
                    if(adc_st != HAL_OK){
                        VND_LOG("START_STREAM: adc_stream_start FAILED (%d)", adc_st);
                        cdc_logf("ERR ADC_START_FAIL st=%d", adc_st);
                    } else {
                        VND_LOG("START_STREAM: ADC/DMA restarted OK");
                    }
                }
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
                }
                if(!full_mode){ diag_mode_active = 1; }
                VND_LOG("START_STREAM");
                /* Не отправляем ACK-STAT через IN на старте — позволим сразу начать A/B. */
                /* Не формируем синтетическую первую пару: ждём реальные данные */
#if !VND_DISABLE_TEST
                if(!test_sent && !test_in_flight && !vnd_ep_busy){ vnd_try_send_test_from_task(); }
#endif
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
        }
        break;
        case VND_CMD_SET_FRAME_SAMPLES:
            if(len >= 3){
                uint16_t ns = (uint16_t)(data[1] | (data[2] << 8));
                if(ns > VND_MAX_SAMPLES) ns = VND_MAX_SAMPLES;
                vnd_frame_samples_req = ns;
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
            /* В полном режиме: STOP с ACK-STAT между парами; в DIAG — немедленная остановка без STAT по bulk */
            if(diag_mode_active){
                /* Мгновенно останавливаем стрим без ACK-STAT в bulk, чтобы не нарушать DIAG поток */
                stop_request = 0; pending_status = 0;
                if(streaming){ streaming = 0; VND_LOG("STOP_STREAM (diag, immediate)"); }
                diag_mode_active = 0;
                vnd_reset_buffers();
                sending_channel = 0xFF; pending_B = 0; pending_B_since_ms = 0; test_sent = 0; test_in_flight = 0; vnd_inflight = 0;
                /* Останавливаем DMA/источник данных */
                extern void adc_stream_stop(void);
                adc_stream_stop();
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
                /* STOP: сначала STAT, потом остановка — всё из таска */
                stop_request = 1; /* помечаем запрос остановки */
                pending_status = 1; /* попросим отправить STAT между парами */
                VND_LOG("STOP_STREAM request -> queue STAT");
                cdc_logf("EVT STOP_REQ t=%lu", (unsigned long)HAL_GetTick());
            }
        }
        break;
        case VND_CMD_GET_STATUS:
        {
            /* GET_STATUS всегда допускается: во время стрима — только между парами */
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
                if(hz > 100) hz = 100;
                diag_hz = hz;
                diag_period_ms = 1000 / diag_hz;
                VND_LOG("SET_BLOCK_HZ %u", diag_hz);
                cdc_logf("EVT SET_BLOCK_HZ %u", (unsigned)diag_hz);
                vnd_update_lcd_params();
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
                    /* При входе в полный режим – задать дефолт 300 сэмплов, снять lock и пересчитать период */
                    vnd_frame_samples_req = VND_FULL_DEFAULT_SAMPLES;
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
                if(profile == 1) prof_id = ADC_PROFILE_A_200HZ;
                else if(profile == 2) prof_id = ADC_PROFILE_B_DEFAULT;
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
