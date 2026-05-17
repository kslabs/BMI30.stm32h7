/**
 * @file temp_sensor.c
 * @brief STM32H723 встроенный датчик температуры (DTS) через ADC3
 * @author Код сгенерирован автоматически
 * @date 2026
 */

#include "main.h"
#include "temp_sensor.h"
#include <stdio.h>

/* === ADC3 Handler экспорт === */
extern ADC_HandleTypeDef hadc3;

/* === Калибровочные константы (TS_CAL1 и TS_CAL2) === */
#define TS_CAL1_ADDR ((uint16_t*)0x08FFF814)  /* 30°C */
#define TS_CAL2_ADDR ((uint16_t*)0x08FFF818)  /* 130°C */

/* === Кэш калибровочных значений === */
static uint16_t g_ts_cal1 = 0;  /* ADC значение при 30°C */
static uint16_t g_ts_cal2 = 0;  /* ADC значение при 130°C */
static uint8_t g_dts_ready = 0; /* Флаг инициализации */

/**
 * @brief Инициализация температурного датчика
 *
 * Копирует калибровочные константы из FLASH памяти.
 * ADC3 уже должен быть инициализирован в MX_ADC3_Init().
 *
 * @retval None
 */
void temp_sensor_init(void)
{
  /* Считываем калибровочные значения из FLASH */
  g_ts_cal1 = *TS_CAL1_ADDR;
  g_ts_cal2 = *TS_CAL2_ADDR;
  
  printf("[DTS] Calibration: CAL1(30°C)=%u CAL2(130°C)=%u\r\n", 
         (unsigned)g_ts_cal1, (unsigned)g_ts_cal2);
  
  g_dts_ready = 1;
}

/**
 * @brief Прочитать сырое значение ADC с DTS канала
 *
 * Выполняет одиночную конверсию ADC3 для DTS и возвращает 12-бит результат.
 * Операция блокирует поток на ~500 микросекунд (387.5 cycles сэмплирования + конверсия).
 *
 * @return uint16_t Сырое 12-бит значение ADC (0..4095)
 */
uint16_t temp_sensor_read_raw(void)
{
  uint32_t adc_value = 0;
  
  if (!g_dts_ready) {
    printf("[DTS] WARNING: not initialized, returning 0\r\n");
    return 0;
  }
  
  /* Запуск одиночной конверсии (SELECT + CONTINUOUS=0 в init) */
  if (HAL_ADC_Start(&hadc3) != HAL_OK) {
    printf("[DTS] ERROR: ADC start failed\r\n");
    return 0;
  }
  
  /* Ожидание завершения конверсии с таймаутом 1 мс */
  if (HAL_ADC_PollForConversion(&hadc3, 1) != HAL_OK) {
    printf("[DTS] ERROR: conversion timeout\r\n");
    return 0;
  }
  
  /* Чтение результата из DR */
  adc_value = HAL_ADC_GetValue(&hadc3);
  
  /* Остановка ADC */
  if (HAL_ADC_Stop(&hadc3) != HAL_OK) {
    printf("[DTS] WARNING: ADC stop failed\r\n");
  }
  
  return (uint16_t)adc_value;
}

/**
 * @brief Преобразовать ADC значение DTS в температуру (°C)
 *
 * Использует линейную интерполяцию между двумя калибровочными точками:
 * - TS_CAL1 при 30°C (из FLASH @ 0x08FFF814)
 * - TS_CAL2 при 130°C (из FLASH @ 0x08FFF818)
 *
 * Формула:
 *   T = 30 + ((TS_DATA - TS_CAL1) / (TS_CAL2 - TS_CAL1)) * (130 - 30)
 *   T = 30 + ((TS_DATA - TS_CAL1) * 100) / (TS_CAL2 - TS_CAL1)
 *
 * @param adc_value Сырое значение ADC (12-бит)
 * @return int16_t Температура в °C (со знаком)
 */
static int16_t temp_sensor_adc_to_celsius(uint16_t adc_value)
{
  int16_t temp_c = 0;
  int32_t numerator = 0;
  int32_t denominator = 0;
  
  if (!g_dts_ready || g_ts_cal1 >= g_ts_cal2) {
    printf("[DTS] ERROR: invalid calibration (CAL1=%u >= CAL2=%u)\r\n",
           (unsigned)g_ts_cal1, (unsigned)g_ts_cal2);
    return 0;  /* Ошибка калибровки */
  }
  
  /* Линейная интерполяция между (TS_CAL1, 30°C) и (TS_CAL2, 130°C) */
  denominator = (int32_t)(g_ts_cal2 - g_ts_cal1);
  numerator = (int32_t)(adc_value - g_ts_cal1) * 100;
  
  if (denominator != 0) {
    temp_c = (int16_t)(30 + (numerator / denominator));
  } else {
    temp_c = 30;  /* Fallback */
  }
  
  /* Ограничиваем диапазон: -40..+85°C (характеристики DTS) */
  if (temp_c < -40) temp_c = -40;
  if (temp_c > 85) temp_c = 85;
  
  return temp_c;
}

/**
 * @brief Прочитать температуру кристалла в градусах Цельсия
 *
 * Выполняет одиночную конверсию ADC и преобразует результат в температуру.
 * Операция займёт ~500 микросекунд.
 *
 * @return int16_t Температура в °C (диапазон: -40..+85)
 */
int16_t temp_sensor_read_celsius(void)
{
  uint16_t adc_raw = 0;
  int16_t temp_c = 0;
  
  /* Прочитать сырое значение ADC с DTS */
  adc_raw = temp_sensor_read_raw();
  
  /* Преобразовать в Celsius */
  temp_c = temp_sensor_adc_to_celsius(adc_raw);
  
  return temp_c;
}

/**
 * @brief Получить текущее состояние готовности датчика
 *
 * @return uint8_t 1 если датчик инициализирован, 0 иначе
 */
uint8_t temp_sensor_is_ready(void)
{
  return g_dts_ready;
}

/**
 * @brief Отпечать диагностику датчика температуры
 *
 * @return None
 */
void temp_sensor_print_diagnostic(void)
{
  uint16_t adc_raw = 0;
  int16_t temp_c = 0;
  
  if (!g_dts_ready) {
    printf("[DTS] Sensor not initialized\r\n");
    return;
  }
  
  adc_raw = temp_sensor_read_raw();
  temp_c = temp_sensor_adc_to_celsius(adc_raw);
  
  printf("[DTS] Diagnostic: CAL1=%u CAL2=%u RAW=%u T=%d°C\r\n",
         (unsigned)g_ts_cal1, (unsigned)g_ts_cal2, 
         (unsigned)adc_raw, (int)temp_c);
}

    // T = 30 + (TS_CAL1 - raw) * 100 / (TS_CAL2 - TS_CAL1)
    // где 100 = (130 - 30) температурный диапазон
    
    int32_t temp = 30;
    if (TS_CAL2 != TS_CAL1) {
        int32_t delta = (int32_t)TS_CAL1 - (int32_t)raw;
        int32_t range = (int32_t)TS_CAL2 - (int32_t)TS_CAL1;
        temp += (delta * 100) / range;
    }
    
    return (int16_t)temp;
}
