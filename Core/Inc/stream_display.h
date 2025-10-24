#ifndef __STREAM_DISPLAY_H
#define __STREAM_DISPLAY_H

#include <stdint.h>

/* Структура для отображения информации о потоке на LCD */
typedef struct {
    uint16_t frequency_hz;      /* Частота в Гц (200 или 300) */
    uint16_t start_sample;      /* Номер начального сэмпла */
    uint16_t sample_count;      /* Количество передаваемых сэмплов */
    uint32_t frames_sent;       /* Количество отправленных кадров */
    uint8_t is_streaming;       /* Флаг активного потока (1 = идёт передача) */
} stream_info_t;

/* Инициализация дисплея потока */
void stream_display_init(void);

/* Обновить информацию на дисплее */
void stream_display_update(const stream_info_t *info);

/* Выполнить периодическое обновление (вызывать из main loop или timer) */
void stream_display_periodic_update(void);

/* Очистить дисплей */
void stream_display_clear(void);

/* Установить счётчик отправленных кадров */
void stream_display_set_frames_sent(uint32_t frames);

#endif // __STREAM_DISPLAY_H
