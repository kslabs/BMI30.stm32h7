/*
  Быстрая памятка (Windows/PowerShell): сборка и прошивка
  -------------------------------------------------------
  Предпосылки:
  - Проект сгенерирован CubeIDE; папка Debug содержит makefile.
  - Для прошивки используем STM32CubeProgrammer (STM32_Programmer_CLI в PATH).

  Сборка (из корня репозитория):
    # перейти в папку отладочной сборки
    cd Debug
    # запустить сборку (можно без -j или указать нужное число потоков)
    make -j4 all
    # целевой ELF: Debug\BMI30.stm32h7.elf

  Очистка:
    cd Debug
    make clean

  Прошивка (через ST-Link, SWD):
    # вариант 1: прошить и перезапустить
    STM32_Programmer_CLI.exe -c port=SWD freq=4000 -w Debug\BMI30.stm32h7.elf -v -rst
    # вариант 2: только запись без reset
    STM32_Programmer_CLI.exe -c port=SWD freq=4000 -w Debug\BMI30.stm32h7.elf -v

  Примечания:
  - Если make не найден: запустите сборку из STM32CubeIDE или добавьте утилиты
    (GNU Make) в PATH. В среде CubeIDE консольная сборка также доступна из папки Debug.
  - Для диагностики USB используйте CDC-лог (Virtual COM) и GET_STATUS по EP0.
  - При изменениях в USB логике: после прошивки выполните START, убедитесь,
    что появляются строки TRY_A/SEND/TXCPLT в CDC и хост получает кадры A/B.
*/
/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2025 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32h7xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "lcd.h"
/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

void HAL_TIM_MspPostInit(TIM_HandleTypeDef *htim);

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */
// Экспорт дескрипторов периферии для модулей
extern ADC_HandleTypeDef hadc1;
extern ADC_HandleTypeDef hadc2;
// Экспорт системного счётчика SysTick тиков
extern volatile uint32_t systick_heartbeat;
/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define Led_Test_Pin GPIO_PIN_3
#define Led_Test_GPIO_Port GPIOE
#define LCD_Led_Pin GPIO_PIN_10
#define LCD_Led_GPIO_Port GPIOE
#define LCD_CS_Pin GPIO_PIN_11
#define LCD_CS_GPIO_Port GPIOE
#define LCD_SCL_Pin GPIO_PIN_12
#define LCD_SCL_GPIO_Port GPIOE
#define LCD_WR_RS_Pin GPIO_PIN_13
#define LCD_WR_RS_GPIO_Port GPIOE
#define LCD_SDA_Pin GPIO_PIN_14
#define LCD_SDA_GPIO_Port GPIOE
#define Data_ready_GPIO22_Pin GPIO_PIN_8
#define Data_ready_GPIO22_GPIO_Port GPIOD

/* USER CODE BEGIN Private defines */
// Совместимость: старые имена DATA_READY_*
#ifndef DATA_READY_Pin
#define DATA_READY_Pin        Data_ready_GPIO22_Pin
#define DATA_READY_GPIO_Port  Data_ready_GPIO22_GPIO_Port
#endif
// Определения для пина сброса LCD (защищены от перезаписи CubeMX)
#define LCD_RST_Pin GPIO_PIN_15
#define LCD_RST_GPIO_Port GPIOE

// --- Профили потоков ADC (буфер/частота) ---
// Профиль B (default): f_buf=300 Гц, N=912 (Fs=273600 Гц)
// Профиль C (high):    f_buf=300 Гц, N=944 (Fs=283200 Гц)
// Профиль D (max):     f_buf=300 Гц, N=976 (Fs=292800 Гц) — ближе к пределу USB, тестовый
// Legacy (не активируем сейчас): f_buf=200 Гц, N=1360 (Fs=272000 Гц)

typedef struct {
    uint16_t samples_per_buf;  // N
    uint16_t buf_rate_hz;      // f_buf
    uint32_t fs_hz;            // Fs = N * f_buf
} adc_stream_profile_t;

enum {
    ADC_PROFILE_A_200HZ = 0,
    ADC_PROFILE_B_DEFAULT = 1,
    ADC_PROFILE_C_HIGH    = 2,
    ADC_PROFILE_D_MAX     = 3,
    ADC_PROFILE_COUNT
};

#define MAX_FRAME_SAMPLES 1360u   // Максимум из поддерживаемых профилей (для статических буферов)
#define FIFO_FRAMES       8u      // Глубина FIFO (кратно 4: half/full DMA = 4 кадра)

// Компиляционный дефолт (будет заменён рантайм профилем)
#define FRAME_SAMPLES_DEFAULT 912u

// Текущий активный профиль (обновляется вызовом set)
uint8_t adc_stream_get_profile(void);
int     adc_stream_set_profile(uint8_t prof_id); // 0 при успехе
uint16_t adc_stream_get_active_samples(void);    // N текущего профиля
uint16_t adc_stream_get_buf_rate(void);          // f_buf
uint32_t adc_stream_get_fs(void);                // Fs

// Совместимость со старым кодом (использующим FRAME_SAMPLES)
#define ADC_BUFFER_SIZE FRAME_SAMPLES_DEFAULT

// Подключаем модуль потоков ADC
#include "adc_stream.h"

// Диагностика HardFault (заполняется обработчиком) 
extern volatile uint32_t hardfault_r0;
extern volatile uint32_t hardfault_r1;
extern volatile uint32_t hardfault_r2;
extern volatile uint32_t hardfault_r3;
extern volatile uint32_t hardfault_r12;
extern volatile uint32_t hardfault_lr;
extern volatile uint32_t hardfault_pc;
extern volatile uint32_t hardfault_psr;
extern volatile uint32_t hardfault_active; // 1 когда данные заполнены
/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
