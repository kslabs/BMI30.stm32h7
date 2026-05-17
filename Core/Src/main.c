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
#include <stdlib.h>
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
#include "adc_stream.h"
#include "ws2812_spi.h"

/* Глобальные хэндлы периферии (стандарт для CubeMX, ранее отсутствовали в файле) */
ADC_HandleTypeDef hadc1;
ADC_HandleTypeDef hadc2;
SPI_HandleTypeDef hspi1;
SPI_HandleTypeDef hspi3;
SPI_HandleTypeDef hspi4;
TIM_HandleTypeDef htim1;
TIM_HandleTypeDef htim2;
TIM_HandleTypeDef htim3;
TIM_HandleTypeDef htim5;
TIM_HandleTypeDef htim6;
TIM_HandleTypeDef htim15;
IWDG_HandleTypeDef hiwdg1;
UART_HandleTypeDef huart1;
UART_HandleTypeDef huart2;
DAC_HandleTypeDef  hdac1;
DMA_HandleTypeDef  hdma_adc1;
DMA_HandleTypeDef  hdma_adc2;
DMA_HandleTypeDef  hdma_spi3_tx;
DMA_HandleTypeDef  hdma_usart2_rx;
DMA_HandleTypeDef  hdma_tim15_up;  // DMA для автоматической записи ARR
DMA_HandleTypeDef  hdma_tim1_up;   // DMA для аппаратной огибающей оптического TX

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
/* Новый флаг: запрос на ПОЛНЫЙ аппаратный сброс по команде 0x22 */
volatile uint8_t  need_hard_reset = 0;
volatile uint32_t systick_heartbeat = 0; /* глобальный счётчик SysTick для stm32h7xx_it.c */
uint8_t star_visible = 0;
uint8_t auto_stream_started = 0;
uint8_t init_messages_ready = 0;
/* Метка последнего синхро-фронта (для LCD/диагностики) */
volatile uint32_t sync_last_edge_ms = 0;
volatile uint8_t sync_edge_seen = 0;
volatile uint8_t sync_align_pending = 0;
volatile uint8_t sync_phase_lock_armed = 0;
volatile uint8_t sync_phase_lock_active = 0;
volatile uint8_t sync_restart_on_edge = 0;
volatile uint32_t sync_edge_count = 0;
/* Измерение периода PD5 через TIM5 (275 MHz) */
volatile uint32_t sync_tim5_period_ticks = 0;
#define SYNC_TARGET_PHASE_AUTO 0xFFFFFFFFu
static volatile uint32_t g_sync_target_phase_ticks = SYNC_TARGET_PHASE_AUTO;
static volatile uint8_t tim15_arr_pulse_stage = 0u;
static volatile uint32_t tim15_arr_pulse_nominal = 0u;
static volatile uint32_t tim15_arr_pulse_count = 0u;
static volatile int32_t tim15_arr_hold_offset = 0;
static volatile int32_t tim15_arr_hold_target_offset = 0;
static volatile uint8_t sync_phase_diag_pending = 0u;
static volatile uint8_t sync_phase_diag_role = 'O';
static volatile uint16_t sync_phase_diag_sample = 0u;
static volatile uint16_t sync_phase_diag_samples = 0u;
static volatile uint32_t sync_phase_diag_ticks = 0u;
static volatile uint32_t sync_phase_diag_arr = 0u;
static volatile uint32_t sync_phase_diag_bufs = 0u;
static volatile uint32_t sync_phase_diag_edge = 0u;
static volatile uint8_t sync_phase_diag_kind = 0u;
static volatile int32_t sync_phase_last_error_ticks = 0;
static volatile int32_t sync_phase_last_pulse_delta = 0;
static volatile uint32_t sync_phase_fast_edges = 0u;
static volatile uint32_t sync_phase_fast_pulses = 0u;
static volatile uint32_t sync_phase_fast_skip_busy = 0u;
static volatile uint32_t sync_phase_fast_skip_spacing = 0u;
static volatile uint32_t sync_phase_fast_last_spacing = 0u;
static volatile uint32_t sync_phase_fast_last_buf = 0xFFFFFFFFu;
static volatile uint8_t optic_sensor_state_public = 0u;
static volatile uint32_t optic_last_change_ms = 0u;
static volatile uint16_t optic_active_hold_deciseconds = 30u;

#ifndef RS485_USART2_USE_DMA_RX
/* DMA RX временно отключён: для sync критична фаза в момент IRQ, поэтому
   основной RX-путь идёт через быстрый drain USART2->RDR прямо в IRQ. */
#define RS485_USART2_USE_DMA_RX 0u
#endif
#define RS485_DMA_RX_BUFFER_SIZE 256u

static uint8_t rs485_tx_byte = 0xA5u;
static uint8_t rs485_dma_rx_buf[RS485_DMA_RX_BUFFER_SIZE] = {0u};
static volatile uint16_t rs485_dma_rx_rd = 0u;
static volatile uint32_t rs485_dma_rx_overrun_count = 0u;
static volatile uint32_t rs485_last_rx_ms = 0;
static volatile uint32_t rs485_rx_packets = 0;
static volatile uint32_t rs485_tx_packets = 0;
/* boot_listen_ms: UID-стагированная задержка (200-2000ms), вычисляется при init */
static uint32_t rs485_boot_listen_ms = 0u;
static volatile uint8_t rs485_tx_busy = 0;
static volatile uint32_t rs485_tx_start_ms = 0u;
static volatile uint8_t rs485_last_sync_edge_kind = 0u;
static volatile uint8_t rs485_sync_phase_relation = 0u;
static volatile uint8_t rs485_sync_locked = 0u;
static volatile uint8_t rs485_sync_led_active = 0u;
static volatile int8_t rs485_sync_relation_score = 0;
static volatile uint8_t rs485_anti_phase_recovery_active = 0u;
static volatile uint8_t rs485_anti_phase_recovery_packets = 0u;
static volatile uint8_t rs485_anti_phase_recovery_request = 0u;
static volatile uint32_t rs485_sync_buf_div4 = 0;
static volatile uint8_t rs485_slave_count_estimate = 0;
static volatile uint32_t rs485_status_window_ticks = 0u;
static volatile uint32_t rs485_status_seen_mask = 0u;
static volatile uint8_t rs485_status_current_window_phase = 0xFFu;
static volatile uint8_t rs485_status_master_expected_phase = 0xFFu;
static volatile uint8_t rs485_status_window_active = 0u;
static volatile uint8_t rs485_status_window_after_tx = 0u;
static volatile uint32_t rs485_status_tx_due_ticks = 0u;
static volatile uint8_t rs485_status_tx_pending = 0u;
static volatile uint8_t rs485_status_tx_sent = 0u;
static volatile uint8_t rs485_status_slot_local_tx = 0u;
static volatile uint8_t rs485_status_cycle_count = 0u;
static volatile uint8_t rs485_status_slot_response_seen = 0u;
static volatile uint8_t rs485_status_slot_response_byte = 0u;
static volatile uint32_t rs485_uart_error_count = 0u;
static volatile uint8_t rs485_status_peer_bytes[31] = {0u};
static volatile uint32_t rs485_status_peer_last_ms[31] = {0u};
static volatile uint32_t rs485_status_local_tx_count = 0u;
static volatile uint32_t rs485_status_local_tx_complete_count = 0u;
static volatile uint32_t rs485_status_peer_rx_count = 0u;
static volatile uint32_t rs485_status_deferred_tx_count = 0u;
static volatile uint32_t rs485_status_window_total_count = 0u;
static volatile uint32_t rs485_status_window_ok_count = 0u;
static volatile uint32_t rs485_status_window_miss_count = 0u;
static volatile uint32_t rs485_status_local_slot_expected_count = 0u;
static volatile uint32_t rs485_status_local_slot_miss_count = 0u;
static volatile uint8_t rs485_status_local_slot_expected = 0u;
static uint8_t rs485_local_node_id = 0u;
static uint8_t rs485_local_uid_hint = 1u;
static uint32_t rs485_master_claim_delay_ms = 1000u;
static volatile uint8_t rs485_tx_queue[32] = {0};
static volatile uint8_t rs485_tx_queue_head = 0u;
static volatile uint8_t rs485_tx_queue_tail = 0u;
static volatile uint8_t rs485_tx_queue_count = 0u;
static volatile uint8_t rs485_discovery_next_id = 1u;
static volatile uint8_t rs485_discovery_wait_id = 0u;
static volatile uint8_t rs485_discovery_response_seen = 0u;
static volatile uint8_t rs485_discovery_window_open = 0u;
static volatile uint8_t rs485_discovery_phase_wait_response = 0u;
static volatile uint8_t rs485_reply_pending_id = 0u;
static volatile uint32_t rs485_discovery_seen_mask = 0u;
static volatile uint32_t rs485_discovery_scan_mask = 0u;
static uint32_t rs485_local_uid_words[3] = {0u, 0u, 0u};
static uint8_t rs485_local_uid_bytes[12] = {0u};
static uint32_t rs485_peer_uid_words[3] = {0u, 0u, 0u};
static uint8_t rs485_peer_uid_bytes[12] = {0u};
static volatile uint8_t rs485_peer_uid_valid = 0u;
static volatile int8_t rs485_peer_uid_cmp = 0;
static volatile uint32_t rs485_peer_uid_last_ms = 0u;
static volatile uint8_t rs485_uid_tx_pending = 0u;
static volatile uint8_t rs485_uid_tx_retries = 0u;
static volatile uint32_t rs485_uid_tx_next_ms = 0u;
static volatile uint32_t rs485_uid_last_tx_ms = 0u;
static volatile uint32_t rs485_sync_tx_suppressed_until_ms = 0u;
static volatile uint8_t rs485_uid_rx_active = 0u;
static volatile uint8_t rs485_uid_rx_index = 0u;
static uint8_t rs485_uid_rx_buf[13] = {0u};

#define RS485_DISCOVERY_REQ_BASE 0x40u
#define RS485_DISCOVERY_ACK_BASE 0x80u
#define RS485_DISCOVERY_ID_MASK  0x1Fu
#define RS485_DISCOVERY_MAX_ID   31u
#define RS485_SYNC7_BASE         0x25u
#define RS485_SYNC_EDGE_BIT      0x80u
#define RS485_UID_FRAME_MAGIC    0xE1u
#define RS485_UID_FRAME_BYTES    12u
#define RS485_UID_FRAME_TAIL     (RS485_UID_FRAME_BYTES + 1u)
#define RS485_SYNC_PRESENT_MS    250u
#define RS485_BOOT_LISTEN_MS     1000u
#define RS485_MASTER_CLAIM_MS    1000u
#define RS485_PEER_UID_STALE_MS  1500u
#define RS485_UID_RETRY_MS       40u
#define RS485_UID_ANNOUNCE_DELAY_MS 6u
#define RS485_UID_MASTER_ANNOUNCE_MS 250u
#define RS485_UID_SLOT_STEP_MS   7u
#define RS485_UID_SLOT_COUNT     16u
#define RS485_UID_ARBITRATION_QUIET_MS 220u
#define RS485_TX_QUEUE_CAPACITY  32u
#define RS485_STATUS_ID_MASK          0x1Fu
#define RS485_STATUS_OPTIC_BIT        0x20u
#define RS485_STATUS_TX_ENABLE_BIT    0x40u
#define RS485_STATUS_LABEL_BIT        0x80u
#define RS485_STATUS_WINDOW_DIV       4u
#define RS485_STATUS_SLOT_STRIDE      2u
/* Искусственную паузу убираем: к моменту приёма полного sync-байта линия уже свободна,
   а лишний guard только добавляет джиттер к ответу slave. */
#define RS485_STATUS_RESPONSE_DELAY_BYTES 0u
#define RS485_STATUS_PEER_HOLD_MS     30u
#define RS485_SYNC_RELATION_UNKNOWN   0u
#define RS485_SYNC_RELATION_IN_PHASE  1u
#define RS485_SYNC_RELATION_ANTI_PHASE 2u

static inline uint8_t rs485_sync_read_local_marker_phase(void)
{
  return (uint8_t)(adc_stream_get_marker_level() & 1u);
}

static inline uint8_t rs485_sync_edge_kind_from_marker_level(uint8_t marker_level)
{
  return (uint8_t)((marker_level ^ 1u) & 1u);
}

static uint32_t rs485_sync_get_tim5_tick_hz(void)
{
  uint32_t tim_clk = HAL_RCC_GetPCLK1Freq();
  uint32_t d2ppre1 = (RCC->D2CFGR & RCC_D2CFGR_D2PPRE1_Msk) >> RCC_D2CFGR_D2PPRE1_Pos;

  if (d2ppre1 != 0u) {
    tim_clk *= 2u;
  }

  return tim_clk;
}

static uint32_t rs485_sync_get_uart_packet_ticks(void)
{
  uint32_t tim_clk = rs485_sync_get_tim5_tick_hz();
  uint32_t baud = huart2.Init.BaudRate;

  if (baud == 0u) {
    return 0u;
  }

  return (uint32_t)((((uint64_t)tim_clk * 10u) + ((uint64_t)baud / 2u)) / (uint64_t)baud);
}

static uint32_t rs485_sync_get_uart_bit_ticks(void)
{
  uint32_t packet_ticks = rs485_sync_get_uart_packet_ticks();

  if (packet_ticks == 0u) {
    return 0u;
  }

  return (packet_ticks + 5u) / 10u;
}

static int32_t rs485_sync_wrap_phase_ticks(int32_t phase_ticks, uint32_t period_ticks)
{
  int32_t period = (int32_t)period_ticks;
  int32_t half_period = (int32_t)(period_ticks / 2u);

  if (period <= 0) {
    return phase_ticks;
  }

  while (phase_ticks > half_period) {
    phase_ticks -= period;
  }
  while (phase_ticks < -half_period) {
    phase_ticks += period;
  }

  return phase_ticks;
}

__attribute__((unused)) static uint32_t rs485_sync_get_auto_target_phase_ticks(uint32_t period_ticks)
{
  extern uint16_t adc_stream_get_active_samples(void);
  uint32_t uart_packet_ticks = rs485_sync_get_uart_packet_ticks();
  uint32_t active_samples = adc_stream_get_active_samples();
  uint32_t sample_ticks = 0u;
  uint32_t rx_comp_ticks = 0u;

  if (period_ticks == 0u) {
    return 0u;
  }

  if (active_samples != 0u) {
    sample_ticks = period_ticks / active_samples;
  }

  rx_comp_ticks = uart_packet_ticks + (sample_ticks * (uint32_t)SYNC_TARGET_PHASE_SAMPLES);

  /* sync-байт приходит после реального фронта master, поэтому целевая фаза
     для slave должна учитывать длительность приёма, а не сворачиваться к
     (period - delay). */
  if (rx_comp_ticks >= period_ticks) {
    rx_comp_ticks %= period_ticks;
  }

  return rx_comp_ticks;
}

int32_t tim15_get_default_target_phase_ticks(void)
{
  extern uint16_t adc_stream_get_active_samples(void);
  uint32_t active_samples = adc_stream_get_active_samples();
  uint32_t period_ticks = sync_tim5_period_ticks;
  uint32_t sample_ticks = 0u;
  uint32_t uart_packet_ticks = 0u;
  int32_t target_ticks = 0;

  if ((active_samples != 0u) && (period_ticks != 0u)) {
    sample_ticks = period_ticks / active_samples;
  }

  if (sample_ticks == 0u) {
    sample_ticks = 1144u;
  }

  /* RX IRQ видит sync только после полного UART-байта. Чтобы локальная фаза
     была привязана к событию master, а не к концу приема байта, AUTO-цель
     сдвигаем вправо на длительность sync-пакета. */
  uart_packet_ticks = rs485_sync_get_uart_packet_ticks();
  target_ticks = (int32_t)sample_ticks * (int32_t)SYNC_TARGET_PHASE_SAMPLES;
  target_ticks += (int32_t)uart_packet_ticks;
  return rs485_sync_wrap_phase_ticks(target_ticks, period_ticks);
}

static void rs485_sync_on_packet_received(uint8_t edge_kind);
static void rs485_discovery_on_sync_received(void);
static void rs485_discovery_on_request(uint8_t value);
static void rs485_discovery_on_response(uint8_t value);
static void rs485_discovery_reset_master_scan(void);
static uint32_t rs485_status_get_effective_period_ticks(void);
static uint32_t rs485_status_get_window_ticks(uint32_t period_ticks);
static void rs485_status_wait_byte_times(uint32_t byte_count);
static uint8_t rs485_status_count_recent_peers(uint32_t now_ms, uint32_t hold_ms);
static uint8_t rs485_status_build_local_byte(void);
static void rs485_status_reset_window_state(void);
static void rs485_status_finalize_window(void);
static void rs485_status_begin_window(void);
static void rs485_status_on_received(uint8_t value);
static void rs485_status_service(void);
static uint8_t rs485_is_sync_byte(uint8_t value);
static void rs485_process_received_byte(uint8_t value);
static void rs485_dma_rx_start(void);
static void rs485_dma_rx_service(void);
void rs485_usart2_irq_rx_service(void);
static void rs485_load_local_uid(void);
static void rs485_uid_rx_reset(void);
static uint8_t rs485_uid_checksum(const uint8_t *uid_bytes);
static int8_t rs485_compare_uid_words(const uint32_t *lhs, const uint32_t *rhs);
static void rs485_uid_schedule_announce(uint32_t now_ms, uint8_t retries, uint32_t delay_ms);
static void rs485_uid_service(uint32_t now_ms);
static void rs485_uid_handle_received(const uint8_t *uid_bytes);
static uint32_t rs485_uid_get_announce_delay_ms(void);
static uint32_t rs485_compute_uid_mix(void);
static uint8_t rs485_compute_local_node_id(void);
static uint32_t rs485_compute_master_claim_delay_ms(void);
static void rs485_sync_auto_role_service(uint32_t now_ms);
static void arr_auto_tune_service(void);
static void tim15_request_hold_offset(int32_t arr_delta);
static uint8_t tim15_schedule_arr_pulse(int32_t arr_delta);
static void sync_phase_handle_irq_fast(uint16_t sample_idx, uint16_t active_samples);
static void phase_micro_adjust_service(void);
static void sync_phase_monitor_service(void);
static void tune_led_service(uint32_t now_ms);
static uint8_t rs485_count_bits_u32(uint32_t value);
static void rs485_tx_queue_push(uint8_t value);
static void rs485_tx_queue_push_front(uint8_t value);
static uint8_t rs485_tx_queue_pop(uint8_t *value);
static void rs485_tx_kick(void);
static void rs485_sync_start_tx_byte(uint8_t value);
static void rs485_sync_service_tx(void);
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
static char stage_log[32][16] __attribute__((unused));
static int  stage_count __attribute__((unused)) = 0;
static void FlushStageLog(void) __attribute__((unused));
static void FlushStageLog(void) { /* no-op в безопасном режиме */ }

/* Дефолты для флагов сборки, чтобы они не оставались «неопределёнными» */
#ifndef MINIMAL_BRINGUP
#define MINIMAL_BRINGUP 0
#endif
#ifndef ENABLE_SOFT_USB_RECOVERY
#define ENABLE_SOFT_USB_RECOVERY 1
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
static void diag_trap(uint8_t code) __attribute__((unused));
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
static void MX_SPI3_Init(void);
static void MX_TIM1_Init(void);
static void MX_SPI1_Init(void);
static void MX_TIM6_Init(void);
static void MX_ADC1_Init(void);
static void MX_ADC2_Init(void);
static void MX_DAC1_Init(void);
static void MX_TIM2_Init(void);
static void MX_TIM3_Init(void);
static void MX_TIM5_Init(void);
static void MX_TIM15_Init(void);
static void MX_IWDG1_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_USART2_UART_Init(void);
/* USER CODE BEGIN PFP */
uint32_t tim2_apply_profile_window(void);
void UpdateLCDStatus(void);
void DrawStarIndicator(void);
static void __attribute__((unused)) UpdateUSBDebug(void); // теперь будет пустая заглушка
void DrawUSBStatus(void);
static const char* usb_state_str(uint8_t s) __attribute__((unused)); // пустая заглушка
static uint32_t optic_tim1_get_input_clk_hz(void);
static void optic_sensor_service(uint32_t now_ms);
static void ws2812_status_service(uint32_t now_ms);
static void pb2_spi3_direct_test_service(uint32_t now_ms);
static void ws2812_gpio_test_init(void);
static void ws2812_gpio_test_service(uint32_t now_ms);
static void optic_tx_apply_runtime_pattern(void);
static void ws2812_test_button_service(uint32_t now_ms);
static uint8_t diag_get_alarm_text(char *buf, size_t buf_sz, uint16_t *color_out);
static void diag_alarm_com_service(uint32_t now_ms);
static uint8_t optic_tx_power_to_percent(uint8_t power_level);
static uint16_t optic_tx_power_badge_color(uint8_t power_level);
static void optic_tx_start(void);
// Forward declaration to avoid implicit declaration and linkage mismatch
static void lcd_print_padded_if_changed(int x, int y, const char* new_text,
                                        char *prev, size_t buf_sz,
                                        uint8_t max_len, uint8_t font_height,
                                        uint16_t fg, uint16_t bg,
                                        uint16_t *prev_fg, uint16_t *prev_bg);
static void lcd_draw_badge_if_changed(int x, int y, const char* new_text,
                    char *prev, size_t buf_sz,
                    uint8_t max_len, uint8_t font_height,
                    uint8_t pad_x, uint8_t pad_y,
                    uint16_t fg, uint16_t bg,
                                      uint16_t *prev_fg, uint16_t *prev_bg);
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

#ifndef PB2_SPI3_DIRECT_TEST_MODE
#define PB2_SPI3_DIRECT_TEST_MODE 0
#endif

#ifndef WS2812_SPI_SCOPE_TEST_MODE
#define WS2812_SPI_SCOPE_TEST_MODE 0
#endif

#ifndef WS2812_BLUE_TEST_MODE
#define WS2812_BLUE_TEST_MODE 0
#endif

#ifndef WS2812_COLOR_CYCLE_TEST_MODE
#define WS2812_COLOR_CYCLE_TEST_MODE 0
#endif

#ifndef WS2812_GPIO_BITBANG_TEST_MODE
#define WS2812_GPIO_BITBANG_TEST_MODE 0
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
// Время запасного «хвоста» окна TIM2 (ADC остановится, когда CH1=LOW)
// Держим умеренный хвост 400 мкс, чтобы не срезать полезное окно 200 Гц
#define TIM2_WINDOW_GUARD_US 400u
// Режим управления подсветкой: 0 = PWM на TIM1_CH2N(PE10), 1 = принудительно GPIO
#ifndef FORCE_BL_GPIO
#define FORCE_BL_GPIO 1
#endif
#define OPTIC_TX_PWM_TARGET_HZ 38000u
#define OPTIC_INPUT_FILTER_MS  2000u
#define OPTIC_TX_BURST_ON_CYCLES  40u // 39-44
#define OPTIC_TX_BURST_OFF_CYCLES 8u  // 9-4
/* Период пачки = ON + OFF импульсов несущей */
#define OPTIC_TX_BURST_PERIOD  (OPTIC_TX_BURST_ON_CYCLES + OPTIC_TX_BURST_OFF_CYCLES)
#define OPTIC_TX_POWER_MAX 255u
#define OPTIC_ACTIVE_HOLD_DEFAULT_DS 30u
#define OPTIC_ACTIVE_HOLD_MAX_DS     600u
#define WS2812_TEST_BUTTON_DEBOUNCE_MS 30u
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
// === UART1 RX мониторинг для индикации приходящих байт (COM4) ===
// По любой принятой байтовой посылке зажигаем LED и гасим через ~100ms.
static volatile uint32_t uart1_led_off_tick = 0;          // таймаут выключения LED после RX
static uint8_t uart1_rx_byte = 0;                         // одиночный байт приёмника
static volatile uint32_t uart1_rx_count = 0;              // счётчик принятых байт
static volatile uint32_t uart1_last_rx_ms = 0;            // время последнего приёма
static volatile ws2812_pattern_t g_ws2812_test_pattern = WS2812_PATTERN_OFF;
// Кольцевой буфер для потенциального анализа команд (пока только индикация)
#define UART1_RX_RING_SZ 128
static uint8_t uart1_rx_ring[UART1_RX_RING_SZ];
static volatile uint16_t uart1_rx_ring_wr = 0;
static volatile uint16_t uart1_rx_ring_rd = 0;
// Линейный буфер команды до CR/LF
#define UART1_CMD_MAX 96
static char uart1_cmd_buf[UART1_CMD_MAX];
static uint16_t uart1_cmd_len = 0;
static volatile uint8_t optic_tx_power_level = OPTIC_TX_POWER_MAX;
static volatile uint8_t optic_tx_started = 0u;
// Быстрый inline для установки LED (используем уже определённые макросы LED_ON/LED_OFF ниже)
static inline void uart1_rx_led_pulse(void){
  LED_ON();
  uart1_led_off_tick = HAL_GetTick() + 100; // держим LED включённым 100мс после каждого байта
}

uint8_t dynamic_led_set_pattern(uint8_t pattern_id)
{
  ws2812_pattern_t pattern = (ws2812_pattern_t)pattern_id;

  if ((uint32_t)pattern >= (uint32_t)WS2812_PATTERN_COUNT) {
    pattern = WS2812_PATTERN_OFF;
  }

  g_ws2812_test_pattern = pattern;
  return (uint8_t)g_ws2812_test_pattern;
}

uint8_t dynamic_led_get_pattern(void)
{
  return (uint8_t)g_ws2812_test_pattern;
}

uint8_t optic_sensor_get_state(void)
{
  return optic_sensor_state_public;
}

uint8_t optic_sensor_set_hold_seconds(uint8_t seconds)
{
  uint16_t deciseconds = (uint16_t)seconds * 10u;

  (void)optic_sensor_set_hold_deciseconds(deciseconds);
  return optic_sensor_get_hold_seconds();
}

uint8_t optic_sensor_get_hold_seconds(void)
{
  uint16_t deciseconds = optic_sensor_get_hold_deciseconds();
  return (uint8_t)((deciseconds + 9u) / 10u);
}

uint16_t optic_sensor_set_hold_deciseconds(uint16_t deciseconds)
{
  if (deciseconds == 0u) {
    deciseconds = OPTIC_ACTIVE_HOLD_DEFAULT_DS;
  }
  if (deciseconds > OPTIC_ACTIVE_HOLD_MAX_DS) {
    deciseconds = OPTIC_ACTIVE_HOLD_MAX_DS;
  }
  optic_active_hold_deciseconds = deciseconds;
  return optic_active_hold_deciseconds;
}

uint16_t optic_sensor_get_hold_deciseconds(void)
{
  return optic_active_hold_deciseconds;
}

static uint32_t optic_tim1_get_input_clk_hz(void)
{
  uint32_t tim_clk = HAL_RCC_GetPCLK2Freq();
  uint32_t d2ppre2 = (RCC->D2CFGR & RCC_D2CFGR_D2PPRE2_Msk) >> RCC_D2CFGR_D2PPRE2_Pos;

  if (d2ppre2 != 0u) {
    tim_clk *= 2u;
  }
  return tim_clk;
}

static void optic_sensor_service(uint32_t now_ms)
{
  uint32_t hold_ms = (uint32_t)optic_active_hold_deciseconds * 100u;

  if ((optic_sensor_state_public != 0u) &&
      ((now_ms - optic_last_change_ms) >= hold_ms)) {
    optic_sensor_state_public = 0u;
    need_usb_status_refresh = 1u;
  }
}

static void optic_tx_apply_runtime_pattern(void)
{
  uint32_t period_ticks;
  uint32_t max_compare;
  uint32_t compare;

  period_ticks = __HAL_TIM_GET_AUTORELOAD(&htim1) + 1u;
  if (period_ticks < 2u) {
    period_ticks = 2u;
  }

  max_compare = period_ticks / 2u;
  if (max_compare == 0u) {
    max_compare = 1u;
  }

  compare = ((uint32_t)optic_tx_power_level * max_compare) / OPTIC_TX_POWER_MAX;
  if ((optic_tx_power_level != 0u) && (compare == 0u)) {
    compare = 1u;
  }

  __HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_3, compare);
}

static uint8_t optic_tx_power_to_percent(uint8_t power_level)
{
  return (uint8_t)((((uint32_t)power_level * 100u) + (OPTIC_TX_POWER_MAX / 2u)) / OPTIC_TX_POWER_MAX);
}

static uint16_t optic_tx_power_badge_color(uint8_t power_level)
{
  uint8_t percent = optic_tx_power_to_percent(power_level);

  if (percent >= 88u) {
    return GREEN;
  }
  if (percent >= 63u) {
    return YELLOW;
  }
  if (percent >= 38u) {
    return CYAN;
  }
  return ORANGE;
}

uint8_t optic_tx_set_power(uint8_t power)
{
  optic_tx_power_level = power;
  optic_tx_apply_runtime_pattern();
  return (uint8_t)optic_tx_power_level;
}

uint8_t optic_tx_get_power(void)
{
  return (uint8_t)optic_tx_power_level;
}

static const char *ws2812_test_pattern_name(ws2812_pattern_t pattern)
{
  switch (pattern) {
    case WS2812_PATTERN_OFF:
      return "OFF";
    case WS2812_PATTERN_TEST_DRIP:
      return "DRIP";
    case WS2812_PATTERN_EVENT_B_UP:
      return "RED_UP";
    case WS2812_PATTERN_EVENT_A_DOWN:
      return "RED_DOWN";
    case WS2812_PATTERN_EVENT_BOTH_ALT:
      return "RED_DOWN_UP_ALT";
    case WS2812_PATTERN_EVENT_SPLIT_IN:
      return "RED_SPLIT_IN";
    case WS2812_PATTERN_EVENT_SPLIT_OUT:
      return "RED_SPLIT_OUT";
    case WS2812_PATTERN_TEST_SCOPE_RGB:
      return "RGB_SCOPE";
    case WS2812_PATTERN_TEST_BLUE:
      return "RED_BLUE_SPLIT";
    case WS2812_PATTERN_TEST_COLOR_CYCLE:
      return "COLOR_CYCLE";
    default:
      return "UNKNOWN";
  }
}

static void ws2812_test_button_service(uint32_t now_ms)
{
  static const ws2812_pattern_t test_patterns[] = {
    WS2812_PATTERN_OFF,
    WS2812_PATTERN_TEST_DRIP,
    WS2812_PATTERN_EVENT_B_UP,
    WS2812_PATTERN_EVENT_A_DOWN,
    WS2812_PATTERN_EVENT_BOTH_ALT,
    WS2812_PATTERN_EVENT_SPLIT_IN,
    WS2812_PATTERN_EVENT_SPLIT_OUT,
    WS2812_PATTERN_TEST_SCOPE_RGB,
    WS2812_PATTERN_TEST_BLUE,
    WS2812_PATTERN_TEST_COLOR_CYCLE
  };
  static uint8_t raw_state = 0u;
  static uint8_t stable_state = 0u;
  static uint32_t last_change_ms = 0u;
  const uint32_t pattern_count = sizeof(test_patterns) / sizeof(test_patterns[0]);
  uint8_t current_raw = (HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_13) == GPIO_PIN_SET) ? 1u : 0u;

  if (current_raw != raw_state) {
    raw_state = current_raw;
    last_change_ms = now_ms;
  }

  if ((stable_state != raw_state) &&
      ((uint32_t)(now_ms - last_change_ms) >= WS2812_TEST_BUTTON_DEBOUNCE_MS)) {
    stable_state = raw_state;

    if (stable_state != 0u) {
      uint32_t next_index = 0u;
      uint32_t index;

      for (index = 0u; index < pattern_count; ++index) {
        if (g_ws2812_test_pattern == test_patterns[index]) {
          next_index = (uint32_t)((index + 1u) % pattern_count);
          break;
        }
      }
      if (index >= pattern_count) {
        next_index = 0u;
      }

      g_ws2812_test_pattern = test_patterns[next_index];
      printf("[WS2812] Button pattern -> %s\r\n",
             ws2812_test_pattern_name(g_ws2812_test_pattern));
    }
  }
}

static void optic_tx_start(void)
{
  uint32_t period_ticks;
  uint32_t tim_clk;

  if (optic_tx_started != 0u) {
    return;
  }

  period_ticks = __HAL_TIM_GET_AUTORELOAD(&htim1) + 1u;
  if (period_ticks < 2u) {
    period_ticks = 2u;
  }

  /* Шаг 1: Запустить TIM3 (gate-генератор) от внутреннего такта.
     После программного UEV в init: CCR1_shadow=40, cnt=0.
     OC1REF=(0<40)=HIGH уже до первого такта → TIM1 gate открыт сразу. */
  if (HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_1) != HAL_OK) {
    printf("[OPTIC][ERR] TIM3 gate start failed\r\n");
    Error_Handler();
  }

  /* Шаг 2: Разрешить выходы TIM1 (MOE) и настроить скважность CH3 */
  __HAL_TIM_MOE_ENABLE(&htim1);
  optic_tx_apply_runtime_pattern();

  /* Шаг 3: Запустить TIM1 CH3 PWM. TIM1 работает пока TIM3 OC1REF=HIGH (GATED).
     40 периодов ON → 8 пауз → повтор. Полностью аппаратно, CPU=0. */
  if (HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_3) != HAL_OK) {
    printf("[OPTIC][ERR] TIM1 CH3 start failed\r\n");
    Error_Handler();
  }

  tim_clk = optic_tim1_get_input_clk_hz();
  optic_tx_started = 1u;
    printf("[OPTIC][GATE] TIM1_SMCR=0x%08lX TIM3_CR1=0x%08lX TIM3_ARR=%lu TIM3_CCR1=%lu\r\n",
      (unsigned long)TIM1->SMCR,
      (unsigned long)TIM3->CR1,
      (unsigned long)TIM3->ARR,
      (unsigned long)TIM3->CCR1);
  printf("[OPTIC] HW gate mode: carrier=%lu Hz, burst=%u on / %u off, power=%u/255\r\n",
         (unsigned long)((period_ticks != 0u) ? (tim_clk / period_ticks) : 0u),
         (unsigned)OPTIC_TX_BURST_ON_CYCLES,
         (unsigned)OPTIC_TX_BURST_OFF_CYCLES,
         (unsigned int)optic_tx_power_level);
}

static void rs485_sync_on_packet_received(uint8_t edge_kind)
{
  extern volatile uint32_t sync_buffer_count_at_edge;
  extern volatile uint32_t sync_buffers_between_edges;
  extern volatile uint32_t adc_stream_total_buffer_count;
  extern volatile uint32_t sync_tim15_cnt_at_pd5;
  extern volatile uint8_t vnd_sync_mode_public;

  uint32_t prev_count = sync_buffer_count_at_edge;
  sync_buffer_count_at_edge = adc_stream_total_buffer_count;
  sync_buffers_between_edges = adc_stream_total_buffer_count - prev_count;

  sync_tim5_period_ticks = htim5.Instance->CNT;
  htim5.Instance->CNT = 0u;

  sync_tim15_cnt_at_pd5 = htim15.Instance->CNT;
  rs485_last_sync_edge_kind = (uint8_t)(edge_kind & 1u);
  if (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) {
    uint8_t local_edge_kind = rs485_sync_edge_kind_from_marker_level(rs485_sync_read_local_marker_phase());

    if (local_edge_kind == rs485_last_sync_edge_kind) {
      if (rs485_sync_relation_score < 8) {
        rs485_sync_relation_score++;
      }
    } else {
      if (rs485_sync_relation_score > -8) {
        rs485_sync_relation_score--;
      }
    }

    /* Сравниваем именно edge-kind, а не raw-уровень GPIO.
       Если local_edge_kind совпадает с bit из sync-пакета, то slave идёт
       в той же фазе, что и master. Если не совпадает — это противофаза. */
    if (rs485_sync_relation_score >= 3) {
      rs485_sync_phase_relation = RS485_SYNC_RELATION_IN_PHASE;
    } else if (rs485_sync_relation_score <= -3) {
      rs485_sync_phase_relation = RS485_SYNC_RELATION_ANTI_PHASE;
    } else {
      rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
    }

    if (rs485_sync_phase_relation == RS485_SYNC_RELATION_ANTI_PHASE) {
      rs485_anti_phase_recovery_active = 1u;
      if (rs485_anti_phase_recovery_packets < 255u) {
        rs485_anti_phase_recovery_packets++;
      }
      if (rs485_anti_phase_recovery_packets >= 4u) {
        rs485_anti_phase_recovery_request = 1u;
      }
    } else {
      rs485_anti_phase_recovery_active = 0u;
      rs485_anti_phase_recovery_packets = 0u;
      rs485_anti_phase_recovery_request = 0u;
    }
  } else {
    rs485_sync_relation_score = 0;
    rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
    rs485_anti_phase_recovery_active = 0u;
    rs485_anti_phase_recovery_packets = 0u;
    rs485_anti_phase_recovery_request = 0u;
  }

  sync_last_edge_ms = HAL_GetTick();
  sync_edge_seen = 1u;
  sync_edge_count++;
  if (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) {
    uint32_t active_samples = adc_stream_get_active_samples();
    uint32_t dma_remaining = active_samples;
    uint32_t sample_idx = 0u;
    uint32_t buf_slot = (uint32_t)(s_next_ring_index & (FIFO_FRAMES - 1u));

    if ((active_samples != 0u) && (hdma_adc1.Instance != NULL)) {
      dma_remaining = __HAL_DMA_GET_COUNTER(&hdma_adc1);
      if (dma_remaining > active_samples) {
        dma_remaining = active_samples;
      }
      sample_idx = active_samples - dma_remaining;
      if (sample_idx >= active_samples) {
        sample_idx = active_samples - 1u;
      }
    }

    sync_phase_diag_role = 'S';
    sync_phase_diag_sample = (uint16_t)sample_idx;
    sync_phase_diag_samples = (uint16_t)active_samples;
    sync_phase_diag_ticks = sync_tim15_cnt_at_pd5;
    sync_phase_diag_arr = dma_remaining;
    sync_phase_diag_bufs = buf_slot;
    sync_phase_diag_edge = sync_edge_count;
    sync_phase_diag_kind = rs485_last_sync_edge_kind;
    sync_phase_diag_pending = 1u;
    sync_phase_handle_irq_fast((uint16_t)sample_idx, (uint16_t)active_samples);
  }

  rs485_last_rx_ms = sync_last_edge_ms;
  rs485_rx_packets++;
  rs485_status_begin_window();
  vnd_sync_on_edge();
}

static uint32_t rs485_compute_uid_mix(void)
{
  uint32_t uid0 = rs485_local_uid_words[0];
  uint32_t uid1 = rs485_local_uid_words[1];
  uint32_t uid2 = rs485_local_uid_words[2];

  return uid0 ^ uid1 ^ uid2 ^ (uid0 >> 16) ^ (uid1 >> 11) ^ (uid2 >> 7);
}

static void rs485_load_local_uid(void)
{
  uint32_t uid0 = *(const uint32_t *)(UID_BASE + 0x00u);
  uint32_t uid1 = *(const uint32_t *)(UID_BASE + 0x04u);
  uint32_t uid2 = *(const uint32_t *)(UID_BASE + 0x08u);

  rs485_local_uid_words[0] = uid0;
  rs485_local_uid_words[1] = uid1;
  rs485_local_uid_words[2] = uid2;
  memcpy(&rs485_local_uid_bytes[0], &uid0, sizeof(uid0));
  memcpy(&rs485_local_uid_bytes[4], &uid1, sizeof(uid1));
  memcpy(&rs485_local_uid_bytes[8], &uid2, sizeof(uid2));
}

static uint8_t rs485_compute_local_node_id(void)
{
  uint32_t mix = rs485_compute_uid_mix();
  return (uint8_t)((mix % RS485_DISCOVERY_MAX_ID) + 1u);
}

static uint32_t rs485_compute_master_claim_delay_ms(void)
{
  uint32_t mix = rs485_compute_uid_mix();

  return 250u + (mix % 1024u);
}

uint32_t rs485_get_master_claim_delay_ms(void)
{
  return rs485_master_claim_delay_ms;
}

static uint8_t rs485_count_bits_u32(uint32_t value)
{
  uint8_t count = 0u;
  while (value != 0u) {
    count = (uint8_t)(count + (uint8_t)(value & 1u));
    value >>= 1;
  }
  return count;
}

static uint32_t rs485_status_get_effective_period_ticks(void)
{
  uint32_t period_ticks = sync_tim5_period_ticks;

  if (period_ticks < 1000u) {
    uint16_t buf_rate = adc_stream_get_buf_rate();
    uint32_t tim_clk = rs485_sync_get_tim5_tick_hz();

    if ((buf_rate != 0u) && (tim_clk != 0u)) {
      period_ticks = (uint32_t)(((uint64_t)tim_clk + ((uint64_t)buf_rate / 2u)) / (uint64_t)buf_rate);
    }
  }

  return period_ticks;
}

static uint32_t rs485_status_get_window_ticks(uint32_t period_ticks)
{
  uint32_t window_ticks = 0u;
  uint32_t byte_ticks = rs485_sync_get_uart_packet_ticks();

  if (period_ticks == 0u) {
    return 0u;
  }

  window_ticks = period_ticks / RS485_STATUS_WINDOW_DIV;
  if (window_ticks <= byte_ticks) {
    return 0u;
  }

  return window_ticks - byte_ticks;
}

static void rs485_status_wait_byte_times(uint32_t byte_count)
{
  uint32_t baud = huart2.Init.BaudRate;
  uint32_t hclk = HAL_RCC_GetHCLKFreq();
  uint32_t start_cycles = 0u;
  uint32_t wait_cycles = 0u;

  if ((byte_count == 0u) || (baud == 0u) || (hclk == 0u)) {
    return;
  }

  if ((CoreDebug->DEMCR & CoreDebug_DEMCR_TRCENA_Msk) == 0u) {
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  }
  if ((DWT->CTRL & DWT_CTRL_CYCCNTENA_Msk) == 0u) {
    DWT->LAR = 0xC5ACCE55u;
    DWT->CYCCNT = 0u;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  }

  wait_cycles = (uint32_t)(((uint64_t)hclk * 10u * (uint64_t)byte_count + (uint64_t)baud - 1u) / (uint64_t)baud);
  start_cycles = DWT->CYCCNT;
  while ((uint32_t)(DWT->CYCCNT - start_cycles) < wait_cycles) {
  }
}

static uint8_t rs485_status_count_recent_peers(uint32_t now_ms, uint32_t hold_ms)
{
  uint8_t count = 0u;
  uint32_t index = 0u;

  for (index = 0u; index < RS485_DISCOVERY_MAX_ID; index++) {
    uint32_t peer_ms = rs485_status_peer_last_ms[index];
    if ((peer_ms != 0u) && ((now_ms - peer_ms) <= hold_ms)) {
      count++;
    }
  }

  return count;
}

static uint8_t rs485_status_build_local_byte(void)
{
  uint8_t status = (uint8_t)(rs485_local_node_id & RS485_STATUS_ID_MASK);

  if (optic_sensor_get_state() != 0u) {
    status |= RS485_STATUS_OPTIC_BIT;
  }
  if (vnd_is_tx_enabled() != 0u) {
    status |= RS485_STATUS_TX_ENABLE_BIT;
  }
  /* Состояние детектора метки пока временно не приходит от хоста. */
  return status;
}

uint8_t rs485_status_get_snapshot(uint8_t *local_status,
                                  uint8_t *node_count,
                                  uint32_t *seen_mask,
                                  uint8_t *status_bytes,
                                  uint8_t max_status_bytes)
{
  uint32_t now_ms = HAL_GetTick();
  uint32_t mask = 0u;
  uint8_t count = 0u;
  uint8_t local = rs485_status_build_local_byte();
  uint8_t local_id = (uint8_t)(local & RS485_STATUS_ID_MASK);
  uint8_t limit = max_status_bytes;

  if (limit > RS485_DISCOVERY_MAX_ID) {
    limit = RS485_DISCOVERY_MAX_ID;
  }

  if (status_bytes != NULL) {
    memset(status_bytes, 0, (size_t)max_status_bytes);
  }

  for (uint8_t index = 0u; index < limit; index++) {
    uint32_t peer_ms = rs485_status_peer_last_ms[index];
    if ((peer_ms != 0u) && ((now_ms - peer_ms) <= RS485_STATUS_PEER_HOLD_MS)) {
      uint8_t value = rs485_status_peer_bytes[index];
      if (status_bytes != NULL) {
        status_bytes[index] = value;
      }
      mask |= (1u << index);
      count++;
    }
  }

  if ((local_id != 0u) && (local_id <= limit)) {
    uint8_t idx = (uint8_t)(local_id - 1u);
    if ((mask & (1u << idx)) == 0u) {
      count++;
    }
    if (status_bytes != NULL) {
      status_bytes[idx] = local;
    }
    mask |= (1u << idx);
  }

  if (local_status != NULL) {
    *local_status = local;
  }
  if (node_count != NULL) {
    *node_count = count;
  }
  if (seen_mask != NULL) {
    *seen_mask = mask;
  }

  return count;
}

static uint8_t rs485_is_sync_byte(uint8_t value)
{
  return (uint8_t)(((value & (uint8_t)~RS485_SYNC_EDGE_BIT) == RS485_SYNC7_BASE) ? 1u : 0u);
}

static void rs485_process_received_byte(uint8_t value)
{
  if (rs485_is_sync_byte(value)) {
    if (rs485_status_window_active != 0u) {
      rs485_status_finalize_window();
    }
    rs485_sync_on_packet_received((value & RS485_SYNC_EDGE_BIT) ? 1u : 0u);
    rs485_discovery_on_sync_received();
  } else if (rs485_status_window_active != 0u) {
    rs485_status_on_received(value);
  } else if (rs485_uid_rx_active != 0u) {
    if (rs485_uid_rx_index < RS485_UID_FRAME_TAIL) {
      rs485_uid_rx_buf[rs485_uid_rx_index++] = value;
    } else {
      rs485_uid_rx_reset();
    }

    if ((rs485_uid_rx_active != 0u) && (rs485_uid_rx_index >= RS485_UID_FRAME_TAIL)) {
      if (rs485_uid_rx_buf[RS485_UID_FRAME_BYTES] == rs485_uid_checksum(rs485_uid_rx_buf)) {
        rs485_uid_handle_received(rs485_uid_rx_buf);
      }
      rs485_uid_rx_reset();
    }
  } else if (value == RS485_UID_FRAME_MAGIC) {
    rs485_uid_rx_active = 1u;
    rs485_uid_rx_index = 0u;
  } else if ((value & (uint8_t)~RS485_DISCOVERY_ID_MASK) == RS485_DISCOVERY_REQ_BASE) {
    rs485_discovery_on_request(value);
  } else if ((value & (uint8_t)~RS485_DISCOVERY_ID_MASK) == RS485_DISCOVERY_ACK_BASE) {
    rs485_discovery_on_response(value);
  }
}

static void rs485_dma_rx_start(void)
{
#if RS485_USART2_USE_DMA_RX
  rs485_dma_rx_rd = 0u;
  memset(rs485_dma_rx_buf, 0, sizeof(rs485_dma_rx_buf));
  (void)HAL_UART_DMAStop(&huart2);
  if (HAL_UART_Receive_DMA(&huart2, rs485_dma_rx_buf, RS485_DMA_RX_BUFFER_SIZE) != HAL_OK) {
    Error_Handler();
  }
  if (huart2.hdmarx != NULL) {
    __HAL_DMA_DISABLE_IT(huart2.hdmarx, DMA_IT_HT);
    __HAL_DMA_DISABLE_IT(huart2.hdmarx, DMA_IT_TC);
  }
#else
  huart2.ErrorCode = HAL_UART_ERROR_NONE;
  huart2.RxState = HAL_UART_STATE_READY;
  huart2.ReceptionType = HAL_UART_RECEPTION_STANDARD;
  huart2.RxISR = NULL;

  huart2.Instance->ICR = USART_ICR_PECF |
                         USART_ICR_FECF |
                         USART_ICR_NECF |
                         USART_ICR_ORECF;
  while ((huart2.Instance->ISR & USART_ISR_RXNE_RXFNE) != 0u) {
    (void)huart2.Instance->RDR;
  }
  SET_BIT(huart2.Instance->CR1, USART_CR1_RXNEIE_RXFNEIE);
  SET_BIT(huart2.Instance->CR3, USART_CR3_EIE);
#endif
}

static void rs485_dma_rx_service(void)
{
#if RS485_USART2_USE_DMA_RX
  uint16_t write_pos = 0u;
  uint16_t pending = 0u;

  if (huart2.hdmarx == NULL) {
    return;
  }

  write_pos = (uint16_t)(RS485_DMA_RX_BUFFER_SIZE - __HAL_DMA_GET_COUNTER(huart2.hdmarx));
  if (write_pos >= RS485_DMA_RX_BUFFER_SIZE) {
    write_pos = 0u;
  }

  pending = (write_pos >= rs485_dma_rx_rd)
      ? (uint16_t)(write_pos - rs485_dma_rx_rd)
      : (uint16_t)((RS485_DMA_RX_BUFFER_SIZE - rs485_dma_rx_rd) + write_pos);
  if (pending > (RS485_DMA_RX_BUFFER_SIZE - 8u)) {
    rs485_dma_rx_overrun_count++;
  }

  while (rs485_dma_rx_rd != write_pos) {
    uint8_t value = rs485_dma_rx_buf[rs485_dma_rx_rd];
    rs485_dma_rx_rd = (uint16_t)((rs485_dma_rx_rd + 1u) & (RS485_DMA_RX_BUFFER_SIZE - 1u));
    rs485_process_received_byte(value);
  }
#endif
}

void rs485_usart2_irq_rx_service(void)
{
  uint32_t error_flags = huart2.Instance->ISR & (USART_ISR_PE | USART_ISR_FE | USART_ISR_NE | USART_ISR_ORE);

  while ((huart2.Instance->ISR & USART_ISR_RXNE_RXFNE) != 0u) {
    uint8_t value = (uint8_t)(huart2.Instance->RDR & 0xFFu);
    rs485_process_received_byte(value);
  }

  if (error_flags != 0u) {
    rs485_uart_error_count++;
    huart2.Instance->ICR = USART_ICR_PECF |
                           USART_ICR_FECF |
                           USART_ICR_NECF |
                           USART_ICR_ORECF;
  }
}

static void rs485_status_reset_window_state(void)
{
  rs485_status_window_ticks = 0u;
  rs485_status_seen_mask = 0u;
  rs485_status_current_window_phase = 0xFFu;
  rs485_status_master_expected_phase = 0xFFu;
  rs485_status_window_active = 0u;
  rs485_status_window_after_tx = 0u;
  rs485_status_tx_due_ticks = 0u;
  rs485_status_tx_pending = 0u;
  rs485_status_tx_sent = 0u;
  rs485_status_slot_local_tx = 0u;
  rs485_status_cycle_count = 0u;
  rs485_status_slot_response_seen = 0u;
  rs485_status_slot_response_byte = 0u;
  rs485_status_local_slot_expected = 0u;
}

static void rs485_status_reset_slot_state(void)
{
  rs485_status_window_ticks = 0u;
  rs485_status_window_active = 0u;
  rs485_status_current_window_phase = 0xFFu;
  rs485_status_tx_due_ticks = 0u;
  rs485_status_tx_pending = 0u;
  rs485_status_tx_sent = 0u;
  rs485_status_slot_local_tx = 0u;
  rs485_status_slot_response_seen = 0u;
  rs485_status_slot_response_byte = 0u;
  rs485_status_local_slot_expected = 0u;
}

static void rs485_status_finalize_window(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t next_node_id = rs485_local_node_id;
  uint8_t slot_had_response = (uint8_t)((rs485_status_slot_response_seen != 0u) || (rs485_status_slot_local_tx != 0u));
  uint8_t response_byte = rs485_status_slot_response_byte;
  uint8_t recent_count = rs485_status_count_recent_peers(HAL_GetTick(), RS485_STATUS_PEER_HOLD_MS);
  uint8_t response_id = (uint8_t)(response_byte & RS485_STATUS_ID_MASK);
  uint8_t active_count = rs485_status_cycle_count;

  if ((rs485_status_window_active == 0u) &&
      (slot_had_response == 0u)) {
    return;
  }

  if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
    if ((rs485_status_master_expected_phase < RS485_STATUS_SLOT_STRIDE) &&
        (rs485_status_current_window_phase == rs485_status_master_expected_phase)) {
      rs485_status_window_total_count++;
      if (slot_had_response != 0u) {
        rs485_status_window_ok_count++;
      } else if ((recent_count != 0u) || (rs485_slave_count_estimate != 0u)) {
        rs485_status_window_miss_count++;
      }
    }
  } else if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
             (rs485_status_local_slot_expected != 0u) &&
             (rs485_status_slot_local_tx == 0u)) {
    rs485_status_local_slot_miss_count++;
  }

  if (slot_had_response != 0u) {
    if (rs485_status_cycle_count < RS485_DISCOVERY_MAX_ID) {
      rs485_status_cycle_count++;
      active_count = rs485_status_cycle_count;
    }

    if ((response_id != 0u) &&
        (response_id <= RS485_DISCOVERY_MAX_ID)) {
      rs485_status_seen_mask |= (1u << (response_id - 1u));
      rs485_status_master_expected_phase = (uint8_t)((response_id - 1u) % RS485_STATUS_SLOT_STRIDE);
      rs485_status_peer_bytes[response_id - 1u] = response_byte;
      rs485_status_peer_last_ms[response_id - 1u] = HAL_GetTick();
      if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
        rs485_slave_count_estimate = rs485_status_count_recent_peers(HAL_GetTick(), RS485_STATUS_PEER_HOLD_MS);
      }
    } else if ((rs485_local_node_id != 0u) &&
               (rs485_local_node_id <= RS485_DISCOVERY_MAX_ID)) {
      rs485_status_seen_mask |= (1u << (rs485_local_node_id - 1u));
    }

    rs485_discovery_seen_mask = rs485_status_seen_mask;
    rs485_discovery_scan_mask = rs485_status_seen_mask;
  } else {
    rs485_discovery_seen_mask = rs485_status_seen_mask;
    rs485_discovery_scan_mask = rs485_status_seen_mask;

    if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
      rs485_slave_count_estimate = recent_count;
    } else if (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) {
      if (next_node_id == 0u) {
        if (active_count < RS485_DISCOVERY_MAX_ID) {
          next_node_id = (uint8_t)(active_count + 1u);
        }
      } else if (next_node_id > (uint8_t)(active_count + 1u)) {
        next_node_id = (uint8_t)(active_count + 1u);
      }

      rs485_local_node_id = next_node_id;
      if (next_node_id != 0u) {
        rs485_slave_count_estimate = active_count;
      } else {
        rs485_slave_count_estimate = active_count;
      }
    } else {
      rs485_slave_count_estimate = 0u;
    }

    rs485_status_seen_mask = 0u;
    rs485_status_tx_sent = 0u;
    rs485_status_cycle_count = 0u;
  }

  rs485_status_reset_slot_state();
}

static void rs485_status_begin_window(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t local_slot_phase = 0u;
  uint8_t current_slot_phase = 0u;
  uint32_t byte_ticks = 0u;

  if (rs485_status_window_active != 0u) {
    rs485_status_finalize_window();
  }

  rs485_status_reset_slot_state();
  rs485_status_window_active = 1u;
  if (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) {
    rs485_status_current_window_phase = rs485_last_sync_edge_kind;
  }

  if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
      (rs485_local_node_id != 0u)) {
    local_slot_phase = (uint8_t)((rs485_local_node_id - 1u) % RS485_STATUS_SLOT_STRIDE);
    /* Таймслот нужно выбирать по биту текущего sync-пакета,
       а не по локальному счётчику принятых пакетов: один пропуск RX иначе
       навсегда сдвигает slave на чужой полупериод. */
    current_slot_phase = (uint8_t)(rs485_last_sync_edge_kind % RS485_STATUS_SLOT_STRIDE);
    if (local_slot_phase == current_slot_phase) {
      rs485_status_local_slot_expected = 1u;
      rs485_status_local_slot_expected_count++;
      byte_ticks = rs485_sync_get_uart_packet_ticks();
      rs485_status_window_ticks = byte_ticks * RS485_STATUS_RESPONSE_DELAY_BYTES;

      if (rs485_tx_busy == 0u) {
        uint32_t primask = __get_PRIMASK();
        __disable_irq();
        if ((rs485_tx_busy == 0u) && (RS485_STATUS_RESPONSE_DELAY_BYTES != 0u)) {
          rs485_status_wait_byte_times(RS485_STATUS_RESPONSE_DELAY_BYTES);
        }
        if (rs485_tx_busy == 0u) {
          rs485_status_tx_sent = 1u;
          rs485_status_slot_local_tx = 1u;
          rs485_status_local_tx_count++;
          rs485_sync_start_tx_byte(rs485_status_build_local_byte());
        } else {
          rs485_status_tx_due_ticks = rs485_status_window_ticks;
          rs485_status_tx_pending = 1u;
          rs485_status_deferred_tx_count++;
        }
        if (primask == 0u) {
          __enable_irq();
        }
      } else {
        rs485_status_tx_due_ticks = rs485_status_window_ticks;
        rs485_status_tx_pending = 1u;
        rs485_status_deferred_tx_count++;
      }
    }
  }
}

static void rs485_status_on_received(uint8_t value)
{
  uint8_t node_id = (uint8_t)(value & RS485_STATUS_ID_MASK);

  if ((rs485_status_window_active == 0u) ||
      (node_id == 0u) ||
      (node_id > RS485_DISCOVERY_MAX_ID)) {
    return;
  }

  if (rs485_status_slot_response_seen == 0u) {
    rs485_status_slot_response_seen = 1u;
    rs485_status_slot_response_byte = value;
    rs485_status_peer_rx_count++;
  }
}

static void rs485_status_service(void)
{
  uint32_t now_ticks = 0u;

  if (rs485_status_window_active == 0u) {
    return;
  }
  if (rs485_status_tx_pending == 0u) {
    return;
  }
  if (rs485_status_tx_sent != 0u) {
    return;
  }
  if (rs485_tx_busy != 0u) {
    return;
  }

  now_ticks = htim5.Instance->CNT;
  if ((rs485_status_tx_due_ticks != 0u) && (now_ticks < rs485_status_tx_due_ticks)) {
    return;
  }

  rs485_status_tx_pending = 0u;
  rs485_status_tx_sent = 1u;
  rs485_status_slot_local_tx = 1u;
  rs485_status_local_tx_count++;
  rs485_sync_start_tx_byte(rs485_status_build_local_byte());
}

static void rs485_discovery_reset_master_scan(void)
{
  rs485_discovery_next_id = 1u;
  rs485_discovery_wait_id = 0u;
  rs485_discovery_response_seen = 0u;
  rs485_discovery_window_open = 0u;
  rs485_discovery_phase_wait_response = 0u;
  rs485_discovery_scan_mask = 0u;
}

static void rs485_uid_rx_reset(void)
{
  rs485_uid_rx_active = 0u;
  rs485_uid_rx_index = 0u;
}

static uint8_t rs485_uid_checksum(const uint8_t *uid_bytes)
{
  uint8_t checksum = RS485_UID_FRAME_MAGIC;
  uint32_t index = 0u;

  for (index = 0u; index < RS485_UID_FRAME_BYTES; index++) {
    checksum ^= uid_bytes[index];
  }
  return checksum;
}

static int8_t rs485_compare_uid_words(const uint32_t *lhs, const uint32_t *rhs)
{
  int index;

  for (index = 2; index >= 0; index--) {
    if (lhs[index] < rhs[index]) {
      return -1;
    }
    if (lhs[index] > rhs[index]) {
      return 1;
    }
  }
  return 0;
}

static void rs485_uid_schedule_announce(uint32_t now_ms, uint8_t retries, uint32_t delay_ms)
{
  uint32_t target_ms;

  if (retries == 0u) {
    return;
  }

  target_ms = now_ms + delay_ms;
  if ((!rs485_uid_tx_pending) ||
      ((int32_t)(target_ms - rs485_uid_tx_next_ms) < 0) ||
      (retries > rs485_uid_tx_retries)) {
    rs485_uid_tx_pending = 1u;
    rs485_uid_tx_retries = retries;
    rs485_uid_tx_next_ms = target_ms;
  }
}

static void rs485_uid_service(uint32_t now_ms)
{
  uint8_t checksum = 0u;
  uint32_t index = 0u;

  if ((!rs485_uid_tx_pending) || ((int32_t)(now_ms - rs485_uid_tx_next_ms) < 0)) {
    return;
  }

  rs485_tx_queue_push(RS485_UID_FRAME_MAGIC);
  for (index = 0u; index < RS485_UID_FRAME_BYTES; index++) {
    rs485_tx_queue_push(rs485_local_uid_bytes[index]);
  }
  checksum = rs485_uid_checksum(rs485_local_uid_bytes);
  rs485_tx_queue_push(checksum);
  rs485_uid_last_tx_ms = now_ms;

  if (rs485_uid_tx_retries > 1u) {
    rs485_uid_tx_retries--;
    rs485_uid_tx_next_ms = now_ms + RS485_UID_RETRY_MS;
  } else {
    rs485_uid_tx_retries = 0u;
    rs485_uid_tx_pending = 0u;
  }
}

static void rs485_uid_handle_received(const uint8_t *uid_bytes)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t peer_words[3] = {0u, 0u, 0u};
  int8_t cmp = 0;

  memcpy(&peer_words[0], &uid_bytes[0], sizeof(uint32_t));
  memcpy(&peer_words[1], &uid_bytes[4], sizeof(uint32_t));
  memcpy(&peer_words[2], &uid_bytes[8], sizeof(uint32_t));

  cmp = rs485_compare_uid_words(peer_words, rs485_local_uid_words);
  if (cmp == 0) {
    return;
  }

  memcpy(rs485_peer_uid_bytes, uid_bytes, RS485_UID_FRAME_BYTES);
  memcpy(rs485_peer_uid_words, peer_words, sizeof(peer_words));
  rs485_peer_uid_cmp = cmp;
  rs485_peer_uid_valid = 1u;
  rs485_peer_uid_last_ms = HAL_GetTick();
  /* PEER_UID debug-лог отключён для чистого phase-monitor потока. */

  if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
    rs485_slave_count_estimate = 1u;
  } else if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) && (rs485_local_node_id == 0u)) {
    rs485_local_node_id = 1u;
  }
}

static uint32_t rs485_uid_get_announce_delay_ms(void)
{
  uint32_t slot = rs485_compute_uid_mix() & (RS485_UID_SLOT_COUNT - 1u);
  return RS485_UID_ANNOUNCE_DELAY_MS + (slot * RS485_UID_SLOT_STEP_MS);
}

static void rs485_uid_begin_arbitration_window(uint32_t now_ms)
{
  rs485_sync_tx_suppressed_until_ms = now_ms + RS485_UID_ARBITRATION_QUIET_MS;
}

static void rs485_tx_queue_push(uint8_t value)
{
  if (rs485_tx_queue_count >= RS485_TX_QUEUE_CAPACITY) {
    return;
  }

  rs485_tx_queue[rs485_tx_queue_tail] = value;
  rs485_tx_queue_tail = (uint8_t)((rs485_tx_queue_tail + 1u) % RS485_TX_QUEUE_CAPACITY);
  rs485_tx_queue_count++;
}

/* Вставка в голову очереди — байт уйдёт первым (высший приоритет, для sync-байта) */
static void rs485_tx_queue_push_front(uint8_t value)
{
  if (rs485_tx_queue_count >= RS485_TX_QUEUE_CAPACITY) {
    return;
  }

  rs485_tx_queue_head = (uint8_t)((rs485_tx_queue_head + RS485_TX_QUEUE_CAPACITY - 1u) % RS485_TX_QUEUE_CAPACITY);
  rs485_tx_queue[rs485_tx_queue_head] = value;
  rs485_tx_queue_count++;
}

static uint8_t rs485_tx_queue_pop(uint8_t *value)
{
  if ((value == NULL) || (rs485_tx_queue_count == 0u)) {
    return 0u;
  }

  *value = rs485_tx_queue[rs485_tx_queue_head];
  rs485_tx_queue_head = (uint8_t)((rs485_tx_queue_head + 1u) % RS485_TX_QUEUE_CAPACITY);
  rs485_tx_queue_count--;
  return 1u;
}

static void rs485_sync_start_tx_byte(uint8_t value)
{
  if (rs485_tx_busy) {
    return;
  }

  rs485_tx_byte = value;
  rs485_tx_busy = 1u;
  rs485_tx_start_ms = HAL_GetTick();
  HAL_GPIO_WritePin(RS485_RDE_GPIO_Port, RS485_RDE_Pin, GPIO_PIN_SET);
  huart2.Instance->ICR = USART_ICR_TCCF;
  SET_BIT(huart2.Instance->CR1, USART_CR1_TCIE);
  huart2.Instance->TDR = rs485_tx_byte;
}

static void rs485_tx_kick(void)
{
  uint8_t value = 0u;

  if (rs485_tx_busy) {
    return;
  }
  if (!rs485_tx_queue_pop(&value)) {
    return;
  }

  rs485_sync_start_tx_byte(value);
}

static void rs485_discovery_on_request(uint8_t value)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t request_id = (uint8_t)(value & RS485_DISCOVERY_ID_MASK);

  if (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) {
    return;
  }
  if (request_id == 0u) {
    return;
  }

  if ((rs485_local_node_id == 0u) && (request_id != 1u)) {
    return;
  }

  if ((rs485_local_node_id != 0u) && (request_id != rs485_local_node_id)) {
    return;
  }

  if (rs485_local_node_id == 0u) {
    rs485_local_node_id = request_id;
  }

  rs485_tx_queue_push((uint8_t)(RS485_DISCOVERY_ACK_BASE | rs485_local_node_id));
  rs485_tx_kick();
}

static void rs485_discovery_on_response(uint8_t value)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t response_id = (uint8_t)(value & RS485_DISCOVERY_ID_MASK);
  uint32_t updated_mask = 0u;

  if (vnd_sync_mode_public != VND_SYNC_MODE_MASTER) {
    return;
  }
  if (!rs485_discovery_phase_wait_response) {
    return;
  }
  if ((response_id == 0u) || (response_id != rs485_discovery_wait_id)) {
    return;
  }

  rs485_discovery_scan_mask |= (1u << (response_id - 1u));
  updated_mask = rs485_discovery_seen_mask | rs485_discovery_scan_mask;
  rs485_slave_count_estimate = rs485_count_bits_u32(updated_mask);
  rs485_discovery_response_seen = 1u;
}

static void rs485_discovery_on_sync_received(void)
{
  /* ACK теперь отправляется сразу при приёме discovery-запроса. */
}

static void rs485_sync_service_tx(void)
{
  rs485_dma_rx_service();

  if (rs485_tx_busy != 0u) {
    uint32_t now_ms = HAL_GetTick();
    uint32_t isr = huart2.Instance->ISR;

    if (((isr & USART_ISR_TC) != 0u) ||
        ((rs485_tx_start_ms != 0u) && ((now_ms - rs485_tx_start_ms) > 2u))) {
      huart2.Instance->ICR = USART_ICR_TCCF;
      CLEAR_BIT(huart2.Instance->CR1, USART_CR1_TCIE);
      HAL_GPIO_WritePin(RS485_RDE_GPIO_Port, RS485_RDE_Pin, GPIO_PIN_RESET);
      rs485_tx_busy = 0u;
    }
  }

  rs485_status_service();
  rs485_tx_kick();
}

static void rs485_sync_auto_role_service(uint32_t now_ms)
{
  extern void vnd_sync_set_mode_auto(uint8_t mode);
  extern uint8_t vnd_sync_is_mode_host_forced(void);
  extern volatile uint8_t vnd_sync_mode_public;
  static uint8_t auto_init_done = 0u;
  static uint32_t auto_start_ms = 0u;
  static uint8_t prev_mode = 0xFFu;

  if (vnd_sync_mode_public != prev_mode) {
    prev_mode = vnd_sync_mode_public;
    rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
    rs485_sync_relation_score = 0;
    rs485_anti_phase_recovery_active = 0u;
    rs485_anti_phase_recovery_packets = 0u;
    rs485_status_reset_window_state();
    rs485_slave_count_estimate = 0u;
    rs485_status_local_tx_count = 0u;
    rs485_status_local_tx_complete_count = 0u;
    rs485_status_peer_rx_count = 0u;
    rs485_status_deferred_tx_count = 0u;
    rs485_status_window_total_count = 0u;
    rs485_status_window_ok_count = 0u;
    rs485_status_window_miss_count = 0u;
    rs485_status_local_slot_expected_count = 0u;
    rs485_status_local_slot_miss_count = 0u;
    if (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) {
      rs485_local_node_id = 0u;
    } else if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
               (rs485_local_node_id == 0u)) {
      rs485_local_node_id = rs485_local_uid_hint;
    }
    tim15_request_hold_offset(0);
  }

  if (vnd_sync_is_mode_host_forced()) {
    return;
  }

  if (!auto_init_done) {
    auto_init_done = 1u;
    auto_start_ms = now_ms;
    vnd_sync_set_mode_auto(VND_SYNC_MODE_SLAVE);
    rs485_last_rx_ms = 0u;
    rs485_local_node_id = 0u;
    rs485_reply_pending_id = 0u;
    rs485_slave_count_estimate = 0u;
    rs485_discovery_seen_mask = 0u;
    rs485_discovery_reset_master_scan();
    /* AUTO_ROLE init-лог отключён для чистого phase-monitor потока. */
    return;
  }

  if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
    return;
  }

  if (rs485_last_rx_ms != 0u) {
    return;
  }

  if ((now_ms - auto_start_ms) < rs485_master_claim_delay_ms) {
    return;
  }

  vnd_sync_set_mode_auto(VND_SYNC_MODE_MASTER);
  rs485_local_node_id = rs485_local_uid_hint;
  rs485_reply_pending_id = 0u;
  rs485_slave_count_estimate = 0u;
  rs485_discovery_seen_mask = 0u;
  rs485_discovery_reset_master_scan();
  /* AUTO_ROLE claim-лог отключён для чистого phase-monitor потока. */
}

typedef struct {
  uint32_t arr;
  int32_t avg_delta;
  uint8_t valid;
} arr_auto_measure_t;

static volatile uint8_t g_arr_auto_ready = 0u;
static volatile uint32_t g_arr_auto_selected = 1144u;
static volatile uint8_t g_arr_auto_rescan_request = 0u;
static volatile uint8_t g_tune_led_freq_active = 0u;

#define TIM15_SYNC_LOCK_WINDOW_TICKS      40
#define TIM15_SYNC_PULSE_DEADBAND_TICKS   80
#define TIM15_SYNC_HOLD_DIVISOR           4096
#define TIM15_SYNC_PULSE_DIVISOR          96
#define TIM15_SYNC_PULSE_BOOST1_DIVISOR   96
#define TIM15_SYNC_PULSE_BOOST2_DIVISOR   48
#define TIM15_SYNC_PULSE_HOLD_UPDATES     16u
#define TIM15_SYNC_HOLD_MAX_OFFSET        6
#define TIM15_SYNC_PULSE_MIN_OFFSET       1
#define TIM15_SYNC_PULSE_MAX_OFFSET       64
#define TIM15_SYNC_PULSE_NEAR_ERROR       400
#define TIM15_SYNC_PULSE_MID_ERROR        1500
#define TIM15_SYNC_PULSE_FAR_ERROR        4000
#define TIM15_SYNC_FILTER_DIVISOR         4
#define TIM15_SYNC_DEADBAND_NUMERATOR     3u
#define TIM15_SYNC_DEADBAND_DENOMINATOR   4u
#define TIM15_SYNC_PULSE_MAX_BITS_NUM     1u
#define TIM15_SYNC_PULSE_MAX_BITS_DEN     2u
/* Базовая точка ARR для SLAVE берётся из текущего активного профиля TIM15,
 * а не из жёстко прошитого значения: профили 300/400 Hz имеют разный номинал.
 * 1 шаг = 1 тик ARR.
 */
static volatile uint32_t g_tim15_slave_base_arr = 0u;
static volatile int32_t g_tim15_slave_arr_step_ticks = 1;

static uint32_t tim15_sync_get_uart_bit_ticks_or_sample(uint32_t sample_ticks)
{
  uint32_t bit_ticks = rs485_sync_get_uart_bit_ticks();

  if (bit_ticks == 0u) {
    bit_ticks = sample_ticks;
  }
  if (bit_ticks == 0u) {
    bit_ticks = 1144u;
  }

  return bit_ticks;
}

static uint32_t tim15_sync_get_deadband_ticks(void)
{
  uint32_t period_ticks = sync_tim5_period_ticks;
  uint32_t active_samples = adc_stream_get_active_samples();
  uint32_t sample_ticks = 1144u;
  uint32_t bit_ticks = 0u;
  uint32_t deadband = 0u;

  if ((period_ticks != 0u) && (active_samples != 0u)) {
    sample_ticks = period_ticks / active_samples;
    if (sample_ticks == 0u) {
      sample_ticks = 1144u;
    }
  }

  /* Держим фазу внутри одного UART-бита: коррекция стартует уже на ~0.75 bit,
     чтобы видимый разброс не успевал уходить на 2-3 bit. */
  bit_ticks = tim15_sync_get_uart_bit_ticks_or_sample(sample_ticks);
  deadband = (bit_ticks * TIM15_SYNC_DEADBAND_NUMERATOR) / TIM15_SYNC_DEADBAND_DENOMINATOR;
  if (deadband == 0u) {
    deadband = 1u;
  }

  return deadband;
}

static int32_t tim15_sync_clamp_i32(int32_t value, int32_t limit)
{
  if (value > limit) {
    return limit;
  }
  if (value < -limit) {
    return -limit;
  }
  return value;
}

static void arr_auto_apply_tim15(uint32_t arr)
{
  extern TIM_HandleTypeDef htim15;

  if (arr < 1u) {
    arr = 1u;
  }

  htim15.Init.Period = arr;
  htim15.Instance->ARR = arr;
  __HAL_TIM_SET_COMPARE(&htim15, TIM_CHANNEL_1, (arr + 1u) / 2u);
}

static int32_t arr_auto_abs_i32(int32_t value)
{
  return (value < 0) ? -value : value;
}

static int32_t tim15_compute_phase_pulse_delta(int32_t phase_ticks)
{
  int32_t abs_phase = arr_auto_abs_i32(phase_ticks);
  int32_t pulse = 0;
  int32_t boost_mid_start = 0;
  int32_t boost_far_start = 0;
  uint32_t deadband_ticks = tim15_sync_get_deadband_ticks();
  uint32_t sample_ticks = 1144u;
  uint32_t period_ticks = sync_tim5_period_ticks;
  uint32_t active_samples = adc_stream_get_active_samples();
  uint32_t bit_ticks = 0u;
  uint32_t pulse_cap = TIM15_SYNC_PULSE_MAX_OFFSET;

  if ((uint32_t)abs_phase <= deadband_ticks) {
    return 0;
  }

  if ((period_ticks != 0u) && (active_samples != 0u)) {
    sample_ticks = period_ticks / active_samples;
    if (sample_ticks == 0u) {
      sample_ticks = 1144u;
    }
  }

  bit_ticks = tim15_sync_get_uart_bit_ticks_or_sample(sample_ticks);
  pulse_cap = (bit_ticks * TIM15_SYNC_PULSE_MAX_BITS_NUM) /
              (TIM15_SYNC_PULSE_HOLD_UPDATES * TIM15_SYNC_PULSE_MAX_BITS_DEN);
  if (pulse_cap < TIM15_SYNC_PULSE_MIN_OFFSET) {
    pulse_cap = TIM15_SYNC_PULSE_MIN_OFFSET;
  }
  if (pulse_cap > TIM15_SYNC_PULSE_MAX_OFFSET) {
    pulse_cap = TIM15_SYNC_PULSE_MAX_OFFSET;
  }

  pulse = abs_phase / TIM15_SYNC_PULSE_DIVISOR;

  /* Адаптивное усиление: чем дальше ушли от lock-зоны, тем агрессивнее
     одноразовый ARR-пульс. Это ускоряет вход в фазу без дёрганой доводки
     рядом с целевой точкой. */
  boost_mid_start = (int32_t)deadband_ticks + (int32_t)(sample_ticks * 2u);
  boost_far_start = (int32_t)deadband_ticks + (int32_t)(sample_ticks * 6u);
  if (abs_phase > boost_mid_start) {
    pulse += (abs_phase - boost_mid_start) / TIM15_SYNC_PULSE_BOOST1_DIVISOR;
  }
  if (abs_phase > boost_far_start) {
    pulse += (abs_phase - boost_far_start) / TIM15_SYNC_PULSE_BOOST2_DIVISOR;
  }

  if (pulse < TIM15_SYNC_PULSE_MIN_OFFSET) {
    pulse = TIM15_SYNC_PULSE_MIN_OFFSET;
  }
  if ((uint32_t)pulse > pulse_cap) {
    pulse = (int32_t)pulse_cap;
  }

  /* Отрицательный delta даёт укороченный период (ускорение),
     положительный — удлинённый (замедление).
     После исправления знака target phase для устойчивой отрицательной ОС
     положительная фазовая ошибка должна уменьшаться положительным delta. */
  return (phase_ticks > 0) ? pulse : -pulse;
}

static uint32_t tim15_phase_pulse_spacing_buffers(uint32_t abs_phase)
{
  uint32_t deadband_ticks = tim15_sync_get_deadband_ticks();
  uint32_t sample_ticks = 1144u;
  uint32_t period_ticks = sync_tim5_period_ticks;
  uint32_t active_samples = adc_stream_get_active_samples();
  uint32_t bit_ticks = 0u;

  if ((period_ticks != 0u) && (active_samples != 0u)) {
    sample_ticks = period_ticks / active_samples;
    if (sample_ticks == 0u) {
      sample_ticks = 1144u;
    }
  }

  bit_ticks = tim15_sync_get_uart_bit_ticks_or_sample(sample_ticks);
  if (abs_phase >= (deadband_ticks + (bit_ticks * 2u))) {
    return 1u;
  }
  if (abs_phase > deadband_ticks) {
    return 2u;
  }
  return 4u;
}

static void tim15_apply_hold_target_if_possible(void)
{
  tim15_arr_hold_target_offset = 0;
  tim15_arr_hold_offset = 0;
}

static void tim15_request_hold_offset(int32_t arr_delta)
{
  (void)arr_delta;
  tim15_arr_hold_target_offset = 0;
  tim15_arr_hold_offset = 0;
}

static uint8_t tim15_schedule_arr_pulse(int32_t arr_delta)
{
  extern TIM_HandleTypeDef htim15;
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t nominal_arr = TIM15->ARR;
  int32_t pulse_arr = 0;

  if (arr_delta == 0) {
    return 0u;
  }
  if (tim15_arr_pulse_stage != 0u) {
    return 0u;
  }

  /* Для SLAVE пульсуем относительно текущего номинального ARR,
     который уже выставлен активным профилем/частотой потока. */
  if (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) {
    if (nominal_arr == 0u) {
      nominal_arr = htim15.Init.Period;
    }
    if (nominal_arr == 0u) {
      nominal_arr = 1u;
    }
    g_tim15_slave_base_arr = nominal_arr;
  }

  pulse_arr = (int32_t)nominal_arr + (arr_delta * g_tim15_slave_arr_step_ticks);
  if (pulse_arr < 1) {
    pulse_arr = 1;
  }

  tim15_arr_pulse_nominal = nominal_arr;
  tim15_arr_pulse_stage = TIM15_SYNC_PULSE_HOLD_UPDATES;
  tim15_arr_pulse_count++;
  arr_auto_apply_tim15((uint32_t)pulse_arr);
  __HAL_TIM_CLEAR_FLAG(&htim15, TIM_FLAG_UPDATE);
  __HAL_TIM_ENABLE_IT(&htim15, TIM_IT_UPDATE);
  return 1u;
}

static void sync_phase_handle_irq_fast(uint16_t sample_idx, uint16_t active_samples)
{
  extern volatile uint8_t vnd_sync_mode_public;
  extern volatile uint32_t adc_stream_total_buffer_count;
  static int32_t filtered_phase_error = 0;
  static uint8_t filtered_phase_valid = 0u;
  static uint32_t filtered_period_ticks = 0u;
  uint32_t period_ticks = sync_tim5_period_ticks;
  uint32_t sample_ticks = 0u;
  int32_t target_phase = 0;
  int32_t measured_phase = 0;
  int32_t phase_error = 0;
  uint32_t abs_phase = 0u;
  uint32_t control_abs_phase = 0u;
  int32_t control_phase_error = 0;
  int32_t pulse_delta = 0;
  uint32_t pulse_spacing = 0u;
  uint32_t current_buf = adc_stream_total_buffer_count;
  uint32_t bit_ticks = 0u;

  if (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) {
    filtered_phase_valid = 0u;
    return;
  }

  if ((active_samples == 0u) || (period_ticks == 0u)) {
    filtered_phase_valid = 0u;
    rs485_sync_locked = 0u;
    rs485_sync_led_active = 0u;
    return;
  }

  sample_ticks = period_ticks / active_samples;
  if (sample_ticks == 0u) {
    sample_ticks = 1144u;
  }
  bit_ticks = tim15_sync_get_uart_bit_ticks_or_sample(sample_ticks);

  target_phase = (g_sync_target_phase_ticks == SYNC_TARGET_PHASE_AUTO)
                 ? tim15_get_default_target_phase_ticks()
                 : rs485_sync_wrap_phase_ticks((int32_t)g_sync_target_phase_ticks, period_ticks);
  measured_phase = (int32_t)((uint32_t)sample_idx * sample_ticks);
  phase_error = rs485_sync_wrap_phase_ticks(measured_phase - target_phase, period_ticks);
  abs_phase = (uint32_t)arr_auto_abs_i32(phase_error);

  if ((filtered_phase_valid == 0u) || (filtered_period_ticks != period_ticks)) {
    filtered_phase_error = phase_error;
    filtered_phase_valid = 1u;
    filtered_period_ticks = period_ticks;
  } else {
    int32_t innovation = rs485_sync_wrap_phase_ticks(phase_error - filtered_phase_error, period_ticks);
    int32_t step_limit = (int32_t)bit_ticks;
    int32_t filter_step = 0;

    if (step_limit < 1) {
      step_limit = 1;
    }
    innovation = tim15_sync_clamp_i32(innovation, step_limit);
    filter_step = innovation / TIM15_SYNC_FILTER_DIVISOR;
    if ((filter_step == 0) && (innovation != 0)) {
      filter_step = (innovation > 0) ? 1 : -1;
    }
    filtered_phase_error = rs485_sync_wrap_phase_ticks(filtered_phase_error + filter_step, period_ticks);
  }

  control_phase_error = filtered_phase_error;
  control_abs_phase = (uint32_t)arr_auto_abs_i32(control_phase_error);
  pulse_delta = tim15_compute_phase_pulse_delta(control_phase_error);
  pulse_spacing = tim15_phase_pulse_spacing_buffers(control_abs_phase);

  sync_phase_fast_edges++;
  sync_phase_last_error_ticks = phase_error;
  sync_phase_last_pulse_delta = pulse_delta;
  sync_phase_fast_last_spacing = pulse_spacing;
  rs485_sync_locked = (uint8_t)(abs_phase <= tim15_sync_get_deadband_ticks());
  rs485_sync_led_active = rs485_sync_locked;

  if (pulse_delta == 0) {
    return;
  }

  if ((sync_phase_fast_last_buf != 0xFFFFFFFFu) &&
      ((current_buf - sync_phase_fast_last_buf) < pulse_spacing)) {
    sync_phase_fast_skip_spacing++;
    return;
  }

  if (tim15_arr_pulse_stage != 0u) {
    sync_phase_fast_skip_busy++;
    return;
  }

  if (tim15_schedule_arr_pulse(pulse_delta) != 0u) {
    sync_phase_fast_last_buf = current_buf;
    sync_phase_fast_pulses++;
  } else {
    sync_phase_fast_skip_busy++;
  }
}

__attribute__((unused)) static uint32_t arr_auto_choose_best(const arr_auto_measure_t *samples, uint32_t count)
{
  uint32_t best_idx = 0u;
  int32_t best_score = 0x7FFFFFFF;
  uint8_t have_bracket = 0u;
  uint32_t index = 0u;

  for (index = 0u; (index + 1u) < count; index++) {
    int32_t left = 0;
    int32_t right = 0;
    int32_t pair_score = 0;
    uint32_t candidate_idx = 0u;

    if (!samples[index].valid || !samples[index + 1u].valid) {
      continue;
    }

    left = samples[index].avg_delta;
    right = samples[index + 1u].avg_delta;
    if ((left == 0) || (right == 0) || ((left < 0) != (right < 0))) {
      pair_score = arr_auto_abs_i32(left) + arr_auto_abs_i32(right);
      candidate_idx = (arr_auto_abs_i32(left) <= arr_auto_abs_i32(right)) ? index : (index + 1u);
      if (!have_bracket || (pair_score < best_score)) {
        have_bracket = 1u;
        best_score = pair_score;
        best_idx = candidate_idx;
      }
    }
  }

  if (have_bracket) {
    return best_idx;
  }

  for (index = 0u; index < count; index++) {
    int32_t score = 0;
    if (!samples[index].valid) {
      continue;
    }
    score = arr_auto_abs_i32(samples[index].avg_delta);
    if (score < best_score) {
      best_score = score;
      best_idx = index;
    }
  }

  return best_idx;
}

static void arr_auto_tune_service(void)
{
  g_arr_auto_selected = TIM15->ARR;
  g_arr_auto_rescan_request = 0u;
  g_arr_auto_ready = 1u;
  g_tune_led_freq_active = 0u;
}

static void phase_micro_adjust_service(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t now_ms = HAL_GetTick();
  static uint32_t last_phase_flip_ms = 0u;

  tim15_request_hold_offset(0);

  if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
      (sync_last_edge_ms != 0u) &&
      ((now_ms - sync_last_edge_ms) <= RS485_SYNC_PRESENT_MS)) {
    if (rs485_anti_phase_recovery_request &&
        ((now_ms - last_phase_flip_ms) >= 250u)) {
        adc_stream_invert_phase_polarity();
        last_phase_flip_ms = now_ms;
        rs485_sync_relation_score = 0;
        rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
        rs485_sync_locked = 0u;
        rs485_sync_led_active = 0u;
        rs485_anti_phase_recovery_active = 0u;
        rs485_anti_phase_recovery_packets = 0u;
        rs485_anti_phase_recovery_request = 0u;
        sync_phase_fast_last_buf = 0xFFFFFFFFu;
        printf("[SYNC] anti-phase detected -> invert local marker polarity, relock IN-PHASE\r\n");
    }
  } else {
    rs485_sync_locked = 0u;
    rs485_sync_led_active = 0u;
    rs485_anti_phase_recovery_active = 0u;
    rs485_anti_phase_recovery_packets = 0u;
    rs485_anti_phase_recovery_request = 0u;
    sync_phase_fast_last_buf = 0xFFFFFFFFu;
  }
}

#ifndef SYNC_PHASE_MONITOR_PRINTF
#define SYNC_PHASE_MONITOR_PRINTF 0
#endif

static void sync_phase_monitor_service(void)
{
  uint32_t primask = __get_PRIMASK();
  uint32_t now_ms = HAL_GetTick();
  uint8_t pending = 0u;
  uint8_t role = 'O';
  uint16_t sample_idx = 0u;
  static uint32_t last_print_ms = 0u;
  static uint8_t have_data = 0u;
  static uint8_t last_role = 'O';
  static uint16_t last_sample = 0u;

  __disable_irq();
  pending = sync_phase_diag_pending;
  if (pending != 0u) {
    role = sync_phase_diag_role;
    sample_idx = sync_phase_diag_sample;
    sync_phase_diag_pending = 0u;
  }
  if (primask == 0u) {
    __enable_irq();
  }

  if (pending != 0u) {
    have_data = 1u;
    last_role = role;
    last_sample = sample_idx;
  }

  if ((!have_data) || ((now_ms - last_print_ms) < 250u)) {
    return;
  }

  last_print_ms = now_ms;
#if SYNC_PHASE_MONITOR_PRINTF
  printf("%c,%u\r\n", (int)last_role, (unsigned)last_sample);
  /* Формат строки: role, real_sample_idx_at_sync */
#endif

  {
    static uint32_t last_rel_print_ms = 0u;
    static uint8_t last_rel = 0xFFu;
    uint8_t rel = rs485_sync_phase_relation;
    uint8_t local_level = rs485_sync_read_local_marker_phase();
    uint8_t local_edge = rs485_sync_edge_kind_from_marker_level(local_level);

    if ((rel != last_rel) || ((now_ms - last_rel_print_ms) >= 1000u)) {
#if SYNC_PHASE_MONITOR_PRINTF
      const char *rel_str = (rel == RS485_SYNC_RELATION_IN_PHASE) ? "IN" :
                            (rel == RS485_SYNC_RELATION_ANTI_PHASE) ? "ANTI" : "UNK";
      printf("[SYNC_REL] rel=%s score=%d local=%u edge=%u remote=%u fix=%u err=%ld pulse=%ld ok=%lu busy=%lu space=%lu uart=%lu ovr=%lu\r\n",
             rel_str,
             (int)rs485_sync_relation_score,
             (unsigned)local_level,
             (unsigned)local_edge,
             (unsigned)rs485_last_sync_edge_kind,
             (unsigned)rs485_anti_phase_recovery_packets,
             (long)sync_phase_last_error_ticks,
             (long)sync_phase_last_pulse_delta,
             (unsigned long)sync_phase_fast_pulses,
             (unsigned long)sync_phase_fast_skip_busy,
             (unsigned long)sync_phase_fast_skip_spacing,
             (unsigned long)rs485_uart_error_count,
             (unsigned long)rs485_dma_rx_overrun_count);
#endif
      last_rel = rel;
      last_rel_print_ms = now_ms;
    }
  }
}

static void tune_led_service(uint32_t now_ms)
{
  extern volatile uint8_t vnd_sync_mode_public;

  /* Если только что был RX по UART1, держим LED включенным поверх фоновой анимации. */
  if (uart1_led_off_tick && (now_ms < uart1_led_off_tick)) {
    LED_ON();
    return;
  }

  if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
      (sync_last_edge_ms != 0u) &&
      ((now_ms - sync_last_edge_ms) <= 250u) &&
      (rs485_sync_led_active != 0u)) {
    LED_ON();
    return;
  }

  if (g_tune_led_freq_active) {
    LED_ON();
    return;
  }

  LED_OFF();
}

static void tim2_led_breathe_service(uint32_t now_ms)
{
  (void)now_ms;

  if (htim2.Instance != TIM2) {
    return;
  }

  /* PA0 / TIM2_CH1 больше не используется как штатный breathing LED.
     Оставляем канал в постоянном "выкл", а сервисную индикацию переносим на onboard WS2812. */
  htim2.Instance->CCER = (htim2.Instance->CCER & ~(TIM_CCER_CC1E | TIM_CCER_CC1P)) | TIM_CCER_CC1E;
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_1, __HAL_TIM_GET_AUTORELOAD(&htim2));
}

static void ws2812_status_service(uint32_t now_ms)
{
#if WS2812_GPIO_BITBANG_TEST_MODE
  (void)now_ms;
  return;
#elif PB2_SPI3_DIRECT_TEST_MODE
  (void)now_ms;
  return;
#else
  ws2812_pattern_t pattern = g_ws2812_test_pattern;

#if WS2812_SPI_SCOPE_TEST_MODE
  (void)now_ms;
  pattern = WS2812_PATTERN_TEST_SCOPE_RGB;
#elif WS2812_BLUE_TEST_MODE
  (void)now_ms;
  pattern = WS2812_PATTERN_TEST_BLUE;
#elif WS2812_COLOR_CYCLE_TEST_MODE
  (void)now_ms;
  pattern = WS2812_PATTERN_TEST_COLOR_CYCLE;
#endif
  ws2812_spi_set_pattern(pattern);
#endif
}

static uint8_t diag_get_alarm_text(char *buf, size_t buf_sz, uint16_t *color_out)
{
  uint32_t last_error;

  if ((buf == NULL) || (buf_sz == 0u)) {
    return 0u;
  }

  buf[0] = '\0';
  if (color_out != NULL) {
    *color_out = WHITE;
  }

  if (need_hard_reset != 0u) {
    snprintf(buf, buf_sz, "ERR:HARD_RESET");
    if (color_out != NULL) {
      *color_out = RED;
    }
    return 1u;
  }

  if (need_recovery != 0u) {
    snprintf(buf, buf_sz, "ERR:RECOVERY");
    if (color_out != NULL) {
      *color_out = YELLOW;
    }
    return 1u;
  }

  last_error = vnd_get_last_error();
  if (last_error != 0u) {
    const char *error_text = "USB_ERR";

    switch (last_error) {
      case 1u:
        error_text = "DMA_TIMEOUT";
        break;
      case 3u:
        error_text = "TX_REJECT";
        break;
      case 4u:
        error_text = "USB_BUSY";
        break;
      default:
        error_text = "USB_ERR";
        break;
    }

    snprintf(buf, buf_sz, "ERR:%s", error_text);
    if (color_out != NULL) {
      *color_out = RED;
    }
    return 1u;
  }

  return 0u;
}

static void diag_alarm_com_service(uint32_t now_ms)
{
  enum {
    DIAG_ALARM_COM_PERIOD_MS = 10000u
  };
  static uint32_t last_report_ms = 0u;
  static uint8_t prev_active = 0u;
  static char prev_text[32] = "";
  char text[32];
  uint16_t color = WHITE;
  uint8_t active;

  active = diag_get_alarm_text(text, sizeof(text), &color);
  (void)color;

  if (active != 0u) {
    uint8_t changed = (uint8_t)((prev_active == 0u) || (strncmp(prev_text, text, sizeof(prev_text)) != 0));
    if (changed || ((uint32_t)(now_ms - last_report_ms) >= DIAG_ALARM_COM_PERIOD_MS)) {
      printf("[ALARM] %s rec=%u hard=%u usb_err=%lu stream=%u tx=%u\r\n",
             text,
             (unsigned)need_recovery,
             (unsigned)need_hard_reset,
             (unsigned long)vnd_get_last_error(),
             (unsigned)vnd_is_streaming(),
             (unsigned)vnd_is_tx_enabled());
      last_report_ms = now_ms;
    }
    strncpy(prev_text, text, sizeof(prev_text) - 1u);
    prev_text[sizeof(prev_text) - 1u] = '\0';
  } else if (prev_active != 0u) {
    printf("[ALARM] CLEARED\r\n");
    last_report_ms = now_ms;
    prev_text[0] = '\0';
  }

  prev_active = active;
}

static void ws2812_gpio_test_init(void)
{
#if WS2812_GPIO_BITBANG_TEST_MODE
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOB_CLK_ENABLE();
  HAL_SPI_DeInit(&hspi3);

  GPIO_InitStruct.Pin = GPIO_PIN_2;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
  GPIOB->BSRR = ((uint32_t)GPIO_PIN_2 << 16);
#endif
}

static void ws2812_gpio_test_wait_cycles(uint32_t cycles)
{
  uint32_t start = DWT->CYCCNT;
  while ((uint32_t)(DWT->CYCCNT - start) < cycles) {
    __NOP();
  }
}

static void ws2812_gpio_test_send_byte(uint8_t value)
{
  uint8_t bit = 0u;
  const uint32_t t0h = 96u;   /* ~350 ns @ 275 MHz */
  const uint32_t t0l = 220u;  /* ~800 ns */
  const uint32_t t1h = 193u;  /* ~700 ns */
  const uint32_t t1l = 165u;  /* ~600 ns */

  for (bit = 0u; bit < 8u; ++bit) {
    if ((value & 0x80u) != 0u) {
      GPIOB->BSRR = GPIO_PIN_2;
      ws2812_gpio_test_wait_cycles(t1h);
      GPIOB->BSRR = ((uint32_t)GPIO_PIN_2 << 16);
      ws2812_gpio_test_wait_cycles(t1l);
    } else {
      GPIOB->BSRR = GPIO_PIN_2;
      ws2812_gpio_test_wait_cycles(t0h);
      GPIOB->BSRR = ((uint32_t)GPIO_PIN_2 << 16);
      ws2812_gpio_test_wait_cycles(t0l);
    }
    value <<= 1;
  }
}

static void ws2812_gpio_test_send_rgb(uint8_t r, uint8_t g, uint8_t b)
{
  __disable_irq();
  ws2812_gpio_test_send_byte(g);
  ws2812_gpio_test_send_byte(r);
  ws2812_gpio_test_send_byte(b);
  __enable_irq();
  HAL_Delay(1);
}

static void ws2812_gpio_test_service(uint32_t now_ms)
{
#if WS2812_GPIO_BITBANG_TEST_MODE
  static uint32_t last_push_ms = 0u;
  static uint8_t phase = 0u;
  uint8_t r = 0u;
  uint8_t g = 0u;
  uint8_t b = 0u;

  if ((now_ms - last_push_ms) < 700u) {
    return;
  }

  switch (phase) {
    case 1u:
      r = 24u;
      break;
    case 3u:
      g = 24u;
      break;
    case 5u:
      b = 24u;
      break;
    default:
      break;
  }

  ws2812_gpio_test_send_rgb(r, g, b);
  last_push_ms = now_ms;
  phase = (uint8_t)((phase + 1u) % 6u);
#else
  (void)now_ms;
#endif
}

static void pb2_spi3_direct_test_service(uint32_t now_ms)
{
#if PB2_SPI3_DIRECT_TEST_MODE
  static uint32_t last_tx_ms = 0u;
  static const uint8_t pattern[] = {
    0xAAu, 0x55u, 0xF0u, 0x0Fu, 0xCCu, 0x33u, 0x96u, 0x69u
  };

  if ((now_ms - last_tx_ms) < 1u) {
    return;
  }

  if (HAL_SPI_Transmit(&hspi3, (uint8_t *)pattern, (uint16_t)sizeof(pattern), 10u) == HAL_OK) {
    last_tx_ms = now_ms;
  }
#else
  (void)now_ms;
#endif
}

void rs485_sync_on_buffer_complete(uint8_t parity)
{
  extern volatile uint32_t adc_stream_total_buffer_count;
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t sync_byte = 0u;

  rs485_sync_buf_div4 = adc_stream_total_buffer_count;

  /* Только MASTER шлёт sync. Вызов — прямо из DMA ISR, сразу после
     adc_marker_pa3_toggle(). parity = текущее состояние PA2/PC7. */
  if (vnd_sync_mode_public != VND_SYNC_MODE_MASTER) {
    rs485_discovery_reset_master_scan();
    return;
  }

  if (rs485_status_window_active != 0u) {
    rs485_status_finalize_window();
  }
  sync_tim5_period_ticks = htim5.Instance->CNT;
  htim5.Instance->CNT = 0u;
  rs485_status_current_window_phase = (uint8_t)(parity % RS485_STATUS_SLOT_STRIDE);
  rs485_status_window_after_tx = 1u;
  sync_byte = (uint8_t)(RS485_SYNC7_BASE | (parity ? RS485_SYNC_EDGE_BIT : 0u));
  /* sync-байт должен уходить максимально близко к событию DMA завершения буфера.
     Если UART свободен — стартуем сразу, без лишней работы с очередью. */
  if ((rs485_tx_busy == 0u) && (rs485_tx_queue_count == 0u)) {
    rs485_sync_start_tx_byte(sync_byte);
  } else {
    rs485_tx_queue_push_front(sync_byte);
    rs485_tx_kick();
  }
}
/* USER CODE END 0 */

uint32_t tim2_apply_profile_window(void){
  uint16_t buf_rate = adc_stream_get_buf_rate();
  uint16_t samples  = adc_stream_get_active_samples();
  if (buf_rate == 0u) {
    printf("[TIM2] skip apply: buf_rate=0\r\n");
    return 0u;
  }

  /* Тактирование TIM2: APB1 timer clock удваивается при делителе ≠1 */
  uint32_t tim_clk = HAL_RCC_GetPCLK1Freq();
  uint32_t d2ppre1 = (RCC->D2CFGR & RCC_D2CFGR_D2PPRE1_Msk) >> RCC_D2CFGR_D2PPRE1_Pos;
  if (d2ppre1 != 0u) {
    tim_clk *= 2u;
  }
  uint32_t tick_hz = tim_clk / (htim2.Init.Prescaler + 1u);
  if (tick_hz == 0u) {
    return 0u;
  }

    /* TIM2 теперь используется ТОЛЬКО как маркер/делитель на 2 (PA2 = TIM2_CH3).
      buf_rate = частота готовых DMA-буферов (например 400 Гц при fs=240кГц и N=600)
      → период TIM2 = tick_hz / (buf_rate/2) для получения 200 Гц меандра CH3 */
  uint32_t period_ticks = (tick_hz * 2u) / buf_rate; // период для 200 Гц меандра (при buf_rate=400)
  if (period_ticks < 16u) {
    period_ticks = 16u; // минимальная защита
  }
  /* Запас оставляем постоянным (TIM2_WINDOW_GUARD_US) в тиках таймера */
  uint32_t guard_ticks = (uint32_t)(((uint64_t)tick_hz * (uint64_t)TIM2_WINDOW_GUARD_US + 999999ULL) / 1000000ULL);
  if (guard_ticks == 0u) {
    guard_ticks = 1u;
  }
  /* Длительность HIGH: всё оставшееся после хвоста. Не растягиваем период, чтобы сохранить f_buf. */
  uint32_t pulse_ticks = (period_ticks > guard_ticks) ? (period_ticks - guard_ticks) : 1u;

  __HAL_TIM_SET_AUTORELOAD(&htim2, period_ticks - 1u);
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_1, pulse_ticks);
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_2, period_ticks - 1u); // индикатор HIGH весь период
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_3, period_ticks / 2u); // контрольный меандр 50%
  __HAL_TIM_SET_COUNTER(&htim2, 0u);

    uint32_t marker_hz = (period_ticks ? (tick_hz / period_ticks) : 0u);
    printf("[TIM2] marker apply: N=%u f_buf=%uHz f_marker=%uHz tick_hz=%lu ARR=%lu CCR1=%lu guard=%luus\r\n",
      (unsigned)samples, (unsigned)buf_rate, (unsigned)marker_hz,
         (unsigned long)tick_hz, (unsigned long)(period_ticks - 1u), (unsigned long)pulse_ticks,
         (unsigned long)TIM2_WINDOW_GUARD_US);
  return pulse_ticks;
}

/**
  * @brief  The application entry point.
  */
int main(void)
{
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
  MX_USART2_UART_Init();
  rs485_load_local_uid();
  rs485_local_uid_hint = rs485_compute_local_node_id();
  rs485_master_claim_delay_ms = rs485_compute_master_claim_delay_ms();
  rs485_local_node_id = 0u;
  rs485_discovery_reset_master_scan();
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
  /* RS485 UID/slot banner отключён: COM оставляем под phase-monitor. */
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
  MX_SPI1_Init();
  MX_SPI3_Init();
  ws2812_spi_init();
  ws2812_gpio_test_init();
  MX_TIM6_Init();
  MX_ADC1_Init();
  MX_ADC2_Init();
  MX_DAC1_Init();
  MX_TIM2_Init();
  MX_TIM3_Init();
  MX_TIM15_Init();
  MX_TIM5_Init();
  MX_USART1_UART_Init();
  /* USER CODE: Диагностика TIM15/ADC после всех Init */
  printf("[TIM15][CFG] TIM15->CR2=0x%08lX MMS=%lu (expected 2=UPDATE)\r\n",
         (unsigned long)TIM15->CR2, (unsigned long)((TIM15->CR2 >> 4) & 0x7));
  printf("[TIM15][CFG] PSC=%lu ARR=%lu → f_counter=275MHz/(PSC+1)=275MHz, f_UPDATE=275MHz/(ARR+1)=%lu kHz\r\n",
         (unsigned long)TIM15->PSC, (unsigned long)TIM15->ARR, 
         (unsigned long)(275000 / (TIM15->ARR + 1)));
    {
      const unsigned long ext_sel = (unsigned long)((ADC1->CFGR >> ADC_CFGR_EXTSEL_Pos) & 0x1FUL);
      const unsigned long ext_en  = (unsigned long)((ADC1->CFGR >> ADC_CFGR_EXTEN_Pos)  & 0x3UL);
      const unsigned long exp_sel = (unsigned long)((hadc1.Init.ExternalTrigConv     >> ADC_CFGR_EXTSEL_Pos) & 0x1FUL);
      const unsigned long exp_en  = (unsigned long)((hadc1.Init.ExternalTrigConvEdge >> ADC_CFGR_EXTEN_Pos)  & 0x3UL);
      printf("[ADC1][CFG] ADC1->CFGR=0x%08lX EXTSEL=%lu EXTEN=%lu (expected EXTSEL=%lu EXTEN=%lu from HAL)\r\n",
        (unsigned long)ADC1->CFGR, ext_sel, ext_en, exp_sel, exp_en);
    }
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
  // УДАЛЕНА гигантская задержка 200M NOP (~0.36s) - не нужна
  MX_GPIO_Init();
  /* Trap после MX_GPIO_Init */
#if defined(DIAG_TRAP_STAGE) && (DIAG_TRAP_STAGE==4)
  diag_trap(4);
#endif
  // ВАЖНО: TIM2 запускается ПОСЛЕ инициализации ADC (см. ниже)
  // чтобы прерывания TIM2 не вызывали adc_stream_tim2_switch_buffers()
  // до готовности ADC

  // TIM3 используется как аппаратный gate для оптики: 40 импульсов ON / 8 импульсов OFF.
  // Старт канала CH1 выполняется внутри optic_tx_start() для синхронного запуска с TIM1.

  // Запуск TIM5 как 32-битного счётчика фазы
  HAL_TIM_Base_Start(&htim5);

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
  optic_tx_start();
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
     /* На LCD последняя текстовая строка (y=56) зарезервирована под диагностику DC.
       Поэтому VID/PID на экран больше не выводим (оставляем только UART printf). */
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
    // CRITICAL: Сбросить счётчик TIM15 в 0 ПЕРЕД стартом для синхронизации с TIM2
    // Иначе первый буфер ADC начнётся с произвольной фазы
    __HAL_TIM_SET_COUNTER(&htim15, 0);
    
    HAL_StatusTypeDef st = HAL_TIM_Base_Start(&htim15);
    if (st != HAL_OK) {
      printf("[TIM15][ERR] HAL_TIM_Base_Start status=%d (state=%d) -> entering Error_Handler\r\n", st, htim15.State);
      err_code = 1002;
      Error_Handler();
    }

    if (HAL_TIM_OC_Start(&htim15, TIM_CHANNEL_1) != HAL_OK) {
      printf("[TIM15][ERR] HAL_TIM_OC_Start CH1 failed\r\n");
      err_code = 1003;
      Error_Handler();
    }

    HAL_NVIC_SetPriority(TIM15_IRQn, 3, 0);
    HAL_NVIC_EnableIRQ(TIM15_IRQn);
    
    // Ждём первого UPDATE от TIM2 (TRGO->ITR1->RESET TIM15) для полной синхронизации
    // TIM2 @ 200 Hz = 5 мс период, максимальное ожидание ~5 мс
    uint32_t sync_timeout = HAL_GetTick() + 10;
    while(__HAL_TIM_GET_COUNTER(&htim15) > 100 && HAL_GetTick() < sync_timeout) {
      // Ждём сброса счётчика TIM15 от TIM2 TRGO
    }
  }
  printf("[TIM15] Started & synced: CR1=0x%08lX SR=0x%08lX CNT=%lu SMCR=0x%08lX\r\n",
         (unsigned long)TIM15->CR1, (unsigned long)TIM15->SR, (unsigned long)TIM15->CNT, (unsigned long)TIM15->SMCR);
  
  // Диагностика: проверяем что TIM15 действительно считает
  uint32_t cnt_before = TIM15->CNT;
  HAL_Delay(1);  // 1 мс
  uint32_t cnt_after = TIM15->CNT;
  printf("[TIM15][TEST] Counter increment test: before=%lu after=%lu delta=%ld (expected ~275)\r\n",
         (unsigned long)cnt_before, (unsigned long)cnt_after, (long)(cnt_after - cnt_before));
  STAGE(22,"TRGON");
  
  // ТЕПЕРЬ запускаем TIM2 CH1 с прерыванием (ПОСЛЕ инициализации ADC!)
    uint32_t tim2_pulse_ticks = tim2_apply_profile_window();
  
  // ДИАГНОСТИКА: проверка NVIC перед запуском TIM2 PWM IRQ
  uint32_t nvic_iser0 = NVIC->ISER[TIM2_IRQn >> 5];
  uint32_t tim2_bit = 1UL << (TIM2_IRQn & 0x1F);
  printf("[TIM2][NVIC] Before Start_IT: TIM2_IRQn=%d enabled=%lu\r\n",
         TIM2_IRQn, (unsigned long)((nvic_iser0 & tim2_bit) ? 1 : 0));
  
      /* TIM2 не управляет DMA и не генерирует PWM на PA2.
        Используем TIM2 как счётчик/делитель и одновременно как PWM для LED на PA0 (CH1). */
      HAL_TIM_PWM_Start(&htim2, TIM_CHANNEL_1);
  
  // ДИАГНОСТИКА: проверка после запуска
  nvic_iser0 = NVIC->ISER[TIM2_IRQn >> 5];
  printf("[TIM2][NVIC] After Start_IT: enabled=%lu CR1=0x%08lX DIER=0x%08lX SR=0x%08lX\r\n",
         (unsigned long)((nvic_iser0 & tim2_bit) ? 1 : 0),
         (unsigned long)TIM2->CR1, (unsigned long)TIM2->DIER, (unsigned long)TIM2->SR);
    printf("[TIM2] Started with CH1 PWM interrupt (CCR1=%lu ticks, ARR=%lu)\r\n",
      (unsigned long)tim2_pulse_ticks, (unsigned long)htim2.Instance->ARR);
  
  HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_SET);
  UpdateLCDStatus();
#elif MINIMAL_BRINGUP
  // Облегчённый путь для MINIMAL_BRINGUP (не используется в SAFE_MINIMAL)
  HAL_GPIO_WritePin(Data_ready_GPIO22_GPIO_Port, Data_ready_GPIO22_Pin, GPIO_PIN_SET);
  CHECK(adc_stream_start(&hadc1, &hadc2), 1101);
  __HAL_TIM_DISABLE(&htim15);
  // ВАЖНО: Не трогаем SMCR! TIM15 в GATED mode управляется TIM2 CH1
  __HAL_TIM_SET_COUNTER(&htim15,0);
  
  // Runtime phase correction via TIM15 update IRQ is disabled.
  __HAL_TIM_DISABLE_IT(&htim15, TIM_IT_UPDATE);
  HAL_NVIC_SetPriority(TIM15_IRQn, 3, 0);  // Средний приоритет
  HAL_NVIC_EnableIRQ(TIM15_IRQn);
  
  CHECK(HAL_TIM_Base_Start(&htim15), 1102);
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
  (void)last_diag_ms;
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
  optic_sensor_service(now);
  ws2812_test_button_service(now);
  static uint8_t first_loop=1; if(first_loop){ PROG('M'); first_loop=0; }
  PROG('A'); // loop start

  // ДИАГНОСТИКА TIM2: очень шумный вывод, включать только при необходимости
#if defined(DIAG_TIM2_ENABLE) && (DIAG_TIM2_ENABLE == 1)
  static uint32_t last_tim2_check_ms = 0;
  if (now - last_tim2_check_ms >= 5000) {
    last_tim2_check_ms = now;
    printf("[TIM2][DIAG] CR1=0x%08lX CNT=%lu ARR=%lu SR=0x%08lX DIER=0x%08lX\r\n",
           (unsigned long)TIM2->CR1, (unsigned long)TIM2->CNT,
           (unsigned long)TIM2->ARR, (unsigned long)TIM2->SR, (unsigned long)TIM2->DIER);
  }
#endif

  arr_auto_tune_service();
  phase_micro_adjust_service();
  sync_phase_monitor_service();

  /* DEBUG dumps отключены: TIM2 больше не управляет DMA, а вывод s_frame_buffer_idx в COM4 шумит. */

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

  /* Авто-STOP: если хост не активен (нет SOF) — выключить передачу */
#ifndef USB_AUTO_STOP_ON_NO_SOF
#define USB_AUTO_STOP_ON_NO_SOF 0u
#endif
#if !SAFE_MINIMAL && USB_AUTO_STOP_ON_NO_SOF
  {
    extern uint8_t vnd_is_streaming(void);
    if (vnd_is_streaming()) {
      uint32_t dt_sof = (g_usb_last_sof_ms > 0u) ? (now - g_usb_last_sof_ms) : 0xFFFFFFFFu;
      uint8_t host_present = (hUsbDeviceHS.dev_state == USBD_STATE_SUSPENDED) || (dt_sof < 400u);
      if (!host_present) {
        extern void vnd_pipeline_stop_reset(int deep);
        vnd_pipeline_stop_reset(0);
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_2, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOC, GPIO_PIN_7, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1, GPIO_PIN_SET);
      }
    }
  }
#endif

  // PROG('V'); // vendor diag disabled for isolation
  // vnd_diag_send64_once();
  // PROG('v');

  /* Запуск задачи стриминга: вызываем при сигнале kick ИЛИ активном стриме */
  // vendor stream task
#if !SAFE_MINIMAL
  extern volatile uint8_t vnd_tx_kick;
  extern uint8_t vnd_is_streaming(void);
  /* Периодический SYNC-лог отключён: COM оставляем под compact phase-monitor. */
  rs485_sync_auto_role_service(now);
  /* rs485_uid_service: UID-фрейм отключён — он мешал sync-арбитражу */
  /* Периодический ROLE-лог отключён: в COM оставляем только компактный phase-monitor. */
  if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
      (sync_last_edge_ms != 0u) &&
      ((now - sync_last_edge_ms) > 250u)) {
    rs485_sync_relation_score = 0;
    rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
    rs485_anti_phase_recovery_active = 0u;
    rs485_anti_phase_recovery_packets = 0u;
    tim15_request_hold_offset(0);
  }
  /* Подстройка частоты TIM15 по фазе (TIM16 счётчик, PD5 reset) */
  {
    extern void adc_sync_pd5_apply_adjustment(void);
    adc_sync_pd5_apply_adjustment();
  }
  rs485_sync_service_tx();
  /* Выравнивание TIM15 по границе буфера — выполняем вне ISR */
  #ifndef SYNC_ACTIONS_ENABLE
  #define SYNC_ACTIONS_ENABLE 0u
  #endif
  #if SYNC_ACTIONS_ENABLE
    if (sync_align_pending) {
      sync_align_pending = 0u;
      __HAL_TIM_SET_COUNTER(&htim15, 0u);
      htim15.Instance->EGR = TIM_EGR_UG;
      sync_phase_lock_active = 1u;
    }
  #endif
  /* ВАЖНО: Vendor_Stream_Task() обслуживает не только USB TX, но и always-on фоновые задачи
     (например, DC адаптацию/сохранение). Поэтому вызываем периодически даже без START/GUI.
     При наличии kick/streaming — вызываем сразу без ожидания периода. */
  {
    static uint32_t last_vendor_ms = 0;
    if (vnd_tx_kick || vnd_is_streaming() || (now - last_vendor_ms) >= 5u) {
      last_vendor_ms = now;
      extern void Vendor_Stream_Task(void);
      Vendor_Stream_Task();
    }
  }
  /* Вотчдог ADC/DMA: если давно нет DMA событий, мягко перезапустить цепочку выборки. */
  {
  extern void adc_stream_watchdog(void);
  adc_stream_watchdog();
  }
  /* Применение подстройки частоты TIM15 по PD5 (slave polling) */
  {
    extern void adc_sync_pd5_apply_adjustment(void);
    adc_sync_pd5_apply_adjustment();
  }
  // Проверка и выключение LED по таймауту (UART RX индикация)
  extern void CDC_LED_Process(void);
  CDC_LED_Process();
#endif

  /* Периодическое обновление статуса на LCD (вернули после отката) */
  {
    static uint32_t last_lcd_ms = 0;
    static uint8_t lcd_first_update = 1;
    // Первое обновление сразу после старта (в течение первых 200ms)
    if (lcd_first_update && now >= 200) {
      lcd_first_update = 0;
      last_lcd_ms = now;
      DrawUSBStatus();
    }
    // Последующие обновления каждые 100ms
    else if (!lcd_first_update && (now - last_lcd_ms >= 100)) { // ~10 Гц
      last_lcd_ms = now;
      DrawUSBStatus();
    }
  }

  if (need_recovery || need_hard_reset) {
#if ENABLE_SOFT_USB_RECOVERY
    if (need_hard_reset) {
      /* Полный аппаратный reset: корректно отключиться от USB и выполнить NVIC_SystemReset */
      need_hard_reset = 0; /* гасим флаг, чтобы не войти повторно */
      printf("[RST] HARD: USB disconnect + NVIC_SystemReset\r\n");
      /* Программное отключение D+ (soft disconnect) + останов USB устройства */
      extern void USB_LL_SetSoftDisconnect(uint8_t enable);
      USB_LL_SetSoftDisconnect(1);
      HAL_Delay(60);
#ifdef HAL_PCD_MODULE_ENABLED
      USBD_Stop(&hUsbDeviceHS);
      USBD_DeInit(&hUsbDeviceHS);
#endif
      HAL_Delay(20);
      boot_diag_finalize_before_reset(HAL_GetTick());
      NVIC_SystemReset();
    }
    /* Иначе — мягкое восстановление USB стека (без MCU reset) */
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
  diag_alarm_com_service(now);
  /* Обработка таймаута выключения LED после UART1 RX */
  if(uart1_led_off_tick && HAL_GetTick() >= uart1_led_off_tick){
    LED_OFF();
    uart1_led_off_tick = 0;
  }
  tune_led_service(now);
  tim2_led_breathe_service(now);
  ws2812_gpio_test_service(now);
  pb2_spi3_direct_test_service(now);
  ws2812_status_service(now);
  ws2812_spi_service(now);
  /* Обработка приёма по UART1: сбор строки и разбор команд (вне ISR) */
  while(uart1_rx_ring_rd != uart1_rx_ring_wr){
    uint8_t ch = uart1_rx_ring[uart1_rx_ring_rd & (UART1_RX_RING_SZ-1)];
    uart1_rx_ring_rd++;
    if(ch == '\r' || ch == '\n'){
      if(uart1_cmd_len > 0){
        // Завершаем строку и парсим
        uart1_cmd_buf[(uart1_cmd_len < (UART1_CMD_MAX-1)) ? uart1_cmd_len : (UART1_CMD_MAX-1)] = 0;
        // Преобразуем в верхний регистр для простого сравнения
        for(uint16_t i=0;i<uart1_cmd_len;i++){
          char c = uart1_cmd_buf[i];
          if(c >= 'a' && c <= 'z') uart1_cmd_buf[i] = (char)(c - 'a' + 'A');
        }
        // Обработка команд аналогично USB CDC
        if(strncmp(uart1_cmd_buf, "HELP", 4) == 0){
          printf("\r\n=== DEBUG COMMANDS (UART1) ===\r\n");
          printf("VER          - firmware version\r\n");
          printf("STATUS       - current state\r\n");
          printf("RS485        - RS485 sync/role detailed status\r\n");
          printf("ROLE MASTER  - force master role (host-forced)\r\n");
          printf("ROLE SLAVE   - force slave role (host-forced)\r\n");
          printf("ROLE AUTO    - release host-forced, return to auto\r\n");
          printf("PHASE [AUTO|ticks] - show/set sync target phase\r\n");
          printf("PERF         - performance stats\r\n");
          printf("FPS          - FPS statistics only\r\n");
          printf("RESET        - software reset\r\n");
          printf("HELP         - this help message\r\n");
          printf("===============================\r\n");
        } else if(strncmp(uart1_cmd_buf, "VER", 3) == 0 || strncmp(uart1_cmd_buf, "VERSION", 7) == 0){
          printf("\r\n=== FIRMWARE VERSION (UART1) ===\r\n");
          printf("Version: %s\r\n", FW_VERSION_STR);
          printf("Git:     %s\r\n", fw_git_hash);
          printf("Built:   %s %s\r\n", fw_build_date, fw_build_time);
          printf("VND_PAIR_BUFFERS: %d\r\n", 8);
          printf("================================\r\n");
        } else if(strncmp(uart1_cmd_buf, "STATUS", 6) == 0){
          printf("\r\n=== DEVICE STATUS (UART1) ===\r\n");
          printf("Uptime: %lu ms\r\n", HAL_GetTick());
          if (g_sync_target_phase_ticks == SYNC_TARGET_PHASE_AUTO) {
            printf("Sync target phase: AUTO target=%ld samples\r\n", (long)SYNC_TARGET_PHASE_SAMPLES);
          } else {
            printf("Sync target phase: %ld ticks\r\n", (long)((int32_t)g_sync_target_phase_ticks));
          }
          printf("Use 'PERF' or 'FPS' for detailed statistics\r\n");
          printf("==============================\r\n");
        } else if(strncmp(uart1_cmd_buf, "PHASE", 5) == 0){
          char *arg = uart1_cmd_buf + 5;
          while(*arg == ' ') arg++;
          if(*arg == 0){
            if (g_sync_target_phase_ticks == SYNC_TARGET_PHASE_AUTO) {
              printf("[UART] PHASE=AUTO target=%ld samples\r\n", (long)SYNC_TARGET_PHASE_SAMPLES);
            } else {
              printf("[UART] PHASE=%ld ticks\r\n", (long)((int32_t)g_sync_target_phase_ticks));
            }
          } else if(strcmp(arg, "AUTO") == 0){
            g_sync_target_phase_ticks = SYNC_TARGET_PHASE_AUTO;
            printf("[UART] PHASE=AUTO target=%ld samples\r\n", (long)SYNC_TARGET_PHASE_SAMPLES);
          } else {
            char *end_ptr = NULL;
            long phase_ticks = strtol(arg, &end_ptr, 10);
            while (end_ptr && *end_ptr == ' ') end_ptr++;
            if ((end_ptr == arg) || (end_ptr && *end_ptr != 0)) {
              printf("[UART] PHASE parse error: '%s'\r\n", arg);
            } else {
              g_sync_target_phase_ticks = (uint32_t)((int32_t)phase_ticks);
              printf("[UART] PHASE=%ld ticks\r\n", phase_ticks);
            }
          }
        } else if(strncmp(uart1_cmd_buf, "FPS", 3) == 0){
          vnd_report_fps_stats();
        } else if(strncmp(uart1_cmd_buf, "PERF", 4) == 0){
          vnd_print_perf_stats();
        } else if(strncmp(uart1_cmd_buf, "RESET", 5) == 0){
          printf("[UART] RESET command received - performing software reset\r\n");
          HAL_Delay(100);
          NVIC_SystemReset();
        } else if(strncmp(uart1_cmd_buf, "RS485", 5) == 0){
          /* Подробный статус RS485 sync/role */
          uint32_t now_ms = HAL_GetTick();
          uint32_t edge_age = (sync_last_edge_ms == 0u) ? 0xFFFFFFFFu : (now_ms - sync_last_edge_ms);
          uint8_t next_slot = (rs485_slave_count_estimate < RS485_DISCOVERY_MAX_ID) ?
                              (uint8_t)(rs485_slave_count_estimate + 1u) :
                              RS485_DISCOVERY_MAX_ID;
          printf("\r\n=== RS485 STATUS ===\r\n");
          printf("mode        : %u (%s)\r\n",
            (unsigned)vnd_sync_mode_public,
            (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) ? "MASTER" :
            (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE)  ? "SLAVE"  : "OFF");
          printf("host_forced : %u\r\n", (unsigned)vnd_sync_is_mode_host_forced());
          printf("node_id     : %u\r\n", (unsigned)rs485_local_node_id);
          printf("slave_count : %u\r\n", (unsigned)rs485_slave_count_estimate);
          printf("next_slot   : %u\r\n", (unsigned)next_slot);
          printf("status_byte : 0x%02X\r\n", (unsigned)rs485_status_build_local_byte());
          printf("sync_edges  : %lu\r\n", (unsigned long)sync_edge_count);
          printf("sync_age_ms : %lu\r\n", (unsigned long)edge_age);
          printf("sync_alive  : %u\r\n", (unsigned)(edge_age <= RS485_SYNC_PRESENT_MS && sync_last_edge_ms != 0u));
          printf("phase_rel   : %u (%s)\r\n",
            (unsigned)rs485_sync_phase_relation,
            (rs485_sync_phase_relation == RS485_SYNC_RELATION_IN_PHASE) ? "IN_PHASE" :
            (rs485_sync_phase_relation == RS485_SYNC_RELATION_ANTI_PHASE) ? "ANTI_PHASE" : "UNKNOWN");
          printf("anti_fix    : %u, packets=%u\r\n",
            (unsigned)rs485_anti_phase_recovery_active,
            (unsigned)rs485_anti_phase_recovery_packets);
          printf("uart_errs   : %lu\r\n", (unsigned long)rs485_uart_error_count);
          printf("rs485_rx    : %lu\r\n", (unsigned long)rs485_rx_packets);
          printf("rs485_tx    : %lu\r\n", (unsigned long)rs485_tx_packets);
          printf("peer_uid_ok : %u\r\n", (unsigned)rs485_peer_uid_valid);
          printf("peer_uid_cmp: %d\r\n", (int)rs485_peer_uid_cmp);
          printf("uid_local   : %08lX-%08lX-%08lX\r\n",
            (unsigned long)rs485_local_uid_words[2],
            (unsigned long)rs485_local_uid_words[1],
            (unsigned long)rs485_local_uid_words[0]);
          printf("uid_peer    : %08lX-%08lX-%08lX\r\n",
            (unsigned long)rs485_peer_uid_words[2],
            (unsigned long)rs485_peer_uid_words[1],
            (unsigned long)rs485_peer_uid_words[0]);
          printf("sync_ok_pub : %u\r\n", (unsigned)vnd_sync_ok_public);
          printf("====================\r\n");
        } else if(strncmp(uart1_cmd_buf, "ROLE", 4) == 0){
          char *arg = uart1_cmd_buf + 4;
          while(*arg == ' ') arg++;
          if(strncmp(arg, "MASTER", 6) == 0){
            extern void vnd_sync_apply_mode_forced(uint8_t mode);
            vnd_sync_apply_mode_forced(VND_SYNC_MODE_MASTER);
            printf("[UART] ROLE forced -> MASTER\r\n");
          } else if(strncmp(arg, "SLAVE", 5) == 0){
            extern void vnd_sync_apply_mode_forced(uint8_t mode);
            vnd_sync_apply_mode_forced(VND_SYNC_MODE_SLAVE);
            printf("[UART] ROLE forced -> SLAVE\r\n");
          } else if(strncmp(arg, "AUTO", 4) == 0){
            extern void vnd_sync_release_host_forced(void);
            vnd_sync_release_host_forced();
            printf("[UART] ROLE -> AUTO (host-forced released)\r\n");
          } else {
            printf("[UART] ROLE usage: ROLE MASTER | ROLE SLAVE | ROLE AUTO\r\n");
          }
        } else {
          printf("[UART] Unknown command: '%s'\r\n", uart1_cmd_buf);
        }
        uart1_cmd_len = 0; // сброс буфера
      }
    } else if(ch == 0x08 || ch == 0x7F){
      // backspace
      if(uart1_cmd_len > 0) uart1_cmd_len--;
    } else {
      if(uart1_cmd_len < (UART1_CMD_MAX-1)){
        uart1_cmd_buf[uart1_cmd_len++] = (char)ch;
      }
    }
  }
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
  PeriphClkInitStruct.PLL3.PLL3M = 5;
  PeriphClkInitStruct.PLL3.PLL3N = 80;
  PeriphClkInitStruct.PLL3.PLL3P = 4;
  PeriphClkInitStruct.PLL3.PLL3Q = 8;
  PeriphClkInitStruct.PLL3.PLL3R = 8;
  PeriphClkInitStruct.PLL3.PLL3RGE = RCC_PLL3VCIRANGE_2;
  PeriphClkInitStruct.PLL3.PLL3VCOSEL = RCC_PLL3VCOWIDE;
  PeriphClkInitStruct.PLL3.PLL3FRACN = 0;
  PeriphClkInitStruct.Spi123ClockSelection = RCC_SPI123CLKSOURCE_PLL3;
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
  hadc1.Init.EOCSelection = ADC_EOC_SINGLE_CONV;  /* EOC после каждой конверсии - правильно для External Trigger */
  hadc1.Init.LowPowerAutoWait = DISABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.NbrOfConversion = 1;  /* 1 конверсия на External Trigger */
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.ExternalTrigConv = ADC_EXTERNALTRIG_T15_TRGO;
  hadc1.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_RISING;
  hadc1.Init.ConversionDataManagement = ADC_CONVERSIONDATA_DMA_ONESHOT;
  hadc1.Init.Overrun = ADC_OVR_DATA_PRESERVED;
  hadc1.Init.LeftBitShift = ADC_LEFTBITSHIFT_NONE;
  hadc1.Init.OversamplingMode = DISABLE;
  hadc1.Init.Oversampling.Ratio = 1;
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE: External trigger configuration moved to adc_stream.c (before DMA start) */

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
  /* Диагностика ADC1 external trigger конфигурации */
  printf("[ADC1][CFG] ADC1->CFGR=0x%08lX EXTSEL=%lu EXTEN=%lu\r\n",
         (unsigned long)ADC1->CFGR,
         (unsigned long)((ADC1->CFGR >> 5) & 0x1F),  // EXTSEL[9:5]
         (unsigned long)((ADC1->CFGR >> 10) & 0x3)); // EXTEN[11:10]
    printf("[ADC1][CFG] Expected (from HAL init): EXTSEL=%lu EXTEN=%lu\r\n",
      (unsigned long)((hadc1.Init.ExternalTrigConv     >> ADC_CFGR_EXTSEL_Pos) & 0x1FUL),
      (unsigned long)((hadc1.Init.ExternalTrigConvEdge >> ADC_CFGR_EXTEN_Pos)  & 0x3UL));
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
  /* CIRCULAR: для TIM2-DRIVEN режима */
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
  * @brief SPI1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_SPI1_Init(void)
{

  /* USER CODE BEGIN SPI1_Init 0 */
  /* USER CODE END SPI1_Init 0 */

  /* USER CODE BEGIN SPI1_Init 1 */
  /* USER CODE END SPI1_Init 1 */
  /* SPI1 (MCP4261): PB3=SCK, PB4=MISO, PB5=MOSI, PA15=NSS(GPIO), AF5 */
  hspi1.Instance = SPI1;
  hspi1.Init.Mode = SPI_MODE_MASTER;
  hspi1.Init.Direction = SPI_DIRECTION_2LINES;
  hspi1.Init.DataSize = SPI_DATASIZE_8BIT;
  hspi1.Init.CLKPolarity = SPI_POLARITY_LOW;
  hspi1.Init.CLKPhase = SPI_PHASE_1EDGE;
  hspi1.Init.NSS = SPI_NSS_SOFT;
  hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_16;
  hspi1.Init.FirstBit = SPI_FIRSTBIT_MSB;
  hspi1.Init.TIMode = SPI_TIMODE_DISABLE;
  hspi1.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
  hspi1.Init.CRCPolynomial = 0x0;
  hspi1.Init.NSSPMode = SPI_NSS_PULSE_ENABLE;
  hspi1.Init.NSSPolarity = SPI_NSS_POLARITY_LOW;
  hspi1.Init.FifoThreshold = SPI_FIFO_THRESHOLD_01DATA;
  hspi1.Init.TxCRCInitializationPattern = SPI_CRC_INITIALIZATION_ALL_ZERO_PATTERN;
  hspi1.Init.RxCRCInitializationPattern = SPI_CRC_INITIALIZATION_ALL_ZERO_PATTERN;
  hspi1.Init.MasterSSIdleness = SPI_MASTER_SS_IDLENESS_00CYCLE;
  hspi1.Init.MasterInterDataIdleness = SPI_MASTER_INTERDATA_IDLENESS_00CYCLE;
  hspi1.Init.MasterReceiverAutoSusp = SPI_MASTER_RX_AUTOSUSP_DISABLE;
  hspi1.Init.MasterKeepIOState = SPI_MASTER_KEEP_IO_STATE_DISABLE;
  hspi1.Init.IOSwap = SPI_IO_SWAP_DISABLE;
  if (HAL_SPI_Init(&hspi1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN SPI1_Init 2 */
  /* USER CODE END SPI1_Init 2 */

}

/**
  * @brief SPI3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_SPI3_Init(void)
{

  /* USER CODE BEGIN SPI3_Init 0 */
  /* USER CODE END SPI3_Init 0 */

  /* USER CODE BEGIN SPI3_Init 1 */
  /* USER CODE END SPI3_Init 1 */
  /* SPI3 parameter configuration*/
  hspi3.Instance = SPI3;
  hspi3.Init.Mode = SPI_MODE_MASTER;
  hspi3.Init.Direction = SPI_DIRECTION_1LINE;
  hspi3.Init.DataSize = SPI_DATASIZE_8BIT;
  hspi3.Init.CLKPolarity = SPI_POLARITY_LOW;
  hspi3.Init.CLKPhase = SPI_PHASE_1EDGE;
  hspi3.Init.NSS = SPI_NSS_SOFT;
  hspi3.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_32;
  hspi3.Init.FirstBit = SPI_FIRSTBIT_MSB;
  hspi3.Init.TIMode = SPI_TIMODE_DISABLE;
  hspi3.Init.CRCCalculation = SPI_CRCCALCULATION_DISABLE;
  hspi3.Init.CRCPolynomial = 0x0;
  hspi3.Init.NSSPMode = SPI_NSS_PULSE_DISABLE;
  hspi3.Init.NSSPolarity = SPI_NSS_POLARITY_LOW;
  hspi3.Init.FifoThreshold = SPI_FIFO_THRESHOLD_01DATA;
  hspi3.Init.TxCRCInitializationPattern = SPI_CRC_INITIALIZATION_ALL_ZERO_PATTERN;
  hspi3.Init.RxCRCInitializationPattern = SPI_CRC_INITIALIZATION_ALL_ZERO_PATTERN;
  hspi3.Init.MasterSSIdleness = SPI_MASTER_SS_IDLENESS_00CYCLE;
  hspi3.Init.MasterInterDataIdleness = SPI_MASTER_INTERDATA_IDLENESS_00CYCLE;
  hspi3.Init.MasterReceiverAutoSusp = SPI_MASTER_RX_AUTOSUSP_DISABLE;
  hspi3.Init.MasterKeepIOState = SPI_MASTER_KEEP_IO_STATE_DISABLE;
  hspi3.Init.IOSwap = SPI_IO_SWAP_DISABLE;
  if (HAL_SPI_Init(&hspi3) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN SPI3_Init 2 */
  /* USER CODE END SPI3_Init 2 */

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
  hspi4.Init.Direction = SPI_DIRECTION_1LINE;
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

  TIM_SlaveConfigTypeDef sSlaveConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM1_Init 1 */
  /* USER CODE END TIM1_Init 1 */
  htim1.Instance = TIM1;
  {
    uint32_t total_ticks = (optic_tim1_get_input_clk_hz() + (OPTIC_TX_PWM_TARGET_HZ / 2u)) / OPTIC_TX_PWM_TARGET_HZ;
    if (total_ticks < 2u) {
      total_ticks = 2u;
    }
    htim1.Init.Prescaler = 0;
    htim1.Init.Period = total_ticks - 1u;
  }
  htim1.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim1.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  /* RCR=0: TIM1 работает непрерывно, gate управляется аппаратно от TIM3 OC1REF. */
  htim1.Init.RepetitionCounter = 0;
  htim1.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_PWM_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }
  /* GATED mode: TIM1 считает только пока TIM3 OC1REF = HIGH (40 тиков несущей из 48).
     TIM3 работает от внутреннего такта с PSC=carrier_ticks-1, ARR=47, CCR1=40. */
  sSlaveConfig.SlaveMode = TIM_SLAVEMODE_GATED;
  sSlaveConfig.InputTrigger = TIM_TS_ITR2;
  if (HAL_TIM_SlaveConfigSynchro(&htim1, &sSlaveConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterOutputTrigger2 = TIM_TRGO2_UPDATE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_ENABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim1, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* CH3 (PA10): несущая 38 кГц, скважность ~50%. Активна пока TIM3 OC1REF=HIGH (gate). */
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = (htim1.Init.Period + 1u) / 2u;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  /* CH2 (PE10, подсветка) — Pulse=0 (управляется отдельно) */
  sConfigOC.Pulse = 0;
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = (htim1.Init.Period + 1u) / 2u;
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_3) != HAL_OK)
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
  printf("[OPTIC] TIM1 initialized: ARR=%lu, PSC=%lu -> %lu Hz on CH3\r\n",
         (unsigned long)htim1.Init.Period,
         (unsigned long)htim1.Init.Prescaler,
         (unsigned long)(optic_tim1_get_input_clk_hz() / (htim1.Init.Period + 1u)));
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
  /* TRGO = OC3REF: CH3 меандр 50% даёт 2 фронта за период → события для even и odd.
     При периоде TIM2=10мс (удвоен в tim2_apply_profile_window): CH3=200 Гц, события=400 Гц.
     Полупериод HIGH = чётные буферы, LOW = нечётные буферы. */
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_OC3REF;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim2, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
    /* Pulse = 4800 µs (из 5000 µs периода) → ADC работает ≈4.8ms, останавливается ~0.2ms.
      1200 samples @ ~275kHz ≈ 4.36ms — теперь точно помещаются без усечения. */
    sConfigOC.Pulse = 4800;
    /* TIM2_CH1 (PA0): физически инверсный выход, поэтому яркость в коде считаем отдельно,
      а в CCR1 подаём уже инвертированное значение. */
    sConfigOC.OCPolarity = TIM_OCPOLARITY_LOW;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  if (HAL_TIM_PWM_ConfigChannel(&htim2, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  /* TIM2_CH2 (PA1): маркер синхронизации (меандр как CH3)
     - Используем PWM1 режим для генерации меандра
     - Полярность HIGH для нормального выхода
  */
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  if (HAL_TIM_PWM_ConfigChannel(&htim2, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  /* TIM2_CH3 (PA2) отключён: PA2 используется как GPIO для DMA done */
  /* USER CODE BEGIN TIM2_Init 2 */
  // Разрешаем прерывания TIM2
  HAL_NVIC_SetPriority(TIM2_IRQn, 1, 0); // TIM2 gate interrupt just below DMA
  HAL_NVIC_EnableIRQ(TIM2_IRQn);
  // Включаем MOE для PWM выходов
  __HAL_TIM_MOE_ENABLE(&htim2);
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
  /* carrier_ticks = CLK / 38kHz ≈ 275MHz / 38000 ≈ 7237.
     PSC = carrier_ticks - 1: TIM3 тикает 1 раз за каждый период 38 кГц.
     ARR = 47 (= 48 - 1): счётчик 0..47 = 48 «тиков несущей» на цикл.
     CCR1 = 40: OC1REF HIGH пока cnt < 40 (PWM1) → gate для TIM1.
     Нет deadlock: TIM3 от внутреннего такта, TIM1 GATED от TIM3 OC1REF. */
  uint32_t carrier_ticks = (optic_tim1_get_input_clk_hz() + (OPTIC_TX_PWM_TARGET_HZ / 2u)) / OPTIC_TX_PWM_TARGET_HZ;
  if (carrier_ticks < 2u) { carrier_ticks = 2u; }

  /* USER CODE BEGIN TIM3_Init 1 */
  /* USER CODE END TIM3_Init 1 */
  htim3.Instance = TIM3;
  /* PSC = carrier_ticks - 1: каждый тик TIM3 = один период несущей 38 кГц */
  htim3.Init.Prescaler = (uint32_t)(carrier_ticks - 1u);
  htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
  /* ARR = BURST_PERIOD - 1 = 47: цикл из 48 «тиков несущей» (40 ON + 8 OFF) */
  htim3.Init.Period = OPTIC_TX_BURST_PERIOD - 1u;
  htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  /* ВАЖНО: HAL_TIM_PWM_MspInit не имеет обработчика для TIM3 → тактирование не включается.
     Включаем тактирование TIM3 явно перед HAL_TIM_PWM_Init. */
  __HAL_RCC_TIM3_CLK_ENABLE();
  if (HAL_TIM_PWM_Init(&htim3) != HAL_OK)
  {
    Error_Handler();
  }
  /* Внутренний такт (без slave): нет deadlock с TIM1 */
  sSlaveConfig.SlaveMode = TIM_SLAVEMODE_DISABLE;
  sSlaveConfig.InputTrigger = TIM_TS_ITR0;
  if (HAL_TIM_SlaveConfigSynchro(&htim3, &sSlaveConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* TRGO=OC1REF → gate signal для TIM1 GATED slave (ITR2) */
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_OC1REF;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_ENABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim3, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* CH1: PWM1, CCR1=40 → OC1REF HIGH пока cnt<40, LOW пока cnt=40..47 */
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = OPTIC_TX_BURST_ON_CYCLES;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  if (HAL_TIM_PWM_ConfigChannel(&htim3, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  /* ВАЖНО: HAL_TIM_PWM_Init генерирует UEV ДО записи CCR1, поэтому
     CCR1_shadow=0 при старте → OC1REF=(0<0)=LOW → gate TIM1 закрыт.
     Программный UEV загружает CCR1_shadow=40 немедленно. */
  TIM3->EGR = TIM_EGR_UG;
  TIM3->SR = ~TIM_SR_UIF;  /* Сбросить флаг UIF (прерывание не используется) */
  printf("[OPTIC] TIM3 init: PSC=%lu ARR=%lu CCR1=%lu CR2=0x%lX SMCR=0x%lX CCMR1=0x%lX\r\n",
         (unsigned long)TIM3->PSC, (unsigned long)TIM3->ARR, (unsigned long)TIM3->CCR1,
         (unsigned long)TIM3->CR2, (unsigned long)TIM3->SMCR, (unsigned long)TIM3->CCMR1);
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

  /* USER CODE BEGIN TIM15_Init 1 */
  /* USER CODE END TIM15_Init 1 */
  htim15.Instance = TIM15;
  htim15.Init.Prescaler = 0;     // Без предделителя: 275 MHz тактовая
  htim15.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim15.Init.Period = 4295;     // 275 MHz / (4295+1) ≈ 64 kHz UPDATE (примерно 200Hz PWM с делением)
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
  if (HAL_TIM_OC_Init(&htim15) != HAL_OK)
  {
    Error_Handler();
  }
  /* TIM15 работает в свободном режиме (Free-Running), генерируя TRGO UPDATE @ 240kHz.
     Slave Mode ОТКЛЮЧЁН чтобы избежать разрушения ADC/DMA синхронизации.
     Фазовая коррекция выполняется программно через минимальные шаги ARR. */
  sSlaveConfig.SlaveMode = TIM_SLAVEMODE_DISABLE;  // Свободный бег, без аппаратного сброса
  sSlaveConfig.InputTrigger = TIM_TS_ITR1;  // Параметр игнорируется когда SlaveMode = DISABLE
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
  sConfigOC.OCMode = TIM_OCMODE_TOGGLE;
  sConfigOC.Pulse = (htim15.Init.Period + 1u) / 2u;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_OC_ConfigChannel(&htim15, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM15_Init 2 */
  /* USER CODE END TIM15_Init 2 */

  HAL_TIM_MspPostInit(&htim15);

}

/**
  * @brief TIM16 Initialization Function
  * @param None
  * @retval None
  */
/**
  * @brief TIM5 Initialization Function (32-bit timer for phase counter)
  * @param None
  * @retval None
  */
static void MX_TIM5_Init(void)
{

  /* USER CODE BEGIN TIM5_Init 0 */
  /* USER CODE END TIM5_Init 0 */

  /* USER CODE BEGIN TIM5_Init 1 */
  /* USER CODE END TIM5_Init 1 */
  htim5.Instance = TIM5;
  /* TIM5 используется как 32-битный счётчик фазы (частота выставляется в adc_stream_apply_timing) */
  htim5.Init.Prescaler = 0;
  htim5.Init.Period = 0xFFFFFFFF;  /* 32-bit maximum */
  htim5.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim5.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim5.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim5) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM5_Init 2 */
  /* USER CODE END TIM5_Init 2 */

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
  // Запускаем прерывания приёма одиночных байтов для индикации LED
  if (HAL_UART_Receive_IT(&huart1, &uart1_rx_byte, 1) != HAL_OK) {
    printf("[UART1][ERR] HAL_UART_Receive_IT failed\r\n");
  } else {
    printf("[UART1] RX interrupt armed\r\n");
  }
  /* USER CODE END USART1_Init 2 */

}

/**
  * @brief USART2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART2_UART_Init(void)
{

  huart2.Instance = USART2;
  huart2.Init.BaudRate = 240000;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  huart2.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart2.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  huart2.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetTxFifoThreshold(&huart2, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetRxFifoThreshold(&huart2, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_EnableFifoMode(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  rs485_dma_rx_start();
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
  HAL_NVIC_SetPriority(DMA1_Stream0_IRQn, 0, 0); // ADC1 DMA = highest priority
  HAL_NVIC_EnableIRQ(DMA1_Stream0_IRQn);
  /* Enable DMA1_Stream1 IRQ for ADC2 DMA completion (used to re-arm oneshot) */
  HAL_NVIC_SetPriority(DMA1_Stream1_IRQn, 0, 1); // ADC2 DMA just below ADC1
  HAL_NVIC_EnableIRQ(DMA1_Stream1_IRQn);
  /* Низкоприоритетный IRQ для USART2 RX DMA (ошибки/служебные события) */
  HAL_NVIC_SetPriority(DMA1_Stream2_IRQn, 8, 0);
  HAL_NVIC_EnableIRQ(DMA1_Stream2_IRQn);
  /* Enable DMA1_Stream5 IRQ for TIM15 ARR auto-update (Half Transfer callback) */
  HAL_NVIC_SetPriority(DMA1_Stream5_IRQn, 2, 0);
  HAL_NVIC_EnableIRQ(DMA1_Stream5_IRQn);
  /* Low-priority DMA for SPI3 WS2812 output */
  HAL_NVIC_SetPriority(DMA1_Stream7_IRQn, 12, 0);
  HAL_NVIC_EnableIRQ(DMA1_Stream7_IRQn);

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

  /* USER CODE BEGIN MX_GPIO_Init_PC13 */
  /* Configure PC13 as USER BUTTON Input */
  // Active High (Press=1) means we need PULLDOWN to keep it 0 when released.
  GPIO_InitStruct.Pin = GPIO_PIN_13;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLDOWN; 
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);
  /* USER CODE END MX_GPIO_Init_PC13 */

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

  /*Configure GPIO pin : PA1 */
  GPIO_InitStruct.Pin = GPIO_PIN_1;
    /* PA1 инверсен PA2: при импульсах на PA2 -> PA1=0, иначе PA1=1.
      Начальное состояние при PA2=0: PA1=1. */
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1, GPIO_PIN_SET);
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /*Configure GPIO pin : PA2 (DMA done indicator) */
  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_2, GPIO_PIN_RESET);
  GPIO_InitStruct.Pin = GPIO_PIN_2;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
  
  /* USER CODE BEGIN MX_GPIO_Init_2 */
  /* PA3: legacy выход, удерживаем в LOW */
  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_3, GPIO_PIN_RESET);
  GPIO_InitStruct.Pin = GPIO_PIN_3;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /* PC7: зеркало битового маркера PA2 */
  HAL_GPIO_WritePin(GPIOC, GPIO_PIN_7, GPIO_PIN_RESET);
  GPIO_InitStruct.Pin = GPIO_PIN_7;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  /* PD0: вход фотоприёмника с внутренней подтяжкой вниз, ловит любое изменение уровня */
  GPIO_InitStruct.Pin = OPTIC_RX_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING_FALLING;
  GPIO_InitStruct.Pull = GPIO_PULLDOWN;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(OPTIC_RX_GPIO_Port, &GPIO_InitStruct);
  HAL_NVIC_SetPriority(EXTI0_IRQn, 5, 0);
  HAL_NVIC_EnableIRQ(EXTI0_IRQn);

  /* PD3: RS-485 RDE, по умолчанию приём */
  HAL_GPIO_WritePin(RS485_RDE_GPIO_Port, RS485_RDE_Pin, GPIO_PIN_RESET);
  GPIO_InitStruct.Pin = RS485_RDE_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(RS485_RDE_GPIO_Port, &GPIO_InitStruct);

  /* PE2: принудительно LOW, отдельный legacy sync output больше не используется */
  HAL_GPIO_WritePin(GPIOE, GPIO_PIN_2, GPIO_PIN_RESET);
  GPIO_InitStruct.Pin = GPIO_PIN_2;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(GPIOE, &GPIO_InitStruct);

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

  // Светодиод выключен по умолчанию (индицирует только прием команд UART)
  HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_RESET);
  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
// Заглушки и помощники для индикации
static void UpdateUSBDebug(void) { /* no-op */ }
static const char* usb_state_str(uint8_t s) { (void)s; return ""; }

// Мигание светодиодом и сторож
static volatile uint32_t tim2_irq_counter = 0; /* диагностика TIM2 IRQ */

/* TIM Period Elapsed callback - обрабатывает Update события для TIM2 и TIM6 */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  // ДИАГНОСТИКА: логируем ПЕРВЫЙ вызов TIM2 callback
  static uint8_t first_tim2_logged = 0;
  if (!first_tim2_logged && htim->Instance == TIM2) {
    printf("[TIM2][CB] FIRST PeriodElapsed callback! CNT=%lu ARR=%lu\r\n",
           (unsigned long)__HAL_TIM_GET_COUNTER(htim),
           (unsigned long)htim->Instance->ARR);
    first_tim2_logged = 1;
  }
  
  if (htim->Instance == TIM2) {
    /* TIM2 больше НЕ управляет DMA/переключением буферов.
       TIM2 используется как стабильный генератор/маркер (например, TIM2_CH3 на PA2). */
    tim2_irq_counter++;  /* может использоваться для LCD/диагностики */
  }
  else if (htim->Instance == TIM6) {
    // TIM6: периодический тик для watchdog и USB vendor task
    tim6_irq_count++;
#ifdef HAL_IWDG_MODULE_ENABLED
    #ifndef DIAG_DISABLE_IWDG
      HAL_IWDG_Refresh(&hiwdg1);
    #endif
#endif
    #if !SAFE_MINIMAL
      extern void usb_vendor_periodic_tick(void);
      usb_vendor_periodic_tick();
    #endif
  }
  else if (htim->Instance == TIM15) {
    if (tim15_arr_pulse_stage != 0u) {
      tim15_arr_pulse_stage--;
      if (tim15_arr_pulse_stage == 0u) {
        arr_auto_apply_tim15(tim15_arr_pulse_nominal);
      }
    }
    if (tim15_arr_pulse_stage == 0u) {
      __HAL_TIM_DISABLE_IT(&htim15, TIM_IT_UPDATE);
    }
    tim15_apply_hold_target_if_possible();
    __HAL_TIM_CLEAR_FLAG(&htim15, TIM_FLAG_UPDATE);
  }
}

/* Старый PWM Pulse Finished callback - не используется */
void HAL_TIM_PWM_PulseFinishedCallback(TIM_HandleTypeDef *htim)
{
  if (!htim) return;
}

// Callback по завершении приёма байта (USART1 RX interrupt)
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if(huart->Instance == USART1){
    uart1_rx_count++;
    uart1_last_rx_ms = HAL_GetTick();
    // Сохраняем в кольцевой буфер (без сложного парсинга)
    uart1_rx_ring[uart1_rx_ring_wr & (UART1_RX_RING_SZ-1)] = uart1_rx_byte;
    uart1_rx_ring_wr++;
    // Импульс LED
    uart1_rx_led_pulse();
    // Эхо обратно в UART1, чтобы пользователь видел, что принято
    if(uart1_rx_byte == '\r'){
      uart1_raw_putc('\r');
      uart1_raw_putc('\n');
    } else {
      uart1_raw_putc((char)uart1_rx_byte);
    }
    // Переустанавливаем приём следующего байта
    if(HAL_UART_Receive_IT(&huart1, &uart1_rx_byte, 1) != HAL_OK){
      // Если ошибка – попробуем восстановить через краткую задержку
      HAL_Delay(1);
      HAL_UART_Receive_IT(&huart1, &uart1_rx_byte, 1);
    }
  }
}

void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART2) {
    uint8_t completed_byte = rs485_tx_byte;
    rs485_tx_packets++;
    rs485_tx_busy = 0u;
    if (rs485_is_sync_byte(completed_byte) == 0u) {
      rs485_status_local_tx_complete_count++;
    }
    if ((rs485_status_window_after_tx != 0u) &&
        (rs485_is_sync_byte(completed_byte) != 0u)) {
      rs485_status_window_after_tx = 0u;
      CLEAR_BIT(huart->Instance->CR1, USART_CR1_TCIE);
      HAL_GPIO_WritePin(RS485_RDE_GPIO_Port, RS485_RDE_Pin, GPIO_PIN_RESET);
      rs485_status_begin_window();
      return;
    }
    if (rs485_tx_queue_count != 0u) {
      rs485_tx_kick();
      return;
    }
    CLEAR_BIT(huart->Instance->CR1, USART_CR1_TCIE);
    HAL_GPIO_WritePin(RS485_RDE_GPIO_Port, RS485_RDE_Pin, GPIO_PIN_RESET);
  }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART2) {
    rs485_uart_error_count++;
    rs485_tx_busy = 0u;
    rs485_tx_start_ms = 0u;
    CLEAR_BIT(huart->Instance->CR1, USART_CR1_TCIE);
    HAL_GPIO_WritePin(RS485_RDE_GPIO_Port, RS485_RDE_Pin, GPIO_PIN_RESET);
    huart->Instance->ICR = USART_ICR_PECF |
                           USART_ICR_FECF |
                           USART_ICR_NECF |
                           USART_ICR_ORECF |
                           USART_ICR_TCCF;
    huart->ErrorCode = HAL_UART_ERROR_NONE;
    huart->RxState = HAL_UART_STATE_READY;
#if RS485_USART2_USE_DMA_RX
    rs485_dma_rx_start();
#else
    while ((huart2.Instance->ISR & USART_ISR_RXNE_RXFNE) != 0u) {
      (void)huart2.Instance->RDR;
    }
    huart2.Instance->CR1 |= USART_CR1_RXNEIE_RXFNEIE;
    huart2.Instance->CR3 |= USART_CR3_EIE;
#endif
  }
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
  if (GPIO_Pin == OPTIC_RX_Pin) {
    uint32_t now_ms = HAL_GetTick();

    optic_last_change_ms = now_ms;
    optic_sensor_state_public = 1u;
    need_usb_status_refresh = 1u;
  }
}

static void lcd_print_padded_if_changed(int x, int y, const char* new_text,
                    char *prev, size_t buf_sz,
                    uint8_t max_len, uint8_t font_height,
                    uint16_t fg, uint16_t bg,
                    uint16_t *prev_fg, uint16_t *prev_bg)
{
    if(!new_text || !prev || buf_sz == 0) return;
  if(strncmp(new_text, prev, buf_sz-1) == 0){
    if(prev_fg && prev_bg && (*prev_fg == fg) && (*prev_bg == bg)) return;
  }
    char line[32];
    size_t n = strlen(new_text);
    if(n > max_len) n = max_len;
    memcpy(line, new_text, n);
    while(n < max_len) line[n++] = ' ';
    line[n] = 0;
    LCD_ShowString_Size((uint16_t)x, (uint16_t)y, line, font_height, fg, bg);
    strncpy(prev, new_text, buf_sz-1);
    prev[buf_sz-1] = 0;
  if(prev_fg) *prev_fg = fg;
  if(prev_bg) *prev_bg = bg;
}

static void lcd_draw_badge_if_changed(int x, int y, const char* new_text,
                                      char *prev, size_t buf_sz,
                                      uint8_t max_len, uint8_t font_height,
                                      uint8_t pad_x, uint8_t pad_y,
                                      uint16_t fg, uint16_t bg,
                                      uint16_t *prev_fg, uint16_t *prev_bg)
{
  uint8_t char_width;
  uint16_t badge_w;
  uint16_t badge_h;
  size_t text_len;
  char text[32];

  if(!new_text || !prev || buf_sz == 0) return;
  if(strncmp(new_text, prev, buf_sz-1) == 0){
    if(prev_fg && prev_bg && (*prev_fg == fg) && (*prev_bg == bg)) return;
  }

  char_width = (font_height == 12u) ? 6u : 8u;
  badge_w = (uint16_t)(max_len * char_width + (2u * pad_x));
  badge_h = (uint16_t)(font_height + (2u * pad_y));
  text_len = strlen(new_text);
  if(text_len > max_len) text_len = max_len;
  memcpy(text, new_text, text_len);
  text[text_len] = 0;

  LCD_FillRect((uint16_t)x, (uint16_t)y, badge_w, badge_h, bg);
  LCD_ShowString_Size((uint16_t)(x + pad_x), (uint16_t)(y + pad_y), text, font_height, fg, bg);

  strncpy(prev, new_text, buf_sz-1);
  prev[buf_sz-1] = 0;
  if(prev_fg) *prev_fg = fg;
  if(prev_bg) *prev_bg = bg;
}

static uint8_t vnd_lcd_sync_color_to_id(uint16_t color)
{
  switch (color) {
    case RED:    return VND_LCD_SYNC_COLOR_RED;
    case GREEN:  return VND_LCD_SYNC_COLOR_GREEN;
    case YELLOW: return VND_LCD_SYNC_COLOR_YELLOW;
    case BLUE:   return VND_LCD_SYNC_COLOR_BLUE;
    case CYAN:   return VND_LCD_SYNC_COLOR_CYAN;
    case WHITE:  return VND_LCD_SYNC_COLOR_WHITE;
    default:     return VND_LCD_SYNC_COLOR_BLACK;
  }
}

void vnd_get_lcd_sync_snapshot(vnd_lcd_sync_snapshot_t *out)
{
  static uint32_t last_eval_ms = 0xFFFFFFFFu;
  static uint8_t sync_color_locked = 0u;
  static uint8_t sync_color_rise_count = 0u;
  static uint8_t sync_color_fall_count = 0u;
  static uint8_t prev_logic_mode = 0xFFu;
  static vnd_lcd_sync_snapshot_t cached;
  uint32_t now;
  uint32_t sync_age_ms = 0xFFFFFFFFu;
  uint8_t display_mode;
  uint8_t display_value = 0u;
  uint8_t sync_signal_alive = 0u;
  uint8_t sync_ok_visual = 0u;
  uint16_t color = RED;
  char display_char = 'M';

  if (out == NULL) {
    return;
  }

  now = HAL_GetTick();
  if (last_eval_ms == now) {
    *out = cached;
    return;
  }

  display_mode = vnd_sync_mode_public;
  if (sync_last_edge_ms != 0u) {
    sync_age_ms = now - sync_last_edge_ms;
    if (sync_age_ms <= RS485_SYNC_PRESENT_MS) {
      sync_signal_alive = 1u;
    }
  }
  if ((display_mode == VND_SYNC_MODE_MASTER) && (rs485_slave_count_estimate != 0u)) {
    sync_signal_alive = 1u;
  }

  if (!sync_signal_alive) {
    display_mode = VND_SYNC_MODE_MASTER;
    display_value = 0u;
    sync_color_locked = 0u;
    sync_color_rise_count = 0u;
    sync_color_fall_count = 0u;
    color = CYAN;
  }

  if ((prev_logic_mode != display_mode) && sync_signal_alive) {
    prev_logic_mode = display_mode;
    sync_color_locked = 0u;
    sync_color_rise_count = 0u;
    sync_color_fall_count = 0u;
  } else if (!sync_signal_alive) {
    prev_logic_mode = display_mode;
  }

  if (display_mode == VND_SYNC_MODE_SLAVE) {
    display_char = 'S';
  } else if (display_mode == VND_SYNC_MODE_OFF) {
    display_char = 'O';
  }

  if (display_mode == VND_SYNC_MODE_MASTER) {
    display_value = (uint8_t)(rs485_slave_count_estimate & 0x1Fu);
    if (!sync_signal_alive) {
      display_value = 0u;
    }
  } else if (display_mode == VND_SYNC_MODE_SLAVE) {
    display_value = (uint8_t)(rs485_local_node_id & 0x1Fu);
  }

  if (sync_signal_alive) {
    sync_ok_visual = vnd_sync_ok_public;
    if ((display_mode == VND_SYNC_MODE_MASTER) && (rs485_slave_count_estimate != 0u)) {
      sync_ok_visual = 1u;
    }
    if (sync_ok_visual != 0u) {
      sync_color_fall_count = 0u;
      if (sync_color_rise_count < 8u) {
        sync_color_rise_count++;
      }
      if (sync_color_rise_count >= 8u) {
        sync_color_locked = 1u;
      }
    } else {
      sync_color_rise_count = 0u;
      if (sync_color_locked != 0u) {
        if (sync_color_fall_count < 40u) {
          sync_color_fall_count++;
        }
        if (sync_color_fall_count >= 40u) {
          sync_color_locked = 0u;
        }
      }
    }
    color = (sync_color_locked != 0u) ? GREEN : RED;
  }

  memset(&cached, 0, sizeof(cached));
  cached.raw_mode = vnd_sync_mode_public;
  cached.display_mode = display_mode;
  cached.display_value = display_value;
  cached.slave_count = (uint8_t)(rs485_slave_count_estimate & 0x1Fu);
  cached.node_id = (uint8_t)(rs485_local_node_id & 0x1Fu);
  cached.display_char = (uint8_t)display_char;
  cached.display_color_id = vnd_lcd_sync_color_to_id(color);
  cached.sync_signal_alive = sync_signal_alive;
  cached.sync_ok_visual = sync_ok_visual;
  cached.sync_color_locked = sync_color_locked;
  cached.display_rgb565 = color;
  cached.sync_age_ms = sync_age_ms;
  last_eval_ms = now;

  *out = cached;
}

void UpdateLCDStatus(void){ need_usb_status_refresh = 1; }

void DrawStarIndicator(void){
  PROG('S');
#if DIAG_SKIP_LCD
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
#if DIAG_SKIP_LCD
  PROG('u');
  return;
#endif
    if(!lcd_ready) {
    // LCD not ready
        return;
    }
    /* Heartbeat основного цикла: '*' мигает ~1 Гц в правом конце первой строки. */
    {
      static uint32_t last_star_toggle_ms = 0;
      static uint8_t star_on = 0;
      static uint8_t star_prev = 0xFF;
      uint32_t now_ms = HAL_GetTick();
      if(last_star_toggle_ms == 0u) last_star_toggle_ms = now_ms;
      if((now_ms - last_star_toggle_ms) >= 500u){ /* 0.5с on / 0.5с off */
        last_star_toggle_ms = now_ms;
        star_on = (uint8_t)!star_on;
      }
      if(star_prev != star_on){
        LCD_ShowString_Size(151, 0, star_on?"*":" ", 16, YELLOW, BLACK);
        star_prev = star_on;
      }
    }
    static char prev_line0[16] = "";
    static char prev_line1[16] = "";
  static char prev_line2[16] = "";
    static char prev_line3[16] = "";
    static char prev_line4[24] = "";
  static uint16_t dc_bar_prev_len = 0xFFFF;
  static uint16_t dc_bar_prev_color = 0xFFFF;
  static uint64_t prev_tx_bytes = 0ULL;
  static uint64_t prev_tx_samples = 0ULL;
  static uint32_t prev_rate_calc_ms = 0;
  static uint32_t last_rate_bps __attribute__((unused)) = 0; /* приблизительно bytes/sec */
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
    static uint16_t prev_line0_fg = 0, prev_line0_bg = 0;
    static uint16_t prev_line1_fg = 0, prev_line1_bg = 0;
    static uint16_t prev_line2_fg = 0, prev_line2_bg = 0;
    static uint16_t prev_line3_fg = 0, prev_line3_bg = 0;
    static uint16_t prev_line4_fg = 0, prev_line4_bg = 0;
    static uint16_t prev_optic_fg = 0, prev_optic_bg = 0;
    static uint16_t prev_optic_power_fg = 0, prev_optic_power_bg = 0;
    static char prev_optic_line[8] = "";
    static char prev_optic_power_line[8] = "";
    
    /* Первая строка (y=0): USB статус */
    lcd_print_padded_if_changed(0,0,text0, prev_line0, sizeof(prev_line0), 7, 16, color0, BLACK, &prev_line0_fg, &prev_line0_bg);

    /* Индикатор SYNC рядом со звездочкой: 2 цифры slave-count/slot + M/S/O.
       MASTER показывает число ответивших slave, SLAVE — занятый номер слота. */
    {
      static uint8_t prev_mode = 0xFF;
      static uint8_t prev_display_value = 0xFF;
      static uint16_t prev_color = 0xFFFFu;
      vnd_lcd_sync_snapshot_t sync_snapshot;
      char count_buf[3] = "  ";
      vnd_get_lcd_sync_snapshot(&sync_snapshot);

      if (sync_snapshot.display_mode != VND_SYNC_MODE_OFF) {
        count_buf[0] = (char)('0' + ((sync_snapshot.display_value / 10u) % 10u));
        count_buf[1] = (char)('0' + (sync_snapshot.display_value % 10u));
        count_buf[2] = '\0';
      }

      if(prev_mode != sync_snapshot.display_mode ||
         prev_display_value != sync_snapshot.display_value ||
         prev_color != sync_snapshot.display_rgb565){
        LCD_ShowString_Size(132, 0, count_buf, 16, sync_snapshot.display_rgb565, BLACK);
        char buf[2] = {(char)sync_snapshot.display_char, 0};
        LCD_ShowString_Size(124, 0, buf, 16, sync_snapshot.display_rgb565, BLACK);
        prev_mode = sync_snapshot.display_mode;
        prev_display_value = sync_snapshot.display_value;
        prev_color = sync_snapshot.display_rgb565;
      }
    }

  /* Строка 1 (y=14): частота маркера (PA1/PA2) */
  {
    uint16_t buf_rate = adc_stream_get_buf_rate();
    uint16_t marker_hz = buf_rate / 2;  // TIM2 делит buf_rate на 2 для получения меандра
    char freq_buf[20];
    snprintf(freq_buf, sizeof(freq_buf), "%u Hz", (unsigned)marker_hz);
    lcd_print_padded_if_changed(0,14, freq_buf, prev_line1, sizeof(prev_line1), 10, 16, CYAN, BLACK, &prev_line1_fg, &prev_line1_bg);
  }
  {
    uint8_t optic_active = optic_sensor_get_state();
    uint16_t optic_bg = optic_active ? GREEN : RED;
    lcd_draw_badge_if_changed(127,16, "Optic", prev_optic_line, sizeof(prev_optic_line), 5, 12,
                              1, 1, BLACK, optic_bg, &prev_optic_fg, &prev_optic_bg);
  }
  {
    char optic_power_buf[8];
    uint8_t optic_power = optic_tx_get_power();
    uint8_t optic_power_pct = optic_tx_power_to_percent(optic_power);
    uint16_t optic_power_bg = optic_tx_power_badge_color(optic_power);
    snprintf(optic_power_buf, sizeof(optic_power_buf), "%3u%%", (unsigned)optic_power_pct);
    lcd_draw_badge_if_changed(92,16, optic_power_buf, prev_optic_power_line, sizeof(prev_optic_power_line), 4, 12,
                              1, 1, BLACK, optic_power_bg, &prev_optic_power_fg, &prev_optic_power_bg);
  }

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
    /* Для диагностики показываем фактический уровень входа PD0 (OPTIC_RX). */
    char rate_buf[16];
    uint8_t pd0_level = (HAL_GPIO_ReadPin(OPTIC_RX_GPIO_Port, OPTIC_RX_Pin) == GPIO_PIN_SET) ? 1u : 0u;
    snprintf(rate_buf, sizeof(rate_buf), "PD0:%u", (unsigned)pd0_level);
  /* Очистка legacy VID/PID убрана */
    /* Используем ширину 12 символов для гарантированного затирания хвоста */
    lcd_print_padded_if_changed(0,28, rate_buf, prev_line2, sizeof(prev_line2), 12, 16, pd0_level ? GREEN : WHITE, BLACK, &prev_line2_fg, &prev_line2_bg);
  } else {
  /* Очистка legacy VID/PID убрана */
    {
      char pd0_buf[16];
      uint8_t pd0_level = (HAL_GPIO_ReadPin(OPTIC_RX_GPIO_Port, OPTIC_RX_Pin) == GPIO_PIN_SET) ? 1u : 0u;
      snprintf(pd0_buf, sizeof(pd0_buf), "PD0:%u", (unsigned)pd0_level);
      lcd_print_padded_if_changed(0,28, pd0_buf, prev_line2, sizeof(prev_line2), 12, 16, pd0_level ? GREEN : WHITE, BLACK, &prev_line2_fg, &prev_line2_bg);
    }
    prev_rate_calc_ms = now;
    prev_tx_bytes = vnd_get_total_tx_bytes();
    prev_tx_samples = vnd_get_total_tx_samples();
    last_rate_bps = 0;
    last_rate_sps = 0;
  }

  /* Третья строка: при активной тревоге показываем её, иначе оставляем фазу TIM5. */
  {
    char line3_buf[20];
    uint16_t line3_color = GREEN;

    if (diag_get_alarm_text(line3_buf, sizeof(line3_buf), &line3_color) == 0u) {
      snprintf(line3_buf, sizeof(line3_buf), "P:%ld", (long)g_tim5_avg_phase);

      {
        long abs_ph = (long)(g_tim5_avg_phase < 0 ? -g_tim5_avg_phase : g_tim5_avg_phase);
        if(abs_ph > 5000) line3_color = RED;
        else if(abs_ph > 1000) line3_color = YELLOW;
      }
    }

    lcd_print_padded_if_changed(0,42, line3_buf, prev_line3, sizeof(prev_line3), 16, 16, line3_color, BLACK, &prev_line3_fg, &prev_line3_bg);
  }

    /* Индикатор прогресса адаптации/сохранения DC: нижняя линия пикселей (y=79, 0..159).
      - Пока DC "dirty" (идёт адаптация, ожидаем запись) — линия заполняется слева направо.
      - Если последняя запись DC не удалась — рисуем красным; после успешной записи — зелёным.
      - Если адаптация заморожена (CMD_SET_DC_ADAPT freeze) или авто-заморозка по амплитуде — полоса становится синей и останавливается.
      Важно: используем только 1px высоту, чтобы не мешать тексту. */
  {
    const uint16_t y = 79;
    uint16_t color = BLACK;
    uint16_t filled = 0;

    uint8_t freeze = (uint8_t)((!vnd_dc_adapt_enabled || vnd_dc_auto_freeze) ? 1u : 0u);

    if(freeze){
      color = BLUE;
      filled = dc_bar_prev_len;
      if(filled > LCD_W) filled = LCD_W;
    } else if(vnd_dc_dirty_public){
      uint32_t period = vnd_dc_save_period_ms;
      if(period == 0u) period = 1u;
      uint32_t start = vnd_dc_dirty_since_ms;
      uint32_t elapsed = (start == 0u) ? 0u : (now - start);
      if(elapsed > period) elapsed = period;
      filled = (uint16_t)((elapsed * (uint32_t)LCD_W) / period);
      if(filled > LCD_W) filled = LCD_W;
      
      if(vnd_dc_save_last_result == 2u){
        color = RED;
      } else if(vnd_dc_save_last_result == 1u){
        color = GREEN;
      } else {
        color = WHITE;
      }
    } else {
      if(vnd_dc_save_last_result == 2u){
        filled = LCD_W;
        color = RED;
      } else if(vnd_dc_save_last_result == 1u){
        filled = LCD_W;
        color = GREEN;
      } else {
        filled = 0;
        color = BLACK;
      }
    }

    if(dc_bar_prev_len != filled || dc_bar_prev_color != color){
      LCD_FillRect(0, y, LCD_W, 1, BLACK);
      if(filled > 0u){
        LCD_FillRect(0, y, filled, 1, color);
      }
      dc_bar_prev_len = filled;
      dc_bar_prev_color = color;
    }
  }

  /* Пятая строка LCD: дата/время прошивки в формате Ver: dd.mm.yy hh:mm */
  {
    char ver_buf[24];
    unsigned year = 0u, month = 0u, day = 0u;
    unsigned hour = 0u, minute = 0u;

    if((sscanf(fw_build_date, "%4u-%2u-%2u", &year, &month, &day) == 3) &&
       (sscanf(fw_build_time, "%2u:%2u", &hour, &minute) == 2)){
      snprintf(ver_buf, sizeof(ver_buf), "Ver: %02u.%02u.%02u %02u:%02u",
               day, month, year % 100u, hour, minute);
    } else {
      snprintf(ver_buf, sizeof(ver_buf), "Ver: %.10s %.5s", fw_build_date, fw_build_time);
    }

    lcd_print_padded_if_changed(0,56, ver_buf, prev_line4, sizeof(prev_line4), 20, 16, WHITE, BLACK, &prev_line4_fg, &prev_line4_bg);
  }
  PROG('u');
}

// (реализации Error_Handler/MPU_Config/assert_failed оставлены в стандартных секциях ниже)
/* USER CODE END 4 */

 /* MPU Configuration */

static void MPU_Config(void) __attribute__((unused));
static void MPU_Config(void)
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

  /* DC persistence Flash sector: map as non-cacheable data region to avoid stale DCache reads.
     Address 0x080E0000..0x080FFFFF (128KB). Execute never. */
  MPU_InitStruct.Enable = MPU_REGION_ENABLE;
  MPU_InitStruct.Number = MPU_REGION_NUMBER1;
  MPU_InitStruct.BaseAddress = 0x080E0000;
  MPU_InitStruct.Size = MPU_REGION_SIZE_128KB;
  MPU_InitStruct.SubRegionDisable = 0x00;
  MPU_InitStruct.TypeExtField = MPU_TEX_LEVEL0;
  MPU_InitStruct.AccessPermission = MPU_REGION_FULL_ACCESS;
  MPU_InitStruct.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
  MPU_InitStruct.IsShareable = MPU_ACCESS_NOT_SHAREABLE;
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
  /* ДИАГНОСТИКА: попытка вывести сообщение об ошибке через UART перед зависанием */
  static volatile uint8_t error_logged = 0;
  if (!error_logged) {
    error_logged = 1;
    /* Простейший вывод через polling (IRQ могут быть отключены) */
    const char msg[] = "\r\n[ERROR_HANDLER] CRITICAL ERROR - System halted!\r\n";
    for (int i = 0; msg[i]; i++) {
      /* Ждём готовности UART (TXE_TXFNF flag в ISR для STM32H7) */
      while (!(USART3->ISR & USART_ISR_TXE_TXFNF)) {}
      USART3->TDR = msg[i];
    }
    /* Даём время на передачу последнего байта */
    for(volatile uint32_t d=0; d<1000000UL; ++d){ __NOP(); }
  }
  
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

