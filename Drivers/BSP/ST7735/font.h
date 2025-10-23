// font.h
#ifndef FONT_H
#define FONT_H

#include <stdint.h>

// Размеры шрифтов
#define FONT_1206_WIDTH  6
#define FONT_1206_HEIGHT 12
#define FONT_1608_WIDTH  8
#define FONT_1608_HEIGHT 16
#define FONT_2010_WIDTH  10
#define FONT_2010_HEIGHT 20
#define FONT_2412_WIDTH  12
#define FONT_2412_HEIGHT 24

// Количество символов в таблицах (ASCII 32-126)
#define ASCII_TABLE_SIZE 95

// Объявления массивов шрифтов
extern const uint8_t asc2_1206[159][12];
extern const uint8_t asc2_1608[159][16];
extern const uint8_t asc2_2010[159][20];
extern const uint8_t asc2_2412[95][36];

// Типы шрифтов
typedef enum {
    FONT_SIZE_1206 = 0,
    FONT_SIZE_1608,
    FONT_SIZE_2010,
    FONT_SIZE_2412
} font_size_t;

// Структура для описания шрифта
typedef struct {
    uint8_t width;
    uint8_t height;
    uint8_t bytes_per_char;
    const uint8_t* data;
} font_info_t;

// Функции для работы со шрифтами
font_info_t get_font_info(font_size_t font_size);
uint8_t get_char_width(font_size_t font_size);
uint8_t get_char_height(font_size_t font_size);

// Функция для получения увеличенного размера шрифта (для звездочки)
font_size_t get_larger_font_size(font_size_t current_size);

#endif // FONT_H