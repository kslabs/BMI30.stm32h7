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
extern TIM_HandleTypeDef htim16;
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
#ifndef FORCE_BL_GPIO
#define FORCE_BL_GPIO 0
#endif

// Совместимость: старые имена DATA_READY_*
#ifndef DATA_READY_Pin
#define DATA_READY_Pin        Data_ready_GPIO22_Pin
#define DATA_READY_GPIO_Port  Data_ready_GPIO22_GPIO_Port
#endif
// Определения для пина сброса LCD (защищены от перезаписи CubeMX)
#define LCD_RST_Pin GPIO_PIN_15
#define LCD_RST_GPIO_Port GPIOE

// Синхронизация: вход (slave) и выход (master)
#define SYNC_IN_Pin GPIO_PIN_5
#define SYNC_IN_GPIO_Port GPIOD
#define SYNC_OUT_Pin GPIO_PIN_8
#define SYNC_OUT_GPIO_Port GPIOB

// RS-485 sync bus
#define RS485_RDE_Pin GPIO_PIN_3
#define RS485_RDE_GPIO_Port GPIOD

// Optical sensor I/O
#define OPTIC_RX_Pin GPIO_PIN_0
#define OPTIC_RX_GPIO_Port GPIOD
#define OPTIC_TX_Pin GPIO_PIN_10
#define OPTIC_TX_GPIO_Port GPIOA

#define RS485_SYNC_BYTE 0xA5u

// MCP4261 через аппаратный SPI1: PB3/PB4/PB5 (SCK/MISO/MOSI, AF5) + PA15 (NSS, GPIO)
#define MCP4261_SPI_NSS_Pin GPIO_PIN_15
#define MCP4261_SPI_NSS_GPIO_Port GPIOA
#define MCP4261_SPI_SCK_Pin GPIO_PIN_3
#define MCP4261_SPI_SCK_GPIO_Port GPIOB
#define MCP4261_SPI_MISO_Pin GPIO_PIN_4
#define MCP4261_SPI_MISO_GPIO_Port GPIOB
#define MCP4261_SPI_MOSI_Pin GPIO_PIN_5
#define MCP4261_SPI_MOSI_GPIO_Port GPIOB

/* Базовая AUTO-цель фазы в семплах. Компенсация длительности RS485 sync-байта
  добавляется отдельно, потому что slave измеряет фазу уже после приема пакета. */
#define SYNC_TARGET_PHASE_SAMPLES (10)

int32_t tim15_get_default_target_phase_ticks(void);
void rs485_sync_on_buffer_complete(uint8_t parity);
uint32_t rs485_get_master_claim_delay_ms(void);
uint8_t optic_sensor_get_state(void);
uint8_t optic_any_sensor_active(void);
uint8_t optic_sensor_set_hold_seconds(uint8_t seconds);
uint8_t optic_sensor_get_hold_seconds(void);
uint16_t optic_sensor_set_hold_deciseconds(uint16_t deciseconds);
uint16_t optic_sensor_get_hold_deciseconds(void);
uint8_t optic_tx_set_power(uint8_t power);
uint8_t optic_tx_get_power(void);
void optic_tx_refresh_enable(void);
uint8_t dynamic_led_set_pattern(uint8_t pattern_id);
uint8_t dynamic_led_get_pattern(void);
uint8_t rs485_status_set_det_adc_bits(uint8_t bits);
uint8_t rs485_status_get_det_adc_bits(void);
uint8_t rs485_status_get_snapshot(uint8_t *local_status,
                                  uint8_t *node_count,
                                  uint32_t *seen_mask,
                                  uint8_t *status_bytes,
                                  uint8_t max_status_bytes);
uint8_t rs485_status_master_optic_active(void);
uint8_t rs485_status_get_master_snapshot(uint8_t *master_status,
                                         uint16_t *age_ms,
                                         uint8_t *flags);
void rs485_set_local_node_id_from_host(uint8_t node_id);
uint8_t rs485_role_get_local_persisted_mode(uint8_t *mode);
uint32_t rs485_node_get_conflict_mask(void);
uint8_t rs485_node_local_id_conflict(void);
uint8_t rs485_multiple_master_detected(void);

/* Persistent RS485 role selection. MASTER and the designated sensor SLAVE are
 * identified by MCU UID, so every board stores the same network-wide state. */
#define RS485_ROLE_PERSIST_MASTER_VALID 0x01u
#define RS485_ROLE_PERSIST_NODE_VALID   0x02u
#define RS485_ROLE_PERSIST_SLAVE_EPOCH  0x04u
#define RS485_ROLE_PERSIST_SLAVE_VALID  0x08u
#define RS485_ROLE_PERSIST_SLAVE_UID64  0x10u
#define RS485_ROLE_PERSIST_LOCAL_SLAVE  0x20u

typedef struct {
  uint8_t flags;
  uint8_t node_id;
  uint8_t slave_id_high_water;
  uint8_t reserved;
  uint32_t master_assigned_unix_s;
  uint16_t master_assigned_millis;
  uint16_t reserved2;
  uint8_t master_uid[12];
  uint32_t slave_assigned_unix_s;
  uint16_t slave_assigned_millis;
  uint8_t selected_slave_node_id;
  uint8_t reserved3;
  uint8_t slave_uid[12];
} rs485_role_persist_state_t;

void rs485_role_persist_export(rs485_role_persist_state_t *out);
void rs485_role_persist_import(const rs485_role_persist_state_t *state);
uint8_t rs485_role_assign_master_from_host(uint64_t assigned_unix_ms);
uint8_t rs485_role_assign_slave_from_host(uint64_t assigned_unix_ms);

#define RS485_SELECTED_SLAVE_FLAG_VALID 0x01u
#define RS485_SELECTED_SLAVE_FLAG_LOCAL 0x02u
#define RS485_SELECTED_SLAVE_FLAG_FRESH 0x04u
typedef struct {
  uint8_t flags;
  uint8_t node_id;
  uint8_t sensor_status; /* bit5 optic, bit6 DetADC1, bit7 DetADC2 */
  uint16_t age_ms;
} rs485_selected_slave_snapshot_t;

void rs485_role_get_selected_slave_snapshot(rs485_selected_slave_snapshot_t *out);
typedef struct {
  uint8_t node_id;
  uint16_t flags;
  char short_id[10]; /* 9 uppercase hex digits + NUL */
  uint16_t rpi_number;
  uint8_t ip4[4];    /* network order: a.b.c.d */
  uint32_t seen_page_mask;
  uint32_t last_ms;
} rs485_identity_snapshot_t;

#define RS485_IDENTITY_FLAG_SHORT_VALID 0x0001u
#define RS485_IDENTITY_FLAG_IP_VALID    0x0002u
#define RS485_IDENTITY_FLAG_COMPLETE    0x0004u
#define RS485_IDENTITY_FLAG_LOCAL       0x0008u
#define RS485_IDENTITY_FLAG_RECENT      0x0010u
#define RS485_IDENTITY_FLAG_SCAN_ACTIVE 0x0020u
#define RS485_IDENTITY_FLAG_SELECTED_SLAVE 0x0040u
#define RS485_IDENTITY_FLAG_MASTER      0x0080u
#define RS485_IDENTITY_FLAG_NODE_CONFLICT 0x0100u
#define RS485_IDENTITY_FLAG_RPI_NUMBER_VALID 0x0200u
#define RS485_IDENTITY_FLAG_DEVICE_ID_ASSIGNED 0x0400u

void rs485_identity_set_local_ip4(const uint8_t ip4[4]);
void rs485_identity_set_local_rpi_info(uint16_t rpi_number,
                                       const uint8_t ip4[4]);
void rs485_identity_request_scan(void);
uint8_t rs485_identity_get_snapshot(uint8_t node_id, rs485_identity_snapshot_t *out);

#define RS485_SENSOR_FLAG_VALID   0x0001u
#define RS485_SENSOR_FLAG_LOCAL   0x0002u
#define RS485_SENSOR_FLAG_RECENT  0x0004u
#define RS485_SENSOR_FLAG_MASTER  0x0008u
typedef struct {
  uint8_t device_id;
  uint8_t last_changed_index;
  uint16_t flags;
  uint16_t sensor_bits;
  uint16_t reserved;
  uint32_t last_change_ms;
} rs485_sensor_snapshot_t;

uint8_t rs485_status_set_controlled_sensor(uint8_t sensor_index,
                                           uint8_t active);
uint8_t rs485_sensor_get_snapshot(uint8_t device_id,
                                  rs485_sensor_snapshot_t *out);
uint8_t rs485_sync_has_active_peer(void);
uint8_t rs485_sync_phase_locked(void);

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
  ADC_PROFILE_A_200HZ = 0,       /* 1200 @ 200Hz (Fs≈240kHz) */
  ADC_PROFILE_B_DEFAULT = 1,     /*  912 @ 300Hz (Fs≈273.6kHz) */
  ADC_PROFILE_C_HIGH    = 2,     /*  944 @ 300Hz (Fs≈283.2kHz) */
  ADC_PROFILE_D_MAX     = 3,     /*  976 @ 300Hz (Fs≈292.8kHz) */
  ADC_PROFILE_E_400HZ   = 4,     /*  680 @ 400Hz (Fs≈272kHz) HIGH-FPS mode */
  ADC_PROFILE_COUNT
};

#define MAX_FRAME_SAMPLES 1360u   // Максимум из поддерживаемых профилей (для статических буферов)
#define FIFO_FRAMES       32u     // Глубина FIFO: увеличена с 8 до 32 для диагностики и предобработки (16 бит ADC)

// Компиляционный дефолт (будет заменён рантайм профилем)
#define FRAME_SAMPLES_DEFAULT 1200u

// Текущий активный профиль (обновляется вызовом set)
uint8_t adc_stream_get_profile(void);
int     adc_stream_set_profile(uint8_t prof_id); // 0 при успехе
uint16_t adc_stream_get_active_samples(void);    // N текущего профиля
uint16_t adc_stream_get_buf_rate(void);          // f_buf
uint32_t adc_stream_get_fs(void);                // Fs
void tim15_sync_apply_nominal_arr(uint32_t nominal_arr);

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
