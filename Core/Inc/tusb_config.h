#ifndef TUSB_CONFIG_H
#define TUSB_CONFIG_H

// Конфигурация TinyUSB. Файл компилируется только при определении USE_TINYUSB.
#ifdef USE_TINYUSB

#include "tusb_option.h"

// MCU и ОС
#define CFG_TUSB_MCU            OPT_MCU_STM32H7
#define CFG_TUSB_OS             OPT_OS_NONE

// Выбор порта и режим (USB1 OTG HS с внутренним FS PHY → FullSpeed)
#define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)

// Отладка (0 выключено)
#define CFG_TUSB_DEBUG          0

// Планировщик (без ОС)
#define CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_ALIGN      __attribute__ ((aligned(4)))

// Контроллер устройства (Device)
#define CFG_TUD_ENABLED         1
#define CFG_TUD_ENDPOINT0_SIZE  64

// Классы
#define CFG_TUD_CDC             1
#define CFG_TUD_HID             0
#define CFG_TUD_MSC             0
#define CFG_TUD_MIDI            0
#define CFG_TUD_VENDOR          0

// CDC буферы (FS, 64 байта EP) — увеличены для стабильной непрерывной передачи
#define CFG_TUD_CDC_RX_BUFSIZE  4096
#define CFG_TUD_CDC_TX_BUFSIZE  8192
#define CFG_TUD_CDC_EP_BUFSIZE  64

#endif // USE_TINYUSB

#endif // TUSB_CONFIG_H