#ifndef TEMP_SENSOR_H
#define TEMP_SENSOR_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Инициализация температурного датчика
 *
 * Копирует калибровочные константы из FLASH памяти.
 * ADC3 должен быть предварительно инициализирован MX_ADC3_Init().
 */
void temp_sensor_init(void);

/**
 * @brief Прочитать сырое значение ADC с DTS канала
 *
 * @return uint16_t Сырое 12-бит значение ADC (0..4095)
 */
uint16_t temp_sensor_read_raw(void);

/**
 * @brief Получить текущую температуру кристалла в градусах Цельсия
 *
 * Операция займёт ~500 микросекунд (одиночная ADC конверсия).
 * 
 * @return int16_t Температура в °C (диапазон: -40..+85)
 */
int16_t temp_sensor_read_celsius(void);

/**
 * @brief Получить состояние готовности датчика
 *
 * @return uint8_t 1 если датчик инициализирован, 0 иначе
 */
uint8_t temp_sensor_is_ready(void);

/**
 * @brief Вывести диагностику датчика температуры
 *
 * Печатает калибровочные значения, сырое ADC значение и рассчитанную температуру.
 */
void temp_sensor_print_diagnostic(void);

#ifdef __cplusplus
}
#endif

#endif // TEMP_SENSOR_H
