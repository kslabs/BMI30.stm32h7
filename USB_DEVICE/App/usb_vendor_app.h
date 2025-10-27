#pragma once
#include <stdint.h>
#include "main.h" /* для MAX_FRAME_SAMPLES */
#ifdef __cplusplus
extern "C" {
#endif

#define VND_CMD_START_STREAM    0x20u
#define VND_CMD_STOP_STREAM     0x21u
#define VND_CMD_GET_STATUS      0x30u
/* Дополнение из спецификации */
#define VND_CMD_SET_FULL_MODE   0x13u /* 1 байт: 0=ROI, 1=FULL */
#define VND_CMD_SET_PROFILE     0x14u /* 1 байт profile */
#define VND_CMD_SET_ROI_US      0x15u /* 4 байта u32 (микросекунды) */
/* Новая команда: установить явный размер кадра (samples_per_frame) для ~20 FPS режимов */
#define VND_CMD_SET_FRAME_SAMPLES 0x17u /* 2 байта u16 */

/* Флаги статуса времени выполнения */
#define VND_STFLAG_STREAMING    0x0001u
#define VND_STFLAG_DIAG_ACTIVE  0x0002u

/* Общие константы формата кадров/параметров (централизовано) */
#ifndef VND_MAX_SAMPLES
#define VND_MAX_SAMPLES     (MAX_FRAME_SAMPLES)
#endif
#ifndef VND_FRAME_HDR_SIZE
#define VND_FRAME_HDR_SIZE  32u
#endif
#ifndef VND_FRAME_MAX_SIZE
#define VND_FRAME_MAX_SIZE  (VND_FRAME_HDR_SIZE + 2u*VND_MAX_SAMPLES)
#endif
#ifndef VND_FLAGS_ADC0
#define VND_FLAGS_ADC0      0x01u
#endif
#ifndef VND_FLAGS_ADC1
#define VND_FLAGS_ADC1      0x02u
#endif
#ifndef VND_DMA_TIMEOUT_MS
#define VND_DMA_TIMEOUT_MS  300u
#endif

/* Статус v1 согласно USBprotocol.txt (<=64B) */
#pragma pack(push,1)
typedef struct {
    char     sig[4];            /* 'STAT' */
    uint8_t  version;           /* 1 */
    uint8_t  reserved0;         /* 0 */
    uint16_t cur_samples;       /* зафиксированный cur_samples_per_frame */
    uint16_t frame_bytes;       /* 32 + 2*cur_samples */
    uint16_t test_frames;       /* сколько тестовых кадров отправлено */
    uint32_t produced_seq;      /* текущий seq (созданных пар) */
    uint32_t sent0;             /* отправлено ADC0 кадров */
    uint32_t sent1;             /* отправлено ADC1 кадров */
    uint32_t dbg_tx_cplt;       /* завершений (USBD_VND_TxCplt) */
    uint32_t dbg_partial_frame_abort; /* отфильтровано частичных */
    uint32_t dbg_size_mismatch; /* несовпадений размеров */
    uint32_t dma_done0;         /* DMA full complete ADC0 */
    uint32_t dma_done1;         /* DMA full complete ADC1 */
    uint32_t frame_wr_seq;      /* внутренняя позиция записи */
    uint16_t flags_runtime;     /* runtime флаги */
    /* Новые диагностические поля (итоговый размер структуры = 64B) */
        /* flags2 биты (соответствует реализации):
             0  = EP IN busy (локально или LL)
             1  = tx_ready
             2  = pending_B (ждём B после A)
             3  = test_in_flight
             4  = start_ack_done
             5  = start_stat_inflight
             6  = start_stat_planned (устар.)
             7  = pending_status (STAT отложен)
             8  = simple_tx_mode
             9  = diag_mode_active
             10 = first_pair_done (завершена первая полноценная пара)
             11 = A READY, 12 = B READY
             13 = A SENDING, 14 = B SENDING
             15 = A_fill READY (готов A в буфере подготовки)
         */
        uint16_t flags2;
    uint8_t  sending_ch;        /* 0=A,1=B,0xFF=нет */
    uint8_t  reserved2;         /* выравнивание */
    uint16_t pair_idx;          /* pair_fill_idx (hi8) <<8 | pair_send_idx (lo8) */
    uint16_t last_tx_len;       /* длина последней передачи */
    uint32_t cur_stream_seq;    /* текущее значение stream_seq */
    uint16_t reserved3;         /* паддинг до 64 байт */
} vnd_status_v1_t; /* 64 байта */
#pragma pack(pop)
_Static_assert(sizeof(vnd_status_v1_t) == 64, "vnd_status_v1_t must be 64 bytes");

/* Публичные функции */
void Vendor_Stream_Task(void);
void usb_vendor_periodic_tick(void); /* тик от TIM6 */
uint8_t vnd_is_streaming(void);
/* Построить статус в буфере (возвращает длину или 0 при ошибке) */
uint16_t vnd_build_status(uint8_t *dst, uint16_t max_len);
/* Диагностическая одноразовая отправка 64B шаблона (оставляем) */
void vnd_diag_send64_once(void);
/* ISR уведомление о появлении новых кадров (override слабого hook из adc_stream) */
void adc_stream_on_new_frames(uint32_t frames_added);
/* Статистика передачи */
uint64_t vnd_get_total_tx_bytes(void);
uint64_t vnd_get_total_tx_samples(void);
uint32_t vnd_get_last_txcplt_ms(void);
/* Получить частоту буферов профиля (Fs блоков/с): прокси к adc_stream */
uint16_t adc_stream_get_buf_rate(void);

/* Сервис: полный сброс пайплайна Vendor (используется классом по EP0 и SET_INTERFACE) */
void vnd_pipeline_stop_reset(int deep);

/* Получить текущие min/max АЦП значения последних отправленных кадров */
void vnd_get_adc_minmax(int16_t *adc0_min, int16_t *adc0_max, 
                        int16_t *adc1_min, int16_t *adc1_max);

#ifdef __cplusplus
}
#endif