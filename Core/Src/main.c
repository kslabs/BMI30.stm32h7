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
#include "font.h"

#ifndef MAIN_COM_LOG_ENABLE
#define MAIN_COM_LOG_ENABLE 0
#endif
#if !MAIN_COM_LOG_ENABLE
#define printf(...) ((void)0)
#endif

#ifndef MAIN_LED_INDICATION_ENABLE
#define MAIN_LED_INDICATION_ENABLE 0
#endif

#ifndef MAIN_WS2812_STATUS_ENABLE
#define MAIN_WS2812_STATUS_ENABLE 1
#endif

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
extern volatile uint32_t sync_tim15_cnt_at_pd5;
extern volatile uint32_t sync_tim5_cnt_at_buffer;
extern volatile uint32_t sync_tim5_buffer_phase_seq;
#define SYNC_TARGET_PHASE_AUTO 0xFFFFFFFFu
#define SYNC_TARGET_PHASE_DEFAULT_TICKS 26500u
#define RS485_SYNC_TARGET_HALF_PERIOD_SHIFT 0u
#define RS485_SYNC_VISUAL_PHASE_TRACK 0u
#define RS485_SYNC_IN_PHASE_LOCAL_EDGE_XOR 1u
/* v85-style sync control: phase correction is allowed on every sync packet. */
#define RS485_SYNC_CONTROL_200HZ_ONLY 0u
#define RS485_SYNC_CONTROL_EDGE_KIND 0u
#define RS485_FREQ_TRIM_ENABLE_DEFAULT 0u
#define RS485_FREQ_TRIM_WINDOW_PACKETS 128u
#define RS485_FREQ_TRIM_EDGE_KIND_AUTO 0xFFu
#define RS485_FREQ_TRIM_SAMPLE_STRIDE_BUFFERS 2u
#define RS485_FREQ_TRIM_MIN_INTERVAL_BUFFERS 4u
#define RS485_FREQ_TRIM_MAX_INTERVAL_BUFFERS 512u
#define RS485_FREQ_TRIM_DRIFT_DEADBAND_TICKS 96u
#define RS485_FREQ_TRIM_OUTLIER_SAMPLES 512u
#define RS485_FREQ_TRIM_MAX_PHASE_BITS 512u
#define RS485_PHASE_HALF_SNAP_ENABLE_DEFAULT 0u
#define RS485_PHASE_HALF_SNAP_THRESHOLD_NUM 7u
#define RS485_PHASE_HALF_SNAP_THRESHOLD_DEN 8u
#define RS485_PHASE_HALF_SNAP_REARM_ENABLE 0u
#define RS485_PHASE_HALF_SNAP_REARM_NUM 1u
#define RS485_PHASE_HALF_SNAP_REARM_DEN 4u
#define RS485_PHASE_HALF_SNAP_COOLDOWN_MS 1500u
#define RS485_PHASE_APPROACH_ENABLE_DEFAULT 0u
#define RS485_PHASE_APPROACH_START_BITS 4u
#define RS485_PHASE_APPROACH_STOP_BITS 1u
#define RS485_PHASE_APPROACH_DELTA_DIV_BITS 16u
#define RS485_PHASE_APPROACH_MAX_DELTA 1
#define RS485_PHASE_APPROACH_FINE_CAP_BITS 64u
#define RS485_PHASE_APPROACH_MID_CAP_BITS 128u
#define RS485_PHASE_APPROACH_FORCE_POSITIVE_DELTA 0u
#define RS485_PHASE_APPROACH_ADAPT_DIRECTION 1u
#define RS485_PHASE_APPROACH_WORSE_BITS 2u
#define RS485_PHASE_APPROACH_WORSE_LIMIT 4u
#define RS485_PHASE_APPROACH_NEG_BOOST_MIN_BITS 8u
#define RS485_PHASE_APPROACH_NEG_BOOST_DELTA 2u
#define RS485_PHASE_APPROACH_NEG_SPACING_MAX 2u
#define RS485_PHASE_APPROACH_SPACING_FAR_BITS 128u
#define RS485_PHASE_APPROACH_SPACING_MID_BITS 64u
#define RS485_PHASE_APPROACH_SPACING_NEAR_BITS 24u
#define RS485_PHASE_APPROACH_SIGN_HOLDOFF_BITS 4u
#define RS485_PHASE_APPROACH_SIGN_HOLDOFF_EDGES 16u
static volatile uint32_t g_sync_target_phase_ticks = SYNC_TARGET_PHASE_DEFAULT_TICKS;
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
static volatile int32_t sync_phase_last_control_error_ticks = 0;
static volatile int32_t sync_phase_last_pulse_delta = 0;
static volatile uint8_t sync_phase_near_raw_mode = 0u;
static volatile uint32_t sync_phase_fast_edges = 0u;
static volatile uint32_t sync_phase_fast_pulses = 0u;
static volatile uint32_t sync_phase_fast_skip_busy = 0u;
static volatile uint32_t sync_phase_fast_skip_spacing = 0u;
static volatile uint32_t sync_phase_fast_last_spacing = 0u;
static volatile uint32_t sync_phase_fast_last_buf = 0xFFFFFFFFu;
static volatile uint8_t rs485_freq_trim_enabled = RS485_FREQ_TRIM_ENABLE_DEFAULT;
static volatile int8_t rs485_freq_trim_dir = 0;
static volatile uint8_t rs485_freq_trim_have_prev_phase = 0u;
static volatile int32_t rs485_freq_trim_prev_phase_ticks = 0;
static volatile int32_t rs485_freq_trim_window_delta_accum = 0;
static volatile uint32_t rs485_freq_trim_window_count = 0u;
static volatile uint32_t rs485_freq_trim_interval_buffers = RS485_FREQ_TRIM_MAX_INTERVAL_BUFFERS;
static volatile uint8_t rs485_freq_trim_edge_kind = RS485_FREQ_TRIM_EDGE_KIND_AUTO;
static volatile int32_t rs485_freq_trim_last_window_delta_ticks = 0;
static volatile int32_t rs485_freq_trim_last_avg_delta_ticks = 0;
static volatile uint32_t rs485_freq_trim_windows = 0u;
static volatile uint32_t rs485_freq_trim_nudge_count = 0u;
static volatile uint32_t rs485_freq_trim_skip_count = 0u;
static volatile uint8_t rs485_phase_half_snap_enabled = RS485_PHASE_HALF_SNAP_ENABLE_DEFAULT;
static volatile uint8_t rs485_phase_half_snap_armed = 1u;
static volatile uint8_t rs485_phase_half_snap_request = 0u;
static volatile uint32_t rs485_phase_half_snap_count = 0u;
static volatile int32_t rs485_phase_half_snap_last_error = 0;
static volatile uint32_t rs485_phase_half_snap_last_ms = 0u;
static volatile uint8_t rs485_phase_approach_enabled = RS485_PHASE_APPROACH_ENABLE_DEFAULT;
static volatile uint8_t rs485_phase_approach_active = 0u;
static volatile int32_t rs485_phase_approach_last_delta = 0;
static volatile uint32_t rs485_phase_approach_last_abs_ticks = 0u;
static volatile uint32_t rs485_phase_approach_last_spacing = 0u;
static volatile uint32_t rs485_phase_approach_nudge_count = 0u;
static volatile uint32_t rs485_phase_approach_skip_count = 0u;
static volatile uint32_t rs485_phase_approach_done_count = 0u;
static volatile int8_t rs485_phase_approach_prev_sign = 0;
static volatile uint16_t rs485_phase_approach_holdoff_edges = 0u;
static volatile int8_t rs485_phase_approach_dir = 1;
static volatile uint8_t rs485_phase_approach_have_prev_abs = 0u;
static volatile uint32_t rs485_phase_approach_prev_abs_ticks = 0u;
static volatile uint8_t rs485_phase_approach_worse_count = 0u;
static volatile uint8_t optic_sensor_state_public = 0u;
static volatile uint8_t optic_input_level_public = 0u;
static volatile uint32_t optic_last_high_ms = 0u;
static volatile uint16_t optic_active_hold_deciseconds = 30u;
static volatile uint8_t rs485_det_adc_bits = 0u;

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
static uint32_t rs485_sync_uart_next_log_edge = 0u;
static volatile uint32_t rs485_sync_control_edge_count = 0u;
static volatile uint32_t rs485_sync_control_period_accum_ticks = 0u;
static volatile uint32_t rs485_sync_control_period_ticks = 0u;
static volatile uint32_t rs485_sync_control_tim5_ticks = 0u;
static volatile uint32_t rs485_sync_control_buf_phase = 0u;
static volatile uint32_t rs485_sync_control_buf_seq = 0u;
static volatile uint32_t rs485_sync_control_tim15_cap = 0u;
static volatile uint8_t rs485_anti_phase_recovery_active = 0u;
static volatile uint8_t rs485_anti_phase_recovery_packets = 0u;
static volatile uint8_t rs485_anti_phase_recovery_request = 0u;
static volatile uint32_t rs485_phase_polarity_flip_count = 0u;
static volatile uint32_t rs485_phase_slew_pulse_count = 0u;
static volatile uint8_t rs485_phase_slew_active = 0u;
static volatile uint32_t rs485_phase_slew_remaining_ticks = 0u;
static volatile uint8_t rs485_sync_restart_request = 0u;
static volatile uint32_t rs485_sync_restart_count = 0u;
static volatile uint32_t rs485_sync_rejected_early_count = 0u;
static volatile uint8_t rs485_phase_guard_recovery_packets = 0u;
static volatile uint8_t sync_phase_filter_reset_request = 0u;
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
static volatile uint8_t rs485_status_slot_response_first = 0u;
static volatile uint8_t rs485_status_slot_response_second = 0u;
static volatile uint8_t rs485_status_slave_reply_div_counter = 0u;
static volatile uint32_t rs485_uart_error_count = 0u;
static volatile uint32_t rs485_uart_pe_count = 0u;
static volatile uint32_t rs485_uart_fe_count = 0u;
static volatile uint32_t rs485_uart_ne_count = 0u;
static volatile uint32_t rs485_uart_ore_count = 0u;
static volatile uint32_t rs485_uart_sync_error_count = 0u;
static volatile uint32_t rs485_anti_phase_sync_error_snapshot = 0u;
static volatile uint32_t rs485_uart_ne_de_count = 0u;
static volatile uint32_t rs485_uart_ne_sync_count = 0u;
static volatile uint32_t rs485_uart_ne_discovery_count = 0u;
static volatile uint32_t rs485_uart_ne_status_count = 0u;
static volatile uint32_t rs485_uart_ne_other_count = 0u;
static volatile uint8_t rs485_status_peer_bytes[32] = {0u};
static volatile uint8_t rs485_status_peer_second_bytes[32] = {0u};
static volatile uint32_t rs485_status_peer_last_ms[32] = {0u};
static volatile uint8_t rs485_status_peer_candidate_count[32] = {0u};
static volatile uint32_t rs485_status_peer_candidate_last_ms[32] = {0u};
static volatile uint8_t rs485_status_rx_word_buf[2] = {0u};
static volatile uint8_t rs485_status_rx_word_index = 0u;
static volatile uint8_t rs485_status_rx_word_after_sync = 0u;
static volatile uint8_t rs485_status_master_first = 0u;
static volatile uint8_t rs485_status_master_second = 0u;
static volatile uint8_t rs485_status_master_selector = 0u;
static volatile uint8_t rs485_status_master_public_byte = 0u;
static volatile uint8_t rs485_status_master_node_id = 0u;
static volatile uint32_t rs485_status_master_last_ms = 0u;
static volatile uint8_t rs485_status_master_candidate_id = 0u;
static volatile uint8_t rs485_status_master_candidate_count = 0u;
static volatile uint32_t rs485_status_master_candidate_last_ms = 0u;
static volatile uint8_t rs485_master_status_tx_pending = 0u;
static volatile uint8_t rs485_master_status_tx_first = 0u;
static volatile uint8_t rs485_master_status_tx_second = 0u;
static volatile uint32_t rs485_master_status_tx_due_ms = 0u;
static volatile uint8_t rs485_status_assign_from_id = 0u;
static volatile uint8_t rs485_status_assign_to_id = 0u;
static volatile uint8_t rs485_status_assign_attempts = 0u;
static volatile uint32_t rs485_status_assign_count = 0u;
static volatile uint32_t rs485_status_assign_confirm_count = 0u;
static volatile uint32_t rs485_status_assign_apply_count = 0u;
static volatile uint8_t rs485_status_assign_last_from_id = 0u;
static volatile uint8_t rs485_status_assign_last_to_id = 0u;
static volatile uint8_t rs485_status_reply_alias_id = 0u;
static volatile uint8_t rs485_status_legacy_master_mode = 0u;
static volatile uint8_t rs485_status_legacy_probe_count = 0u;
static volatile uint32_t rs485_status_legacy_detect_count = 0u;
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
static volatile uint16_t rs485_sensor_local_bits = 0u;
static volatile uint32_t rs485_sensor_local_last_ms = 0u;
static volatile uint8_t rs485_sensor_local_last_index = 0u;
static volatile uint16_t rs485_sensor_peer_bits[32] = {0u};
static volatile uint32_t rs485_sensor_peer_last_ms[32] = {0u};
static volatile uint8_t rs485_sensor_peer_last_index[32] = {0u};
#define RS485_SENSOR_EVENT_QUEUE_CAPACITY 17u /* ring holds all 16 sensor states */
static volatile uint8_t rs485_sensor_event_queue_index[RS485_SENSOR_EVENT_QUEUE_CAPACITY] = {0u};
static volatile uint8_t rs485_sensor_event_queue_value[RS485_SENSOR_EVENT_QUEUE_CAPACITY] = {0u};
static volatile uint8_t rs485_sensor_event_queue_read = 0u;
static volatile uint8_t rs485_sensor_event_queue_write = 0u;
static volatile uint8_t rs485_sensor_event_current_valid = 0u;
static volatile uint8_t rs485_sensor_event_current_index = 0u;
static volatile uint8_t rs485_sensor_event_current_value = 0u;
static volatile uint8_t rs485_sensor_event_retries = 0u;
static volatile uint32_t rs485_sensor_event_cycle = 0u;
static volatile uint32_t rs485_sensor_event_next_cycle = 0u;
static volatile uint32_t rs485_sensor_event_tx_count = 0u;
static volatile uint32_t rs485_sensor_event_rx_count = 0u;
static volatile uint32_t rs485_sensor_event_queue_overflow = 0u;
static uint8_t rs485_local_node_id = 0u;
static uint8_t rs485_local_uid_hint = 1u;
static volatile uint8_t rs485_local_node_id_assigned = 0u;
static uint32_t rs485_master_claim_delay_ms = 1000u;
#define RS485_TX_QUEUE_CAPACITY  64u
static volatile uint8_t rs485_tx_queue[RS485_TX_QUEUE_CAPACITY] = {0};
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
/* Network-wide role assignment. Every board stores the UID of the MASTER;
   only the board whose UID matches may enter MASTER mode. */
static volatile uint8_t rs485_role_master_valid = 0u;
static uint8_t rs485_role_master_uid[12] = {0u};
static volatile uint32_t rs485_role_master_assigned_unix_s = 0u;
static volatile uint16_t rs485_role_master_assigned_millis = 0u;
static volatile uint8_t rs485_role_local_slave_persisted = 0u;
/* The designated sensor SLAVE is independent from MASTER. slave_epoch_valid
   remains set even after a clear, preventing an old claim from reviving the
   previous selection after a reset or temporary bus split. */
static volatile uint8_t rs485_role_slave_epoch_valid = 0u;
static volatile uint8_t rs485_role_slave_valid = 0u;
static volatile uint8_t rs485_role_slave_uid_prefix_only = 0u;
static uint8_t rs485_role_slave_uid[12] = {0u};
static volatile uint32_t rs485_role_slave_assigned_unix_s = 0u;
static volatile uint16_t rs485_role_slave_assigned_millis = 0u;
static volatile uint8_t rs485_role_selected_slave_node_id = 0u;
static volatile uint8_t rs485_role_slave_id_high_water = 0u;
static volatile uint8_t rs485_role_persist_dirty = 0u;
static uint32_t rs485_role_persist_dirty_since_ms = 0u;
static volatile uint8_t rs485_role_persist_save_pending = 0u;
static uint32_t rs485_role_persist_save_ok_snapshot = 0u;
static uint32_t rs485_role_persist_last_request_ms = 0u;
static volatile uint8_t rs485_role_boot_listen_active = 1u;
static uint32_t rs485_role_boot_listen_until_ms = 0u;

/* In fallback (no host-assigned MASTER), remember the strongest MASTER UID
   accepted on E7. A rebooting lower-UID board can briefly elect itself while
   the real bus is quiet; its stale E7 must not erase every slave number. */
static volatile uint8_t rs485_role_auto_master_valid = 0u;
static uint8_t rs485_role_auto_master_uid[12] = {0u};
static uint32_t rs485_role_auto_master_conflict_ms = 0u;
static volatile uint8_t rs485_role_auto_probe_active = 0u;
static uint32_t rs485_role_auto_probe_until_ms = 0u;
static volatile uint8_t rs485_role_auto_heartbeat_tx_remaining = 0u;
static uint32_t rs485_role_auto_heartbeat_tx_next_ms = 0u;

/* Host MASTER assignment is propagated over RS485. Regular announcements let
   a board which was powered off learn the current assignment before it can
   restore an obsolete local MASTER role. */
static volatile uint8_t rs485_role_claim_tx_pending = 0u;
static volatile uint8_t rs485_role_claim_tx_force = 0u;
static volatile uint8_t rs485_role_claim_tx_retries = 0u;
static uint32_t rs485_role_claim_tx_next_ms = 0u;
static uint32_t rs485_role_claim_last_periodic_ms = 0u;
static volatile uint8_t rs485_role_slave_claim_tx_pending = 0u;
static volatile uint8_t rs485_role_slave_claim_tx_force = 0u;
static volatile uint8_t rs485_role_slave_claim_tx_retries = 0u;
static uint32_t rs485_role_slave_claim_tx_next_ms = 0u;
static uint32_t rs485_role_slave_claim_last_periodic_ms = 0u;

/* UID-addressed slave enumeration: the MASTER gives IDs 1,2,3... to one UID
   at a time. Unassigned slaves keep node_id=0 and therefore cannot collide in
   the normal status slots. */
static volatile uint8_t rs485_role_enum_state = 0u;
static volatile uint8_t rs485_role_enum_round = 0u;
static volatile uint8_t rs485_role_enum_collect_pass = 0u;
static volatile uint8_t rs485_role_enum_target_id = 0u;
static volatile uint8_t rs485_role_enum_candidate_valid = 0u;
static uint8_t rs485_role_enum_candidate_uid[12] = {0u};
static volatile uint8_t rs485_role_enum_assign_retries = 0u;
static volatile uint8_t rs485_role_enum_ack_received = 0u;
static volatile uint8_t rs485_role_enum_ack_tx_pending = 0u;
static volatile uint8_t rs485_role_enum_ack_tx_id = 0u;
/* On a slave, E7 makes node_id=0 only provisionally. Do not overwrite the
   last confirmed Flash id until the matching UID-addressed E4 arrives. */
static volatile uint8_t rs485_role_enum_waiting_assignment = 0u;
static volatile uint8_t rs485_role_last_confirmed_node_id = 0u;
static uint32_t rs485_role_enum_ack_tx_next_ms = 0u;
static volatile uint8_t rs485_role_enum_reset_tx_remaining = 0u;
static uint32_t rs485_role_enum_reset_tx_next_ms = 0u;
static uint32_t rs485_role_enum_deadline_ms = 0u;
static uint32_t rs485_role_enum_next_ms = 0u;
static uint32_t rs485_role_enum_stable_mask = 0u;
static uint32_t rs485_role_enum_stable_since_ms = 0u;
/* Stable node IDs are configured by the local RPI.  UID-bearing announcements
   are used only to detect duplicate IDs; they never allocate or renumber. */
static uint8_t rs485_node_claim_uid[32][12] = {{0u}};
static volatile uint32_t rs485_node_claim_last_ms[32] = {0u};
static uint8_t rs485_node_claim_alt_uid[32][12] = {{0u}};
static volatile uint32_t rs485_node_claim_alt_last_ms[32] = {0u};
static volatile uint32_t rs485_node_conflict_mask = 0u;
static volatile uint8_t rs485_node_claim_tx_pending = 0u;
static volatile uint8_t rs485_node_claim_tx_retries = 0u;
static uint32_t rs485_node_claim_tx_next_ms = 0u;
static uint32_t rs485_node_claim_last_periodic_ms = 0u;
static volatile uint32_t rs485_foreign_master_last_ms = 0u;

static volatile uint8_t rs485_role_rx_magic = 0u;
static volatile uint8_t rs485_role_rx_index = 0u;
static volatile uint8_t rs485_role_rx_expected = 0u;
static uint8_t rs485_role_rx_buf[20] = {0u};
#define RS485_ROLE_PENDING_CAPACITY 4u
static volatile uint8_t rs485_role_frame_pending_write = 0u;
static volatile uint8_t rs485_role_frame_pending_read = 0u;
static uint8_t rs485_role_frame_pending_magic[RS485_ROLE_PENDING_CAPACITY] = {0u};
static uint8_t rs485_role_frame_pending_buf[RS485_ROLE_PENDING_CAPACITY][20] = {{0u}};
static uint64_t rs485_local_short_id36 = 0u;
static volatile uint16_t rs485_identity_local_rpi_number = 0u;
static uint8_t rs485_identity_local_ip4[4] = {0u, 0u, 0u, 0u};
static volatile uint8_t rs485_identity_local_ip_set = 0u;
static uint8_t rs485_identity_nibbles[32][21] = {{0u}};
static volatile uint32_t rs485_identity_seen_page_mask[32] = {0u};
static volatile uint32_t rs485_identity_last_ms[32] = {0u};
static uint8_t rs485_identity_local_catalog_id = 0u;
static volatile uint8_t rs485_identity_local_catalog_valid = 0u;
static volatile uint8_t rs485_identity_scan_active = 0u;
static volatile uint8_t rs485_identity_scan_page = 0u;
static volatile uint8_t rs485_identity_self_tx_state = 0u;
static volatile uint8_t rs485_identity_current_req_active = 0u;
static volatile uint8_t rs485_identity_current_req_page = 0u;
static volatile uint8_t rs485_identity_current_req_selector = 0u;
static volatile uint8_t rs485_identity_master_self_page = 0xFFu;
static volatile uint8_t rs485_identity_master_self_node_id = 0u;
static volatile uint8_t rs485_identity_master_self_node_valid = 0u;
static volatile uint32_t rs485_identity_scan_last_start_ms = 0u;
static volatile uint32_t rs485_identity_scan_last_complete_ms = 0u;
static volatile uint8_t rs485_identity_rescan_requested = 0u;
static volatile uint32_t rs485_identity_uid_dedupe_count = 0u;
static volatile uint8_t rs485_master_status_tx_no_response = 0u;
static volatile uint8_t rs485_status_window_no_response_expected = 0u;
static volatile uint8_t rs485_status_master_no_reply_slot = 0u;

#define RS485_DISCOVERY_REQ_BASE 0x40u
#define RS485_DISCOVERY_ACK_BASE 0x80u
#define RS485_DISCOVERY_ID_MASK  0x1Fu
#define RS485_DISCOVERY_MAX_ID   31u
#define RS485_DEVICE_ID_COUNT    32u
#define RS485_SYNC7_BASE         0x25u
#define RS485_SYNC_EDGE_BIT      0x80u
#define RS485_UID_FRAME_MAGIC    0xE1u
#define RS485_UID_FRAME_BYTES    12u
#define RS485_UID_FRAME_TAIL     (RS485_UID_FRAME_BYTES + 1u)
#define RS485_SYNC_PRESENT_MS    250u
#define RS485_AUTO_ROLE_MASTER_LOSS_MS 1000u
#define RS485_BOOT_LISTEN_MS     1000u
#define RS485_MASTER_CLAIM_MS    1000u
#define RS485_PEER_UID_STALE_MS  1500u
#define RS485_UID_RETRY_MS       40u
#define RS485_UID_ANNOUNCE_DELAY_MS 6u
#define RS485_UID_MASTER_ANNOUNCE_MS 250u
#define RS485_UID_SLOT_STEP_MS   7u
#define RS485_UID_SLOT_COUNT     16u
#define RS485_UID_ARBITRATION_QUIET_MS 220u
#define RS485_ROLE_CLAIM_MAGIC          0xE2u
#define RS485_ROLE_ENUM_REQ_MAGIC       0xE3u
#define RS485_ROLE_ENUM_ASSIGN_MAGIC    0xE4u
#define RS485_ROLE_ENUM_ACK_MAGIC       0xE5u
#define RS485_ROLE_SLAVE_CLAIM_MAGIC    0xE6u
#define RS485_ROLE_ENUM_RESET_MAGIC     0xE7u
#define RS485_ROLE_AUTO_HEARTBEAT_MAGIC 0xE8u
#define RS485_ROLE_NODE_CLAIM_MAGIC     0xE9u
#define RS485_ROLE_CLAIM_FORCE_BIT      0x80u
#define RS485_ROLE_SLAVE_VALID_BIT      0x01u
#define RS485_ROLE_SLAVE_NODE_SHIFT     1u
#define RS485_ROLE_SLAVE_NODE_MASK      0x3Eu
#define RS485_ROLE_CLAIM_PAYLOAD_BYTES  20u /* flags + Unix s/ms + UID96 + checksum */
#define RS485_ROLE_ENUM_REQ_BYTES       3u  /* round + target id + checksum */
#define RS485_ROLE_ENUM_ASSIGN_BYTES    14u /* target id + UID96 + checksum */
#define RS485_ROLE_ENUM_ACK_BYTES       14u /* assigned id + UID96 + checksum */
#define RS485_ROLE_ENUM_RESET_BYTES     13u /* master UID96 + checksum */
#define RS485_ROLE_AUTO_HEARTBEAT_BYTES 13u /* candidate MASTER UID96 + checksum */
#define RS485_ROLE_NODE_CLAIM_BYTES     14u /* stable node id + UID96 + checksum */
#define RS485_ROLE_CLAIM_PERIODIC       0u
#define RS485_ROLE_CLAIM_HOST           1u
#define RS485_ROLE_CLAIM_BOOT           2u
#define RS485_ROLE_BOOT_LISTEN_MS       1200u
#define RS485_ROLE_CLAIM_PERIOD_MS      10000u
#define RS485_ROLE_CLAIM_RETRY_MS       25u
#define RS485_ROLE_BUS_QUIET_MS         12u
#define RS485_ROLE_ENUM_COLLECT_MS      96u
#define RS485_ROLE_ENUM_COLLECT_PASSES  8u
#define RS485_ROLE_ENUM_RETRY_MS        500u
#define RS485_ROLE_ENUM_IDLE_RETRY_MS   10000u
#define RS485_ROLE_ENUM_SETTLE_MS       250u
#define RS485_ROLE_ENUM_ACK_TIMEOUT_MS  120u
#define RS485_ROLE_ENUM_ASSIGN_RETRY_MS 35u
#define RS485_ROLE_ENUM_RESET_RETRY_MS  35u
#define RS485_ROLE_ENUM_RESET_RETRIES   3u
#define RS485_ROLE_ENUM_GUARD_BYTES     RS485_ROLE_CLAIM_PAYLOAD_BYTES
#define RS485_ROLE_ENUM_RECONCILE_MS    6000u
#define RS485_ROLE_AUTO_CONFLICT_HOLDOFF_MS 2000u
#define RS485_ROLE_AUTO_PROBE_MS        1600u
#define RS485_ROLE_AUTO_HEARTBEAT_RETRIES 3u
#define RS485_ROLE_AUTO_HEARTBEAT_RETRY_MS 40u
#define RS485_ROLE_PERSIST_DEBOUNCE_MS  1000u
#define RS485_ROLE_AUTO_ENUM_ENABLE     0u
#define RS485_ROLE_NETWORK_ASSIGN_ENABLE 0u
#define RS485_STATUS_COMPACT_ENABLE     0u
#define RS485_NODE_CLAIM_PERIOD_MS      5000u
#define RS485_NODE_CLAIM_HOLD_MS        15000u
#define RS485_NODE_CLAIM_RETRY_MS       37u
#define RS485_NODE_CLAIM_RETRIES        3u
/* A free-running 15-byte claim cannot be inserted safely into the continuous
   200 Hz SYNC/status exchange: receivers that are already inside a two-byte
   status window interpret its payload as node status.  Role and node_id are
   therefore reported to each board's RPI through USB/UART status; a managing
   host detects duplicates by comparing those readbacks. */
#define RS485_NODE_CLAIM_TX_ENABLE      0u
#define RS485_MULTI_MASTER_HOLD_MS      3000u
#define RS485_STATUS_ID_MASK          0x1Fu
#define RS485_STATUS_OPTIC_BIT        0x20u
#define RS485_STATUS_DET_ADC1_BIT     0x40u
#define RS485_STATUS_DET_ADC2_BIT     0x80u
#define RS485_STATUS_WINDOW_DIV       4u
#define RS485_STATUS_SLOT_STRIDE      2u
#define RS485_STATUS_WORD_BYTES       2u
#define RS485_STATUS_SLAVE_REPLY_DIV  1u
#define RS485_STATUS_SLAVE_REPLY_ENABLE 1u
/* Slave opens the reply slot after sync + two master-status bytes. */
#define RS485_STATUS_RESPONSE_DELAY_BYTES 0u
#define RS485_STATUS_RESPONSE_DELAY_BITS  2u
#define RS485_STATUS_MASTER_WORD_TIMEOUT_BYTES 4u
#define RS485_STATUS_MASTER_WORD_DELAY_MS 1u
#define RS485_STATUS_MASTER_WORD_DELAY_BITS 24u
#define RS485_STATUS_LEGACY_PROBE_DIV 16u
#define RS485_STATUS_MASTER_INFO_MASK  0xE0u
#define RS485_STATUS_MASTER_INFO_VALUE 0xC0u
#define RS485_STATUS_ASSIGN_CMD_MASK  0xE0u
#define RS485_STATUS_ASSIGN_CMD_VALUE 0xA0u
#define RS485_STATUS_IDENT_REQ_MASK   0xE0u
#define RS485_STATUS_IDENT_REQ_VALUE  0x80u
#define RS485_STATUS_IDENT_RESP_MASK  0xF0u
#define RS485_STATUS_IDENT_RESP_VALUE 0x40u
#define RS485_STATUS_SENSOR_EVENT_MASK  0xE0u
#define RS485_STATUS_SENSOR_EVENT_VALUE 0x20u
#define RS485_STATUS_MASTER_SELF_PAGE_MASK  0xE0u
#define RS485_STATUS_MASTER_SELF_PAGE_VALUE 0xE0u
#define RS485_STATUS_MASTER_SELF_DATA_MASK  0xF0u
#define RS485_STATUS_MASTER_SELF_DATA_VALUE 0x60u
#define RS485_STATUS_SENSOR_MASK        0xE0u
#define RS485_SENSOR_EVENT_RETRIES      3u
#define RS485_SENSOR_EVENT_RETRY_SLOTS  4u
#define RS485_STATUS_EVENT_DELAY_BITS   2u
/* A regular selected-slave reply waits until an immediate sensor event has
   had enough time to put its two-byte word on the bus. */
#define RS485_STATUS_REGULAR_DELAY_BITS 32u
#define RS485_IDENT_SHORT_NIBBLES     9u
#define RS485_IDENT_RPI_NIBBLES       4u
#define RS485_IDENT_IP_NIBBLES        8u
#define RS485_IDENT_PAGE_COUNT        (RS485_IDENT_SHORT_NIBBLES + \
                                       RS485_IDENT_RPI_NIBBLES + \
                                       RS485_IDENT_IP_NIBBLES)
#define RS485_IDENT_SHORT_PAGE_MASK   ((1u << RS485_IDENT_SHORT_NIBBLES) - 1u)
#define RS485_IDENT_ALL_PAGES_MASK    ((1u << RS485_IDENT_PAGE_COUNT) - 1u)
#define RS485_IDENT_TABLE_COUNT       32u
#define RS485_IDENT_RECENT_MS         30000u
#define RS485_IDENT_EXPIRE_CHECK_MS   1000u
#define RS485_IDENT_AUTO_SCAN_BOOT_MS 1000u
#define RS485_IDENT_AUTO_SCAN_PERIOD_MS 5000u
#define RS485_IDENT_SELF_STATE_NONE   0u
#define RS485_IDENT_SELF_STATE_PAGE   1u
#define RS485_IDENT_SELF_STATE_DATA   2u
#define RS485_STATUS_ASSIGN_RETRY_LIMIT 8u
#define RS485_STATUS_ASSIGN_ID_HOLD_MS 5000u
#define RS485_STATUS_PEER_HOLD_MS     1000u
#define RS485_STATUS_PEER_CONFIRM_COUNT 3u
#define RS485_STATUS_PEER_CONFIRM_WINDOW_MS 500u
#define RS485_SYNC_RELATION_UNKNOWN   0u
#define RS485_SYNC_RELATION_IN_PHASE  1u
#define RS485_SYNC_RELATION_ANTI_PHASE 2u
#define RS485_SYNC_RELATION_CONFIRM_SCORE 5
#define RS485_SYNC_ANTI_PHASE_CONFIRM_PACKETS 200u
#define RS485_SYNC_ANTI_PHASE_MASTER_FRESH_MS RS485_STATUS_PEER_HOLD_MS
#define RS485_SYNC_RESTART_MIN_MS 250u
#define RS485_SYNC_EARLY_REJECT_NUM 3u
#define RS485_SYNC_EARLY_REJECT_DEN 4u
#define RS485_SYNC_EARLY_REJECT_MAX_TICKS 550000u
#define RS485_SYNC_PERIOD_GUARD_ENABLE 1u
#define RS485_SYNC_UART_PERIODIC_LOG_ENABLE 0u
#define RS485_SYNC_UART_PERIODIC_LOG_EDGES 10u
#define RS485_SYNC_ANTI_PHASE_RECOVERY_ENABLE 1u
#define RS485_SYNC_PHASE_TRACK_ENABLE 1u

/* Public device IDs are the native five-bit values 00..31. Whether an ID is
   configured is carried by rs485_local_node_id_assigned, never inferred from
   the numeric value zero. */
static inline uint8_t rs485_status_id_to_wire(uint8_t device_id)
{
  return (uint8_t)(device_id & RS485_STATUS_ID_MASK);
}

static inline uint8_t rs485_status_id_from_wire(uint8_t wire_id)
{
  return (uint8_t)(wire_id & RS485_STATUS_ID_MASK);
}

static inline uint8_t rs485_sync_read_local_marker_phase(void)
{
  return (uint8_t)(adc_stream_get_marker_level() & 1u);
}

static inline uint8_t rs485_sync_edge_kind_from_marker_level(uint8_t marker_level)
{
  return (uint8_t)(marker_level & 1u);
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
static void rs485_freq_trim_reset(uint8_t clear_diag);
static void rs485_freq_trim_on_phase(int32_t phase_error,
                                     uint32_t period_ticks,
                                     uint32_t sample_ticks,
                                     uint32_t bit_ticks);
static void rs485_freq_trim_service(uint32_t now_ms);
static void rs485_discovery_on_sync_received(void);
static void rs485_discovery_on_request(uint8_t value);
static void rs485_discovery_on_response(uint8_t value);
static void rs485_discovery_reset_master_scan(void);
static uint32_t rs485_status_get_effective_period_ticks(void);
static uint32_t rs485_status_get_window_ticks(uint32_t period_ticks);
static uint32_t rs485_status_get_response_delay_ticks(void);
static void rs485_status_wait_bit_times(uint32_t bit_count);
static void rs485_status_wait_response_delay(void);
static uint8_t rs485_status_count_recent_peers(uint32_t now_ms, uint32_t hold_ms);
static uint32_t rs485_status_get_recent_peer_mask(uint32_t now_ms, uint32_t hold_ms);
static uint8_t rs485_status_confirm_peer_candidate(uint8_t node_id,
                                                   uint32_t now_ms);
static uint8_t rs485_status_confirm_master_candidate(uint8_t node_id,
                                                     uint32_t now_ms);
static void rs485_status_clear_peer_id(uint8_t node_id);
static void rs485_status_clear_peer_table(void);
static void rs485_status_assignment_reset(void);
static void rs485_status_compact_schedule_assignment(uint32_t now_ms);
static void rs485_status_compact_on_master_response(uint8_t response_id);
static void rs485_status_apply_master_assignment(uint8_t first, uint8_t second);
static uint8_t rs485_status_build_local_byte(void);
static uint8_t rs485_status_build_public_local_byte(void);
static uint8_t rs485_status_build_master_identity_first_byte(void);
static uint8_t rs485_status_build_local_second_byte(uint8_t selector);
static void rs485_status_note_master_word(uint8_t first, uint8_t second);
static uint8_t rs485_sensor_event_set_local(uint8_t sensor_index, uint8_t active);
static uint8_t rs485_sensor_event_prepare(uint8_t *sensor_index, uint8_t *active);
static void rs485_sensor_event_commit_tx(void);
static void rs485_sensor_queue_active_snapshot(void);
static void rs485_sensor_note_remote(uint8_t device_id,
                                     uint8_t sensor_index,
                                     uint8_t active,
                                     uint32_t now_ms);
static void rs485_identity_clear_row(uint8_t node_id);
static void rs485_identity_expire_rows(uint32_t now_ms);
static uint8_t rs485_identity_get_local_nibble(uint8_t page);
static void rs485_identity_store_local_row(uint32_t now_ms);
static void rs485_identity_note_local_id_change(uint8_t old_id, uint8_t new_id);
static void rs485_identity_note_master_request(uint8_t first, uint8_t second);
static void rs485_identity_note_master_self_word(uint8_t first, uint8_t second);
static void rs485_identity_note_response(uint8_t node_id, uint8_t second);
static void rs485_identity_master_on_selector_wrap(void);
static void rs485_identity_service(uint32_t now_ms);
static void rs485_status_reset_window_state(void);
static void rs485_status_finalize_window(void);
static void rs485_status_begin_window(void);
static uint8_t rs485_status_on_received(uint8_t first, uint8_t second);
static void rs485_status_on_master_word(uint8_t first, uint8_t second);
static void rs485_status_service(void);
static void rs485_master_status_service(uint32_t now_ms);
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
static void rs485_role_reset_local_assignment_epoch(void);
static void rs485_role_mark_persist_dirty(void);
static void rs485_role_mark_persist_dirty_immediate(void);
static void rs485_role_persist_service(uint32_t now_ms);
static void rs485_role_schedule_master_claim(uint8_t reason);
static void rs485_role_claim_service(uint32_t now_ms);
static void rs485_role_schedule_slave_claim(uint8_t reason);
static void rs485_role_slave_claim_service(uint32_t now_ms);
static void rs485_role_enumeration_service(uint32_t now_ms);
static void rs485_role_start_enumeration_epoch(uint32_t now_ms);
static void rs485_node_claim_reset_registry(void);
static void rs485_node_claim_schedule(uint32_t now_ms, uint8_t retries);
static void rs485_node_claim_service(uint32_t now_ms);
static void rs485_node_claim_note(uint8_t node_id, const uint8_t uid[12]);
static void rs485_role_schedule_auto_heartbeat(uint32_t now_ms,
                                               uint8_t retries);
static void rs485_role_auto_heartbeat_tx_service(uint32_t now_ms);
static uint8_t rs485_role_frame_checksum(uint8_t magic,
                                         const uint8_t *payload,
                                         uint8_t payload_len);
static int8_t rs485_role_compare_assignment(uint32_t unix_s,
                                            uint16_t millis,
                                            const uint8_t uid[12]);
static int8_t rs485_role_compare_slave_assignment(uint32_t unix_s,
                                                  uint16_t millis,
                                                  const uint8_t uid[12]);
static uint8_t rs485_role_local_is_selected_slave(void);
static void rs485_role_reset_rx(void);
static void rs485_role_rx_service(void);
static void rs485_role_process_frame(uint8_t magic, const uint8_t *payload);
static void rs485_sync_assigned_role_service(uint32_t now_ms);
static void arr_auto_tune_service(void);
static uint32_t tim15_sync_get_deadband_ticks(void);
static int32_t arr_auto_abs_i32(int32_t value);
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
static void rs485_sync_start_tx_status_word(uint8_t first, uint8_t second);
static void rs485_sync_service_tx(void);

/* Охраняемая флаг-структура для need_recovery с сигнатурами по краям. */
typedef struct {
  uint32_t c1;
  volatile uint32_t flag;
  uint32_t c2;
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
/* Boot-only CPU log.  It does not need DMA access and is deliberately kept
   out of the nearly full D1 RAM so nested IRQ stack frames cannot hit .bss. */
static char stage_log[32][16]
    __attribute__((section(".ram_dtcm"), aligned(32), unused));
static int stage_count __attribute__((unused)) = 0;
static void FlushStageLog(void) __attribute__((unused));
static void FlushStageLog(void) { /* no-op в безопасном режиме */ }

/* Дефолты для флагов сборки, чтобы они не оставались неопределёнными. */
#ifndef MINIMAL_BRINGUP
#define MINIMAL_BRINGUP 0
#endif
#ifndef ENABLE_SOFT_USB_RECOVERY
#define ENABLE_SOFT_USB_RECOVERY 1
#endif
#ifndef DIAG_TRAP_STAGE
#define DIAG_TRAP_STAGE 0
#endif
#ifndef EARLY_CDC_PROBE
#define EARLY_CDC_PROBE 1
#endif

/* Диагностика причин сброса и стадий загрузки. */
static uint32_t reset_cause_raw = 0;
static const uint32_t build_signature_hex = 0xA5B6C7D8u;

typedef struct {
  uint32_t magic;
  uint32_t index;
  uint32_t rsr[8];
  uint32_t hardfault_count;
  uint32_t busfault_count;
  uint32_t usagefault_count;
} reset_trace_t;

static reset_trace_t __attribute__((section(".noinit"))) g_reset_trace;

static void reset_trace_record(uint32_t rsr)
{
  if (g_reset_trace.magic != 0x21524553UL) {
    g_reset_trace.magic = 0x21524553UL;
    g_reset_trace.index = 0u;
    for (int index = 0; index < 8; index++) {
      g_reset_trace.rsr[index] = 0u;
    }
    g_reset_trace.hardfault_count = 0u;
    g_reset_trace.busfault_count = 0u;
    g_reset_trace.usagefault_count = 0u;
  }

  g_reset_trace.rsr[g_reset_trace.index & 7u] = rsr;
  g_reset_trace.index++;
}

static const char *reset_cause_str(uint32_t rsr)
{
  if ((rsr & RCC_RSR_IWDG1RSTF) != 0u) return "IWDG";
  if ((rsr & RCC_RSR_WWDG1RSTF) != 0u) return "WWDG";
  if ((rsr & RCC_RSR_LPWRRSTF) != 0u) return "LPWR";
  if ((rsr & RCC_RSR_BORRSTF) != 0u) return "BOR";
  if ((rsr & RCC_RSR_PINRSTF) != 0u) return "PIN";
  if ((rsr & RCC_RSR_SFTRSTF) != 0u) return "SOFT";
  if ((rsr & RCC_RSR_PORRSTF) != 0u) return "POR";
  return "UNK";
}

static void log_reset_cause(void)
{
  char flags[96];

  reset_cause_raw = RCC->RSR;
  reset_trace_record(reset_cause_raw);

  flags[0] = '\0';
  #define ADD_FLAG(bit, name) do { \
    if ((reset_cause_raw & (bit)) != 0u) { \
      if (flags[0] != '\0') { \
        strncat(flags, ",", sizeof(flags) - strlen(flags) - 1u); \
      } \
      strncat(flags, (name), sizeof(flags) - strlen(flags) - 1u); \
    } \
  } while (0)
  ADD_FLAG(RCC_RSR_IWDG1RSTF, "IWDG");
  ADD_FLAG(RCC_RSR_WWDG1RSTF, "WWDG");
  ADD_FLAG(RCC_RSR_LPWRRSTF, "LPWR");
  ADD_FLAG(RCC_RSR_BORRSTF, "BOR");
  ADD_FLAG(RCC_RSR_PINRSTF, "PIN");
  ADD_FLAG(RCC_RSR_SFTRSTF, "SOFT");
  ADD_FLAG(RCC_RSR_PORRSTF, "POR");
  #undef ADD_FLAG

  if (flags[0] == '\0') {
    strncpy(flags, "NONE", sizeof(flags) - 1u);
    flags[sizeof(flags) - 1u] = '\0';
  }

  printf("[BOOT] RSR=0x%08lX FLAGS=%s PRIMARY=%s SIGN=0x%08lX\r\n",
         (unsigned long)reset_cause_raw,
         flags,
         reset_cause_str(reset_cause_raw),
         (unsigned long)build_signature_hex);
  printf("[BOOT] RSR_TRACE idx=%lu: ", (unsigned long)g_reset_trace.index);
  for (int index = 0; index < 8; index++) {
    uint32_t value = g_reset_trace.rsr[(g_reset_trace.index - 1u - (uint32_t)index) & 7u];
    printf(index != 0 ? ",0x%08lX" : "0x%08lX", (unsigned long)value);
  }
  printf("\r\n");
  RCC->RSR |= RCC_RSR_RMVF;
}

static volatile uint8_t iwdg_enabled_runtime = 0u;

typedef struct {
  uint32_t magic;
  uint32_t boot_counter;
  uint32_t slot;
  struct {
    uint32_t uptime_ms;
    uint32_t progress_flags;
    uint32_t rsr;
  } rec[8];
} boot_diag_t;

static boot_diag_t __attribute__((section(".noinit"))) g_boot_diag;

enum {
  BOOT_PROGRESS_AFTER_PWM = (1u << 0),
  BOOT_PROGRESS_AFTER_USB_INIT = (1u << 1),
  BOOT_PROGRESS_AFTER_ADC = (1u << 2),
  BOOT_PROGRESS_ENTER_LOOP = (1u << 3)
};

static uint32_t g_progress_flags = 0u;

static void boot_diag_init(uint32_t current_rsr)
{
  (void)current_rsr;

  if (g_boot_diag.magic != 0x42444731UL) {
    memset(&g_boot_diag, 0, sizeof(g_boot_diag));
    g_boot_diag.magic = 0x42444731UL;
  }

  g_boot_diag.boot_counter++;
  printf("[BOOT] LAST_UPTIMES(ms): ");
  for (int index = 0; index < 8; index++) {
    uint32_t value = g_boot_diag.rec[(g_boot_diag.slot - 1u - (uint32_t)index) & 7u].uptime_ms;
    printf(index != 0 ? ",%lu" : "%lu", (unsigned long)value);
  }
  printf("\r\n");
  printf("[BOOT] LAST_PROGRESS: ");
  for (int index = 0; index < 4; index++) {
    uint32_t value = g_boot_diag.rec[(g_boot_diag.slot - 1u - (uint32_t)index) & 7u].progress_flags;
    printf(index != 0 ? ",0x%02lX" : "0x%02lX", (unsigned long)value);
  }
  printf("\r\n");
}

static void boot_diag_periodic(uint32_t uptime_ms)
{
  uint32_t slot = g_boot_diag.slot & 7u;

  g_boot_diag.rec[slot].uptime_ms = uptime_ms;
  g_boot_diag.rec[slot].progress_flags = g_progress_flags;
  g_boot_diag.rec[slot].rsr = reset_cause_raw;
}

static void boot_diag_finalize_before_reset(uint32_t uptime_ms)
{
  uint32_t slot = g_boot_diag.slot & 7u;

  g_boot_diag.rec[slot].uptime_ms = uptime_ms;
  g_boot_diag.rec[slot].progress_flags = g_progress_flags;
  g_boot_diag.rec[slot].rsr = reset_cause_raw;
  g_boot_diag.slot++;
}

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
uint32_t tim2_apply_profile_window(void);
void UpdateLCDStatus(void);
void DrawStarIndicator(void);
static void __attribute__((unused)) UpdateUSBDebug(void);
void DrawUSBStatus(void);
static const char *usb_state_str(uint8_t s) __attribute__((unused));

#ifndef SAFE_MINIMAL
#define SAFE_MINIMAL 0
#endif
#ifndef SAFE_BLINK_ONLY
#define SAFE_BLINK_ONLY 0
#endif
#ifndef ENABLE_UART_HEARTBEAT
#define ENABLE_UART_HEARTBEAT 0
#endif
#ifndef DIAG_DISABLE_IWDG
#define DIAG_DISABLE_IWDG 0
#endif
#ifndef DIAG_EXTEND_EXISTING_IWDG
#define DIAG_EXTEND_EXISTING_IWDG 1
#endif
#ifndef DIAG_INT_MASK_LOG_PERIOD
#define DIAG_INT_MASK_LOG_PERIOD 50
#endif

static inline void diag_halt(const char *tag)
{
  printf("[DIAG] HALT %s\r\n", tag);
  __BKPT(0);
  while (1) {
    __NOP();
  }
}

void uart1_raw_putc(char c)
{
  if ((USART1->CR1 & USART_CR1_UE) == 0u) {
    return;
  }

  for (volatile uint32_t timeout = 0u; timeout < 20000u; ++timeout) {
    if ((USART1->ISR & USART_ISR_TXE_TXFNF) != 0u) {
      USART1->TDR = (uint8_t)c;
      return;
    }
  }
}

static void uart1_raw_write_str(const char *text)
{
  if (text == NULL) {
    return;
  }
  while (*text != '\0') {
    uart1_raw_putc(*text++);
  }
}

static void uart1_raw_write_u32_dec(uint32_t value)
{
  char buf[10];
  uint8_t len = 0u;

  if (value == 0u) {
    uart1_raw_putc('0');
    return;
  }

  while ((value != 0u) && (len < (uint8_t)sizeof(buf))) {
    buf[len++] = (char)('0' + (value % 10u));
    value /= 10u;
  }
  while (len != 0u) {
    uart1_raw_putc(buf[--len]);
  }
}

static void uart1_raw_write_i32_dec(int32_t value)
{
  uint32_t magnitude;

  if (value < 0) {
    uart1_raw_putc('-');
    magnitude = 0u - (uint32_t)value;
  } else {
    magnitude = (uint32_t)value;
  }
  uart1_raw_write_u32_dec(magnitude);
}

static void uart1_raw_write_hex_nibble(uint8_t value)
{
  value &= 0x0Fu;
  uart1_raw_putc((char)((value < 10u) ? ('0' + value) : ('A' + (value - 10u))));
}

static void uart1_raw_write_u8_hex(uint8_t value)
{
  uart1_raw_write_hex_nibble((uint8_t)(value >> 4));
  uart1_raw_write_hex_nibble(value);
}

static void uart1_raw_write_u32_hex(uint32_t value)
{
  for (int8_t shift = 28; shift >= 0; shift = (int8_t)(shift - 4)) {
    uart1_raw_write_hex_nibble((uint8_t)(value >> (uint8_t)shift));
  }
}

static void uart1_raw_print_version(void)
{
  uart1_raw_write_str("\r\nVERSION fw=");
  uart1_raw_write_str(FW_VERSION_STR);
  uart1_raw_write_str(" git=");
  uart1_raw_write_str(fw_git_hash);
  uart1_raw_write_str(" build_date=");
  uart1_raw_write_str(fw_build_date);
  uart1_raw_write_str(" build_time=");
  uart1_raw_write_str(fw_build_time);
  uart1_raw_write_str(" pairs=8\r\n");
}

static void uart1_raw_print_sync_state(void)
{
  extern volatile uint32_t sync_buffers_between_edges;
  vnd_lcd_sync_snapshot_t lcd;
  uint8_t local_status = 0u;
  uint8_t active_status_count = 0u;
  uint8_t master_status = 0u;
  uint8_t master_flags = 0u;
  uint16_t master_age_ms = 0xFFFFu;
  uint8_t total_devices = 1u;
  uint8_t persisted_mode = VND_SYNC_MODE_SLAVE;
  uint8_t role_persisted = 0u;
  uint32_t seen_mask = 0u;

  memset(&lcd, 0, sizeof(lcd));
  vnd_get_lcd_sync_snapshot(&lcd);
  (void)rs485_status_get_snapshot(&local_status,
                                  &active_status_count,
                                  &seen_mask,
                                  NULL,
                                  32u);
  (void)rs485_status_get_master_snapshot(&master_status,
                                         &master_age_ms,
                                         &master_flags);
  role_persisted = rs485_role_get_local_persisted_mode(&persisted_mode);

  if ((lcd.raw_mode == VND_SYNC_MODE_MASTER) ||
      (lcd.raw_mode == VND_SYNC_MODE_SLAVE)) {
    total_devices = (active_status_count != 0u) ? active_status_count : 1u;
  }

  uart1_raw_write_str("\r\nSYNC_STATE raw=");
  uart1_raw_write_u32_dec(lcd.raw_mode);
  uart1_raw_write_str(" display=");
  uart1_raw_write_u32_dec(lcd.display_mode);
  uart1_raw_write_str(" char=");
  uart1_raw_putc((char)lcd.display_char);
  uart1_raw_write_str(" node=");
  uart1_raw_write_u32_dec(lcd.node_id);
  uart1_raw_write_str(" id_assigned=");
  uart1_raw_write_u32_dec(lcd.node_id_assigned);
  uart1_raw_write_str(" display_value=");
  uart1_raw_write_u32_dec(lcd.display_value);
  uart1_raw_write_str(" active_status_count=");
  uart1_raw_write_u32_dec(active_status_count);
  uart1_raw_write_str(" total_devices=");
  uart1_raw_write_u32_dec(total_devices);
  uart1_raw_write_str(" sync_seen_mask=0x");
  uart1_raw_write_u32_hex(seen_mask);
  uart1_raw_write_str(" uid_dedupe=");
  uart1_raw_write_u32_dec(rs485_identity_uid_dedupe_count);
  uart1_raw_write_str(" local_status=0x");
  uart1_raw_write_u8_hex(local_status);
  uart1_raw_write_str(" master_status=0x");
  uart1_raw_write_u8_hex(master_status);
  uart1_raw_write_str(" master_age_ms=");
  uart1_raw_write_u32_dec((uint32_t)master_age_ms);
  uart1_raw_write_str(" master_flags=0x");
  uart1_raw_write_u8_hex(master_flags);
  uart1_raw_write_str(" role_persisted=");
  uart1_raw_write_u32_dec(role_persisted);
  uart1_raw_write_str(" persisted_mode=");
  uart1_raw_write_u32_dec((uint32_t)persisted_mode);
  uart1_raw_write_str(" id_conflicts=0x");
  uart1_raw_write_u32_hex(rs485_node_get_conflict_mask());
  uart1_raw_write_str(" multiple_master=");
  uart1_raw_write_u32_dec((uint32_t)rs485_multiple_master_detected());
  uart1_raw_write_str(" sync_alive=");
  uart1_raw_write_u32_dec((uint32_t)lcd.sync_signal_alive);
  uart1_raw_write_str(" sync_ok=");
  uart1_raw_write_u32_dec((uint32_t)lcd.sync_ok_visual);
  uart1_raw_write_str(" sync_locked=");
  uart1_raw_write_u32_dec((uint32_t)lcd.sync_color_locked);
  uart1_raw_write_str(" sync_age_ms=");
  uart1_raw_write_u32_dec(lcd.sync_age_ms);
  uart1_raw_write_str(" sync_edges=");
  uart1_raw_write_u32_dec(sync_edge_count);
  uart1_raw_write_str(" phase_rel=");
  uart1_raw_write_u32_dec((uint32_t)rs485_sync_phase_relation);
  uart1_raw_write_str(" phase_score=");
  uart1_raw_write_i32_dec((int32_t)rs485_sync_relation_score);
  uart1_raw_write_str(" anti_active=");
  uart1_raw_write_u32_dec((uint32_t)rs485_anti_phase_recovery_active);
  uart1_raw_write_str(" anti_packets=");
  uart1_raw_write_u32_dec((uint32_t)rs485_anti_phase_recovery_packets);
  uart1_raw_write_str(" anti_request=");
  uart1_raw_write_u32_dec((uint32_t)rs485_anti_phase_recovery_request);
  uart1_raw_write_str(" anti_sync_err=");
  uart1_raw_write_u32_dec(rs485_anti_phase_sync_error_snapshot);
  uart1_raw_write_str(" phase_flip=");
  uart1_raw_write_u32_dec(rs485_phase_polarity_flip_count);
  uart1_raw_write_str(" phase_slew=");
  uart1_raw_write_u32_dec(rs485_phase_slew_pulse_count);
  uart1_raw_write_str(" phase_slew_active=");
  uart1_raw_write_u32_dec((uint32_t)rs485_phase_slew_active);
  uart1_raw_write_str(" phase_slew_left=");
  uart1_raw_write_u32_dec(rs485_phase_slew_remaining_ticks);
  uart1_raw_write_str(" phase_locked=");
  uart1_raw_write_u32_dec((uint32_t)rs485_sync_locked);
  uart1_raw_write_str(" phase_err=");
  uart1_raw_write_i32_dec(sync_phase_last_error_ticks);
  uart1_raw_write_str(" phase_ctrl=");
  uart1_raw_write_i32_dec(sync_phase_last_control_error_ticks);
  uart1_raw_write_str(" phase_pulse=");
  uart1_raw_write_i32_dec(sync_phase_last_pulse_delta);
  uart1_raw_write_str(" phase_updates=");
  uart1_raw_write_u32_dec(sync_phase_fast_edges);
  uart1_raw_write_str("/");
  uart1_raw_write_u32_dec(sync_phase_fast_pulses);
  uart1_raw_write_str(" phase_skip=");
  uart1_raw_write_u32_dec(sync_phase_fast_skip_busy);
  uart1_raw_write_str("/");
  uart1_raw_write_u32_dec(sync_phase_fast_skip_spacing);
  uart1_raw_write_str(" phase_restart=");
  uart1_raw_write_u32_dec(rs485_sync_restart_count);
  uart1_raw_write_str(" rs485_rx=");
  uart1_raw_write_u32_dec(rs485_rx_packets);
  uart1_raw_write_str(" rs485_tx=");
  uart1_raw_write_u32_dec(rs485_tx_packets);
  uart1_raw_write_str(" uart_err=");
  uart1_raw_write_u32_dec(rs485_uart_error_count);
  uart1_raw_write_str(" uart_pe=");
  uart1_raw_write_u32_dec(rs485_uart_pe_count);
  uart1_raw_write_str(" uart_fe=");
  uart1_raw_write_u32_dec(rs485_uart_fe_count);
  uart1_raw_write_str(" uart_ne=");
  uart1_raw_write_u32_dec(rs485_uart_ne_count);
  uart1_raw_write_str(" uart_ore=");
  uart1_raw_write_u32_dec(rs485_uart_ore_count);
  uart1_raw_write_str(" sync_err=");
  uart1_raw_write_u32_dec(rs485_uart_sync_error_count);
  uart1_raw_write_str(" ne_de=");
  uart1_raw_write_u32_dec(rs485_uart_ne_de_count);
  uart1_raw_write_str(" ne_sync=");
  uart1_raw_write_u32_dec(rs485_uart_ne_sync_count);
  uart1_raw_write_str(" ne_disc=");
  uart1_raw_write_u32_dec(rs485_uart_ne_discovery_count);
  uart1_raw_write_str(" ne_status=");
  uart1_raw_write_u32_dec(rs485_uart_ne_status_count);
  uart1_raw_write_str(" ne_other=");
  uart1_raw_write_u32_dec(rs485_uart_ne_other_count);
  uart1_raw_write_str(" period=");
  uart1_raw_write_u32_dec(sync_tim5_period_ticks);
  uart1_raw_write_str(" sample=");
  uart1_raw_write_u32_dec((uint32_t)sync_phase_diag_sample);
  uart1_raw_write_str("/");
  uart1_raw_write_u32_dec((uint32_t)sync_phase_diag_samples);
  uart1_raw_write_str(" tim15=");
  uart1_raw_write_u32_dec(TIM15->CNT);
  uart1_raw_write_str("/");
  uart1_raw_write_u32_dec(TIM15->ARR);
  uart1_raw_write_str(" bufs_per_sync=");
  uart1_raw_write_u32_dec(sync_buffers_between_edges);
  uart1_raw_write_str(" sync_reject=");
  uart1_raw_write_u32_dec(rs485_sync_rejected_early_count);
  uart1_raw_write_str("\r\n");
}

static uint8_t uart1_parse_long_arg(char *arg, long min_value, long max_value, long *out_value)
{
  char *end_ptr = NULL;
  long value;

  if ((arg == NULL) || (out_value == NULL)) {
    return 0u;
  }

  while (*arg == ' ') {
    arg++;
  }
  if (*arg == 0) {
    return 0u;
  }

  value = strtol(arg, &end_ptr, 10);
  while ((end_ptr != NULL) && (*end_ptr == ' ')) {
    end_ptr++;
  }
  if ((end_ptr == arg) || ((end_ptr != NULL) && (*end_ptr != 0)) ||
      (value < min_value) || (value > max_value)) {
    return 0u;
  }

  *out_value = value;
  return 1u;
}

static void uart1_raw_print_optic_state(void)
{
  adc_stream_debug_t adc_dbg;
  vnd_change_event_diag_t usb_event_diag;
  uint8_t local_status = 0u;
  uint8_t active_status_count = 0u;
  uint8_t master_status = 0u;
  uint8_t master_flags = 0u;
  uint8_t status_bytes[RS485_DEVICE_ID_COUNT] = {0u};
  uint16_t master_age_ms = 0xFFFFu;
  uint32_t seen_mask = 0u;
  uint32_t optic_mask = 0u;
  uint32_t now_ms = HAL_GetTick();
  uint32_t host_rx_ack_ms = vnd_get_last_host_rx_ack_ms();
  uint32_t host_rx_age_ms = (host_rx_ack_ms == 0u) ? 0xFFFFFFFFu : (now_ms - host_rx_ack_ms);
  uint32_t frame_txcplt_ms = vnd_get_last_frame_txcplt_ms();
  uint32_t frame_txcplt_age_ms = (frame_txcplt_ms == 0u) ? 0xFFFFFFFFu : (now_ms - frame_txcplt_ms);
  uint32_t tx_cmd_last_ms = vnd_get_tx_host_cmd_last_ms();
  uint32_t tx_cmd_age_ms = (tx_cmd_last_ms == 0u) ? 0xFFFFFFFFu : (now_ms - tx_cmd_last_ms);
  uint32_t tx_change_last_ms = vnd_get_tx_effective_last_change_ms();
  uint32_t tx_change_age_ms = (tx_change_last_ms == 0u) ? 0xFFFFFFFFu : (now_ms - tx_change_last_ms);
  uint32_t dc_set_age_ms = (vnd_dc_speed_set_at_ms == 0u)
                         ? 0xFFFFFFFFu
                         : (now_ms - vnd_dc_speed_set_at_ms);
  uint32_t dc_adapt_age_ms = (vnd_dc_adapt_last_ms == 0u)
                           ? 0xFFFFFFFFu
                           : (now_ms - vnd_dc_adapt_last_ms);
  uint32_t adc_publish_age_ms = 0xFFFFFFFFu;
  uint8_t pd0_level = (HAL_GPIO_ReadPin(OPTIC_RX_GPIO_Port, OPTIC_RX_Pin) == GPIO_PIN_SET) ? 1u : 0u;
  uint8_t marker_phase = 0u;
  uint8_t pa1_level = 0u;
  uint8_t pa2_level = 0u;
  uint8_t pc7_level = 0u;
  uint32_t primask = __get_PRIMASK();

  memset(&adc_dbg, 0, sizeof(adc_dbg));
  memset(&usb_event_diag, 0, sizeof(usb_event_diag));
  adc_stream_get_debug(&adc_dbg);
  vnd_get_change_event_diag(&usb_event_diag);
  if (adc_dbg.last_publish_ms != 0u) {
    adc_publish_age_ms = now_ms - adc_dbg.last_publish_ms;
  }

  /* Capture the phase and all TX200 pins before the slow UART print starts.
     Reading each pin inline while printing can span several 200 Hz edges and
     falsely report a mixed phase. */
  __disable_irq();
  marker_phase = adc_stream_get_marker_level();
  pa1_level = (GPIOA->IDR & GPIO_PIN_1) ? 1u : 0u;
  pa2_level = (GPIOA->IDR & GPIO_PIN_2) ? 1u : 0u;
  pc7_level = (GPIOC->IDR & GPIO_PIN_7) ? 1u : 0u;
  if (primask == 0u) {
    __enable_irq();
  }

  (void)rs485_status_get_snapshot(&local_status,
                                  &active_status_count,
                                  &seen_mask,
                                  status_bytes,
                                  RS485_DEVICE_ID_COUNT);
  for (uint8_t device_id = 0u;
       device_id < RS485_DEVICE_ID_COUNT;
       device_id++) {
    if (((seen_mask & (1u << device_id)) != 0u) &&
        ((status_bytes[device_id] & RS485_STATUS_OPTIC_BIT) != 0u)) {
      optic_mask |= (1u << device_id);
    }
  }
  (void)rs485_status_get_master_snapshot(&master_status,
                                         &master_age_ms,
                                         &master_flags);

  uart1_raw_write_str("\r\nOPTIC tx200=");
  uart1_raw_write_u32_dec((uint32_t)vnd_is_tx_enabled());
  uart1_raw_write_str(" tx_req=");
  uart1_raw_write_u32_dec((uint32_t)vnd_is_tx_requested_enabled());
  uart1_raw_write_str(" stream=");
  uart1_raw_write_u32_dec((uint32_t)vnd_is_streaming());
  uart1_raw_write_str(" dc_ms=");
  uart1_raw_write_u32_dec(vnd_dc_speed_settle_ms);
  uart1_raw_write_str(" dc_cmd=");
  uart1_raw_write_u32_dec(vnd_dc_speed_cmd_count);
  uart1_raw_write_str(" dc_rej=");
  uart1_raw_write_u32_dec(vnd_dc_speed_reject_count);
  uart1_raw_write_str(" dc_set_age_ms=");
  uart1_raw_write_u32_dec(dc_set_age_ms);
  uart1_raw_write_str(" dc_calls=");
  uart1_raw_write_u32_dec(vnd_dc_adapt_call_count);
  uart1_raw_write_str(" dc_updates=");
  uart1_raw_write_u32_dec(vnd_dc_adapt_updates);
  uart1_raw_write_str(" dc_adapt_age_ms=");
  uart1_raw_write_u32_dec(dc_adapt_age_ms);
  uart1_raw_write_str(" dc_dt_ms=");
  uart1_raw_write_u32_dec(vnd_dc_adapt_last_dt_ms);
  uart1_raw_write_str(" dc_maxerr=");
  uart1_raw_write_u32_dec(vnd_dc_adapt_max_abs_err);
  uart1_raw_write_str(" dc_maxcorr=");
  uart1_raw_write_u32_dec(vnd_dc_adapt_max_corr);
  uart1_raw_write_str(" dc_meanerr=");
  uart1_raw_write_i32_dec(vnd_dc_adapt_mean_err);
  uart1_raw_write_str(" adc_wr=");
  uart1_raw_write_u32_dec(adc_dbg.frame_wr_seq);
  uart1_raw_write_str(" adc_rd=");
  uart1_raw_write_u32_dec(adc_dbg.frame_rd_seq);
  uart1_raw_write_str(" adc_drop=");
  uart1_raw_write_u32_dec(adc_dbg.frame_overflow_drops);
  uart1_raw_write_str(" adc_pause=");
  uart1_raw_write_u32_dec((uint32_t)adc_stream_paused);
  uart1_raw_write_str(" adc_pub_age_ms=");
  uart1_raw_write_u32_dec(adc_publish_age_ms);
  uart1_raw_write_str(" adc_restart=");
  uart1_raw_write_u32_dec(adc_dbg.restart_attempts);
  uart1_raw_write_str("/");
  uart1_raw_write_u32_dec(adc_dbg.restart_success);
  uart1_raw_write_str(" adc_restart_suppressed=");
  uart1_raw_write_u32_dec(adc_dbg.restart_suppressed_live);
  uart1_raw_write_str(" adc_restart_reason=");
  uart1_raw_write_u32_dec(adc_dbg.restart_reason);
  uart1_raw_write_str(" adc_restart_ndtr=");
  uart1_raw_write_u32_dec(adc_dbg.restart_ndtr_a);
  uart1_raw_write_str("/");
  uart1_raw_write_u32_dec(adc_dbg.restart_ndtr_b);
  uart1_raw_write_str(" adc_restart_age_ms=");
  uart1_raw_write_u32_dec(adc_dbg.restart_publish_age_ms);
  uart1_raw_write_str(" adc_tc_rearm_fail=");
  uart1_raw_write_u32_dec(adc_dbg.tc_rearm_failures);
  uart1_raw_write_str(" adc_gap_max_ms=");
  uart1_raw_write_u32_dec(adc_dbg.publish_gap_max_ms);
  uart1_raw_write_str(" adc_gap10=");
  uart1_raw_write_u32_dec(adc_dbg.publish_gap_over_10ms);
  uart1_raw_write_str(" tx_cmd_count=");
  uart1_raw_write_u32_dec(vnd_get_tx_host_cmd_count());
  uart1_raw_write_str(" tx_cmd_val=");
  uart1_raw_write_u32_dec((uint32_t)vnd_get_tx_host_cmd_last_value());
  uart1_raw_write_str(" tx_cmd_age_ms=");
  uart1_raw_write_u32_dec(tx_cmd_age_ms);
  uart1_raw_write_str(" tx_change=");
  uart1_raw_write_u32_dec(vnd_get_tx_effective_change_count());
  uart1_raw_write_str(" tx_off=");
  uart1_raw_write_u32_dec(vnd_get_tx_effective_off_count());
  uart1_raw_write_str(" tx_change_val=");
  uart1_raw_write_u32_dec((uint32_t)vnd_get_tx_effective_last_value());
  uart1_raw_write_str(" tx_change_age_ms=");
  uart1_raw_write_u32_dec(tx_change_age_ms);
  uart1_raw_write_str(" marker_phase=");
  uart1_raw_write_u32_dec((uint32_t)marker_phase);
  uart1_raw_write_str(" pa1=");
  uart1_raw_write_u32_dec((uint32_t)pa1_level);
  uart1_raw_write_str(" pa2=");
  uart1_raw_write_u32_dec((uint32_t)pa2_level);
  uart1_raw_write_str(" pc7=");
  uart1_raw_write_u32_dec((uint32_t)pc7_level);
  uart1_raw_write_str(" host_rx_age_ms=");
  uart1_raw_write_u32_dec(host_rx_age_ms);
  uart1_raw_write_str(" usb_frame_age_ms=");
  uart1_raw_write_u32_dec(frame_txcplt_age_ms);
  uart1_raw_write_str(" usb_txcplt=");
  uart1_raw_write_u32_dec(vnd_get_stream_tx_cplt_count());
  uart1_raw_write_str(" usb_recovery=");
  uart1_raw_write_u32_dec(vnd_get_stream_recovery_count());
  uart1_raw_write_str(" usb_force_idle=");
  uart1_raw_write_u32_dec(vnd_get_stream_force_idle_count());
  uart1_raw_write_str(" usb_error=");
  uart1_raw_write_u32_dec(vnd_get_last_error());
  uart1_raw_write_str(" power=");
  uart1_raw_write_u32_dec((uint32_t)optic_tx_get_power());
  uart1_raw_write_str(" hold_ds=");
  uart1_raw_write_u32_dec((uint32_t)optic_sensor_get_hold_deciseconds());
  uart1_raw_write_str(" rx=");
  uart1_raw_write_u32_dec((uint32_t)optic_sensor_get_state());
  uart1_raw_write_str(" activity_hold=");
  uart1_raw_write_u32_dec((uint32_t)optic_sensor_state_public);
  uart1_raw_write_str(" pd0=");
  uart1_raw_write_u32_dec((uint32_t)pd0_level);
  uart1_raw_write_str(" any=");
  uart1_raw_write_u32_dec((uint32_t)optic_any_sensor_active());
  uart1_raw_write_str(" master_rx=");
  uart1_raw_write_u32_dec((uint32_t)rs485_status_master_optic_active());
  uart1_raw_write_str(" active_status_count=");
  uart1_raw_write_u32_dec((uint32_t)active_status_count);
  uart1_raw_write_str(" sync_seen_mask=0x");
  uart1_raw_write_u32_hex(seen_mask);
  uart1_raw_write_str(" optic_mask=0x");
  uart1_raw_write_u32_hex(optic_mask);
  uart1_raw_write_str(" local_status=0x");
  uart1_raw_write_u8_hex(local_status);
  uart1_raw_write_str(" local_optic_bit=");
  uart1_raw_write_u32_dec((uint32_t)(((local_status & RS485_STATUS_OPTIC_BIT) != 0u) ? 1u : 0u));
  uart1_raw_write_str(" master_status=0x");
  uart1_raw_write_u8_hex(master_status);
  uart1_raw_write_str(" master_age_ms=");
  uart1_raw_write_u32_dec((uint32_t)master_age_ms);
  uart1_raw_write_str(" master_flags=0x");
  uart1_raw_write_u8_hex(master_flags);
  uart1_raw_write_str(" sensor_evt_tx=");
  uart1_raw_write_u32_dec(rs485_sensor_event_tx_count);
  uart1_raw_write_str(" sensor_evt_rx=");
  uart1_raw_write_u32_dec(rs485_sensor_event_rx_count);
  uart1_raw_write_str(" sensor_evt_overflow=");
  uart1_raw_write_u32_dec(rs485_sensor_event_queue_overflow);
  uart1_raw_write_str(" ws_busy=");
  uart1_raw_write_u32_dec((uint32_t)ws2812_spi_is_busy());
  uart1_raw_write_str(" ws_frames=");
  uart1_raw_write_u32_dec(ws2812_spi_get_frame_count());
  uart1_raw_write_str(" ws_recoveries=");
  uart1_raw_write_u32_dec(ws2812_spi_get_recovery_count());
  uart1_raw_write_str(" ws_start_delay_max_us=");
  uart1_raw_write_u32_dec(ws2812_spi_get_phase_start_delay_max_us());
  uart1_raw_write_str(" ws_wire_us=");
  uart1_raw_write_u32_dec(ws2812_spi_get_wire_time_us());
  uart1_raw_write_str(" ws_late_skip=");
  uart1_raw_write_u32_dec(ws2812_spi_get_phase_late_skip_count());
  uart1_raw_write_str(" usb_evt_enq=");
  uart1_raw_write_u32_dec(usb_event_diag.enqueued);
  uart1_raw_write_str(" usb_evt_ok=");
  uart1_raw_write_u32_dec(usb_event_diag.tx_ok);
  uart1_raw_write_str(" usb_evt_cplt=");
  uart1_raw_write_u32_dec(usb_event_diag.tx_cplt);
  uart1_raw_write_str(" usb_evt_drop=");
  uart1_raw_write_u32_dec(usb_event_diag.dropped);
  uart1_raw_write_str(" usb_evt_q=");
  uart1_raw_write_u32_dec((uint32_t)usb_event_diag.queued);
  uart1_raw_write_str(" usb_evt_last=0x");
  uart1_raw_write_u8_hex(usb_event_diag.last_enqueue_type);
  uart1_raw_write_str("\r\n");
}

#if MAIN_LED_INDICATION_ENABLE
static inline void LED_ON(void) { HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_SET); }
static inline void LED_OFF(void) { HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_RESET); }
#else
static inline void LED_ON(void) { HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_RESET); }
static inline void LED_OFF(void) { HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_RESET); }
#endif

#if ENABLE_UART_HEARTBEAT
#define PROG(ch) uart1_raw_putc((ch))
#else
#define PROG(ch) do{}while(0)
#endif

#define TIM2_WINDOW_GUARD_US 400u

#ifndef FORCE_BL_GPIO
#define FORCE_BL_GPIO 0
#endif

#define OPTIC_TX_PWM_TARGET_HZ 38000u
#define OPTIC_INPUT_FILTER_MS 2000u
#define OPTIC_TX_BURST_ON_CYCLES 40u
#define OPTIC_TX_BURST_OFF_CYCLES 8u
#define OPTIC_TX_BURST_PERIOD (OPTIC_TX_BURST_ON_CYCLES + OPTIC_TX_BURST_OFF_CYCLES)
#define OPTIC_TX_POWER_MAX 255u
#define OPTIC_ACTIVE_HOLD_DEFAULT_DS 30u
#define OPTIC_ACTIVE_HOLD_MAX_DS 600u
#define WS2812_TEST_BUTTON_DEBOUNCE_MS 30u

#ifndef BL_ACTIVE_LOW
#define BL_ACTIVE_LOW 1
#endif
#if BL_ACTIVE_LOW
  #define BL_ON() HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_RESET)
  #define BL_OFF() HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_SET)
#else
  #define BL_ON() HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_SET)
  #define BL_OFF() HAL_GPIO_WritePin(LCD_Led_GPIO_Port, LCD_Led_Pin, GPIO_PIN_RESET)
#endif

static volatile uint32_t uart1_led_off_tick = 0u;
static uint8_t uart1_rx_byte = 0u;
static volatile uint32_t uart1_rx_count = 0u;
static volatile uint32_t uart1_last_rx_ms = 0u;
static volatile ws2812_pattern_t g_ws2812_test_pattern = WS2812_PATTERN_OFF;
#define UART1_RX_RING_SZ 128
static uint8_t uart1_rx_ring[UART1_RX_RING_SZ];
static volatile uint16_t uart1_rx_ring_wr = 0u;
static volatile uint16_t uart1_rx_ring_rd = 0u;
#define UART1_CMD_MAX 96
static char uart1_cmd_buf[UART1_CMD_MAX];
static uint16_t uart1_cmd_len = 0u;
static volatile uint8_t optic_tx_power_level = OPTIC_TX_POWER_MAX;
static volatile uint8_t optic_tx_started = 0u;

static inline void uart1_rx_led_pulse(void)
{
#if MAIN_LED_INDICATION_ENABLE
  LED_ON();
  uart1_led_off_tick = HAL_GetTick() + 100u;
#else
  uart1_led_off_tick = 0u;
#endif
}

uint8_t dynamic_led_set_pattern(uint8_t pattern_id)
{
#if MAIN_WS2812_STATUS_ENABLE
  ws2812_pattern_t pattern = (ws2812_pattern_t)pattern_id;

  if ((uint32_t)pattern >= (uint32_t)WS2812_PATTERN_COUNT) {
    pattern = WS2812_PATTERN_OFF;
  }

  g_ws2812_test_pattern = pattern;
  return (uint8_t)g_ws2812_test_pattern;
#else
  (void)pattern_id;
  g_ws2812_test_pattern = WS2812_PATTERN_OFF;
  return (uint8_t)g_ws2812_test_pattern;
#endif
}

uint8_t dynamic_led_get_pattern(void)
{
  return (uint8_t)g_ws2812_test_pattern;
}

uint8_t optic_sensor_get_state(void)
{
  /* This is the one canonical optical-sensor signal used by every consumer:
     HIGH immediately follows PD0 HIGH and remains HIGH for the configured
     hold time after the last observed HIGH. PD0 LOW never starts/retriggers
     the hold timer. */
  return optic_sensor_state_public;
}

static uint8_t rs485_sensor_event_set_local(uint8_t sensor_index, uint8_t active)
{
  uint32_t primask = __get_PRIMASK();
  uint32_t now_ms = HAL_GetTick();
  uint16_t bit;
  uint8_t write_index;
  uint8_t next_index;

  if (sensor_index >= 16u) {
    return 0u;
  }
  active = (active != 0u) ? 1u : 0u;
  bit = (uint16_t)(1u << sensor_index);
  __disable_irq();
  if ((((rs485_sensor_local_bits & bit) != 0u) ? 1u : 0u) != active) {
    if (active != 0u) {
      rs485_sensor_local_bits |= bit;
    } else {
      rs485_sensor_local_bits &= (uint16_t)~bit;
    }
    rs485_sensor_local_last_ms = now_ms;
    rs485_sensor_local_last_index = sensor_index;

    if (rs485_local_node_id_assigned != 0u) {
      write_index = rs485_sensor_event_queue_write;
      next_index = (uint8_t)((write_index + 1u) %
                             RS485_SENSOR_EVENT_QUEUE_CAPACITY);
      if (next_index != rs485_sensor_event_queue_read) {
        rs485_sensor_event_queue_index[write_index] = sensor_index;
        rs485_sensor_event_queue_value[write_index] = active;
        __DMB();
        rs485_sensor_event_queue_write = next_index;
        rs485_sensor_event_next_cycle = rs485_sensor_event_cycle;
      } else {
        rs485_sensor_event_queue_overflow++;
      }
    }
  }
  if (primask == 0u) {
    __enable_irq();
  }
  return active;
}

static void rs485_sensor_queue_active_snapshot(void)
{
  rs485_sensor_event_queue_read = 0u;
  rs485_sensor_event_queue_write = 0u;
  rs485_sensor_event_current_valid = 0u;
  rs485_sensor_event_retries = 0u;
  for (uint8_t sensor_index = 0u; sensor_index < 16u; sensor_index++) {
    uint16_t bit = (uint16_t)(1u << sensor_index);
    uint8_t write_index;
    uint8_t next_index;

    if ((rs485_sensor_local_bits & bit) == 0u) {
      continue;
    }
    write_index = rs485_sensor_event_queue_write;
    next_index = (uint8_t)((write_index + 1u) %
                           RS485_SENSOR_EVENT_QUEUE_CAPACITY);
    if (next_index == rs485_sensor_event_queue_read) {
      break;
    }
    rs485_sensor_event_queue_index[write_index] = sensor_index;
    rs485_sensor_event_queue_value[write_index] = 1u;
    rs485_sensor_event_queue_write = next_index;
  }
  rs485_sensor_event_next_cycle = rs485_sensor_event_cycle;
}

uint8_t rs485_status_set_controlled_sensor(uint8_t sensor_index, uint8_t active)
{
  uint8_t applied = rs485_sensor_event_set_local(sensor_index, active);
  if (applied != 0u) {
    need_usb_status_refresh = 1u;
  }
  return applied;
}

static uint8_t rs485_sensor_event_prepare(uint8_t *sensor_index,
                                          uint8_t *active)
{
  if ((sensor_index == NULL) || (active == NULL)) {
    return 0u;
  }

  if (rs485_sensor_event_current_valid == 0u) {
    uint8_t read_index = rs485_sensor_event_queue_read;
    if (read_index == rs485_sensor_event_queue_write) {
      return 0u;
    }
    rs485_sensor_event_current_index =
        rs485_sensor_event_queue_index[read_index];
    rs485_sensor_event_current_value =
        rs485_sensor_event_queue_value[read_index];
    rs485_sensor_event_retries = RS485_SENSOR_EVENT_RETRIES;
    rs485_sensor_event_next_cycle = rs485_sensor_event_cycle;
    rs485_sensor_event_current_valid = 1u;
  }

  if ((int32_t)(rs485_sensor_event_cycle -
                rs485_sensor_event_next_cycle) < 0) {
    return 0u;
  }

  *sensor_index = rs485_sensor_event_current_index;
  *active = rs485_sensor_event_current_value;
  return 1u;
}

static void rs485_sensor_event_commit_tx(void)
{
  rs485_sensor_event_tx_count++;
  if (rs485_sensor_event_retries > 1u) {
    uint32_t retry_slots =
        1u + ((uint32_t)rs485_local_node_id &
              (RS485_SENSOR_EVENT_RETRY_SLOTS - 1u));
    rs485_sensor_event_retries--;
    rs485_sensor_event_next_cycle = rs485_sensor_event_cycle + retry_slots;
    return;
  }

  if (rs485_sensor_event_queue_read != rs485_sensor_event_queue_write) {
    rs485_sensor_event_queue_read =
        (uint8_t)((rs485_sensor_event_queue_read + 1u) %
                  RS485_SENSOR_EVENT_QUEUE_CAPACITY);
  }
  rs485_sensor_event_retries = 0u;
  rs485_sensor_event_current_valid = 0u;
  rs485_sensor_event_next_cycle = rs485_sensor_event_cycle + 1u;
}

static void rs485_sensor_note_remote(uint8_t device_id,
                                     uint8_t sensor_index,
                                     uint8_t active,
                                     uint32_t now_ms)
{
  uint8_t index;
  uint16_t bit;
  uint16_t before;

  if ((device_id > RS485_DISCOVERY_MAX_ID) ||
      (sensor_index >= 16u)) {
    return;
  }

  index = device_id;
  bit = (uint16_t)(1u << sensor_index);
  before = rs485_sensor_peer_bits[index];
  if (active != 0u) {
    rs485_sensor_peer_bits[index] |= bit;
  } else {
    rs485_sensor_peer_bits[index] &= (uint16_t)~bit;
  }
  if (before != rs485_sensor_peer_bits[index]) {
    rs485_sensor_peer_last_ms[index] = now_ms;
    rs485_sensor_peer_last_index[index] = sensor_index;
    need_usb_status_refresh = 1u;
  }
}

uint8_t rs485_sensor_get_snapshot(uint8_t device_id,
                                  rs485_sensor_snapshot_t *out)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t primask;
  uint32_t now_ms = HAL_GetTick();
  uint8_t local_request = (device_id == 0xFFu) ? 1u : 0u;
  uint8_t target_id = local_request ? rs485_local_node_id : device_id;

  if ((out == NULL) ||
      ((local_request == 0u) && (target_id > RS485_DISCOVERY_MAX_ID))) {
    return 0u;
  }

  memset(out, 0, sizeof(*out));
  out->device_id = target_id;
  primask = __get_PRIMASK();
  __disable_irq();
  if (local_request != 0u) {
    out->flags = RS485_SENSOR_FLAG_VALID | RS485_SENSOR_FLAG_LOCAL;
    out->sensor_bits = rs485_sensor_local_bits;
    out->last_changed_index = rs485_sensor_local_last_index;
    out->last_change_ms = rs485_sensor_local_last_ms;
    if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
      out->flags |= RS485_SENSOR_FLAG_MASTER;
    }
  } else {
    uint32_t peer_ms = rs485_status_peer_last_ms[target_id];
    uint32_t master_ms = rs485_status_master_last_ms;
    uint8_t is_master =
        (uint8_t)(((master_ms != 0u) &&
                   (target_id == rs485_status_master_node_id)) ? 1u : 0u);

    if ((peer_ms != 0u) || (is_master != 0u)) {
      out->flags |= RS485_SENSOR_FLAG_VALID;
      out->sensor_bits = rs485_sensor_peer_bits[target_id];
      out->last_changed_index = rs485_sensor_peer_last_index[target_id];
      out->last_change_ms = rs485_sensor_peer_last_ms[target_id];
      if (((peer_ms != 0u) &&
           ((now_ms - peer_ms) <= RS485_STATUS_PEER_HOLD_MS)) ||
          ((is_master != 0u) &&
           ((now_ms - master_ms) <= RS485_STATUS_PEER_HOLD_MS))) {
        out->flags |= RS485_SENSOR_FLAG_RECENT;
      }
      if (is_master != 0u) {
        out->flags |= RS485_SENSOR_FLAG_MASTER;
      }
    }
  }
  if (primask == 0u) {
    __enable_irq();
  }
  return 1u;
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
  need_usb_status_refresh = 1u;
  return optic_active_hold_deciseconds;
}

uint16_t optic_sensor_get_hold_deciseconds(void)
{
  return optic_active_hold_deciseconds;
}

uint8_t rs485_status_set_det_adc_bits(uint8_t bits)
{
  uint8_t applied = (uint8_t)(bits & 0x03u);

  if (rs485_det_adc_bits != applied) {
    uint8_t changed = (uint8_t)(rs485_det_adc_bits ^ applied);
    rs485_det_adc_bits = applied;
    if ((changed & 0x01u) != 0u) {
      (void)rs485_sensor_event_set_local(1u, applied & 0x01u);
    }
    if ((changed & 0x02u) != 0u) {
      (void)rs485_sensor_event_set_local(2u, applied & 0x02u);
    }
    need_usb_status_refresh = 1u;
  }
  return rs485_det_adc_bits;
}

uint8_t rs485_status_get_det_adc_bits(void)
{
  return (uint8_t)(rs485_det_adc_bits & 0x03u);
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
  uint8_t input_level =
      (HAL_GPIO_ReadPin(OPTIC_RX_GPIO_Port, OPTIC_RX_Pin) == GPIO_PIN_SET)
          ? 1u
          : 0u;

  optic_input_level_public = input_level;

  if (input_level != 0u) {
    /* A HIGH level starts/retriggers the hold. A sustained HIGH therefore
       remains active indefinitely and the hold starts only after it goes LOW. */
    optic_last_high_ms = now_ms;
    if (optic_sensor_state_public == 0u) {
      optic_sensor_state_public = 1u;
      (void)rs485_sensor_event_set_local(0u, 1u);
      need_usb_status_refresh = 1u;
    }
  } else if ((optic_sensor_state_public != 0u) &&
             ((now_ms - optic_last_high_ms) >= hold_ms)) {
    optic_sensor_state_public = 0u;
    (void)rs485_sensor_event_set_local(0u, 0u);
    need_usb_status_refresh = 1u;
  }
}

static void optic_tx_apply_runtime_pattern(void)
{
  uint32_t period_ticks;
  uint32_t max_compare;
  uint32_t compare;
  uint8_t output_power = (uint8_t)optic_tx_power_level;

  period_ticks = __HAL_TIM_GET_AUTORELOAD(&htim1) + 1u;
  if (period_ticks < 2u) {
    period_ticks = 2u;
  }

  max_compare = period_ticks / 2u;
  if (max_compare == 0u) {
    max_compare = 1u;
  }

  compare = ((uint32_t)output_power * max_compare) / OPTIC_TX_POWER_MAX;
  if ((output_power != 0u) && (compare == 0u)) {
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

void optic_tx_refresh_enable(void)
{
  optic_tx_apply_runtime_pattern();
}

#if MAIN_WS2812_STATUS_ENABLE
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
#endif

static void ws2812_test_button_service(uint32_t now_ms)
{
#if !MAIN_WS2812_STATUS_ENABLE
  (void)now_ms;
  return;
#else
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
#endif
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

  if (HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_1) != HAL_OK) {
    printf("[OPTIC][ERR] TIM3 gate start failed\r\n");
    Error_Handler();
  }

  __HAL_TIM_MOE_ENABLE(&htim1);
  optic_tx_apply_runtime_pattern();

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
#if RS485_SYNC_UART_PERIODIC_LOG_ENABLE
  rs485_sync_control_period_accum_ticks += sync_tim5_period_ticks;
  if ((rs485_last_sync_edge_kind & 1u) == (RS485_SYNC_CONTROL_EDGE_KIND & 1u)) {
    rs485_sync_control_period_ticks = rs485_sync_control_period_accum_ticks;
    rs485_sync_control_period_accum_ticks = 0u;
    rs485_sync_control_tim5_ticks = sync_tim5_period_ticks;
    rs485_sync_control_buf_phase = sync_tim5_cnt_at_buffer;
    rs485_sync_control_buf_seq = sync_tim5_buffer_phase_seq;
    rs485_sync_control_tim15_cap = sync_tim15_cnt_at_pd5;
    rs485_sync_control_edge_count++;
  }
#endif
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

    /* Compare edge-kind, not the raw GPIO level. The UART byte is observed
       after the master's edge, so equal kinds mean anti-phase and different
       kinds mean in-phase. Near the circular buffer boundary an otherwise
       healthy PLL can briefly see the master's edge just before the local
       edge. Do not publish that short crossing as ANTI_PHASE: the existing
       200 clean-packet recovery qualifier is also the public-state debounce. */
    if (rs485_sync_relation_score <= -RS485_SYNC_RELATION_CONFIRM_SCORE) {
      rs485_sync_phase_relation = RS485_SYNC_RELATION_IN_PHASE;
    } else if ((rs485_sync_phase_relation != RS485_SYNC_RELATION_IN_PHASE) &&
               (rs485_sync_phase_relation != RS485_SYNC_RELATION_ANTI_PHASE)) {
      rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
    }

    if ((RS485_SYNC_ANTI_PHASE_RECOVERY_ENABLE != 0u) &&
        (rs485_sync_relation_score >= RS485_SYNC_RELATION_CONFIRM_SCORE)) {
      uint32_t now_ms = HAL_GetTick();
      uint8_t master_fresh =
          (uint8_t)(((rs485_status_master_last_ms != 0u) &&
                     ((now_ms - rs485_status_master_last_ms) <=
                      RS485_SYNC_ANTI_PHASE_MASTER_FRESH_MS)) ? 1u : 0u);

      if ((master_fresh != 0u) &&
          (rs485_uart_sync_error_count ==
           rs485_anti_phase_sync_error_snapshot)) {
        rs485_anti_phase_recovery_active = 1u;
        if (rs485_anti_phase_recovery_packets < 255u) {
          rs485_anti_phase_recovery_packets++;
        }
        if (rs485_anti_phase_recovery_packets >=
            RS485_SYNC_ANTI_PHASE_CONFIRM_PACKETS) {
          rs485_sync_phase_relation = RS485_SYNC_RELATION_ANTI_PHASE;
          rs485_anti_phase_recovery_request = 1u;
        }
      } else {
        rs485_anti_phase_sync_error_snapshot =
            rs485_uart_sync_error_count;
        rs485_anti_phase_recovery_active = 0u;
        rs485_anti_phase_recovery_packets = 0u;
        rs485_anti_phase_recovery_request = 0u;
      }
    } else {
      rs485_anti_phase_sync_error_snapshot = rs485_uart_sync_error_count;
      rs485_anti_phase_recovery_active = 0u;
      rs485_anti_phase_recovery_packets = 0u;
      rs485_anti_phase_recovery_request = 0u;
      rs485_sync_restart_request = 0u;
      if ((rs485_sync_phase_relation == RS485_SYNC_RELATION_ANTI_PHASE) &&
          (rs485_phase_slew_active == 0u)) {
        rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
      }
    }
  } else {
    rs485_anti_phase_sync_error_snapshot = rs485_uart_sync_error_count;
    rs485_sync_relation_score = 0;
    rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
    rs485_anti_phase_recovery_active = 0u;
    rs485_anti_phase_recovery_packets = 0u;
    rs485_anti_phase_recovery_request = 0u;
    rs485_sync_restart_request = 0u;
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
  vnd_sync_on_edge();
}

static uint32_t rs485_compute_uid_mix(void)
{
  uint32_t uid0 = rs485_local_uid_words[0];
  uint32_t uid1 = rs485_local_uid_words[1];
  uint32_t uid2 = rs485_local_uid_words[2];

  return uid0 ^ uid1 ^ uid2 ^ (uid0 >> 16) ^ (uid1 >> 11) ^ (uid2 >> 7);
}

static uint64_t rs485_compute_short_id36(void)
{
  uint64_t hash = 1469598103934665603ULL;

  for (uint32_t index = 0u; index < sizeof(rs485_local_uid_bytes); index++) {
    hash ^= (uint64_t)rs485_local_uid_bytes[index];
    hash *= 1099511628211ULL;
  }
  hash ^= (hash >> 36);
  hash &= 0xFFFFFFFFFULL;
  return (hash != 0u) ? hash : 1u;
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
  rs485_local_short_id36 = rs485_compute_short_id36();
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

static uint8_t rs485_role_local_is_selected_master(void)
{
  return (uint8_t)((rs485_role_master_valid != 0u) &&
                   (memcmp(rs485_role_master_uid,
                           rs485_local_uid_bytes,
                           sizeof(rs485_role_master_uid)) == 0));
}

static uint8_t rs485_role_slave_uid_matches(const uint8_t uid[12])
{
  size_t compare_len = (rs485_role_slave_uid_prefix_only != 0u) ? 8u : 12u;

  return (uint8_t)((rs485_role_slave_valid != 0u) &&
                   (memcmp(rs485_role_slave_uid, uid, compare_len) == 0));
}

static uint8_t rs485_role_local_is_selected_slave(void)
{
  return rs485_role_slave_uid_matches(rs485_local_uid_bytes);
}

static int8_t rs485_role_compare_time(uint32_t lhs_s,
                                      uint16_t lhs_ms,
                                      uint32_t rhs_s,
                                      uint16_t rhs_ms)
{
  if (lhs_s < rhs_s) {
    return -1;
  }
  if (lhs_s > rhs_s) {
    return 1;
  }
  if (lhs_ms < rhs_ms) {
    return -1;
  }
  if (lhs_ms > rhs_ms) {
    return 1;
  }
  return 0;
}

/* A direct RPI assignment is a user decision and must win on the first
   command even if Flash contains an assignment timestamp slightly in the
   future (clock correction, delayed command or an earlier host). Preserve the
   timestamp ordering used on RS-485 by advancing the accepted host time to one
   millisecond after the newest locally known role event when necessary. */
static void rs485_role_make_host_time_newest(uint32_t *unix_s,
                                             uint16_t *millis)
{
  uint32_t newest_s = 0u;
  uint16_t newest_ms = 0u;

  if ((unix_s == NULL) || (millis == NULL)) {
    return;
  }

  if (rs485_role_master_valid != 0u) {
    newest_s = rs485_role_master_assigned_unix_s;
    newest_ms = rs485_role_master_assigned_millis;
  }
  if ((rs485_role_slave_epoch_valid != 0u) &&
      ((newest_s == 0u) ||
       (rs485_role_compare_time(rs485_role_slave_assigned_unix_s,
                                rs485_role_slave_assigned_millis,
                                newest_s,
                                newest_ms) > 0))) {
    newest_s = rs485_role_slave_assigned_unix_s;
    newest_ms = rs485_role_slave_assigned_millis;
  }

  if ((newest_s != 0u) &&
      (rs485_role_compare_time(*unix_s, *millis,
                               newest_s, newest_ms) <= 0)) {
    *unix_s = newest_s;
    *millis = newest_ms;
    if (*millis < 999u) {
      (*millis)++;
    } else if (*unix_s < 0xFFFFFFFFu) {
      (*unix_s)++;
      *millis = 0u;
    }
  }
}

static void rs485_role_clear_selected_slave_at(uint32_t unix_s,
                                                uint16_t millis)
{
  rs485_role_slave_epoch_valid = 1u;
  rs485_role_slave_valid = 0u;
  rs485_role_slave_uid_prefix_only = 0u;
  rs485_role_slave_assigned_unix_s = unix_s;
  rs485_role_slave_assigned_millis = millis;
  rs485_role_selected_slave_node_id = 0u;
  memset(rs485_role_slave_uid, 0, sizeof(rs485_role_slave_uid));
}

static void rs485_role_reset_local_assignment_epoch(void)
{
  /* A role change starts a fresh runtime/status epoch, but the stable node ID
     belongs to the physical board and must never be cleared or renumbered. */
  rs485_role_enum_waiting_assignment = 0u;
  rs485_role_slave_id_high_water = 0u;
  rs485_status_slave_reply_div_counter = 0u;
  rs485_status_seen_mask = 0u;
  rs485_status_cycle_count = 0u;
  rs485_status_slot_response_seen = 0u;
  rs485_status_slot_response_first = 0u;
  rs485_status_slot_response_second = 0u;
  rs485_slave_count_estimate = 0u;
  rs485_role_enum_state = 0u;
  rs485_role_enum_collect_pass = 0u;
  rs485_role_enum_target_id = 0u;
  rs485_role_enum_candidate_valid = 0u;
  rs485_role_enum_assign_retries = 0u;
  rs485_role_enum_ack_received = 0u;
  rs485_role_enum_next_ms = HAL_GetTick() + RS485_ROLE_BUS_QUIET_MS;
  rs485_role_enum_stable_mask = 0u;
  rs485_role_enum_stable_since_ms = HAL_GetTick();
  rs485_role_enum_ack_tx_pending = 0u;
  rs485_role_enum_ack_tx_id = 0u;
  rs485_status_assignment_reset();
  rs485_node_claim_reset_registry();
}

static void rs485_role_mark_persist_dirty(void)
{
  rs485_role_persist_dirty = 1u;
  rs485_role_persist_dirty_since_ms = HAL_GetTick();
}

static void rs485_role_mark_persist_dirty_immediate(void)
{
  uint32_t now_ms = HAL_GetTick();

  /* A host role command is authoritative and must survive even if the role
     change immediately starts a long slave-enumeration pass. Queue the Flash
     write here instead of waiting for role-service/debounce. The actual Flash
     operation still runs from Vendor_Stream_Task(), never from the USB IRQ. */
  if (rs485_role_persist_save_pending == 0u) {
    rs485_role_persist_dirty = 0u;
    rs485_role_persist_save_pending = 1u;
    rs485_role_persist_save_ok_snapshot = vnd_dc_save_ok_count;
    rs485_role_persist_last_request_ms = now_ms;
  } else {
    /* A previous snapshot may already be inside the Flash writer. Keep one
       dirty generation so the newest role is written after it completes. */
    rs485_role_persist_dirty = 1u;
    rs485_role_persist_dirty_since_ms =
        now_ms - RS485_ROLE_PERSIST_DEBOUNCE_MS;
  }
  vnd_dc_request_save_to_flash();
}

void rs485_role_persist_export(rs485_role_persist_state_t *out)
{
  uint32_t primask = 0u;

  if (out == NULL) {
    return;
  }

  primask = __get_PRIMASK();
  __disable_irq();
  memset(out, 0, sizeof(*out));
  if (rs485_role_master_valid != 0u) {
    out->flags |= RS485_ROLE_PERSIST_MASTER_VALID;
    out->master_assigned_unix_s = rs485_role_master_assigned_unix_s;
    out->master_assigned_millis = rs485_role_master_assigned_millis;
    memcpy(out->master_uid,
           rs485_role_master_uid,
           sizeof(out->master_uid));
  }
  if (rs485_role_local_slave_persisted != 0u) {
    out->flags |= RS485_ROLE_PERSIST_LOCAL_SLAVE;
  }
  if (rs485_role_slave_epoch_valid != 0u) {
    out->flags |= RS485_ROLE_PERSIST_SLAVE_EPOCH;
    out->slave_assigned_unix_s = rs485_role_slave_assigned_unix_s;
    out->slave_assigned_millis = rs485_role_slave_assigned_millis;
    if (rs485_role_slave_valid != 0u) {
      out->flags |= RS485_ROLE_PERSIST_SLAVE_VALID;
      memcpy(out->slave_uid,
             rs485_role_slave_uid,
             sizeof(out->slave_uid));
    }
    if (rs485_role_slave_uid_prefix_only != 0u) {
      out->flags |= RS485_ROLE_PERSIST_SLAVE_UID64;
    }
    out->selected_slave_node_id = rs485_role_selected_slave_node_id;
  }
  if ((rs485_local_node_id_assigned != 0u) &&
      (rs485_local_node_id <= RS485_DISCOVERY_MAX_ID)) {
    out->flags |= RS485_ROLE_PERSIST_NODE_VALID;
    out->node_id = rs485_local_node_id;
  }
  /* Kept in the on-Flash v2 layout for backward compatibility only. */
  out->slave_id_high_water = 0u;
  if (primask == 0u) {
    __enable_irq();
  }
}

void rs485_role_persist_import(const rs485_role_persist_state_t *state)
{
  uint8_t old_id = rs485_local_node_id;

  if (state == NULL) {
    return;
  }

  rs485_role_master_valid = 0u;
  rs485_role_master_assigned_unix_s = 0u;
  rs485_role_master_assigned_millis = 0u;
  memset(rs485_role_master_uid, 0, sizeof(rs485_role_master_uid));
  if (((state->flags & RS485_ROLE_PERSIST_MASTER_VALID) != 0u) &&
      (state->master_assigned_unix_s != 0u) &&
      (state->master_assigned_millis < 1000u)) {
    memcpy(rs485_role_master_uid,
           state->master_uid,
           sizeof(rs485_role_master_uid));
    rs485_role_master_assigned_unix_s = state->master_assigned_unix_s;
    rs485_role_master_assigned_millis = state->master_assigned_millis;
    rs485_role_master_valid = 1u;
  }

  rs485_role_slave_epoch_valid = 0u;
  rs485_role_slave_valid = 0u;
  rs485_role_slave_uid_prefix_only = 0u;
  rs485_role_slave_assigned_unix_s = 0u;
  rs485_role_slave_assigned_millis = 0u;
  rs485_role_selected_slave_node_id = 0u;
  memset(rs485_role_slave_uid, 0, sizeof(rs485_role_slave_uid));
  if (((state->flags & RS485_ROLE_PERSIST_SLAVE_EPOCH) != 0u) &&
      (state->slave_assigned_unix_s != 0u) &&
      (state->slave_assigned_millis < 1000u)) {
    rs485_role_slave_epoch_valid = 1u;
    rs485_role_slave_assigned_unix_s = state->slave_assigned_unix_s;
    rs485_role_slave_assigned_millis = state->slave_assigned_millis;
    if ((state->flags & RS485_ROLE_PERSIST_SLAVE_VALID) != 0u) {
      rs485_role_slave_valid = 1u;
      rs485_role_slave_uid_prefix_only =
          ((state->flags & RS485_ROLE_PERSIST_SLAVE_UID64) != 0u) ? 1u : 0u;
      memcpy(rs485_role_slave_uid,
             state->slave_uid,
             sizeof(rs485_role_slave_uid));
      if (state->selected_slave_node_id <= RS485_DISCOVERY_MAX_ID) {
        rs485_role_selected_slave_node_id = state->selected_slave_node_id;
      }
    }
  }

  rs485_role_local_slave_persisted =
      ((state->flags & RS485_ROLE_PERSIST_LOCAL_SLAVE) != 0u) ? 1u : 0u;
  if ((rs485_role_local_slave_persisted == 0u) &&
      (((rs485_role_master_valid != 0u) &&
        (rs485_role_local_is_selected_master() == 0u)) ||
       (rs485_role_local_is_selected_slave() != 0u))) {
    /* Migrate old network-wide records: a stored remote MASTER or a locally
       selected legacy sensor-SLAVE both mean that this board was a SLAVE. */
    rs485_role_local_slave_persisted = 1u;
  }

  rs485_local_node_id = 0u;
  rs485_local_node_id_assigned = 0u;
  rs485_role_enum_waiting_assignment = 0u;
  rs485_role_last_confirmed_node_id = 0u;
  if (((state->flags & RS485_ROLE_PERSIST_NODE_VALID) != 0u) &&
      (state->node_id <= RS485_DISCOVERY_MAX_ID)) {
    rs485_local_node_id = state->node_id;
    rs485_local_node_id_assigned = 1u;
    rs485_role_last_confirmed_node_id = state->node_id;
  }
  rs485_role_slave_id_high_water = 0u;
  if (rs485_role_local_is_selected_slave() != 0u) {
    memcpy(rs485_role_slave_uid,
           rs485_local_uid_bytes,
           sizeof(rs485_role_slave_uid));
    rs485_role_slave_uid_prefix_only = 0u;
    rs485_role_selected_slave_node_id = rs485_local_node_id;
  }
  rs485_identity_note_local_id_change(old_id, rs485_local_node_id);
  rs485_node_claim_reset_registry();
  rs485_node_claim_schedule(HAL_GetTick(), RS485_NODE_CLAIM_RETRIES);
  rs485_role_persist_dirty = 0u;
  rs485_role_persist_save_pending = 0u;
}

uint8_t rs485_role_assign_master_from_host(uint64_t assigned_unix_ms)
{
  uint64_t assigned_unix_s = assigned_unix_ms / 1000ULL;
  uint32_t unix_s32 = 0u;
  uint16_t millis16 = 0u;

  if ((assigned_unix_ms == 0ULL) ||
      (assigned_unix_s == 0ULL) ||
      (assigned_unix_s > 0xFFFFFFFFULL)) {
    return 0u;
  }

  unix_s32 = (uint32_t)assigned_unix_s;
  millis16 = (uint16_t)(assigned_unix_ms % 1000ULL);
  rs485_role_make_host_time_newest(&unix_s32, &millis16);
  rs485_role_reset_local_assignment_epoch();
  memcpy(rs485_role_master_uid,
         rs485_local_uid_bytes,
         sizeof(rs485_role_master_uid));
  rs485_role_master_assigned_unix_s = unix_s32;
  rs485_role_master_assigned_millis = millis16;
  rs485_role_master_valid = 1u;
  rs485_role_local_slave_persisted = 0u;
  rs485_role_clear_selected_slave_at(unix_s32, millis16);
  rs485_role_boot_listen_active = 0u;
  vnd_sync_apply_network_mode(VND_SYNC_MODE_MASTER);
  rs485_role_mark_persist_dirty_immediate();
  rs485_node_claim_schedule(HAL_GetTick(), RS485_NODE_CLAIM_RETRIES);
  return 1u;
}

uint8_t rs485_role_assign_slave_from_host(uint64_t assigned_unix_ms)
{
  uint64_t assigned_unix_s = assigned_unix_ms / 1000ULL;
  uint32_t unix_s32 = 0u;
  uint16_t millis16 = 0u;

  if ((assigned_unix_ms == 0ULL) ||
      (assigned_unix_s == 0ULL) ||
      (assigned_unix_s > 0xFFFFFFFFULL)) {
    return 0u;
  }

  unix_s32 = (uint32_t)assigned_unix_s;
  millis16 = (uint16_t)(assigned_unix_ms % 1000ULL);
  rs485_role_make_host_time_newest(&unix_s32, &millis16);
  rs485_role_reset_local_assignment_epoch();

  /* SLAVE is a local persistent role, not a special "selected sensor" in the
     group. Clearing MASTER here is what makes a duplicate master visible and
     explicitly repairable by the user/RPI. */
  rs485_role_master_valid = 0u;
  rs485_role_master_assigned_unix_s = 0u;
  rs485_role_master_assigned_millis = 0u;
  memset(rs485_role_master_uid, 0, sizeof(rs485_role_master_uid));
  rs485_role_local_slave_persisted = 1u;
  rs485_role_clear_selected_slave_at(unix_s32, millis16);
  rs485_role_boot_listen_active = 0u;
  vnd_sync_apply_network_mode(VND_SYNC_MODE_SLAVE);
  rs485_role_mark_persist_dirty_immediate();
  rs485_node_claim_schedule(HAL_GetTick(), RS485_NODE_CLAIM_RETRIES);
  return 1u;
}

uint8_t rs485_role_get_local_persisted_mode(uint8_t *mode)
{
  if (mode == NULL) {
    return 0u;
  }
  if (rs485_role_local_is_selected_master() != 0u) {
    *mode = VND_SYNC_MODE_MASTER;
    return 1u;
  }
  if (rs485_role_local_slave_persisted != 0u) {
    *mode = VND_SYNC_MODE_SLAVE;
    return 1u;
  }
  return 0u;
}

static void rs485_role_persist_service(uint32_t now_ms)
{
  if (rs485_role_persist_save_pending != 0u) {
    if (vnd_dc_save_ok_count != rs485_role_persist_save_ok_snapshot) {
      rs485_role_persist_save_pending = 0u;
    } else if ((now_ms - rs485_role_persist_last_request_ms) >= 5000u) {
      rs485_role_persist_save_ok_snapshot = vnd_dc_save_ok_count;
      rs485_role_persist_last_request_ms = now_ms;
      vnd_dc_request_save_to_flash();
    }
  }

  if ((rs485_role_persist_dirty != 0u) &&
      (rs485_role_persist_save_pending == 0u) &&
      ((now_ms - rs485_role_persist_dirty_since_ms) >=
       RS485_ROLE_PERSIST_DEBOUNCE_MS)) {
    rs485_role_persist_dirty = 0u;
    rs485_role_persist_save_pending = 1u;
    rs485_role_persist_save_ok_snapshot = vnd_dc_save_ok_count;
    rs485_role_persist_last_request_ms = now_ms;
    vnd_dc_request_save_to_flash();
  }
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

static uint32_t rs485_status_make_contiguous_mask(uint8_t node_count)
{
  uint32_t mask = 0u;

  if (node_count > RS485_DEVICE_ID_COUNT) {
    node_count = RS485_DEVICE_ID_COUNT;
  }

  for (uint8_t node_id = 0u; node_id < node_count; node_id++) {
    mask |= (1u << node_id);
  }

  return mask;
}

static uint8_t rs485_status_contiguous_count_from_mask(uint32_t mask)
{
  uint8_t count = 0u;

  while ((count < RS485_DEVICE_ID_COUNT) &&
         ((mask & (1u << count)) != 0u)) {
    count++;
  }

  return count;
}

static uint8_t rs485_status_normalize_slave_count(uint8_t mode,
                                                  uint8_t local_id,
                                                  uint8_t raw_count,
                                                  uint32_t raw_mask)
{
  uint8_t count = 0u;

  (void)raw_count;

  if (mode == VND_SYNC_MODE_OFF) {
    return 0u;
  }

  count = rs485_status_contiguous_count_from_mask(raw_mask);
  if (rs485_slave_count_estimate > count) {
    count = rs485_slave_count_estimate;
  }
  if ((mode == VND_SYNC_MODE_SLAVE) &&
      (local_id != 0u) &&
      (local_id > count)) {
    count = local_id;
  }
  if (count > RS485_DISCOVERY_MAX_ID) {
    count = RS485_DISCOVERY_MAX_ID;
  }

  return count;
}

static void rs485_status_set_slave_count_estimate(uint8_t count)
{
  if (count > RS485_DISCOVERY_MAX_ID) {
    count = RS485_DISCOVERY_MAX_ID;
  }

  if (rs485_slave_count_estimate != count) {
    rs485_slave_count_estimate = count;
    need_usb_status_refresh = 1u;
  }
}

static char rs485_identity_hex_digit(uint8_t value)
{
  value &= 0x0Fu;
  return (char)((value < 10u) ? ('0' + value) : ('A' + (value - 10u)));
}

static uint8_t rs485_identity_ip_is_set(const uint8_t ip4[4])
{
  return (uint8_t)(((ip4[0] | ip4[1] | ip4[2] | ip4[3]) != 0u) ? 1u : 0u);
}

static void rs485_identity_clear_row(uint8_t node_id)
{
  uint8_t index = 0u;

  if (node_id >= RS485_IDENT_TABLE_COUNT) {
    return;
  }

  index = node_id;
  memset(rs485_identity_nibbles[index], 0, RS485_IDENT_PAGE_COUNT);
  rs485_identity_seen_page_mask[index] = 0u;
  rs485_identity_last_ms[index] = 0u;
}

static uint8_t rs485_identity_short_rows_equal(uint8_t lhs_id,
                                                uint8_t rhs_id)
{
  if ((lhs_id >= RS485_IDENT_TABLE_COUNT) ||
      (rhs_id >= RS485_IDENT_TABLE_COUNT) ||
      ((rs485_identity_seen_page_mask[lhs_id] &
        RS485_IDENT_SHORT_PAGE_MASK) != RS485_IDENT_SHORT_PAGE_MASK) ||
      ((rs485_identity_seen_page_mask[rhs_id] &
        RS485_IDENT_SHORT_PAGE_MASK) != RS485_IDENT_SHORT_PAGE_MASK)) {
    return 0u;
  }

  return (uint8_t)((memcmp(rs485_identity_nibbles[lhs_id],
                           rs485_identity_nibbles[rhs_id],
                           RS485_IDENT_SHORT_NIBBLES) == 0) ? 1u : 0u);
}

static void rs485_identity_deduplicate_row(uint8_t current_id)
{
  if ((current_id >= RS485_IDENT_TABLE_COUNT) ||
      ((rs485_identity_seen_page_mask[current_id] &
        RS485_IDENT_SHORT_PAGE_MASK) != RS485_IDENT_SHORT_PAGE_MASK)) {
    return;
  }

  for (uint8_t old_id = 0u; old_id < RS485_IDENT_TABLE_COUNT; old_id++) {
    if ((old_id == current_id) ||
        (rs485_identity_short_rows_equal(current_id, old_id) == 0u)) {
      continue;
    }

    /* The local catalog row is authoritative. A theoretical 36-bit hash
       collision must never hide this board; a real local ID change has already
       moved that row to current_id in rs485_identity_note_local_id_change(). */
    if ((rs485_identity_local_catalog_valid != 0u) &&
        (old_id == rs485_identity_local_catalog_id) &&
        (current_id != rs485_identity_local_catalog_id)) {
      continue;
    }

    /* A single STM32 UID may occupy only one live RS-485 address. As soon as
       its identity pages arrive at the new address, remove the former status,
       sensor and identity cache entry instead of waiting for their timeouts. */
    rs485_status_clear_peer_id(old_id);
    if (rs485_identity_uid_dedupe_count < 0xFFFFFFFFu) {
      rs485_identity_uid_dedupe_count++;
    }
    need_usb_status_refresh = 1u;
  }
}

static void rs485_identity_expire_rows(uint32_t now_ms)
{
  static uint32_t last_check_ms = 0u;
  uint8_t expired_any = 0u;

  if ((last_check_ms != 0u) &&
      ((now_ms - last_check_ms) < RS485_IDENT_EXPIRE_CHECK_MS)) {
    return;
  }
  last_check_ms = now_ms;

  for (uint8_t node_id = 0u; node_id < RS485_IDENT_TABLE_COUNT; node_id++) {
    uint32_t primask;
    uint32_t row_last_ms;

    /* The local identity is configuration, not a peer-cache entry. On a
       slave it does not receive its own RS-485 pages, so it must never expire. */
    if ((rs485_identity_local_catalog_valid != 0u) &&
        (node_id == rs485_identity_local_catalog_id)) {
      continue;
    }

    primask = __get_PRIMASK();
    __disable_irq();
    row_last_ms = rs485_identity_last_ms[node_id];
    if ((row_last_ms != 0u) &&
        ((now_ms - row_last_ms) > RS485_IDENT_RECENT_MS)) {
      rs485_identity_clear_row(node_id);
      expired_any = 1u;
    }
    if (primask == 0u) {
      __enable_irq();
    }
  }

  if (expired_any != 0u) {
    need_usb_status_refresh = 1u;
  }
}

static uint8_t rs485_identity_get_local_nibble(uint8_t page)
{
  if (page < RS485_IDENT_SHORT_NIBBLES) {
    uint8_t shift = (uint8_t)((RS485_IDENT_SHORT_NIBBLES - 1u - page) * 4u);
    return (uint8_t)((rs485_local_short_id36 >> shift) & 0x0Fu);
  }

  page = (uint8_t)(page - RS485_IDENT_SHORT_NIBBLES);
  if (page < RS485_IDENT_RPI_NIBBLES) {
    uint8_t shift =
        (uint8_t)((RS485_IDENT_RPI_NIBBLES - 1u - page) * 4u);
    return (uint8_t)((rs485_identity_local_rpi_number >> shift) & 0x0Fu);
  }

  page = (uint8_t)(page - RS485_IDENT_RPI_NIBBLES);
  if (page < RS485_IDENT_IP_NIBBLES) {
    uint8_t byte_index = (uint8_t)(page >> 1);
    uint8_t byte_value = rs485_identity_local_ip4[byte_index];
    if ((page & 1u) == 0u) {
      return (uint8_t)((byte_value >> 4) & 0x0Fu);
    }
    return (uint8_t)(byte_value & 0x0Fu);
  }

  return 0u;
}

static void rs485_identity_store_nibble(uint8_t node_id,
                                        uint8_t page,
                                        uint8_t nibble,
                                        uint32_t now_ms)
{
  uint8_t index = 0u;

  if ((node_id >= RS485_IDENT_TABLE_COUNT) ||
      (page >= RS485_IDENT_PAGE_COUNT)) {
    return;
  }

  index = node_id;
  rs485_identity_nibbles[index][page] = (uint8_t)(nibble & 0x0Fu);
  rs485_identity_seen_page_mask[index] |= (1u << page);
  rs485_identity_last_ms[index] = now_ms;
  if ((rs485_identity_seen_page_mask[index] &
       RS485_IDENT_SHORT_PAGE_MASK) == RS485_IDENT_SHORT_PAGE_MASK) {
    rs485_identity_deduplicate_row(index);
  }
}

static void rs485_identity_store_local_row(uint32_t now_ms)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t catalog_id = rs485_local_node_id;

  (void)vnd_sync_mode_public;

  if ((rs485_local_node_id_assigned == 0u) ||
      (catalog_id >= RS485_IDENT_TABLE_COUNT)) {
    return;
  }

  if ((rs485_identity_local_catalog_valid != 0u) &&
      (rs485_identity_local_catalog_id != catalog_id)) {
    rs485_identity_clear_row(rs485_identity_local_catalog_id);
  }

  for (uint8_t page = 0u; page < RS485_IDENT_PAGE_COUNT; page++) {
    rs485_identity_store_nibble(catalog_id,
                                page,
                                rs485_identity_get_local_nibble(page),
                                now_ms);
  }
  rs485_identity_local_catalog_id = catalog_id;
  rs485_identity_local_catalog_valid = 1u;
}

static void rs485_identity_note_local_id_change(uint8_t old_id, uint8_t new_id)
{
  uint8_t new_catalog_id = new_id;

  (void)old_id;
  if ((rs485_identity_local_catalog_valid != 0u) &&
      (rs485_identity_local_catalog_id != new_catalog_id)) {
    rs485_identity_clear_row(rs485_identity_local_catalog_id);
    rs485_identity_local_catalog_valid = 0u;
  }
  if ((rs485_local_node_id_assigned != 0u) &&
      (new_catalog_id < RS485_IDENT_TABLE_COUNT)) {
    rs485_identity_store_local_row(HAL_GetTick());
  }
  rs485_identity_rescan_requested = 1u;
}

void rs485_identity_set_local_ip4(const uint8_t ip4[4])
{
  uint32_t primask;

  if (ip4 == NULL) {
    return;
  }

  primask = __get_PRIMASK();
  __disable_irq();
  rs485_identity_local_ip4[0] = ip4[0];
  rs485_identity_local_ip4[1] = ip4[1];
  rs485_identity_local_ip4[2] = ip4[2];
  rs485_identity_local_ip4[3] = ip4[3];
  rs485_identity_local_ip_set = rs485_identity_ip_is_set(rs485_identity_local_ip4);
  rs485_identity_store_local_row(HAL_GetTick());
  need_usb_status_refresh = 1u;
  if (primask == 0u) {
    __enable_irq();
  }
}

void rs485_identity_set_local_rpi_info(uint16_t rpi_number,
                                       const uint8_t ip4[4])
{
  uint32_t primask;

  if (ip4 == NULL) {
    return;
  }

  primask = __get_PRIMASK();
  __disable_irq();
  rs485_identity_local_rpi_number = rpi_number;
  memcpy(rs485_identity_local_ip4, ip4, 4u);
  rs485_identity_local_ip_set =
      rs485_identity_ip_is_set(rs485_identity_local_ip4);
  rs485_identity_store_local_row(HAL_GetTick());
  need_usb_status_refresh = 1u;
  if (primask == 0u) {
    __enable_irq();
  }
}

void rs485_identity_request_scan(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t primask = __get_PRIMASK();
  uint32_t now_ms = HAL_GetTick();

  __disable_irq();
  if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
      (rs485_local_node_id_assigned != 0u)) {
    rs485_identity_rescan_requested = 0u;
    rs485_identity_scan_active = 1u;
    rs485_identity_scan_page = 0u;
    rs485_status_master_selector = 0u;
    rs485_identity_self_tx_state = RS485_IDENT_SELF_STATE_PAGE;
    rs485_identity_current_req_active = 0u;
    rs485_identity_current_req_page = 0u;
    rs485_identity_current_req_selector = 0u;
    rs485_identity_master_self_page = 0xFFu;
    rs485_identity_master_self_node_id = 0u;
    rs485_identity_master_self_node_valid = 0u;
    rs485_identity_scan_last_start_ms = now_ms;
  }
  rs485_identity_store_local_row(HAL_GetTick());
  need_usb_status_refresh = 1u;
  if (primask == 0u) {
    __enable_irq();
  }
}

uint8_t rs485_identity_get_snapshot(uint8_t node_id, rs485_identity_snapshot_t *out)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t primask;
  uint8_t local_request = (node_id == 0xFFu) ? 1u : 0u;
  uint8_t local_id = rs485_local_node_id;
  uint8_t target_id = local_request ? local_id : node_id;
  uint32_t now_ms = HAL_GetTick();
  uint32_t mask = 0u;
  uint32_t last_ms = 0u;
  uint16_t flags = 0u;
  uint16_t rpi_number = 0u;
  uint8_t target_live = local_request;
  uint8_t nibbles[RS485_IDENT_PAGE_COUNT];
  uint8_t ip4[4] = {0u, 0u, 0u, 0u};

  if (out == NULL) {
    return 0u;
  }
  memset(out, 0, sizeof(*out));
  memset(nibbles, 0, sizeof(nibbles));
  memset(out->short_id, '?', 9u);
  out->short_id[9] = '\0';

  if ((local_request == 0u) &&
      (target_id >= RS485_IDENT_TABLE_COUNT)) {
    return 0u;
  }

  primask = __get_PRIMASK();
  __disable_irq();
  mask = rs485_identity_seen_page_mask[target_id];
  last_ms = rs485_identity_last_ms[target_id];
  memcpy(nibbles, rs485_identity_nibbles[target_id], sizeof(nibbles));
  if (local_request != 0u) {
    flags |= RS485_IDENTITY_FLAG_LOCAL;
    if (rs485_local_node_id_assigned != 0u) {
      flags |= RS485_IDENTITY_FLAG_DEVICE_ID_ASSIGNED;
    }
    if (rs485_identity_local_ip_set != 0u) {
      flags |= RS485_IDENTITY_FLAG_IP_VALID;
    }
    if (rs485_identity_local_rpi_number != 0u) {
      flags |= RS485_IDENTITY_FLAG_RPI_NUMBER_VALID;
    }
    mask |= RS485_IDENT_ALL_PAGES_MASK;
    for (uint8_t page = 0u; page < RS485_IDENT_PAGE_COUNT; page++) {
      nibbles[page] = rs485_identity_get_local_nibble(page);
    }
    last_ms = now_ms;
  } else {
    uint32_t peer_ms = rs485_status_peer_last_ms[target_id];
    uint32_t master_ms = rs485_status_master_last_ms;

    if (((rs485_local_node_id_assigned != 0u) &&
         (target_id == local_id)) ||
        ((peer_ms != 0u) &&
         ((now_ms - peer_ms) <= RS485_STATUS_PEER_HOLD_MS)) ||
        ((master_ms != 0u) &&
         (target_id == rs485_status_master_node_id) &&
         ((now_ms - master_ms) <= RS485_STATUS_PEER_HOLD_MS))) {
      target_live = 1u;
    }

    /* Keep the 30 s cache internally for a quick reconnect, but never expose
       cached identity as a currently connected device. */
    if (target_live == 0u) {
      mask = 0u;
      last_ms = 0u;
      memset(nibbles, 0, sizeof(nibbles));
    }
  }
  if ((local_request == 0u) && (mask != 0u)) {
    flags |= RS485_IDENTITY_FLAG_DEVICE_ID_ASSIGNED;
  }
  if (rs485_identity_scan_active != 0u) {
    flags |= RS485_IDENTITY_FLAG_SCAN_ACTIVE;
  }
  if (primask == 0u) {
    __enable_irq();
  }

  if (((local_request != 0u) &&
       (vnd_sync_mode_public == VND_SYNC_MODE_MASTER)) ||
      ((target_live != 0u) &&
       (rs485_status_master_last_ms != 0u) &&
       ((now_ms - rs485_status_master_last_ms) <=
        RS485_STATUS_PEER_HOLD_MS) &&
       (target_id == rs485_status_master_node_id))) {
    flags |= RS485_IDENTITY_FLAG_MASTER;
  }
  if ((target_live != 0u) &&
      (target_id <= RS485_DISCOVERY_MAX_ID) &&
      ((rs485_node_conflict_mask & (1u << target_id)) != 0u)) {
    flags |= RS485_IDENTITY_FLAG_NODE_CONFLICT;
  }
  if (((local_request != 0u) &&
       (rs485_role_local_is_selected_slave() != 0u)) ||
      ((target_live != 0u) &&
       (local_request == 0u) &&
       (target_id == rs485_role_selected_slave_node_id) &&
       (rs485_role_slave_valid != 0u))) {
    flags |= RS485_IDENTITY_FLAG_SELECTED_SLAVE;
  }

  if ((mask & ((1u << RS485_IDENT_SHORT_NIBBLES) - 1u)) ==
      ((1u << RS485_IDENT_SHORT_NIBBLES) - 1u)) {
    flags |= RS485_IDENTITY_FLAG_SHORT_VALID;
    for (uint8_t page = 0u; page < RS485_IDENT_SHORT_NIBBLES; page++) {
      out->short_id[page] = rs485_identity_hex_digit(nibbles[page]);
    }
  }

  for (uint8_t page = 0u; page < RS485_IDENT_RPI_NIBBLES; page++) {
    rpi_number = (uint16_t)((rpi_number << 4) |
        nibbles[RS485_IDENT_SHORT_NIBBLES + page]);
  }
  if ((mask & ((((1u << RS485_IDENT_RPI_NIBBLES) - 1u)
                << RS485_IDENT_SHORT_NIBBLES))) ==
      (((1u << RS485_IDENT_RPI_NIBBLES) - 1u)
       << RS485_IDENT_SHORT_NIBBLES)) {
    if (rpi_number != 0u) {
      flags |= RS485_IDENTITY_FLAG_RPI_NUMBER_VALID;
    }
  }

  for (uint8_t page = 0u; page < RS485_IDENT_IP_NIBBLES; page++) {
    uint8_t nibble =
        nibbles[RS485_IDENT_SHORT_NIBBLES +
                RS485_IDENT_RPI_NIBBLES + page];
    if ((page & 1u) == 0u) {
      ip4[page >> 1] = (uint8_t)(nibble << 4);
    } else {
      ip4[page >> 1] |= nibble;
    }
  }
  if ((mask & (((1u << RS485_IDENT_IP_NIBBLES) - 1u)
               << (RS485_IDENT_SHORT_NIBBLES +
                   RS485_IDENT_RPI_NIBBLES))) ==
      (((1u << RS485_IDENT_IP_NIBBLES) - 1u)
       << (RS485_IDENT_SHORT_NIBBLES +
           RS485_IDENT_RPI_NIBBLES))) {
    if (rs485_identity_ip_is_set(ip4) != 0u) {
      flags |= RS485_IDENTITY_FLAG_IP_VALID;
    }
  }
  if ((mask & RS485_IDENT_ALL_PAGES_MASK) == RS485_IDENT_ALL_PAGES_MASK) {
    flags |= RS485_IDENTITY_FLAG_COMPLETE;
  }
  if ((last_ms != 0u) && ((now_ms - last_ms) <= RS485_IDENT_RECENT_MS)) {
    flags |= RS485_IDENTITY_FLAG_RECENT;
  }

  out->node_id = target_id;
  out->flags = flags;
  out->rpi_number = rpi_number;
  memcpy(out->ip4, ip4, sizeof(out->ip4));
  out->seen_page_mask = mask & RS485_IDENT_ALL_PAGES_MASK;
  out->last_ms = last_ms;
  return 1u;
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

static uint32_t rs485_status_get_response_delay_ticks(void)
{
  uint32_t byte_ticks = rs485_sync_get_uart_packet_ticks();
  uint32_t bit_ticks = rs485_sync_get_uart_bit_ticks();
  uint32_t delay_ticks = 0u;

  if (RS485_STATUS_RESPONSE_DELAY_BYTES != 0u) {
    delay_ticks += byte_ticks * RS485_STATUS_RESPONSE_DELAY_BYTES;
  }
  if (RS485_STATUS_RESPONSE_DELAY_BITS != 0u) {
    delay_ticks += bit_ticks * RS485_STATUS_RESPONSE_DELAY_BITS;
  }

  return delay_ticks;
}

static void rs485_status_wait_bit_times(uint32_t bit_count)
{
  uint32_t baud = huart2.Init.BaudRate;
  uint32_t hclk = HAL_RCC_GetHCLKFreq();
  uint32_t start_cycles = 0u;
  uint32_t wait_cycles = 0u;

  if ((bit_count == 0u) || (baud == 0u) || (hclk == 0u)) {
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

  wait_cycles = (uint32_t)((((uint64_t)hclk * (uint64_t)bit_count) +
                            ((uint64_t)baud - 1u)) / (uint64_t)baud);
  start_cycles = DWT->CYCCNT;
  while ((uint32_t)(DWT->CYCCNT - start_cycles) < wait_cycles) {
  }
}

static void rs485_status_wait_response_delay(void)
{
  uint32_t bit_count = (RS485_STATUS_RESPONSE_DELAY_BYTES * 10u) +
                       RS485_STATUS_RESPONSE_DELAY_BITS;

  rs485_status_wait_bit_times(bit_count);
}

static uint8_t rs485_status_count_recent_peers(uint32_t now_ms, uint32_t hold_ms)
{
  uint8_t count = 0u;
  uint32_t index = 0u;

  for (index = 0u; index < RS485_DEVICE_ID_COUNT; index++) {
    uint32_t peer_ms = rs485_status_peer_last_ms[index];
    if ((peer_ms != 0u) && ((now_ms - peer_ms) <= hold_ms)) {
      count++;
    }
  }

  return count;
}

static uint32_t rs485_status_get_recent_peer_mask(uint32_t now_ms, uint32_t hold_ms)
{
  uint32_t mask = 0u;
  uint8_t index = 0u;

  for (index = 0u; index < RS485_DEVICE_ID_COUNT; index++) {
    uint32_t peer_ms = rs485_status_peer_last_ms[index];
    if ((peer_ms != 0u) && ((now_ms - peer_ms) <= hold_ms)) {
      mask |= (1u << index);
    }
  }

  return mask;
}

static uint8_t rs485_status_confirm_peer_candidate(uint8_t node_id,
                                                   uint32_t now_ms)
{
  uint32_t peer_ms;
  uint32_t candidate_ms;
  uint8_t count;

  if (node_id > RS485_DISCOVERY_MAX_ID) {
    return 0u;
  }

  peer_ms = rs485_status_peer_last_ms[node_id];
  if ((peer_ms != 0u) &&
      ((now_ms - peer_ms) <= RS485_STATUS_PEER_HOLD_MS)) {
    rs485_status_peer_candidate_count[node_id] =
        RS485_STATUS_PEER_CONFIRM_COUNT;
    rs485_status_peer_candidate_last_ms[node_id] = now_ms;
    return 1u;
  }

  candidate_ms = rs485_status_peer_candidate_last_ms[node_id];
  count = rs485_status_peer_candidate_count[node_id];
  if ((candidate_ms == 0u) ||
      ((now_ms - candidate_ms) > RS485_STATUS_PEER_CONFIRM_WINDOW_MS)) {
    count = 1u;
  } else if (count < RS485_STATUS_PEER_CONFIRM_COUNT) {
    count++;
  }

  rs485_status_peer_candidate_count[node_id] = count;
  rs485_status_peer_candidate_last_ms[node_id] = now_ms;
  return (uint8_t)((count >= RS485_STATUS_PEER_CONFIRM_COUNT) ? 1u : 0u);
}

static uint8_t rs485_status_confirm_master_candidate(uint8_t node_id,
                                                     uint32_t now_ms)
{
  if (node_id > RS485_DISCOVERY_MAX_ID) {
    return 0u;
  }

  if ((rs485_status_master_last_ms != 0u) &&
      ((now_ms - rs485_status_master_last_ms) <= RS485_STATUS_PEER_HOLD_MS) &&
      (node_id == rs485_status_master_node_id)) {
    rs485_status_master_candidate_id = node_id;
    rs485_status_master_candidate_count = RS485_STATUS_PEER_CONFIRM_COUNT;
    rs485_status_master_candidate_last_ms = now_ms;
    return 1u;
  }

  if ((rs485_status_master_candidate_last_ms == 0u) ||
      ((now_ms - rs485_status_master_candidate_last_ms) >
       RS485_STATUS_PEER_CONFIRM_WINDOW_MS) ||
      (node_id != rs485_status_master_candidate_id)) {
    rs485_status_master_candidate_id = node_id;
    rs485_status_master_candidate_count = 1u;
  } else if (rs485_status_master_candidate_count <
             RS485_STATUS_PEER_CONFIRM_COUNT) {
    rs485_status_master_candidate_count++;
  }
  rs485_status_master_candidate_last_ms = now_ms;

  return (uint8_t)((rs485_status_master_candidate_count >=
                    RS485_STATUS_PEER_CONFIRM_COUNT) ? 1u : 0u);
}

static void rs485_status_clear_peer_id(uint8_t node_id)
{
  uint8_t index = 0u;

  if (node_id > RS485_DISCOVERY_MAX_ID) {
    return;
  }

  index = node_id;
  rs485_status_peer_bytes[index] = 0u;
  rs485_status_peer_second_bytes[index] = 0u;
  rs485_status_peer_last_ms[index] = 0u;
  rs485_status_peer_candidate_count[index] = 0u;
  rs485_status_peer_candidate_last_ms[index] = 0u;
  rs485_sensor_peer_bits[index] = 0u;
  rs485_sensor_peer_last_ms[index] = 0u;
  rs485_sensor_peer_last_index[index] = 0u;
  rs485_status_seen_mask &= ~(1u << index);
  rs485_discovery_seen_mask &= ~(1u << index);
  rs485_discovery_scan_mask &= ~(1u << index);
  rs485_identity_clear_row(node_id);
}

static void rs485_status_clear_peer_table(void)
{
  uint8_t index = 0u;

  for (index = 0u; index < RS485_DEVICE_ID_COUNT; index++) {
    rs485_status_peer_bytes[index] = 0u;
    rs485_status_peer_second_bytes[index] = 0u;
    rs485_status_peer_last_ms[index] = 0u;
    rs485_status_peer_candidate_count[index] = 0u;
    rs485_status_peer_candidate_last_ms[index] = 0u;
  }
}

static void rs485_status_assignment_reset(void)
{
  rs485_status_clear_peer_table();
  rs485_status_master_selector = 0u;
  rs485_status_assign_from_id = 0u;
  rs485_status_assign_to_id = 0u;
  rs485_status_assign_attempts = 0u;
  rs485_status_reply_alias_id = 0u;
}

static void rs485_status_compact_schedule_assignment(uint32_t now_ms)
{
  uint32_t active_mask = 0u;
  uint8_t target_id = 0u;
  uint8_t from_id = 0u;

#if !RS485_STATUS_COMPACT_ENABLE
  (void)now_ms;
  return;
#endif

  if (rs485_status_assign_from_id != 0u) {
    return;
  }

  active_mask = rs485_status_get_recent_peer_mask(now_ms, RS485_STATUS_ASSIGN_ID_HOLD_MS);
  if (active_mask == 0u) {
    return;
  }

  for (target_id = 1u; target_id <= RS485_DISCOVERY_MAX_ID; target_id++) {
    if ((active_mask & (1u << (target_id - 1u))) != 0u) {
      continue;
    }
    for (from_id = (uint8_t)(target_id + 1u);
         from_id <= RS485_DISCOVERY_MAX_ID;
         from_id++) {
      if ((active_mask & (1u << (from_id - 1u))) != 0u) {
        rs485_status_assign_from_id = from_id;
        rs485_status_assign_to_id = target_id;
        rs485_status_assign_attempts = 0u;
        rs485_status_assign_count++;
        return;
      }
    }
    return;
  }
}

static void rs485_status_compact_on_master_response(uint8_t response_id)
{
  uint32_t compacted_mask = 0u;
  uint8_t compacted_high_water = 0u;

  if ((rs485_status_assign_from_id == 0u) ||
      (rs485_status_assign_to_id == 0u)) {
    return;
  }
  if (rs485_status_master_selector != rs485_status_assign_from_id) {
    return;
  }
  if (response_id != rs485_status_assign_to_id) {
    return;
  }

  rs485_status_clear_peer_id(rs485_status_assign_from_id);
  compacted_mask = rs485_status_get_recent_peer_mask(
      HAL_GetTick(), RS485_STATUS_ASSIGN_ID_HOLD_MS);
  compacted_high_water = rs485_status_contiguous_count_from_mask(compacted_mask);
  if (rs485_role_slave_id_high_water != compacted_high_water) {
    rs485_role_slave_id_high_water = compacted_high_water;
    rs485_role_mark_persist_dirty();
  }
  rs485_role_enum_stable_mask = 0u;
  rs485_role_enum_stable_since_ms = HAL_GetTick();
  rs485_status_assign_from_id = 0u;
  rs485_status_assign_to_id = 0u;
  rs485_status_assign_attempts = 0u;
  rs485_status_assign_confirm_count++;
}

static void rs485_status_master_advance_selector(void)
{
  if (rs485_status_master_selector >= RS485_DISCOVERY_MAX_ID) {
    rs485_status_master_selector = 0u;
    rs485_status_compact_schedule_assignment(HAL_GetTick());
    rs485_identity_master_on_selector_wrap();
  } else {
    rs485_status_master_selector++;
  }
}

static uint8_t rs485_status_build_local_byte(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t status = 0u;

  if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
    status = rs485_status_id_to_wire(rs485_status_master_selector);
  } else {
    status = rs485_status_id_to_wire(rs485_local_node_id);
  }

  if (optic_sensor_get_state() != 0u) {
    status |= RS485_STATUS_OPTIC_BIT;
  }
  uint8_t det_adc_bits = rs485_status_get_det_adc_bits();
  if ((det_adc_bits & 0x01u) != 0u) {
    status |= RS485_STATUS_DET_ADC1_BIT;
  }
  if ((det_adc_bits & 0x02u) != 0u) {
    status |= RS485_STATUS_DET_ADC2_BIT;
  }
  return status;
}

static uint8_t rs485_status_build_public_local_byte(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t status = rs485_status_build_local_byte();

  if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
    status = (uint8_t)((status & (uint8_t)~RS485_STATUS_ID_MASK) |
                       rs485_status_id_to_wire(rs485_local_node_id));
  }

  return status;
}

static uint8_t rs485_status_build_master_identity_first_byte(void)
{
  /* Device ID 0 is a valid public address. Carry the actual MASTER id in
     self-identity words instead of using zero as an in-band sentinel. */
  return rs485_status_build_public_local_byte();
}

static void rs485_identity_note_master_request(uint8_t first, uint8_t second)
{
  uint8_t selector =
      rs485_status_id_from_wire((uint8_t)(first & RS485_STATUS_ID_MASK));
  uint8_t page = (uint8_t)(second & RS485_STATUS_ID_MASK);

  if (((second & RS485_STATUS_IDENT_REQ_MASK) == RS485_STATUS_IDENT_REQ_VALUE) &&
      (page < RS485_IDENT_PAGE_COUNT)) {
    rs485_identity_current_req_active = 1u;
    rs485_identity_current_req_page = page;
    rs485_identity_current_req_selector = selector;
  } else {
    rs485_identity_current_req_active = 0u;
    rs485_identity_current_req_page = 0u;
    rs485_identity_current_req_selector = 0u;
  }
}

static void rs485_identity_note_master_self_word(uint8_t first, uint8_t second)
{
  uint8_t master_id =
      rs485_status_id_from_wire((uint8_t)(first & RS485_STATUS_ID_MASK));

  if (master_id > RS485_DISCOVERY_MAX_ID) {
    return;
  }

  if ((second & RS485_STATUS_MASTER_SELF_PAGE_MASK) ==
      RS485_STATUS_MASTER_SELF_PAGE_VALUE) {
    uint8_t page = (uint8_t)(second & RS485_STATUS_ID_MASK);
    rs485_identity_master_self_page = (page < RS485_IDENT_PAGE_COUNT) ? page : 0xFFu;
    rs485_identity_master_self_node_id = master_id;
    rs485_identity_master_self_node_valid = 1u;
    return;
  }

  if (((second & RS485_STATUS_MASTER_SELF_DATA_MASK) ==
       RS485_STATUS_MASTER_SELF_DATA_VALUE) &&
      (rs485_identity_master_self_page < RS485_IDENT_PAGE_COUNT)) {
    uint8_t target_id = rs485_identity_master_self_node_id;

    if ((rs485_identity_master_self_node_valid != 0u) &&
        (target_id <= RS485_DISCOVERY_MAX_ID)) {
      rs485_identity_store_nibble(target_id,
                                  rs485_identity_master_self_page,
                                  (uint8_t)(second & 0x0Fu),
                                  HAL_GetTick());
    }
    rs485_identity_master_self_page = 0xFFu;
    rs485_identity_master_self_node_id = 0u;
    rs485_identity_master_self_node_valid = 0u;
    need_usb_status_refresh = 1u;
  }
}

static void rs485_identity_note_response(uint8_t node_id, uint8_t second)
{
  uint32_t now_ms = HAL_GetTick();

  if ((rs485_identity_current_req_active == 0u) ||
      (rs485_identity_current_req_page >= RS485_IDENT_PAGE_COUNT) ||
      (node_id != rs485_identity_current_req_selector) ||
      ((second & RS485_STATUS_IDENT_RESP_MASK) != RS485_STATUS_IDENT_RESP_VALUE)) {
    return;
  }
  if ((rs485_status_peer_last_ms[node_id] == 0u) ||
      ((now_ms - rs485_status_peer_last_ms[node_id]) >
       RS485_STATUS_PEER_HOLD_MS)) {
    return;
  }

  rs485_identity_store_nibble(node_id,
                              rs485_identity_current_req_page,
                              (uint8_t)(second & 0x0Fu),
                              now_ms);
  need_usb_status_refresh = 1u;
}

static void rs485_identity_master_on_selector_wrap(void)
{
  if (rs485_identity_scan_active == 0u) {
    return;
  }

  if ((uint8_t)(rs485_identity_scan_page + 1u) >= RS485_IDENT_PAGE_COUNT) {
    rs485_identity_scan_active = 0u;
    rs485_identity_self_tx_state = RS485_IDENT_SELF_STATE_NONE;
    rs485_identity_current_req_active = 0u;
    rs485_identity_current_req_page = 0u;
    rs485_identity_current_req_selector = 0u;
    rs485_identity_scan_last_complete_ms = HAL_GetTick();
    need_usb_status_refresh = 1u;
    return;
  }

  rs485_identity_scan_page++;
  rs485_identity_self_tx_state = RS485_IDENT_SELF_STATE_PAGE;
}

static void rs485_identity_service(uint32_t now_ms)
{
  extern volatile uint8_t vnd_sync_mode_public;

  /* Presence and sensor status disappear from the live map after 500 ms.
     Identity is a slower background cache, but it must not retain disconnected
     devices forever: evict remote rows after the existing 30 s RECENT window. */
  rs485_identity_expire_rows(now_ms);

  if (vnd_sync_mode_public != VND_SYNC_MODE_MASTER) {
    rs485_identity_scan_active = 0u;
    rs485_identity_self_tx_state = RS485_IDENT_SELF_STATE_NONE;
    return;
  }
  if ((rs485_local_node_id_assigned == 0u) ||
      (rs485_identity_scan_active != 0u)) {
    return;
  }

  if (rs485_identity_rescan_requested != 0u) {
    rs485_identity_request_scan();
    return;
  }

  if (((rs485_identity_scan_last_start_ms == 0u) &&
       (now_ms >= RS485_IDENT_AUTO_SCAN_BOOT_MS)) ||
      ((rs485_identity_scan_last_complete_ms != 0u) &&
       ((now_ms - rs485_identity_scan_last_complete_ms) >=
        RS485_IDENT_AUTO_SCAN_PERIOD_MS))) {
    rs485_identity_request_scan();
  }
}

static uint8_t rs485_status_build_local_second_byte(uint8_t selector)
{
  extern volatile uint8_t vnd_sync_mode_public;

  if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
    rs485_identity_current_req_active = 0u;
    rs485_identity_current_req_page = 0u;
    rs485_identity_current_req_selector = 0u;
    if ((rs485_status_assign_from_id != 0u) &&
        (rs485_status_assign_to_id != 0u) &&
        (selector == rs485_status_assign_from_id)) {
      uint32_t reserved_mask = rs485_status_get_recent_peer_mask(HAL_GetTick(),
                                                                 RS485_STATUS_ASSIGN_ID_HOLD_MS);
      uint32_t from_bit = (1u << (rs485_status_assign_from_id - 1u));
      uint32_t to_bit = (1u << (rs485_status_assign_to_id - 1u));
      if (((reserved_mask & from_bit) == 0u) ||
          ((reserved_mask & to_bit) != 0u)) {
        rs485_status_assign_from_id = 0u;
        rs485_status_assign_to_id = 0u;
        rs485_status_assign_attempts = 0u;
        return 0u;
      }
      if (rs485_status_assign_attempts >= RS485_STATUS_ASSIGN_RETRY_LIMIT) {
        rs485_status_assign_from_id = 0u;
        rs485_status_assign_to_id = 0u;
        rs485_status_assign_attempts = 0u;
        return 0u;
      }
      rs485_status_assign_attempts++;
      return (uint8_t)(RS485_STATUS_ASSIGN_CMD_VALUE |
                       (rs485_status_assign_to_id & RS485_STATUS_ID_MASK));
    }
    if ((rs485_identity_scan_active != 0u) &&
        (rs485_identity_scan_page < RS485_IDENT_PAGE_COUNT) &&
        (selector <= RS485_DISCOVERY_MAX_ID)) {
      rs485_identity_current_req_active = 1u;
      rs485_identity_current_req_page = rs485_identity_scan_page;
      rs485_identity_current_req_selector = selector;
      return (uint8_t)(RS485_STATUS_IDENT_REQ_VALUE |
                       (rs485_identity_scan_page & RS485_STATUS_ID_MASK));
    }
    if (rs485_local_node_id_assigned != 0u) {
      return (uint8_t)(RS485_STATUS_MASTER_INFO_VALUE |
                       rs485_status_id_to_wire(rs485_local_node_id));
    }
    return 0u;
  }
  (void)selector;

  if ((rs485_identity_current_req_active != 0u) &&
      (rs485_identity_current_req_page < RS485_IDENT_PAGE_COUNT)) {
    return (uint8_t)(RS485_STATUS_IDENT_RESP_VALUE |
                     rs485_identity_get_local_nibble(rs485_identity_current_req_page));
  }

  return 0u;
}

static void rs485_status_note_master_word(uint8_t first, uint8_t second)
{
  uint8_t master_id = 0u;
  uint8_t master_id_valid = 0u;
  uint8_t public_byte = 0u;
  uint32_t now_ms = HAL_GetTick();

  if (((second & RS485_STATUS_SENSOR_EVENT_MASK) ==
       RS485_STATUS_SENSOR_EVENT_VALUE) ||
      ((second & RS485_STATUS_MASTER_SELF_PAGE_MASK) ==
       RS485_STATUS_MASTER_SELF_PAGE_VALUE) ||
      ((second & RS485_STATUS_MASTER_SELF_DATA_MASK) ==
       RS485_STATUS_MASTER_SELF_DATA_VALUE)) {
    master_id =
        rs485_status_id_from_wire((uint8_t)(first & RS485_STATUS_ID_MASK));
    master_id_valid = 1u;
  } else if ((second & RS485_STATUS_MASTER_INFO_MASK) ==
             RS485_STATUS_MASTER_INFO_VALUE) {
    master_id =
        rs485_status_id_from_wire((uint8_t)(second & RS485_STATUS_ID_MASK));
    master_id_valid = 1u;
  } else if ((rs485_status_master_last_ms != 0u) &&
             ((now_ms - rs485_status_master_last_ms) <=
              RS485_STATUS_PEER_HOLD_MS)) {
    master_id = rs485_status_master_node_id;
    master_id_valid = 1u;
  }

  if ((master_id_valid == 0u) ||
      (master_id > RS485_DISCOVERY_MAX_ID)) {
    return;
  }
  if (rs485_status_confirm_master_candidate(master_id, now_ms) == 0u) {
    return;
  }

  public_byte = (uint8_t)((first & (uint8_t)~RS485_STATUS_ID_MASK) |
                          rs485_status_id_to_wire(master_id));
  if ((rs485_status_master_last_ms == 0u) ||
      (rs485_status_master_public_byte != public_byte)) {
    need_usb_status_refresh = 1u;
  }

  rs485_status_master_node_id = master_id;
  rs485_status_master_public_byte = public_byte;
  rs485_status_master_last_ms = now_ms;
}

uint8_t rs485_status_get_snapshot(uint8_t *local_status,
                                  uint8_t *node_count,
                                  uint32_t *seen_mask,
                                  uint8_t *status_bytes,
                                  uint8_t max_status_bytes)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t mode = vnd_sync_mode_public;
  uint32_t now_ms = HAL_GetTick();
  uint32_t mask = 0u;
  uint8_t count = 0u;
  uint8_t local = rs485_status_build_public_local_byte();
  uint8_t local_id = (rs485_local_node_id_assigned != 0u)
      ? rs485_local_node_id
      : 0u;
  uint8_t limit = max_status_bytes;

  if (limit > RS485_DEVICE_ID_COUNT) {
    limit = RS485_DEVICE_ID_COUNT;
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

  if ((mode != VND_SYNC_MODE_OFF) &&
      (rs485_local_node_id_assigned != 0u) &&
      (local_id < limit)) {
    uint8_t idx = local_id;
    uint32_t bit = (1u << idx);
    if ((mask & (1u << idx)) == 0u) {
      count++;
    }
    if (status_bytes != NULL) {
      status_bytes[idx] = (status_bytes[idx] == 0u)
          ? local
          : (uint8_t)(local | (status_bytes[idx] & (uint8_t)~RS485_STATUS_ID_MASK));
    }
    mask |= bit;
  }

  if ((mode == VND_SYNC_MODE_SLAVE) &&
      (rs485_status_master_node_id < limit) &&
      (rs485_status_master_last_ms != 0u) &&
      ((now_ms - rs485_status_master_last_ms) <= RS485_STATUS_PEER_HOLD_MS)) {
    uint8_t idx = rs485_status_master_node_id;
    uint32_t bit = (1u << idx);
    if ((mask & bit) == 0u) {
      count++;
    }
    if (status_bytes != NULL) {
      status_bytes[idx] = rs485_status_master_public_byte;
    }
    mask |= bit;
  }

  if (status_bytes != NULL) {
    for (uint8_t index = 0u; index < limit; index++) {
      uint32_t bit = (1u << index);
      if ((mask & bit) == 0u) {
        status_bytes[index] = 0u;
      } else if ((rs485_node_conflict_mask & bit) != 0u) {
        /* Keep the address visible but suppress ambiguous sensor state. */
        status_bytes[index] =
            rs485_status_id_to_wire(index);
      } else if (status_bytes[index] == 0u) {
        status_bytes[index] =
            rs485_status_id_to_wire(index);
      }
    }
  }

  count = rs485_count_bits_u32(mask);

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

void rs485_role_get_selected_slave_snapshot(rs485_selected_slave_snapshot_t *out)
{
  if (out == NULL) {
    return;
  }
  memset(out, 0, sizeof(*out));
  out->age_ms = 0xFFFFu;

  /* Deprecated topology concept. Every fixed node now publishes its own
     sensors in sync_status_bytes[device_id]. */
}

uint8_t rs485_status_master_optic_active(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t now_ms = HAL_GetTick();

  if (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) {
    return 0u;
  }
  if (rs485_status_master_last_ms == 0u) {
    return 0u;
  }
  if ((now_ms - rs485_status_master_last_ms) > RS485_STATUS_PEER_HOLD_MS) {
    return 0u;
  }
  if ((rs485_status_master_last_ms != 0u) &&
      ((rs485_node_conflict_mask &
        (1u << rs485_status_master_node_id)) != 0u)) {
    return 0u;
  }

  return (uint8_t)(((rs485_status_master_public_byte & RS485_STATUS_OPTIC_BIT) != 0u) ? 1u : 0u);
}

uint8_t rs485_status_get_master_snapshot(uint8_t *master_status,
                                         uint16_t *age_ms,
                                         uint8_t *flags)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t now_ms = HAL_GetTick();
  uint32_t age = 0xFFFFFFFFu;
  uint8_t status = 0u;
  uint8_t out_flags = 0u;
  uint8_t fresh = 0u;

  if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
    status = rs485_status_build_public_local_byte();
    age = 0u;
    out_flags |= 0x01u; /* valid */
    out_flags |= 0x02u; /* fresh */
    out_flags |= 0x08u; /* local master */
    fresh = 1u;
  } else if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
             (rs485_status_master_last_ms != 0u)) {
    status = rs485_status_master_public_byte;
    age = now_ms - rs485_status_master_last_ms;
    out_flags |= 0x01u; /* valid */
    if (age <= RS485_STATUS_PEER_HOLD_MS) {
      out_flags |= 0x02u; /* fresh */
      fresh = 1u;
    }
  }

  if ((fresh != 0u) && ((status & RS485_STATUS_OPTIC_BIT) != 0u)) {
    out_flags |= 0x04u; /* master optic active */
  }

  if (master_status != NULL) {
    *master_status = status;
  }
  if (age_ms != NULL) {
    *age_ms = (age > 0xFFFEu) ? 0xFFFFu : (uint16_t)age;
  }
  if (flags != NULL) {
    *flags = out_flags;
  }

  return fresh;
}

uint8_t optic_any_sensor_active(void)
{
  uint32_t now_ms = HAL_GetTick();

  /* The same held optical signal is used by local effects, public status,
     onboard indication and RS485 bit5. */
  if (optic_sensor_get_state() != 0u) {
    return 1u;
  }

  if (rs485_status_master_optic_active() != 0u) {
    return 1u;
  }

  for (uint8_t index = 0u; index < RS485_DEVICE_ID_COUNT; index++) {
    uint32_t peer_ms = rs485_status_peer_last_ms[index];
    if (((rs485_node_conflict_mask & (1u << index)) == 0u) &&
        (peer_ms != 0u) &&
        ((now_ms - peer_ms) <= RS485_STATUS_PEER_HOLD_MS) &&
        ((rs485_status_peer_bytes[index] & RS485_STATUS_OPTIC_BIT) != 0u)) {
      return 1u;
    }
  }

  return 0u;
}

void rs485_set_local_node_id_from_host(uint8_t node_id)
{
  uint32_t primask = 0u;
  uint8_t old_id = rs485_local_node_id;

  if (node_id > RS485_DISCOVERY_MAX_ID) {
    return;
  }
  primask = __get_PRIMASK();
  __disable_irq();
  rs485_local_node_id = node_id;
  rs485_local_node_id_assigned = 1u;
  rs485_role_enum_waiting_assignment = 0u;
  rs485_role_last_confirmed_node_id = node_id;
  rs485_status_slave_reply_div_counter = 0u;
  rs485_status_seen_mask = 0u;
  rs485_status_cycle_count = 0u;
  rs485_status_slot_response_seen = 0u;
  rs485_status_slot_response_first = 0u;
  rs485_status_slot_response_second = 0u;
  rs485_slave_count_estimate = 0u;
  rs485_status_clear_peer_table();
  rs485_sensor_queue_active_snapshot();
  rs485_identity_note_local_id_change(old_id, rs485_local_node_id);
  if (rs485_role_local_is_selected_slave() != 0u) {
    rs485_role_selected_slave_node_id = node_id;
    rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_HOST);
  }
  if (primask == 0u) {
    __enable_irq();
  }
  rs485_role_slave_id_high_water = 0u;
  rs485_node_claim_reset_registry();
  rs485_node_claim_schedule(HAL_GetTick(), RS485_NODE_CLAIM_RETRIES);
  rs485_role_mark_persist_dirty_immediate();
}

uint8_t rs485_sync_has_active_peer(void)
{
  uint32_t now_ms = HAL_GetTick();

  if (rs485_slave_count_estimate != 0u) {
    return 1u;
  }

  return (uint8_t)((rs485_status_count_recent_peers(now_ms, RS485_STATUS_PEER_HOLD_MS) != 0u) ? 1u : 0u);
}

uint8_t rs485_sync_phase_locked(void)
{
  uint32_t now_ms = HAL_GetTick();

  if ((sync_last_edge_ms == 0u) ||
      ((now_ms - sync_last_edge_ms) > RS485_SYNC_PRESENT_MS)) {
    return 0u;
  }

  return (uint8_t)(((rs485_sync_locked != 0u) &&
                    (rs485_sync_phase_relation == RS485_SYNC_RELATION_IN_PHASE)) ? 1u : 0u);
}

static uint8_t rs485_is_sync_byte(uint8_t value)
{
  return (uint8_t)(((value & (uint8_t)~RS485_SYNC_EDGE_BIT) == RS485_SYNC7_BASE) ? 1u : 0u);
}

static uint8_t rs485_sync_accept_by_period_guard(void)
{
#if !RS485_SYNC_PERIOD_GUARD_ENABLE
  return 1u;
#else
  uint32_t prev_period = sync_tim5_period_ticks;
  uint32_t elapsed = htim5.Instance->CNT;
  uint32_t min_elapsed = 0u;

  if (prev_period < 1000u) {
    return 1u;
  }

  min_elapsed =
      (prev_period / RS485_SYNC_EARLY_REJECT_DEN) * RS485_SYNC_EARLY_REJECT_NUM;
  if (min_elapsed < 1000u) {
    min_elapsed = 1000u;
  }
  if (min_elapsed > RS485_SYNC_EARLY_REJECT_MAX_TICKS) {
    min_elapsed = RS485_SYNC_EARLY_REJECT_MAX_TICKS;
  }

  if (elapsed < min_elapsed) {
    rs485_sync_rejected_early_count++;
    return 0u;
  }

  return 1u;
#endif
}

static uint8_t rs485_status_sync_candidate_is_late(void)
{
  uint32_t timeout_ticks = rs485_sync_get_uart_packet_ticks() *
                           RS485_STATUS_MASTER_WORD_TIMEOUT_BYTES;
  uint32_t elapsed_ticks = htim5.Instance->CNT;

  if (timeout_ticks < 1000u) {
    timeout_ticks = 1000u;
  }

  return (uint8_t)((elapsed_ticks > timeout_ticks) ? 1u : 0u);
}

static void rs485_process_received_byte(uint8_t value)
{
  uint8_t sync_candidate = rs485_is_sync_byte(value);
  uint8_t read_master_status = 1u;

  /* Once a framed UID/role packet has started, payload bytes must not be
     reinterpreted as sync/status markers. */
  if (rs485_uid_rx_active != 0u) {
    if (rs485_uid_rx_index < RS485_UID_FRAME_TAIL) {
      rs485_uid_rx_buf[rs485_uid_rx_index++] = value;
    } else {
      rs485_uid_rx_reset();
    }

    if ((rs485_uid_rx_active != 0u) &&
        (rs485_uid_rx_index >= RS485_UID_FRAME_TAIL)) {
      if (rs485_uid_rx_buf[RS485_UID_FRAME_BYTES] ==
          rs485_uid_checksum(rs485_uid_rx_buf)) {
        rs485_uid_handle_received(rs485_uid_rx_buf);
      }
      rs485_uid_rx_reset();
    }
    return;
  }

  if (rs485_role_rx_magic != 0u) {
    if (rs485_role_rx_index < rs485_role_rx_expected) {
      rs485_role_rx_buf[rs485_role_rx_index++] = value;
    } else {
      rs485_role_reset_rx();
      return;
    }

    if (rs485_role_rx_index >= rs485_role_rx_expected) {
      uint8_t magic = rs485_role_rx_magic;
      uint8_t payload_len = (uint8_t)(rs485_role_rx_expected - 1u);
      uint8_t received_checksum = rs485_role_rx_buf[payload_len];
      if (received_checksum ==
          rs485_role_frame_checksum(magic,
                                     rs485_role_rx_buf,
                                     payload_len)) {
        uint8_t write_index = rs485_role_frame_pending_write;
        uint8_t next_index = (uint8_t)((write_index + 1u) %
                                       RS485_ROLE_PENDING_CAPACITY);
        if (next_index != rs485_role_frame_pending_read) {
          memcpy(rs485_role_frame_pending_buf[write_index],
                 rs485_role_rx_buf,
                 sizeof(rs485_role_frame_pending_buf[write_index]));
          rs485_role_frame_pending_magic[write_index] = magic;
          /* Publish the producer index last so the main loop never observes
             a partial payload written by the USART IRQ. */
          __DMB();
          rs485_role_frame_pending_write = next_index;
        }
      }
      rs485_role_reset_rx();
    }
    return;
  }

  if (rs485_status_rx_word_after_sync != 0u) {
    if ((sync_candidate != 0u) &&
        (rs485_status_sync_candidate_is_late() != 0u)) {
      rs485_status_rx_word_after_sync = 0u;
      rs485_status_rx_word_index = 0u;
      rs485_status_legacy_master_mode = 1u;
      rs485_status_legacy_detect_count++;
      goto process_sync_byte;
    }
    rs485_status_rx_word_buf[rs485_status_rx_word_index++] = value;
    if (rs485_status_rx_word_index >= RS485_STATUS_WORD_BYTES) {
      rs485_status_rx_word_after_sync = 0u;
      rs485_status_rx_word_index = 0u;
      rs485_status_legacy_master_mode = 0u;
      rs485_status_legacy_probe_count = 0u;
      rs485_status_on_master_word(rs485_status_rx_word_buf[0], rs485_status_rx_word_buf[1]);
    }
  } else if (rs485_status_window_active != 0u) {
    if ((sync_candidate != 0u) &&
        (rs485_status_sync_candidate_is_late() != 0u)) {
      rs485_status_finalize_window();
      goto process_sync_byte;
    }
    rs485_status_rx_word_buf[rs485_status_rx_word_index++] = value;
    if (rs485_status_rx_word_index >= RS485_STATUS_WORD_BYTES) {
      rs485_status_rx_word_index = 0u;
      if (rs485_status_on_received(rs485_status_rx_word_buf[0],
                                   rs485_status_rx_word_buf[1]) == 0u) {
        rs485_status_finalize_window();
      }
    }
  } else if (sync_candidate != 0u) {
process_sync_byte:
    read_master_status = 1u;
    if (rs485_sync_accept_by_period_guard() == 0u) {
      return;
    }
    /* Only a period-valid sync byte can indicate another MASTER.  Payload
       bytes and the local transceiver echo may have the same 0x25/0xA5 value,
       but arrive far too early after our own buffer boundary. */
    if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
        (rs485_tx_busy == 0u)) {
      rs485_foreign_master_last_ms = HAL_GetTick();
      need_usb_status_refresh = 1u;
    }
    if (rs485_status_legacy_master_mode != 0u) {
      if (rs485_status_legacy_probe_count < RS485_STATUS_LEGACY_PROBE_DIV) {
        rs485_status_legacy_probe_count++;
        read_master_status = 0u;
      } else {
        rs485_status_legacy_probe_count = 0u;
      }
    }
    rs485_status_rx_word_index = 0u;
    rs485_status_rx_word_after_sync = read_master_status;
    rs485_role_reset_rx();
    rs485_uid_rx_reset();
    rs485_sync_on_packet_received((value & RS485_SYNC_EDGE_BIT) ? 1u : 0u);
    rs485_discovery_on_sync_received();
    if ((rs485_status_legacy_master_mode != 0u) &&
        (read_master_status == 0u)) {
      rs485_status_master_first = 0u;
      rs485_status_master_second = 0u;
      rs485_status_master_selector = 0u;
      /* A single missing two-byte status word must not remove the MASTER from
         the public topology. Keep the confirmed cache through legacy probes;
         the normal RS485_STATUS_PEER_HOLD_MS expiry still removes a genuinely
         absent master. */
      rs485_status_begin_window();
    }
  } else if (value == RS485_UID_FRAME_MAGIC) {
    rs485_uid_rx_active = 1u;
    rs485_uid_rx_index = 0u;
  } else if ((value == RS485_ROLE_CLAIM_MAGIC) ||
             (value == RS485_ROLE_ENUM_REQ_MAGIC) ||
             (value == RS485_ROLE_ENUM_ASSIGN_MAGIC) ||
             (value == RS485_ROLE_ENUM_ACK_MAGIC) ||
             (value == RS485_ROLE_SLAVE_CLAIM_MAGIC) ||
             (value == RS485_ROLE_ENUM_RESET_MAGIC) ||
             (value == RS485_ROLE_AUTO_HEARTBEAT_MAGIC) ||
             (value == RS485_ROLE_NODE_CLAIM_MAGIC)) {
    rs485_role_rx_magic = value;
    rs485_role_rx_index = 0u;
    if ((value == RS485_ROLE_CLAIM_MAGIC) ||
        (value == RS485_ROLE_SLAVE_CLAIM_MAGIC)) {
      rs485_role_rx_expected = RS485_ROLE_CLAIM_PAYLOAD_BYTES;
    } else if (value == RS485_ROLE_ENUM_REQ_MAGIC) {
      rs485_role_rx_expected = RS485_ROLE_ENUM_REQ_BYTES;
    } else if (value == RS485_ROLE_ENUM_RESET_MAGIC) {
      rs485_role_rx_expected = RS485_ROLE_ENUM_RESET_BYTES;
    } else if (value == RS485_ROLE_AUTO_HEARTBEAT_MAGIC) {
      rs485_role_rx_expected = RS485_ROLE_AUTO_HEARTBEAT_BYTES;
    } else if (value == RS485_ROLE_NODE_CLAIM_MAGIC) {
      rs485_role_rx_expected = RS485_ROLE_NODE_CLAIM_BYTES;
    } else if (value == RS485_ROLE_ENUM_ASSIGN_MAGIC) {
      rs485_role_rx_expected = RS485_ROLE_ENUM_ASSIGN_BYTES;
    } else {
      rs485_role_rx_expected = RS485_ROLE_ENUM_ACK_BYTES;
    }
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
  uint8_t error_during_local_tx = rs485_tx_busy;
  uint8_t error_in_status_context =
      (uint8_t)(((rs485_status_rx_word_after_sync != 0u) ||
                 (rs485_status_window_active != 0u)) ? 1u : 0u);
  uint8_t first_value = 0u;
  uint8_t have_value = 0u;

  while ((huart2.Instance->ISR & USART_ISR_RXNE_RXFNE) != 0u) {
    uint8_t value = (uint8_t)(huart2.Instance->RDR & 0xFFu);
    if (have_value == 0u) {
      first_value = value;
      have_value = 1u;
    }
    rs485_process_received_byte(value);
  }

  if (error_flags != 0u) {
    rs485_uart_error_count++;
    if ((have_value != 0u) &&
        (rs485_is_sync_byte(first_value) != 0u)) {
      rs485_uart_sync_error_count++;
    }
    if ((error_flags & USART_ISR_PE) != 0u) {
      rs485_uart_pe_count++;
    }
    if ((error_flags & USART_ISR_FE) != 0u) {
      rs485_uart_fe_count++;
    }
    if ((error_flags & USART_ISR_NE) != 0u) {
      rs485_uart_ne_count++;
      if (error_during_local_tx != 0u) {
        rs485_uart_ne_de_count++;
      }
      if ((have_value != 0u) && (rs485_is_sync_byte(first_value) != 0u)) {
        rs485_uart_ne_sync_count++;
      } else if ((have_value != 0u) &&
                 ((((first_value & (uint8_t)~RS485_DISCOVERY_ID_MASK) ==
                    RS485_DISCOVERY_REQ_BASE)) ||
                  (((first_value & (uint8_t)~RS485_DISCOVERY_ID_MASK) ==
                    RS485_DISCOVERY_ACK_BASE)))) {
        rs485_uart_ne_discovery_count++;
      } else if (error_in_status_context != 0u) {
        rs485_uart_ne_status_count++;
      } else {
        rs485_uart_ne_other_count++;
      }
    }
    if ((error_flags & USART_ISR_ORE) != 0u) {
      rs485_uart_ore_count++;
    }
    /* Do not reset the status parser here. On the shared RS-485 bus FE/NE/ORE
       can be latched near DE turn-around after RXNE already carried a valid
       sync byte. Clearing the parser here drops master_status and leaves
       slaves in legacy S00/M00 until the next clean cycle. */
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
  rs485_status_slot_response_first = 0u;
  rs485_status_slot_response_second = 0u;
  rs485_status_rx_word_index = 0u;
  rs485_status_rx_word_after_sync = 0u;
  rs485_status_legacy_master_mode = 0u;
  rs485_status_legacy_probe_count = 0u;
  rs485_status_master_public_byte = 0u;
  rs485_status_master_node_id = 0u;
  rs485_status_master_last_ms = 0u;
  rs485_status_master_candidate_id = 0u;
  rs485_status_master_candidate_count = 0u;
  rs485_status_master_candidate_last_ms = 0u;
  rs485_status_slave_reply_div_counter = 0u;
  rs485_status_local_slot_expected = 0u;
  rs485_sensor_event_queue_read = 0u;
  rs485_sensor_event_queue_write = 0u;
  rs485_sensor_event_current_valid = 0u;
  rs485_sensor_event_retries = 0u;
  rs485_sensor_event_cycle = 0u;
  rs485_sensor_event_next_cycle = 0u;
  rs485_status_assign_from_id = 0u;
  rs485_status_assign_to_id = 0u;
  rs485_status_assign_attempts = 0u;
  rs485_status_reply_alias_id = 0u;
  rs485_identity_current_req_active = 0u;
  rs485_identity_current_req_page = 0u;
  rs485_identity_current_req_selector = 0u;
  rs485_identity_master_self_page = 0xFFu;
  rs485_identity_master_self_node_id = 0u;
  rs485_identity_master_self_node_valid = 0u;
  rs485_master_status_tx_no_response = 0u;
  rs485_status_window_no_response_expected = 0u;
  rs485_status_master_no_reply_slot = 0u;
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
  rs485_status_slot_response_first = 0u;
  rs485_status_slot_response_second = 0u;
  rs485_status_rx_word_index = 0u;
  rs485_status_local_slot_expected = 0u;
  rs485_status_window_no_response_expected = 0u;
}

static void rs485_status_finalize_window(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t now_ms = HAL_GetTick();
  uint8_t slot_had_response = (uint8_t)((rs485_status_slot_response_seen != 0u) || (rs485_status_slot_local_tx != 0u));
  uint8_t response_first = rs485_status_slot_response_first;
  uint8_t response_second = rs485_status_slot_response_second;
  uint32_t recent_mask = rs485_status_get_recent_peer_mask(now_ms, RS485_STATUS_PEER_HOLD_MS);
  uint8_t recent_count = rs485_count_bits_u32(recent_mask);
  uint8_t response_id =
      rs485_status_id_from_wire(
          (uint8_t)(response_first & RS485_STATUS_ID_MASK));
  uint8_t response_is_sensor_event =
      (uint8_t)(((response_second & RS485_STATUS_SENSOR_EVENT_MASK) ==
                 RS485_STATUS_SENSOR_EVENT_VALUE) ? 1u : 0u);
  uint8_t active_count = rs485_status_cycle_count;
  uint8_t no_response_expected = rs485_status_window_no_response_expected;

  if ((rs485_status_window_active == 0u) &&
      (slot_had_response == 0u)) {
    return;
  }

  if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
      (no_response_expected == 0u)) {
    rs485_status_window_total_count++;
    if (slot_had_response != 0u) {
      rs485_status_window_ok_count++;
    } else if ((recent_count != 0u) || (rs485_slave_count_estimate != 0u)) {
      rs485_status_window_miss_count++;
    }
  } else if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
             (rs485_status_local_slot_expected != 0u) &&
             (rs485_status_slot_local_tx == 0u)) {
    rs485_status_local_slot_miss_count++;
  }

  if (slot_had_response != 0u) {
    if (rs485_status_cycle_count < RS485_DEVICE_ID_COUNT) {
      rs485_status_cycle_count++;
      active_count = rs485_status_cycle_count;
    }

    if ((rs485_status_slot_response_seen != 0u) &&
        (response_id <= RS485_DISCOVERY_MAX_ID)) {
      uint8_t response_index = response_id;
      uint8_t response_was_live =
          (uint8_t)(((rs485_status_peer_last_ms[response_index] != 0u) &&
                     ((now_ms - rs485_status_peer_last_ms[response_index]) <=
                      RS485_STATUS_PEER_HOLD_MS)) ? 1u : 0u);
      uint8_t response_confirmed =
          (response_is_sensor_event != 0u)
              ? (uint8_t)(((rs485_status_peer_last_ms[response_index] != 0u) &&
                           ((now_ms - rs485_status_peer_last_ms[response_index]) <=
                            RS485_STATUS_PEER_HOLD_MS)) ? 1u : 0u)
              : rs485_status_confirm_peer_candidate(response_id, now_ms);

      if (response_confirmed != 0u) {
        if (response_was_live == 0u) {
          /* Never merge new pages with a complete identity left at this ID by
             an older device. The MASTER immediately starts a network scan so
             every board can associate the new ID with its UID signature. */
          rs485_identity_clear_row(response_id);
          if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
            rs485_identity_rescan_requested = 1u;
          }
        }
        rs485_status_seen_mask |= (1u << response_index);
        rs485_status_master_expected_phase = rs485_status_current_window_phase;
        if ((rs485_status_peer_last_ms[response_index] == 0u) ||
            (rs485_status_peer_bytes[response_index] != response_first)) {
          need_usb_status_refresh = 1u;
        }
        rs485_status_peer_bytes[response_index] = response_first;
        rs485_status_peer_second_bytes[response_index] = response_second;
        rs485_status_peer_last_ms[response_index] = now_ms;
        rs485_identity_note_response(response_id, response_second);
        if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
          rs485_status_compact_on_master_response(response_id);
          recent_mask = rs485_status_get_recent_peer_mask(now_ms, RS485_STATUS_PEER_HOLD_MS);
          rs485_status_set_slave_count_estimate(rs485_count_bits_u32(recent_mask));
        }
      }
    } else if ((rs485_status_slot_local_tx != 0u) &&
               (rs485_local_node_id_assigned != 0u) &&
               (rs485_local_node_id <= RS485_DISCOVERY_MAX_ID)) {
      rs485_status_seen_mask |= (1u << rs485_local_node_id);
    }

    rs485_discovery_seen_mask = rs485_status_seen_mask;
    rs485_discovery_scan_mask = rs485_status_seen_mask;
  } else if (no_response_expected == 0u) {
    rs485_discovery_seen_mask = rs485_status_seen_mask;
    rs485_discovery_scan_mask = rs485_status_seen_mask;

    if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
      rs485_status_set_slave_count_estimate(recent_count);
    } else if (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) {
      rs485_status_set_slave_count_estimate(active_count);
    } else {
      rs485_status_set_slave_count_estimate(0u);
    }

    rs485_status_seen_mask = 0u;
    rs485_status_tx_sent = 0u;
    rs485_status_cycle_count = 0u;
  }

  if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
      (no_response_expected == 0u)) {
    rs485_status_master_advance_selector();
  }

  rs485_status_reset_slot_state();
}

static void rs485_status_begin_window(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t should_reply = 0u;
  uint8_t sensor_event_reply = 0u;
  uint8_t local_selected = 0u;
  uint8_t first = 0u;
  uint8_t second = 0u;
  uint8_t sensor_index = 0u;
  uint8_t sensor_active = 0u;

  if (rs485_status_window_active != 0u) {
    rs485_status_finalize_window();
  }

  rs485_status_reset_slot_state();
  rs485_status_window_active = 1u;
  if (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) {
    rs485_status_window_no_response_expected = rs485_master_status_tx_no_response;
    rs485_master_status_tx_no_response = 0u;
  }
  if (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) {
    rs485_status_current_window_phase = rs485_last_sync_edge_kind;
    rs485_sensor_event_cycle++;
  }

  if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
      (RS485_STATUS_SLAVE_REPLY_ENABLE != 0u)) {
    if ((rs485_local_node_id_assigned != 0u) &&
        (rs485_status_master_no_reply_slot == 0u) &&
        ((rs485_status_master_selector == rs485_local_node_id) ||
         (rs485_status_master_selector == rs485_status_reply_alias_id))) {
      local_selected = 1u;
    }

    if ((rs485_local_node_id_assigned != 0u) &&
        (rs485_sensor_event_prepare(&sensor_index,
                                    &sensor_active) != 0u)) {
      sensor_event_reply = 1u;
      should_reply = 1u;
    } else if (local_selected != 0u) {
      should_reply = 1u;
    }
  }

  if (should_reply != 0u) {
    if ((sensor_event_reply == 0u) &&
        (RS485_STATUS_SLAVE_REPLY_DIV > 1u)) {
      rs485_status_slave_reply_div_counter++;
      if (rs485_status_slave_reply_div_counter < RS485_STATUS_SLAVE_REPLY_DIV) {
        rs485_status_finalize_window();
        return;
      }
      rs485_status_slave_reply_div_counter = 0u;
    }

    if (sensor_event_reply == 0u) {
      rs485_status_local_slot_expected = 1u;
      rs485_status_local_slot_expected_count++;
      rs485_status_wait_bit_times(RS485_STATUS_REGULAR_DELAY_BITS);
    } else {
      rs485_status_wait_bit_times(RS485_STATUS_EVENT_DELAY_BITS);
    }

    first = rs485_status_build_local_byte();
    second = (sensor_event_reply != 0u)
        ? (uint8_t)(RS485_STATUS_SENSOR_EVENT_VALUE |
                    ((sensor_index & 0x0Fu) << 1) |
                    (sensor_active & 0x01u))
        : rs485_status_build_local_second_byte(rs485_status_master_selector);

    if (rs485_tx_busy == 0u) {
      rs485_status_tx_sent = 1u;
      rs485_status_slot_local_tx = 1u;
      rs485_status_local_tx_count++;
      rs485_sync_start_tx_status_word(first, second);
      rs485_status_reply_alias_id = 0u;

      if (sensor_event_reply != 0u) {
        rs485_sensor_event_commit_tx();
      }
    } else {
      /* Keep a sensor event pending for the next 5 ms cycle. A regular
         response may be skipped, but it must never displace the event. */
      rs485_status_deferred_tx_count++;
    }
  }

  /* Keep non-local slave windows open: every slave must overhear peer replies
     so its public SYNC_STATE describes the whole slave group 1..N. */
}

static void rs485_status_apply_master_assignment(uint8_t first, uint8_t second)
{
#if !RS485_STATUS_COMPACT_ENABLE
  (void)first;
  (void)second;
  return;
#else
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t selector = (uint8_t)(first & RS485_STATUS_ID_MASK);
  uint8_t assigned_id = (uint8_t)(second & RS485_STATUS_ID_MASK);
  uint8_t old_id = rs485_local_node_id;

  if (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) {
    return;
  }
  if ((second & RS485_STATUS_ASSIGN_CMD_MASK) != RS485_STATUS_ASSIGN_CMD_VALUE) {
    return;
  }
  if ((selector == 0u) ||
      (assigned_id == 0u) ||
      (assigned_id > RS485_DISCOVERY_MAX_ID)) {
    return;
  }
  if ((rs485_local_node_id_assigned == 0u) ||
      (selector != rs485_local_node_id)) {
    return;
  }

  rs485_status_reply_alias_id = selector;
  rs485_status_assign_last_from_id = selector;
  rs485_status_assign_last_to_id = assigned_id;
  rs485_status_assign_apply_count++;
  rs485_local_node_id = assigned_id;
  rs485_local_node_id_assigned = 1u;
  rs485_role_enum_waiting_assignment = 0u;
  rs485_role_last_confirmed_node_id = assigned_id;
  /* A slave only needs to persist its own confirmed id. The MASTER keeps the
     network high-water separately after validating the UID-addressed ACK. */
  rs485_role_slave_id_high_water = assigned_id;
  rs485_status_slave_reply_div_counter = 0u;
  rs485_identity_note_local_id_change(old_id, assigned_id);
  if (rs485_role_local_is_selected_slave() != 0u) {
    rs485_role_selected_slave_node_id = assigned_id;
    rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_HOST);
  }
  rs485_role_mark_persist_dirty();
#endif
}

static void rs485_status_on_master_word(uint8_t first, uint8_t second)
{
  extern volatile uint8_t vnd_sync_mode_public;

  rs485_status_master_first = first;
  rs485_status_master_second = second;
  if (((second & RS485_STATUS_MASTER_SELF_PAGE_MASK) ==
       RS485_STATUS_MASTER_SELF_PAGE_VALUE) ||
      ((second & RS485_STATUS_MASTER_SELF_DATA_MASK) ==
       RS485_STATUS_MASTER_SELF_DATA_VALUE) ||
      ((second & RS485_STATUS_SENSOR_EVENT_MASK) ==
       RS485_STATUS_SENSOR_EVENT_VALUE)) {
    rs485_status_master_selector = 0u;
    rs485_status_master_no_reply_slot = 1u;
  } else {
    rs485_status_master_selector =
        rs485_status_id_from_wire((uint8_t)(first & RS485_STATUS_ID_MASK));
    rs485_status_master_no_reply_slot = 0u;
  }

  if (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) {
    rs485_status_note_master_word(first, second);
    if ((second & RS485_STATUS_SENSOR_EVENT_MASK) ==
        RS485_STATUS_SENSOR_EVENT_VALUE) {
      uint8_t master_device_id =
          rs485_status_id_from_wire((uint8_t)(first & RS485_STATUS_ID_MASK));
      uint8_t sensor_index = (uint8_t)((second >> 1) & 0x0Fu);
      uint8_t sensor_active = (uint8_t)(second & 0x01u);
      uint32_t now_ms = HAL_GetTick();

      rs485_sensor_note_remote(
          master_device_id, 0u,
          (uint8_t)((first & RS485_STATUS_OPTIC_BIT) != 0u), now_ms);
      rs485_sensor_note_remote(
          master_device_id, 1u,
          (uint8_t)((first & RS485_STATUS_DET_ADC1_BIT) != 0u), now_ms);
      rs485_sensor_note_remote(
          master_device_id, 2u,
          (uint8_t)((first & RS485_STATUS_DET_ADC2_BIT) != 0u), now_ms);
      rs485_sensor_note_remote(master_device_id, sensor_index,
                               sensor_active, now_ms);
      rs485_sensor_event_rx_count++;
    }
    rs485_identity_note_master_request(first, second);
    rs485_identity_note_master_self_word(first, second);
    rs485_status_apply_master_assignment(first, second);
    rs485_status_begin_window();
  }
}

static uint8_t rs485_status_on_received(uint8_t first, uint8_t second)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t node_id =
      rs485_status_id_from_wire((uint8_t)(first & RS485_STATUS_ID_MASK));
  uint8_t is_sensor_event =
      (uint8_t)(((second & RS485_STATUS_SENSOR_EVENT_MASK) ==
                 RS485_STATUS_SENSOR_EVENT_VALUE) ? 1u : 0u);
  uint32_t now_ms = HAL_GetTick();

  if ((rs485_status_window_active == 0u) ||
      (node_id > RS485_DISCOVERY_MAX_ID)) {
    return 0u;
  }
  if (is_sensor_event != 0u) {
    uint8_t index = node_id;
    uint32_t bit = (1u << index);
    uint8_t sensor_index = (uint8_t)((second >> 1) & 0x0Fu);
    uint8_t sensor_active = (uint8_t)(second & 0x01u);
    uint8_t peer_confirmed =
        rs485_status_confirm_peer_candidate(node_id, now_ms);

    if (peer_confirmed != 0u) {
      if ((rs485_status_peer_last_ms[index] == 0u) ||
          (rs485_status_peer_bytes[index] != first)) {
        need_usb_status_refresh = 1u;
      }
      rs485_status_peer_bytes[index] = first;
      rs485_status_peer_second_bytes[index] = second;
      rs485_status_peer_last_ms[index] = now_ms;
      rs485_status_seen_mask |= bit;
      rs485_discovery_seen_mask |= bit;
      rs485_discovery_scan_mask |= bit;
      rs485_sensor_note_remote(node_id, 0u,
                               (uint8_t)((first & RS485_STATUS_OPTIC_BIT) != 0u),
                               now_ms);
      rs485_sensor_note_remote(node_id, 1u,
                               (uint8_t)((first & RS485_STATUS_DET_ADC1_BIT) != 0u),
                               now_ms);
      rs485_sensor_note_remote(node_id, 2u,
                               (uint8_t)((first & RS485_STATUS_DET_ADC2_BIT) != 0u),
                               now_ms);
      rs485_sensor_note_remote(node_id, sensor_index, sensor_active, now_ms);
    }
    rs485_status_peer_rx_count++;
    rs485_sensor_event_rx_count++;

    if ((peer_confirmed != 0u) &&
        (vnd_sync_mode_public == VND_SYNC_MODE_MASTER)) {
      uint32_t recent_mask =
          rs485_status_get_recent_peer_mask(now_ms, RS485_STATUS_PEER_HOLD_MS);
      rs485_status_set_slave_count_estimate(rs485_count_bits_u32(recent_mask));
    }

    /* If the event came from the selected node it replaces that node's
       regular reply for this cycle.  Otherwise keep the window open for the
       delayed selected-node word. */
    if (node_id != rs485_status_master_selector) {
      return 1u;
    }
  } else if (node_id != rs485_status_master_selector) {
    /* A regular word is valid only for the node selected by the master.
       Keeping the window open rejects collision garbage without losing a
       later valid selected response. */
    return 1u;
  }

  if (is_sensor_event == 0u) {
    rs485_sensor_note_remote(node_id, 0u,
                             (uint8_t)((first & RS485_STATUS_OPTIC_BIT) != 0u),
                             now_ms);
    rs485_sensor_note_remote(node_id, 1u,
                             (uint8_t)((first & RS485_STATUS_DET_ADC1_BIT) != 0u),
                             now_ms);
    rs485_sensor_note_remote(node_id, 2u,
                             (uint8_t)((first & RS485_STATUS_DET_ADC2_BIT) != 0u),
                             now_ms);
  }

  if (rs485_status_slot_response_seen == 0u) {
    rs485_status_slot_response_seen = 1u;
    rs485_status_slot_response_first = first;
    rs485_status_slot_response_second = second;
    rs485_status_peer_rx_count++;
  }
  return 0u;
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
  rs485_sync_start_tx_status_word(rs485_status_build_local_byte(),
                                  rs485_status_build_local_second_byte(rs485_status_master_selector));
  rs485_status_reply_alias_id = 0u;
}

static void rs485_master_status_service(uint32_t now_ms)
{
  if (rs485_master_status_tx_pending == 0u) {
    return;
  }
  if ((int32_t)(now_ms - rs485_master_status_tx_due_ms) < 0) {
    return;
  }
  if ((rs485_tx_busy != 0u) || (rs485_tx_queue_count != 0u)) {
    return;
  }

  rs485_master_status_tx_pending = 0u;
  rs485_status_window_after_tx = RS485_STATUS_WORD_BYTES;
  rs485_sync_start_tx_status_word(rs485_master_status_tx_first,
                                  rs485_master_status_tx_second);
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

static int8_t rs485_compare_uid_bytes_numeric(const uint8_t lhs[12],
                                               const uint8_t rhs[12])
{
  uint32_t lhs_words[3] = {0u, 0u, 0u};
  uint32_t rhs_words[3] = {0u, 0u, 0u};

  memcpy(lhs_words, lhs, sizeof(lhs_words));
  memcpy(rhs_words, rhs, sizeof(rhs_words));
  return rs485_compare_uid_words(lhs_words, rhs_words);
}

static void rs485_role_remember_auto_master(const uint8_t uid[12])
{
  memcpy(rs485_role_auto_master_uid,
         uid,
         sizeof(rs485_role_auto_master_uid));
  rs485_role_auto_master_valid = 1u;
}

static void rs485_role_forget_auto_master(void)
{
  rs485_role_auto_master_valid = 0u;
  memset(rs485_role_auto_master_uid,
         0,
         sizeof(rs485_role_auto_master_uid));
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

  if (rs485_role_enum_state == 2u) {
    if ((rs485_role_enum_candidate_valid == 0u) ||
        (memcmp(uid_bytes,
                rs485_role_enum_candidate_uid,
                sizeof(rs485_role_enum_candidate_uid)) > 0)) {
      memcpy(rs485_role_enum_candidate_uid,
             uid_bytes,
             sizeof(rs485_role_enum_candidate_uid));
      rs485_role_enum_candidate_valid = 1u;
    }
  }
  /* PEER_UID debug-лог отключён для чистого phase-monitor потока. */
}

static uint32_t rs485_uid_get_announce_delay_ms(void)
{
  uint32_t slot = rs485_compute_uid_mix() & (RS485_UID_SLOT_COUNT - 1u);
  return RS485_UID_ANNOUNCE_DELAY_MS + (slot * RS485_UID_SLOT_STEP_MS);
}

static uint8_t rs485_role_frame_checksum(uint8_t magic,
                                         const uint8_t *payload,
                                         uint8_t payload_len)
{
  uint8_t checksum = magic;

  for (uint8_t index = 0u; index < payload_len; index++) {
    checksum ^= payload[index];
  }
  return checksum;
}

static void rs485_role_queue_frame(uint8_t magic,
                                   const uint8_t *payload,
                                   uint8_t payload_len)
{
  rs485_tx_queue_push(magic);
  for (uint8_t index = 0u; index < payload_len; index++) {
    rs485_tx_queue_push(payload[index]);
  }
  rs485_tx_queue_push(rs485_role_frame_checksum(magic, payload, payload_len));
  rs485_tx_kick();
}

static void rs485_node_claim_reset_registry(void)
{
  memset(rs485_node_claim_uid, 0, sizeof(rs485_node_claim_uid));
  memset((void *)rs485_node_claim_last_ms, 0, sizeof(rs485_node_claim_last_ms));
  memset(rs485_node_claim_alt_uid, 0, sizeof(rs485_node_claim_alt_uid));
  memset((void *)rs485_node_claim_alt_last_ms, 0,
         sizeof(rs485_node_claim_alt_last_ms));
  rs485_node_conflict_mask = 0u;

  if ((rs485_local_node_id_assigned != 0u) &&
      (rs485_local_node_id <= RS485_DISCOVERY_MAX_ID)) {
    uint8_t index = rs485_local_node_id;
    memcpy(rs485_node_claim_uid[index], rs485_local_uid_bytes, 12u);
    rs485_node_claim_last_ms[index] = HAL_GetTick();
  }
  need_usb_status_refresh = 1u;
}

static void rs485_node_claim_schedule(uint32_t now_ms, uint8_t retries)
{
  uint32_t slot_delay_ms;

  if (RS485_NODE_CLAIM_TX_ENABLE == 0u) {
    (void)now_ms;
    (void)retries;
    rs485_node_claim_tx_pending = 0u;
    rs485_node_claim_tx_retries = 0u;
    return;
  }

  if ((retries == 0u) ||
      (rs485_local_node_id_assigned == 0u) ||
      (rs485_local_node_id > RS485_DISCOVERY_MAX_ID)) {
    return;
  }

  slot_delay_ms = RS485_ROLE_BUS_QUIET_MS +
                  ((rs485_compute_uid_mix() & 0x1Fu) * 3u);
  rs485_node_claim_tx_pending = 1u;
  rs485_node_claim_tx_retries = retries;
  rs485_node_claim_tx_next_ms = now_ms + slot_delay_ms;
}

static void rs485_node_claim_note(uint8_t node_id, const uint8_t uid[12])
{
  uint8_t index;
  uint32_t now_ms;
  uint32_t bit;

  if ((uid == NULL) ||
      (node_id > RS485_DISCOVERY_MAX_ID) ||
      (memcmp(uid, rs485_local_uid_bytes, 12u) == 0)) {
    return;
  }

  index = node_id;
  bit = (1u << index);
  now_ms = HAL_GetTick();

  if ((rs485_node_claim_last_ms[index] == 0u) ||
      ((now_ms - rs485_node_claim_last_ms[index]) >
       RS485_NODE_CLAIM_HOLD_MS)) {
    memcpy(rs485_node_claim_uid[index], uid, 12u);
    rs485_node_claim_last_ms[index] = now_ms;
    memset(rs485_node_claim_alt_uid[index], 0, 12u);
    rs485_node_claim_alt_last_ms[index] = 0u;
    rs485_node_conflict_mask &= ~bit;
  } else if (memcmp(rs485_node_claim_uid[index], uid, 12u) == 0) {
    rs485_node_claim_last_ms[index] = now_ms;
  } else {
    memcpy(rs485_node_claim_alt_uid[index], uid, 12u);
    rs485_node_claim_alt_last_ms[index] = now_ms;
    rs485_node_conflict_mask |= bit;
  }

  need_usb_status_refresh = 1u;
}

static void rs485_node_claim_service(uint32_t now_ms)
{
  uint8_t payload[RS485_ROLE_NODE_CLAIM_BYTES - 1u];

  for (uint8_t index = 0u; index < RS485_DEVICE_ID_COUNT; index++) {
    uint32_t bit = (1u << index);
    uint8_t primary_is_local =
        (uint8_t)(((rs485_local_node_id_assigned != 0u) &&
                   (rs485_local_node_id == index) &&
                   (memcmp(rs485_node_claim_uid[index],
                           rs485_local_uid_bytes, 12u) == 0)) ? 1u : 0u);

    if ((primary_is_local == 0u) &&
        (rs485_node_claim_last_ms[index] != 0u) &&
        ((now_ms - rs485_node_claim_last_ms[index]) >
         RS485_NODE_CLAIM_HOLD_MS)) {
      if ((rs485_node_claim_alt_last_ms[index] != 0u) &&
          ((now_ms - rs485_node_claim_alt_last_ms[index]) <=
           RS485_NODE_CLAIM_HOLD_MS)) {
        memcpy(rs485_node_claim_uid[index],
               rs485_node_claim_alt_uid[index], 12u);
        rs485_node_claim_last_ms[index] =
            rs485_node_claim_alt_last_ms[index];
      } else {
        memset(rs485_node_claim_uid[index], 0, 12u);
        rs485_node_claim_last_ms[index] = 0u;
      }
      memset(rs485_node_claim_alt_uid[index], 0, 12u);
      rs485_node_claim_alt_last_ms[index] = 0u;
      rs485_node_conflict_mask &= ~bit;
      need_usb_status_refresh = 1u;
    } else if ((rs485_node_claim_alt_last_ms[index] != 0u) &&
               ((now_ms - rs485_node_claim_alt_last_ms[index]) >
                RS485_NODE_CLAIM_HOLD_MS)) {
      memset(rs485_node_claim_alt_uid[index], 0, 12u);
      rs485_node_claim_alt_last_ms[index] = 0u;
      rs485_node_conflict_mask &= ~bit;
      need_usb_status_refresh = 1u;
    }
  }

  if ((rs485_local_node_id_assigned != 0u) &&
      (rs485_local_node_id <= RS485_DISCOVERY_MAX_ID) &&
      (rs485_node_claim_tx_pending == 0u) &&
      ((rs485_node_claim_last_periodic_ms == 0u) ||
       ((now_ms - rs485_node_claim_last_periodic_ms) >=
        RS485_NODE_CLAIM_PERIOD_MS))) {
    rs485_node_claim_last_periodic_ms = now_ms;
    rs485_node_claim_schedule(now_ms, RS485_NODE_CLAIM_RETRIES);
  }

  if ((rs485_node_claim_tx_pending == 0u) ||
      ((int32_t)(now_ms - rs485_node_claim_tx_next_ms) < 0) ||
      (rs485_tx_busy != 0u) ||
      (rs485_tx_queue_count != 0u) ||
      (rs485_status_window_active != 0u)) {
    return;
  }

  /* The service only enters after the current status window has closed.
     Keep this frame short enough to finish in the gap before the next 200 Hz
     SYNC; a legacy 20-byte guard here could cross that edge and be decoded as
     false status IDs. */
  payload[0] = rs485_local_node_id;
  memcpy(payload + 1u, rs485_local_uid_bytes, 12u);
  rs485_role_queue_frame(RS485_ROLE_NODE_CLAIM_MAGIC,
                         payload,
                         (uint8_t)sizeof(payload));

  if (rs485_node_claim_tx_retries > 1u) {
    rs485_node_claim_tx_retries--;
    rs485_node_claim_tx_next_ms = now_ms + RS485_NODE_CLAIM_RETRY_MS;
  } else {
    rs485_node_claim_tx_retries = 0u;
    rs485_node_claim_tx_pending = 0u;
  }
}

uint32_t rs485_node_get_conflict_mask(void)
{
  return rs485_node_conflict_mask;
}

uint8_t rs485_node_local_id_conflict(void)
{
  if ((rs485_local_node_id_assigned == 0u) ||
      (rs485_local_node_id > RS485_DISCOVERY_MAX_ID)) {
    return 0u;
  }
  return (uint8_t)(((rs485_node_conflict_mask &
                     (1u << rs485_local_node_id)) != 0u) ? 1u : 0u);
}

uint8_t rs485_multiple_master_detected(void)
{
  uint32_t last_ms = rs485_foreign_master_last_ms;

  if ((vnd_sync_mode_public != VND_SYNC_MODE_MASTER) ||
      (last_ms == 0u)) {
    return 0u;
  }
  return (uint8_t)(((HAL_GetTick() - last_ms) <=
                    RS485_MULTI_MASTER_HOLD_MS) ? 1u : 0u);
}

static void rs485_role_schedule_auto_heartbeat(uint32_t now_ms,
                                               uint8_t retries)
{
  uint32_t slot_delay_ms =
      RS485_ROLE_BUS_QUIET_MS + ((rs485_compute_uid_mix() & 0x1Fu) * 3u);

  if (retries == 0u) {
    return;
  }
  rs485_role_auto_heartbeat_tx_remaining = retries;
  rs485_role_auto_heartbeat_tx_next_ms = now_ms + slot_delay_ms;
  rs485_sync_tx_suppressed_until_ms =
      now_ms + RS485_UID_ARBITRATION_QUIET_MS;
  if (rs485_status_window_active != 0u) {
    rs485_status_finalize_window();
  }
}

static void rs485_role_auto_heartbeat_tx_service(uint32_t now_ms)
{
  extern volatile uint8_t vnd_sync_mode_public;

  if (rs485_role_auto_heartbeat_tx_remaining == 0u) {
    return;
  }
  if (vnd_sync_mode_public != VND_SYNC_MODE_MASTER) {
    rs485_role_auto_heartbeat_tx_remaining = 0u;
    return;
  }
  if ((int32_t)(now_ms - rs485_role_auto_heartbeat_tx_next_ms) < 0) {
    return;
  }
  if ((rs485_tx_busy != 0u) ||
      (rs485_tx_queue_count != 0u) ||
      (rs485_status_window_active != 0u)) {
    return;
  }

  rs485_role_queue_frame(RS485_ROLE_AUTO_HEARTBEAT_MAGIC,
                         rs485_local_uid_bytes,
                         12u);
  rs485_role_auto_heartbeat_tx_remaining--;
  rs485_role_auto_heartbeat_tx_next_ms =
      now_ms + RS485_ROLE_AUTO_HEARTBEAT_RETRY_MS;
}

static int8_t rs485_role_compare_assignment(uint32_t unix_s,
                                            uint16_t millis,
                                            const uint8_t uid[12])
{
  if (unix_s < rs485_role_master_assigned_unix_s) {
    return -1;
  }
  if (unix_s > rs485_role_master_assigned_unix_s) {
    return 1;
  }
  if (millis < rs485_role_master_assigned_millis) {
    return -1;
  }
  if (millis > rs485_role_master_assigned_millis) {
    return 1;
  }

  /* Two host commands in the same millisecond are ordered by UID so every
     board still reaches the same result. */
  {
    int cmp = memcmp(uid, rs485_role_master_uid, 12u);
    return (cmp < 0) ? -1 : ((cmp > 0) ? 1 : 0);
  }
}

static uint8_t rs485_role_uid_is_zero(const uint8_t uid[12])
{
  uint8_t any = 0u;

  for (uint8_t index = 0u; index < 12u; index++) {
    any |= uid[index];
  }
  return (uint8_t)((any == 0u) ? 1u : 0u);
}

static int8_t rs485_role_compare_slave_assignment(uint32_t unix_s,
                                                  uint16_t millis,
                                                  const uint8_t uid[12])
{
  int8_t time_order = rs485_role_compare_time(unix_s,
                                               millis,
                                               rs485_role_slave_assigned_unix_s,
                                               rs485_role_slave_assigned_millis);

  if (time_order != 0) {
    return time_order;
  }

  /* A clear wins an exact-timestamp tie and remains a tombstone. This keeps a
     delayed old selected-SLAVE frame from reviving the former assignment. */
  if (rs485_role_uid_is_zero(uid) != 0u) {
    return (rs485_role_slave_valid != 0u) ? 1 : 0;
  }
  if (rs485_role_slave_valid == 0u) {
    return -1;
  }

  {
    size_t compare_len = (rs485_role_slave_uid_prefix_only != 0u) ? 8u : 12u;
    int cmp = memcmp(uid, rs485_role_slave_uid, compare_len);
    return (cmp < 0) ? -1 : ((cmp > 0) ? 1 : 0);
  }
}

static void rs485_role_schedule_master_claim(uint8_t reason)
{
#if !RS485_ROLE_NETWORK_ASSIGN_ENABLE
  (void)reason;
  return;
#else
  uint32_t now_ms = HAL_GetTick();
  uint32_t delay_ms = RS485_ROLE_BUS_QUIET_MS;
  uint8_t retries = (reason == RS485_ROLE_CLAIM_PERIODIC) ? 1u : 3u;

  if ((rs485_role_master_valid == 0u) ||
      (rs485_role_local_is_selected_master() == 0u)) {
    return;
  }

  if (reason == RS485_ROLE_CLAIM_BOOT) {
    /* Simultaneously powered boards with different saved assignments must not
       transmit their claims in lockstep. A newer claim received meanwhile
       replaces this delayed one immediately. */
    uint32_t mix = rs485_compute_uid_mix() ^
                   rs485_role_master_assigned_unix_s ^
                   (uint32_t)rs485_role_master_assigned_millis;
    delay_ms += (mix & 0x7Fu);
  }
  rs485_role_claim_tx_pending = 1u;
  rs485_role_claim_tx_force =
      (reason == RS485_ROLE_CLAIM_HOST) ? 1u : 0u;
  rs485_role_claim_tx_retries = retries;
  rs485_role_claim_tx_next_ms = now_ms + delay_ms;
  rs485_sync_tx_suppressed_until_ms =
      now_ms + delay_ms +
      ((uint32_t)(retries - 1u) * RS485_ROLE_CLAIM_RETRY_MS) + 5u;
#endif
}

static void rs485_role_claim_service(uint32_t now_ms)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t payload[RS485_ROLE_CLAIM_PAYLOAD_BYTES - 1u] = {0u};

  if ((rs485_role_claim_tx_pending != 0u) &&
      ((vnd_sync_mode_public != VND_SYNC_MODE_MASTER) ||
       (rs485_role_local_is_selected_master() == 0u))) {
    /* A newer network claim may have demoted us while an old frame was still
       delayed. Never put that obsolete assignment back on the bus. */
    rs485_role_claim_tx_pending = 0u;
    rs485_role_claim_tx_retries = 0u;
    rs485_role_claim_tx_force = 0u;
  }

  if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
      (rs485_role_local_is_selected_master() != 0u) &&
      (rs485_role_claim_tx_pending == 0u) &&
      (rs485_role_enum_state == 0u) &&
      ((rs485_role_claim_last_periodic_ms == 0u) ||
       ((now_ms - rs485_role_claim_last_periodic_ms) >=
        RS485_ROLE_CLAIM_PERIOD_MS))) {
    rs485_role_claim_last_periodic_ms = now_ms;
    rs485_role_schedule_master_claim(RS485_ROLE_CLAIM_PERIODIC);
  }

  if ((rs485_role_claim_tx_pending == 0u) ||
      ((int32_t)(now_ms - rs485_role_claim_tx_next_ms) < 0)) {
    return;
  }
  if ((rs485_tx_busy != 0u) ||
      (rs485_tx_queue_count != 0u) ||
      (rs485_status_window_active != 0u)) {
    return;
  }

  payload[0] = (uint8_t)(0x01u |
                         (rs485_role_claim_tx_force ?
                          RS485_ROLE_CLAIM_FORCE_BIT : 0u));
  payload[1] = (uint8_t)(rs485_role_master_assigned_unix_s & 0xFFu);
  payload[2] = (uint8_t)((rs485_role_master_assigned_unix_s >> 8) & 0xFFu);
  payload[3] = (uint8_t)((rs485_role_master_assigned_unix_s >> 16) & 0xFFu);
  payload[4] = (uint8_t)((rs485_role_master_assigned_unix_s >> 24) & 0xFFu);
  payload[5] = (uint8_t)(rs485_role_master_assigned_millis & 0xFFu);
  payload[6] = (uint8_t)((rs485_role_master_assigned_millis >> 8) & 0xFFu);
  memcpy(payload + 7u, rs485_role_master_uid, 12u);
  rs485_role_queue_frame(RS485_ROLE_CLAIM_MAGIC,
                         payload,
                         (uint8_t)sizeof(payload));

  if (rs485_role_claim_tx_retries > 1u) {
    rs485_role_claim_tx_retries--;
    rs485_role_claim_tx_next_ms = now_ms + RS485_ROLE_CLAIM_RETRY_MS;
  } else {
    rs485_role_claim_tx_retries = 0u;
    rs485_role_claim_tx_pending = 0u;
  }
}

static void rs485_role_schedule_slave_claim(uint8_t reason)
{
#if !RS485_ROLE_NETWORK_ASSIGN_ENABLE
  (void)reason;
  return;
#else
  uint32_t now_ms = HAL_GetTick();
  uint32_t delay_ms = RS485_ROLE_BUS_QUIET_MS;
  uint8_t retries = (reason == RS485_ROLE_CLAIM_PERIODIC) ? 1u : 3u;

  if (rs485_role_slave_epoch_valid == 0u) {
    return;
  }
  if ((rs485_role_local_is_selected_master() == 0u) &&
      (rs485_role_local_is_selected_slave() == 0u)) {
    return;
  }

  if (reason == RS485_ROLE_CLAIM_BOOT) {
    uint32_t mix = rs485_compute_uid_mix() ^
                   rs485_role_slave_assigned_unix_s ^
                   (uint32_t)rs485_role_slave_assigned_millis;
    delay_ms += (mix & 0x7Fu);
  }
  rs485_role_slave_claim_tx_pending = 1u;
  rs485_role_slave_claim_tx_force =
      (reason == RS485_ROLE_CLAIM_HOST) ? 1u : 0u;
  rs485_role_slave_claim_tx_retries = retries;
  rs485_role_slave_claim_tx_next_ms = now_ms + delay_ms;
  if (rs485_role_local_is_selected_master() != 0u) {
    uint32_t until_ms = now_ms + delay_ms +
        ((uint32_t)(retries - 1u) * RS485_ROLE_CLAIM_RETRY_MS) + 5u;
    if ((int32_t)(until_ms - rs485_sync_tx_suppressed_until_ms) > 0) {
      rs485_sync_tx_suppressed_until_ms = until_ms;
    }
  }
#endif
}

static void rs485_role_slave_claim_service(uint32_t now_ms)
{
  extern volatile uint8_t vnd_sync_mode_public;
  uint8_t payload[RS485_ROLE_CLAIM_PAYLOAD_BYTES - 1u] = {0u};
  uint8_t local_is_master = rs485_role_local_is_selected_master();
  uint8_t local_is_slave = rs485_role_local_is_selected_slave();

  if ((rs485_role_slave_claim_tx_pending != 0u) &&
      (local_is_master == 0u) &&
      (local_is_slave == 0u)) {
    rs485_role_slave_claim_tx_pending = 0u;
    rs485_role_slave_claim_tx_retries = 0u;
    rs485_role_slave_claim_tx_force = 0u;
  }

  if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
      (local_is_master != 0u) &&
      (rs485_role_slave_epoch_valid != 0u) &&
      (rs485_role_slave_claim_tx_pending == 0u) &&
      (rs485_role_enum_state == 0u) &&
      ((rs485_role_slave_claim_last_periodic_ms == 0u) ||
       ((now_ms - rs485_role_slave_claim_last_periodic_ms) >=
        RS485_ROLE_CLAIM_PERIOD_MS))) {
    rs485_role_slave_claim_last_periodic_ms = now_ms;
    rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_PERIODIC);
  }

  if ((rs485_role_slave_claim_tx_pending == 0u) ||
      ((int32_t)(now_ms - rs485_role_slave_claim_tx_next_ms) < 0)) {
    return;
  }
  if ((rs485_tx_busy != 0u) ||
      (rs485_tx_queue_count != 0u) ||
      (rs485_status_window_active != 0u)) {
    return;
  }

  if (rs485_role_slave_valid != 0u) {
    uint8_t node_id = rs485_role_selected_slave_node_id;
    if (node_id > RS485_DISCOVERY_MAX_ID) {
      node_id = 0u;
    }
    payload[0] = (uint8_t)(RS485_ROLE_SLAVE_VALID_BIT |
                           ((node_id << RS485_ROLE_SLAVE_NODE_SHIFT) &
                            RS485_ROLE_SLAVE_NODE_MASK));
    memcpy(payload + 7u, rs485_role_slave_uid, 12u);
  }
  if (rs485_role_slave_claim_tx_force != 0u) {
    payload[0] |= RS485_ROLE_CLAIM_FORCE_BIT;
  }
  payload[1] = (uint8_t)(rs485_role_slave_assigned_unix_s & 0xFFu);
  payload[2] = (uint8_t)((rs485_role_slave_assigned_unix_s >> 8) & 0xFFu);
  payload[3] = (uint8_t)((rs485_role_slave_assigned_unix_s >> 16) & 0xFFu);
  payload[4] = (uint8_t)((rs485_role_slave_assigned_unix_s >> 24) & 0xFFu);
  payload[5] = (uint8_t)(rs485_role_slave_assigned_millis & 0xFFu);
  payload[6] = (uint8_t)((rs485_role_slave_assigned_millis >> 8) & 0xFFu);
  rs485_role_queue_frame(RS485_ROLE_SLAVE_CLAIM_MAGIC,
                         payload,
                         (uint8_t)sizeof(payload));

  if (rs485_role_slave_claim_tx_retries > 1u) {
    rs485_role_slave_claim_tx_retries--;
    rs485_role_slave_claim_tx_next_ms = now_ms + RS485_ROLE_CLAIM_RETRY_MS;
  } else {
    rs485_role_slave_claim_tx_retries = 0u;
    rs485_role_slave_claim_tx_pending = 0u;
  }
}

static uint32_t rs485_role_enum_response_delay_ms(uint8_t round)
{
  uint32_t value = rs485_compute_uid_mix() ^
                   ((uint32_t)round * 0x9E3779B9u);

  value ^= value >> 16;
  value *= 0x7FEB352Du;
  value ^= value >> 15;
  /* 32 two-millisecond UID slots fit inside a sub-100 ms commissioning
     window. Collisions are resolved by changing the hash with every round. */
  return 12u + ((value & 0x1Fu) * 2u);
}

static uint8_t rs485_role_max_id_from_mask(uint32_t mask)
{
  for (uint8_t node_id = RS485_DISCOVERY_MAX_ID; node_id != 0u; node_id--) {
    if ((mask & (1u << (node_id - 1u))) != 0u) {
      return node_id;
    }
  }
  return 0u;
}

static void rs485_role_start_enumeration_epoch(uint32_t now_ms)
{
#if !RS485_ROLE_AUTO_ENUM_ENABLE
  (void)now_ms;
  rs485_role_enum_state = 0u;
  rs485_role_enum_reset_tx_remaining = 0u;
  rs485_role_enum_ack_tx_pending = 0u;
  rs485_role_enum_waiting_assignment = 0u;
  return;
#else
  /* The RPI-assigned MASTER is authoritative for the complete numbering pass.
     Never reuse a saved high-water value here: it can describe a different
     network partition and can hide duplicated persisted slave ids. */
  rs485_role_auto_probe_active = 0u;
  rs485_role_auto_heartbeat_tx_remaining = 0u;
  rs485_role_enum_waiting_assignment = 0u;
  rs485_role_reset_local_assignment_epoch();
  rs485_role_enum_reset_tx_remaining = RS485_ROLE_ENUM_RESET_RETRIES;
  rs485_role_enum_reset_tx_next_ms = now_ms + RS485_ROLE_BUS_QUIET_MS;
  rs485_role_enum_next_ms = now_ms + RS485_ROLE_ENUM_SETTLE_MS;
  rs485_role_mark_persist_dirty();
#endif
}

static void rs485_role_enumeration_service(uint32_t now_ms)
{
#if !RS485_ROLE_AUTO_ENUM_ENABLE
  (void)now_ms;
  rs485_role_enum_state = 0u;
  rs485_role_enum_reset_tx_remaining = 0u;
  rs485_role_enum_ack_tx_pending = 0u;
  return;
#else
  extern volatile uint8_t vnd_sync_mode_public;
  uint32_t recent_mask = 0u;
  uint8_t recent_max_id = 0u;

  if ((rs485_role_enum_ack_tx_pending != 0u) &&
      ((int32_t)(now_ms - rs485_role_enum_ack_tx_next_ms) >= 0) &&
      (rs485_tx_busy == 0u) &&
      (rs485_tx_queue_count == 0u) &&
      (rs485_status_window_active == 0u)) {
    uint8_t payload[RS485_ROLE_ENUM_ACK_BYTES - 1u];
    payload[0] = rs485_role_enum_ack_tx_id;
    memcpy(payload + 1u, rs485_local_uid_bytes, 12u);
    rs485_role_queue_frame(RS485_ROLE_ENUM_ACK_MAGIC,
                           payload,
                           (uint8_t)sizeof(payload));
    rs485_role_enum_ack_tx_pending = 0u;
  }

  if (vnd_sync_mode_public != VND_SYNC_MODE_MASTER) {
    rs485_role_enum_state = 0u;
    rs485_role_enum_reset_tx_remaining = 0u;
    return;
  }
  if ((rs485_role_master_valid != 0u) &&
      (rs485_role_local_is_selected_master() == 0u)) {
    rs485_role_enum_state = 0u;
    return;
  }
  if ((rs485_role_auto_probe_active != 0u) ||
      (rs485_role_auto_heartbeat_tx_remaining != 0u)) {
    return;
  }

  if (rs485_role_enum_reset_tx_remaining != 0u) {
    uint8_t payload[RS485_ROLE_ENUM_RESET_BYTES - 1u];

    if ((int32_t)(now_ms - rs485_role_enum_reset_tx_next_ms) < 0) {
      return;
    }
    if ((rs485_tx_busy != 0u) ||
        (rs485_tx_queue_count != 0u) ||
        (rs485_status_window_active != 0u) ||
        (rs485_role_claim_tx_pending != 0u) ||
        (rs485_role_slave_claim_tx_pending != 0u)) {
      return;
    }

    /* A slave may still be waiting for the two status bytes following the
       last sync marker, or for the tail of a truncated framed message. Drain
       every such parser before E7. Without this guard a non-rebooted S01 can
       consume the E7 magic as status data, keep its old id, and collide with
       the newly assigned S01 until the six-second recovery epoch. */
    for (uint8_t index = 0u; index < RS485_ROLE_ENUM_GUARD_BYTES; index++) {
      rs485_tx_queue_push(0u);
    }
    memcpy(payload, rs485_local_uid_bytes, sizeof(payload));
    rs485_role_queue_frame(RS485_ROLE_ENUM_RESET_MAGIC,
                           payload,
                           (uint8_t)sizeof(payload));
    rs485_role_enum_reset_tx_remaining--;
    rs485_role_enum_reset_tx_next_ms =
        now_ms + RS485_ROLE_ENUM_RESET_RETRY_MS;
    rs485_role_enum_next_ms = now_ms + RS485_ROLE_ENUM_SETTLE_MS;
    rs485_sync_tx_suppressed_until_ms =
        now_ms + RS485_ROLE_ENUM_RESET_RETRY_MS + 5u;
    return;
  }

  recent_mask = rs485_status_get_recent_peer_mask(now_ms,
                                                   RS485_STATUS_ASSIGN_ID_HOLD_MS);
  recent_max_id = rs485_role_max_id_from_mask(recent_mask);
  if ((recent_mask != 0u) &&
      (recent_mask == rs485_status_make_contiguous_mask(recent_max_id))) {
    if (recent_mask != rs485_role_enum_stable_mask) {
      rs485_role_enum_stable_mask = recent_mask;
      rs485_role_enum_stable_since_ms = now_ms;
    } else if ((rs485_role_enum_state == 0u) &&
               (rs485_role_slave_id_high_water > recent_max_id) &&
               ((now_ms - rs485_role_enum_stable_since_ms) >=
                RS485_ROLE_ENUM_RECONCILE_MS)) {
      /* A stale saved high-water value must not make the next slave skip from
         S03 to S23. Reconcile downward only after the exact contiguous mask
         has remained unchanged long enough to exclude an in-flight ACK. */
      rs485_role_slave_id_high_water = recent_max_id;
      rs485_role_mark_persist_dirty();
    }
  } else if (recent_mask != rs485_role_enum_stable_mask) {
    rs485_role_enum_stable_mask = recent_mask;
    rs485_role_enum_stable_since_ms = now_ms;
  }
  if ((recent_mask == 0u) &&
      (rs485_role_enum_state == 0u) &&
      (rs485_role_slave_id_high_water != 0u) &&
      ((now_ms - rs485_role_enum_stable_since_ms) >=
       RS485_ROLE_ENUM_RECONCILE_MS)) {
    /* If all confirmed status slots disappeared, a saturated/stale high-water
       must not strand returning boards at S-- forever. Start one clean epoch;
       its UID-addressed ACKs rebuild the contiguous list from S01. */
    rs485_role_start_enumeration_epoch(now_ms);
    return;
  }
  /* Never advance the allocator from anonymous status slots. During role
     changes a delayed/colliding status byte can look like an arbitrary node
     id. Only the UID96-matched ENUM_ACK below is authoritative for consuming
     a new id. The status mask is used only for conservative downward repair. */

  if (rs485_role_slave_id_high_water >= RS485_DISCOVERY_MAX_ID) {
    return;
  }

  if (rs485_role_enum_state == 0u) {
    if ((int32_t)(now_ms - rs485_role_enum_next_ms) < 0) {
      return;
    }
    if ((rs485_role_claim_tx_pending != 0u) ||
        (rs485_role_slave_claim_tx_pending != 0u)) {
      return;
    }

    rs485_role_enum_round++;
    if (rs485_role_enum_round == 0u) {
      rs485_role_enum_round = 1u;
    }
    if (rs485_role_enum_collect_pass == 0u) {
      rs485_role_enum_target_id =
          (uint8_t)(rs485_role_slave_id_high_water + 1u);
      rs485_role_enum_candidate_valid = 0u;
      memset(rs485_role_enum_candidate_uid,
             0,
             sizeof(rs485_role_enum_candidate_uid));
    }
    /* Close the current status window before suppressing new sync markers.
       Otherwise the ISR returns at the suppression check and leaves
       status_window_active latched, so state 1 can wait forever. */
    if (rs485_status_window_active != 0u) {
      rs485_status_finalize_window();
    }
    rs485_sync_tx_suppressed_until_ms =
        now_ms + RS485_ROLE_BUS_QUIET_MS +
        RS485_ROLE_ENUM_COLLECT_MS + 30u;
    /* A non-addressed slave deliberately keeps its last status window open.
       Drain that two-byte state, and also any truncated UID/role parser, with
       zeros before the framed E3 request. Zero cannot start any protocol
       frame; the longest pending parser consumes at most 20 bytes. */
    for (uint8_t index = 0u; index < RS485_ROLE_ENUM_GUARD_BYTES; index++) {
      rs485_tx_queue_push(0u);
    }
    rs485_tx_kick();
    rs485_role_enum_state = 1u;
    rs485_role_enum_deadline_ms = now_ms + RS485_ROLE_BUS_QUIET_MS;
    return;
  }

  if (rs485_role_enum_state == 1u) {
    uint8_t payload[RS485_ROLE_ENUM_REQ_BYTES - 1u];
    if ((int32_t)(now_ms - rs485_role_enum_deadline_ms) < 0) {
      return;
    }
    if ((rs485_tx_busy != 0u) ||
        (rs485_tx_queue_count != 0u) ||
        (rs485_status_window_active != 0u)) {
      return;
    }
    payload[0] = rs485_role_enum_round;
    payload[1] = rs485_role_enum_target_id;
    rs485_role_queue_frame(RS485_ROLE_ENUM_REQ_MAGIC,
                           payload,
                           (uint8_t)sizeof(payload));
    rs485_role_enum_state = 2u;
    rs485_role_enum_deadline_ms = now_ms + RS485_ROLE_ENUM_COLLECT_MS;
    return;
  }

  if (rs485_role_enum_state == 2u) {
    if ((int32_t)(now_ms - rs485_role_enum_deadline_ms) < 0) {
      return;
    }
    if ((uint8_t)(rs485_role_enum_collect_pass + 1u) <
        RS485_ROLE_ENUM_COLLECT_PASSES) {
      /* Run every differently hashed response round even when no UID was
         decoded in the first one. A collision can hide all first-round
         replies; stopping there incorrectly creates a 10-second idle retry.
         The candidate, once received, is accumulated across all passes. */
      rs485_role_enum_collect_pass++;
      rs485_role_enum_state = 0u;
      rs485_role_enum_next_ms = now_ms + RS485_ROLE_BUS_QUIET_MS;
      return;
    }
    if (rs485_role_enum_candidate_valid != 0u) {
      uint8_t payload[RS485_ROLE_ENUM_ASSIGN_BYTES - 1u];
      if ((rs485_tx_busy != 0u) || (rs485_tx_queue_count != 0u)) {
        return;
      }
      payload[0] = rs485_role_enum_target_id;
      memcpy(payload + 1u, rs485_role_enum_candidate_uid, 12u);
      rs485_role_queue_frame(RS485_ROLE_ENUM_ASSIGN_MAGIC,
                             payload,
                             (uint8_t)sizeof(payload));
      rs485_role_enum_assign_retries = 2u;
      rs485_role_enum_ack_received = 0u;
      rs485_role_enum_state = 3u;
      rs485_role_enum_next_ms = now_ms + RS485_ROLE_ENUM_ASSIGN_RETRY_MS;
      rs485_role_enum_deadline_ms = now_ms + RS485_ROLE_ENUM_ACK_TIMEOUT_MS;
      rs485_sync_tx_suppressed_until_ms =
          now_ms + RS485_ROLE_ENUM_ACK_TIMEOUT_MS + 10u;
    } else {
      rs485_role_enum_collect_pass = 0u;
      rs485_role_enum_state = 0u;
      rs485_role_enum_next_ms =
          now_ms + ((rs485_role_slave_id_high_water != 0u)
                        ? RS485_ROLE_ENUM_IDLE_RETRY_MS
                        : RS485_ROLE_ENUM_RETRY_MS);
    }
    return;
  }

  if (rs485_role_enum_state == 3u) {
    if (rs485_role_enum_ack_received != 0u) {
      rs485_role_slave_id_high_water = rs485_role_enum_target_id;
      rs485_role_enum_collect_pass = 0u;
      rs485_role_mark_persist_dirty();
      rs485_role_enum_stable_mask = 0u;
      rs485_role_enum_stable_since_ms = now_ms;
      rs485_role_enum_state = 0u;
      rs485_role_enum_next_ms = now_ms + RS485_ROLE_ENUM_SETTLE_MS;
      rs485_sync_tx_suppressed_until_ms = now_ms;
      return;
    }

    if ((rs485_role_enum_assign_retries != 0u) &&
        ((int32_t)(now_ms - rs485_role_enum_next_ms) >= 0) &&
        (rs485_tx_busy == 0u) &&
        (rs485_tx_queue_count == 0u)) {
      uint8_t payload[RS485_ROLE_ENUM_ASSIGN_BYTES - 1u];
      payload[0] = rs485_role_enum_target_id;
      memcpy(payload + 1u, rs485_role_enum_candidate_uid, 12u);
      rs485_role_queue_frame(RS485_ROLE_ENUM_ASSIGN_MAGIC,
                             payload,
                             (uint8_t)sizeof(payload));
      rs485_role_enum_assign_retries--;
      rs485_role_enum_next_ms = now_ms + RS485_ROLE_ENUM_ASSIGN_RETRY_MS;
      return;
    }

    if ((int32_t)(now_ms - rs485_role_enum_deadline_ms) >= 0) {
      /* Do not consume the id without an UID-addressed acknowledgement. The
         next round retries the same high-water+1 value. */
      rs485_role_enum_collect_pass = 0u;
      rs485_role_enum_state = 0u;
      rs485_role_enum_next_ms = now_ms + RS485_ROLE_ENUM_SETTLE_MS;
      rs485_sync_tx_suppressed_until_ms = now_ms;
    }
  }
#endif
}

static void rs485_role_reset_rx(void)
{
  rs485_role_rx_magic = 0u;
  rs485_role_rx_index = 0u;
  rs485_role_rx_expected = 0u;
}

static void rs485_role_rx_service(void)
{
  for (uint8_t drained = 0u; drained < RS485_ROLE_PENDING_CAPACITY; drained++) {
    uint8_t magic = 0u;
    uint8_t payload[20];
    uint32_t primask = __get_PRIMASK();

    __disable_irq();
    if (rs485_role_frame_pending_read != rs485_role_frame_pending_write) {
      uint8_t read_index = rs485_role_frame_pending_read;
      magic = rs485_role_frame_pending_magic[read_index];
      memcpy(payload,
             rs485_role_frame_pending_buf[read_index],
             sizeof(payload));
      rs485_role_frame_pending_magic[read_index] = 0u;
      rs485_role_frame_pending_read =
          (uint8_t)((read_index + 1u) % RS485_ROLE_PENDING_CAPACITY);
    }
    if (primask == 0u) {
      __enable_irq();
    }

    if (magic == 0u) {
      break;
    }
    rs485_role_process_frame(magic, payload);
  }
}

static void rs485_role_process_frame(uint8_t magic, const uint8_t *payload)
{
  extern volatile uint8_t vnd_sync_mode_public;

  if (payload == NULL) {
    return;
  }

  if (magic == RS485_ROLE_NODE_CLAIM_MAGIC) {
    rs485_node_claim_note(payload[0], payload + 1u);
    return;
  }

#if !RS485_ROLE_NETWORK_ASSIGN_ENABLE
  if ((magic == RS485_ROLE_CLAIM_MAGIC) ||
      (magic == RS485_ROLE_SLAVE_CLAIM_MAGIC)) {
    /* Roles are local persistent configuration. A peer may report its role,
       but it is never allowed to rewrite this board's role. */
    return;
  }
#endif
#if !RS485_ROLE_AUTO_ENUM_ENABLE
  if ((magic == RS485_ROLE_ENUM_REQ_MAGIC) ||
      (magic == RS485_ROLE_ENUM_ASSIGN_MAGIC) ||
      (magic == RS485_ROLE_ENUM_ACK_MAGIC) ||
      (magic == RS485_ROLE_ENUM_RESET_MAGIC)) {
    return;
  }
#endif

  if (magic == RS485_ROLE_CLAIM_MAGIC) {
    uint8_t flags = payload[0];
    uint32_t unix_s = (uint32_t)payload[1] |
                      ((uint32_t)payload[2] << 8) |
                      ((uint32_t)payload[3] << 16) |
                      ((uint32_t)payload[4] << 24);
    uint16_t millis = (uint16_t)payload[5] |
                      ((uint16_t)payload[6] << 8);
    const uint8_t *master_uid = payload + 7u;
    int8_t order = 1;
    uint8_t assignment_epoch_changed = 0u;
    uint8_t clears_selected_slave = 0u;

    if (((flags & 0x01u) == 0u) ||
        (unix_s == 0u) ||
        (millis >= 1000u)) {
      return;
    }
    if (rs485_role_slave_uid_matches(master_uid) != 0u) {
      if (rs485_role_compare_time(unix_s,
                                  millis,
                                  rs485_role_slave_assigned_unix_s,
                                  rs485_role_slave_assigned_millis) < 0) {
        return;
      }
      clears_selected_slave = 1u;
    }
    if (rs485_role_master_valid != 0u) {
      order = rs485_role_compare_assignment(unix_s, millis, master_uid);
    }
    if (order < 0) {
      if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
          (rs485_role_local_is_selected_master() != 0u)) {
        rs485_role_schedule_master_claim(RS485_ROLE_CLAIM_PERIODIC);
      }
      return;
    }
    if ((order == 0) &&
        (memcmp(master_uid, rs485_role_master_uid, 12u) == 0)) {
      if (clears_selected_slave != 0u) {
        rs485_role_clear_selected_slave_at(unix_s, millis);
        rs485_role_mark_persist_dirty_immediate();
        need_usb_status_refresh = 1u;
        if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
            (rs485_role_local_is_selected_master() != 0u)) {
          rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_HOST);
        }
      }
      return;
    }

    assignment_epoch_changed =
        (uint8_t)((rs485_role_master_valid == 0u) ||
                  (rs485_role_master_assigned_unix_s != unix_s) ||
                  (rs485_role_master_assigned_millis != millis) ||
                  (memcmp(master_uid, rs485_role_master_uid, 12u) != 0));
    if (assignment_epoch_changed != 0u) {
      rs485_role_reset_local_assignment_epoch();
    }
    memcpy(rs485_role_master_uid, master_uid, 12u);
    rs485_role_master_assigned_unix_s = unix_s;
    rs485_role_master_assigned_millis = millis;
    rs485_role_master_valid = 1u;
    if (clears_selected_slave != 0u) {
      rs485_role_clear_selected_slave_at(unix_s, millis);
    }
    rs485_role_boot_listen_active = 0u;
    rs485_role_mark_persist_dirty_immediate();

    if (memcmp(master_uid, rs485_local_uid_bytes, 12u) != 0) {
      if (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) {
        vnd_sync_apply_network_mode(VND_SYNC_MODE_SLAVE);
      }
    }
    return;
  }

  if (magic == RS485_ROLE_SLAVE_CLAIM_MAGIC) {
    uint8_t flags = payload[0];
    uint8_t slave_valid =
        ((flags & RS485_ROLE_SLAVE_VALID_BIT) != 0u) ? 1u : 0u;
    uint8_t selected_node =
        (uint8_t)((flags & RS485_ROLE_SLAVE_NODE_MASK) >>
                  RS485_ROLE_SLAVE_NODE_SHIFT);
    uint32_t unix_s = (uint32_t)payload[1] |
                      ((uint32_t)payload[2] << 8) |
                      ((uint32_t)payload[3] << 16) |
                      ((uint32_t)payload[4] << 24);
    uint16_t millis = (uint16_t)payload[5] |
                      ((uint16_t)payload[6] << 8);
    const uint8_t *slave_uid = payload + 7u;
    uint8_t zero_uid = rs485_role_uid_is_zero(slave_uid);
    int8_t order = 1;
    uint8_t was_local_selected = rs485_role_local_is_selected_slave();

    if ((unix_s == 0u) ||
        (millis >= 1000u) ||
        ((slave_valid != 0u) && (zero_uid != 0u)) ||
        ((slave_valid == 0u) && (zero_uid == 0u)) ||
        (selected_node > RS485_DISCOVERY_MAX_ID)) {
      return;
    }
    if ((slave_valid != 0u) &&
        (rs485_role_master_valid != 0u) &&
        (memcmp(slave_uid, rs485_role_master_uid, 12u) == 0)) {
      return;
    }
    if (rs485_role_slave_epoch_valid != 0u) {
      order = rs485_role_compare_slave_assignment(unix_s,
                                                   millis,
                                                   slave_uid);
    }
    if (order < 0) {
      if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
          (rs485_role_local_is_selected_master() != 0u)) {
        rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_PERIODIC);
      }
      return;
    }
    if ((order == 0) &&
        (slave_valid == rs485_role_slave_valid)) {
      uint8_t claim_changed = 0u;
      if ((slave_valid != 0u) &&
          (rs485_role_slave_uid_prefix_only != 0u)) {
        memcpy(rs485_role_slave_uid, slave_uid, 12u);
        rs485_role_slave_uid_prefix_only = 0u;
        rs485_role_mark_persist_dirty();
        claim_changed = 1u;
      }
      if ((slave_valid != 0u) &&
          (selected_node != 0u) &&
          (rs485_role_selected_slave_node_id != selected_node)) {
        rs485_role_selected_slave_node_id = selected_node;
        rs485_role_mark_persist_dirty();
        claim_changed = 1u;
      }
      if (claim_changed != 0u) {
        need_usb_status_refresh = 1u;
        if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
            (rs485_role_local_is_selected_master() != 0u)) {
          rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_PERIODIC);
        }
      }
      return;
    }

    rs485_role_slave_epoch_valid = 1u;
    rs485_role_slave_assigned_unix_s = unix_s;
    rs485_role_slave_assigned_millis = millis;
    rs485_role_slave_uid_prefix_only = 0u;
    rs485_role_slave_valid = slave_valid;
    rs485_role_selected_slave_node_id =
        (slave_valid != 0u) ? selected_node : 0u;
    if (slave_valid != 0u) {
      memcpy(rs485_role_slave_uid, slave_uid, 12u);
      if (rs485_role_local_is_selected_slave() != 0u) {
        if ((rs485_local_node_id_assigned != 0u) &&
            (rs485_local_node_id != 0u)) {
          rs485_role_selected_slave_node_id = rs485_local_node_id;
        }
        vnd_sync_apply_network_mode(VND_SYNC_MODE_SLAVE);
      }
    } else {
      memset(rs485_role_slave_uid, 0, sizeof(rs485_role_slave_uid));
    }
    if ((was_local_selected != 0u) &&
        (rs485_role_local_is_selected_slave() == 0u) &&
        (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE)) {
      vnd_sync_apply_network_mode(VND_SYNC_MODE_SLAVE);
    }
    rs485_role_mark_persist_dirty_immediate();
    need_usb_status_refresh = 1u;
    if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
        (rs485_role_local_is_selected_master() != 0u)) {
      rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_PERIODIC);
    }
    return;
  }

  if (magic == RS485_ROLE_AUTO_HEARTBEAT_MAGIC) {
    /* Legacy UID-election heartbeat. Roles are assigned only by RPI, so this
       frame is intentionally ignored and can never change a board's role. */
    return;
  }

  if (magic == RS485_ROLE_ENUM_RESET_MAGIC) {
    const uint8_t *master_uid = payload;
    uint8_t accept_reset = 0u;
    uint32_t now_ms = HAL_GetTick();

    if (memcmp(master_uid, rs485_local_uid_bytes, 12u) == 0) {
      return;
    }
    if (rs485_role_master_valid != 0u) {
      if (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) {
        return;
      }
      accept_reset =
          (memcmp(master_uid, rs485_role_master_uid, 12u) == 0) ? 1u : 0u;
    } else {
      /* E7 is accepted only from the MASTER UID previously assigned by RPI.
         Without a saved assignment no peer may invent an automatic role. */
      return;
    }
    if (accept_reset != 0u) {
      rs485_role_reset_local_assignment_epoch();
      rs485_role_enum_waiting_assignment = 1u;
      rs485_last_rx_ms = now_ms;
      rs485_role_mark_persist_dirty();
      need_usb_status_refresh = 1u;
    }
    return;
  }

  if (magic == RS485_ROLE_ENUM_REQ_MAGIC) {
    uint8_t round = payload[0];
    uint8_t target_id = payload[1];
    if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
        (target_id != 0u) &&
        (target_id <= RS485_DISCOVERY_MAX_ID)) {
      uint32_t now_ms = HAL_GetTick();
      rs485_last_rx_ms = now_ms;
      if ((rs485_local_node_id_assigned == 0u) ||
          (rs485_local_node_id == target_id)) {
        /* A slave which accepted E4 but whose ACK was lost also answers for
           the same target. The MASTER then retries that UID instead of giving
           the already-used number to another board. */
        rs485_uid_schedule_announce(now_ms,
                                    1u,
                                    rs485_role_enum_response_delay_ms(round));
      }
    }
    return;
  }

  if (magic == RS485_ROLE_ENUM_ASSIGN_MAGIC) {
    uint8_t assigned_id = payload[0];
    const uint8_t *target_uid = payload + 1u;
    if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
        (assigned_id != 0u) &&
        (assigned_id <= RS485_DISCOVERY_MAX_ID) &&
        (memcmp(target_uid, rs485_local_uid_bytes, 12u) == 0)) {
      uint8_t old_id = rs485_local_node_id;
      rs485_local_node_id = assigned_id;
      rs485_local_node_id_assigned = 1u;
      rs485_role_slave_id_high_water = assigned_id;
      rs485_role_enum_waiting_assignment = 0u;
      rs485_role_last_confirmed_node_id = assigned_id;
      rs485_status_slave_reply_div_counter = 0u;
      rs485_identity_note_local_id_change(old_id, assigned_id);
      if (rs485_role_local_is_selected_slave() != 0u) {
        rs485_role_selected_slave_node_id = assigned_id;
        rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_HOST);
      }
      rs485_role_mark_persist_dirty();
      rs485_role_enum_ack_tx_id = assigned_id;
      rs485_role_enum_ack_tx_pending = 1u;
      rs485_role_enum_ack_tx_next_ms = HAL_GetTick() + RS485_ROLE_BUS_QUIET_MS;
    }
    return;
  }

  if (magic == RS485_ROLE_ENUM_ACK_MAGIC) {
    uint8_t assigned_id = payload[0];
    const uint8_t *assigned_uid = payload + 1u;

    if ((assigned_id == 0u) ||
        (assigned_id > RS485_DISCOVERY_MAX_ID)) {
      return;
    }
    /* Never let an unsolicited, delayed or falsely framed E5 byte sequence
       change numbering. Only the current MASTER may accept the exact ACK for
       its in-flight target id and UID96. */
    if ((vnd_sync_mode_public != VND_SYNC_MODE_MASTER) ||
        ((rs485_role_master_valid != 0u) &&
         (rs485_role_local_is_selected_master() == 0u)) ||
        (rs485_role_enum_state != 3u) ||
        (assigned_id != rs485_role_enum_target_id) ||
        (memcmp(assigned_uid,
                rs485_role_enum_candidate_uid,
                sizeof(rs485_role_enum_candidate_uid)) != 0)) {
      return;
    }
    if (rs485_role_slave_uid_matches(assigned_uid) != 0u) {
      memcpy(rs485_role_slave_uid, assigned_uid, 12u);
      rs485_role_slave_uid_prefix_only = 0u;
      rs485_role_selected_slave_node_id = assigned_id;
      rs485_role_mark_persist_dirty();
      need_usb_status_refresh = 1u;
      if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
          (rs485_role_local_is_selected_master() != 0u)) {
        rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_PERIODIC);
      }
    }
    rs485_role_enum_ack_received = 1u;
  }
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

static void rs485_sync_start_tx_status_word(uint8_t first, uint8_t second)
{
  if (rs485_tx_busy != 0u) {
    rs485_tx_queue_push_front(second);
    rs485_tx_queue_push_front(first);
    return;
  }

  rs485_tx_queue_push_front(second);
  rs485_sync_start_tx_byte(first);
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
  if ((rs485_local_node_id_assigned == 0u) ||
      (rs485_local_node_id == 0u) ||
      (request_id != rs485_local_node_id)) {
    return;
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
  rs485_status_set_slave_count_estimate(rs485_count_bits_u32(updated_mask));
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
  rs485_master_status_service(HAL_GetTick());
  rs485_tx_kick();
}

static void rs485_sync_assigned_role_service(uint32_t now_ms)
{
  extern uint8_t vnd_sync_is_mode_host_forced(void);
  extern volatile uint8_t vnd_sync_mode_public;
  static uint8_t role_init_done = 0u;
  static uint8_t prev_mode = 0xFFu;

  if (vnd_sync_mode_public != prev_mode) {
    prev_mode = vnd_sync_mode_public;
    rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
    rs485_sync_relation_score = 0;
    rs485_anti_phase_recovery_active = 0u;
    rs485_anti_phase_recovery_packets = 0u;
    rs485_phase_guard_recovery_packets = 0u;
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
    if ((vnd_sync_mode_public == VND_SYNC_MODE_MASTER) &&
        (rs485_role_local_is_selected_master() != 0u)) {
      rs485_role_start_enumeration_epoch(now_ms);
    }
    rs485_role_auto_probe_active = 0u;
    rs485_role_auto_heartbeat_tx_remaining = 0u;
    tim15_request_hold_offset(0);
  }

  if (!role_init_done) {
    role_init_done = 1u;
    rs485_last_rx_ms = 0u;
    rs485_reply_pending_id = 0u;
    rs485_slave_count_estimate = 0u;
    rs485_discovery_seen_mask = 0u;
    rs485_discovery_reset_master_scan();
    rs485_status_assignment_reset();
    rs485_role_boot_listen_active = 0u;
  }

  /* There is no automatic MASTER election. RPI is the only role authority.
     A blank/erased Flash record leaves the board passive as SLAVE until RPI
     assigns a MASTER. Explicit local OFF remains respected. */
  if (vnd_sync_is_mode_host_forced() != 0u) {
    return;
  }
  if (rs485_role_master_valid == 0u) {
    if (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) {
      vnd_sync_apply_network_mode(VND_SYNC_MODE_SLAVE);
    }
    return;
  }

  if (rs485_role_local_is_selected_master() != 0u) {
    if (vnd_sync_mode_public != VND_SYNC_MODE_MASTER) {
      vnd_sync_apply_network_mode(VND_SYNC_MODE_MASTER);
      rs485_role_schedule_master_claim(RS485_ROLE_CLAIM_BOOT);
      if (rs485_role_slave_epoch_valid != 0u) {
        rs485_role_schedule_slave_claim(RS485_ROLE_CLAIM_BOOT);
      }
    }
  } else if (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) {
    vnd_sync_apply_network_mode(VND_SYNC_MODE_SLAVE);
  }
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
#define TIM15_SYNC_PULSE_MAX_BITS_DEN     1u
#define TIM15_SYNC_QUIET_ENTER_BITS       2u
#define TIM15_SYNC_QUIET_EXIT_BITS        32u
#define TIM15_SYNC_QUIET_ENTER_PACKETS    1u
#define TIM15_SYNC_QUIET_FILTER_DIVISOR   8u
#define TIM15_SYNC_QUIET_MAX_OFFSET       8
#define TIM15_SYNC_QUIET_SPACING_BUFFERS  2u
#define TIM15_SYNC_NEAR_RAW_BITS          2u
#define TIM15_SYNC_NEAR_RAW_MAX_OFFSET    1
#define TIM15_SYNC_NEAR_RAW_SPACING       16u
#define TIM15_SYNC_NEAR_RAW_ACQUIRE_MAX_OFFSET 8
#define TIM15_SYNC_NEAR_RAW_ACQUIRE_SPACING    2u
#define TIM15_SYNC_SLOW_SPACING_BUFFERS   128u
#define TIM15_SYNC_PHASE_PULSE_ENABLE     1u
/* Базовая точка ARR для SLAVE берётся из текущего активного профиля TIM15,
 * а не из жёстко прошитого значения: профили 300/400 Hz имеют разный номинал.
 * 1 шаг = 1 тик ARR.
 */
static volatile uint32_t g_tim15_slave_base_arr = 0u;
static volatile int32_t g_tim15_slave_arr_step_ticks = 1;
static volatile uint8_t sync_phase_quiet_mode = 0u;
static volatile uint16_t sync_phase_quiet_enter_count = 0u;

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

void tim15_sync_apply_nominal_arr(uint32_t nominal_arr)
{
  extern TIM_HandleTypeDef htim15;
  uint32_t primask;
  uint32_t previous_nominal_arr;

  if (nominal_arr < 1u) {
    nominal_arr = 1u;
  }

  /* A host/profile rate change owns the nominal frequency. Atomically cancel
     a temporary phase pulse so its completion IRQ cannot restore the stale
     boot/profile ARR after the new rate has already been applied. */
  primask = __get_PRIMASK();
  __disable_irq();
  tim15_arr_pulse_stage = 0u;
  tim15_arr_pulse_nominal = nominal_arr;
  tim15_arr_hold_offset = 0;
  tim15_arr_hold_target_offset = 0;
  previous_nominal_arr = g_tim15_slave_base_arr;
  g_tim15_slave_base_arr = nominal_arr;
  /* A rate/profile change invalidates the distance accumulated by an active
     anti-phase slew. Leaving the old distance alive can finish a correction
     sized for the boot ARR, classify the marker as anti-phase again, and run
     a second full slew. Reapplying an already active stream/profile is common
     host behaviour, though, and must not keep clearing the 200-packet
     qualifier before a slew has even started. */
  if ((previous_nominal_arr != nominal_arr) &&
      ((rs485_phase_slew_active != 0u) ||
       (rs485_anti_phase_recovery_request != 0u))) {
    rs485_phase_slew_active = 0u;
    rs485_phase_slew_remaining_ticks = 0u;
    rs485_anti_phase_recovery_active = 0u;
    rs485_anti_phase_recovery_packets = 0u;
    rs485_anti_phase_recovery_request = 0u;
    rs485_phase_guard_recovery_packets = 0u;
    rs485_sync_relation_score = 0;
    rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
    rs485_sync_locked = 0u;
    rs485_sync_led_active = 0u;
    sync_phase_fast_last_buf = 0xFFFFFFFFu;
    sync_phase_filter_reset_request = 1u;
  }
  __HAL_TIM_DISABLE_IT(&htim15, TIM_IT_UPDATE);
  __HAL_TIM_CLEAR_FLAG(&htim15, TIM_FLAG_UPDATE);
  arr_auto_apply_tim15(nominal_arr);
  if (primask == 0u) {
    __enable_irq();
  }
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
  if ((sync_phase_quiet_mode != 0u) &&
      (pulse_cap > TIM15_SYNC_QUIET_MAX_OFFSET)) {
    pulse_cap = TIM15_SYNC_QUIET_MAX_OFFSET;
  }

  pulse = abs_phase / TIM15_SYNC_PULSE_DIVISOR;

  /* v85 adaptive boost: быстрее входит в фазу, но ограничен UART-bit cap. */
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

  /* Increasing ARR delays the next local buffer boundary and decreases the
     measured sample phase. Thus a positive phase error needs +ARR and a
     negative error needs -ARR. */
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

  if (sync_phase_quiet_mode != 0u) {
    return (abs_phase > deadband_ticks) ? TIM15_SYNC_QUIET_SPACING_BUFFERS : 4u;
  }
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

static void rs485_freq_trim_reset(uint8_t clear_diag)
{
  rs485_freq_trim_dir = 0;
  rs485_freq_trim_have_prev_phase = 0u;
  rs485_freq_trim_prev_phase_ticks = 0;
  rs485_freq_trim_window_delta_accum = 0;
  rs485_freq_trim_window_count = 0u;
  rs485_freq_trim_interval_buffers = RS485_FREQ_TRIM_MAX_INTERVAL_BUFFERS;
  rs485_freq_trim_edge_kind = RS485_FREQ_TRIM_EDGE_KIND_AUTO;

  if (clear_diag != 0u) {
    rs485_freq_trim_last_window_delta_ticks = 0;
    rs485_freq_trim_last_avg_delta_ticks = 0;
    rs485_freq_trim_windows = 0u;
    rs485_freq_trim_nudge_count = 0u;
    rs485_freq_trim_skip_count = 0u;
  }
}

static void rs485_freq_trim_on_phase(int32_t phase_error,
                                     uint32_t period_ticks,
                                     uint32_t sample_ticks,
                                     uint32_t bit_ticks)
{
  extern volatile uint8_t vnd_sync_mode_public;
  int32_t phase_delta = 0;
  uint32_t abs_phase = 0u;
  uint32_t max_phase = 0u;
  uint32_t abs_delta = 0u;
  uint32_t outlier_limit = 0u;
  uint8_t edge_kind = (uint8_t)(rs485_last_sync_edge_kind & 1u);

  if ((rs485_freq_trim_enabled == 0u) ||
      (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE)) {
    rs485_freq_trim_reset(0u);
    return;
  }

  if ((period_ticks == 0u) || (sample_ticks == 0u)) {
    return;
  }

  abs_phase = (uint32_t)arr_auto_abs_i32(phase_error);
  max_phase = bit_ticks * RS485_FREQ_TRIM_MAX_PHASE_BITS;
  if (max_phase == 0u) {
    max_phase = sample_ticks * RS485_FREQ_TRIM_OUTLIER_SAMPLES;
  }
  if ((max_phase != 0u) && (abs_phase > max_phase)) {
    rs485_freq_trim_dir = 0;
    rs485_freq_trim_have_prev_phase = 0u;
    rs485_freq_trim_window_delta_accum = 0;
    rs485_freq_trim_window_count = 0u;
    return;
  }

  if (rs485_freq_trim_edge_kind == RS485_FREQ_TRIM_EDGE_KIND_AUTO) {
    rs485_freq_trim_edge_kind = edge_kind;
  }
  if (edge_kind != rs485_freq_trim_edge_kind) {
    return;
  }

  if (rs485_freq_trim_have_prev_phase == 0u) {
    rs485_freq_trim_prev_phase_ticks = phase_error;
    rs485_freq_trim_have_prev_phase = 1u;
    return;
  }

  phase_delta = rs485_sync_wrap_phase_ticks(
      phase_error - rs485_freq_trim_prev_phase_ticks,
      period_ticks);
  rs485_freq_trim_prev_phase_ticks = phase_error;

  outlier_limit = sample_ticks * RS485_FREQ_TRIM_OUTLIER_SAMPLES;
  if (bit_ticks > outlier_limit) {
    outlier_limit = bit_ticks;
  }
  if (outlier_limit == 0u) {
    outlier_limit = 1u;
  }

  abs_delta = (uint32_t)arr_auto_abs_i32(phase_delta);
  if (abs_delta > outlier_limit) {
    return;
  }

  rs485_freq_trim_window_delta_accum += phase_delta;
  rs485_freq_trim_window_count++;

  if (rs485_freq_trim_window_count >= RS485_FREQ_TRIM_WINDOW_PACKETS) {
    int32_t total_delta = rs485_freq_trim_window_delta_accum;
    uint32_t abs_total = (uint32_t)arr_auto_abs_i32(total_delta);
    uint32_t deadband = bit_ticks * 2u;

    if (deadband < RS485_FREQ_TRIM_DRIFT_DEADBAND_TICKS) {
      deadband = RS485_FREQ_TRIM_DRIFT_DEADBAND_TICKS;
    }

    rs485_freq_trim_last_window_delta_ticks = total_delta;
    rs485_freq_trim_last_avg_delta_ticks =
        total_delta / (int32_t)rs485_freq_trim_window_count;
    if (rs485_freq_trim_windows < 0xFFFFFFFFu) {
      rs485_freq_trim_windows++;
    }

    if (abs_total > deadband) {
      uint32_t pulses_per_window =
          (abs_total + (TIM15_SYNC_PULSE_HOLD_UPDATES / 2u)) /
          TIM15_SYNC_PULSE_HOLD_UPDATES;
      uint32_t interval = RS485_FREQ_TRIM_MAX_INTERVAL_BUFFERS;

      if (pulses_per_window == 0u) {
        pulses_per_window = 1u;
      }
      interval = (RS485_FREQ_TRIM_WINDOW_PACKETS *
                  RS485_FREQ_TRIM_SAMPLE_STRIDE_BUFFERS) / pulses_per_window;
      if (interval < RS485_FREQ_TRIM_MIN_INTERVAL_BUFFERS) {
        interval = RS485_FREQ_TRIM_MIN_INTERVAL_BUFFERS;
      } else if (interval > RS485_FREQ_TRIM_MAX_INTERVAL_BUFFERS) {
        interval = RS485_FREQ_TRIM_MAX_INTERVAL_BUFFERS;
      }

      /* Старое измерение adc_stream.c: если фаза растёт, TIM15 отстаёт и
         его надо ускорить через ARR-. Если фаза падает, TIM15 спешит и
         его надо замедлить через ARR+. */
      rs485_freq_trim_dir = (total_delta > 0) ? -1 : 1;
      rs485_freq_trim_interval_buffers = interval;
    } else {
      rs485_freq_trim_dir = 0;
      rs485_freq_trim_interval_buffers = RS485_FREQ_TRIM_MAX_INTERVAL_BUFFERS;
    }

    rs485_freq_trim_window_delta_accum = 0;
    rs485_freq_trim_window_count = 0u;
  }
}

static void rs485_freq_trim_service(uint32_t now_ms)
{
  extern volatile uint8_t vnd_sync_mode_public;
  extern volatile uint32_t adc_stream_total_buffer_count;
  uint32_t current_buf = adc_stream_total_buffer_count;
  static uint32_t last_apply_buf = 0u;
  int8_t dir = rs485_freq_trim_dir;
  uint32_t interval = rs485_freq_trim_interval_buffers;

  if ((rs485_freq_trim_enabled == 0u) ||
      (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) ||
      (rs485_sync_phase_relation != RS485_SYNC_RELATION_IN_PHASE) ||
      (rs485_phase_slew_active != 0u) ||
      (sync_last_edge_ms == 0u) ||
      ((now_ms - sync_last_edge_ms) > RS485_SYNC_PRESENT_MS)) {
    rs485_freq_trim_reset(0u);
    return;
  }

  if (dir == 0) {
    return;
  }

  if (interval < RS485_FREQ_TRIM_MIN_INTERVAL_BUFFERS) {
    interval = RS485_FREQ_TRIM_MIN_INTERVAL_BUFFERS;
  }

  if ((current_buf - last_apply_buf) < interval) {
    return;
  }

  if (tim15_arr_pulse_stage != 0u) {
    if (rs485_freq_trim_skip_count < 0xFFFFFFFFu) {
      rs485_freq_trim_skip_count++;
    }
    return;
  }

  if (tim15_schedule_arr_pulse((int32_t)dir) != 0u) {
    last_apply_buf = current_buf;
    if (rs485_freq_trim_nudge_count < 0xFFFFFFFFu) {
      rs485_freq_trim_nudge_count++;
    }
  } else if (rs485_freq_trim_skip_count < 0xFFFFFFFFu) {
    rs485_freq_trim_skip_count++;
  }
}

static uint32_t rs485_phase_approach_bits_to_ticks(uint32_t bit_ticks, uint32_t bits)
{
  if (bit_ticks == 0u) {
    bit_ticks = 1u;
  }
  return bit_ticks * bits;
}

static uint32_t rs485_phase_approach_spacing(uint32_t abs_phase, uint32_t bit_ticks)
{
  if (abs_phase >= rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_SPACING_FAR_BITS)) {
    return 1u;
  }
  if (abs_phase >= rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_SPACING_MID_BITS)) {
    return 2u;
  }
  if (abs_phase >= rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_SPACING_NEAR_BITS)) {
    return 4u;
  }
  return 8u;
}

static int32_t rs485_phase_approach_compute_delta(int32_t phase_error,
                                                  uint32_t abs_phase,
                                                  uint32_t bit_ticks)
{
  uint32_t start_ticks = 0u;
  uint32_t stop_ticks = 0u;
  uint32_t divisor_ticks = 0u;
  uint32_t magnitude = 0u;
  uint32_t max_magnitude = (uint32_t)RS485_PHASE_APPROACH_MAX_DELTA;

  if (rs485_phase_approach_enabled == 0u) {
    rs485_phase_approach_active = 0u;
    rs485_phase_approach_last_delta = 0;
    rs485_phase_approach_last_abs_ticks = abs_phase;
    rs485_phase_approach_prev_sign = 0;
    rs485_phase_approach_holdoff_edges = 0u;
    rs485_phase_approach_dir = 1;
    rs485_phase_approach_have_prev_abs = 0u;
    rs485_phase_approach_prev_abs_ticks = 0u;
    rs485_phase_approach_worse_count = 0u;
    return 0;
  }

  start_ticks = rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_START_BITS);
  stop_ticks = rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_STOP_BITS);
  if (stop_ticks == 0u) {
    stop_ticks = 1u;
  }
  if (start_ticks <= stop_ticks) {
    start_ticks = stop_ticks + 1u;
  }

  rs485_phase_approach_last_abs_ticks = abs_phase;

  if (rs485_phase_approach_holdoff_edges != 0u) {
    rs485_phase_approach_holdoff_edges--;
    rs485_phase_approach_active = 0u;
    rs485_phase_approach_last_delta = 0;
    return 0;
  }

  if (abs_phase <= stop_ticks) {
    if (rs485_phase_approach_active != 0u) {
      if (rs485_phase_approach_done_count < 0xFFFFFFFFu) {
        rs485_phase_approach_done_count++;
      }
    }
    rs485_phase_approach_active = 0u;
    rs485_phase_approach_last_delta = 0;
    rs485_phase_approach_prev_sign = 0;
    rs485_phase_approach_holdoff_edges = 0u;
    rs485_phase_approach_have_prev_abs = 0u;
    rs485_phase_approach_worse_count = 0u;
    return 0;
  }

  if (abs_phase < start_ticks) {
    rs485_phase_approach_active = 0u;
    rs485_phase_approach_last_delta = 0;
    rs485_phase_approach_prev_sign = 0;
    rs485_phase_approach_have_prev_abs = 0u;
    rs485_phase_approach_worse_count = 0u;
    return 0;
  }

  {
    int8_t current_sign = (phase_error > 0) ? 1 : -1;
    if ((rs485_phase_approach_prev_sign != 0) &&
        (current_sign != rs485_phase_approach_prev_sign) &&
        (abs_phase <= rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_SIGN_HOLDOFF_BITS))) {
      rs485_phase_approach_holdoff_edges = RS485_PHASE_APPROACH_SIGN_HOLDOFF_EDGES;
      rs485_phase_approach_active = 0u;
      rs485_phase_approach_last_delta = 0;
      if (rs485_phase_approach_done_count < 0xFFFFFFFFu) {
        rs485_phase_approach_done_count++;
      }
      rs485_phase_approach_prev_sign = current_sign;
      return 0;
    }
    rs485_phase_approach_prev_sign = current_sign;
  }

#if RS485_PHASE_APPROACH_ADAPT_DIRECTION
  {
    uint32_t worse_margin = rs485_phase_approach_bits_to_ticks(
        bit_ticks,
        RS485_PHASE_APPROACH_WORSE_BITS);

    if (worse_margin == 0u) {
      worse_margin = 1u;
    }
    if (rs485_phase_approach_dir == 0) {
      rs485_phase_approach_dir = 1;
    }

    if (rs485_phase_approach_have_prev_abs != 0u) {
      if (abs_phase > (rs485_phase_approach_prev_abs_ticks + worse_margin)) {
        if (rs485_phase_approach_worse_count < 255u) {
          rs485_phase_approach_worse_count++;
        }
      } else if ((abs_phase + worse_margin) < rs485_phase_approach_prev_abs_ticks) {
        rs485_phase_approach_worse_count = 0u;
      }

      if (rs485_phase_approach_worse_count >= RS485_PHASE_APPROACH_WORSE_LIMIT) {
        rs485_phase_approach_dir = (rs485_phase_approach_dir > 0) ? -1 : 1;
        rs485_phase_approach_worse_count = 0u;
        rs485_phase_approach_prev_abs_ticks = abs_phase;
        rs485_phase_approach_active = 0u;
        rs485_phase_approach_last_delta = 0;
        rs485_phase_approach_holdoff_edges = RS485_PHASE_APPROACH_SIGN_HOLDOFF_EDGES;
        return 0;
      }
    } else {
      rs485_phase_approach_have_prev_abs = 1u;
    }

    rs485_phase_approach_prev_abs_ticks = abs_phase;
  }
#endif

  divisor_ticks = rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_DELTA_DIV_BITS);
  if (divisor_ticks == 0u) {
    divisor_ticks = 1u;
  }
  magnitude = abs_phase / divisor_ticks;
  if (magnitude == 0u) {
    magnitude = 1u;
  }
  if (abs_phase < rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_FINE_CAP_BITS)) {
    max_magnitude = 1u;
  } else if (abs_phase < rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_MID_CAP_BITS)) {
    max_magnitude = 2u;
  }
#if RS485_PHASE_APPROACH_FORCE_POSITIVE_DELTA
  if ((phase_error < 0) &&
      (abs_phase >= rs485_phase_approach_bits_to_ticks(bit_ticks, RS485_PHASE_APPROACH_NEG_BOOST_MIN_BITS)) &&
      (max_magnitude < RS485_PHASE_APPROACH_NEG_BOOST_DELTA)) {
    max_magnitude = RS485_PHASE_APPROACH_NEG_BOOST_DELTA;
  }
#endif
  if (magnitude > max_magnitude) {
    magnitude = max_magnitude;
  }

  rs485_phase_approach_active = 1u;
  /* Направление ARR возле цели подбирается по факту уменьшения |phase_err|:
     это защищает от прохода через ноль и от неверной ручной трактовки знака. */
#if RS485_PHASE_APPROACH_ADAPT_DIRECTION
  rs485_phase_approach_last_delta =
      (rs485_phase_approach_dir >= 0) ? (int32_t)magnitude : -(int32_t)magnitude;
#elif RS485_PHASE_APPROACH_FORCE_POSITIVE_DELTA
  rs485_phase_approach_last_delta = (int32_t)magnitude;
#else
  rs485_phase_approach_last_delta = (phase_error > 0) ? (int32_t)magnitude : -(int32_t)magnitude;
#endif
  return rs485_phase_approach_last_delta;
}

static void sync_phase_handle_irq_fast(uint16_t sample_idx, uint16_t active_samples)
{
  extern volatile uint8_t vnd_sync_mode_public;
  extern volatile uint32_t adc_stream_total_buffer_count;
  extern volatile uint32_t sync_tim15_cnt_at_pd5;
  static int32_t filtered_phase_error = 0;
  static uint8_t filtered_phase_valid = 0u;
  static uint32_t filtered_period_ticks = 0u;
  uint32_t period_ticks = sync_tim5_period_ticks;
  uint32_t control_period_ticks = sync_tim5_period_ticks;
  uint32_t sample_ticks = 0u;
  uint32_t fine_phase_ticks = 0u;
  int32_t target_phase = 0;
  int32_t measured_phase = 0;
  int32_t phase_error = 0;
  uint32_t raw_abs_phase = 0u;
  uint32_t control_abs_phase = 0u;
  int32_t control_phase_error = 0;
  int32_t pulse_delta = 0;
  uint32_t pulse_spacing = 0u;
  uint32_t current_buf = adc_stream_total_buffer_count;
  uint32_t bit_ticks = 0u;
  uint8_t near_raw_mode = 0u;
  uint8_t phase_locked_now = 0u;
  uint8_t approach_pulse = 0u;

  if (vnd_sync_mode_public != VND_SYNC_MODE_SLAVE) {
    filtered_phase_valid = 0u;
    sync_phase_quiet_mode = 0u;
    sync_phase_quiet_enter_count = 0u;
    return;
  }

#if !RS485_SYNC_PHASE_TRACK_ENABLE
  filtered_phase_valid = 0u;
  rs485_sync_locked = 0u;
  rs485_sync_led_active = 0u;
  sync_phase_quiet_mode = 0u;
  sync_phase_quiet_enter_count = 0u;
  sync_phase_last_pulse_delta = 0;
  rs485_phase_half_snap_request = 0u;
  rs485_phase_approach_active = 0u;
  rs485_phase_approach_last_delta = 0;
  rs485_freq_trim_reset(0u);
  return;
#endif

  if ((active_samples == 0u) || (period_ticks == 0u)) {
    filtered_phase_valid = 0u;
    rs485_sync_locked = 0u;
    rs485_sync_led_active = 0u;
    sync_phase_quiet_mode = 0u;
    sync_phase_quiet_enter_count = 0u;
    return;
  }

  if (sync_phase_filter_reset_request != 0u) {
    sync_phase_filter_reset_request = 0u;
    filtered_phase_valid = 0u;
    sync_phase_quiet_mode = 0u;
    sync_phase_quiet_enter_count = 0u;
  }

  sample_ticks = period_ticks / active_samples;
  if (sample_ticks == 0u) {
    sample_ticks = 1144u;
  }
  bit_ticks = tim15_sync_get_uart_bit_ticks_or_sample(sample_ticks);

#if RS485_SYNC_CONTROL_200HZ_ONLY
  if ((rs485_last_sync_edge_kind & 1u) != (RS485_SYNC_CONTROL_EDGE_KIND & 1u)) {
    return;
  }
#endif

  target_phase = (g_sync_target_phase_ticks == SYNC_TARGET_PHASE_AUTO)
                 ? tim15_get_default_target_phase_ticks()
                 : rs485_sync_wrap_phase_ticks((int32_t)g_sync_target_phase_ticks, period_ticks);
#if RS485_SYNC_TARGET_HALF_PERIOD_SHIFT
  target_phase = rs485_sync_wrap_phase_ticks(
      target_phase + (int32_t)(period_ticks / 2u),
      period_ticks);
#endif
  fine_phase_ticks = sync_tim15_cnt_at_pd5;
  if (fine_phase_ticks >= sample_ticks) {
    fine_phase_ticks %= sample_ticks;
  }
  {
    uint32_t phase_in_half_ticks =
        ((uint32_t)sample_idx * sample_ticks) + fine_phase_ticks;
    int32_t target_base_ticks = rs485_sync_wrap_phase_ticks(target_phase, period_ticks);

    if (phase_in_half_ticks >= period_ticks) {
      phase_in_half_ticks %= period_ticks;
    }
    if (target_base_ticks < 0) {
      target_base_ticks += (int32_t)period_ticks;
    }

#if RS485_SYNC_VISUAL_PHASE_TRACK
    {
      uint8_t local_phase_kind =
          (uint8_t)(rs485_sync_edge_kind_from_marker_level(rs485_sync_read_local_marker_phase()) & 1u);
      uint8_t target_phase_kind =
          (uint8_t)((rs485_last_sync_edge_kind ^ RS485_SYNC_IN_PHASE_LOCAL_EDGE_XOR) & 1u);

      control_period_ticks = period_ticks * RS485_STATUS_SLOT_STRIDE;
      measured_phase = (int32_t)(((uint32_t)local_phase_kind * period_ticks) +
                                 phase_in_half_ticks);
      target_phase = (int32_t)(((uint32_t)target_phase_kind * period_ticks) +
                               (uint32_t)target_base_ticks);
      measured_phase = rs485_sync_wrap_phase_ticks(measured_phase, control_period_ticks);
      target_phase = rs485_sync_wrap_phase_ticks(target_phase, control_period_ticks);
    }
#else
    measured_phase = rs485_sync_wrap_phase_ticks((int32_t)phase_in_half_ticks, period_ticks);
    target_phase = rs485_sync_wrap_phase_ticks(target_base_ticks, period_ticks);
#endif
  }
  phase_error = rs485_sync_wrap_phase_ticks(measured_phase - target_phase, control_period_ticks);
  if ((filtered_phase_valid == 0u) || (filtered_period_ticks != control_period_ticks)) {
    filtered_phase_error = phase_error;
    filtered_phase_valid = 1u;
    filtered_period_ticks = control_period_ticks;
  } else {
    int32_t innovation = rs485_sync_wrap_phase_ticks(phase_error - filtered_phase_error, control_period_ticks);
    int32_t step_limit = (int32_t)bit_ticks;
    int32_t filter_step = 0;

    if (step_limit < 1) {
      step_limit = 1;
    }
    innovation = tim15_sync_clamp_i32(innovation, step_limit);
    {
      uint32_t filter_divisor = (sync_phase_quiet_mode != 0u)
                                ? TIM15_SYNC_QUIET_FILTER_DIVISOR
                                : TIM15_SYNC_FILTER_DIVISOR;
      if (filter_divisor == 0u) {
        filter_divisor = 1u;
      }
      filter_step = innovation / (int32_t)filter_divisor;
    }
    if ((filter_step == 0) && (innovation != 0)) {
      filter_step = (innovation > 0) ? 1 : -1;
    }
    filtered_phase_error = rs485_sync_wrap_phase_ticks(filtered_phase_error + filter_step, control_period_ticks);
  }

  raw_abs_phase = (uint32_t)arr_auto_abs_i32(phase_error);
#if RS485_SYNC_VISUAL_PHASE_TRACK
  if ((rs485_phase_half_snap_enabled != 0u) &&
      (RS485_PHASE_HALF_SNAP_THRESHOLD_DEN != 0u) &&
      (period_ticks != 0u)) {
    uint32_t half_snap_threshold =
        (uint32_t)(((uint64_t)period_ticks *
                    (uint64_t)RS485_PHASE_HALF_SNAP_THRESHOLD_NUM) /
                   (uint64_t)RS485_PHASE_HALF_SNAP_THRESHOLD_DEN);
#if RS485_PHASE_HALF_SNAP_REARM_ENABLE
    uint32_t half_snap_rearm =
        (uint32_t)(((uint64_t)period_ticks *
                    (uint64_t)RS485_PHASE_HALF_SNAP_REARM_NUM) /
                   (uint64_t)RS485_PHASE_HALF_SNAP_REARM_DEN);

    if ((half_snap_rearm != 0u) && (raw_abs_phase <= half_snap_rearm)) {
      rs485_phase_half_snap_armed = 1u;
    }
#endif
    if ((rs485_phase_half_snap_armed != 0u) &&
        (half_snap_threshold != 0u) &&
        (raw_abs_phase >= half_snap_threshold)) {
      rs485_phase_half_snap_armed = 0u;
      rs485_phase_half_snap_last_error = phase_error;
      rs485_phase_half_snap_request = 1u;
    }
  }
#endif
  {
    uint32_t deadband_ticks = tim15_sync_get_deadband_ticks();
    uint32_t quiet_enter_ticks = deadband_ticks + (bit_ticks * TIM15_SYNC_QUIET_ENTER_BITS);
    uint32_t quiet_exit_ticks = deadband_ticks + (bit_ticks * TIM15_SYNC_QUIET_EXIT_BITS);
    uint32_t near_raw_ticks = deadband_ticks + (bit_ticks * TIM15_SYNC_NEAR_RAW_BITS);

    near_raw_mode = (uint8_t)(raw_abs_phase <= near_raw_ticks);

    if (sync_phase_quiet_mode != 0u) {
      if (raw_abs_phase > quiet_exit_ticks) {
        sync_phase_quiet_mode = 0u;
        sync_phase_quiet_enter_count = 0u;
      }
    } else if (raw_abs_phase <= quiet_enter_ticks) {
      if (sync_phase_quiet_enter_count < TIM15_SYNC_QUIET_ENTER_PACKETS) {
        sync_phase_quiet_enter_count++;
      }
      if (sync_phase_quiet_enter_count >= TIM15_SYNC_QUIET_ENTER_PACKETS) {
        sync_phase_quiet_mode = 1u;
      }
    } else {
      sync_phase_quiet_enter_count = 0u;
    }
  }
  control_phase_error = ((sync_phase_quiet_mode != 0u) || (near_raw_mode != 0u))
                        ? phase_error
                        : filtered_phase_error;
  control_abs_phase = (uint32_t)arr_auto_abs_i32(control_phase_error);
  phase_locked_now = (uint8_t)(control_abs_phase <= tim15_sync_get_deadband_ticks());
#if TIM15_SYNC_PHASE_PULSE_ENABLE
  pulse_delta = tim15_compute_phase_pulse_delta(control_phase_error);
  pulse_spacing = tim15_phase_pulse_spacing_buffers(control_abs_phase);
  /* Keep the newer boundary-safe phase loop active while the relation is
     being classified. Only the explicit full-half-cycle slew owns TIM15
     exclusively; running normal PLL pulses during that operation would
     subtract from its accumulated delay. */
  if (rs485_phase_slew_active != 0u) {
    pulse_delta = 0;
  }
  if (near_raw_mode != 0u) {
    int32_t near_limit = phase_locked_now ? TIM15_SYNC_NEAR_RAW_MAX_OFFSET
                                          : TIM15_SYNC_NEAR_RAW_ACQUIRE_MAX_OFFSET;
    uint32_t near_spacing = phase_locked_now ? TIM15_SYNC_NEAR_RAW_SPACING
                                             : TIM15_SYNC_NEAR_RAW_ACQUIRE_SPACING;
    if (pulse_delta > near_limit) {
      pulse_delta = near_limit;
    } else if (pulse_delta < -near_limit) {
      pulse_delta = -near_limit;
    }
    if ((pulse_delta != 0) && (pulse_spacing < near_spacing)) {
      pulse_spacing = near_spacing;
    }
  }
#else
  pulse_delta = 0;
  pulse_spacing = TIM15_SYNC_SLOW_SPACING_BUFFERS;
#endif
  if ((rs485_sync_phase_relation == RS485_SYNC_RELATION_IN_PHASE) &&
      (rs485_phase_slew_active == 0u)) {
    int32_t approach_delta = rs485_phase_approach_compute_delta(phase_error,
                                                                raw_abs_phase,
                                                                bit_ticks);
    if (approach_delta != 0) {
      pulse_delta = approach_delta;
      pulse_spacing = rs485_phase_approach_spacing(raw_abs_phase, bit_ticks);
#if RS485_PHASE_APPROACH_FORCE_POSITIVE_DELTA
      if ((phase_error < 0) && (pulse_spacing > RS485_PHASE_APPROACH_NEG_SPACING_MAX)) {
        pulse_spacing = RS485_PHASE_APPROACH_NEG_SPACING_MAX;
      }
#endif
      rs485_phase_approach_last_spacing = pulse_spacing;
      approach_pulse = 1u;
    }
  } else {
    /* The coarse anti-phase slew owns TIM15 until the logical half-cycle is
       correct. Running the normal approach loop here schedules opposite ARR
       pulses and can cancel the coarse slew indefinitely. */
    rs485_phase_approach_active = 0u;
    rs485_phase_approach_last_delta = 0;
    rs485_phase_approach_prev_sign = 0;
    rs485_phase_approach_holdoff_edges = 0u;
    rs485_phase_approach_have_prev_abs = 0u;
    rs485_phase_approach_worse_count = 0u;
  }

  sync_phase_fast_edges++;
  sync_phase_last_error_ticks = phase_error;
  sync_phase_last_control_error_ticks = control_phase_error;
  sync_phase_last_pulse_delta = pulse_delta;
  sync_phase_near_raw_mode = near_raw_mode;
  sync_phase_fast_last_spacing = pulse_spacing;
  rs485_sync_locked = phase_locked_now;
  rs485_sync_led_active = rs485_sync_locked;
  if ((rs485_sync_phase_relation != RS485_SYNC_RELATION_IN_PHASE) ||
      (rs485_phase_slew_active != 0u) ||
      (rs485_phase_approach_active != 0u) ||
      (rs485_phase_approach_holdoff_edges != 0u)) {
    rs485_freq_trim_reset(0u);
  } else {
    rs485_freq_trim_on_phase(phase_error, control_period_ticks, sample_ticks, bit_ticks);
  }

  if (pulse_delta == 0) {
    return;
  }

  if ((sync_phase_fast_last_buf != 0xFFFFFFFFu) &&
      ((current_buf - sync_phase_fast_last_buf) < pulse_spacing)) {
    sync_phase_fast_skip_spacing++;
    if ((approach_pulse != 0u) && (rs485_phase_approach_skip_count < 0xFFFFFFFFu)) {
      rs485_phase_approach_skip_count++;
    }
    return;
  }

  if (tim15_arr_pulse_stage != 0u) {
    sync_phase_fast_skip_busy++;
    if ((approach_pulse != 0u) && (rs485_phase_approach_skip_count < 0xFFFFFFFFu)) {
      rs485_phase_approach_skip_count++;
    }
    return;
  }

  if (tim15_schedule_arr_pulse(pulse_delta) != 0u) {
    sync_phase_fast_last_buf = current_buf;
    sync_phase_fast_pulses++;
    if ((approach_pulse != 0u) && (rs485_phase_approach_nudge_count < 0xFFFFFFFFu)) {
      rs485_phase_approach_nudge_count++;
    }
  } else {
    sync_phase_fast_skip_busy++;
    if ((approach_pulse != 0u) && (rs485_phase_approach_skip_count < 0xFFFFFFFFu)) {
      rs485_phase_approach_skip_count++;
    }
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
  extern volatile uint32_t adc_stream_total_buffer_count;
  uint32_t now_ms = HAL_GetTick();
  static uint32_t last_phase_slew_buffer = 0xFFFFFFFFu;
  static uint32_t last_sync_restart_ms = 0u;

  tim15_request_hold_offset(0);

  if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
      (sync_last_edge_ms != 0u) &&
      ((now_ms - sync_last_edge_ms) <= RS485_SYNC_PRESENT_MS)) {
    if (rs485_phase_half_snap_request != 0u) {
      int32_t snap_error = rs485_phase_half_snap_last_error;
      (void)snap_error;
      rs485_phase_half_snap_request = 0u;

      if ((rs485_phase_half_snap_last_ms == 0u) ||
          ((now_ms - rs485_phase_half_snap_last_ms) >= RS485_PHASE_HALF_SNAP_COOLDOWN_MS)) {
        /* Keep ADC/DMA continuous. Phase/relation is relocked by the RS-485
           control loop without resetting the acquisition cycle. */
        rs485_phase_half_snap_last_ms = now_ms;
        if (rs485_phase_half_snap_count < 0xFFFFFFFFu) {
          rs485_phase_half_snap_count++;
        }
        rs485_sync_relation_score = 0;
        rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
        rs485_sync_locked = 0u;
        rs485_sync_led_active = 0u;
        rs485_anti_phase_recovery_active = 0u;
        rs485_anti_phase_recovery_packets = 0u;
        rs485_anti_phase_recovery_request = 0u;
        rs485_sync_restart_request = 0u;
        rs485_phase_guard_recovery_packets = 0u;
        rs485_phase_approach_active = 0u;
        rs485_phase_approach_last_delta = 0;
        rs485_phase_approach_prev_sign = 0;
        rs485_phase_approach_holdoff_edges = 0u;
        rs485_phase_approach_have_prev_abs = 0u;
        rs485_phase_approach_worse_count = 0u;
        sync_phase_fast_last_buf = 0xFFFFFFFFu;
        sync_phase_filter_reset_request = 1u;
        rs485_freq_trim_reset(0u);
        printf("[SYNC] coarse phase relock without ADC restart err=%ld\r\n",
               (long)snap_error);
      }
    }

    if ((RS485_SYNC_ANTI_PHASE_RECOVERY_ENABLE != 0u) &&
        (rs485_phase_slew_active == 0u) &&
        (rs485_anti_phase_recovery_request != 0u)) {
      uint32_t half_period_ticks = sync_tim5_period_ticks;

      if (half_period_ticks == 0u) {
        uint32_t samples = adc_stream_get_active_samples();
        half_period_ticks = samples * (TIM15->ARR + 1u);
      }
      if (half_period_ticks != 0u) {
        /* Correct polarity without an asynchronous GPIO toggle. A complete
           extra half-period of accumulated timer delay changes marker parity
           while returning the buffer boundary to the same phase position. */
        rs485_phase_slew_remaining_ticks = half_period_ticks;
        rs485_phase_slew_active = 1u;
        last_phase_slew_buffer = 0xFFFFFFFFu;
        rs485_anti_phase_recovery_request = 0u;
        sync_phase_fast_last_buf = 0xFFFFFFFFu;
        sync_phase_filter_reset_request = 1u;
        rs485_freq_trim_reset(0u);
      }
    }

    if ((RS485_SYNC_ANTI_PHASE_RECOVERY_ENABLE != 0u) &&
        (rs485_phase_slew_active != 0u) &&
        (last_phase_slew_buffer != adc_stream_total_buffer_count) &&
        (tim15_arr_pulse_stage == 0u)) {
      uint32_t step_ticks = (uint32_t)((g_tim15_slave_arr_step_ticks < 0)
                                      ? -g_tim15_slave_arr_step_ticks
                                      : g_tim15_slave_arr_step_ticks);
      uint32_t pulse_ticks = 0u;

      if (step_ticks == 0u) {
        step_ticks = 1u;
      }
      pulse_ticks = (uint32_t)TIM15_SYNC_PULSE_MAX_OFFSET *
                    (uint32_t)TIM15_SYNC_PULSE_HOLD_UPDATES *
                    step_ticks;

      /* Never invert the physical TX marker while it is running: the immediate
         GPIO toggle creates one short/long half-cycle and is visible as a
         magnetic-field interruption. Accumulate exactly one complete
         half-period using bounded +ARR pulses. The temporary relation change
         at the buffer boundary must not stop this operation early. */
      if (tim15_schedule_arr_pulse(TIM15_SYNC_PULSE_MAX_OFFSET) != 0u) {
        last_phase_slew_buffer = adc_stream_total_buffer_count;
        if (rs485_phase_slew_pulse_count < 0xFFFFFFFFu) {
          rs485_phase_slew_pulse_count++;
        }
        if (rs485_phase_slew_remaining_ticks <= pulse_ticks) {
          rs485_phase_slew_remaining_ticks = 0u;
          rs485_phase_slew_active = 0u;
          rs485_sync_relation_score = 0;
          rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
          rs485_sync_locked = 0u;
          rs485_sync_led_active = 0u;
          rs485_anti_phase_recovery_active = 0u;
          rs485_anti_phase_recovery_packets = 0u;
          rs485_anti_phase_recovery_request = 0u;
          rs485_phase_guard_recovery_packets = 0u;
          sync_phase_fast_last_buf = 0xFFFFFFFFu;
          sync_phase_filter_reset_request = 1u;
          rs485_freq_trim_reset(0u);
        } else {
          rs485_phase_slew_remaining_ticks -= pulse_ticks;
        }
      }
    }

    extern uint8_t vnd_is_streaming(void);
    if (rs485_sync_restart_request && vnd_is_streaming()) {
      rs485_sync_restart_request = 0u;
      rs485_phase_guard_recovery_packets = 0u;
      sync_phase_fast_last_buf = 0xFFFFFFFFu;
      sync_phase_filter_reset_request = 1u;
    }
    if (rs485_sync_restart_request &&
        ((now_ms - last_sync_restart_ms) >= RS485_SYNC_RESTART_MIN_MS)) {
        last_sync_restart_ms = now_ms;
        rs485_sync_relation_score = 0;
        rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
        rs485_sync_locked = 0u;
        rs485_sync_led_active = 0u;
        rs485_anti_phase_recovery_active = 0u;
        rs485_anti_phase_recovery_packets = 0u;
        rs485_anti_phase_recovery_request = 0u;
        rs485_sync_restart_request = 0u;
        rs485_phase_guard_recovery_packets = 0u;
        sync_phase_fast_last_buf = 0xFFFFFFFFu;
        sync_phase_filter_reset_request = 1u;
        printf("[SYNC] bad phase/relation -> relock without ADC restart\r\n");
    }
  } else {
    rs485_phase_slew_active = 0u;
    rs485_phase_slew_remaining_ticks = 0u;
    rs485_phase_half_snap_armed = 1u;
    rs485_phase_half_snap_request = 0u;
    rs485_sync_locked = 0u;
    rs485_sync_led_active = 0u;
    rs485_anti_phase_recovery_active = 0u;
    rs485_anti_phase_recovery_packets = 0u;
    rs485_anti_phase_recovery_request = 0u;
    rs485_sync_restart_request = 0u;
    rs485_phase_guard_recovery_packets = 0u;
    sync_phase_fast_last_buf = 0xFFFFFFFFu;
    sync_phase_filter_reset_request = 1u;
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
#if !MAIN_LED_INDICATION_ENABLE
  (void)now_ms;
  uart1_led_off_tick = 0u;
  LED_OFF();
  return;
#else
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
#endif
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
#if !MAIN_WS2812_STATUS_ENABLE
  static uint8_t off_applied = 0u;
  (void)now_ms;
  g_ws2812_test_pattern = WS2812_PATTERN_OFF;
  if(off_applied == 0u) {
    ws2812_spi_set_pattern(WS2812_PATTERN_OFF);
    off_applied = 1u;
  }
  return;
#else
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
  uint8_t status_first = 0u;
  uint8_t status_second = 0u;
  uint8_t status_no_response = 0u;
  uint8_t sensor_index = 0u;
  uint8_t sensor_active = 0u;

  rs485_sync_buf_div4 = adc_stream_total_buffer_count;

  /* Только MASTER шлёт sync. Вызов — прямо из DMA ISR, сразу после
     adc_marker_pa3_toggle(). parity = текущее состояние PA2/PC7. */
  if (vnd_sync_mode_public != VND_SYNC_MODE_MASTER) {
    rs485_discovery_reset_master_scan();
    return;
  }
  /* Role and device ID are independent persistent settings. An unassigned
     MASTER keeps ADC/USB running but is silent on the shared bus, because ID0
     is a real address and must never be used as an implicit default. */
  if (rs485_local_node_id_assigned == 0u) {
    rs485_master_status_tx_pending = 0u;
    return;
  }

  if ((rs485_sync_tx_suppressed_until_ms != 0u) &&
      ((int32_t)(HAL_GetTick() - rs485_sync_tx_suppressed_until_ms) < 0)) {
    return;
  }

  if (rs485_status_window_active != 0u) {
    rs485_status_finalize_window();
  }
  rs485_sensor_event_cycle++;
  sync_tim5_period_ticks = htim5.Instance->CNT;
  htim5.Instance->CNT = 0u;
  rs485_status_current_window_phase = (uint8_t)(parity % RS485_STATUS_SLOT_STRIDE);
  sync_byte = (uint8_t)(RS485_SYNC7_BASE | (parity ? RS485_SYNC_EDGE_BIT : 0u));
  if ((rs485_local_node_id_assigned != 0u) &&
      (rs485_sensor_event_prepare(&sensor_index, &sensor_active) != 0u)) {
    status_first = rs485_status_build_public_local_byte();
    status_second = (uint8_t)(RS485_STATUS_SENSOR_EVENT_VALUE |
                              ((sensor_index & 0x0Fu) << 1) |
                              (sensor_active & 0x01u));
    status_no_response = 1u;
    rs485_sensor_event_commit_tx();
  } else if ((rs485_identity_scan_active != 0u) &&
      (rs485_identity_scan_page < RS485_IDENT_PAGE_COUNT) &&
      (rs485_identity_self_tx_state != RS485_IDENT_SELF_STATE_NONE)) {
    status_first = rs485_status_build_master_identity_first_byte();
    status_no_response = 1u;
    rs485_identity_current_req_active = 0u;
    rs485_identity_current_req_page = 0u;
    rs485_identity_current_req_selector = 0u;
    if (rs485_identity_self_tx_state == RS485_IDENT_SELF_STATE_PAGE) {
      status_second = (uint8_t)(RS485_STATUS_MASTER_SELF_PAGE_VALUE |
                                (rs485_identity_scan_page & RS485_STATUS_ID_MASK));
      rs485_identity_self_tx_state = RS485_IDENT_SELF_STATE_DATA;
    } else {
      status_second = (uint8_t)(RS485_STATUS_MASTER_SELF_DATA_VALUE |
                                rs485_identity_get_local_nibble(rs485_identity_scan_page));
      rs485_identity_self_tx_state = RS485_IDENT_SELF_STATE_NONE;
    }
  } else {
    status_first = rs485_status_build_local_byte();
    status_second = rs485_status_build_local_second_byte(rs485_status_master_selector);
  }
  rs485_status_master_first = status_first;
  rs485_status_master_second = status_second;
  if (status_no_response == 0u) {
    rs485_status_master_selector =
        rs485_status_id_from_wire(
            (uint8_t)(status_first & RS485_STATUS_ID_MASK));
  }
  rs485_master_status_tx_first = status_first;
  rs485_master_status_tx_second = status_second;
  rs485_master_status_tx_no_response = status_no_response;
  rs485_master_status_tx_due_ms = HAL_GetTick() + RS485_STATUS_MASTER_WORD_DELAY_MS;
  rs485_master_status_tx_pending = 1u;
  /* sync должен уходить максимально близко к событию DMA завершения буфера.
     status[2] уходит следом с короткой задержкой: RXNE ISR slave успевает
     обработать sync-событие и не теряет master_status в ORE/legacy path. */
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
    // Дождаться применения (PVU/RVU сброшены), но не блокировать весь boot,
    // если IWDG после watchdog-reset оставил update-флаг активным.
    for (volatile uint32_t iwdg_update_timeout = 100000u;
         (IWDG1->SR != 0u) && (iwdg_update_timeout != 0u);
         iwdg_update_timeout--) {
      __NOP();
    }
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
  #if DIAG_DISABLE_IWDG
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
  printf("[SAFE] BLINK_ONLY: GPIO + UART only. No timers/USB/ADC/SPI/других TIM.\r\n");
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
  temp_sensor_init();
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
  /* Load DC plus the network-wide RS485 MASTER UID/timestamp before USB is
     exposed to the host. Otherwise a very early host assignment could be
     overwritten by the old Flash record immediately after enumeration. */
  vnd_persistent_config_load_once();
  printf("[INIT] Before USB_DEVICE_Init\r\n");
  MX_USB_DEVICE_Init();
#if DIAG_DISABLE_IWDG
  printf("[DIAG] IWDG disabled by DIAG_DISABLE_IWDG\r\n");
#else
  MX_IWDG1_Init();
#endif
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

  /* PE10 is TIM1_CH2N and the LCD backlight is active-low.  For PWM1 the
     complementary output stays LOW for the whole cycle when CCR2=ARR+1,
     which gives the backlight its maximum (100%) brightness. */
  uint32_t bl_period_ticks = __HAL_TIM_GET_AUTORELOAD(&htim1) + 1u;
  __HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_2, bl_period_ticks);
  printf("[PWM] LCD backlight set to maximum: CCR2=%lu\r\n",
         (unsigned long)bl_period_ticks);

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
  uint32_t duty_x10 = (bl_period_ticks != 0u)
                    ? ((ccr2 * 1000UL + bl_period_ticks / 2u) / bl_period_ticks)
                    : 0u;
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

#if MAIN_LOOP_ISOLATION_TEST && (MAIN_LOOP_ISOLATION_STAGE == 0)
  {
    static uint32_t iso_last_lcd_ms = 0u;
    static uint32_t iso_loop_max_cycles = 0u;
    static uint32_t iso_loop_min_cycles = 0xFFFFFFFFu;

    if ((uint32_t)(now - iso_last_lcd_ms) >= 50u) {
      iso_last_lcd_ms = now;
      DrawUSBStatus();
    }

    {
      uint32_t dwt_end = DWT->CYCCNT;
      uint32_t loop_cycles = (uint32_t)(dwt_end - dwt_start);
      if (loop_cycles > iso_loop_max_cycles) {
        iso_loop_max_cycles = loop_cycles;
      }
      if (loop_cycles < iso_loop_min_cycles) {
        iso_loop_min_cycles = loop_cycles;
      }
      loop_cycle_last_avg = loop_cycles;
    }

    continue;
  }
#endif

#if MAIN_LOOP_ISOLATION_TEST && (MAIN_LOOP_ISOLATION_STAGE == 2)
  goto main_loop_second_half;
#endif

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
  rs485_freq_trim_service(now);
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

  /* Опциональный авто-STOP USB-потока при отсутствии SOF.
     Логическая фаза и ADC не зависят от USB; физический TX200 разрешён только
     сочетанием сохранённого host-request и активного stream. */
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
      }
    }
  }
#endif

#if MAIN_LOOP_ISOLATION_TEST && (MAIN_LOOP_ISOLATION_STAGE == 1)
  {
    static uint32_t iso_stage1_last_lcd_ms = 0u;
    if ((uint32_t)(now - iso_stage1_last_lcd_ms) >= 50u) {
      iso_stage1_last_lcd_ms = now;
      DrawUSBStatus();
    }
    continue;
  }
#endif

main_loop_second_half:

  // PROG('V'); // vendor diag disabled for isolation
  // vnd_diag_send64_once();
  // PROG('v');

  /* Запуск vendor stream task: обслуживает START/STOP, ADC restart, фоновые задачи и USB TX. */
#if !SAFE_MINIMAL
  {
    extern volatile uint8_t vnd_tx_kick;
    extern uint8_t vnd_is_streaming(void);
    static uint32_t last_vendor_ms = 0;
    if (vnd_tx_kick || vnd_is_streaming() || (now - last_vendor_ms) >= 5u) {
      last_vendor_ms = now;
      extern void Vendor_Stream_Task(void);
      Vendor_Stream_Task();
    }
  }
#if !MAIN_LOOP_ISOLATION_TEST || (MAIN_LOOP_ISOLATION_STAGE != 3) || (MAIN_LOOP_ISOLATION_STAGE3_PART == 1)
  /* Периодический SYNC-лог отключён: COM оставляем под compact phase-monitor. */
  rs485_role_rx_service();
  rs485_sync_assigned_role_service(now);
  rs485_role_claim_service(now);
  rs485_role_slave_claim_service(now);
  rs485_role_enumeration_service(now);
  rs485_node_claim_service(now);
  rs485_identity_service(now);
  rs485_uid_service(now);
  rs485_role_persist_service(now);
  /* UID discovery remains available for numbering/identity, never for roles. */
  /* Периодический ROLE-лог отключён: в COM оставляем только компактный phase-monitor. */
  if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
      (sync_last_edge_ms != 0u) &&
      ((now - sync_last_edge_ms) > 250u)) {
    rs485_sync_relation_score = 0;
    rs485_sync_phase_relation = RS485_SYNC_RELATION_UNKNOWN;
    rs485_anti_phase_recovery_active = 0u;
    rs485_anti_phase_recovery_packets = 0u;
    rs485_phase_guard_recovery_packets = 0u;
    tim15_request_hold_offset(0);
  }
  /* Подстройка частоты TIM15 по фазе (TIM16 счётчик, PD5 reset) */
  {
    extern void adc_sync_pd5_apply_adjustment(void);
    adc_sync_pd5_apply_adjustment();
  }
  rs485_sync_service_tx();
#if RS485_SYNC_UART_PERIODIC_LOG_ENABLE
  {
    uint32_t current_edges = rs485_sync_control_edge_count;

    if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
        (RS485_SYNC_UART_PERIODIC_LOG_EDGES != 0u)) {
      if ((rs485_sync_uart_next_log_edge == 0u) ||
          (current_edges + RS485_SYNC_UART_PERIODIC_LOG_EDGES < rs485_sync_uart_next_log_edge)) {
        rs485_sync_uart_next_log_edge =
            ((current_edges / RS485_SYNC_UART_PERIODIC_LOG_EDGES) + 1u) *
            RS485_SYNC_UART_PERIODIC_LOG_EDGES;
      }

      if (current_edges >= rs485_sync_uart_next_log_edge) {
        uint32_t primask = __get_PRIMASK();
        uint32_t period200_snapshot = 0u;
        uint32_t prev_period200_snapshot = 0u;
        uint32_t tim5_snapshot = 0u;
        uint32_t prev_tim5_snapshot = 0u;
        uint32_t buf_phase_snapshot = 0u;
        uint32_t buf_phase_seq_snapshot = 0u;
        uint32_t prev_buf_phase_snapshot = 0u;
        uint32_t tim15_cap_snapshot = 0u;
        static uint32_t last_period200_snapshot = 0u;
        static uint32_t last_tim5_snapshot = 0u;
        static uint32_t last_buf_phase_snapshot = 0u;
        static uint8_t have_last_snapshot = 0u;

        __disable_irq();
        current_edges = rs485_sync_control_edge_count;
        period200_snapshot = rs485_sync_control_period_ticks;
        tim5_snapshot = rs485_sync_control_tim5_ticks;
        buf_phase_snapshot = rs485_sync_control_buf_phase;
        buf_phase_seq_snapshot = rs485_sync_control_buf_seq;
        tim15_cap_snapshot = rs485_sync_control_tim15_cap;
        if (primask == 0u) {
          __enable_irq();
        }

        prev_period200_snapshot = have_last_snapshot ?
            (uint32_t)(period200_snapshot - last_period200_snapshot) : 0u;
        prev_tim5_snapshot = have_last_snapshot ?
            (uint32_t)(tim5_snapshot - last_tim5_snapshot) : 0u;
        prev_buf_phase_snapshot = have_last_snapshot ?
            (uint32_t)(buf_phase_snapshot - last_buf_phase_snapshot) : 0u;
        last_period200_snapshot = period200_snapshot;
        last_tim5_snapshot = tim5_snapshot;
        last_buf_phase_snapshot = buf_phase_snapshot;
        have_last_snapshot = 1u;

        printf("[SYNC200] edge=%lu kind=%u sync200_period=%lu d_p200=%ld half_period=%lu d_half=%ld buf_phase=%lu d_buf=%ld buf_seq=%lu tim15_cap=%lu\r\n",
               (unsigned long)current_edges,
               (unsigned)RS485_SYNC_CONTROL_EDGE_KIND,
               (unsigned long)period200_snapshot,
               (long)((int32_t)prev_period200_snapshot),
               (unsigned long)tim5_snapshot,
               (long)((int32_t)prev_tim5_snapshot),
               (unsigned long)buf_phase_snapshot,
               (long)((int32_t)prev_buf_phase_snapshot),
               (unsigned long)buf_phase_seq_snapshot,
               (unsigned long)tim15_cap_snapshot);

        do {
          rs485_sync_uart_next_log_edge += RS485_SYNC_UART_PERIODIC_LOG_EDGES;
        } while (current_edges >= rs485_sync_uart_next_log_edge);
      }
    } else {
      rs485_sync_uart_next_log_edge = 0u;
    }
  }
#endif
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
#endif

#if !MAIN_LOOP_ISOLATION_TEST || (MAIN_LOOP_ISOLATION_STAGE != 3) || (MAIN_LOOP_ISOLATION_STAGE3_PART == 2)
  #if !MAIN_LOOP_ISOLATION_TEST || (MAIN_LOOP_ISOLATION_STAGE != 3) || (MAIN_LOOP_ISOLATION_STAGE3_PART2_SUB == 1)
    /* Вотчдог ADC/DMA: если давно нет DMA событий, мягко перезапустить цепочку выборки. */
    {
      extern void adc_stream_watchdog(void);
      adc_stream_watchdog();
      }
    #endif
  #endif

  #if !MAIN_LOOP_ISOLATION_TEST || (MAIN_LOOP_ISOLATION_STAGE != 3) || (MAIN_LOOP_ISOLATION_STAGE3_PART == 2)
    /* Применение подстройки частоты TIM15 по PD5 (slave polling) */
    {
      extern void adc_sync_pd5_apply_adjustment(void);
      adc_sync_pd5_apply_adjustment();
    }
    // Проверка и выключение LED по таймауту (UART RX индикация)
    extern void CDC_LED_Process(void);
    CDC_LED_Process();
  #endif
#endif

  /* Периодическое обновление статуса на LCD (вернули после отката) */
  {
    static uint32_t last_lcd_ms = 0;
    static uint8_t lcd_first_update = 1;
    extern uint8_t vnd_is_streaming(void);
    uint32_t lcd_period_ms = vnd_is_streaming() ? 1000u : 100u;
    // Первое обновление сразу после старта (в течение первых 200ms)
    if (lcd_first_update && now >= 200) {
      lcd_first_update = 0;
      last_lcd_ms = now;
      DrawUSBStatus();
    }
    // Во время USB-стрима LCD не должен ограничивать Vendor_Stream_Task.
    else if (!lcd_first_update && (now - last_lcd_ms >= lcd_period_ms)) {
      last_lcd_ms = now;
      DrawUSBStatus();
    }
  }

  /* Low-priority change monitor: polls slow system values only when Vendor IN is idle. */
  Vendor_ChangeEvent_Task();

#if MAIN_LOOP_ISOLATION_TEST && (MAIN_LOOP_ISOLATION_STAGE == 3)
  continue;
#endif

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
#if DIAG_DISABLE_IWDG
  if(iwdg_enabled_runtime){ printf("[WARN] IWDG active unexpected\r\n"); }
#endif
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
          printf("SYNCSTATE    - compact sync state over raw UART\r\n");
          printf("OPTIC        - optic tx/rx status over raw UART\r\n");
          printf("TX200 0|1    - set 200Hz marker TX request; OPTIC shows tx_req/tx200\r\n");
          printf("OPTP 0..255  - set 38kHz optic TX power over UART\r\n");
          printf("OPTH 0..600  - set optic hold time in deciseconds\r\n");
          printf("ROLE MASTER  - rejected; MASTER requires host Unix timestamp\r\n");
          printf("ROLE SLAVE   - force slave role (host-forced)\r\n");
          printf("ROLE AUTO    - release host-forced, return to auto\r\n");
          printf("EVT          - EVT1 queue/send diagnostics\r\n");
          printf("VND          - Vendor stream/CRC diagnostics\r\n");
          printf("DC           - DC load/save diagnostics\r\n");
          printf("DCSAVE       - save current DC to Flash once\r\n");
          printf("PHASE [AUTO|ticks] - show/set sync target phase\r\n");
          printf("ARR [offset] - show/set TIM15 ARR fine offset (- speeds up)\r\n");
          printf("FTRIM [0|1] - slow fractional sync frequency trim\r\n");
          printf("APPROACH [0|1] - faster phase approach before slow trim\r\n");
          printf("RS485ID [0..31] - show/set persistent role-independent node id\r\n");
          printf("PERF         - performance stats\r\n");
          printf("FPS          - FPS statistics only\r\n");
          printf("RESET        - software reset\r\n");
          printf("HELP         - this help message\r\n");
          printf("===============================\r\n");
        } else if(strncmp(uart1_cmd_buf, "VER", 3) == 0 || strncmp(uart1_cmd_buf, "VERSION", 7) == 0){
          uart1_raw_print_version();
        } else if(strncmp(uart1_cmd_buf, "STATUS", 6) == 0){
          printf("\r\n=== DEVICE STATUS (UART1) ===\r\n");
          printf("Uptime: %lu ms\r\n", HAL_GetTick());
          if (g_sync_target_phase_ticks == SYNC_TARGET_PHASE_AUTO) {
            printf("Sync target phase: AUTO target=%ld samples\r\n", (long)SYNC_TARGET_PHASE_SAMPLES);
          } else {
            printf("Sync target phase: %ld ticks\r\n", (long)((int32_t)g_sync_target_phase_ticks));
          }
          printf("TIM15 ARR fine offset: %ld\r\n", (long)adc_stream_get_arr_fine_offset());
          printf("Sync freq trim: en=%u dir=%d interval=%lu drift=%ld nudges=%lu\r\n",
            (unsigned)rs485_freq_trim_enabled,
            (int)rs485_freq_trim_dir,
            (unsigned long)rs485_freq_trim_interval_buffers,
            (long)rs485_freq_trim_last_window_delta_ticks,
            (unsigned long)rs485_freq_trim_nudge_count);
          printf("Sync freq trim window: edge=%u count=%lu accum=%ld\r\n",
            (unsigned)rs485_freq_trim_edge_kind,
            (unsigned long)rs485_freq_trim_window_count,
            (long)rs485_freq_trim_window_delta_accum);
          printf("Sync half snap: en=%u armed=%u req=%u count=%lu err=%ld last_ms=%lu\r\n",
            (unsigned)rs485_phase_half_snap_enabled,
            (unsigned)rs485_phase_half_snap_armed,
            (unsigned)rs485_phase_half_snap_request,
            (unsigned long)rs485_phase_half_snap_count,
            (long)rs485_phase_half_snap_last_error,
            (unsigned long)rs485_phase_half_snap_last_ms);
          printf("Sync approach: en=%u active=%u err=%lu delta=%ld spacing=%lu nudges=%lu\r\n",
            (unsigned)rs485_phase_approach_enabled,
            (unsigned)rs485_phase_approach_active,
            (unsigned long)rs485_phase_approach_last_abs_ticks,
            (long)rs485_phase_approach_last_delta,
            (unsigned long)rs485_phase_approach_last_spacing,
            (unsigned long)rs485_phase_approach_nudge_count);
          printf("Sync target mode: half_shift=%u visual=%u control_200hz=%u edge=%u xor=%u\r\n",
            (unsigned)RS485_SYNC_TARGET_HALF_PERIOD_SHIFT,
            (unsigned)RS485_SYNC_VISUAL_PHASE_TRACK,
            (unsigned)RS485_SYNC_CONTROL_200HZ_ONLY,
            (unsigned)RS485_SYNC_CONTROL_EDGE_KIND,
            (unsigned)RS485_SYNC_IN_PHASE_LOCAL_EDGE_XOR);
          printf("Use 'PERF' or 'FPS' for detailed statistics\r\n");
          printf("==============================\r\n");
        } else if(strncmp(uart1_cmd_buf, "EVT", 3) == 0){
          printf("\r\n=== EVT1 STATUS (UART1) ===\r\n");
          Vendor_ChangeEvent_DiagPrint();
          printf("===========================\r\n");
        } else if(strncmp(uart1_cmd_buf, "SYNCSTATE", 9) == 0 ||
                  strncmp(uart1_cmd_buf, "SYNC", 4) == 0){
          uart1_raw_print_sync_state();
        } else if(strncmp(uart1_cmd_buf, "OPTIC", 5) == 0){
          uart1_raw_print_optic_state();
        } else if(strncmp(uart1_cmd_buf, "TX200", 5) == 0){
          char *arg = uart1_cmd_buf + 5;
          long enable = 0;
          while(*arg == ' ') arg++;
          if(*arg == 0){
            uart1_raw_print_optic_state();
          } else if(uart1_parse_long_arg(arg, 0, 1, &enable) == 0u) {
            uart1_raw_write_str("\r\nTX200 usage: TX200 0 | TX200 1\r\n");
          } else {
            (void)vnd_set_tx_enabled((uint8_t)enable);
            uart1_raw_write_str("\r\nTX200 OK\r\n");
            uart1_raw_print_optic_state();
          }
        } else if(strncmp(uart1_cmd_buf, "OPTP", 4) == 0){
          char *arg = uart1_cmd_buf + 4;
          long power = 0;
          while(*arg == ' ') arg++;
          if(*arg == 0){
            uart1_raw_print_optic_state();
          } else if(uart1_parse_long_arg(arg, 0, 255, &power) == 0u) {
            uart1_raw_write_str("\r\nOPTP usage: OPTP 0..255\r\n");
          } else {
            (void)optic_tx_set_power((uint8_t)power);
            uart1_raw_write_str("\r\nOPTP OK\r\n");
            uart1_raw_print_optic_state();
          }
        } else if(strncmp(uart1_cmd_buf, "OPTH", 4) == 0){
          char *arg = uart1_cmd_buf + 4;
          long hold_ds = 0;
          while(*arg == ' ') arg++;
          if(*arg == 0){
            uart1_raw_print_optic_state();
          } else if(uart1_parse_long_arg(arg, 0, OPTIC_ACTIVE_HOLD_MAX_DS, &hold_ds) == 0u) {
            uart1_raw_write_str("\r\nOPTH usage: OPTH 0..600\r\n");
          } else {
            (void)optic_sensor_set_hold_deciseconds((uint16_t)hold_ds);
            uart1_raw_write_str("\r\nOPTH OK\r\n");
            uart1_raw_print_optic_state();
          }
        } else if(strncmp(uart1_cmd_buf, "VND", 3) == 0){
          printf("\r\n=== VENDOR STREAM DIAG (UART1) ===\r\n");
          Vendor_StreamDiagPrint();
          printf("=================================\r\n");
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
        } else if(strncmp(uart1_cmd_buf, "ARR", 3) == 0){
          char *arg = uart1_cmd_buf + 3;
          while(*arg == ' ') arg++;
          if(*arg == 0){
            printf("[UART] ARR fine offset=%ld\r\n", (long)adc_stream_get_arr_fine_offset());
          } else {
            char *end_ptr = NULL;
            long arr_offset = strtol(arg, &end_ptr, 10);
            while (end_ptr && *end_ptr == ' ') end_ptr++;
            if ((end_ptr == arg) || (end_ptr && *end_ptr != 0)) {
              printf("[UART] ARR parse error: '%s'\r\n", arg);
            } else {
              adc_stream_set_arr_fine_offset((int32_t)arr_offset);
              rs485_freq_trim_reset(0u);
              printf("[UART] ARR fine offset=%ld\r\n", (long)adc_stream_get_arr_fine_offset());
            }
          }
        } else if(strncmp(uart1_cmd_buf, "FTRIM", 5) == 0){
          char *arg = uart1_cmd_buf + 5;
          while(*arg == ' ') arg++;
          if(*arg == 0){
            printf("[UART] FTRIM en=%u dir=%d interval=%lu drift=%ld nudges=%lu\r\n",
              (unsigned)rs485_freq_trim_enabled,
              (int)rs485_freq_trim_dir,
              (unsigned long)rs485_freq_trim_interval_buffers,
              (long)rs485_freq_trim_last_window_delta_ticks,
              (unsigned long)rs485_freq_trim_nudge_count);
            printf("[UART] FTRIM edge=%u count=%lu accum=%ld\r\n",
              (unsigned)rs485_freq_trim_edge_kind,
              (unsigned long)rs485_freq_trim_window_count,
              (long)rs485_freq_trim_window_delta_accum);
          } else {
            char *end_ptr = NULL;
            long enable = strtol(arg, &end_ptr, 10);
            while (end_ptr && *end_ptr == ' ') end_ptr++;
            if ((end_ptr == arg) || (end_ptr && *end_ptr != 0) ||
                ((enable != 0) && (enable != 1))) {
              printf("[UART] FTRIM usage: FTRIM 0 | FTRIM 1\r\n");
            } else {
              rs485_freq_trim_enabled = (uint8_t)enable;
              rs485_freq_trim_reset((enable == 0) ? 1u : 0u);
              printf("[UART] FTRIM en=%u\r\n", (unsigned)rs485_freq_trim_enabled);
            }
          }
        } else if(strncmp(uart1_cmd_buf, "APPROACH", 8) == 0){
          char *arg = uart1_cmd_buf + 8;
          while(*arg == ' ') arg++;
          if(*arg == 0){
            printf("[UART] APPROACH en=%u active=%u err=%lu delta=%ld dir=%d worse=%u spacing=%lu nudges=%lu skips=%lu done=%lu\r\n",
              (unsigned)rs485_phase_approach_enabled,
              (unsigned)rs485_phase_approach_active,
              (unsigned long)rs485_phase_approach_last_abs_ticks,
              (long)rs485_phase_approach_last_delta,
              (int)rs485_phase_approach_dir,
              (unsigned)rs485_phase_approach_worse_count,
              (unsigned long)rs485_phase_approach_last_spacing,
              (unsigned long)rs485_phase_approach_nudge_count,
              (unsigned long)rs485_phase_approach_skip_count,
              (unsigned long)rs485_phase_approach_done_count);
          } else {
            char *end_ptr = NULL;
            long enable = strtol(arg, &end_ptr, 10);
            while (end_ptr && *end_ptr == ' ') end_ptr++;
            if ((end_ptr == arg) || (end_ptr && *end_ptr != 0) ||
                ((enable != 0) && (enable != 1))) {
              printf("[UART] APPROACH usage: APPROACH 0 | APPROACH 1\r\n");
            } else {
              rs485_phase_approach_enabled = (uint8_t)enable;
              rs485_phase_approach_active = 0u;
              rs485_phase_approach_last_delta = 0;
              rs485_phase_approach_prev_sign = 0;
              rs485_phase_approach_holdoff_edges = 0u;
              rs485_phase_approach_dir = 1;
              rs485_phase_approach_have_prev_abs = 0u;
              rs485_phase_approach_prev_abs_ticks = 0u;
              rs485_phase_approach_worse_count = 0u;
              rs485_freq_trim_reset(0u);
              printf("[UART] APPROACH en=%u\r\n", (unsigned)rs485_phase_approach_enabled);
            }
          }
        } else if(strncmp(uart1_cmd_buf, "DCSAVE", 6) == 0){
          vnd_dc_request_save_to_flash();
          printf("[UART] DCSAVE requested\r\n");
        } else if(strncmp(uart1_cmd_buf, "DC", 2) == 0){
          uint32_t now_ms = HAL_GetTick();
          uint32_t dirty_age = (vnd_dc_dirty_since_ms == 0u) ? 0xFFFFFFFFu : (now_ms - vnd_dc_dirty_since_ms);
          printf("\r\n=== DC STATUS (UART1) ===\r\n");
          printf("settle_ms    : %lu (0=off, 1=fastest, 1000=1s)\r\n",
                 (unsigned long)vnd_dc_speed_settle_ms);
          printf("dirty        : %u\r\n", (unsigned)vnd_dc_dirty_public);
          printf("dirty_age_ms : %lu\r\n", (unsigned long)dirty_age);
          printf("save_period  : %lu\r\n", (unsigned long)vnd_dc_save_period_ms);
          printf("save_ok      : %lu\r\n", (unsigned long)vnd_dc_save_ok_count);
          printf("save_fail    : %lu\r\n", (unsigned long)vnd_dc_save_fail_count);
          printf("save_result  : %u\r\n", (unsigned)vnd_dc_save_last_result);
          printf("save_last_ms : %lu\r\n", (unsigned long)vnd_dc_save_last_ms);
          printf("write_counter: %lu\r\n", (unsigned long)vnd_dc_write_counter_public);
          printf("load_flags   : 0x%02X\r\n", (unsigned)vnd_dc_load_flags_public);
          printf("loaded_crc16 : 0x%04X\r\n", (unsigned)vnd_dc_loaded_crc16_public);
          printf("next_off     : %lu\r\n", (unsigned long)vnd_dc_flash_next_off_public);
          printf("last_err     : 0x%08lX\r\n", (unsigned long)vnd_dc_save_last_err);
          printf("bank/sector  : %lu/%lu sec_err=%lu\r\n",
                 (unsigned long)vnd_dc_save_last_bank,
                 (unsigned long)vnd_dc_save_last_sector,
                 (unsigned long)vnd_dc_save_last_sector_error);
          printf("=========================\r\n");
        } else if(strncmp(uart1_cmd_buf, "FPS", 3) == 0){
          vnd_report_fps_stats();
        } else if(strncmp(uart1_cmd_buf, "PERF", 4) == 0){
          vnd_print_perf_stats();
        } else if(strncmp(uart1_cmd_buf, "RESET", 5) == 0){
          printf("[UART] RESET command received - performing software reset\r\n");
          HAL_Delay(100);
          NVIC_SystemReset();
        } else if(strncmp(uart1_cmd_buf, "RS485ID", 7) == 0){
          char *arg = uart1_cmd_buf + 7;
          while(*arg == ' ') arg++;
          if(*arg == 0){
            printf("[UART] RS485ID=%u hint=%u legacy_master=%u\r\n",
              (unsigned)rs485_local_node_id,
              (unsigned)rs485_local_uid_hint,
              (unsigned)rs485_status_legacy_master_mode);
          } else {
            char *end_ptr = NULL;
            long node_id = strtol(arg, &end_ptr, 10);
            while (end_ptr && *end_ptr == ' ') end_ptr++;
            if ((end_ptr == arg) || (end_ptr && *end_ptr != 0) ||
                (node_id < 0) || (node_id > RS485_DISCOVERY_MAX_ID)) {
              printf("[UART] RS485ID usage: RS485ID 0..31\r\n");
            } else {
              rs485_set_local_node_id_from_host((uint8_t)node_id);
              printf("[UART] RS485ID=%ld\r\n", node_id);
            }
          }
        } else if(strncmp(uart1_cmd_buf, "RS485", 5) == 0 || strncmp(uart1_cmd_buf, "RS488", 5) == 0){
          /* Подробный статус RS485 sync/role */
          uint32_t now_ms = HAL_GetTick();
          uint32_t edge_age = (sync_last_edge_ms == 0u) ? 0xFFFFFFFFu : (now_ms - sync_last_edge_ms);
          uint8_t next_slot = (rs485_slave_count_estimate < RS485_DISCOVERY_MAX_ID) ?
                              (uint8_t)(rs485_slave_count_estimate + 1u) :
                              RS485_DISCOVERY_MAX_ID;
          uint32_t primask = __get_PRIMASK();
          int32_t phase_err_snapshot = 0;
          int32_t phase_ctrl_err_snapshot = 0;
          int32_t phase_pulse_snapshot = 0;
          uint8_t phase_quiet_snapshot = 0u;
          uint8_t phase_near_snapshot = 0u;
          uint16_t phase_stable_snapshot = 0u;
          uint32_t phase_pulses_snapshot = 0u;
          uint32_t phase_busy_snapshot = 0u;
          uint32_t phase_space_snapshot = 0u;
          uint32_t phase_spacing_snapshot = 0u;
          uint8_t trim_enabled_snapshot = 0u;
          int8_t trim_dir_snapshot = 0;
          uint32_t trim_interval_snapshot = 0u;
          int32_t trim_drift_snapshot = 0;
          int32_t trim_avg_snapshot = 0;
          uint32_t trim_windows_snapshot = 0u;
          uint32_t trim_nudges_snapshot = 0u;
          uint32_t trim_skips_snapshot = 0u;
          uint8_t trim_edge_snapshot = 0u;
          uint32_t trim_count_snapshot = 0u;
          int32_t trim_accum_snapshot = 0;
          uint8_t half_snap_enabled_snapshot = 0u;
          uint8_t half_snap_armed_snapshot = 0u;
          uint8_t half_snap_request_snapshot = 0u;
          uint32_t half_snap_count_snapshot = 0u;
          int32_t half_snap_error_snapshot = 0;
          uint32_t half_snap_last_ms_snapshot = 0u;
          uint8_t approach_enabled_snapshot = 0u;
          uint8_t approach_active_snapshot = 0u;
          uint32_t approach_abs_snapshot = 0u;
          int32_t approach_delta_snapshot = 0;
          uint32_t approach_spacing_snapshot = 0u;
          uint32_t approach_nudges_snapshot = 0u;
          uint32_t approach_skips_snapshot = 0u;
          uint32_t approach_done_snapshot = 0u;
          uint16_t approach_holdoff_snapshot = 0u;
          int8_t approach_dir_snapshot = 0;
          uint8_t approach_worse_snapshot = 0u;
          uint32_t tim5_period_snapshot = 0u;
          uint32_t tim15_cap_snapshot = 0u;
          uint32_t tim15_cnt_snapshot = 0u;
          uint32_t tim15_arr_snapshot = 0u;
          uint32_t active_samples_snapshot = 0u;
          uint32_t sample_ticks_snapshot = 0u;
          uint32_t bit_ticks_snapshot = 0u;

          __disable_irq();
          phase_err_snapshot = sync_phase_last_error_ticks;
          phase_ctrl_err_snapshot = sync_phase_last_control_error_ticks;
          phase_pulse_snapshot = sync_phase_last_pulse_delta;
          phase_quiet_snapshot = sync_phase_quiet_mode;
          phase_near_snapshot = sync_phase_near_raw_mode;
          phase_stable_snapshot = sync_phase_quiet_enter_count;
          phase_pulses_snapshot = sync_phase_fast_pulses;
          phase_busy_snapshot = sync_phase_fast_skip_busy;
          phase_space_snapshot = sync_phase_fast_skip_spacing;
          phase_spacing_snapshot = sync_phase_fast_last_spacing;
          trim_enabled_snapshot = rs485_freq_trim_enabled;
          trim_dir_snapshot = rs485_freq_trim_dir;
          trim_interval_snapshot = rs485_freq_trim_interval_buffers;
          trim_drift_snapshot = rs485_freq_trim_last_window_delta_ticks;
          trim_avg_snapshot = rs485_freq_trim_last_avg_delta_ticks;
          trim_windows_snapshot = rs485_freq_trim_windows;
          trim_nudges_snapshot = rs485_freq_trim_nudge_count;
          trim_skips_snapshot = rs485_freq_trim_skip_count;
          trim_edge_snapshot = rs485_freq_trim_edge_kind;
          trim_count_snapshot = rs485_freq_trim_window_count;
          trim_accum_snapshot = rs485_freq_trim_window_delta_accum;
          half_snap_enabled_snapshot = rs485_phase_half_snap_enabled;
          half_snap_armed_snapshot = rs485_phase_half_snap_armed;
          half_snap_request_snapshot = rs485_phase_half_snap_request;
          half_snap_count_snapshot = rs485_phase_half_snap_count;
          half_snap_error_snapshot = rs485_phase_half_snap_last_error;
          half_snap_last_ms_snapshot = rs485_phase_half_snap_last_ms;
          approach_enabled_snapshot = rs485_phase_approach_enabled;
          approach_active_snapshot = rs485_phase_approach_active;
          approach_abs_snapshot = rs485_phase_approach_last_abs_ticks;
          approach_delta_snapshot = rs485_phase_approach_last_delta;
          approach_spacing_snapshot = rs485_phase_approach_last_spacing;
          approach_nudges_snapshot = rs485_phase_approach_nudge_count;
          approach_skips_snapshot = rs485_phase_approach_skip_count;
          approach_done_snapshot = rs485_phase_approach_done_count;
          approach_holdoff_snapshot = rs485_phase_approach_holdoff_edges;
          approach_dir_snapshot = rs485_phase_approach_dir;
          approach_worse_snapshot = rs485_phase_approach_worse_count;
          tim5_period_snapshot = sync_tim5_period_ticks;
          tim15_cap_snapshot = sync_tim15_cnt_at_pd5;
          tim15_cnt_snapshot = htim15.Instance->CNT;
          tim15_arr_snapshot = htim15.Instance->ARR;
          if (primask == 0u) {
            __enable_irq();
          }

          active_samples_snapshot = (uint32_t)adc_stream_get_active_samples();
          if ((active_samples_snapshot != 0u) && (tim5_period_snapshot != 0u)) {
            sample_ticks_snapshot = tim5_period_snapshot / active_samples_snapshot;
          }
          bit_ticks_snapshot = tim15_sync_get_uart_bit_ticks_or_sample(
              (sample_ticks_snapshot != 0u) ? sample_ticks_snapshot : 1u);

          printf("\r\n=== RS485 STATUS ===\r\n");
          printf("mode        : %u (%s)\r\n",
            (unsigned)vnd_sync_mode_public,
            (vnd_sync_mode_public == VND_SYNC_MODE_MASTER) ? "MASTER" :
            (vnd_sync_mode_public == VND_SYNC_MODE_SLAVE)  ? "SLAVE"  : "OFF");
          printf("host_forced : %u\r\n", (unsigned)vnd_sync_is_mode_host_forced());
          printf("node_id     : %u\r\n", (unsigned)rs485_local_node_id);
          printf("node_assign : %u last=%u->%u apply=%lu\r\n",
            (unsigned)rs485_local_node_id_assigned,
            (unsigned)rs485_status_assign_last_from_id,
            (unsigned)rs485_status_assign_last_to_id,
            (unsigned long)rs485_status_assign_apply_count);
          printf("slave_count : %u\r\n", (unsigned)rs485_slave_count_estimate);
          printf("next_slot   : %u\r\n", (unsigned)next_slot);
          printf("status_byte : 0x%02X\r\n", (unsigned)rs485_status_build_local_byte());
          printf("status_word : 0x%02X 0x%02X\r\n",
            (unsigned)rs485_status_build_local_byte(),
            (unsigned)rs485_status_build_local_second_byte(rs485_status_master_selector));
          printf("master_word : 0x%02X 0x%02X sel=%u\r\n",
            (unsigned)rs485_status_master_first,
            (unsigned)rs485_status_master_second,
            (unsigned)rs485_status_master_selector);
          printf("compact_asn : %u->%u try=%u set=%lu ok=%lu alias=%u\r\n",
            (unsigned)rs485_status_assign_from_id,
            (unsigned)rs485_status_assign_to_id,
            (unsigned)rs485_status_assign_attempts,
            (unsigned long)rs485_status_assign_count,
            (unsigned long)rs485_status_assign_confirm_count,
            (unsigned)rs485_status_reply_alias_id);
          printf("peer_mask   : 0x%08lX\r\n",
            (unsigned long)rs485_status_get_recent_peer_mask(HAL_GetTick(), RS485_STATUS_PEER_HOLD_MS));
          printf("legacy_mst  : %u detects=%lu probe=%u\r\n",
            (unsigned)rs485_status_legacy_master_mode,
            (unsigned long)rs485_status_legacy_detect_count,
            (unsigned)rs485_status_legacy_probe_count);
          printf("sync_edges  : %lu\r\n", (unsigned long)sync_edge_count);
          printf("sync_reject : %lu\r\n", (unsigned long)rs485_sync_rejected_early_count);
          printf("sync_age_ms : %lu\r\n", (unsigned long)edge_age);
          printf("sync_alive  : %u\r\n", (unsigned)(edge_age <= RS485_SYNC_PRESENT_MS && sync_last_edge_ms != 0u));
          printf("timers      : tim5_period=%lu tim15_cap=%lu tim15_cnt=%lu tim15_arr=%lu samp=%lu samp_ticks=%lu bit_ticks=%lu\r\n",
            (unsigned long)tim5_period_snapshot,
            (unsigned long)tim15_cap_snapshot,
            (unsigned long)tim15_cnt_snapshot,
            (unsigned long)tim15_arr_snapshot,
            (unsigned long)active_samples_snapshot,
            (unsigned long)sample_ticks_snapshot,
            (unsigned long)bit_ticks_snapshot);
          printf("phase_rel   : %u (%s)\r\n",
            (unsigned)rs485_sync_phase_relation,
            (rs485_sync_phase_relation == RS485_SYNC_RELATION_IN_PHASE) ? "IN_PHASE" :
            (rs485_sync_phase_relation == RS485_SYNC_RELATION_ANTI_PHASE) ? "ANTI_PHASE" : "UNKNOWN");
          printf("anti_fix    : %u, packets=%u\r\n",
            (unsigned)rs485_anti_phase_recovery_active,
            (unsigned)rs485_anti_phase_recovery_packets);
          printf("phase_err   : %ld ticks, pulse=%ld\r\n",
            (long)phase_err_snapshot,
            (long)phase_pulse_snapshot);
          printf("phase_ctrl  : quiet=%u near=%u stable=%u err=%ld pulses=%lu busy=%lu space=%lu spacing=%lu\r\n",
            (unsigned)phase_quiet_snapshot,
            (unsigned)phase_near_snapshot,
            (unsigned)phase_stable_snapshot,
            (long)phase_ctrl_err_snapshot,
            (unsigned long)phase_pulses_snapshot,
            (unsigned long)phase_busy_snapshot,
            (unsigned long)phase_space_snapshot,
            (unsigned long)phase_spacing_snapshot);
          printf("freq_trim   : en=%u dir=%d edge=%u count=%lu accum=%ld interval=%lu drift=%ld avg=%ld windows=%lu nudges=%lu skips=%lu\r\n",
            (unsigned)trim_enabled_snapshot,
            (int)trim_dir_snapshot,
            (unsigned)trim_edge_snapshot,
            (unsigned long)trim_count_snapshot,
            (long)trim_accum_snapshot,
            (unsigned long)trim_interval_snapshot,
            (long)trim_drift_snapshot,
            (long)trim_avg_snapshot,
            (unsigned long)trim_windows_snapshot,
            (unsigned long)trim_nudges_snapshot,
            (unsigned long)trim_skips_snapshot);
          printf("half_snap   : en=%u armed=%u req=%u count=%lu err=%ld last_ms=%lu\r\n",
            (unsigned)half_snap_enabled_snapshot,
            (unsigned)half_snap_armed_snapshot,
            (unsigned)half_snap_request_snapshot,
            (unsigned long)half_snap_count_snapshot,
            (long)half_snap_error_snapshot,
            (unsigned long)half_snap_last_ms_snapshot);
          printf("approach    : en=%u active=%u err=%lu delta=%ld dir=%d worse=%u spacing=%lu nudges=%lu skips=%lu done=%lu hold=%u\r\n",
            (unsigned)approach_enabled_snapshot,
            (unsigned)approach_active_snapshot,
            (unsigned long)approach_abs_snapshot,
            (long)approach_delta_snapshot,
            (int)approach_dir_snapshot,
            (unsigned)approach_worse_snapshot,
            (unsigned long)approach_spacing_snapshot,
            (unsigned long)approach_nudges_snapshot,
            (unsigned long)approach_skips_snapshot,
            (unsigned long)approach_done_snapshot,
            (unsigned)approach_holdoff_snapshot);
          printf("target_mode : half_shift=%u visual=%u control_200hz=%u edge=%u xor=%u\r\n",
            (unsigned)RS485_SYNC_TARGET_HALF_PERIOD_SHIFT,
            (unsigned)RS485_SYNC_VISUAL_PHASE_TRACK,
            (unsigned)RS485_SYNC_CONTROL_200HZ_ONLY,
            (unsigned)RS485_SYNC_CONTROL_EDGE_KIND,
            (unsigned)RS485_SYNC_IN_PHASE_LOCAL_EDGE_XOR);
          printf("phase_guard : packets=%u, restart=%u/%lu\r\n",
            (unsigned)rs485_phase_guard_recovery_packets,
            (unsigned)rs485_sync_restart_request,
            (unsigned long)rs485_sync_restart_count);
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
            printf("[UART] ROLE MASTER rejected: use host SET_SYNC_MODE with Unix timestamp\r\n");
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

  /* Watchdog UART1 RX: если приём по прерыванию сорвался, переармируем без reboot. */
  {
    static uint32_t uart1_rx_watchdog_ms = 0;
    static uint8_t uart1_rx_was_armed = 1;
    if ((now - uart1_rx_watchdog_ms) >= 1000u) {
      uart1_rx_watchdog_ms = now;

      uint8_t need_rearm = 0u;
      if ((USART1->CR1 & USART_CR1_RXNEIE_RXFNEIE) == 0u) {
        need_rearm = 1u;
      }
      if (huart1.RxState != HAL_UART_STATE_BUSY_RX) {
        need_rearm = 1u;
      }

      if (need_rearm) {
        __HAL_UART_CLEAR_OREFLAG(&huart1);
        __HAL_UART_CLEAR_FEFLAG(&huart1);
        __HAL_UART_CLEAR_NEFLAG(&huart1);
        __HAL_UART_CLEAR_PEFLAG(&huart1);
        huart1.ErrorCode = HAL_UART_ERROR_NONE;
        huart1.RxState = HAL_UART_STATE_READY;

        if (HAL_UART_Receive_IT(&huart1, &uart1_rx_byte, 1) == HAL_OK) {
          if (!uart1_rx_was_armed) {
            printf("[UART1] RX watchdog re-armed\r\n");
          }
          uart1_rx_was_armed = 1u;
        } else {
          if (uart1_rx_was_armed) {
            printf("[UART1][WARN] RX watchdog re-arm failed\r\n");
          }
          uart1_rx_was_armed = 0u;
        }
      } else {
        uart1_rx_was_armed = 1u;
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
  /* Legacy TIM2_CH2 configuration. PA1 is reconfigured below as GPIO and
     driven as the complement of the PA2/PC7 TX200 phase. */
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
     ARR = BURST_PERIOD - 1 = 47: цикл из 48 «тиков несущей» (40 ON + 8 OFF) */
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
  /* Default profile is fixed at 600 samples * 400 buffers/s = 240 kHz.
     Start at the correct rate even before the RPI repeats its configuration. */
  htim15.Init.Period = 1144;     // 275 MHz / (1144+1) ≈ 240 kHz UPDATE
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
  /* USER CODE END MX_GPIO_INIT_1 */

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
  /* USER CODE END MX_GPIO_INIT_PC13 */

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
    /* PA1: инверсия TX200-фазы PA2/PC7. До явной команды enable держим HIGH (OFF). */
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
  
  /* USER CODE BEGIN MX_GPIO_INIT_2 */
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
  /* USER CODE END MX_GPIO_INIT_2 */
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
    #if !DIAG_DISABLE_IWDG
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
    // Переустанавливаем приём следующего байта
    if(HAL_UART_Receive_IT(&huart1, &uart1_rx_byte, 1) != HAL_OK){
      // В ISR не блокируемся; восстановлением займётся watchdog в main loop.
      huart1.ErrorCode = HAL_UART_ERROR_NONE;
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
    if ((rs485_master_status_tx_pending != 0u) &&
        (rs485_is_sync_byte(completed_byte) != 0u) &&
        (rs485_tx_queue_count == 0u)) {
      rs485_status_wait_bit_times(RS485_STATUS_MASTER_WORD_DELAY_BITS);
      rs485_master_status_tx_pending = 0u;
      rs485_status_window_after_tx = RS485_STATUS_WORD_BYTES;
      rs485_sync_start_tx_status_word(rs485_master_status_tx_first,
                                      rs485_master_status_tx_second);
      return;
    }
    if (rs485_status_window_after_tx != 0u) {
      rs485_status_window_after_tx--;
      if (rs485_status_window_after_tx == 0u) {
        CLEAR_BIT(huart->Instance->CR1, USART_CR1_TCIE);
        HAL_GPIO_WritePin(RS485_RDE_GPIO_Port, RS485_RDE_Pin, GPIO_PIN_RESET);
        rs485_status_begin_window();
        return;
      }
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
  if (huart->Instance == USART1) {
    // Быстро очищаем ошибки RX и немедленно переармируем приём.
    huart->Instance->ICR = USART_ICR_PECF |
                           USART_ICR_FECF |
                           USART_ICR_NECF |
                           USART_ICR_ORECF;
    huart->ErrorCode = HAL_UART_ERROR_NONE;
    huart->RxState = HAL_UART_STATE_READY;
    (void)HAL_UART_Receive_IT(&huart1, &uart1_rx_byte, 1);
    return;
  }

  if (huart->Instance == USART2) {
    rs485_uart_error_count++;
    rs485_tx_busy = 0u;
    rs485_tx_start_ms = 0u;
    rs485_status_rx_word_index = 0u;
    rs485_status_rx_word_after_sync = 0u;
    rs485_role_reset_rx();
    rs485_uid_rx_reset();
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
    uint8_t input_level =
        (HAL_GPIO_ReadPin(OPTIC_RX_GPIO_Port, OPTIC_RX_Pin) == GPIO_PIN_SET)
            ? 1u
            : 0u;

    optic_input_level_public = input_level;
    if (input_level != 0u) {
      optic_last_high_ms = now_ms;
      if (optic_sensor_state_public == 0u) {
        optic_sensor_state_public = 1u;
        (void)rs485_sensor_event_set_local(0u, 1u);
        need_usb_status_refresh = 1u;
      }
    }
  }
}

static void lcd_print_padded_if_changed(int x, int y, const char* new_text,
                    char *prev, size_t buf_sz,
                    uint8_t max_len, uint8_t font_height,
                    uint16_t fg, uint16_t bg,
                    uint16_t *prev_fg, uint16_t *prev_bg)
{
    if(!new_text || !prev || buf_sz == 0) return;
    if(max_len >= buf_sz) max_len = (uint8_t)(buf_sz - 1u);
    char line[32];
    uint8_t char_width = (font_height == 12u) ? 6u : 8u;
    uint8_t redraw_all = 0u;
    size_t n = strlen(new_text);
    if(n > max_len) n = max_len;
    memcpy(line, new_text, n);
    while(n < max_len) line[n++] = ' ';
    line[n] = 0;

    if(prev_fg && *prev_fg != fg) redraw_all = 1u;
    if(prev_bg && *prev_bg != bg) redraw_all = 1u;

    for(uint8_t i = 0u; i < max_len; i++){
      if(redraw_all || prev[i] != line[i]){
        LCD_ShowChar((uint16_t)(x + (int)i * (int)char_width),
                     (uint16_t)y,
                     (uint8_t)line[i],
                     font_height,
                     fg,
                     bg);
      }
    }

    memcpy(prev, line, max_len);
    prev[max_len] = 0;
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
  if(max_len >= buf_sz) max_len = (uint8_t)(buf_sz - 1u);

  char_width = (font_height == 12u) ? 6u : 8u;
  badge_w = (uint16_t)(max_len * char_width + (2u * pad_x));
  badge_h = (uint16_t)(font_height + (2u * pad_y));
  text_len = strlen(new_text);
  if(text_len > max_len) text_len = max_len;
  memcpy(text, new_text, text_len);
  while(text_len < max_len) text[text_len++] = ' ';
  text[text_len] = 0;

  uint8_t redraw_all = 0u;
  if(prev_fg && *prev_fg != fg) redraw_all = 1u;
  if(prev_bg && *prev_bg != bg) redraw_all = 1u;
  if(redraw_all){
    LCD_FillRect((uint16_t)x, (uint16_t)y, badge_w, badge_h, bg);
  }
  for(uint8_t i = 0u; i < max_len; i++){
    if(redraw_all || prev[i] != text[i]){
      LCD_ShowChar((uint16_t)(x + pad_x + (uint16_t)i * char_width),
                   (uint16_t)(y + pad_y),
                   (uint8_t)text[i],
                   font_height,
                   fg,
                   bg);
    }
  }

  memcpy(prev, text, max_len);
  prev[max_len] = 0;
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

static uint16_t lcd_role_overlay_pick_color(void)
{
  static uint32_t prng = 0xA53C5A7Bu;
  /* Keep only the highest-luminance RGB565 colors.  Pure red and blue were
     technically saturated but looked much darker from a distance. */
  static const uint16_t colors[] = {
    WHITE, YELLOW, CYAN
  };
  prng ^= (HAL_GetTick() + 0x9E3779B9u);
  prng ^= (sync_edge_count << 7);
  prng ^= (prng << 13);
  prng ^= (prng >> 17);
  prng ^= (prng << 5);
  return colors[prng % (uint32_t)(sizeof(colors) / sizeof(colors[0]))];
}

static void lcd_draw_scaled_char_1608(uint16_t x, uint16_t y, char ch,
                                      uint8_t scale, uint16_t color)
{
  uint8_t idx;

  if ((ch < ' ') || ((uint8_t)(ch - ' ') >= 159u) || (scale == 0u)) {
    return;
  }

  idx = (uint8_t)(ch - ' ');
  for (uint8_t col = 0u; col < 8u; col++) {
    for (uint8_t row = 0u; row < 16u; row++) {
      uint8_t b = asc2_1608[idx][(uint8_t)(col * 2u + (row / 8u))];
      if ((b & (uint8_t)(0x80u >> (row & 7u))) != 0u) {
        LCD_FillRect((uint16_t)(x + ((uint16_t)col * scale)),
                     (uint16_t)(y + ((uint16_t)row * scale)),
                     scale,
                     scale,
                     color);
      }
    }
  }
}

static void lcd_role_overlay_make_text(char out[4])
{
  vnd_lcd_sync_snapshot_t snap;
  uint8_t value = 0u;

  vnd_get_lcd_sync_snapshot(&snap);
  if (snap.raw_mode == VND_SYNC_MODE_SLAVE) {
    out[0] = 'S';
    value = (uint8_t)(snap.node_id & 0x1Fu);
  } else if (snap.raw_mode == VND_SYNC_MODE_MASTER) {
    out[0] = 'M';
    value = (uint8_t)(snap.node_id & 0x1Fu);
  } else {
    out[0] = 'O';
  }

  if (((snap.raw_mode == VND_SYNC_MODE_SLAVE) ||
       (snap.raw_mode == VND_SYNC_MODE_MASTER)) &&
      (snap.node_id_assigned == 0u)) {
    /* Assignment is a separate state; numeric ID 0 is a public M00/S00. */
    out[1] = '-';
    out[2] = '-';
  } else if (snap.raw_mode == VND_SYNC_MODE_OFF) {
    out[1] = ' ';
    out[2] = ' ';
  } else {
    out[1] = (char)('0' + ((value / 10u) % 10u));
    out[2] = (char)('0' + (value % 10u));
  }
  out[3] = '\0';
}

static void lcd_draw_role_overlay_text(const char text[4], uint16_t color)
{
  const uint8_t scale = 5u;
  const uint16_t char_w = (uint16_t)(8u * scale);
  uint16_t x = (uint16_t)((LCD_W - (3u * char_w)) / 2u);

  LCD_FillRect(0, 0, LCD_W, LCD_H, BLACK);
  for (uint8_t i = 0u; i < 3u; i++) {
    if (text[i] != ' ') {
      lcd_draw_scaled_char_1608((uint16_t)(x + ((uint16_t)i * char_w)),
                                0u,
                                text[i],
                                scale,
                                color);
    }
  }
}

static uint8_t lcd_role_overlay_service(uint32_t now, uint8_t *normal_redraw_needed)
{
  static uint8_t active = 0u;
  static uint8_t was_enabled = 0u;
  static uint32_t phase_start_ms = 0u;
  static uint32_t seen_config_seq = 0xFFFFFFFFu;
  static uint16_t current_color = WHITE;
  static char last_text[4] = "";
  uint8_t enabled = vnd_lcd_role_overlay_enabled;
  uint32_t period_ms = (uint32_t)vnd_lcd_role_overlay_period_s * 1000u;
  uint32_t duration_ms = (uint32_t)vnd_lcd_role_overlay_duration_s * 1000u;

  if (period_ms < 3000u) {
    period_ms = 3000u;
  }
  if (duration_ms < 3000u) {
    duration_ms = 3000u;
  }

  if (seen_config_seq != vnd_lcd_role_overlay_config_seq) {
    seen_config_seq = vnd_lcd_role_overlay_config_seq;
    active = 0u;
    phase_start_ms = now - period_ms;
    last_text[0] = '\0';
    vnd_lcd_role_overlay_active = 0u;
    if (normal_redraw_needed != NULL) {
      *normal_redraw_needed = 1u;
    }
  }

  if (enabled == 0u) {
    if ((active != 0u) || (was_enabled != 0u)) {
      active = 0u;
      was_enabled = 0u;
      vnd_lcd_role_overlay_active = 0u;
      LCD_FillRect(0, 0, LCD_W, LCD_H, BLACK);
      if (normal_redraw_needed != NULL) {
        *normal_redraw_needed = 1u;
      }
    }
    return 0u;
  }
  was_enabled = 1u;

  if (active == 0u) {
    if ((now - phase_start_ms) < period_ms) {
      return 0u;
    }
    active = 1u;
    phase_start_ms = now;
    current_color = lcd_role_overlay_pick_color();
    vnd_lcd_role_overlay_rgb565 = current_color;
    vnd_lcd_role_overlay_color_id = vnd_lcd_sync_color_to_id(current_color);
    vnd_lcd_role_overlay_active = 1u;
    last_text[0] = '\0';
  }

  if ((now - phase_start_ms) >= duration_ms) {
    active = 0u;
    phase_start_ms = now;
    vnd_lcd_role_overlay_active = 0u;
    LCD_FillRect(0, 0, LCD_W, LCD_H, BLACK);
    if (normal_redraw_needed != NULL) {
      *normal_redraw_needed = 1u;
    }
    return 0u;
  }

  {
    char text[4];
    lcd_role_overlay_make_text(text);
    if ((last_text[0] != text[0]) ||
        (last_text[1] != text[1]) ||
        (last_text[2] != text[2])) {
      lcd_draw_role_overlay_text(text, current_color);
      memcpy(last_text, text, sizeof(last_text));
    }
  }

  return 1u;
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
    /* Loss of SYNC is a link state, not a role change. Keep the RPI-assigned
       MASTER/SLAVE/OFF role on LCD; a slave without sync remains Sxx/S-- and
       must never be presented as a second master. */
    display_mode = vnd_sync_mode_public;
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
    display_value = (uint8_t)(rs485_local_node_id & 0x1Fu);
  } else if (display_mode == VND_SYNC_MODE_SLAVE) {
    display_value = (uint8_t)(rs485_local_node_id & 0x1Fu);
  }

  if (sync_signal_alive) {
    sync_ok_visual = vnd_sync_ok_public;
    if (display_mode == VND_SYNC_MODE_SLAVE) {
      sync_ok_visual = (rs485_sync_phase_relation == RS485_SYNC_RELATION_IN_PHASE) ? 1u : 0u;
    }
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
  if ((rs485_node_local_id_conflict() != 0u) ||
      (rs485_multiple_master_detected() != 0u)) {
    color = RED;
    sync_color_locked = 0u;
  }

  memset(&cached, 0, sizeof(cached));
  cached.raw_mode = vnd_sync_mode_public;
  cached.display_mode = display_mode;
  cached.display_value = display_value;
  cached.slave_count = rs485_slave_count_estimate;
  cached.node_id = (uint8_t)(rs485_local_node_id & 0x1Fu);
  cached.node_id_assigned = rs485_local_node_id_assigned;
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
    uint32_t now = HAL_GetTick();
    static uint32_t last_star_toggle_ms = 0;
    static uint8_t star_on = 0;
    static uint8_t star_prev = 0xFF;
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
    static uint16_t prev_line0_fg = 0, prev_line0_bg = 0;
    static uint16_t prev_line1_fg = 0, prev_line1_bg = 0;
    static uint16_t prev_line2_fg = 0, prev_line2_bg = 0;
    static uint16_t prev_line3_fg = 0, prev_line3_bg = 0;
    static uint16_t prev_line4_fg = 0, prev_line4_bg = 0;
    static uint16_t prev_optic_fg = 0, prev_optic_bg = 0;
    static uint16_t prev_optic_power_fg = 0, prev_optic_power_bg = 0;
    static char prev_optic_line[8] = "";
    static char prev_optic_power_line[8] = "";
    static uint8_t lcd_sync_prev_mode = 0xFF;
    static uint8_t lcd_sync_prev_display_value = 0xFF;
    static uint16_t lcd_sync_prev_color = 0xFFFFu;
    static uint8_t force_full_redraw = 1u;
    uint8_t overlay_redraw_needed = 0u;

    if (lcd_role_overlay_service(now, &overlay_redraw_needed) != 0u) {
      PROG('u');
      return;
    }

    if (overlay_redraw_needed != 0u) {
      force_full_redraw = 1u;
    }

    if (force_full_redraw != 0u) {
      LCD_FillRect(0, 0, LCD_W, LCD_H, BLACK);
      memset(prev_line0, 0, sizeof(prev_line0));
      memset(prev_line1, 0, sizeof(prev_line1));
      memset(prev_line2, 0, sizeof(prev_line2));
      memset(prev_line3, 0, sizeof(prev_line3));
      memset(prev_line4, 0, sizeof(prev_line4));
      memset(prev_optic_line, 0, sizeof(prev_optic_line));
      memset(prev_optic_power_line, 0, sizeof(prev_optic_power_line));
      prev_line0_fg = prev_line0_bg = 0xFFFFu;
      prev_line1_fg = prev_line1_bg = 0xFFFFu;
      prev_line2_fg = prev_line2_bg = 0xFFFFu;
      prev_line3_fg = prev_line3_bg = 0xFFFFu;
      prev_line4_fg = prev_line4_bg = 0xFFFFu;
      prev_optic_fg = prev_optic_bg = 0xFFFFu;
      prev_optic_power_fg = prev_optic_power_bg = 0xFFFFu;
      dc_bar_prev_len = 0xFFFFu;
      dc_bar_prev_color = 0xFFFFu;
      lcd_sync_prev_mode = 0xFFu;
      lcd_sync_prev_display_value = 0xFFu;
      lcd_sync_prev_color = 0xFFFFu;
      star_prev = 0xFFu;
      last_star_toggle_ms = now;
      star_on = 0u;
      prev_tx_bytes = vnd_get_total_tx_bytes();
      prev_tx_samples = vnd_get_total_tx_samples();
      prev_rate_calc_ms = now;
      last_rate_bps = 0u;
      last_rate_sps = 0u;
      force_full_redraw = 0u;
    }

    /* Heartbeat основного цикла: '*' мигает ~1 Гц в правом конце первой строки. */
    {
      uint32_t now_ms = now;
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
    
    /* Первая строка (y=0): USB статус */
    lcd_print_padded_if_changed(0,0,text0, prev_line0, sizeof(prev_line0), 7, 16, color0, BLACK, &prev_line0_fg, &prev_line0_bg);

    /* Индикатор SYNC: постоянный node_id и сохранённая локальная роль. */
    {
      vnd_lcd_sync_snapshot_t sync_snapshot;
      char count_buf[3] = "  ";
      vnd_get_lcd_sync_snapshot(&sync_snapshot);

      if (((sync_snapshot.display_mode == VND_SYNC_MODE_SLAVE) ||
           (sync_snapshot.display_mode == VND_SYNC_MODE_MASTER)) &&
          (sync_snapshot.node_id_assigned == 0u)) {
        count_buf[0] = '-';
        count_buf[1] = '-';
        count_buf[2] = '\0';
      } else if (sync_snapshot.display_mode != VND_SYNC_MODE_OFF) {
        count_buf[0] = (char)('0' + ((sync_snapshot.display_value / 10u) % 10u));
        count_buf[1] = (char)('0' + (sync_snapshot.display_value % 10u));
        count_buf[2] = '\0';
      }

      if(lcd_sync_prev_mode != sync_snapshot.display_mode ||
         lcd_sync_prev_display_value != sync_snapshot.display_value ||
         lcd_sync_prev_color != sync_snapshot.display_rgb565){
        LCD_ShowString_Size(132, 0, count_buf, 16, sync_snapshot.display_rgb565, BLACK);
        char buf[2] = {(char)sync_snapshot.display_char, 0};
        LCD_ShowString_Size(124, 0, buf, 16, sync_snapshot.display_rgb565, BLACK);
        lcd_sync_prev_mode = sync_snapshot.display_mode;
        lcd_sync_prev_display_value = sync_snapshot.display_value;
        lcd_sync_prev_color = sync_snapshot.display_rgb565;
      }
    }

  /* Строка 1 (y=14): частота TX200/фазового маркера PA1/PA2/PC7 */
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
    /* LCD shows the canonical held optical-sensor signal, not raw PD0. */
    char rate_buf[16];
    uint8_t optic_active = optic_sensor_get_state();
    snprintf(rate_buf, sizeof(rate_buf), "OPT:%u", (unsigned)optic_active);
  /* Очистка legacy VID/PID убрана */
    /* Используем ширину 12 символов для гарантированного затирания хвоста */
    lcd_print_padded_if_changed(0,28, rate_buf, prev_line2, sizeof(prev_line2), 12, 16, optic_active ? GREEN : WHITE, BLACK, &prev_line2_fg, &prev_line2_bg);
  } else {
  /* Очистка legacy VID/PID убрана */
    {
      char optic_buf[16];
      uint8_t optic_active = optic_sensor_get_state();
      snprintf(optic_buf, sizeof(optic_buf), "OPT:%u", (unsigned)optic_active);
      lcd_print_padded_if_changed(0,28, optic_buf, prev_line2, sizeof(prev_line2), 12, 16, optic_active ? GREEN : WHITE, BLACK, &prev_line2_fg, &prev_line2_bg);
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
      - Если RPI установил скорость 0 — полоса становится синей и останавливается.
      Важно: используем только 1px высоту, чтобы не мешать тексту. */
  {
    const uint16_t y = 79;
    uint16_t color = BLACK;
    uint16_t filled = 0;

    uint8_t freeze = (uint8_t)((vnd_dc_speed_settle_ms == 0u) ? 1u : 0u);

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
      if(dc_bar_prev_color != color){
        LCD_FillRect(0, y, LCD_W, 1, BLACK);
        if(filled > 0u){
          LCD_FillRect(0, y, filled, 1, color);
        }
      } else if(filled > dc_bar_prev_len){
        LCD_FillRect(dc_bar_prev_len, y, (uint16_t)(filled - dc_bar_prev_len), 1, color);
      } else if(filled < dc_bar_prev_len){
        LCD_FillRect(filled, y, (uint16_t)(dc_bar_prev_len - filled), 1, BLACK);
      } else if(filled > 0u){
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
  /* USER CODE BEGIN 6 */
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
  /* USER CODE END 6 */
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

