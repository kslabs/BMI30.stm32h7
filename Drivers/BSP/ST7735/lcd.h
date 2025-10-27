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
void LCD_Init(void);
void LCD_Clear(uint16_t color);
void LCD_DrawPoint(uint16_t x, uint16_t y, uint16_t color);
void LCD_ShowChar(uint16_t x, uint16_t y, uint8_t num, uint8_t size, uint16_t color, uint16_t back_color);
void LCD_ShowString(uint16_t x, uint16_t y, const char *p, uint16_t color);
void LCD_ShowString_Size(uint16_t x, uint16_t y, const char *p, uint8_t size, uint16_t color, uint16_t back_color);
void LCD_FillRect(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t color);

extern uint8_t lcd_ready; // флаг готовности (устанавливается в LCD_Init)

#endif