// Заглушка после удаления TinyUSB. Реальный стек: STM32Cube USB CDC.
#include "usb_tinyusb_bridge.h"
#include <stdbool.h>

bool usb_tinyusb_configured(void) { return false; }
void usb_tinyusb_init(void) {}
void usb_tinyusb_task(void) {}
void usb_fs_phy_manual_init(void) {}
// usb_cdc_ll_write реализован в usb_cdc_ll_st.c