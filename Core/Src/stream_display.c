#include "stream_display.h"
#include "lcd.h"
#include <stdio.h>
#include <string.h>

/* Глобальная структура информации о потоке */
static stream_info_t g_stream_info = {0};
static uint32_t g_last_update_ms = 0;

/* Период обновления дисплея (мс) */
#define DISPLAY_UPDATE_PERIOD_MS 500

extern uint32_t HAL_GetTick(void);
/* Счётчики отправленных кадров по каналам (из usb_vendor_app.c) */
extern volatile uint32_t dbg_sent_ch0_total;
extern volatile uint32_t dbg_sent_ch1_total;

void stream_display_init(void)
{
    if (!lcd_ready) {
        LCD_Init();
    }
    LCD_Clear(BLACK);
    stream_display_clear();
}

void stream_display_update(const stream_info_t *info)
{
    if (!info || !lcd_ready) {
        return;
    }
    
    /* Копируем информацию */
    memcpy(&g_stream_info, info, sizeof(stream_info_t));
    
    /* Очищаем область дисплея */
    LCD_Clear(BLACK);
    
    /* Формируем и выводим строки */
    char line[40];
    
    /* Строка 1: Статус */
    if (g_stream_info.is_streaming) {
        LCD_ShowString_Size(0, 0, "STREAMING", 2, GREEN, BLACK);
    } else {
        LCD_ShowString_Size(0, 0, "STOPPED", 2, RED, BLACK);
    }
    
    /* Строка 2: Частота */
    snprintf(line, sizeof(line), "Freq: %u Hz", g_stream_info.frequency_hz);
    LCD_ShowString_Size(0, 16, line, 1, YELLOW, BLACK);
    
    /* Строка 3: Количество сэмплов */
    snprintf(line, sizeof(line), "Samples: %u", g_stream_info.sample_count);
    LCD_ShowString_Size(0, 26, line, 1, CYAN, BLACK);
    
    /* Строка 4: Номер сэмпла */
    snprintf(line, sizeof(line), "Start: %u", g_stream_info.start_sample);
    LCD_ShowString_Size(0, 36, line, 1, CYAN, BLACK);
    
    /* Строка 5: Количество отправленных кадров */
    snprintf(line, sizeof(line), "Frames: %lu", g_stream_info.frames_sent);
    LCD_ShowString_Size(0, 46, line, 1, GBLUE, BLACK);
    
    /* Строка 6: Информация о профиле */
    const char *profile_str = (g_stream_info.frequency_hz == 200) ? "Profile1" : "Profile2";
    LCD_ShowString_Size(0, 56, profile_str, 1, MAGENTA, BLACK);
    
    g_last_update_ms = HAL_GetTick();
}

void stream_display_periodic_update(void)
{
    uint32_t now_ms = HAL_GetTick();
    
    /* Обновляем счётчик кадров из глобальных переменных */
    g_stream_info.frames_sent = dbg_sent_ch0_total + dbg_sent_ch1_total;
    
    /* Обновляем дисплей периодически */
    if ((now_ms - g_last_update_ms) >= DISPLAY_UPDATE_PERIOD_MS) {
        stream_display_update(&g_stream_info);
    }
}

void stream_display_clear(void)
{
    if (!lcd_ready) {
        return;
    }
    
    LCD_Clear(BLACK);
    memset(&g_stream_info, 0, sizeof(g_stream_info));
    g_last_update_ms = 0;
}

void stream_display_set_frames_sent(uint32_t frames)
{
    g_stream_info.frames_sent = frames;
}

