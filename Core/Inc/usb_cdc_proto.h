#ifndef USB_CDC_PROTO_H
#define USB_CDC_PROTO_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// Версия протокола
#define USB_PROTO_VER 0x01

// Команды CDC (OUT -> STM32)
#define CMD_PING            0x01
#define CMD_SET_WINDOWS     0x10  // start0,len0,start1,len1 (используем только start0,len0 как окно)
#define CMD_SET_BLOCK_RATE  0x11  // частота кадров (Гц) либо 0 = внешний тик (не используется сейчас)
#define CMD_START_STREAM    0x20
#define CMD_STOP_STREAM     0x21
#define CMD_GET_STATUS      0x30
#define CMD_SET_FULL_MODE   0x13  // 0=оконный, 1=full (полный буфер)
// Новые команды Vendor протокола
#define CMD_SET_PROFILE      0x14  // param: profile id (0=B,1=C)
#define CMD_SET_ROI_US       0x15  // payload: offset_us(uint32), length_us(uint32)

// Ответы (IN <- STM32)
#define RSP_ACK             0x80
#define RSP_NACK            0x81

// Магия кадра данных (2 байта перед заголовком)
#define FRAME_MAGIC0 0x5A
#define FRAME_MAGIC1 0xA5

// Максимум каналов в кадре (ограничение устройства)
#define MAX_CHANNELS 2
// Совместимость со старым кодом
#ifndef MAX_WINDOWS
#define MAX_WINDOWS MAX_CHANNELS
#endif

// Специальная частота блоков: максимально быстро (без синхронизации)
#define USB_BLOCK_RATE_FAST 0xFFFFu

// УПРОЩЕННЫЙ ВАРИАНТ С УДАЛЕНИЕМ ПОНЯТИЯ SEGMENT / SEG_LEN
// (только окна и полный кадр 1375 семплов)

// Минимальный/максимальный размер окна (окно = samples per channel в кадре)
#define USB_WINDOW_LEN_MIN 1
#define USB_WINDOW_LEN_MAX 1375

// Флаги заголовка по спецификации v1:
// bit0=ADC0 frame, bit1=ADC1 frame, bit2=CRC present, bit7=TEST frame
#define VFLAG_ADC0        0x01
#define VFLAG_ADC1        0x02
#define VFLAG_CRC         0x04
#define VFLAG_TEST        0x80

// Обновлённая структура конфигурации потока (минимально необходимая)
typedef struct {
    uint8_t  streaming;          // 0/1
    uint8_t  full_mode;          // 1=FULL, 0=ROI1
    uint8_t  profile_id;         // активный профиль (0=B,1=C)
    uint32_t roi_offset_us;      // начало окна в мкс
    uint32_t roi_length_us;      // длина окна в мкс
    uint32_t seq_adc[2];         // отдельные счётчики кадров per ADC
} usb_stream_cfg_t;

// 32-байтный заголовок кадра (per ADC)
#pragma pack(push,1)
typedef struct {
    uint16_t magic;       // 0xA55A
    uint8_t  version;     // =1
    uint8_t  flags;       // бит0=adc_id, бит1=mode ROI, бит2=CRC (0)
    uint32_t seq;         // sequence per ADC
    uint32_t timestamp;   // HAL_GetTick() или другой счётчик
    uint16_t total_samples; // фактически переданных выборок (ROI или FULL N)
    uint16_t zone_count;    // 0=FULL,1=ROI1
    uint32_t zone1_offset;  // смещение в выборках (FULL=0)
    uint32_t zone1_length;  // длина в выборках (FULL=total_samples)
    uint32_t reserved;      // =0 (будущее)
    uint16_t reserved2;     // =0
    uint16_t crc16;         // 0 пока отключено
} vendor_frame_hdr_t;
#pragma pack(pop)

// Публичные API модуля
void usb_stream_init(void);
void usb_stream_poll(void);
uint8_t usb_stream_try_send_frame(void); // возвращает 1 если кадр отправлен
usb_stream_cfg_t* usb_stream_cfg(void);

// Хук для стека USB: передать принятые из CDC байты в парсер протокола
void usb_stream_on_rx_bytes(const uint8_t* data, size_t len);

// Внешний тик 200 Гц (например, от TIM6) — запрос на отправку одного кадра.
// Режим активен, если block_rate_hz == 0.
void usb_stream_on_frame_tick(void);

// Add prototype for ACK with parameter
void stream_send_ack_param(uint8_t cmd, uint8_t param);

// Объявление (используются в реализации)
void usb_stream_send_status(void);
void usb_stream_send_test_frame(void);

// Совместимость (устаревшие имена)
#define usb_cdc_init            usb_stream_init
#define usb_cdc_poll            usb_stream_poll
#define usb_cdc_try_send_frame  usb_stream_try_send_frame
#define usb_cdc_cfg             usb_stream_cfg
#define usb_cdc_on_rx_bytes     usb_stream_on_rx_bytes
#define usb_cdc_on_frame_tick   usb_stream_on_frame_tick
#define cdc_send_ack_param      stream_send_ack_param

#ifdef __cplusplus
}
#endif

#endif // USB_CDC_PROTO_H

// Дополнения STATUS: теперь включает backlog(cur,max), drops, bytes_per_frame, roi_samples.
// ROI принудительно округляется вниз до кратности 32 выборок для выравнивания USB.
// CRC16 всегда добавляется (VF_CRC_PRESENT установлен), полином 0x1021 init=0xFFFF.