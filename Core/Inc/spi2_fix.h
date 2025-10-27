/**
 * @file spi2_fix.h
 * @brief Заголовочный файл для функций исправления SPI2 передачи
 */

#ifndef SPI2_FIX_H
#define SPI2_FIX_H

#include "main.h"

// Функции для исправления проблем SPI2
void ClearAllSPI2Flags(void);
void PreloadSPI2_TX_FIFO(void);
void InitializeSPI2Communication_Fixed(void);
void RecoverSPI2_AfterError(void);

#endif /* SPI2_FIX_H */