/* Логирование Vendor: ОТКЛЮЧЕНО для максимальной производительности */
#ifndef USBD_VND_LOG_ENABLE
#define USBD_VND_LOG_ENABLE 0
#endif
#if USBD_VND_LOG_ENABLE
#define VND_LOGF(...) printf(__VA_ARGS__)
#else
#define VND_LOGF(...) do{}while(0)
#endif

/*
 * usbd_cdc_custom.c
 * Объединённый класс: стандартный CDC (2 интерфейса) + дополнительный Vendor Bulk интерфейс.
 * Добавлен 3‑й интерфейс (bNumInterfaces=3) и два конечных точки: EP3 OUT (0x03), EP3 IN (0x83) Bulk.
 */
#include "usbd_cdc.h"      // для типов и макросов CDC
#include "usbd_ctlreq.h"
#include <stdio.h>
#include "usb_vendor_app.h" // ДОБАВЛЕНО: для VND_CMD_* и vnd_build_status
#include <string.h>
#include "stm32h7xx_hal.h"  // для SCB_CleanDCache_by_Addr (H7, D-Cache)

#ifndef USBD_CDC_USERDATA_INDEX
#define USBD_CDC_USERDATA_INDEX 0
#endif
#define CDC_USR(pdev) ((USBD_CDC_ItfTypeDef*)(pdev)->pUserData[USBD_CDC_USERDATA_INDEX])

/* Дополнительные endpoint'ы Vendor */
#define VND_OUT_EP                     0x03U
#define VND_IN_EP                      0x83U
#define VND_DATA_HS_MAX_PACKET_SIZE    512U
#define VND_DATA_FS_MAX_PACKET_SIZE    64U

/* Максимальный размер одного кадра Vendor (32 байта заголовок + до 1024 выборок *2) */
#define VND_MAX_FRAME_SIZE             (32u + 1024u*2u) /* =2080 */

/*
 * Конфигурационный дескриптор: добавляем Vendor IF#2 с двумя alt-setting:
 *  - alt 0: 0 endpoints (idle)
 *  - alt 1: bulk OUT 0x03, bulk IN 0x83 (stream)
 * Итого: 9 байт (IF alt0) + 9+7+7 байт (IF alt1 + 2 EP) = 32 байта вместо прежних 23.
 */
#ifndef USB_CDC_CONFIG_DESC_SIZ
#warning "Ожидается объявление USB_CDC_CONFIG_DESC_SIZ в usbd_cdc.h"
#define USB_CDC_CONFIG_DESC_SIZ  (67) /* fallback */
#endif
#define USB_CDC_VENDOR_CONFIG_DESC_SIZ (USB_CDC_CONFIG_DESC_SIZ + 40U)

/* Прототипы */
static uint8_t USBD_CDCVND_Init(USBD_HandleTypeDef *pdev, uint8_t cfgidx);
static uint8_t USBD_CDCVND_DeInit(USBD_HandleTypeDef *pdev, uint8_t cfgidx);
static uint8_t USBD_CDCVND_Setup(USBD_HandleTypeDef *pdev, USBD_SetupReqTypedef *req);
static uint8_t USBD_CDCVND_DataIn(USBD_HandleTypeDef *pdev, uint8_t epnum);
static uint8_t USBD_CDCVND_DataOut(USBD_HandleTypeDef *pdev, uint8_t epnum);
static uint8_t USBD_CDCVND_EP0_RxReady(USBD_HandleTypeDef *pdev);
static uint8_t *USBD_CDCVND_GetFSCfgDesc(uint16_t *length);
static uint8_t *USBD_CDCVND_GetHSCfgDesc(uint16_t *length);
static uint8_t *USBD_CDCVND_GetOtherSpeedCfgDesc(uint16_t *length);
static uint8_t *USBD_CDCVND_GetDeviceQualifierDescriptor(uint16_t *length);

/* Флаги фоновых запросов управления (EP0) */
static volatile uint8_t g_req_soft_reset = 0;
static volatile uint8_t g_req_deep_reset = 0;
/* Текущие alt-settings по интерфейсам (нас интересует IF#2) */
static volatile uint8_t g_alt_if2 = 0; /* 0=idle, 1=stream */

/* Внешние зависимости */
extern USBD_HandleTypeDef hUsbDeviceHS;
extern void vnd_pipeline_stop_reset(int deep);
/* Прототип локальной функции, определённой ниже, чтобы избежать implicit declaration */
void USBD_VND_ForceTxIdle(void);

/* Простейшие буферы Vendor (нужны до VND_Class_*Reset) */
static uint8_t vnd_rx_buf[VND_DATA_HS_MAX_PACKET_SIZE];
static uint8_t vnd_tx_buf[VND_MAX_FRAME_SIZE];
static volatile uint32_t vnd_rx_len = 0;
static volatile uint8_t vnd_tx_busy = 0;
static volatile uint8_t vnd_last_tx_rc = 0xFF; /* последний rc из USBD_LL_Transmit */
static volatile uint16_t vnd_last_tx_len = 0;

/* Запросить soft/deep reset откуда угодно (в т.ч. из приложения) */
void USBD_VND_RequestSoftReset(void){ g_req_soft_reset = 1; }
void USBD_VND_RequestDeepReset(void){ g_req_deep_reset = 1; }

/* Выполнить мягкий/глубокий ресет класса Vendor (без ре-энумерации USB) */
static void VND_Class_SoftReset(USBD_HandleTypeDef *pdev)
{
  /* Снять занятость, очистить возможные STALL, флешнуть FIFO EP */
  USBD_VND_ForceTxIdle();
  (void)USBD_LL_FlushEP(pdev, VND_IN_EP);
  (void)USBD_LL_FlushEP(pdev, VND_OUT_EP);
  (void)USBD_LL_ClearStallEP(pdev, VND_IN_EP);
  (void)USBD_LL_ClearStallEP(pdev, VND_OUT_EP);
  /* Реарм приёма */
  if (g_alt_if2 == 1) {
    if (pdev->dev_speed == USBD_SPEED_HIGH)
      (void)USBD_LL_PrepareReceive(pdev, VND_OUT_EP, vnd_rx_buf, VND_DATA_HS_MAX_PACKET_SIZE);
    else
      (void)USBD_LL_PrepareReceive(pdev, VND_OUT_EP, vnd_rx_buf, VND_DATA_FS_MAX_PACKET_SIZE);
  }
  /* Остановить и очистить пайплайн приложения */
  vnd_pipeline_stop_reset(0);
}

static void VND_Class_DeepReset(USBD_HandleTypeDef *pdev)
{
  /* Закрыть и переоткрыть конечные точки Vendor */
  (void)USBD_LL_CloseEP(pdev, VND_IN_EP);  pdev->ep_in[VND_IN_EP & 0x0FU].is_used = 0U;
  (void)USBD_LL_CloseEP(pdev, VND_OUT_EP); pdev->ep_out[VND_OUT_EP & 0x0FU].is_used = 0U;
  if (g_alt_if2 == 1) {
    if (pdev->dev_speed == USBD_SPEED_HIGH) {
      (void)USBD_LL_OpenEP(pdev, VND_IN_EP,  USBD_EP_TYPE_BULK, VND_DATA_HS_MAX_PACKET_SIZE);
      pdev->ep_in[VND_IN_EP & 0x0FU].is_used = 1U;
      (void)USBD_LL_OpenEP(pdev, VND_OUT_EP, USBD_EP_TYPE_BULK, VND_DATA_HS_MAX_PACKET_SIZE);
      pdev->ep_out[VND_OUT_EP & 0x0FU].is_used = 1U;
    } else {
      (void)USBD_LL_OpenEP(pdev, VND_IN_EP,  USBD_EP_TYPE_BULK, VND_DATA_FS_MAX_PACKET_SIZE);
      pdev->ep_in[VND_IN_EP & 0x0FU].is_used = 1U;
      (void)USBD_LL_OpenEP(pdev, VND_OUT_EP, USBD_EP_TYPE_BULK, VND_DATA_FS_MAX_PACKET_SIZE);
      pdev->ep_out[VND_OUT_EP & 0x0FU].is_used = 1U;
    }
    /* Реарм приёма */
    if (pdev->dev_speed == USBD_SPEED_HIGH)
      (void)USBD_LL_PrepareReceive(pdev, VND_OUT_EP, vnd_rx_buf, VND_DATA_HS_MAX_PACKET_SIZE);
    else
      (void)USBD_LL_PrepareReceive(pdev, VND_OUT_EP, vnd_rx_buf, VND_DATA_FS_MAX_PACKET_SIZE);
  }
  /* Полная переинициализация пайплайна приложения */
  vnd_pipeline_stop_reset(1);
}

/* Фоновый сервис для исполнения заявок управления */
void USBD_VND_ProcessControlRequests(void)
{
  if (g_req_deep_reset) { g_req_deep_reset = 0; VND_Class_DeepReset(&hUsbDeviceHS); }
  if (g_req_soft_reset) { g_req_soft_reset = 0; VND_Class_SoftReset(&hUsbDeviceHS); }
}

/* Удаляем зависимость от статического дескриптора оригинального файла */
static __ALIGN_BEGIN uint8_t USBD_CDCVND_DeviceQualifierDesc[USB_LEN_DEV_QUALIFIER_DESC] __ALIGN_END = {
  USB_LEN_DEV_QUALIFIER_DESC,
  USB_DESC_TYPE_DEVICE_QUALIFIER,
  0x00, 0x02, /* USB 2.00 */
  0x00, 0x00, 0x00, /* class/subclass/proto (each interface specifies) */
  0x40, /* max packet size for ep0 */
  0x01, /* num configurations */
  0x00
};

/* буферы Vendor уже объявлены выше (для использования в VND_Class_*Reset) */

/* Слабый callback для приёма данных Vendor */
__weak void USBD_VND_DataReceived(const uint8_t *data, uint32_t len) { (void)data; (void)len; }

/* Слабый callback завершения передачи Vendor IN */
__weak void USBD_VND_TxCplt(void) {}

/* API для передачи по Vendor */
uint8_t USBD_VND_Transmit(USBD_HandleTypeDef *pdev, const uint8_t *data, uint16_t len)
{
  if (len > (uint16_t)sizeof(vnd_tx_buf)) return (uint8_t)USBD_FAIL; /* недопустимо: кадр больше ожидаемого */
  /* Сначала проверяем занятость; при BUSY — минимальный лог без засорения основного [VND_TX] */
  if (vnd_tx_busy) {
    if (len >= 4) {
      VND_LOGF("[VND_BUSY] ep=0x%02X len=%u head=%02X %02X %02X %02X\r\n", (unsigned)VND_IN_EP, (unsigned)len,
             (unsigned)data[0], (unsigned)data[1], (unsigned)data[2], (unsigned)data[3]);
    } else {
      VND_LOGF("[VND_BUSY] ep=0x%02X len=%u\r\n", (unsigned)VND_IN_EP, (unsigned)len);
    }
    return (uint8_t)USBD_BUSY;
  }
  memcpy(vnd_tx_buf, data, len);
  /* ВАЖНО (STM32H7, включён D-Cache): очистить кэш перед DMA/USB IN,
     иначе хост увидит старые/нулевые данные в памяти. Выравниваем адрес/длину на 32 байта. */
#if defined (SCB_CleanDCache_by_Addr)
  {
    uintptr_t addr = (uintptr_t)vnd_tx_buf;
    uint32_t  clean_addr = (uint32_t)(addr & ~((uintptr_t)31U));
    uint32_t  clean_len  = (uint32_t)(((addr + len + 31U) & ~((uintptr_t)31U)) - clean_addr);
    SCB_CleanDCache_by_Addr((uint32_t*)clean_addr, (int32_t)clean_len);
  }
#endif
  /* Жёсткий запрет STAT mid-stream: если это не рабочий кадр (не 0x5A 0xA5) и идёт стрим, разрешаем только при явном разрешении */
  extern uint8_t streaming; /* из usb_vendor_app.c */
  extern volatile uint8_t vnd_status_permit_once; /* одноразовое разрешение STAT */
  if (streaming) {
    if (!(len >= 2 && vnd_tx_buf[0]==0x5A && vnd_tx_buf[1]==0xA5)) {
      if (vnd_status_permit_once) {
        vnd_status_permit_once = 0; /* использовать разрешение один раз */
      } else {
        /* Блокируем STAT mid-stream */
        /* Лёгкая диагностика блокировки */
        if (len >= 4) {
          VND_LOGF("[VND_BLOCK] ep=0x%02X len=%u head=%02X %02X %02X %02X\r\n", (unsigned)VND_IN_EP, (unsigned)len,
                 (unsigned)vnd_tx_buf[0], (unsigned)vnd_tx_buf[1], (unsigned)vnd_tx_buf[2], (unsigned)vnd_tx_buf[3]);
        } else {
          VND_LOGF("[VND_BLOCK] ep=0x%02X len=%u\r\n", (unsigned)VND_IN_EP, (unsigned)len);
        }
        return (uint8_t)USBD_BUSY;
      }
    }
  }
  vnd_tx_busy = 1U;
  /* Восстанавливаем total_length для корректной ZLP логики в DataIn callback */
  pdev->ep_in[VND_IN_EP & 0x0FU].total_length = len;
  vnd_last_tx_len = len;
    vnd_last_tx_rc = (uint8_t)USBD_LL_Transmit(pdev, VND_IN_EP, vnd_tx_buf, len);
    
    /* КРИТИЧЕСКИ ВАЖНО: memory barrier через volatile read USB регистра.
       Без этого компилятор может переупорядочить операции и HAL ISR не увидит
       правильное состояние. Чтение GINTSTS безопасно и гарантирует порядок. */
    {
        USB_OTG_GlobalTypeDef *usb_reg = (USB_OTG_GlobalTypeDef *)USB1_OTG_HS_PERIPH_BASE;
        (void)usb_reg->GINTSTS; /* volatile read для memory barrier */
    }  /* Логируем только реально поставленные в LL передачи как [VND_TX] */
  if (vnd_last_tx_rc == (uint8_t)USBD_OK) {
    if (len >= 4) {
      VND_LOGF("[VND_TX] ep=0x%02X len=%u head=%02X %02X %02X %02X\r\n", (unsigned)VND_IN_EP, (unsigned)len,
             (unsigned)vnd_tx_buf[0], (unsigned)vnd_tx_buf[1], (unsigned)vnd_tx_buf[2], (unsigned)vnd_tx_buf[3]);
    } else {
      VND_LOGF("[VND_TX] ep=0x%02X len=%u\r\n", (unsigned)VND_IN_EP, (unsigned)len);
    }
  } else {
    /* Если LL вернул BUSY/FAIL — снимаем флаг занятости и логируем как FAIL */
    vnd_tx_busy = 0U;
    if (len >= 4) {
      VND_LOGF("[VND_FAIL] ep=0x%02X rc=%u len=%u head=%02X %02X %02X %02X\r\n", (unsigned)VND_IN_EP, (unsigned)vnd_last_tx_rc, (unsigned)len,
             (unsigned)vnd_tx_buf[0], (unsigned)vnd_tx_buf[1], (unsigned)vnd_tx_buf[2], (unsigned)vnd_tx_buf[3]);
    } else {
      VND_LOGF("[VND_FAIL] ep=0x%02X rc=%u len=%u\r\n", (unsigned)VND_IN_EP, (unsigned)vnd_last_tx_rc, (unsigned)len);
    }
  }
  return vnd_last_tx_rc;
}

uint32_t USBD_VND_Read(uint8_t *dst, uint32_t max_len)
{
  uint32_t copy = (vnd_rx_len < max_len) ? vnd_rx_len : max_len;
  memcpy(dst, vnd_rx_buf, copy);
  vnd_rx_len = 0; /* помечаем прочитанным */
  return copy;
}

/* Диагностика состояния Vendor IN */
uint8_t USBD_VND_TxIsBusy(void) { return vnd_tx_busy; }
uint8_t USBD_VND_LastTxRC(void) { return vnd_last_tx_rc; }
uint16_t USBD_VND_LastTxLen(void) { return vnd_last_tx_len; }

/* Форсируем свободное состояние TX (использовать осторожно: только при подтверждённом клине) */
void USBD_VND_ForceTxIdle(void)
{
  if (vnd_tx_busy) {
    VND_LOGF("[VND_FORCE_IDLE] clearing busy (last len=%u rc=%u)\r\n", (unsigned)vnd_last_tx_len, (unsigned)vnd_last_tx_rc);
  }
  vnd_tx_busy = 0U;
}

/* Конфигурационные дескрипторы (HS/FS/Other) */
__ALIGN_BEGIN static uint8_t USBD_CDCVND_CfgHSDesc[USB_CDC_VENDOR_CONFIG_DESC_SIZ] __ALIGN_END = {
  /* Configuration Descriptor */
  0x09, USB_DESC_TYPE_CONFIGURATION,
  LOBYTE(USB_CDC_VENDOR_CONFIG_DESC_SIZ), HIBYTE(USB_CDC_VENDOR_CONFIG_DESC_SIZ),
  0x03, /* bNumInterfaces: 3 (2 CDC + 1 Vendor) */
  0x01, 0x00,
#if (USBD_SELF_POWERED == 1U)
  0xC0,
#else
  0x80,
#endif
  USBD_MAX_POWER,
  /* -------- IAD for CDC (IF0+IF1) -------- */
  0x08, 0x0B, 0x00, 0x02, 0x02, 0x02, 0x01, 0x00,
  /* -------- CDC Communication Interface (IF0) -------- */
  0x09, USB_DESC_TYPE_INTERFACE, 0x00, 0x00, 0x01, 0x02, 0x02, 0x01, 0x00,
  /* Header */              0x05, 0x24, 0x00, 0x10, 0x01,
  /* Call Management */     0x05, 0x24, 0x01, 0x00, 0x01,
  /* ACM */                 0x04, 0x24, 0x02, 0x02,
  /* Union */               0x05, 0x24, 0x06, 0x00, 0x01,
  /* CMD EP (Interrupt IN) */
  0x07, USB_DESC_TYPE_ENDPOINT, CDC_CMD_EP, 0x03,
  LOBYTE(CDC_CMD_PACKET_SIZE), HIBYTE(CDC_CMD_PACKET_SIZE), CDC_HS_BINTERVAL,
  /* -------- CDC Data Interface (IF1) -------- */
  0x09, USB_DESC_TYPE_INTERFACE, 0x01, 0x00, 0x02, 0x0A, 0x00, 0x00, 0x00,
  /* OUT EP */ 0x07, USB_DESC_TYPE_ENDPOINT, CDC_OUT_EP, 0x02,
  LOBYTE(CDC_DATA_HS_MAX_PACKET_SIZE), HIBYTE(CDC_DATA_HS_MAX_PACKET_SIZE), 0x00,
  /* IN EP  */ 0x07, USB_DESC_TYPE_ENDPOINT, CDC_IN_EP, 0x02,
  LOBYTE(CDC_DATA_HS_MAX_PACKET_SIZE), HIBYTE(CDC_DATA_HS_MAX_PACKET_SIZE), 0x00,
  /* -------- Vendor Interface (IF2) ALT 0 (idle, 0 EP) -------- */
  0x09, USB_DESC_TYPE_INTERFACE, 0x02, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x05,
  /* -------- Vendor Interface (IF2) ALT 1 (stream, 2 EP) -------- */
  0x09, USB_DESC_TYPE_INTERFACE, 0x02, 0x01, 0x02, 0xFF, 0x00, 0x00, 0x05,
  /* Vendor OUT */ 0x07, USB_DESC_TYPE_ENDPOINT, VND_OUT_EP, 0x02,
  LOBYTE(VND_DATA_HS_MAX_PACKET_SIZE), HIBYTE(VND_DATA_HS_MAX_PACKET_SIZE), 0x00,
  /* Vendor IN  */ 0x07, USB_DESC_TYPE_ENDPOINT, VND_IN_EP, 0x02,
  LOBYTE(VND_DATA_HS_MAX_PACKET_SIZE), HIBYTE(VND_DATA_HS_MAX_PACKET_SIZE), 0x00,
};

__ALIGN_BEGIN static uint8_t USBD_CDCVND_CfgFSDesc[USB_CDC_VENDOR_CONFIG_DESC_SIZ] __ALIGN_END = {
  0x09, USB_DESC_TYPE_CONFIGURATION,
  LOBYTE(USB_CDC_VENDOR_CONFIG_DESC_SIZ), HIBYTE(USB_CDC_VENDOR_CONFIG_DESC_SIZ),
  0x03, 0x01, 0x00,
#if (USBD_SELF_POWERED == 1U)
  0xC0,
#else
  0x80,
#endif
  USBD_MAX_POWER,
  /* IAD (IF0+IF1) */ 0x08, 0x0B, 0x00, 0x02, 0x02, 0x02, 0x01, 0x00,
  /* IF0 */ 0x09, USB_DESC_TYPE_INTERFACE, 0x00, 0x00, 0x01, 0x02, 0x02, 0x01, 0x00,
  0x05,0x24,0x00,0x10,0x01,
  0x05,0x24,0x01,0x00,0x01,
  0x04,0x24,0x02,0x02,
  0x05,0x24,0x06,0x00,0x01,
  0x07, USB_DESC_TYPE_ENDPOINT, CDC_CMD_EP, 0x03,
  LOBYTE(CDC_CMD_PACKET_SIZE), HIBYTE(CDC_CMD_PACKET_SIZE), CDC_FS_BINTERVAL,
  /* IF1 */ 0x09, USB_DESC_TYPE_INTERFACE, 0x01, 0x00, 0x02, 0x0A, 0x00, 0x00, 0x00,
  0x07, USB_DESC_TYPE_ENDPOINT, CDC_OUT_EP, 0x02,
  LOBYTE(CDC_DATA_FS_MAX_PACKET_SIZE), HIBYTE(CDC_DATA_FS_MAX_PACKET_SIZE), 0x00,
  0x07, USB_DESC_TYPE_ENDPOINT, CDC_IN_EP, 0x02,
  LOBYTE(CDC_DATA_FS_MAX_PACKET_SIZE), HIBYTE(CDC_DATA_FS_MAX_PACKET_SIZE), 0x00,
  /* IF2 Vendor ALT0 */ 0x09, USB_DESC_TYPE_INTERFACE, 0x02, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x05,
  /* IF2 Vendor ALT1 */ 0x09, USB_DESC_TYPE_INTERFACE, 0x02, 0x01, 0x02, 0xFF, 0x00, 0x00, 0x05,
  0x07, USB_DESC_TYPE_ENDPOINT, VND_OUT_EP, 0x02,
  LOBYTE(VND_DATA_FS_MAX_PACKET_SIZE), HIBYTE(VND_DATA_FS_MAX_PACKET_SIZE), 0x00,
  0x07, USB_DESC_TYPE_ENDPOINT, VND_IN_EP, 0x02,
  LOBYTE(VND_DATA_FS_MAX_PACKET_SIZE), HIBYTE(VND_DATA_FS_MAX_PACKET_SIZE), 0x00,
};

__ALIGN_BEGIN static uint8_t USBD_CDCVND_OtherSpeedCfgDesc[USB_CDC_VENDOR_CONFIG_DESC_SIZ] __ALIGN_END = {
  0x09, USB_DESC_TYPE_OTHER_SPEED_CONFIGURATION,
  LOBYTE(USB_CDC_VENDOR_CONFIG_DESC_SIZ), HIBYTE(USB_CDC_VENDOR_CONFIG_DESC_SIZ),
  0x03, 0x01, 0x04,
#if (USBD_SELF_POWERED == 1U)
  0xC0,
#else
  0x80,
#endif
  USBD_MAX_POWER,
  /* IAD (IF0+IF1) */ 0x08, 0x0B, 0x00, 0x02, 0x02, 0x02, 0x01, 0x00,
  /* IF0 */ 0x09, USB_DESC_TYPE_INTERFACE, 0x00, 0x00, 0x01, 0x02, 0x02, 0x01, 0x00,
  0x05,0x24,0x00,0x10,0x01,
  0x05,0x24,0x01,0x00,0x01,
  0x04,0x24,0x02,0x02,
  0x05,0x24,0x06,0x00,0x01,
  0x07, USB_DESC_TYPE_ENDPOINT, CDC_CMD_EP, 0x03,
  LOBYTE(CDC_CMD_PACKET_SIZE), HIBYTE(CDC_CMD_PACKET_SIZE), CDC_FS_BINTERVAL,
  /* IF1 */ 0x09, USB_DESC_TYPE_INTERFACE, 0x01, 0x00, 0x02, 0x0A, 0x00, 0x00, 0x00,
  0x07, USB_DESC_TYPE_ENDPOINT, CDC_OUT_EP, 0x02, 0x40, 0x00, 0x00,
  0x07, USB_DESC_TYPE_ENDPOINT, CDC_IN_EP, 0x02, 0x40, 0x00, 0x00,
  /* IF2 Vendor ALT0 */ 0x09, USB_DESC_TYPE_INTERFACE, 0x02, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x05,
  /* IF2 Vendor ALT1 */ 0x09, USB_DESC_TYPE_INTERFACE, 0x02, 0x01, 0x02, 0xFF, 0x00, 0x00, 0x05,
  0x07, USB_DESC_TYPE_ENDPOINT, VND_OUT_EP, 0x02, 0x40, 0x00, 0x00,
  0x07, USB_DESC_TYPE_ENDPOINT, VND_IN_EP, 0x02, 0x40, 0x00, 0x00,
};

/* Экспортируемая структура класса */
USBD_ClassTypeDef USBD_CDC_VENDOR = {
  USBD_CDCVND_Init,
  USBD_CDCVND_DeInit,
  USBD_CDCVND_Setup,
  NULL,
  USBD_CDCVND_EP0_RxReady,
  USBD_CDCVND_DataIn,
  USBD_CDCVND_DataOut,
  NULL, NULL, NULL,
  USBD_CDCVND_GetHSCfgDesc,
  USBD_CDCVND_GetFSCfgDesc,
  USBD_CDCVND_GetOtherSpeedCfgDesc,
  USBD_CDCVND_GetDeviceQualifierDescriptor,
};

/* ---------------- Реализация коллбэков ---------------- */
static uint8_t USBD_CDCVND_Init(USBD_HandleTypeDef *pdev, uint8_t cfgidx)
{
  g_alt_if2 = 0; /* при конфигурации по умолчанию IF2 в alt0 (idle) */
  UNUSED(cfgidx);
  USBD_CDC_HandleTypeDef *hcdc = USBD_malloc(sizeof(USBD_CDC_HandleTypeDef));
  if (!hcdc) { pdev->pClassData = NULL; return (uint8_t)USBD_EMEM; }
  pdev->pClassData = hcdc;

  if (pdev->dev_speed == USBD_SPEED_HIGH) {
    (void)USBD_LL_OpenEP(pdev, CDC_IN_EP,  USBD_EP_TYPE_BULK, CDC_DATA_HS_IN_PACKET_SIZE);
    pdev->ep_in[CDC_IN_EP & 0xFU].is_used = 1U;
    (void)USBD_LL_OpenEP(pdev, CDC_OUT_EP, USBD_EP_TYPE_BULK, CDC_DATA_HS_OUT_PACKET_SIZE);
    pdev->ep_out[CDC_OUT_EP & 0xFU].is_used = 1U;
    pdev->ep_in[CDC_CMD_EP & 0xFU].bInterval = CDC_HS_BINTERVAL;
    /* Vendor IF#2 alt0 по умолчанию — EP будут открыты по SET_INTERFACE(alt=1) */
  } else {
    (void)USBD_LL_OpenEP(pdev, CDC_IN_EP,  USBD_EP_TYPE_BULK, CDC_DATA_FS_IN_PACKET_SIZE);
    pdev->ep_in[CDC_IN_EP & 0xFU].is_used = 1U;
    (void)USBD_LL_OpenEP(pdev, CDC_OUT_EP, USBD_EP_TYPE_BULK, CDC_DATA_FS_OUT_PACKET_SIZE);
    pdev->ep_out[CDC_OUT_EP & 0xFU].is_used = 1U;
    pdev->ep_in[CDC_CMD_EP & 0xFU].bInterval = CDC_FS_BINTERVAL;
    /* Vendor IF#2 alt0 по умолчанию — EP будут открыты по SET_INTERFACE(alt=1) */
  }
  (void)USBD_LL_OpenEP(pdev, CDC_CMD_EP, USBD_EP_TYPE_INTR, CDC_CMD_PACKET_SIZE);
  pdev->ep_in[CDC_CMD_EP & 0xFU].is_used = 1U;

  if (CDC_USR(pdev)) CDC_USR(pdev)->Init();
  hcdc->TxState = 0; hcdc->RxState = 0;

  /* Готовим приём CDC OUT; Vendor OUT готовим только после включения alt1 */
  if (pdev->dev_speed == USBD_SPEED_HIGH) {
    (void)USBD_LL_PrepareReceive(pdev, CDC_OUT_EP, hcdc->RxBuffer, CDC_DATA_HS_OUT_PACKET_SIZE);
  } else {
    (void)USBD_LL_PrepareReceive(pdev, CDC_OUT_EP, hcdc->RxBuffer, CDC_DATA_FS_OUT_PACKET_SIZE);
  }

  /* ОТКЛЮЧЕНО: не отправляем ничего по Vendor IN до явного START_STREAM,
    чтобы не занимать endpoint до того, как хост начнёт читать. */

  /* Диагностический лог скорости USB и maxpacket Vendor IN */
  if(pdev->dev_speed == USBD_SPEED_HIGH){
    VND_LOGF("[VND_INIT] SPEED=HS vnd_mps=%u cdc_in_mps=%u\r\n",
             (unsigned)pdev->ep_in[VND_IN_EP & 0x0FU].maxpacket,
             (unsigned)pdev->ep_in[CDC_IN_EP & 0x0FU].maxpacket);
  } else {
    VND_LOGF("[VND_INIT] SPEED=FS vnd_mps=%u cdc_in_mps=%u\r\n",
             (unsigned)pdev->ep_in[VND_IN_EP & 0x0FU].maxpacket,
             (unsigned)pdev->ep_in[CDC_IN_EP & 0x0FU].maxpacket);
  }

  return (uint8_t)USBD_OK;
}

static uint8_t USBD_CDCVND_DeInit(USBD_HandleTypeDef *pdev, uint8_t cfgidx)
{
  UNUSED(cfgidx);
  (void)USBD_LL_CloseEP(pdev, CDC_IN_EP);  pdev->ep_in[CDC_IN_EP & 0xFU].is_used = 0U;
  (void)USBD_LL_CloseEP(pdev, CDC_OUT_EP); pdev->ep_out[CDC_OUT_EP & 0xFU].is_used = 0U;
  (void)USBD_LL_CloseEP(pdev, CDC_CMD_EP); pdev->ep_in[CDC_CMD_EP & 0xFU].is_used = 0U; pdev->ep_in[CDC_CMD_EP & 0xFU].bInterval = 0U;
  (void)USBD_LL_CloseEP(pdev, VND_IN_EP);  pdev->ep_in[VND_IN_EP & 0xFU].is_used = 0U;
  (void)USBD_LL_CloseEP(pdev, VND_OUT_EP); pdev->ep_out[VND_OUT_EP & 0xFU].is_used = 0U;
  if (pdev->pClassData) {
    if (CDC_USR(pdev)) CDC_USR(pdev)->DeInit();
    USBD_free(pdev->pClassData);
    pdev->pClassData = NULL;
  }
  return (uint8_t)USBD_OK;
}

static uint8_t USBD_CDCVND_Setup(USBD_HandleTypeDef *pdev, USBD_SetupReqTypedef *req)
{
  USBD_CDC_HandleTypeDef *hcdc = (USBD_CDC_HandleTypeDef*)pdev->pClassData;
  if (!hcdc) return (uint8_t)USBD_FAIL;
  uint16_t status_info = 0; uint16_t len;
  /* ДОБАВЛЕНО: ветка обработки vendor-specific control (GET_STATUS / SOFT/DEEP RESET) */
  if ( (req->bmRequest & USB_REQ_TYPE_MASK) == USB_REQ_TYPE_VENDOR ) {
    VND_LOGF("[SETUP:VND] bm=0x%02X bReq=0x%02X wIndex=%u wLength=%u", (unsigned)req->bmRequest, (unsigned)req->bRequest, (unsigned)req->wIndex, (unsigned)req->wLength);
    /* Принимаем IN GET_STATUS вне зависимости от получателя и номера интерфейса (wIndex),
       чтобы упростить жизнь хостам, где CTRL к Interface может быть ограничен. */
    if ( (req->bmRequest & 0x80U) && req->bRequest == VND_CMD_GET_STATUS ) {
      uint8_t buf[64];
      uint16_t l = vnd_build_status(buf, sizeof(buf));
      if(!l){ USBD_CtlError(pdev, req); return (uint8_t)USBD_FAIL; }
      VND_LOGF("[SETUP:VND] -> STAT %uB", (unsigned)l);
      USBD_CtlSendData(pdev, buf, l);
      return (uint8_t)USBD_OK;
    } else if ( (req->bmRequest & 0x80U) == 0 && req->wLength == 0 && req->bRequest == 0x7Eu ) {
      /* SOFT_RESET: мгновенно подтверждаем статусом и выполняем ресет в фоне */
      g_req_soft_reset = 1; USBD_CtlSendStatus(pdev); return (uint8_t)USBD_OK;
    } else if ( (req->bmRequest & 0x80U) == 0 && req->wLength == 0 && req->bRequest == 0x7Fu ) {
      /* DEEP_RESET: то же, но с переоткрытием EP */
      g_req_deep_reset = 1; USBD_CtlSendStatus(pdev); return (uint8_t)USBD_OK;
    } else if ( (req->bmRequest & 0x80U) == 0 && req->wLength == 0 &&
                (req->bRequest == VND_CMD_START_STREAM || req->bRequest == VND_CMD_STOP_STREAM) ) {
      /* Разрешаем START/STOP по control OUT без данных: эмулируем приём команды Vendor */
      uint8_t cmd = (uint8_t)req->bRequest;
      USBD_VND_DataReceived(&cmd, 1U);
      USBD_CtlSendStatus(pdev);
      return (uint8_t)USBD_OK;
    } else if ( (req->bmRequest & 0x80U) == 0 && req->wLength == 0 &&
                (req->bRequest == VND_CMD_SET_ASYNC_MODE || req->bRequest == VND_CMD_SET_CHMODE ||
                 req->bRequest == VND_CMD_SET_FULL_MODE  || req->bRequest == VND_CMD_SET_PROFILE) ) {
      /* Альтернативный путь: принять параметр через wValue (без data stage) */
      uint8_t tmp[2];
      tmp[0] = (uint8_t)req->bRequest;
      tmp[1] = (uint8_t)(req->wValue & 0xFFU);
      USBD_VND_DataReceived(tmp, 2U);
      USBD_CtlSendStatus(pdev);
      return (uint8_t)USBD_OK;
    } else if ( (req->bmRequest & 0x80U) == 0 && req->wLength == 0 &&
                (req->bRequest == VND_CMD_SET_FRAME_SAMPLES) ) {
      /* SET_FRAME_SAMPLES: 16-битное значение в wValue (LSB first) */
      uint8_t tmp[3];
      tmp[0] = (uint8_t)req->bRequest;
      tmp[1] = (uint8_t)(req->wValue & 0xFFU);
      tmp[2] = (uint8_t)((req->wValue >> 8) & 0xFFU);
      USBD_VND_DataReceived(tmp, 3U);
      USBD_CtlSendStatus(pdev);
      return (uint8_t)USBD_OK;
    } else if ( (req->bmRequest & 0x80U) == 0 && req->wLength > 0 &&
                (req->bRequest == VND_CMD_SET_ASYNC_MODE || req->bRequest == VND_CMD_SET_CHMODE ||
                 req->bRequest == VND_CMD_SET_FULL_MODE  || req->bRequest == VND_CMD_SET_PROFILE) ) {
      /* Принимаем небольшие конфиги по control OUT с телом данных, доставляем в Vendor как будто по Bulk OUT */
      hcdc->CmdOpCode = req->bRequest;
      hcdc->CmdLength = (uint8_t)req->wLength;
      USBD_CtlPrepareRx(pdev, (uint8_t*)hcdc->data, req->wLength); // cast
      return (uint8_t)USBD_OK;
    } else {
      VND_LOGF("[SETUP:VND] unsupported -> STALL");
      USBD_CtlError(pdev, req);
      return (uint8_t)USBD_FAIL;
    }
  }
  switch (req->bmRequest & USB_REQ_TYPE_MASK) {
    case USB_REQ_TYPE_CLASS:
      if (req->wLength != 0U) {
        if (req->bmRequest & 0x80U) {
          if (CDC_USR(pdev)) CDC_USR(pdev)->Control(req->bRequest, (uint8_t*)hcdc->data, req->wLength); // cast
          len = (uint16_t)MIN(CDC_REQ_MAX_DATA_SIZE, req->wLength);
          USBD_CtlSendData(pdev, (uint8_t*)hcdc->data, len); // cast
        } else {
          hcdc->CmdOpCode = req->bRequest; hcdc->CmdLength = (uint8_t)req->wLength;
          USBD_CtlPrepareRx(pdev, (uint8_t*)hcdc->data, req->wLength); // cast
        }
      } else {
        if (CDC_USR(pdev)) CDC_USR(pdev)->Control(req->bRequest, (uint8_t*)req, 0U);
      }
      break;
    case USB_REQ_TYPE_STANDARD:
      switch (req->bRequest) {
        case USB_REQ_GET_STATUS:
          if (pdev->dev_state == USBD_STATE_CONFIGURED) USBD_CtlSendData(pdev, (uint8_t*)&status_info, 2U); else { USBD_CtlError(pdev, req); return (uint8_t)USBD_FAIL; }
          break;
        case USB_REQ_GET_INTERFACE:
          if (pdev->dev_state == USBD_STATE_CONFIGURED) {
            uint8_t cur = 0;
            if (req->wIndex == 2) cur = (uint8_t)g_alt_if2; /* наш Vendor IF */
            /* CDC интерфейсы IF0/IF1 всегда alt0 */
            USBD_CtlSendData(pdev, &cur, 1U);
          } else { USBD_CtlError(pdev, req); return (uint8_t)USBD_FAIL; }
          break;
        case USB_REQ_SET_INTERFACE:
          if (pdev->dev_state != USBD_STATE_CONFIGURED) { USBD_CtlError(pdev, req); return (uint8_t)USBD_FAIL; }
          /* Поддержка altsetting для IF#2: 0 -> idle (закрыть EP), 1 -> stream (открыть EP) */
          if (req->wIndex == 2) {
            uint16_t alt = req->wValue;
            if (alt == 0) {
              /* Остановить пайплайн приложения и закрыть EP */
              printf("[USB_IF2] SET_INTERFACE alt=0 (CLOSE)\r\n");
              vnd_pipeline_stop_reset(0);
              (void)USBD_LL_CloseEP(pdev, VND_IN_EP);  pdev->ep_in[VND_IN_EP & 0x0FU].is_used = 0U;
              (void)USBD_LL_CloseEP(pdev, VND_OUT_EP); pdev->ep_out[VND_OUT_EP & 0x0FU].is_used = 0U;
              g_alt_if2 = 0;
            } else if (alt == 1) {
              /* Открыть EP и реармить приём */
              printf("[USB_IF2] SET_INTERFACE alt=1 (OPEN) speed=%s\r\n", 
                     pdev->dev_speed == USBD_SPEED_HIGH ? "HS" : "FS");
              if (pdev->dev_speed == USBD_SPEED_HIGH) {
                (void)USBD_LL_OpenEP(pdev, VND_IN_EP,  USBD_EP_TYPE_BULK, VND_DATA_HS_MAX_PACKET_SIZE);
                pdev->ep_in[VND_IN_EP & 0x0FU].is_used = 1U;
                (void)USBD_LL_OpenEP(pdev, VND_OUT_EP, USBD_EP_TYPE_BULK, VND_DATA_HS_MAX_PACKET_SIZE);
                pdev->ep_out[VND_OUT_EP & 0x0FU].is_used = 1U;
                (void)USBD_LL_PrepareReceive(pdev, VND_OUT_EP, vnd_rx_buf, VND_DATA_HS_MAX_PACKET_SIZE);
              } else {
                (void)USBD_LL_OpenEP(pdev, VND_IN_EP,  USBD_EP_TYPE_BULK, VND_DATA_FS_MAX_PACKET_SIZE);
                pdev->ep_in[VND_IN_EP & 0x0FU].is_used = 1U;
                (void)USBD_LL_OpenEP(pdev, VND_OUT_EP, USBD_EP_TYPE_BULK, VND_DATA_FS_MAX_PACKET_SIZE);
                pdev->ep_out[VND_OUT_EP & 0x0FU].is_used = 1U;
                (void)USBD_LL_PrepareReceive(pdev, VND_OUT_EP, vnd_rx_buf, VND_DATA_FS_MAX_PACKET_SIZE);
              }
              g_alt_if2 = 1;
              printf("[USB_IF2] EP#3 and EP#83 opened, receiving enabled\r\n");
            }
            USBD_CtlSendStatus(pdev);
            /* Выполним возможные отложенные сервисы */
            USBD_VND_ProcessControlRequests();
            return (uint8_t)USBD_OK;
          }
          /* Иные интерфейсы (CDC IF0/IF1) — поддерживают только alt=0. Подтверждаем статусом без действий. */
          USBD_CtlSendStatus(pdev);
          return (uint8_t)USBD_OK;
        default: USBD_CtlError(pdev, req); return (uint8_t)USBD_FAIL;
      }
      break;
    default: USBD_CtlError(pdev, req); return (uint8_t)USBD_FAIL;
  }
  return (uint8_t)USBD_OK;
}

static uint8_t USBD_CDCVND_DataIn(USBD_HandleTypeDef *pdev, uint8_t epnum)
{
  USBD_CDC_HandleTypeDef *hcdc = (USBD_CDC_HandleTypeDef*)pdev->pClassData;
  if (!hcdc) return (uint8_t)USBD_FAIL;
  PCD_HandleTypeDef *hpcd = (PCD_HandleTypeDef*)pdev->pData;
  if ((CDC_IN_EP & 0x7FU) == epnum) {
    if ((pdev->ep_in[epnum].total_length > 0U) && ((pdev->ep_in[epnum].total_length % hpcd->IN_ep[epnum].maxpacket) == 0U)) {
      pdev->ep_in[epnum].total_length = 0U;
      (void)USBD_LL_Transmit(pdev, epnum, NULL, 0U); /* ZLP */
    } else {
      hcdc->TxState = 0U;
      if (CDC_USR(pdev) && CDC_USR(pdev)->TransmitCplt) CDC_USR(pdev)->TransmitCplt(hcdc->TxBuffer, &hcdc->TxLength, epnum);
    }
  } else if ((VND_IN_EP & 0x7FU) == epnum) {
    /* Vendor Bulk IN: CDC-подобная схема завершения.
       Если длина передачи кратна размеру пакета (MPS), требуется отправить ZLP,
       иначе некоторые хосты (Windows/libusb FS) будут ждать продолжения и в итоге
       получать таймаут. Поведение аналогично CDC. */
    uint16_t tl = pdev->ep_in[epnum].total_length;
    uint16_t mps = hpcd->IN_ep[epnum].maxpacket;
    static uint32_t vnd_dataIn_counter = 0; vnd_dataIn_counter++;
    VND_LOGF("[VND_DataIn:ENTER] ep=%u tl=%u mps=%u busy=%u cnt=%lu\r\n", (unsigned)epnum, (unsigned)tl,(unsigned)mps,(unsigned)vnd_tx_busy,(unsigned long)vnd_dataIn_counter);
    if ((tl > 0U) && ((tl % mps) == 0U)) {
      /* Нужен ZLP для корректного завершения трансфера */
      VND_LOGF("[VND_DataIn] ep=%u total=%u -> SEND ZLP (phase1) cnt=%lu\r\n", (unsigned)epnum, (unsigned)tl, (unsigned long)vnd_dataIn_counter);
      pdev->ep_in[epnum].total_length = 0U;
      (void)USBD_LL_Transmit(pdev, epnum, NULL, 0U); /* ZLP */
      /* НЕ вызываем TxCplt здесь — он будет вызван при следующем DataIn после ZLP */
    } else {
      /* Обычное завершение (либо tl=0 после ZLP, либо tl не кратно mps) */
      VND_LOGF("[VND_DataIn] ep=%u total=%u -> COMPLETE (TxCplt) cnt=%lu\r\n", (unsigned)epnum, (unsigned)tl, (unsigned long)vnd_dataIn_counter);
      pdev->ep_in[epnum].total_length = 0U; /* очистить остаток для надёжности */
      vnd_tx_busy = 0U;
      USBD_VND_TxCplt();
    }
  }
  return (uint8_t)USBD_OK;
}

static uint8_t USBD_CDCVND_DataOut(USBD_HandleTypeDef *pdev, uint8_t epnum)
{
  USBD_CDC_HandleTypeDef *hcdc = (USBD_CDC_HandleTypeDef*)pdev->pClassData;
  if (!hcdc) return (uint8_t)USBD_FAIL;
  if (epnum == (CDC_OUT_EP & 0x7FU)) {
    hcdc->RxLength = USBD_LL_GetRxDataSize(pdev, epnum);
    if (CDC_USR(pdev) && CDC_USR(pdev)->Receive) CDC_USR(pdev)->Receive(hcdc->RxBuffer, &hcdc->RxLength);
  } else if (epnum == (VND_OUT_EP & 0x7FU)) {
    vnd_rx_len = USBD_LL_GetRxDataSize(pdev, epnum);
    /* D-Cache (STM32H7): инвалидировать диапазон RX перед чтением, иначе возможны "старые" данные */
#if defined (SCB_InvalidateDCache_by_Addr)
    if (vnd_rx_len > 0) {
      uintptr_t addr = (uintptr_t)vnd_rx_buf;
      uint32_t inv_addr = (uint32_t)(addr & ~((uintptr_t)31U));
      uint32_t inv_len  = (uint32_t)(((addr + vnd_rx_len + 31U) & ~((uintptr_t)31U)) - inv_addr);
      SCB_InvalidateDCache_by_Addr((uint32_t*)inv_addr, (int32_t)inv_len);
    }
#endif
    /* Мини-лог: подтверждаем приём однобайтовой команды */
    if (vnd_rx_len > 0) {
      printf("[CMD] 0x%02X len=%lu\r\n", (unsigned)vnd_rx_buf[0], (unsigned long)vnd_rx_len);
    }
    USBD_VND_DataReceived(vnd_rx_buf, vnd_rx_len);
    /* Небольшой memory barrier для надёжности перед реармом приёма */
    {
      USB_OTG_GlobalTypeDef *usb_reg = (USB_OTG_GlobalTypeDef *)USB1_OTG_HS_PERIPH_BASE;
      (void)usb_reg->GINTSTS; /* volatile read */
    }
    /* Реармим */
    if (pdev->dev_speed == USBD_SPEED_HIGH)
      (void)USBD_LL_PrepareReceive(pdev, VND_OUT_EP, vnd_rx_buf, VND_DATA_HS_MAX_PACKET_SIZE);
    else
      (void)USBD_LL_PrepareReceive(pdev, VND_OUT_EP, vnd_rx_buf, VND_DATA_FS_MAX_PACKET_SIZE);
  }
  return (uint8_t)USBD_OK;
}

static uint8_t USBD_CDCVND_EP0_RxReady(USBD_HandleTypeDef *pdev)
{
  USBD_CDC_HandleTypeDef *hcdc = (USBD_CDC_HandleTypeDef*)pdev->pClassData;
  if (!hcdc) return (uint8_t)USBD_FAIL;
  if (hcdc->CmdOpCode != 0xFFU) {
    uint8_t op = hcdc->CmdOpCode;
    uint8_t len = hcdc->CmdLength;
    /* Если это один из наших vendor control OUT запросов с полезной нагрузкой —
       формируем буфер [opcode | payload] и передаём в общий обработчик Vendor. */
    if (op == VND_CMD_SET_ASYNC_MODE ||
        op == VND_CMD_SET_CHMODE    ||
        op == VND_CMD_SET_FULL_MODE ||
        op == VND_CMD_SET_PROFILE) {
      uint32_t tot = (uint32_t)len + 1U;
      if (tot > sizeof(vnd_rx_buf)) tot = sizeof(vnd_rx_buf); /* страхуемся от выхода за границы */
      vnd_rx_buf[0] = op;
      if (tot > 1U) {
        memcpy(&vnd_rx_buf[1], (uint8_t*)hcdc->data, (size_t)(tot - 1U)); // cast
      }
      USBD_VND_DataReceived(vnd_rx_buf, tot);
    } else {
      /* Иначе — это обычная CDC class команда */
      if (CDC_USR(pdev) && CDC_USR(pdev)->Control) {
        CDC_USR(pdev)->Control(op, (uint8_t*)hcdc->data, len); // cast
      }
    }
    hcdc->CmdOpCode = 0xFFU;
  }
  return (uint8_t)USBD_OK;
}

static uint8_t *USBD_CDCVND_GetFSCfgDesc(uint16_t *length) { *length = sizeof(USBD_CDCVND_CfgFSDesc); return USBD_CDCVND_CfgFSDesc; }
static uint8_t *USBD_CDCVND_GetHSCfgDesc(uint16_t *length) { *length = sizeof(USBD_CDCVND_CfgHSDesc); return USBD_CDCVND_CfgHSDesc; }
static uint8_t *USBD_CDCVND_GetOtherSpeedCfgDesc(uint16_t *length) { *length = sizeof(USBD_CDCVND_OtherSpeedCfgDesc); return USBD_CDCVND_OtherSpeedCfgDesc; }
static uint8_t *USBD_CDCVND_GetDeviceQualifierDescriptor(uint16_t *length) { *length = USB_LEN_DEV_QUALIFIER_DESC; return USBD_CDCVND_DeviceQualifierDesc; }

/* Конец файла */
