/* LCD rendering is CPU-only. All runtime panel I/O is a lowest-priority,
   cooperative row-DMA state machine; no foreground wait or per-pixel SPI. */
#include "lcd.h"
#include "st7735.h"
#include "font.h"
#include <string.h>

extern SPI_HandleTypeDef hspi4;

#define LCD_DMA_TIMEOUT_MS 100u
#define LCD_DMA_PHASES 6u
#define LCD_ROW_BYTES (2u * LCD_W)
/* Reuse initialized and transfer_started_ms: no extra RAM or framebuffer. */
#define LCD_BOOT_ROWS 1u
#define LCD_BOOT_ON_PENDING 2u
#define LCD_BOOT_HOLD 3u
#define LCD_RUNNING 4u
#define LCD_BOOT_VISIBLE_MS 500u

typedef struct {
    uint16_t pixels[LCD_H][LCD_W];
    volatile uint32_t generation[LCD_H];
    volatile uint8_t dirty[LCD_H];
    DMA_HandleTypeDef dma;
    uint32_t copied_generation;
    uint32_t transfer_started_ms;
    uint32_t next_start_ms;
    uint16_t row;
    uint16_t cursor;
    uint8_t phase;
    uint8_t active;
    uint8_t waiting;
    uint8_t initialized;
} lcd_background_t;

/* D1 is full in the verified baseline. DTCM is CPU-only; DMA never reads it.
   NOLOAD storage is explicitly initialized in LCD_Init, not by startup bss. */
static lcd_background_t lcd_bg __attribute__((section(".ram_dtcm"), aligned(32)));
volatile lcd_background_diag_t g_lcd_background_diag
    __attribute__((section(".ram_dtcm"), aligned(32)));

/* One immutable, DMA-accessible row, shared by the command phases. It is not
   written again until SPI EOT has made the HAL handle READY. */
static uint8_t lcd_tx_row[LCD_ROW_BYTES]
    __attribute__((section(".ram_d2"), aligned(32)));

uint8_t lcd_ready = 0u;

static inline uint32_t lcd_cycles(void) { return DWT->CYCCNT; }
static inline uint8_t lcd_render_allowed(void)
{
    /* State0 is the private synchronous boot-scene render inside LCD_Init,
       before USB is started. No later IRQ/status render may alter that scene. */
    return (uint8_t)(lcd_ready &&
                    (lcd_bg.initialized == 0u || lcd_bg.initialized == LCD_RUNNING));
}

uint8_t LCD_BootScreenReady(uint32_t now_ms)
{
    if (g_lcd_background_diag.fault) return 0u;
    if (lcd_bg.initialized == LCD_BOOT_HOLD &&
        (uint32_t)(now_ms - lcd_bg.transfer_started_ms) >= LCD_BOOT_VISIBLE_MS) {
        lcd_bg.initialized = LCD_RUNNING;
    }
    return (uint8_t)(lcd_bg.initialized == LCD_RUNNING);
}
static inline void lcd_note_render(uint32_t started)
{
    uint32_t cycles = lcd_cycles() - started;
    if(cycles > g_lcd_background_diag.render_max_cycles)
        g_lcd_background_diag.render_max_cycles = cycles;
}

static inline void lcd_changed_row(uint16_t y)
{
    /* START/STOP command indication may render from USB IRQ. Publish pixels
       before the generation/dirty marker observed by the foreground flusher. */
    __DMB();
    lcd_bg.generation[y]++;
    lcd_bg.dirty[y] = 1u;
    g_lcd_background_diag.render_changed_rows++;
}

static void lcd_fault(uint32_t reason)
{
    g_lcd_background_diag.fault = reason;
    g_lcd_background_diag.spi_error = hspi4.ErrorCode;
    g_lcd_background_diag.dma_error = lcd_bg.dma.ErrorCode;
}

void LCD_Init(void)
{
    lcd_ready = 0u;
    ST7735_Init(); /* The existing boot-only controller reset/delay sequence. */
    memset(&lcd_bg, 0, sizeof(lcd_bg));
    memset((void*)&g_lcd_background_diag, 0, sizeof(g_lcd_background_diag));
    memset((void*)lcd_bg.dirty, 1, sizeof(lcd_bg.dirty));
    memset(lcd_tx_row, 0, sizeof(lcd_tx_row));
    LCD_CS_HIGH();

    /* SPI4 and DMA2_Stream0 have no other owner in this exact baseline.
       Existing ADC/DMA1, USB, RS485 and SPI3/WS2812 priorities are untouched. */
    __HAL_RCC_DMA2_CLK_ENABLE();
    lcd_bg.dma.Instance = DMA2_Stream0;
    lcd_bg.dma.Init.Request = DMA_REQUEST_SPI4_TX;
    lcd_bg.dma.Init.Direction = DMA_MEMORY_TO_PERIPH;
    lcd_bg.dma.Init.PeriphInc = DMA_PINC_DISABLE;
    lcd_bg.dma.Init.MemInc = DMA_MINC_ENABLE;
    lcd_bg.dma.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    lcd_bg.dma.Init.MemDataAlignment = DMA_MDATAALIGN_BYTE;
    lcd_bg.dma.Init.Mode = DMA_NORMAL;
    lcd_bg.dma.Init.Priority = DMA_PRIORITY_LOW;
    lcd_bg.dma.Init.FIFOMode = DMA_FIFOMODE_DISABLE;
    if(HAL_DMA_Init(&lcd_bg.dma) != HAL_OK){ lcd_fault(1u); return; }
    __HAL_LINKDMA(&hspi4, hdmatx, lcd_bg.dma);
    HAL_NVIC_SetPriority(DMA2_Stream0_IRQn, 15u, 0u);
    HAL_NVIC_SetPriority(SPI4_IRQn, 15u, 0u);
    HAL_NVIC_ClearPendingIRQ(DMA2_Stream0_IRQn);
    HAL_NVIC_ClearPendingIRQ(SPI4_IRQn);
    /* Render only facts known before the host starts; never guess TX/role. */
    lcd_ready = 1u;
    LCD_ShowString_Size(10u, 12u, "BMI30", 16u, WHITE, BLACK);
    LCD_ShowString_Size(10u, 34u, "STM32 1.2.59", 12u, WHITE, BLACK);
    LCD_ShowString_Size(10u, 52u, "Starting...", 12u, WHITE, BLACK);
    lcd_bg.initialized = LCD_BOOT_ROWS;
    HAL_NVIC_EnableIRQ(DMA2_Stream0_IRQn);
    HAL_NVIC_EnableIRQ(SPI4_IRQn);
}

void DMA2_Stream0_IRQHandler(void)
{
    if(lcd_bg.initialized) HAL_DMA_IRQHandler(&lcd_bg.dma);
}

void SPI4_IRQHandler(void)
{
    if(lcd_bg.initialized) HAL_SPI_IRQHandler(&hspi4);
}

/* H7 normal DMA completion enables SPI EOT IRQ; HAL closes the transfer and
   sets State=READY in SPI4_IRQHandler. The existing global SPI callbacks only
   handle SPI3, so they are left byte-for-byte unchanged. */
void __attribute__((optimize("O2"))) LCD_BackgroundService(uint32_t now_ms,
                                                         uint8_t may_start)
{
    uint32_t started = lcd_cycles();
    g_lcd_background_diag.service_calls++;
    g_lcd_background_diag.spi_state = (uint32_t)hspi4.State;
    g_lcd_background_diag.phase = lcd_bg.phase;
    g_lcd_background_diag.row = lcd_bg.row;
    g_lcd_background_diag.waiting = lcd_bg.waiting;
    if(!lcd_bg.initialized) goto done;
    if(lcd_bg.initialized == LCD_BOOT_HOLD && !LCD_BootScreenReady(now_ms)) goto done;

    if(lcd_bg.waiting){
        uint32_t age = now_ms - lcd_bg.transfer_started_ms;
        if(age > g_lcd_background_diag.dma_age_max_ms)
            g_lcd_background_diag.dma_age_max_ms = age;
        if(hspi4.State != HAL_SPI_STATE_READY){
            if(age > LCD_DMA_TIMEOUT_MS && !g_lcd_background_diag.fault){
                /* A panel/SPI fault cannot introduce a foreground wait or
                   trigger ADC/USB recovery. Keep the row dirty and stop LCD. */
                g_lcd_background_diag.dma_timeouts++;
                lcd_fault(3u);
            }
            goto done;
        }
        lcd_bg.waiting = 0u;
        if(hspi4.ErrorCode != HAL_SPI_ERROR_NONE || g_lcd_background_diag.fault){
            LCD_CS_HIGH();
            if(!g_lcd_background_diag.fault) lcd_fault(4u);
            goto done;
        }
        if(lcd_bg.initialized == LCD_BOOT_ON_PENDING && lcd_bg.phase == LCD_DMA_PHASES){
            /* The sole DISPON byte has physically completed without error. */
            lcd_bg.initialized = LCD_BOOT_HOLD;
            lcd_bg.transfer_started_ms = now_ms;
            LCD_CS_HIGH();
            goto done;
        }
        lcd_bg.phase++;
        if(lcd_bg.phase >= LCD_DMA_PHASES){
            /* A render during the DMA keeps this row pending for retransmit. */
            /* Clear before rechecking. An IRQ render between an earlier
               comparison and a later clear must never lose its dirty flag. */
            lcd_bg.dirty[lcd_bg.row] = 0u;
            __DMB();
            if(lcd_bg.generation[lcd_bg.row] != lcd_bg.copied_generation)
                lcd_bg.dirty[lcd_bg.row] = 1u;
            lcd_bg.cursor = (uint16_t)((lcd_bg.row + 1u) % LCD_H);
            lcd_bg.active = 0u;
            g_lcd_background_diag.rows_completed++;
            /* Every row started dirty and rendering is frozen during boot;
               cursor order therefore proves one complete known GRAM pass. */
            if(lcd_bg.initialized == LCD_BOOT_ROWS &&
               g_lcd_background_diag.rows_completed == LCD_H)
                lcd_bg.initialized = LCD_BOOT_ON_PENDING;
            LCD_CS_HIGH();
            goto done;
        }
    }
    if(g_lcd_background_diag.fault) goto done;
    if(!may_start){ g_lcd_background_diag.budget_skips++; goto done; }
    /* At most one small DMA start per tick. ADC/USB work runs before this task. */
    if((int32_t)(now_ms - lcd_bg.next_start_ms) < 0) goto done;
    if(hspi4.State != HAL_SPI_STATE_READY) goto done;

    if(lcd_bg.initialized == LCD_BOOT_ON_PENDING){
        /* Same one-start-per-tick/budget rule as rows, never blocking SPI. */
        lcd_bg.phase = LCD_DMA_PHASES;
        LCD_CS_LOW();
    } else if(!lcd_bg.active){
        uint16_t i;
        for(i = 0u; i < LCD_H; i++){
            uint16_t row = (uint16_t)((lcd_bg.cursor + i) % LCD_H);
            if(lcd_bg.dirty[row]){
                lcd_bg.row = row;
                lcd_bg.phase = 0u;
                lcd_bg.active = 1u;
                g_lcd_background_diag.row_jobs++;
                LCD_CS_LOW();
                break;
            }
        }
        if(i == LCD_H) goto done;
    }

    uint16_t length = 1u;
    switch(lcd_bg.phase){
        case 0u:
            LCD_RS_LOW(); lcd_tx_row[0] = ST7735_CASET; break;
        case 1u:
            LCD_RS_HIGH();
            lcd_tx_row[0] = (uint8_t)(LCD_X_SHIFT >> 8);
            lcd_tx_row[1] = (uint8_t)LCD_X_SHIFT;
            lcd_tx_row[2] = (uint8_t)((LCD_X_SHIFT + LCD_W - 1u) >> 8);
            lcd_tx_row[3] = (uint8_t)(LCD_X_SHIFT + LCD_W - 1u);
            length = 4u; break;
        case 2u:
            LCD_RS_LOW(); lcd_tx_row[0] = ST7735_RASET; break;
        case 3u: {
            uint16_t y = (uint16_t)(lcd_bg.row + LCD_Y_SHIFT);
            LCD_RS_HIGH();
            lcd_tx_row[0] = lcd_tx_row[2] = (uint8_t)(y >> 8);
            lcd_tx_row[1] = lcd_tx_row[3] = (uint8_t)y;
            length = 4u; break;
        }
        case 4u:
            LCD_RS_LOW(); lcd_tx_row[0] = ST7735_RAMWR; break;
        case LCD_DMA_PHASES:
            LCD_RS_LOW(); lcd_tx_row[0] = ST7735_DISPON; break;
        default:
            LCD_RS_HIGH();
            lcd_bg.copied_generation = lcd_bg.generation[lcd_bg.row];
            __DMB();
            for(uint16_t x = 0u; x < LCD_W; x++){
                uint16_t pixel = lcd_bg.pixels[lcd_bg.row][x];
                lcd_tx_row[2u*x] = (uint8_t)(pixel >> 8);
                lcd_tx_row[2u*x+1u] = (uint8_t)pixel;
            }
            length = LCD_ROW_BYTES;
            break;
    }

    /* D2 staging is aligned and whole-cache-line sized. The verified firmware
       leaves DCache off. If enabled later, clean only this owned DMA buffer. */
#if defined(__DCACHE_PRESENT) && (__DCACHE_PRESENT == 1U)
    if((SCB->CCR & SCB_CCR_DC_Msk) != 0u)
        SCB_CleanDCache_by_Addr((uint32_t*)lcd_tx_row, (int32_t)sizeof(lcd_tx_row));
#endif
    __DMB();
    lcd_bg.waiting = 1u;
    lcd_bg.transfer_started_ms = now_ms;
    HAL_StatusTypeDef result = HAL_SPI_Transmit_DMA(&hspi4, lcd_tx_row, length);
    if(result == HAL_OK){
        g_lcd_background_diag.dma_starts++;
        lcd_bg.next_start_ms = now_ms + 1u;
    } else {
        lcd_bg.waiting = 0u;
        g_lcd_background_diag.dma_start_failures++;
        if(result != HAL_BUSY){ LCD_CS_HIGH(); lcd_fault(2u); }
        lcd_bg.next_start_ms = now_ms + 1u;
    }
done:
    {
        uint32_t cycles = lcd_cycles() - started;
        if(cycles > g_lcd_background_diag.service_max_cycles)
            g_lcd_background_diag.service_max_cycles = cycles;
    }
}

void __attribute__((optimize("O2"))) LCD_DrawPoint(uint16_t x, uint16_t y,
                                                 uint16_t color)
{
    if(!lcd_render_allowed() || x >= LCD_W || y >= LCD_H) return;
    if(lcd_bg.pixels[y][x] != color){
        lcd_bg.pixels[y][x] = color;
        lcd_changed_row(y);
    }
}

void __attribute__((optimize("O2"))) LCD_FillRect(uint16_t x, uint16_t y,
                                                uint16_t w, uint16_t h,
                                                uint16_t color)
{
    uint32_t started = lcd_cycles();
    if(!lcd_render_allowed() || x >= LCD_W || y >= LCD_H || !w || !h) return;
    if(w > LCD_W - x) w = (uint16_t)(LCD_W - x);
    if(h > LCD_H - y) h = (uint16_t)(LCD_H - y);
    for(uint16_t row = y; row < y+h; row++){
        uint8_t changed = 0u;
        for(uint16_t col = x; col < x+w; col++){
            if(lcd_bg.pixels[row][col] != color){
                lcd_bg.pixels[row][col] = color;
                changed = 1u;
            }
        }
        if(changed) lcd_changed_row(row);
    }
    lcd_note_render(started);
}

void LCD_Clear(uint16_t color) { LCD_FillRect(0u,0u,LCD_W,LCD_H,color); }

void __attribute__((optimize("O2"))) LCD_ShowChar(uint16_t x, uint16_t y,
    uint8_t num, uint8_t size, uint16_t color, uint16_t back_color)
{
    uint32_t started = lcd_cycles();
    if(!lcd_render_allowed() || x >= LCD_W || y >= LCD_H || num < ' ' ||
       (uint8_t)(num-' ') >= 159u || (size != 12u && size != 16u)) return;
    uint8_t glyph = (uint8_t)(num-' ');
    uint16_t width = (size == 12u) ? 6u : 8u;
    uint16_t height = size;
    if(width > LCD_W-x) width = (uint16_t)(LCD_W-x);
    if(height > LCD_H-y) height = (uint16_t)(LCD_H-y);
    for(uint16_t row = 0u; row < height; row++){
        uint8_t changed = 0u;
        for(uint16_t col = 0u; col < width; col++){
            uint16_t index = (uint16_t)(2u*col + row/8u);
            uint8_t bits = (size == 12u) ? asc2_1206[glyph][index] : asc2_1608[glyph][index];
            uint8_t lit = (uint8_t)(bits & (uint8_t)(0x80u >> (row & 7u)));
            if(!lit && back_color == TRANSPARENT) continue;
            uint16_t pixel = lit ? color : back_color;
            if(lcd_bg.pixels[y+row][x+col] != pixel){
                lcd_bg.pixels[y+row][x+col] = pixel;
                changed = 1u;
            }
        }
        if(changed) lcd_changed_row((uint16_t)(y+row));
    }
    lcd_note_render(started);
}

void LCD_ShowString(uint16_t x, uint16_t y, const char *text, uint16_t color)
{
    if(text == NULL) return;
    while(*text){
        if(x > LCD_W-8u){ x = 0u; y = (uint16_t)(y+16u); }
        if(y > LCD_H-16u){ y = 0u; x = 0u; }
        LCD_ShowChar(x,y,(uint8_t)*text++,16u,color,TRANSPARENT);
        x = (uint16_t)(x+8u);
    }
}

void LCD_ShowString_Size(uint16_t x, uint16_t y, const char *text,
                         uint8_t size, uint16_t color, uint16_t back_color)
{
    if(text == NULL || (size != 12u && size != 16u)) return;
    uint16_t width = (size == 12u) ? 6u : 8u;
    while(*text){
        if(x > LCD_W-width){ x = 0u; y = (uint16_t)(y+size); }
        if(y > LCD_H-size){ y = 0u; x = 0u; }
        LCD_ShowChar(x,y,(uint8_t)*text++,size,color,back_color);
        x = (uint16_t)(x+width);
    }
}
