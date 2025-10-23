#pragma once
#include "usbd_def.h"
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

extern USBD_ClassTypeDef USBD_CDC_VENDOR;

uint8_t USBD_VND_Transmit(USBD_HandleTypeDef *pdev, const uint8_t *data, uint16_t len);
uint32_t USBD_VND_Read(uint8_t *dst, uint32_t max_len);
void USBD_VND_DataReceived(const uint8_t *data, uint32_t len); /* weak */

/* Диагностика/сервис Vendor IN */
uint8_t USBD_VND_TxIsBusy(void);
uint8_t USBD_VND_LastTxRC(void);
uint16_t USBD_VND_LastTxLen(void);
/* Экстренный сброс флага занятости (на случай, если DataIn не вызвался на FS) */
void USBD_VND_ForceTxIdle(void);

#ifdef __cplusplus
}
#endif
