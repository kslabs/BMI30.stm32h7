/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
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
/* Includes: восстановление базовых заголовков и прототипов */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "main.h"
#include "build_info.h"
#include "usb_device.h"
#include "usbd_def.h"
#include "usbd_core.h"
#include "usbd_desc.h"
#include "usb_vendor_app.h"
#include "usb_cdc_proto.h"
/* Для ранних CDC-тестов (COM4) */
#include "usbd_cdc_if.h"

/* Глобальные хэндлы периферии (стандарт для CubeMX, ранее отсутствовали в файле) */
ADC_HandleTypeDef hadc1;
ADC_HandleTypeDef hadc2;
SPI_HandleTypeDef hspi2;
SPI_HandleTypeDef hspi4;
TIM_HandleTypeDef htim1;
TIM_HandleTypeDef htim2;
TIM_HandleTypeDef htim3;
TIM_HandleTypeDef htim6;
TIM_HandleTypeDef htim15;
IWDG_HandleTypeDef hiwdg1;
UART_HandleTypeDef huart1;
DAC_HandleTypeDef  hdac1;
DMA_HandleTypeDef  hdma_adc1;
DMA_HandleTypeDef  hdma_adc2;

/* Экспорт переменной устройства USB (определена в usb_device.c) */
extern USBD_HandleTypeDef hUsbDeviceHS;
extern volatile uint32_t g_usb_last_sof_ms; /* объявлен в USB_DEVICE/Target/usbd_conf.c */

/* Служебные переменные для диагностики/цикла (были использованы ниже) */
volatile uint32_t tim6_irq_count = 0;
volatile uint8_t  tim6_led_toggled_flag = 0;
volatile uint32_t tim6_led_toggle_counter = 0;
volatile uint32_t main_loop_heartbeat = 0;
volatile uint32_t last_heartbeat_ms = 0;
volatile uint32_t loop_cycle_accum = 0;
volatile uint32_t loop_cycle_count = 0;
volatile uint32_t loop_cycle_last_report_ms = 0;
volatile uint32_t loop_cycle_last_avg = 0;
volatile uint8_t  need_recovery = 0;
volatile uint8_t  need_usb_status_refresh = 0;
volatile uint32_t systick_heartbeat = 0; /* глобальный счётчик SysTick для stm32h7xx_it.c */
uint8_t star_visible = 0;
uint8_t auto_stream_started = 0;
uint8_t init_messages_ready = 0;
/* Охраняемая «флаг-структура» для need_recovery с сигнатурами по краям */
typedef struct {
  uint32_t c1;                 /* 0xDEADBEEF */
  volatile uint32_t flag;      /* флаг запроса восстановления */
  uint32_t c2;                 /* 0xA55AA55A */
} need_recovery_guard_t;
need_recovery_guard_t need_recovery_guard = { 0xDEADBEEFUL, 0u, 0xA55AA55AUL };

/* Заглушки для диагностических макросов/утилит, чтобы избежать ошибок линковки в SAFE режимах */
#ifndef CHECK
#define CHECK(expr, errcode) do { \
  HAL_StatusTypeDef _st = (expr); \
  if (_st != HAL_OK) { err_code = (errcode); Error_Handler(); } \
} while(0)
#endif
static volatile int err_code = 0;
#ifndef STAGE
#define STAGE(idx, tag) do{ (void)(idx); (void)(tag); }while(0)
#endif
static char stage_log[32][16];
static int  stage_count = 0;
static void FlushStageLog(void) { /* no-op в безопасном режиме */ }

/* Дефолты для флагов сборки, чтобы они не оставались «неопределёнными» */
#ifndef MINIMAL_BRINGUP
#define MINIMAL_BRINGUP 0
#endif
#ifndef ENABLE_SOFT_USB_RECOVERY
#define ENABLE_SOFT_USB_RECOVERY 0
#endif

/* Диагностика: бесконечный блинк LED (PE3) для локализации места зависания */
#ifndef DIAG_TRAP_STAGE
#define DIAG_TRAP_STAGE 0 /* 0=выключено; 1=после HAL_Init, 2=после SystemClock, 3=после PeriphClk, 4=после MX_GPIO, 5=перед main loop */
#endif
#ifndef EARLY_CDC_PROBE
#define EARLY_CDC_PROBE 1
#endif
static void diag_busy_delay(uint32_t cycles){ for(volatile uint32_t i=0;i<cycles;i++){ __NOP(); } }
static void diag_config_led_pe3(void){
  /* Включить тактирование GPIOE и перевести PE3 в Output */
  __HAL_RCC_GPIOE_CLK_ENABLE();
  int led_idx = __builtin_ctz(Led_Test_Pin);
  GPIOE->MODER &= ~(3u << (led_idx*2));
  GPIOE->MODER |=  (1u << (led_idx*2));
}
static void diag_trap(uint8_t code){
  diag_config_led_pe3();
  /* Цикл: code отчётливых вспышек (~300мс ON/~300мс OFF), затем длинная пауза ~1.5с */
  for(;;){
    for(uint8_t i=0;i<code;i++){
      GPIOE->BSRR = Led_Test_Pin;            /* ON */
      diag_busy_delay(12000000UL);
      GPIOE->BSRR = (Led_Test_Pin << 16);    /* OFF */
      diag_busy_delay(12000000UL);
    }
    diag_busy_delay(48000000UL);
  }
}

/* Minimal bring-up notes: SAFE_BLINK_ONLY path inside main() provides
   pre-HAL blinking on PE3 and PD8 with backlight (PE10) forced OFF. */
/* HardFault_Capture реализован в stm32h7xx_it.c; сюда можно позже добавить расширенную печать через extern */
extern void HardFault_Capture(uint32_t *stacked);
// --- Диагностика причин сброса ---
static uint32_t reset_cause_raw = 0; // сохраняем RCC->RSR до очистки
static const uint32_t build_signature_hex = 0xA5B6C7D8; // уникальная метка для верификации прошивки
// Кольцевой буфер причин последних сбросов в .noinit
typedef struct {
  uint32_t magic;
  uint32_t index;            // следующий слот для записи
  uint32_t rsr[8];           // последние 8 значений RCC->RSR
  uint32_t hardfault_count;  // число HardFault сессий
  uint32_t busfault_count;   // число BusFault
  uint32_t usagefault_count; // число UsageFault
} reset_trace_t;
static reset_trace_t __attribute__((section(".noinit"))) g_reset_trace;
static void reset_trace_record(uint32_t rsr){
  if(g_reset_trace.magic != 0x21524553UL){ // '!RES'
    g_reset_trace.magic = 0x21524553UL;
    g_reset_trace.index = 0;
    for(int i=0;i<8;i++) g_reset_trace.rsr[i]=0;
    g_reset_trace.hardfault_count = 0;
    g_reset_trace.busfault_count = 0;
    g_reset_trace.usagefault_count = 0;
  }
  g_reset_trace.rsr[g_reset_trace.index & 7U] = rsr;
  g_reset_trace.index++;
}

static const char* reset_cause_str(uint32_t rsr){
  if(rsr & RCC_RSR_IWDG1RSTF) return "IWDG";   // Independent watchdog
  if(rsr & RCC_RSR_WWDG1RSTF) return "WWDG";   // Window watchdog
  if(rsr & RCC_RSR_LPWRRSTF)  return "LPWR";   // Low-power reset
  if(rsr & RCC_RSR_BORRSTF)   return "BOR";    // Brown-out reset
  if(rsr & RCC_RSR_PINRSTF)   return "PIN";    // NRST pin
  if(rsr & RCC_RSR_SFTRSTF)   return "SOFT";   // Software reset
  if(rsr & RCC_RSR_PORRSTF)   return "POR";    // Power-on reset
  return "UNK";
}
static void log_reset_cause(void){
  // Считываем и сразу очищаем флаги (запись 1 очищает)
  reset_cause_raw = RCC->RSR;
  reset_trace_record(reset_cause_raw);
  char flags[96];
  flags[0]='\0';
  #define ADD_FLAG(bit,name) do{ if(reset_cause_raw & (bit)){ if(flags[0]) strncat(flags, ",", sizeof(flags)-1); strncat(flags, (name), sizeof(flags)-1);} }while(0)
  ADD_FLAG(RCC_RSR_IWDG1RSTF, "IWDG");
  ADD_FLAG(RCC_RSR_WWDG1RSTF, "WWDG");
  ADD_FLAG(RCC_RSR_LPWRRSTF,  "LPWR");
  ADD_FLAG(RCC_RSR_BORRSTF,   "BOR");
  ADD_FLAG(RCC_RSR_PINRSTF,   "PIN");
  ADD_FLAG(RCC_RSR_SFTRSTF,   "SOFT");
  ADD_FLAG(RCC_RSR_PORRSTF,   "POR");
  if(!flags[0]) strncpy(flags, "NONE", sizeof(flags)-1);
  printf("[BOOT] RSR=0x%08lX FLAGS=%s PRIMARY=%s SIGN=0x%08lX\r\n", (unsigned long)reset_cause_raw, flags, reset_cause_str(reset_cause_raw), (unsigned long)build_signature_hex);
  // Печатаем трассу (последние до 8 значений)
  printf("[BOOT] RSR_TRACE idx=%lu: ", (unsigned long)g_reset_trace.index);
  for(int i=0;i<8;i++){
    uint32_t v = g_reset_trace.rsr[(g_reset_trace.index - 1 - i) & 7U];
    printf(i?",0x%08lX":"0x%08lX", (unsigned long)v);
  }
  printf("\r\n");
  RCC->RSR |= RCC_RSR_RMVF; // снять флаги
}
static volatile uint8_t iwdg_enabled_runtime = 0; // отметка вызова MX_IWDG1_Init
/* Дополнительная диагностика времени жизни до сброса */
typedef struct {
  uint32_t magic;              // 'BDG1'
  uint32_t boot_counter;       // общий счётчик загрузок
  uint32_t slot;               // следующий индекс для circular
  struct {
    uint32_t uptime_ms;        // сохранённый аптайм перед предыдущим сбросом
    uint32_t progress_flags;   // битовая маска стадий, достигнутых в предыдущей сессии
    uint32_t rsr;              // RSR той сессии (дублирование для корреляции)
  } rec[8];
} boot_diag_t;
static boot_diag_t __attribute__((section(".noinit"))) g_boot_diag;

enum {
  BOOT_PROGRESS_AFTER_PWM      = (1u<<0),
  BOOT_PROGRESS_AFTER_USB_INIT = (1u<<1),
  BOOT_PROGRESS_AFTER_ADC      = (1u<<2),
  BOOT_PROGRESS_ENTER_LOOP     = (1u<<3)
};
static uint32_t g_progress_flags = 0;

static void boot_diag_init(uint32_t current_rsr){
  if(g_boot_diag.magic != 0x42444731UL){ // 'BDG1'
    memset(&g_boot_diag, 0, sizeof(g_boot_diag));
    g_boot_diag.magic = 0x42444731UL;
  }
  g_boot_diag.boot_counter++;
  // Ничего не пишем сейчас – запись произойдёт перед потенциальным сбросом / периодически
  // Для визуализации напечатаем последние 4 аптайма
  printf("[BOOT] LAST_UPTIMES(ms): ");
  for(int i=0;i<8;i++){
    uint32_t v = g_boot_diag.rec[(g_boot_diag.slot - 1 - i) & 7u].uptime_ms;
    printf(i?",%lu":"%lu", (unsigned long)v);
  }
  printf("\r\n");
  printf("[BOOT] LAST_PROGRESS: ");
  for(int i=0;i<4;i++){
    uint32_t pf = g_boot_diag.rec[(g_boot_diag.slot - 1 - i) & 7u].progress_flags;
    printf(i?",0x%02lX":"0x%02lX", (unsigned long)pf);
  }
  printf("\r\n");
}

static void boot_diag_periodic(uint32_t uptime_ms){
  // Периодическое обновление текущего слота, чтобы при внезапном PIN reset мы имели аптайм
  uint32_t s = (g_boot_diag.slot) & 7u; // текущий рабочий слот
  g_boot_diag.rec[s].uptime_ms = uptime_ms;
  g_boot_diag.rec[s].progress_flags = g_progress_flags;
  g_boot_diag.rec[s].rsr = reset_cause_raw; // последний считанный (текущая сессия)
}

static void boot_diag_finalize_before_reset(uint32_t uptime_ms){
  // Завершаем текущий слот и переходим к следующему
  uint32_t s = (g_boot_diag.slot) & 7u;
  g_boot_diag.rec[s].uptime_ms = uptime_ms;
  g_boot_diag.rec[s].progress_flags = g_progress_flags;
  g_boot_diag.rec[s].rsr = reset_cause_raw;
  g_boot_diag.slot++;
}
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
void PeriphCommonClock_Config(void);
static void MPU_Config(void);
static void MX_GPIO_Init(void);
static void MX_DMA_Init(void);
static void MX_SPI4_Init(void);
static void MX_TIM1_Init(void);
static void MX_SPI2_Init(void);
static void MX_TIM6_Init(void);
static void MX_ADC1_Init(void);
static void MX_ADC2_Init(void);
static void MX_DAC1_Init(void);
static void MX_TIM2_Init(void);
static void MX_TIM3_Init(void);
static void MX_TIM15_Init(void);
static void MX_IWDG1_Init(void);
static void MX_USART1_UART_Init(void);
/* USER CODE BEGIN PFP */
void UpdateLCDStatus(void);
void DrawStarIndicator(void);
static void __attribute__((unused)) UpdateUSBDebug(void); // теперь будет пустая заглушка
void DrawUSBStatus(void);
static const char* usb_state_str(uint8_t s) __attribute__((unused)); // пустая заглушка
// Forward declaration to avoid implicit declaration and linkage mismatch
static void lcd_print_padded_if_changed(int x, int y, const char* new_text,
                                        char *prev, size_t buf_sz,
                                        uint8_t max_len, uint8_t font_height,
                                        uint16_t fg, uint16_t bg);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
// Временная диагностика: отключить настройку MPU (иначе ранний HardFault при неполной конфигурации регионов)
#ifndef DISABLE_MPU
#define DISABLE_MPU 1
#endif
// Сверхбезопасный минимальный запуск: только GPIO+UART+TIM6, без LCD/USB/ADC/SPI/других TIM
#ifndef SAFE_MINIMAL
#define SAFE_MINIMAL 0
#endif
// Абсолютно простой режим: только GPIO и мигание LED в главном цикле (без прерываний/таймеров)
#ifndef SAFE_BLINK_ONLY
#define SAFE_BLINK_ONLY 0
#endif
// Управление периодическим UART-хартбитом [HB]: 0=выкл (по умолчанию)
#ifndef ENABLE_UART_HEARTBEAT
#define ENABLE_UART_HEARTBEAT 0
#endif
// --- Диагностика перезагрузок ---
// Определите DIAG_HALT_BEFORE_LOOP чтобы остановить МК перед входом в while(1)
// #define DIAG_HALT_BEFORE_LOOP 1
// Определите DIAG_HALT_AFTER_PWM чтобы остановить сразу после настройки PWM подсветки
// #define DIAG_HALT_AFTER_PWM 1
// Отключить сторож на время поэтапной локализации (дублируем принудительно):
#ifndef DIAG_DISABLE_IWDG
#define DIAG_DISABLE_IWDG 1
#endif
// Попытка растянуть уже запущенный IWDG (если он был активирован ранее) чтобы он не мешал диагностике
#ifndef DIAG_EXTEND_EXISTING_IWDG
#define DIAG_EXTEND_EXISTING_IWDG 1
#endif
// Включить подкормку IWDG дополнительно в основном цикле (диагностика зависаний прерываний)
// #define DIAG_FEED_IWDG_IN_MAIN 1
// Пропустить любые обращения к LCD (исключить SPI4 как причину длительных блокировок)
#define DIAG_SKIP_LCD 0
// Логировать состояние масок прерываний и BASEPRI каждые N циклов
#define DIAG_INT_MASK_LOG_PERIOD 50

static inline void diag_halt(const char *tag){
  printf("[DIAG] HALT %s\r\n", tag);
  __BKPT(0);
  while(1){ __NOP(); }
}
// Лёгкий неблокирующий (с ограничением) вывод одиночного символа в UART1 без HAL.
void uart1_raw_putc(char c){
  if(!(USART1->CR1 & USART_CR1_UE)) return;
  for(volatile uint32_t to=0; to<20000; ++to){
    if(USART1->ISR & USART_ISR_TXE_TXFNF){ USART1->TDR = (uint8_t)c; return; }
  }
}
/* Контрольные маркеры прохождения кода */
static inline void LED_ON(void){ HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_SET); }
static inline void LED_OFF(void){ HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_RESET); }
/* Управление односимвольным трейсом основного цикла (по умолчанию выкл) */
#ifndef ENABLE_UART_PROG
#define ENABLE_UART_PROG 0
#endif
#if ENABLE_UART_PROG
#define PROG(ch) uart1_raw_putc((ch))
#else
#define PROG(ch) do{}while(0)
#endif
// Режим управления подсветкой: 0 = PWM на TIM1_CH2N(PE10), 1 = принудительно GPIO
#ifndef FORCE_BL_GPIO
#define FORCE_BL_GPIO 1
#endif
// Полярность подсветки и макросы управления (используются в main и MX_GPIO_Init)
#ifndef BL_ACTIVE_LOW
/* Подсветка на аппаратной плате подключена active-low (PE10 через транзистор).
  Установим значение 1, чтобы вызов BL_ON() выставлял уровень, включающий подсветку. */
#define BL_ACTIVE_LOW 1
#endif
#if BL_ACTIVE_LOW
  #define BL_ON()  HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_RESET)
  #define BL_OFF() HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_SET)
#else
  #define BL_ON()  HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_SET)
  #define BL_OFF() HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_RESET)
#endif
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
  /* TEST MARKER: Check if new code is running */
  printf("[MARKER_MAIN] Entered main() function\r\n");

  /* USER CODE BEGIN 1 */
  static uint32_t early_rsr_raw = 0; // первое чтение до HAL_Init
  early_rsr_raw = RCC->RSR; /* читаем как можно раньше */
  uint8_t iwdg_extended_early = 0;
#if SAFE_BLINK_ONLY
  // Абсолютно ранний режим: без HAL_Init/ClockConfig – только GPIO и мигание в бесконечном цикле
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  // Настроим LED (PE3), BL (PE10), DATA_READY (PD8) в режим Output
  GPIO_TypeDef *led_port = Led_Test_GPIO_Port;
  GPIO_TypeDef *bl_port  = LCD_Led_GPIO_Port;
  GPIO_TypeDef *dr_port  = Data_ready_GPIO22_GPIO_Port;
  uint32_t led_mask = Led_Test_Pin;
  uint32_t bl_mask  = LCD_Led_Pin;
  uint32_t dr_mask  = Data_ready_GPIO22_Pin;
  int led_idx = __builtin_ctz(led_mask);
  int bl_idx  = __builtin_ctz(bl_mask);
  int dr_idx  = __builtin_ctz(dr_mask);
  led_port->MODER &= ~(3u << (led_idx*2)); led_port->MODER |=  (1u << (led_idx*2));
  bl_port->MODER  &= ~(3u << (bl_idx*2));  bl_port->MODER  |=  (1u << (bl_idx*2));
  dr_port->MODER  &= ~(3u << (dr_idx*2));  dr_port->MODER  |=  (1u << (dr_idx*2));
  // Попробуем растянуть и кормить IWDG (если он уже запущен опциями/прошивкой)
  IWDG1->KR = 0x5555;      // unlock
  IWDG1->PR = 0x06;        // prescaler /256
  IWDG1->RLR = 0x0FFF;     // max reload
  while(IWDG1->SR != 0){ /* wait for updates to apply */ }
  IWDG1->KR = 0xAAAA;      // reload immediately
  // Начальные уровни: LED OFF, DR OFF, BL OFF (active-low -> high)
  led_port->BSRR = (led_mask << 16);
  dr_port->BSRR  = (dr_mask  << 16);
  bl_port->BSRR  = bl_mask;
  while(1){
    // Тогглим только LED/DR (подсветку держим всегда выкл)
    if (led_port->ODR & led_mask) {
      led_port->BSRR = (led_mask << 16);
      dr_port->BSRR  = (dr_mask  << 16);
    } else {
      led_port->BSRR = led_mask;
      dr_port->BSRR  = dr_mask;
    }
    // Замедлим мигание для наглядности (на дефолтном HSI)
    for(volatile uint32_t d=0; d<80000000UL; ++d){ __NOP(); }
    // Кормим IWDG, если активен
    IWDG1->KR = 0xAAAA;
  }
#else
  // Сверхранняя индикация: мигаем LED и подсветкой до HAL_Init (на случай раннего fault)
  // Обе линии висят на порте E по схеме платы; используем макросы HAL для портов/пинов
  // Включаем тактирование основных портов GPIO (A, B, C, D, E, H), чтобы точно покрыть задействованные линии
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  GPIO_TypeDef *led_port = Led_Test_GPIO_Port;
  GPIO_TypeDef *bl_port  = LCD_Led_GPIO_Port;
  uint32_t led_mask = Led_Test_Pin;     // битовая маска
  uint32_t bl_mask  = LCD_Led_Pin;      // битовая маска
  int led_idx = __builtin_ctz(led_mask); // индекс пина 0..15
  int bl_idx  = __builtin_ctz(bl_mask);
  // Настраиваем режим Output для обеих линий (очистить 2 бита MODER и выставить 01)
  led_port->MODER &= ~(3u << (led_idx*2));
  led_port->MODER |=  (1u << (led_idx*2));
  bl_port->MODER  &= ~(3u << (bl_idx*2));
  bl_port->MODER  |=  (1u << (bl_idx*2));
  // Короткая задержка и три мигания
  for(volatile int i=0;i<100000;i++){ __NOP(); }
  for(int k=0;k<3;k++){
    led_port->BSRR = led_mask;  // set
    bl_port->BSRR  = bl_mask;   // set
    for(volatile int i=0;i<200000;i++){ __NOP(); }
    led_port->BSRR = (led_mask << 16); // reset
    bl_port->BSRR  = (bl_mask  << 16); // reset
    for(volatile int i=0;i<200000;i++){ __NOP(); }
  }
  // Оставляем финальное состояние: LED = ON, Подсветка = ON (активный высокий)
  led_port->BSRR = led_mask;  // LED on
  bl_port->BSRR  = bl_mask;   // BL on
#endif
#if DIAG_EXTEND_EXISTING_IWDG
  if(early_rsr_raw & RCC_RSR_IWDG1RSTF){
    // Переинициализация параметров IWDG (его нельзя остановить, но можно растянуть таймаут)
    // Ключ разблокировки
    IWDG1->KR = 0x5555;
    // Prescaler = 256 (0x06), максимум для делителя
    IWDG1->PR = 0x06;
    // Reload максимум 0x0FFF
    IWDG1->RLR = 0x0FFF;
    // Дождаться применения (PVU/RVU сброшены)
    while(IWDG1->SR != 0) { /* wait */ }
    // Немедленно перезагрузим
    IWDG1->KR = 0xAAAA;
    iwdg_extended_early = 1;
  }
#endif

  /* USER CODE END 1 */

  /* MPU Configuration--------------------------------------------------------*/
#if !DISABLE_MPU
  MPU_Config();
#else
  // Пропускаем настройку MPU на время восстановления работоспособности
#endif

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();
  /* Trap после HAL_Init */
#if defined(DIAG_TRAP_STAGE) && (DIAG_TRAP_STAGE==1)
  diag_trap(1);
#endif

  /* USER CODE BEGIN Init */
  // Счетчик перезагрузок для диагностики
  static uint32_t reboot_count __attribute__((section(".noinit"))) = 0;
  reboot_count++;
  printf("[BOOT] Device reboot count: %lu\r\n", (unsigned long)reboot_count);
  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();
  /* Trap после SystemClock_Config */
#if defined(DIAG_TRAP_STAGE) && (DIAG_TRAP_STAGE==2)
  diag_trap(2);
#endif

  /* Configure the peripherals common clocks */
  PeriphCommonClock_Config();
  /* Trap после PeriphCommonClock_Config */
#if defined(DIAG_TRAP_STAGE) && (DIAG_TRAP_STAGE==3)
  diag_trap(3);
#endif

  /* USER CODE BEGIN SysInit */
  /* РАННИЙ UART для диагностики: инициализация сразу после тактирования */
  MX_USART1_UART_Init();
  setvbuf(stdout, NULL, _IONBF, 0);
  static uint32_t build_counter __attribute__((section(".noinit"))) = 0;
  build_counter++;
  printf("[BOOT] BUILD_TS=%s-%s COUNT=%lu SIGN=0x%08lX\r\n", __DATE__, __TIME__, (unsigned long)build_counter, (unsigned long)build_signature_hex);
  printf("[BOOT] DIAG_REV=%d\r\n", 4);
  printf("[EARLY] RSR=0x%08lX (pre-HAL_Init snapshot)\r\n", (unsigned long)early_rsr_raw);
  if(iwdg_extended_early){ printf("[EARLY] IWDG_EXTENDED presc=256 reload=0x0FFF\r\n"); }
  log_reset_cause();
  printf("[BOOT] FW_VERSION=%s DATE=%s TIME=%s HASH=%s\r\n", fw_version, fw_build_date, fw_build_time, fw_git_hash);
  printf("[BOOT] %s\r\n", fw_build_full);
  printf("[UART] USART1=115200 8N1 ready\r\n");
#if 1
  // Дополнительная диагностика debug и option bytes
  uint32_t dhcsr = CoreDebug->DHCSR;
  uint32_t dbg_cr = DBGMCU->CR;
  uint32_t opt_raw = 0;
#ifdef FLASH_OPTSR_CUR
  opt_raw = FLASH->OPTSR_CUR; // Текущие опции (read-only)
#elif defined(FLASH_OPTSR_PRG)
  opt_raw = FLASH->OPTSR_PRG;
#endif
  printf("[BOOT] DBG:DHCSR=0x%08lX C_DEBUGEN=%lu S_SLEEP=%lu S_LOCKUP=%lu CR=0x%08lX OPTSR=0x%08lX\r\n",
         (unsigned long)dhcsr,
         (unsigned long)((dhcsr>>0) & 1),
         (unsigned long)((dhcsr>>18)&1),
         (unsigned long)((dhcsr>>19)&1),
         (unsigned long)dbg_cr,
         (unsigned long)opt_raw);
#endif
  #ifdef DIAG_DISABLE_IWDG
    printf("[BOOT] IWDG_CFG=DISABLED (compile-time macro)\r\n");
  #else
    printf("[BOOT] IWDG_CFG=ENABLED (will init later)\r\n");
  #endif
  boot_diag_init(early_rsr_raw);
  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
#if SAFE_BLINK_ONLY
  // Ультра-простой безопасный режим: только GPIO и мигание LED в главном цикле
  MX_GPIO_Init();
  // Держим подсветку всегда ВЫКЛ (active-low -> высокий уровень)
  BL_OFF();
  printf("[SAFE] BLINK_ONLY: GPIO + UART only. No timers/USB/ADC/SPI.\r\n");
  // Установим исходное состояние LED = OFF
  HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_RESET);
  // Тоже будем подмигивать пином Data_ready для надёжности видимости
  HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_RESET);
  while(1){
    // Прямое переключение через BSRR (минимум зависимостей) + явная подсветка
    static uint8_t st = 0; st ^= 1;
    if (st) {
      Led_Test_GPIO_Port->BSRR = Led_Test_Pin;                // LED ON
      Data_ready_GPIO22_GPIO_Port->BSRR = Data_ready_GPIO22_Pin;
      LCD_Led_GPIO_Port->BSRR = (LCD_Led_Pin << 16);          // BL ON (active-low -> reset)
    } else {
      Led_Test_GPIO_Port->BSRR = (Led_Test_Pin << 16);        // LED OFF
      Data_ready_GPIO22_GPIO_Port->BSRR = (Data_ready_GPIO22_Pin << 16);
      LCD_Led_GPIO_Port->BSRR = LCD_Led_Pin;                  // BL OFF (active-low -> set)
    }
    // Неблокирующий признак жизни по UART (если подключён)
    uart1_raw_putc('*');
    // Простейшая задержка по занятым циклам (без зависимости от SysTick)
    for(volatile uint32_t d=0; d<3000000UL; ++d){ __NOP(); }
    // Замедление для наглядного мигания (~0.3-0.4s на полупериод при 550 МГц)
    for(volatile uint32_t d=0; d<200000000UL; ++d){ __NOP(); }
  }
#elif SAFE_MINIMAL
  /* Минимальная ветка: вывод тестовой строки на LCD без USB/ADC */
  MX_GPIO_Init();
  LED_ON(); /* маркер: дошли до GPIO_Init */
  MX_TIM6_Init();
  /* В SAFE_MINIMAL не запускаем TIM6 IRQ, чтобы LED не мигал и оставался маркером */
  /* HAL_TIM_Base_Start_IT(&htim6); */
  /* Настроим SPI4 и инициализируем LCD */
  MX_SPI4_Init();
  /* Подсветка через GPIO (FORCE_BL_GPIO=1) */
  #if BL_ACTIVE_LOW
    HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_RESET); /* BL ON */
  #else
    HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_SET);   /* BL ON */
  #endif
  /* Небольшая задержка после включения подсветки и сброса LCD перед инициализацией */
  HAL_Delay(20);
  LCD_Init();
  LED_ON(); /* маркер: после LCD_Init */
  LCD_FillRect(0,0,LCD_W,LCD_H, BLACK);
  LCD_ShowString_Size(2, 2, "HELLO LCD", 16, YELLOW, BLACK);
  LCD_ShowString_Size(2, 20, "SAFE_MINIMAL", 12, WHITE, BLACK);
  printf("[SAFE] LCD init done, text rendered.\r\n");
  /* USB CDC в этой ветке не инициализируем, т.к. кабеля нет */
#else
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_SPI4_Init();
  MX_TIM1_Init();
  MX_SPI2_Init();
  MX_TIM6_Init();
  MX_ADC1_Init();
  MX_ADC2_Init();
  MX_DAC1_Init();
  MX_TIM2_Init();
  MX_TIM3_Init();
  MX_TIM15_Init();
  MX_USART1_UART_Init();
  printf("[INIT] Before USB_DEVICE_Init\r\n");
  MX_USB_DEVICE_Init();
  /* Полностью исключаем инициализацию IWDG (даже если где-то потерян DIAG_DISABLE_IWDG) */
  printf("[DIAG] IWDG hard-disabled (no init call)\r\n");
  g_progress_flags |= BOOT_PROGRESS_AFTER_USB_INIT;
#endif
  /* USER CODE BEGIN 2 */
  // Безбуферный stdout, баннер сборки (перенесено выше)
  printf("[USB] DEVICE_INIT\r\n");
  HAL_GPIO_WritePin(DATA_READY_GPIO_Port, DATA_READY_Pin, GPIO_PIN_RESET);
  // Запускаем TIM6 с прерыванием для диагностического мигания (LED в HAL_TIM_PeriodElapsedCallback)
  HAL_TIM_Base_Start_IT(&htim6);

  // Запуск каналов для TIM2
#if !SAFE_MINIMAL
    for(volatile uint32_t d=0; d<200000000UL; ++d){ __NOP(); }
  MX_GPIO_Init();
  /* Trap после MX_GPIO_Init */
#if defined(DIAG_TRAP_STAGE) && (DIAG_TRAP_STAGE==4)
  diag_trap(4);
#endif
  HAL_TIM_OC_Start(&htim2, TIM_CHANNEL_3);  // Контроль

  // Установка скважности для TIM2 (CH1 и CH2) — 50%
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_1, 2499); // 50% скважность
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_2, 2499); // 50% скважность

  // Запуск каналов для TIM3
  HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_1); // Фаза
  HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_2); // Меандр
  HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_3); // Контроль

  // Установка скважности для TIM3 (CH1, CH2, CH3) — 50%
  __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_1, 2499); // 50% скважность
  __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_2, 2499); // 50% скважность
  __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_3, 2499); // 50% скважность
  g_progress_flags |= BOOT_PROGRESS_AFTER_ADC;
#endif

/*
  // --- ВОССТАНОВЛЕНИЕ ПОДСВЕТКИ LCD (BLK) ---
#if !FORCE_BL_GPIO
  // Держим подсветку ВЫКЛ до очистки экрана
  BL_OFF();
#else
  // Жёстко оставляем подсветку ВКЛ для исключения проблем с PWM/MOE
  HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_SET);
#endif

  // Небольшая задержка для стабилизации питания LCD
  HAL_Delay(100);
*/


  // --- ИНИЦИАЛИЗАЦИЯ LCD ---
#if !SAFE_MINIMAL
  LCD_Init();
  LCD_FillRect(0, 0, LCD_W, LCD_H, BLACK);
  /* Гарантированно включаем подсветку после инициализации LCD независимо от режима PWM/GPIO */
  BL_ON();
#if 1
  g_progress_flags |= BOOT_PROGRESS_AFTER_PWM;
#endif
#if !FORCE_BL_GPIO && !defined(DISABLE_PWM_TEST)
  printf("[PWM] FORCE_BL_GPIO=%d, entering PWM block\r\n", FORCE_BL_GPIO);
  // --- ПОДСВЕТКА через TIM1 CH2N (PE10) ---
  printf("[PWM] Starting LCD backlight PWM...\r\n");

  // Устанавливаем duty cycle (попробуем 70% вместо 60%)
  __HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_2, 1750); // ~70% при ARR=2499
  printf("[PWM] Duty cycle set to 1750\r\n");

  // Правильный порядок запуска для complementary PWM:
  // 1. Сначала включаем главный выход (MOE)
  __HAL_TIM_MOE_ENABLE(&htim1);
  printf("[PWM] MOE enabled\r\n");

  // 2. Запускаем основной канал
  if (HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_2) == HAL_OK) {
    printf("[PWM] Main channel started OK\r\n");
  } else {
    printf("[PWM] ERROR: Main channel start failed!\r\n");
    Error_Handler();
  }

  // 3. Запускаем complementary канал
  if (HAL_TIMEx_PWMN_Start(&htim1, TIM_CHANNEL_2) == HAL_OK) {
    printf("[PWM] Complementary channel started OK\r\n");
  } else {
    printf("[PWM] ERROR: Complementary channel start failed!\r\n");
    Error_Handler();
  }

  // 4. Запускаем тактирование базы
  if (HAL_TIM_Base_Start(&htim1) == HAL_OK) {
    printf("[PWM] Base started OK\r\n");
  } else {
    printf("[PWM] ERROR: Base start failed!\r\n");
    Error_Handler();
  }

  // Проверяем статус
  uint32_t ccr2 = TIM1->CCR2;
  uint32_t arr = TIM1->ARR;
  // Избегаем float в printf (отсутствует поддержка): duty x10 (одна десятая процента)
  uint32_t duty_x10 = (arr ? (ccr2 * 1000UL + arr/2)/arr : 0); // в десятых процента
  printf("[PWM] TIM1: ARR=%lu, CCR2=%lu, Duty=%lu.%lu%%\r\n",
         (unsigned long)arr, (unsigned long)ccr2,
         (unsigned long)(duty_x10/10), (unsigned long)(duty_x10%10));

  // Короткая задержка для стабилизации
  HAL_Delay(10);
  printf("[PWM] PWM setup completed successfully\r\n");
  /* Ensure backlight is on after PWM init — keep BL on to allow reading the LCD */
  BL_ON();
  #ifdef DIAG_HALT_AFTER_PWM
    diag_halt("AFTER_PWM");
  #endif
  strncpy(stage_log[stage_count], "LCD READY", sizeof(stage_log[0])-1);
  stage_log[stage_count][sizeof(stage_log[0])-1] = 0;
  stage_count++;
#if MINIMAL_BRINGUP
  LCD_FillRect(0,0,LCD_W,LCD_H,BLACK);
#else
  FlushStageLog();
  STAGE(20,"LCD");
  for (int y = stage_count*12; y < 160; y += 8) {
    LCD_FillRect(0, y, 160, 8, BLACK);
  }
#endif
#else
  printf("[PWM] PWM disabled by DISABLE_PWM_TEST or FORCE_BL_GPIO\r\n");
#endif
#endif // !SAFE_MINIMAL

  // Код инициализации, который должен выполняться всегда
  printf("[INIT] Starting common initialization...\r\n");

  // Подготавливаем инициализационные сообщения (отключено)
  init_messages_ready = 0; // ничего не выводим

  // Включаем детектор USB питания (для встроенного FS PHY)
  HAL_PWREx_EnableUSBVoltageDetector();
  printf("[INIT] USB voltage detector enabled\r\n");
#if MINIMAL_BRINGUP
  // В минимальном режиме теперь тоже запускаем USB для отображения статуса
  usb_cdc_init();
  usb_cdc_cfg()->streaming = 0; // пока отключено
#else
  #if !SAFE_MINIMAL
    usb_cdc_init();
    usb_cdc_cfg()->streaming = 0;
  #endif
#endif

  // Первичная отметка для вывода статуса USB / буквы U
  UpdateLCDStatus();

  // Вывод краткой информации об устройстве USB при запуске
  #if !SAFE_MINIMAL
  {
    uint16_t vid = USBD_Desc_GetVID();
    uint16_t pid = USBD_Desc_GetPID();
    uint16_t lang= USBD_Desc_GetLangID();
    const char* mfg = USBD_Desc_GetManufacturer();
    const char* prd = USBD_Desc_GetProduct();
    printf("[USB] VID=0x%04X PID=0x%04X LANGID=%u\r\n", vid, pid, (unsigned)lang);
    printf("[USB] MFG=\"%s\" PROD=\"%s\"\r\n", mfg, prd);
    // Короткая строка на LCD
    char line[32];
  /* Переносим строку VID/PID ниже (y=36), чтобы не конфликтовать с динамической строкой TX */
  snprintf(line, sizeof(line), "VID:%04X PID:%04X", vid, pid);
  LCD_ShowString_Size(1, 65, line, 12, WHITE, BLACK);
  }
  #endif

  #if !SAFE_MINIMAL
  printf("[INIT] USB initialization completed\r\n");
  #endif


  // Запуск АЦП с DMA через модуль adc_stream (перенумеровано после LCD)
#if !MINIMAL_BRINGUP && !SAFE_MINIMAL
  CHECK(adc_stream_start(&hadc1, &hadc2), 1001); // если ошибка -> Error_Handler
  STAGE(21,"ADCSTR");
  // --- FIX TIM15 SLAVE RESET HANG ---
  __HAL_TIM_DISABLE(&htim15);
  /* keep SMCR as configured (slave reset to ITR1) */
  __HAL_TIM_SET_COUNTER(&htim15,0);

  // Диагностика перед запуском TIM15
  printf("[TIM15] Pre-Start: CR1=0x%08lX CR2=0x%08lX SMCR=0x%08lX SR=0x%08lX DIER=0x%08lX PSC=%lu ARR=%lu CNT=%lu\r\n",
     (unsigned long)TIM15->CR1, (unsigned long)TIM15->CR2, (unsigned long)TIM15->SMCR,
     (unsigned long)TIM15->SR, (unsigned long)TIM15->DIER,
     (unsigned long)TIM15->PSC, (unsigned long)TIM15->ARR, (unsigned long)TIM15->CNT);
  // Если состояние HAL не READY, попытаемся принудительно вернуть READY для обхода зависания
  if (htim15.State != HAL_TIM_STATE_READY) {
    printf("[TIM15] State=%d (not READY), forcing READY\r\n", htim15.State);
    htim15.State = HAL_TIM_STATE_READY;
  }
  {
    HAL_StatusTypeDef st = HAL_TIM_Base_Start(&htim15);
    if (st != HAL_OK) {
      printf("[TIM15][ERR] HAL_TIM_Base_Start status=%d (state=%d) -> entering Error_Handler\r\n", st, htim15.State);
      err_code = 1002;
      Error_Handler();
    }
  }
  printf("[TIM15] Started: CR1=0x%08lX SR=0x%08lX CNT=%lu\r\n", (unsigned long)TIM15->CR1, (unsigned long)TIM15->SR, (unsigned long)TIM15->CNT);

  
  // Запускаем PWM канал TIM15_CH1 (PE5) для наблюдения на осциллографе
  HAL_TIM_PWM_Start(&htim15, TIM_CHANNEL_1);
  STAGE(22,"TRGON");
  
  HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_SET);
  UpdateLCDStatus();
#elif MINIMAL_BRINGUP
  // Облегчённый путь для MINIMAL_BRINGUP (не используется в SAFE_MINIMAL)
  HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_SET);
  CHECK(adc_stream_start(&hadc1, &hadc2), 1101);
  __HAL_TIM_DISABLE(&htim15);
  TIM15->SMCR = 0; // убрать SLAVEMODE_RESET
  __HAL_TIM_SET_COUNTER(&htim15,0);
  CHECK(HAL_TIM_Base_Start(&htim15), 1102);
  HAL_TIM_PWM_Start(&htim15, TIM_CHANNEL_1);
#else
  // SAFE_MINIMAL: ничего не запускаем из ADC/TIM15
#endif



  printf("[INIT] ADC and TIM15 initialization completed\r\n");

  printf("[INIT] Entering main loop...\r\n");
  /* Trap перед входом в основной цикл */
#if defined(DIAG_TRAP_STAGE) && (DIAG_TRAP_STAGE==5)
  diag_trap(5);
#endif
  g_progress_flags |= BOOT_PROGRESS_ENTER_LOOP;
  /* Включаем DWT счётчик циклов (если не включён) для диагностики зависания */
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->LAR = 0xC5ACCE55; /* разблокировка (для некоторых ревизий) */
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  uint32_t last_diag_ms = 0; /* для периодического аварийного принта даже если * не печатается */
  #ifdef DIAG_HALT_BEFORE_LOOP
    diag_halt("BEFORE_LOOP");
  #endif


  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
  uint32_t dwt_start = DWT->CYCCNT; /* начало итерации */
    static uint32_t loop_count = 0;
    loop_count++;
    main_loop_heartbeat++;
    last_heartbeat_ms = HAL_GetTick();
    uint32_t now = last_heartbeat_ms;
  static uint8_t first_loop=1; if(first_loop){ PROG('M'); first_loop=0; }
  PROG('A'); // loop start

  // Отложенный лог из TIM6 (убран printf из ISR)
  if (tim6_led_toggled_flag) { tim6_led_toggled_flag = 0; PROG('L'); }

  if ((loop_count % DIAG_INT_MASK_LOG_PERIOD) == 0) { PROG('I'); }

  #ifdef DIAG_FEED_IWDG_IN_MAIN
  HAL_IWDG_Refresh(&hiwdg1);
  #endif

  // Мониторинг PWM TIM1: если MOE или канал перестали выдавать, пытаемся восстановить (отключено для чистоты логов)
  #if 0
  if((loop_count & 0x3F) == 1){ // раз в 64 цикла
    uint32_t bdtr = TIM1->BDTR;
    uint32_t cr1  = TIM1->CR1;
    uint32_t ccer = TIM1->CCER;
    uint32_t ccr2l = TIM1->CCR2;
    if(!(bdtr & TIM_BDTR_MOE) || !(cr1 & TIM_CR1_CEN)){
      printf("[PWM-MON] Re-enabling TIM1: BDTR=0x%08lX CR1=0x%08lX CCER=0x%08lX CCR2=%lu\r\n",
           (unsigned long)bdtr,(unsigned long)cr1,(unsigned long)ccer,(unsigned long)ccr2l);
      __HAL_TIM_MOE_ENABLE(&htim1);
      HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_2);
      HAL_TIMEx_PWMN_Start(&htim1, TIM_CHANNEL_2);
      HAL_TIM_Base_Start(&htim1);
    }
  }
  #endif

    // Логируем каждые 10 итераций цикла
  if ((loop_count & 0x3F)==0) { PROG('T'); boot_diag_periodic(now); }
  /* Periodic integrity check for guarded need_recovery */
  if((loop_count & 0x3F)==0){
    if(need_recovery_guard.c1 != 0xDEADBEEFUL || need_recovery_guard.c2 != 0xA55AA55AUL){
      printf("[DIAG][MEM] GUARD_FAIL c1=0x%08lX c2=0x%08lX flag=%u @%p size=%u\r\n",
             (unsigned long)need_recovery_guard.c1, (unsigned long)need_recovery_guard.c2,
             (unsigned int)need_recovery_guard.flag, (void*)&need_recovery_guard, (unsigned)sizeof(need_recovery_guard));
    }
    if(need_recovery_guard.flag != 0){
      printf("[DIAG][MEM] need_recovery FLAG SET=%u (c1=0x%08lX c2=0x%08lX) clear->0\r\n",
             (unsigned int)need_recovery_guard.flag,
             (unsigned long)need_recovery_guard.c1, (unsigned long)need_recovery_guard.c2);
      need_recovery_guard.flag = 0; /* предотвращаем цикл */
    }
  }

  PROG('B'); // before star
  /* ========== Минимальный индикатор работы основного цикла (выкл по умолчанию) ========== */
  #if ENABLE_UART_HEARTBEAT
  {
    enum { STAR_INTERVAL_MS = 100 }; // можно уменьшить до 50 при необходимости
    static uint32_t star_last_ms = 0;
    if (now - star_last_ms >= STAR_INTERVAL_MS) {
        star_last_ms = now;
        uart1_raw_putc('*'); // минимальная нагрузка (1 байт)
        HAL_GPIO_TogglePin(HEARTBEAT_GPIO_Port, HEARTBEAT_Pin); // визуально на пине
    }
  /* Резервный канал: каждые 100 мс печатаем диагностику, если звёздочки вдруг не видны */
  if(now - last_diag_ms >= 100){
    last_diag_ms = now;
    static uint32_t diag_seq = 0;
    printf("[D]%lu ms=%lu cyc=%lu wr=%lu rd=%lu irq=%lu\r\n",
         (unsigned long)diag_seq++,
         (unsigned long)now,
         (unsigned long)DWT->CYCCNT,
         (unsigned long)frame_wr_seq,
         (unsigned long)frame_rd_seq,
         (unsigned long)tim6_irq_count);
  }
  /* Если HAL_GetTick() перестал расти (SysTick замёрз/IRQ выключены) – диагностируем и пытаемся восстановить */
  {
    static uint32_t freeze_ref_ms = 0;
    static uint32_t freeze_iter = 0;
    if(freeze_ref_ms == 0){
      freeze_ref_ms = now; freeze_iter = 0;
    } else if(now == freeze_ref_ms){
      if(++freeze_iter == 50000){
        uint32_t primask = __get_PRIMASK();
        uint32_t syst_ctrl = SysTick->CTRL;
        uint32_t syst_load = SysTick->LOAD;
        uint32_t syst_val  = SysTick->VAL;
        printf("[TICK-FROZEN] ms=%lu primask=%lu SYST_CTRL=0x%08lX LOAD=%lu VAL=%lu -> enabling IRQ & SysTick\r\n",
             (unsigned long)now,
             (unsigned long)primask,
             (unsigned long)syst_ctrl,
             (unsigned long)syst_load,
             (unsigned long)syst_val);
        __enable_irq();
        /* Насильно включаем прерывание и счётчик SysTick */
        SysTick->CTRL |= SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_TICKINT_Msk | SysTick_CTRL_ENABLE_Msk;
      }
      else if((freeze_iter & 0xFFFF) == 0){
        /* периодический сэмпл для длительной заморозки */
        printf("[TICK-STILL] iter=%lu ms=%lu SYST_CTRL=0x%08lX VAL=%lu\r\n",
             (unsigned long)freeze_iter,
             (unsigned long)now,
             (unsigned long)SysTick->CTRL,
             (unsigned long)SysTick->VAL);
      }
    } else { /* tick ожил */
      if(freeze_iter > 50000){
        printf("[TICK-RECOVERED] after_iter=%lu new_ms=%lu\r\n", (unsigned long)freeze_iter, (unsigned long)now);
      }
      freeze_ref_ms = now; freeze_iter = 0;
    }
  }
    }
  #endif /* ENABLE_UART_HEARTBEAT */
  /* Все остальные индикаторы (spinner, hz, wr/rd, sof) временно отключены для
     чистоты. Вернём позже после подтверждения нормальной скорости цикла. */

  /* USB детект временно отключен для упрощения */

  // PROG('V'); // vendor diag disabled for isolation
  // vnd_diag_send64_once();
  // PROG('v');

  /* Запуск задачи стриминга: вызываем таск при активном стриме */
  // vendor stream task
#if !SAFE_MINIMAL
  extern uint8_t vnd_is_streaming(void);
  if (vnd_is_streaming()) {
    extern void Vendor_Stream_Task(void);
    Vendor_Stream_Task();
  }
#endif

    if (need_recovery) {
#if ENABLE_SOFT_USB_RECOVERY
        need_recovery = 0;
        extern void USB_LL_SetSoftDisconnect(uint8_t enable);
        USB_LL_SetSoftDisconnect(1);
        HAL_Delay(50);
        USB_LL_SetSoftDisconnect(0);
        HAL_Delay(10);
#ifdef HAL_PCD_MODULE_ENABLED
        USBD_Stop(&hUsbDeviceHS);
        USBD_DeInit(&hUsbDeviceHS);
#endif
        MX_USB_DEVICE_Init();
        auto_stream_started = 0;
#else
  boot_diag_finalize_before_reset(HAL_GetTick());
  NVIC_SystemReset();
#endif
    }

  #if ENABLE_UART_HEARTBEAT
  if ((loop_count % 1000u)==0) uart1_raw_putc('.');
  #endif
  if(iwdg_enabled_runtime){ printf("[WARN] IWDG active unexpected\r\n"); }
  /* Подсчёт длительности итерации */
  uint32_t dwt_end = DWT->CYCCNT;
  loop_cycle_accum += (uint32_t)(dwt_end - dwt_start);
  loop_cycle_count++;
  uint32_t ms_now = HAL_GetTick();
  if(ms_now - loop_cycle_last_report_ms >= 1000 && loop_cycle_count){
    loop_cycle_last_avg = (uint32_t)(loop_cycle_accum / loop_cycle_count);
    loop_cycle_accum = 0; loop_cycle_count = 0; loop_cycle_last_report_ms = ms_now;
    /* printf отключён для изоляции зависания */
  }
  }
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  /* (не используется) */
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Supply configuration update enable
  */
  HAL_PWREx_ConfigSupply(PWR_LDO_SUPPLY);

  /** Configure the main internal regulator output voltage
  */
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE0);

  while(!__HAL_PWR_GET_FLAG(PWR_FLAG_VOSRDY)) {}

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI48|RCC_OSCILLATORTYPE_HSI
                              |RCC_OSCILLATORTYPE_LSI|RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.HSIState = RCC_HSI_DIV1;
  RCC_OscInitStruct.HSICalibrationValue = 64;
  RCC_OscInitStruct.LSIState = RCC_LSI_ON;
  RCC_OscInitStruct.HSI48State = RCC_HSI48_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 2;
  RCC_OscInitStruct.PLL.PLLN = 44;
  RCC_OscInitStruct.PLL.PLLP = 1;
  RCC_OscInitStruct.PLL.PLLQ = 5;
  RCC_OscInitStruct.PLL.PLLR = 2;
  RCC_OscInitStruct.PLL.PLLRGE = RCC_PLL1VCIRANGE_3;
  RCC_OscInitStruct.PLL.PLLVCOSEL = RCC_PLL1VCOWIDE;
  RCC_OscInitStruct.PLL.PLLFRACN = 0;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2
                              |RCC_CLOCKTYPE_D3PCLK1|RCC_CLOCKTYPE_D1PCLK1;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.SYSCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB3CLKDivider = RCC_APB3_DIV2;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_APB1_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_APB2_DIV2;
  RCC_ClkInitStruct.APB4CLKDivider = RCC_APB4_DIV2;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_3) != HAL_OK)
  {
    Error_Handler();
  }
  HAL_RCC_MCOConfig(RCC_MCO1, RCC_MCO1SOURCE_HSI, RCC_MCODIV_1);
}

/**
  * @brief Peripherals Common Clock Configuration
  * @retval None
  */
void PeriphCommonClock_Config(void)
{
  RCC_PeriphCLKInitTypeDef PeriphClkInitStruct = {0};

  /** Initializes the peripherals clock
  */
  PeriphClkInitStruct.PeriphClockSelection = RCC_PERIPHCLK_ADC|RCC_PERIPHCLK_SPI2
                              |RCC_PERIPHCLK_SPI4|RCC_PERIPHCLK_TIM;
  PeriphClkInitStruct.PLL2.PLL2M = 5;
  PeriphClkInitStruct.PLL2.PLL2N = 128;
  PeriphClkInitStruct.PLL2.PLL2P = 4;
  PeriphClkInitStruct.PLL2.PLL2Q = 4;
  PeriphClkInitStruct.PLL2.PLL2R = 2;
  PeriphClkInitStruct.PLL2.PLL2RGE = RCC_PLL2VCIRANGE_2;
  PeriphClkInitStruct.PLL2.PLL2VCOSEL = RCC_PLL2VCOWIDE;
  PeriphClkInitStruct.PLL2.PLL2FRACN = 0;
  PeriphClkInitStruct.Spi123ClockSelection = RCC_SPI123CLKSOURCE_PLL2;
  PeriphClkInitStruct.Spi45ClockSelection = RCC_SPI45CLKSOURCE_PLL2;
  PeriphClkInitStruct.AdcClockSelection = RCC_ADCCLKSOURCE_PLL2;
  PeriphClkInitStruct.TIMPresSelection = RCC_TIMPRES_ACTIVATED;

  if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInitStruct) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief ADC1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC1_Init(void)
{

  /* USER CODE BEGIN ADC1_Init 0 */
  /* USER CODE END ADC1_Init 0 */

  ADC_MultiModeTypeDef multimode = {0};
  ADC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN ADC1_Init 1 */
  /* USER CODE END ADC1_Init 1 */

  /** Common config
  */
  hadc1.Instance = ADC1;
  hadc1.Init.ClockPrescaler = ADC_CLOCK_ASYNC_DIV1;
  hadc1.Init.Resolution = ADC_RESOLUTION_16B;
  hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;
  hadc1.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
  hadc1.Init.LowPowerAutoWait = DISABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.NbrOfConversion = 1;
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.ExternalTrigConv = ADC_EXTERNALTRIG_T15_TRGO;
  hadc1.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_RISING;
  hadc1.Init.ConversionDataManagement = ADC_CONVERSIONDATA_DMA_CIRCULAR;
  hadc1.Init.Overrun = ADC_OVR_DATA_PRESERVED;
  hadc1.Init.LeftBitShift = ADC_LEFTBITSHIFT_NONE;
  hadc1.Init.OversamplingMode = DISABLE;
  hadc1.Init.Oversampling.Ratio = 1;
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure the ADC multi-mode
  */
  multimode.Mode = ADC_MODE_INDEPENDENT;
  if (HAL_ADCEx_MultiModeConfigChannel(&hadc1, &multimode) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Regular Channel
  */
  sConfig.Channel = ADC_CHANNEL_3;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_1CYCLE_5;
  sConfig.SingleDiff = ADC_SINGLE_ENDED;
  sConfig.OffsetNumber = ADC_OFFSET_NONE;
  sConfig.Offset = 0;
  sConfig.OffsetSignedSaturation = DISABLE;
  if (HAL_ADC_ConfigChannel(&hadc1, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC1_Init 2 */
  /* USER CODE END ADC1_Init 2 */

}

/**
  * @brief ADC2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC2_Init(void)
{

  /* USER CODE BEGIN ADC2_Init 0 */
  /* USER CODE END ADC2_Init 0 */

  ADC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN ADC2_Init 1 */
  /* USER CODE END ADC2_Init 1 */

  /** Common config
  */
  hadc2.Instance = ADC2;
  hadc2.Init.ClockPrescaler = ADC_CLOCK_ASYNC_DIV1;
  hadc2.Init.Resolution = ADC_RESOLUTION_16B;
  hadc2.Init.ScanConvMode = ADC_SCAN_DISABLE;
  hadc2.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
  hadc2.Init.LowPowerAutoWait = DISABLE;
  hadc2.Init.ContinuousConvMode = DISABLE;
  hadc2.Init.NbrOfConversion = 1;
  hadc2.Init.DiscontinuousConvMode = DISABLE;
  hadc2.Init.ExternalTrigConv = ADC_EXTERNALTRIG_T15_TRGO;
  hadc2.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_RISING;
  hadc2.Init.ConversionDataManagement = ADC_CONVERSIONDATA_DMA_CIRCULAR;
  hadc2.Init.Overrun = ADC_OVR_DATA_PRESERVED;
  hadc2.Init.LeftBitShift = ADC_LEFTBITSHIFT_NONE;
  hadc2.Init.OversamplingMode = DISABLE;
  hadc2.Init.Oversampling.Ratio = 1;
  if (HAL_ADC_Init(&hadc2) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Regular Channel
  */
  sConfig.Channel = ADC_CHANNEL_4;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_1CYCLE_5;
  sConfig.SingleDiff = ADC_SINGLE_ENDED;
  sConfig.OffsetNumber = ADC_OFFSET_NONE;
  sConfig.Offset = 0;
  sConfig.OffsetSignedSaturation = DISABLE;
  if (HAL_ADC_ConfigChannel(&hadc2, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC2_Init 2 */
  /* USER CODE END ADC2_Init 2 */

}

/**
  * @brief DAC1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_DAC1_Init(void)
{

  /* USER CODE BEGIN DAC1_Init 0 */
  /* USER CODE END DAC1_Init 0 */

  DAC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN DAC1_Init 1 */
  /* USER CODE END DAC1_Init 1 */

  /** DAC Initialization
  */
  hdac1.Instance = DAC1;
  if (HAL_DAC_Init(&hdac1) != HAL_OK)
  {
    Error_Handler();
  }

  /** DAC channel OUT1 config
  */
  sConfig.DAC_SampleAndHold = DAC_SAMPLEANDHOLD_DISABLE;
  sConfig.DAC_Trigger = DAC_TRIGGER_NONE;
  sConfig.DAC_OutputBuffer = DAC_OUTPUTBUFFER_ENABLE;
  sConfig.DAC_ConnectOnChipPeripheral = DAC_CHIPCONNECT_ENABLE;
  sConfig.DAC_UserTrimming = DAC_TRIMMING_FACTORY;
  if (HAL_DAC_ConfigChannel(&hdac1, &sConfig, DAC_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }

  /** DAC channel OUT2 config
  */
  if (HAL_DAC_ConfigChannel(&hdac1, &sConfig, DAC_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN DAC1_Init 2 */
  /* USER CODE END DAC1_Init 2 */

}

/**
  * @brief IWDG1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_IWDG1_Init(void) __attribute__((unused));
static void MX_IWDG1_Init(void)
{

  /* USER CODE BEGIN IWDG1_Init 0 */
  /* USER CODE END IWDG1_Init 0 */

  /* USER CODE BEGIN IWDG1_Init 1 */
  /* USER CODE END IWDG1_Init 1 */
  hiwdg1.Instance = IWDG1;
  hiwdg1.Init.Prescaler = IWDG_PRESCALER_4;
  hiwdg1.Init.Window = 4095;
  hiwdg1.Init.Reload = 4095;
  if (HAL_IWDG_Init(&hiwdg1) != HAL_OK)
  {
    Error_Handler();
  }
  iwdg_enabled_runtime = 1;
  printf("[IWDG] INIT presc=4 reload=4095 (t≈~)\r\n");
  /* USER CODE BEGIN IWDG1_Init 2 */
  /* USER CODE END IWDG1_Init 2 */

}

/**
  * @brief SPI2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_SPI2_Init(void)
{

  /* USER CODE BEGIN SPI2_Init 0 */
  /* USER CODE END SPI2_Init 0 */

  /* USER CODE BEGIN SPI2_Init 1 */
  /* USER CODE END SPI2_Init 1 */
  /* SPI2 parameter configuration*/
  hspi2.Instance = SPI2;
  hspi2.Init.Mode = SPI_MODE_MASTER;
  hspi2.Init.Direction = SPI_DIRECTION_2LINES;
  hspi2.Init.DataSize = SPI_DATASIZE_4BIT;
  hspi2.Init.CLKPolarity = SPI_POLARITY_LOW;
  hspi2.Init.CLKPhase = SPI_PHASE_1EDGE;
  hspi2.Init.NSS = SPI_NSS_SOFT;
  hspi2.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_2;
  hspi2.Init.FirstBit = SPI_FIRSTBIT_MSB;
  hspi2.Init.TIMode = SPI_TIMODE_DISABLE;
  hspi2.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
  hspi2.Init.CRCPolynomial = 0x0;
  hspi2.Init.NSSPMode = SPI_NSS_PULSE_ENABLE;
  hspi2.Init.NSSPolarity = SPI_NSS_POLARITY_LOW;
  hspi2.Init.FifoThreshold = SPI_FIFO_THRESHOLD_01DATA;
  hspi2.Init.TxCRCInitializationPattern = SPI_CRC_INITIALIZATION_ALL_ZERO_PATTERN;
  hspi2.Init.RxCRCInitializationPattern = SPI_CRC_INITIALIZATION_ALL_ZERO_PATTERN;
  hspi2.Init.MasterSSIdleness = SPI_MASTER_SS_IDLENESS_00CYCLE;
  hspi2.Init.MasterInterDataIdleness = SPI_MASTER_INTERDATA_IDLENESS_00CYCLE;
  hspi2.Init.MasterReceiverAutoSusp = SPI_MASTER_RX_AUTOSUSP_DISABLE;
  hspi2.Init.MasterKeepIOState = SPI_MASTER_KEEP_IO_STATE_DISABLE;
  hspi2.Init.IOSwap = SPI_IO_SWAP_DISABLE;
  if (HAL_SPI_Init(&hspi2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN SPI2_Init 2 */
  /* USER CODE END SPI2_Init 2 */

}

/**
  * @brief SPI4 Initialization Function
  * @param None
  * @retval None
  */
static void MX_SPI4_Init(void)
{

  /* USER CODE BEGIN SPI4_Init 0 */
  /* USER CODE END SPI4_Init 0 */

  /* USER CODE BEGIN SPI4_Init 1 */
  /* USER CODE END SPI4_Init 1 */
  /* SPI4 parameter configuration*/
  hspi4.Instance = SPI4;
  hspi4.Init.Mode = SPI_MODE_MASTER;
  hspi4.Init.Direction = SPI_DIRECTION_2LINES_TXONLY;
  hspi4.Init.DataSize = SPI_DATASIZE_8BIT;
  hspi4.Init.CLKPolarity = SPI_POLARITY_LOW;
  hspi4.Init.CLKPhase = SPI_PHASE_1EDGE;
  hspi4.Init.NSS = SPI_NSS_SOFT;
  hspi4.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_4;
  hspi4.Init.FirstBit = SPI_FIRSTBIT_MSB;
  hspi4.Init.TIMode = SPI_TIMODE_DISABLE;
  hspi4.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
  hspi4.Init.CRCPolynomial = 0x0;
  hspi4.Init.NSSPMode = SPI_NSS_PULSE_ENABLE;
  hspi4.Init.NSSPolarity = SPI_NSS_POLARITY_LOW;
  hspi4.Init.FifoThreshold = SPI_FIFO_THRESHOLD_01DATA;
  hspi4.Init.TxCRCInitializationPattern = SPI_CRC_INITIALIZATION_ALL_ZERO_PATTERN;
  hspi4.Init.RxCRCInitializationPattern = SPI_CRC_INITIALIZATION_ALL_ZERO_PATTERN;
  hspi4.Init.MasterSSIdleness = SPI_MASTER_SS_IDLENESS_00CYCLE;
  hspi4.Init.MasterInterDataIdleness = SPI_MASTER_INTERDATA_IDLENESS_00CYCLE;
  hspi4.Init.MasterReceiverAutoSusp = SPI_MASTER_RX_AUTOSUSP_DISABLE;
  hspi4.Init.MasterKeepIOState = SPI_MASTER_KEEP_IO_STATE_DISABLE;
  hspi4.Init.IOSwap = SPI_IO_SWAP_DISABLE;
  if (HAL_SPI_Init(&hspi4) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN SPI4_Init 2 */
  /* USER CODE END SPI4_Init 2 */

}

/**
  * @brief TIM1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM1_Init(void)
{

  /* USER CODE BEGIN TIM1_Init 0 */
  /* USER CODE END TIM1_Init 0 */

  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM1_Init 1 */
  /* USER CODE END TIM1_Init 1 */
  htim1.Instance = TIM1;
  htim1.Init.Prescaler = 274;
  htim1.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim1.Init.Period = 2499;
  htim1.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim1.Init.RepetitionCounter = 0;
  htim1.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_PWM_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterOutputTrigger2 = TIM_TRGO2_UPDATE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim1, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 0;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 0;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.BreakFilter = 0;
  sBreakDeadTimeConfig.Break2State = TIM_BREAK2_DISABLE;
  sBreakDeadTimeConfig.Break2Polarity = TIM_BREAK2POLARITY_HIGH;
  sBreakDeadTimeConfig.Break2Filter = 0;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim1, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM1_Init 2 */
  // Разрешаем автоматическое включение основного выхода (MOE) для надёжного старта CH2N
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_ENABLE;
  HAL_TIMEx_ConfigBreakDeadTime(&htim1, &sBreakDeadTimeConfig);
  printf("[PWM] TIM1 initialized: ARR=%lu, PSC=%lu\r\n",
         (unsigned long)htim1.Init.Period, (unsigned long)htim1.Init.Prescaler);
  /* USER CODE END TIM1_Init 2 */
  HAL_TIM_MspPostInit(&htim1);

}

/**
  * @brief TIM2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM2_Init(void)
{

  /* USER CODE BEGIN TIM2_Init 0 */
  /* USER CODE END TIM2_Init 0 */

  TIM_SlaveConfigTypeDef sSlaveConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};

  /* USER CODE BEGIN TIM2_Init 1 */
  /* USER CODE END TIM2_Init 1 */
  htim2.Instance = TIM2;
  htim2.Init.Prescaler = 274;
  htim2.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim2.Init.Period = 4999;
  htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim2) != HAL_OK)
  {
    Error_Handler();
  }
  /* TIM2 uses internal clock; we will use its TRGO to sync others */
  sSlaveConfig.SlaveMode = TIM_SLAVEMODE_DISABLE;
  sSlaveConfig.InputTrigger = TIM_TS_ITR0;
  if (HAL_TIM_SlaveConfigSynchro(&htim2, &sSlaveConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* Emit TRGO on update to reset TIM15/ADCs periodically */
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim2, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 2499;
 
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  if (HAL_TIM_PWM_ConfigChannel(&htim2, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim2, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim2, &sConfigOC, TIM_CHANNEL_3) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM2_Init 2 */
  /* USER CODE END TIM2_Init 2 */
  HAL_TIM_MspPostInit(&htim2);

}

/**
  * @brief TIM3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM3_Init(void)
{

  /* USER CODE BEGIN TIM3_Init 0 */
  /* USER CODE END TIM3_Init 0 */

  TIM_SlaveConfigTypeDef sSlaveConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};

  /* USER CODE BEGIN TIM3_Init 1 */
  /* USER CODE END TIM3_Init 1 */
  htim3.Instance = TIM3;
  htim3.Init.Prescaler = 274;
  htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim3.Init.Period = 4999;
  htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim3) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim3) != HAL_OK)
  {
    Error_Handler();
  }
  sSlaveConfig.SlaveMode = TIM_SLAVEMODE_RESET;
  sSlaveConfig.InputTrigger = TIM_TS_ITR3;
  if (HAL_TIM_SlaveConfigSynchro(&htim3, &sSlaveConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_OC2REF;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim3, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 2499;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  if (HAL_TIM_PWM_ConfigChannel(&htim3, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim3, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim3, &sConfigOC, TIM_CHANNEL_3) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM3_Init 2 */
  /* USER CODE END TIM3_Init 2 */
  HAL_TIM_MspPostInit(&htim3);

}

/**
  * @brief TIM6 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM6_Init(void)
{

  /* USER CODE BEGIN TIM6_Init 0 */
  /* USER CODE END TIM6_Init 0 */

  TIM_MasterConfigTypeDef sMasterConfig = {0};

  /* USER CODE BEGIN TIM6_Init 1 */
  /* USER CODE END TIM6_Init 1 */
  htim6.Instance = TIM6;
  htim6.Init.Prescaler = 274;
  htim6.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim6.Init.Period = 4999;
  htim6.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_Base_Init(&htim6) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim6, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM6_Init 2 */
  // Включаем прерывание TIM6 (для мигания светодиодом в HAL_TIM_PeriodElapsedCallback)
  HAL_NVIC_SetPriority(TIM6_DAC_IRQn, 6, 0);
  HAL_NVIC_EnableIRQ(TIM6_DAC_IRQn);
  /* USER CODE END TIM6_Init 2 */

}

/**
  * @brief TIM15 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM15_Init(void)
{

  /* USER CODE BEGIN TIM15_Init 0 */
  /* USER CODE END TIM15_Init 0 */

  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_SlaveConfigTypeDef sSlaveConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM15_Init 1 */
  /* USER CODE END TIM15_Init 1 */
  htim15.Instance = TIM15;
  htim15.Init.Prescaler = 0;
  htim15.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim15.Init.Period = 999;
  htim15.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim15.Init.RepetitionCounter = 0;
  htim15.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_Base_Init(&htim15) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim15, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim15) != HAL_OK)
  {
    Error_Handler();
  }
  /* TIM15 resets on TIM2 TRGO (ITR1) for aligned restart */
  sSlaveConfig.SlaveMode = TIM_SLAVEMODE_RESET;
  sSlaveConfig.InputTrigger = TIM_TS_ITR1;
  if (HAL_TIM_SlaveConfigSynchro(&htim15, &sSlaveConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim15, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 499;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim15, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 0;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.BreakFilter = 0;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim15, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM15_Init 2 */
  /* USER CODE END TIM15_Init 2 */
  HAL_TIM_MspPostInit(&htim15);

}

/**
  * @brief USART1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART1_UART_Init(void)
{

  /* USER CODE BEGIN USART1_Init 0 */
  /* USER CODE END USART1_Init 0 */

  /* USER CODE BEGIN USART1_Init 1 */
  /* USER CODE END USART1_Init 1 */
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 115200;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_1;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  huart1.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart1.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  huart1.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetTxFifoThreshold(&huart1, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetRxFifoThreshold(&huart1, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_DisableFifoMode(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART1_Init 2 */
  /* USER CODE END USART1_Init 2 */

}

/**
  * Enable DMA controller clock
  */
static void MX_DMA_Init(void)
{

  /* DMA controller clock enable */
  __HAL_RCC_DMA1_CLK_ENABLE();

  /* DMA interrupt init */
  /* DMA1_Stream0_IRQn interrupt configuration */
  HAL_NVIC_SetPriority(DMA1_Stream0_IRQn, 6, 0);
  HAL_NVIC_EnableIRQ(DMA1_Stream0_IRQn);
  /* DMA1_Stream1_IRQn disabled intentionally (ADC2 DMA runs without IRQ) */
  HAL_NVIC_DisableIRQ(DMA1_Stream1_IRQn);

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */
  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOE, Led_Test_Pin|LCD_CS_Pin|LCD_WR_RS_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin : Led_Test_Pin */
  GPIO_InitStruct.Pin = Led_Test_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(Led_Test_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pins : LCD_CS_Pin LCD_WR_RS_Pin */
  GPIO_InitStruct.Pin = LCD_CS_Pin|LCD_WR_RS_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(GPIOE, &GPIO_InitStruct);

  /*Configure GPIO pin : Data_ready_GPIO22_Pin */
  GPIO_InitStruct.Pin = Data_ready_GPIO22_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(Data_ready_GPIO22_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : PA8 */
  GPIO_InitStruct.Pin = GPIO_PIN_8;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  GPIO_InitStruct.Alternate = GPIO_AF0_MCO;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */
  // Настройка RST дисплея и подсветки как GPIO до старта PWM
  GPIO_InitStruct.Pin = LCD_RST_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(LCD_RST_GPIO_Port, &GPIO_InitStruct);
  // Жёсткий аппаратный сброс LCD: low->delay->high
  HAL_GPIO_WritePin(LCD_RST_GPIO_Port, LCD_RST_Pin, GPIO_PIN_RESET);
  HAL_Delay(10);
  HAL_GPIO_WritePin(LCD_RST_GPIO_Port, LCD_RST_Pin, GPIO_PIN_SET);
  HAL_Delay(10);

#if FORCE_BL_GPIO
  // Настройка подсветки как GPIO ТОЛЬКО если используется GPIO режим
  GPIO_InitStruct.Pin = LCD_Led_Pin; /* PE10 */
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(LCD_Led_GPIO_Port, &GPIO_InitStruct);
  // По умолчанию выключаем подсветку (active low -> высокий уровень)
  BL_OFF();
#endif
  HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_SET);
  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
// Заглушки и помощники для индикации
static void UpdateUSBDebug(void) { /* no-op */ }
static const char* usb_state_str(uint8_t s) { (void)s; return ""; }

// Мигание светодиодом и сторож
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  if (htim->Instance == TIM6) {
    static uint16_t div = 0; // делитель частоты мигания
    tim6_irq_count++;
    if (++div >= 20) {       // быстреее мигание для наглядности (~2–4 Гц в зависимости от частоты TIM6)
      div = 0;
      #if !SAFE_MINIMAL
        HAL_GPIO_TogglePin(Led_Test_GPIO_Port, Led_Test_Pin);
        tim6_led_toggled_flag = 1; // попросим main вывести лог
        tim6_led_toggle_counter++;
      #endif
    }
#ifdef HAL_IWDG_MODULE_ENABLED
    #ifndef DIAG_DISABLE_IWDG
      HAL_IWDG_Refresh(&hiwdg1); // кормим сторож (пока без унифицированного макроса)
    #endif
#endif
    /* Пинаем USB vendor таск периодическим тиком, чтобы продвигать состояние */
    #if !SAFE_MINIMAL
      extern void usb_vendor_periodic_tick(void);
      usb_vendor_periodic_tick();
    #endif
  }
}

static void lcd_print_padded_if_changed(int x, int y, const char* new_text,
                                        char *prev, size_t buf_sz,
                                        uint8_t max_len, uint8_t font_height,
                                        uint16_t fg, uint16_t bg)
{
    if(!new_text || !prev || buf_sz == 0) return;
    if(strncmp(new_text, prev, buf_sz-1) == 0) return;
    char line[32];
    size_t n = strlen(new_text);
    if(n > max_len) n = max_len;
    memcpy(line, new_text, n);
    while(n < max_len) line[n++] = ' ';
    line[n] = 0;
    LCD_ShowString_Size((uint16_t)x, (uint16_t)y, line, font_height, fg, bg);
    strncpy(prev, new_text, buf_sz-1);
    prev[buf_sz-1] = 0;
}

void UpdateLCDStatus(void){ need_usb_status_refresh = 1; }

void DrawStarIndicator(void){
  PROG('S');
#ifdef DIAG_SKIP_LCD
  PROG('s');
  return;
#endif
    if(!lcd_ready) {
    // LCD not ready
        return;
    }
    const int x_right = 151;
    LCD_ShowString_Size(x_right, 0, star_visible?"*":" ", 16, YELLOW, BLACK);
  // Временно отключены остальные вызовы
    /*
    printf("[FUNC] DrawStarIndicator: Before HAL_GetTick\r\n");
    uint32_t now = HAL_GetTick();
    printf("[FUNC] DrawStarIndicator: After HAL_GetTick, now=%lu\r\n", (unsigned long)now);
  // Хост присутствует при свежем SOF (<400мс) или в состоянии SUSPENDED
  printf("[FUNC] DrawStarIndicator: Before dt_sof calculation\r\n");
  uint32_t dt_sof = now - g_usb_last_sof_ms;
  printf("[FUNC] DrawStarIndicator: After dt_sof calculation, dt_sof=%lu\r\n", (unsigned long)dt_sof);
  printf("[FUNC] DrawStarIndicator: Before host_present calculation\r\n");
  uint8_t host_present = (hUsbDeviceHS.dev_state == USBD_STATE_SUSPENDED) || (dt_sof < 400);
  printf("[FUNC] DrawStarIndicator: After host_present calculation, host_present=%u\r\n", (unsigned)host_present);
    printf("[FUNC] DrawStarIndicator: Before dev_state access\r\n");
    uint8_t s = hUsbDeviceHS.dev_state;
    printf("[FUNC] DrawStarIndicator: After dev_state access, s=%u\r\n", (unsigned)s);
    uint16_t u_color = RED;
    if(host_present){
        switch(s){
            case USBD_STATE_ADDRESSED:  u_color = YELLOW; break;
            case USBD_STATE_CONFIGURED: u_color = GREEN;  break;
            case USBD_STATE_SUSPENDED:  u_color = CYAN;   break;
            default: u_color = RED; break;
        }
    }
    printf("[FUNC] DrawStarIndicator: Color determined, u_color=%u\r\n", (unsigned)u_color);
    static uint16_t prev_color = 0xFFFF;
    printf("[FUNC] DrawStarIndicator: prev_color=%u\r\n", (unsigned)prev_color);
    if(prev_color != u_color){
        printf("[FUNC] DrawStarIndicator: Before LCD_ShowString_Size 2\r\n");
        LCD_ShowString_Size(x_right, 16, "U", 16, u_color, BLACK);
        printf("[FUNC] DrawStarIndicator: After LCD_ShowString_Size 2\r\n");
        prev_color = u_color;
    }
    */
  PROG('s');
}

void DrawUSBStatus(void){
  PROG('U');
#ifdef DIAG_SKIP_LCD
  PROG('u');
  return;
#endif
    if(!lcd_ready) {
    // LCD not ready
        return;
    }
    static char prev_line0[16] = "";
    static char prev_line1[16] = "";
  static char prev_line2[16] = "";
    static char prev_line3[16] = "";
  static uint64_t prev_tx_bytes = 0ULL;
  static uint64_t prev_tx_samples = 0ULL;
  static uint32_t prev_rate_calc_ms = 0;
  static uint32_t last_rate_bps = 0; /* приблизительно bytes/sec */
  static uint32_t last_rate_sps = 0; /* семплов в секунду (оба канала суммарно) */
  uint32_t now = HAL_GetTick();
  /* Хост присутствует только при свежем SOF (<400мс) или SUSPENDED */
  uint32_t dt_sof = now - g_usb_last_sof_ms;
  uint8_t host_present = (hUsbDeviceHS.dev_state == USBD_STATE_SUSPENDED) || (dt_sof < 400);
    uint8_t s = hUsbDeviceHS.dev_state;

    const char *text0; uint16_t color0;
    if(!host_present){ text0 = "USB:--"; color0 = RED; }
    else {
        switch(s){
            case USBD_STATE_ADDRESSED: text0 = "USB:ADR"; color0 = YELLOW; break;
            case USBD_STATE_CONFIGURED: text0 = "USB:CFG"; color0 = GREEN; break;
            case USBD_STATE_SUSPENDED:  text0 = "USB:SUS"; color0 = CYAN;  break;
            default: text0 = "USB:--"; color0 = RED; break;
        }
    }
    lcd_print_padded_if_changed(0,0,text0, prev_line0, sizeof(prev_line0), 7, 12, color0, BLACK);

  char ds_buf[12];
  if(!host_present) snprintf(ds_buf,sizeof(ds_buf),"DS:--");
  else snprintf(ds_buf,sizeof(ds_buf),"DS:%02u", (unsigned)s);
    lcd_print_padded_if_changed(0,12, ds_buf, prev_line1, sizeof(prev_line1), 5, 12, WHITE, BLACK);

  /* Скорость обмена: считаем раз в ~500мс (байты/с и семплы/с) */
  if(host_present && s == USBD_STATE_CONFIGURED){
    uint32_t dt = now - prev_rate_calc_ms;
    if(dt >= 500){
      uint64_t cur = vnd_get_total_tx_bytes();
      uint64_t cur_samples = vnd_get_total_tx_samples();
      uint64_t dbytes = (cur >= prev_tx_bytes)? (cur - prev_tx_bytes):0ULL;
      uint64_t dsamps = (cur_samples >= prev_tx_samples)? (cur_samples - prev_tx_samples):0ULL;
      /* bytes per second approximation */
      if(dt > 0){
        last_rate_bps = (uint32_t)( (dbytes * 1000ULL) / dt );
        last_rate_sps = (uint32_t)( (dsamps * 1000ULL) / dt );
      }
      prev_tx_bytes = cur;
      prev_tx_samples = cur_samples;
      prev_rate_calc_ms = now;
    }
    /* Формат строки: S:xxxxx (sps) на первом плане; экономный вывод */
    char rate_buf[16];
    if(last_rate_sps >= 100000){
      uint32_t ks = last_rate_sps / 1000U;
      snprintf(rate_buf, sizeof(rate_buf), "S:%3uK", (unsigned)ks);
    } else {
      snprintf(rate_buf, sizeof(rate_buf), "S:%4u", (unsigned)last_rate_sps);
    }
  /* Очистка legacy VID/PID убрана */
    /* Используем ширину 12 символов для гарантированного затирания хвоста */
    lcd_print_padded_if_changed(0,24, rate_buf, prev_line2, sizeof(prev_line2), 12, 12, vnd_is_streaming()?GREEN:WHITE, BLACK);
    /* Показываем VID/PID ниже скорости (фиксированное положение y=36) */
    {
      uint16_t vid = USBD_Desc_GetVID();
      uint16_t pid = USBD_Desc_GetPID();
      char vidpid[20];
      snprintf(vidpid, sizeof(vidpid), "VID:%04X PID:%04X", (unsigned)vid, (unsigned)pid);
      lcd_print_padded_if_changed(0,36, vidpid, prev_line3, sizeof(prev_line3), 16, 12, WHITE, BLACK);
    }
  } else {
  /* Очистка legacy VID/PID убрана */
    lcd_print_padded_if_changed(0,24, host_present?"S:----":"S:----", prev_line2, sizeof(prev_line2), 12, 12, WHITE, BLACK);
    /* При отсутствии хоста затираем VID/PID строку */
    lcd_print_padded_if_changed(0,36, "VID:---- PID:----", prev_line3, sizeof(prev_line3), 16, 12, WHITE, BLACK);
    prev_rate_calc_ms = now;
    prev_tx_bytes = vnd_get_total_tx_bytes();
    prev_tx_samples = vnd_get_total_tx_samples();
    last_rate_bps = 0;
    last_rate_sps = 0;
  }
  PROG('u');
}

// (реализации Error_Handler/MPU_Config/assert_failed оставлены в стандартных секциях ниже)
/* USER CODE END 4 */

 /* MPU Configuration */

void MPU_Config(void)
{
  MPU_Region_InitTypeDef MPU_InitStruct = {0};

  /* Disables the MPU */
  HAL_MPU_Disable();

  /** Initializes and configures the Region and the memory to be protected
  */
  MPU_InitStruct.Enable = MPU_REGION_ENABLE;
  MPU_InitStruct.Number = MPU_REGION_NUMBER0;
  MPU_InitStruct.BaseAddress = 0x0;
  MPU_InitStruct.Size = MPU_REGION_SIZE_4GB;
  MPU_InitStruct.SubRegionDisable = 0x87;
  MPU_InitStruct.TypeExtField = MPU_TEX_LEVEL0;
  MPU_InitStruct.AccessPermission = MPU_REGION_NO_ACCESS;
  MPU_InitStruct.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
  MPU_InitStruct.IsShareable = MPU_ACCESS_SHAREABLE;
  MPU_InitStruct.IsCacheable = MPU_ACCESS_NOT_CACHEABLE;
  MPU_InitStruct.IsBufferable = MPU_ACCESS_NOT_BUFFERABLE;

  HAL_MPU_ConfigRegion(&MPU_InitStruct);
  /* Enables the MPU */
  HAL_MPU_Enable(MPU_PRIVILEGED_DEFAULT);

}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* Визуальный индикатор ошибки: мигание подсветкой LCD_Led (PE10, active-low) и LED (PE3) */
  __disable_irq();
  /* Включаем тактирование GPIOE на случай ранней ошибки */
  __HAL_RCC_GPIOE_CLK_ENABLE();
  int led_idx = __builtin_ctz(Led_Test_Pin);
  int bl_idx  = __builtin_ctz(LCD_Led_Pin);
  GPIOE->MODER &= ~(3u << (led_idx*2));
  GPIOE->MODER |=  (1u << (led_idx*2));
  GPIOE->MODER &= ~(3u << (bl_idx*2));
  GPIOE->MODER |=  (1u << (bl_idx*2));
  for(;;){
    /* LED ON, BL ON (active-low -> reset) */
    GPIOE->BSRR = Led_Test_Pin;
    GPIOE->BSRR = (LCD_Led_Pin << 16);
    for(volatile uint32_t d=0; d<24000000UL; ++d){ __NOP(); }
    /* LED OFF, BL OFF (active-low -> set) */
    GPIOE->BSRR = (Led_Test_Pin << 16);
    GPIOE->BSRR = LCD_Led_Pin;
    for(volatile uint32_t d=0; d<12000000UL; ++d){ __NOP(); }
  }
  /* USER CODE END Error_Handler_Debug */
}
/* Fault handlers moved back to stm32h7xx_it.c (removed duplicated minimal versions) */
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
