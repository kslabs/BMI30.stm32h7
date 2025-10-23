#include "st7735.h"
#include "main.h" // Подключаем для доступа к hspi4 и HAL_Delay
#include "lcd.h"  // Добавляем для LCD_W, LCD_H и смещений

// Объявляем, что будем использовать hspi4, который определен в main.c
extern SPI_HandleTypeDef hspi4;

// --- Приватные функции для отправки данных по SPI ---

void ST7735_SPI_Send(uint8_t data)
{
    // Упрощенная версия без проверки флагов - HAL_SPI_Transmit уже содержит все необходимые проверки
    HAL_SPI_Transmit(&hspi4, &data, 1, 100);
}

void ST7735_WriteCommand(uint8_t cmd)
{
    LCD_CS_LOW();   // Активируем CS
    LCD_RS_LOW();   // Режим команды
    ST7735_SPI_Send(cmd);
    LCD_CS_HIGH();  // Деактивируем CS
}

void ST7735_WriteData(uint8_t data)
{
    LCD_CS_LOW();   // Активируем CS
    LCD_RS_HIGH();  // Режим данных
    ST7735_SPI_Send(data);
    LCD_CS_HIGH();  // Деактивируем CS
}

// --- Функции, адаптированные из примера WeAct Studio ---

/**
 * @brief Инициализирует дисплей ST7735.
 * @note Последовательность команд взята из рабочего примера WeAct.
 */
void ST7735_Init(void)
{
    LCD_CS_LOW();
    LCD_RST_LOW();
    HAL_Delay(100);
    LCD_RST_HIGH();
    HAL_Delay(100);

    ST7735_WriteCommand(ST7735_SWRESET); // 1: Software reset
    HAL_Delay(150);

    ST7735_WriteCommand(ST7735_SLPOUT);  // 2: Out of sleep mode
    HAL_Delay(255);

    // 3: Frame rate control
    ST7735_WriteCommand(ST7735_FRMCTR1);
    ST7735_WriteData(0x01);
    ST7735_WriteData(0x2C);
    ST7735_WriteData(0x2D);

    ST7735_WriteCommand(ST7735_FRMCTR2);
    ST7735_WriteData(0x01);
    ST7735_WriteData(0x2C);
    ST7735_WriteData(0x2D);

    ST7735_WriteCommand(ST7735_FRMCTR3);
    ST7735_WriteData(0x01);
    ST7735_WriteData(0x2C);
    ST7735_WriteData(0x2D);
    ST7735_WriteData(0x01);
    ST7735_WriteData(0x2C);
    ST7735_WriteData(0x2D);

    // 4: Display inversion control
    ST7735_WriteCommand(ST7735_INVCTR);
    ST7735_WriteData(0x07);

    // 5: Power control
    ST7735_WriteCommand(ST7735_PWCTR1);
    ST7735_WriteData(0xA2);
    ST7735_WriteData(0x02);
    ST7735_WriteData(0x84);

    ST7735_WriteCommand(ST7735_PWCTR2);
    ST7735_WriteData(0xC5);

    ST7735_WriteCommand(ST7735_PWCTR3);
    ST7735_WriteData(0x0A);
    ST7735_WriteData(0x00);

    ST7735_WriteCommand(ST7735_PWCTR4);
    ST7735_WriteData(0x8A);
    ST7735_WriteData(0x2A);

    ST7735_WriteCommand(ST7735_PWCTR5);
    ST7735_WriteData(0x8A);
    ST7735_WriteData(0xEE);

    // 6: VCOM control
    ST7735_WriteCommand(ST7735_VMCTR1);
    ST7735_WriteData(0x0E);

    // 7: Inversion on
    ST7735_WriteCommand(ST7735_INVON);

    // 8: Memory access control
    ST7735_WriteCommand(ST7735_MADCTL);
    ST7735_WriteData(0xA8); // Landscape rotated 180° с правильным порядком цветов (0xA0 + BGR бит)

    // 9: Color mode
    ST7735_WriteCommand(ST7735_COLMOD);
    ST7735_WriteData(0x05); // 16-bit color

    // 10: Gamma correction
    ST7735_WriteCommand(ST7735_GMCTRP1);
    ST7735_WriteData(0x02);
    ST7735_WriteData(0x1c);
    ST7735_WriteData(0x07);
    ST7735_WriteData(0x12);
    ST7735_WriteData(0x37);
    ST7735_WriteData(0x32);
    ST7735_WriteData(0x29);
    ST7735_WriteData(0x2d);
    ST7735_WriteData(0x29);
    ST7735_WriteData(0x25);
    ST7735_WriteData(0x2B);
    ST7735_WriteData(0x39);
    ST7735_WriteData(0x00);
    ST7735_WriteData(0x01);
    ST7735_WriteData(0x03);
    ST7735_WriteData(0x10);

    ST7735_WriteCommand(ST7735_GMCTRN1);
    ST7735_WriteData(0x03);
    ST7735_WriteData(0x1d);
    ST7735_WriteData(0x07);
    ST7735_WriteData(0x06);
    ST7735_WriteData(0x2E);
    ST7735_WriteData(0x2C);
    ST7735_WriteData(0x29);
    ST7735_WriteData(0x2D);
    ST7735_WriteData(0x2E);
    ST7735_WriteData(0x2E);
    ST7735_WriteData(0x37);
    ST7735_WriteData(0x3F);
    ST7735_WriteData(0x00);
    ST7735_WriteData(0x00);
    ST7735_WriteData(0x02);
    ST7735_WriteData(0x10);

    // 11: Normal display on
    ST7735_WriteCommand(ST7735_NORON);
    HAL_Delay(10);

    // 12: Display on
    ST7735_WriteCommand(ST7735_DISPON);
    HAL_Delay(100);

    LCD_CS_HIGH();
}

/**
 * @brief Устанавливает "окно" для отрисовки.
 * @note Использует смещения из lcd.h.
 */
void ST7735_AddrSet(uint16_t x1, uint16_t y1, uint16_t x2, uint16_t y2)
{
    ST7735_WriteCommand(ST7735_CASET);
    ST7735_WriteData((x1 + LCD_X_SHIFT) >> 8);
    ST7735_WriteData((x1 + LCD_X_SHIFT) & 0xFF);
    ST7735_WriteData((x2 + LCD_X_SHIFT) >> 8);
    ST7735_WriteData((x2 + LCD_X_SHIFT) & 0xFF);

    ST7735_WriteCommand(ST7735_RASET);
    ST7735_WriteData((y1 + LCD_Y_SHIFT) >> 8);
    ST7735_WriteData((y1 + LCD_Y_SHIFT) & 0xFF);
    ST7735_WriteData((y2 + LCD_Y_SHIFT) >> 8);
    ST7735_WriteData((y2 + LCD_Y_SHIFT) & 0xFF);

    ST7735_WriteCommand(ST7735_RAMWR);
}

/**
 * @brief Заливает весь экран одним цветом.
 */
void ST7735_Fill(uint16_t color)
{
    uint16_t i, j;
    LCD_CS_LOW();
    ST7735_AddrSet(0, 0, LCD_W - 1, LCD_H - 1);
    LCD_RS_HIGH();
    for (i = 0; i < LCD_H; i++)
    {
        for (j = 0; j < LCD_W; j++)
        {
            ST7735_SPI_Send(color >> 8);
            ST7735_SPI_Send(color);
        }
    }
    LCD_CS_HIGH();
}

// Другие функции отрисовки (DrawPoint, DrawLine и т.д.) здесь пока опустим,
// так как они зависят от файла lcd.c, который мы добавим позже.
// Нам нужны только базовые функции для проверки.