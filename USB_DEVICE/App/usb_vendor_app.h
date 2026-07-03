#pragma once
#include <stdint.h>
#include "main.h" /* для MAX_FRAME_SAMPLES */
#ifdef __cplusplus
extern "C" {
#endif

#define VND_CMD_START_STREAM    0x20u
#define VND_CMD_STOP_STREAM     0x21u
#define VND_CMD_GET_STATUS      0x30u
#define VND_CMD_SET_TX_ENABLE   0x33u /* 1 байт: 0=выкл, 1=вкл */
#define VND_CMD_SET_OPTIC_POWER 0x34u /* 1 байт: 0..255, мощность оптического TX */
#define VND_CMD_LED_EVENT       0x35u /* payload: u8 event, u16 duration_ms */
#define VND_CMD_HOST_RX_ACK     0x36u /* payload: u32 total host-received A/B frames */
#define VND_CMD_HOST_RX_CLEAR   0x37u /* payload: none, clear host receive heartbeat */
#define VND_CMD_GET_LCD_STATUS  0x38u /* получить состояние LCD sync-индикатора (M/S/O, число, цвет) */
#define VND_CMD_SET_OPTIC_HOLD  0x39u /* payload: u16 deciseconds; legacy u8 seconds also accepted */
#define VND_CMD_SET_DC_CONFIG   0x1Fu /* payload: v1 DC timing config, see vnd_dc_config_v1_t fields */
#define VND_CMD_GET_DC_CONFIG   0x3Au /* получить текущий DC timing config ('DCCF', 40 байт) */
#define VND_CMD_SET_LED_PATTERN 0x3Bu /* payload: u8 ws2812_pattern_t for the 20 dynamic LEDs */
#define VND_CMD_SET_DET_ADC     0x3Cu /* payload: u8 bit0=DetADC1, bit1=DetADC2 */

/* Асинхронные service-события по Vendor IN 0x83: сигнатура 'EVT1'. */
#define VND_EVT_TYPE_FW_INFO     0x00u /* payload: firmware version/build + STM32 UID96 v1 */
#define VND_EVT_TYPE_TEMP_C      0x01u /* payload: int16_t temp_c, little-endian, 1 deg C step */
#define VND_EVT_TYPE_MCU_ADC     0x02u /* payload: mcu adc/supply snapshot v1 */
#define VND_EVT_TYPE_OPTIC_STATE 0x10u /* payload: optic/tx state v1 */
#define VND_EVT_TYPE_SYNC_STATE  0x11u /* payload: sync role/topology state v1 */
#define VND_EVT_TYPE_MODE_STATE  0x12u /* payload: stream/mode state v1 */
#define VND_EVT_TYPE_ERROR_STATE 0x13u /* payload: error counters v1 */

/* Дополнение из спецификации */
#define VND_CMD_SET_FULL_MODE   0x13u /* 1 байт: 0=ROI, 1=FULL */
#define VND_CMD_SET_PROFILE     0x14u /* 1 байт profile */
#define VND_CMD_SET_ROI_US      0x15u /* 4 байта u32 (микросекунды) */
/* Новая команда: установить явный размер кадра (samples_per_frame) для ~20 FPS режимов */
#define VND_CMD_SET_FRAME_SAMPLES 0x17u /* 2 байта u16 */
#define VND_CMD_SET_SYNC_MODE   0x1Du /* payload: u8 mode (0=master, 1=slave, 2=off), host-forced */
#define VND_CMD_SET_RS485_ID   0x3Du /* payload: u8 node_id (0=unassigned, 1..31=slave id) */
/* Режим асинхронной отправки A/B и выбор каналов */
#ifndef VND_CMD_SET_ASYNC_MODE
#define VND_CMD_SET_ASYNC_MODE   0x18u /* payload: u8 mode (0=pair/strict A->B, 1=async independent) */
#endif
#ifndef VND_CMD_SET_CHMODE
#define VND_CMD_SET_CHMODE       0x19u /* payload: u8 mode (0=A-only, 1=B-only, 2=both) */
#endif

/* Режимы синхронизации (master/slave/off) */
#ifndef VND_SYNC_MODE_MASTER
#define VND_SYNC_MODE_MASTER 0u
#define VND_SYNC_MODE_SLAVE  1u
#define VND_SYNC_MODE_OFF    2u
#endif

#define VND_LED_EVENT_NONE       0u
#define VND_LED_EVENT_CHANNEL_B  1u
#define VND_LED_EVENT_CHANNEL_A  2u
#define VND_LED_EVENT_BOTH       3u
#define VND_LED_EVENT_SPLIT_IN   4u
#define VND_LED_EVENT_SPLIT_OUT  5u

/* Флаги статуса времени выполнения */
#define VND_STFLAG_STREAMING      0x0001u  /* streaming включён (после START) */
#define VND_STFLAG_DIAG_ACTIVE    0x0002u  /* активен диагностический режим */
#define VND_STFLAG_PENDING_INIT   0x0004u  /* после START ещё нет ни одного кадра A/B (ожидание инициализации) */
#define VND_STFLAG_STREAM_ACTIVE  0x0008u  /* поток действительно активен (есть переданные A/B) */
#define VND_STFLAG_TX_ENABLED     0x0010u  /* внешний TX разрешён командой/кнопкой */
#define VND_STFLAG_OPTIC_ACTIVE   0x0020u  /* оптический датчик на PD0 активен */
#define VND_STFLAG_HOST_RX_ALIVE  0x0040u  /* хост недавно подтвердил чтение потока */

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
#ifndef VND_STATUS_MAX
#define VND_STATUS_MAX      136u
#endif
#ifndef VND_LCD_STATUS_MAX
#define VND_LCD_STATUS_MAX  24u
#endif
#ifndef VND_DC_CONFIG_MAX
#define VND_DC_CONFIG_MAX   40u
#endif
/* Дефолт: 300 семплов на канал в полном режиме */
#ifndef VND_FULL_DEFAULT_SAMPLES
#define VND_FULL_DEFAULT_SAMPLES 300u
#endif
#ifndef VND_FLAGS_ADC0
#define VND_FLAGS_ADC0      0x01u
#endif
#ifndef VND_FLAGS_ADC1
#define VND_FLAGS_ADC1      0x02u
#endif
#ifndef VND_FRAME_FLAG_CRC16
#define VND_FRAME_FLAG_CRC16 0x04u
#endif
#ifndef VND_DMA_TIMEOUT_MS
#define VND_DMA_TIMEOUT_MS  300u
#endif

#define VND_LCD_SYNC_COLOR_BLACK   0u
#define VND_LCD_SYNC_COLOR_RED     1u
#define VND_LCD_SYNC_COLOR_GREEN   2u
#define VND_LCD_SYNC_COLOR_YELLOW  3u
#define VND_LCD_SYNC_COLOR_BLUE    4u
#define VND_LCD_SYNC_COLOR_CYAN    5u
#define VND_LCD_SYNC_COLOR_WHITE   6u

#define VND_LCD_SYNC_FLAG_SIGNAL_ALIVE      0x0001u
#define VND_LCD_SYNC_FLAG_SYNC_OK_VISUAL    0x0002u
#define VND_LCD_SYNC_FLAG_COLOR_LOCKED      0x0004u
#define VND_LCD_SYNC_FLAG_DISPLAY_FALLBACK  0x0008u
#define VND_LCD_SYNC_FLAG_HOST_FORCED       0x0010u

#define VND_DC_MODE_FREEZE     0u /* apply stored DC, do not learn */
#define VND_DC_MODE_WORK       1u /* normal slow tracking */
#define VND_DC_MODE_DETECT     2u /* medium tracking while host detects a tag */
#define VND_DC_MODE_BOOT_FAST  3u /* fastest tracking until host selects another mode */

#define VND_DC_CFG_FLAG_ADAPT_ENABLED 0x0001u
#define VND_DC_CFG_FLAG_AUTO_FREEZE   0x0002u
#define VND_DC_CFG_FLAG_DIRTY         0x0004u

typedef struct {
    uint8_t  raw_mode;           /* 0=master, 1=slave, 2=off */
    uint8_t  display_mode;       /* what LCD currently shows */
    uint8_t  display_value;      /* 0..31, rendered as two digits for M/S */
    uint8_t  slave_count;        /* raw rs485_slave_count_estimate */
    uint8_t  node_id;            /* raw rs485_local_node_id */
    uint8_t  display_char;       /* 'M', 'S' or 'O' */
    uint8_t  display_color_id;   /* VND_LCD_SYNC_COLOR_* */
    uint8_t  sync_signal_alive;  /* 1 if LCD logic considers sync present */
    uint8_t  sync_ok_visual;     /* 1 if LCD logic considers sync "good" */
    uint8_t  sync_color_locked;  /* 1 when green is latched */
    uint16_t display_rgb565;     /* actual LCD RGB565 color */
    uint32_t sync_age_ms;        /* 0xFFFFFFFF if no edge timestamp */
} vnd_lcd_sync_snapshot_t;

/* Статус v1 согласно USBprotocol.txt (<=64B) */
#pragma pack(push,1)
typedef struct {
    char     sig[4];            /* 'LCDS' */
    uint8_t  version;           /* 1 */
    uint8_t  raw_mode;          /* 0=master,1=slave,2=off */
    uint8_t  display_mode;      /* what LCD currently shows */
    uint8_t  display_value;     /* 0..31 */
    uint8_t  slave_count;       /* raw slave count */
    uint8_t  node_id;           /* raw local node id */
    uint8_t  display_color_id;  /* VND_LCD_SYNC_COLOR_* */
    uint8_t  display_char;      /* 'M', 'S' or 'O' */
    uint16_t display_rgb565;    /* LCD color in RGB565 */
    uint16_t flags;             /* VND_LCD_SYNC_FLAG_* */
    uint32_t sync_age_ms;       /* age of last sync edge or 0xFFFFFFFF */
    char     text[4];           /* exact LCD text prefix, e.g. "M03" or "O  " */
} vnd_lcd_status_v1_t;

typedef struct {
    char     sig[4];            /* 'DCCF' */
    uint8_t  version;           /* 1 */
    uint8_t  mode;              /* VND_DC_MODE_* effective/current mode */
    uint16_t flags;             /* VND_DC_CFG_FLAG_* */
    uint32_t work_settle_ms;    /* WORK max-error-to-midscale smooth DC slew time */
    uint32_t detect_settle_ms;  /* DETECT max-error-to-midscale smooth DC slew time */
    uint32_t fast_settle_ms;    /* BOOT_FAST max-error-to-midscale smooth DC slew time */
    uint32_t fast_duration_ms;  /* legacy wire name: last adapt_settle_ms alias, not a timer */
    uint32_t active_settle_ms;  /* currently used smooth DC slew time */
    uint32_t mode_enter_ms;     /* HAL_GetTick() when current mode was entered */
    uint32_t fast_until_ms;     /* legacy field; always 0 in continuous-speed model */
    uint32_t adapt_updates;     /* accepted DC learning updates since boot */
} vnd_dc_config_v1_t;

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
    uint8_t  reserved2;         /* диагностика пайплайна (упакованные nibble, см. реализацию) */
    uint16_t pair_idx;          /* pair_fill_idx (hi8) <<8 | pair_send_idx (lo8) */
    uint16_t last_tx_len;       /* длина последней передачи */
    uint32_t cur_stream_seq;    /* текущее значение stream_seq */
     /* packed optic state:
         [1:0]  bit0=optic_active (срабатывание фотоприёмника), bit1=tx_enable
         [7:2]  optic_hold_seconds rounded up (1..63) — legacy view; v5 has optic_hold_ds
         [15:8] optic_power (0..255) — текущее установленное значение чувствительности/мощности TX */
     uint16_t reserved3;
    /* === Расширение v2 (добавлено после 64B, хосты, ожидающие 64B, работают как прежде) === */
    uint32_t stage_alt1_ms;     /* метка HAL_GetTick() при последнем SET_INTERFACE alt=1 */
    uint32_t stage_start_ms;    /* метка START_STREAM (start_cmd_ms) */
    uint32_t stage_first_frame_ms; /* метка первого успешного TXCPLT рабочего кадра (A или B) */
    /* === Расширение v3 (ДИАГНОСТИКА: счётчики нулевых буферов для выявления проблем ADC) === */
    uint32_t ch_zero_buffers_A; /* количество буферов ADC1 с полностью нулевым содержимым */
    uint32_t ch_zero_buffers_B; /* количество буферов ADC2 с полностью нулевым содержимым */
    /* === Расширение v4 (ВРЕМЕННЫЕ МЕТКИ ADC для хоста) === */
    uint32_t now_ms;           /* текущее HAL_GetTick() при формировании STAT */
    uint32_t last_full0_ms;    /* HAL_GetTick() последнего полного DMA кадра ADC1 */
    uint32_t last_full1_ms;    /* HAL_GetTick() последнего полного DMA кадра ADC2 */
    /* === Расширение v5 (RPI optic/sync/LED control status) === */
    uint16_t optic_hold_ds;    /* hold time in 0.1 s units; default 30 = 3.0 s */
    uint8_t  led_pattern;      /* current host-selectable dynamic LED pattern */
    uint8_t  sync_local_status;/* local RS485 status byte: id/selector[4:0], optic bit5, DetADC bits6..7 */
    uint32_t sync_seen_mask;   /* bit0=node1 ... bit30=node31 present in sync_status_bytes */
    uint8_t  sync_node_count;  /* number of active status bytes in sync_seen_mask */
    uint8_t  sync_status_bytes[31]; /* status byte by node id: index 0=node1 ... index30=node31 */
} vnd_status_v1_t; /* 64B(v1)+12B(v2)+8B(v3)+12B(v4)+40B(v5)=136 bytes */
#pragma pack(pop)
_Static_assert(sizeof(vnd_lcd_status_v1_t) == 24, "vnd_lcd_status_v1_t must be 24 bytes");
_Static_assert(sizeof(vnd_dc_config_v1_t) == 40, "vnd_dc_config_v1_t must be 40 bytes");
_Static_assert(sizeof(vnd_status_v1_t) == 136, "vnd_status_v1_t must be 136 bytes (v5 extended)");

/* Публичные переменные */
extern volatile uint8_t vnd_tx_kick; /* Флаг пробуждения таска после события */

/* Публичные функции */
void Vendor_Stream_Task(void);
void Vendor_ChangeEvent_Task(void);
void Vendor_Control_Task(void);
void Vendor_Status_Task(void);
void Vendor_Maintenance_Task(void);
void usb_vendor_periodic_tick(void); /* тик от TIM6 */
uint8_t vnd_is_streaming(void);
uint8_t vnd_is_tx_enabled(void);
/* Построить статус в буфере (возвращает длину или 0 при ошибке) */
uint16_t vnd_build_status(uint8_t *dst, uint16_t max_len);
uint16_t vnd_build_lcd_status(uint8_t *dst, uint16_t max_len);
uint16_t vnd_build_dc_config(uint8_t *dst, uint16_t max_len);
void vnd_get_lcd_sync_snapshot(vnd_lcd_sync_snapshot_t *out);
/* Диагностическая одноразовая отправка 64B шаблона (оставляем) */
void vnd_diag_send64_once(void);
/* ISR уведомление о появлении новых кадров (override слабого hook из adc_stream) */
void adc_stream_on_new_frames(uint32_t frames_added);
/* Статистика передачи */
uint64_t vnd_get_total_tx_bytes(void);
uint64_t vnd_get_total_tx_samples(void);
uint32_t vnd_get_last_txcplt_ms(void);
uint32_t vnd_get_last_frame_txcplt_ms(void);
uint32_t vnd_get_last_host_rx_ack_ms(void);
uint32_t vnd_get_last_error(void);
void vnd_log_usb_close_snapshot(const char *reason);
/* Получить частоту буферов профиля (Fs блоков/с): прокси к adc_stream */
uint16_t adc_stream_get_buf_rate(void);

/* Сервис: полный сброс пайплайна Vendor (используется классом по EP0 и SET_INTERFACE) */
void vnd_pipeline_stop_reset(int deep);

/* Получить текущие min/max АЦП значения последних отправленных кадров */
void vnd_get_adc_minmax(int16_t *adc0_min, int16_t *adc0_max, 
                        int16_t *adc1_min, int16_t *adc1_max);

/* Тестовый генератор (пилообразный сигнал) — объявление доступно и для main.c */
void vnd_generate_test_sawtooth(void);

/* FPS и статистика производительности */
void vnd_report_fps_stats(void);
void vnd_print_perf_stats(void);
void Vendor_ChangeEvent_DiagPrint(void);
void Vendor_StreamDiagPrint(void);

/* Сигнал о фронте синхронизации (slave) для индикации S на LCD */
void vnd_sync_on_edge(void);
void vnd_request_adc_restart_from_isr(void);
void vnd_sync_set_mode_auto(uint8_t mode);
void vnd_sync_apply_mode_forced(uint8_t mode);
void vnd_sync_release_host_forced(void);
uint8_t vnd_sync_is_mode_host_forced(void);

/* DC (AVG_ROI) persistence: counters for LCD/diagnostics */
extern volatile uint32_t vnd_dc_save_ok_count;
extern volatile uint32_t vnd_dc_save_fail_count;
extern volatile uint32_t vnd_dc_save_last_ms;
extern volatile uint8_t  vnd_dc_save_last_result; /* 0=none, 1=ok, 2=fail */

/* DC Adaptation control (can be frozen by host during signal detection) */
extern volatile uint8_t  vnd_dc_adapt_enabled; /* 1=active (learning), 0=freeze (keep current values) */

/* Auto-freeze when signal swing is too large for reliable DC learning. */
extern volatile uint8_t  vnd_dc_auto_freeze; /* 1=auto-freeze by amplitude gate */

/* Monotonic counter stored in Flash blob (loaded on boot, incremented on each save attempt). */
extern volatile uint32_t vnd_dc_write_counter_public;

/* DC (AVG_ROI) live state for LCD progress indicator */
extern volatile uint8_t  vnd_dc_dirty_public;      /* 0/1: DC changed and pending save */
extern volatile uint32_t vnd_dc_dirty_since_ms;    /* HAL_GetTick() when became dirty */
extern volatile uint32_t vnd_dc_save_period_ms;    /* save period used by firmware */

/* DC save diagnostics */
extern volatile uint32_t vnd_dc_save_last_err;          /* HAL_FLASH_GetError() (if available) */
extern volatile uint32_t vnd_dc_save_last_sector_error; /* sector_error from HAL_FLASHEx_Erase */
extern volatile uint32_t vnd_dc_save_last_bank;         /* FLASH_BANK_1/2 */
extern volatile uint32_t vnd_dc_save_last_sector;       /* FLASH_SECTOR_x */

/* DC load diagnostics for LCD/debug
    flags: bit0=loaded OK, bit1=erase_pending (journal tail corrupted or sector full) */
extern volatile uint8_t  vnd_dc_load_flags_public;
extern volatile uint16_t vnd_dc_loaded_crc16_public;
extern volatile uint32_t vnd_dc_flash_next_off_public;
void vnd_dc_note_flash_fault(uint32_t fault_addr, uint32_t cfsr);
void vnd_dc_request_save_to_flash(void);

/* Sync master/slave status for LCD */
extern volatile uint8_t  vnd_sync_mode_public; /* 0=master,1=slave,2=off */
extern volatile uint8_t  vnd_sync_ok_public;   /* 1=sync pulses present */

#ifdef __cplusplus
}
#endif
