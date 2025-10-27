#include "lcd.h"
// filepath: c:\Users\TEST\Documents\Work\BMI20\STM32\Proj_BMI30_H723.01\BMI30.stm32h7\Drivers\BSP\ST7735\lcd.c
#include <stdint.h>
#include "st7735.h"
#include "font.h" // Мы создадим этот файл следующим шагом
#include <string.h> // Подключаем для использования функции strlen

uint8_t lcd_ready = 0; // глобальный флаг готовности LCD (extern в lcd.h)

/**
 * @brief Инициализация LCD.
 * @note Эта функция является "оберткой" и просто вызывает
 *       низкоуровневую функцию инициализации из драйвера st7735.
 */
void LCD_Init(void)
{
	ST7735_Init();
    lcd_ready = 1; // сигнал готовности LCD
}

/**
 * @brief Очищает весь экран (заливает одним цветом).
 * @note Также является "оберткой" для низкоуровневой функции.
 * @param color - 16-битный цвет для заливки в формате RGB565.
 */
void LCD_Clear(uint16_t color)
{
	ST7735_Fill(color);
}

/**
 * @brief Рисует один пиксель на экране.
 * @note Эта функция не очень эффективна для массовой отрисовки,
 *       так как каждый раз устанавливает "окно" размером 1x1 пиксель.
 *       Для заливки областей лучше использовать ST7735_Fill.
 * @param x, y - Координаты пикселя.
 * @param color - Цвет пикселя.
 */
void LCD_DrawPoint(uint16_t x,uint16_t y,uint16_t color)
{
	LCD_CS_LOW();
	ST7735_AddrSet(x,y,x,y);
	ST7735_WriteData(color>>8);
	ST7735_WriteData(color);
	LCD_CS_HIGH();
}

/**
 * @brief Выводит один символ на экран.
 * @note Точная копия из примера WeAct Studio с поддержкой прозрачного фона.
 * @param x, y - Координаты верхнего левого угла символа.
 * @param num - ASCII-код символа для отображения.
 * @param size - Размер шрифта (12 или 16).
 * @param color - Цвет самого символа.
 * @param back_color - Цвет фона (используйте TRANSPARENT для прозрачного фона).
 */
void LCD_ShowChar(uint16_t x, uint16_t y, uint8_t num, uint8_t size, uint16_t color, uint16_t back_color)
{
    uint8_t temp;
    uint8_t pos, t;
    uint16_t y0 = y;
    uint8_t csize = (size / 8 + ((size % 8) ? 1 : 0)) * (size / 2);

    num = num - ' ';
    if (num < 0 || num >= 159) return; // Проверка диапазона
    for (pos = 0; pos < csize; pos++)
    {
        if (size == 12)
            temp = asc2_1206[num][pos];
        else if (size == 16)
            temp = asc2_1608[num][pos];
        else
            return;
        for (t = 0; t < 8; t++)
        {
            if (temp & 0x80)
                LCD_DrawPoint(x, y, color);
            else if (back_color != TRANSPARENT)  // Рисуем фон только если он НЕ прозрачный
                LCD_DrawPoint(x, y, back_color);
            temp <<= 1;
            y++;
            if (y >= LCD_H) return;
            if ((y - y0) == size)
            {
                y = y0;
                x++;
                if (x >= LCD_W) return;
                break;
            }
        }
    }
}

/**
 * @brief Выводит строку символов с прозрачным фоном.
 * @note Обновленная реализация с поддержкой прозрачности.
 * @param x, y - Начальные координаты для вывода строки.
 * @param p - Указатель на строку (заканчивается нулевым символом '\0').
 * @param color - Цвет текста.
 */
void LCD_ShowString(uint16_t x, uint16_t y, const char *p, uint16_t color)
{
    while (*p != '\0')
    {
        if (x > (LCD_W - 8))
        {
            x = 0;
            y += 16;
        }
        if (y > (LCD_H - 16))
        {
            y = 0;
            x = 0;
        }
        LCD_ShowChar(x, y, *p, 16, color, TRANSPARENT);  // Используем прозрачный фон
        x += 8;
        p++;
    }
}

/**
 * @brief Выводит строку символов с выбором размера шрифта.
 * @param x, y - Начальные координаты для вывода строки.
 * @param p - Указатель на строку (заканчивается нулевым символом '\0').
 * @param size - Размер шрифта (12 или 16).
 * @param color - Цвет текста.
 * @param back_color - Цвет фона.
 */
void LCD_ShowString_Size(uint16_t x, uint16_t y, const char *p, uint8_t size, uint16_t color, uint16_t back_color)
{
    uint8_t char_width = (size == 12) ? 6 : 8;  // Ширина символа зависит от размера
    uint8_t char_height = size;                  // Высота символа равна размеру
    
    while (*p != '\0')
    {
        // Проверяем, помещается ли символ на текущей строке
        if (x > (LCD_W - char_width))
        {
            x = 0;
            y += char_height;
        }
        
        // Проверяем, помещается ли строка на экране
        if (y > (LCD_H - char_height))
        {
            y = 0;
            x = 0;
        }
        
        LCD_ShowChar(x, y, *p, size, color, back_color);
        x += char_width;
        p++;
    }
}

/*
Примечание: Реализации функций рисования фигур (LCD_DrawLine, LCD_DrawRectangle, LCD_DrawCircle)
здесь намеренно опущены для простоты. Они нам сейчас не нужны для базового тестового вывода.
Если они понадобятся в будущем, мы сможем их легко добавить, так как они уже объявлены в lcd.h.
*/

/**
 * @brief Заливает прямоугольную область одним цветом.
 * @note Эта функция является высокоуровневой оберткой для ST7735_Fill.
 * @param x, y - Координаты верхнего левого угла.
 * @param w, h - Ширина и высота прямоугольника.
 * @param color - Цвет для заливки.
 */
void LCD_FillRect(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t color)
{
    uint32_t i;
    uint32_t total_pixels = w * h;

    // Проверяем границы
    if (x >= LCD_W || y >= LCD_H) return;
    if (x + w > LCD_W) w = LCD_W - x;
    if (y + h > LCD_H) h = LCD_H - y;

    // Устанавливаем окно для рисования
    ST7735_AddrSet(x, y, x + w - 1, y + h - 1);
    
    // Теперь отправляем данные пикселей
    LCD_CS_LOW();
    LCD_RS_HIGH(); // Режим данных
    
    for (i = 0; i < total_pixels; i++)
    {
        ST7735_SPI_Send(color >> 8);
        ST7735_SPI_Send(color);
    }
    
    LCD_CS_HIGH();
}