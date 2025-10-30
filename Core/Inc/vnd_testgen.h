#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* Возвращает 1, если есть новый сгенерированный кадр для тестового режима.
 * Выдаёт указатели на внутренние буферы ch0/ch1 и количество доступных сэмплов.
 * Буферы действительны до следующего вызова генератора. */
int vnd_testgen_try_consume_latest(uint16_t **out_ch0, uint16_t **out_ch1, uint16_t *out_samples);

/* Генерация пилообразного тестового кадра; вызывать из TIM2 IRQ (@200 Hz) */
void vnd_generate_test_sawtooth(void);

#ifdef __cplusplus
}
#endif
