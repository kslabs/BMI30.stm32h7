// Низкоуровневая передача через стандартный стек STM32Cube USB CDC (HS, FS физика в HS core FS mode)
// Реализация функции usb_cdc_ll_write, используемой протоколом usb_cdc_proto.c

#include "usbd_cdc_if.h"
#include <stdint.h>
#include <stdbool.h>

// Простейшая блокирующая отправка. Если шина занята (USBD_BUSY) делаем короткие попытки.
// В дальнейшем можно заменить на кольцевой буфер и ISR завершения передачи.
bool usb_cdc_ll_write(const uint8_t *data, size_t len) {
    if (!data || !len) return false;
    if (len > 0xFFFFu) len = 0xFFFFu; // ограничение API
    uint32_t t0 = HAL_GetTick();
    while (1) {
        uint8_t st = CDC_Transmit_HS((uint8_t*)data, (uint16_t)len);
        if (st == USBD_OK) return true;
        if (st != USBD_BUSY) return false; // ошибка
        if ((HAL_GetTick() - t0) > 10) return false; // таймаут ~10 мс
    }
}
