#ifndef __LCD_H
#define __LCD_H

#include "main.h"

// Цвета RGB565
#define WHITE          0xFFFF
#define BLACK          0x0000
#define BLUE           0x001F
#define BRED           0xF81F
#define GRED           0xFFE0
#define GBLUE          0x07FF
#define RED            0xF800
#define MAGENTA        0xF81F
#define GREEN          0x07E0
#define CYAN           0x7FFF
#define YELLOW         0xFFE0
#define BROWN          0xBC40
#define BRRED          0xFC07
#define GRAY           0x8430

// Дополнительные цвета для радуги
#define ORANGE         0xFD20  // Оранжевый
#define VIOLET         0x801F  // Фиолетовый

// Специальное значение для прозрачного фона
#define TRANSPARENT    0xFFFF  // Используем WHITE как маркер прозрачности

// Размеры экрана
#define LCD_W 160
#define LCD_H 80
#define LCD_X_SHIFT 1
#define LCD_Y_SHIFT 26
// Добавлены совместимые алиасы имен, используемых в main.c
#ifndef LCD_WIDTH
#define LCD_WIDTH LCD_W
#endif
#ifndef LCD_HEIGHT
#define LCD_HEIGHT LCD_H
#endif

// Прототипы функций
typedef struct {
    uint32_t service_calls;
    uint32_t budget_skips;
    uint32_t render_changed_rows;
    uint32_t row_jobs;
    uint32_t rows_completed;
    uint32_t dma_starts;
    uint32_t dma_start_failures;
    uint32_t dma_timeouts;
    uint32_t render_max_cycles;
    uint32_t service_max_cycles;
    uint32_t dma_age_max_ms;
    uint32_t spi_state;
    uint32_t spi_error;
    uint32_t dma_error;
    uint32_t phase;
    uint32_t row;
    uint32_t waiting;
    uint32_t fault;
    uint32_t scene_render_max_cycles;
} lcd_background_diag_t;
extern volatile lcd_background_diag_t g_lcd_background_diag;
/* Foreground only, called after acquisition/control work. At most one small
   DMA phase starts; may_start=0 still permits completion housekeeping. */
void LCD_BackgroundService(uint32_t now_ms, uint8_t may_start);

void LCD_Init(void);
/* True only after a valid first frame, DISPON completion and500ms visible hold. */
uint8_t LCD_BootScreenReady(uint32_t now_ms);
void LCD_Clear(uint16_t color);
void LCD_DrawPoint(uint16_t x, uint16_t y, uint16_t color);
void LCD_ShowChar(uint16_t x, uint16_t y, uint8_t num, uint8_t size, uint16_t color, uint16_t back_color);
void LCD_ShowString(uint16_t x, uint16_t y, const char *p, uint16_t color);
void LCD_ShowString_Size(uint16_t x, uint16_t y, const char *p, uint8_t size, uint16_t color, uint16_t back_color);
void LCD_FillRect(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t color);

extern uint8_t lcd_ready; // флаг готовности (устанавливается в LCD_Init)

#endif