#ifndef TUSB_H_
#define TUSB_H_
/*
 * ВРЕМЕННЫЙ STUB заголовок TinyUSB.
 * Этот файл создан потому, что исходный tusb.h не найден в проекте.
 * Замените/удалите его после корректного добавления библиотеки TinyUSB.
 */
#include <stdint.h>
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif

static inline void tud_init(uint8_t rhport) { (void)rhport; }
static inline void tud_task(void) { }
static inline bool tud_mounted(void) { return false; }
static inline uint32_t tud_vendor_write_available(void) { return 0; }
static inline uint32_t tud_vendor_write(const void *buf, uint32_t count) { (void)buf; return count; }
static inline void tud_vendor_flush(void) { }
static inline uint32_t tud_vendor_read(void *buf, uint32_t bufsize) { (void)buf; (void)bufsize; return 0; }
static inline void tusb_int_handler(uint8_t rhport, bool in_isr) { (void)rhport; (void)in_isr; }

#ifdef __cplusplus
}
#endif
#endif /* TUSB_H_ */
