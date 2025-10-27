#ifdef USE_SPI2
/**
 * @file spi2_fix.c
 * @brief Функции для исправления проблем SPI2 передачи на MISO (PB14)
 */

#include "main.h"
#include <string.h>  // ИСПРАВЛЕНО: Добавляем для функции strcpy

extern SPI_HandleTypeDef hspi2;
extern uint8_t spi_rx_buffer[];
extern uint8_t spi_tx_buffer[];
extern char init_message_line1[];

/**
 * @brief Принудительно очищает все флаги ошибок SPI2 STM32H7
 */
void ClearAllSPI2Flags(void)
{
    // КРИТИЧЕСКИ ВАЖНО: Очищаем ВСЕ флаги ошибок STM32H7 SPI2!
    
    // Стандартные флаги HAL
    __HAL_SPI_CLEAR_OVRFLAG(&hspi2);   // Переполнение RX
    __HAL_SPI_CLEAR_MODFFLAG(&hspi2);  // Mode fault (NSS прерван)
    __HAL_SPI_CLEAR_FREFLAG(&hspi2);   // Frame error
    
    // STM32H7 специфичные флаги через регистр IFCR
    SET_BIT(hspi2.Instance->IFCR, SPI_IFCR_EOTC);    // End of transfer
    SET_BIT(hspi2.Instance->IFCR, SPI_IFCR_TXTFC);   // Transmission transfer filled
    SET_BIT(hspi2.Instance->IFCR, SPI_IFCR_UDRC);    // Underrun (TX пустой)
    SET_BIT(hspi2.Instance->IFCR, SPI_IFCR_OVRC);    // Overrun (RX переполнен)
    SET_BIT(hspi2.Instance->IFCR, SPI_IFCR_CRCEC);   // CRC error
    SET_BIT(hspi2.Instance->IFCR, SPI_IFCR_TIFREC);  // TI frame format error
    SET_BIT(hspi2.Instance->IFCR, SPI_IFCR_MODFC);   // Mode fault
    SET_BIT(hspi2.Instance->IFCR, SPI_IFCR_TSERFC);  // Transaction reload
    SET_BIT(hspi2.Instance->IFCR, SPI_IFCR_SUSPC);   // Suspend
    
    // Очищаем внутренние флаги HAL
    hspi2.ErrorCode = HAL_SPI_ERROR_NONE;
    hspi2.State = HAL_SPI_STATE_READY;
}

/**
 * @brief Предзагружает TX FIFO для передачи реальных данных АЦП (убрали тестовые 0x55)
 */
void PreloadSPI2_TX_FIFO(void)
{
    // ИСПРАВЛЕНО: НЕ заполняем TX FIFO тестовыми данными!
    // TX FIFO будет заполняться реальными данными АЦП при запросах от RPI
    
    // ВАЖНО: Очищаем TX FIFO перед использованием
    uint32_t timeout = 1000;
    
    // Проверяем что TX FIFO пустой и готов к работе
    while(!(hspi2.Instance->SR & SPI_FLAG_TXE) && timeout > 0) {
        timeout--;
    }
    
    // TX FIFO готов, но НЕ заполняем его тестовыми данными!
    // Реальные данные АЦП будут загружены через DMA при запросах от RPI
}

/**
 * @brief Исправленная инициализация SPI2 для передачи реальных данных АЦП
 */
void InitializeSPI2Communication_Fixed(void)
{
    // КРИТИЧЕСКИ ВАЖНО: ПОЛНЫЙ СБРОС SPI2!
    HAL_SPI_DeInit(&hspi2);
    HAL_Delay(10); // Даем время на сброс всех регистров
    
    if(HAL_SPI_Init(&hspi2) != HAL_OK) {
        strcpy(init_message_line1, "SPI2 INIT ERR");
        return;
    }
    
    // ПРИНУДИТЕЛЬНО ОЧИЩАЕМ ВСЕ ФЛАГИ ОШИБОК
    ClearAllSPI2Flags();
    
    // ВКЛЮЧАЕМ SPI2 ЕСЛИ ОН ВЫКЛЮЧЕН
    if((hspi2.Instance->CR1 & SPI_CR1_SPE) == 0) {
        __HAL_SPI_ENABLE(&hspi2);
    }
    
    // ИСПРАВЛЕНО: НЕ предзагружаем TX FIFO тестовыми данными!
    // TX FIFO будет заполняться реальными данными АЦП при DMA передачах
    PreloadSPI2_TX_FIFO(); // Только проверяем готовность TX FIFO
    
    // ЗАПУСКАЕМ ТОЛЬКО ПРИЕМ - передача будет автоматически при запросах АЦП
    if(HAL_SPI_Receive_IT(&hspi2, spi_rx_buffer, 1) != HAL_OK)
    {
        strcpy(init_message_line1, "SPI2 RX ERR");
    }
    else
    {
        strcpy(init_message_line1, "ADC DATA READY"); // Готов передавать данные АЦП!
    }
}

/**
 * @brief Принудительно восстанавливает SPI2 после ошибки
 */
void RecoverSPI2_AfterError(void)
{
    // Очищаем все флаги ошибок
    ClearAllSPI2Flags();
    
    // Перезагружаем TX FIFO
    PreloadSPI2_TX_FIFO();
    
    // Перезапускаем прием
    HAL_SPI_Receive_IT(&hspi2, spi_rx_buffer, 1);
}
#endif // USE_SPI2