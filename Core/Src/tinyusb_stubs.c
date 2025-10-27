// ВРЕМЕННЫЕ STUB-реализации TinyUSB, чтобы проект собирался без
// добавления исходников стекa TinyUSB. НЕ ДАЮТ РАБОЧЕГО USB.
// Удалить этот файл после подключения настоящего TinyUSB.

#include <stdint.h>
#include <stdbool.h>

// Заглушки TinyUSB теперь компилируются ТОЛЬКО если НЕ задан USE_TINYUSB.
#ifndef USE_TINYUSB
#warning "Используются STUB функции TinyUSB (USB CDC не работает)"

// --- Минимальный набор недостающих функций ---

// CDC интерфейс (один интерфейс 0)
bool tud_cdc_n_connected(uint8_t itf) { (void)itf; return false; }
bool tud_mounted(void) { return false; }

uint32_t tud_cdc_n_write(uint8_t itf, void const* buffer, uint32_t bufsize) {
    (void)itf; (void)buffer; return bufsize; // делаем вид что записали
}
bool tud_cdc_n_write_flush(uint8_t itf) { (void)itf; return true; }

// Инициализация root hub порта
bool tud_rhport_init(uint8_t rhport) { (void)rhport; return true; }

// Главный таск (расширенный) — просто заглушка
void tud_task_ext(uint32_t timeout_ms, bool in_isr) { (void)timeout_ms; (void)in_isr; }

// DCD (Device Controller Driver) IRQ обработчик (HS FS-порт)
void dcd_int_handler(uint8_t rhport) { (void)rhport; }

// Возможны дополнительные вызовы позже — добавлять по списку линковщика.

#endif // !USE_TINYUSB