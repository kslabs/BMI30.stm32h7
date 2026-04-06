#include "ws2812_spi.h"
#include "usb_vendor_app.h"

#include <string.h>

extern SPI_HandleTypeDef hspi3;

#ifndef WS2812_SPI_USE_DMA
#define WS2812_SPI_USE_DMA 1
#endif

#ifndef WS2812_SPI_IRQ_BLOCK_TEST
#define WS2812_SPI_IRQ_BLOCK_TEST 0
#endif

#ifndef WS2812_COLOR_ORDER_RGB
#define WS2812_COLOR_ORDER_RGB 0
#endif

#ifndef WS2812_SPI_DIAG_PULSES
#define WS2812_SPI_DIAG_PULSES 0
#endif

enum {
  WS2812_PREFIX_BYTES = 3u,
  WS2812_RESET_BYTES = 64u,
  WS2812_BYTES_PER_COLOR = 4u,
  WS2812_BYTES_PER_LED = 12u,
  WS2812_TX_BUF_SIZE = WS2812_PREFIX_BYTES + (WS2812_LED_COUNT * WS2812_BYTES_PER_LED) + WS2812_RESET_BYTES,
  WS2812_FRAME_RATE_HZ = 400u,
  WS2812_IDLE_STEP_FRAMES = 20u,
  WS2812_STREAM_STEP_FRAMES = 10u,
  WS2812_SYNC_STEP_FRAMES = 20u,
  WS2812_UART_STEP_FRAMES = 8u,
  WS2812_TUNE_STEP_FRAMES = 20u,
  WS2812_ALERT_STEP_FRAMES = 20u,
  WS2812_DRIP_STEP_FRAMES = 20u,
  WS2812_TEST_STEP_FRAMES = 200u,
  WS2812_SPI_CODE_0 = 0x8u, /* 1000 */
  WS2812_SPI_CODE_1 = 0xEu  /* 1110 */
};

typedef struct {
  uint8_t onboard_r;
  uint8_t onboard_g;
  uint8_t onboard_b;
  uint8_t strip_r;
  uint8_t strip_g;
  uint8_t strip_b;
  uint16_t hold_ms;
} ws2812_frame_def_t;

typedef struct {
  const ws2812_frame_def_t *frames;
  uint8_t frame_count;
} ws2812_pattern_def_t;

static uint8_t s_pixels[WS2812_LED_COUNT][3];
static uint8_t s_tx_buf[WS2812_TX_BUF_SIZE]
  __attribute__((section(".ram_d2"), aligned(32)));
static volatile uint8_t s_busy = 0u;
static volatile ws2812_pattern_t s_active_pattern = WS2812_PATTERN_OFF;
static volatile ws2812_pattern_t s_requested_pattern = WS2812_PATTERN_OFF;
static volatile uint8_t s_pattern_force_send = 1u;
static volatile uint32_t s_frame_period_cycles = 1u;
static volatile uint32_t s_last_frame_cycles = 0u;
static volatile uint16_t s_pattern_anim_step = 0u;
static volatile uint16_t s_pattern_frame_repeat = 0u;
static volatile uint32_t s_pattern_frame_counter = 0u;
static volatile ws2812_pattern_t s_override_pattern = WS2812_PATTERN_OFF;
static volatile uint32_t s_override_until_ms = 0u;

static const ws2812_frame_def_t s_pattern_off[] = {
  { 0u, 0u, 0u, 0u, 0u, 0u, 200u }
};

static const ws2812_frame_def_t s_pattern_idle_breathe[] = {
  { 0u, 2u, 0u, 0u, 2u, 1u, 90u },
  { 0u, 3u, 0u, 0u, 4u, 1u, 90u },
  { 0u, 5u, 0u, 0u, 7u, 2u, 90u },
  { 0u, 7u, 0u, 0u, 10u, 3u, 90u },
  { 0u, 9u, 0u, 0u, 13u, 4u, 90u },
  { 0u, 12u, 0u, 0u, 18u, 5u, 90u },
  { 0u, 16u, 0u, 0u, 24u, 7u, 90u },
  { 0u, 12u, 0u, 0u, 18u, 5u, 90u },
  { 0u, 9u, 0u, 0u, 13u, 4u, 90u },
  { 0u, 7u, 0u, 0u, 10u, 3u, 90u },
  { 0u, 5u, 0u, 0u, 7u, 2u, 90u },
  { 0u, 3u, 0u, 0u, 4u, 1u, 90u }
};

static const ws2812_frame_def_t s_pattern_streaming[] = {
  { 0u, 12u, 0u, 0u, 28u, 0u, 120u },
  { 0u, 18u, 0u, 0u, 10u, 0u, 120u }
};

static const ws2812_frame_def_t s_pattern_sync_pulse[] = {
  { 0u, 0u, 0u, 0u, 0u, 0u, 70u },
  { 0u, 0u, 10u, 0u, 0u, 28u, 70u },
  { 0u, 0u, 4u, 0u, 0u, 10u, 70u },
  { 0u, 0u, 0u, 0u, 0u, 0u, 140u }
};

static const ws2812_frame_def_t s_pattern_uart_rx[] = {
  { 18u, 18u, 18u, 0u, 0u, 0u, 50u },
  { 0u, 0u, 0u, 6u, 6u, 6u, 50u }
};

static const ws2812_frame_def_t s_pattern_tune[] = {
  { 20u, 8u, 0u, 20u, 8u, 0u, 100u },
  { 10u, 4u, 0u, 6u, 2u, 0u, 100u }
};

static const ws2812_frame_def_t s_pattern_recovery[] = {
  { 20u, 0u, 0u, 32u, 0u, 0u, 160u },
  { 0u, 0u, 0u, 0u, 0u, 0u, 160u }
};

static const ws2812_frame_def_t s_pattern_hard_reset[] = {
  { 16u, 0u, 16u, 32u, 0u, 24u, 100u },
  { 0u, 0u, 0u, 0u, 0u, 0u, 80u }
};

static const ws2812_frame_def_t s_pattern_test_scope_rgb[] = {
  { 32u, 0u, 0u, 32u, 0u, 0u, 1200u },
  { 0u, 32u, 0u, 0u, 32u, 0u, 1200u },
  { 0u, 0u, 32u, 0u, 0u, 32u, 1200u }
};

static const ws2812_frame_def_t s_pattern_test_blue[] = {
  { 0u, 0u, 8u, 0u, 0u, 24u, 20u }
};

static const ws2812_frame_def_t s_pattern_test_color_cycle[] = {
  { 0u, 0u, 0u, 0u, 0u, 0u, 1200u },
  { 64u, 0u, 0u, 255u, 0u, 0u, 1200u },
  { 0u, 0u, 0u, 0u, 0u, 0u, 1200u },
  { 0u, 64u, 0u, 0u, 255u, 0u, 1200u },
  { 0u, 0u, 0u, 0u, 0u, 0u, 1200u },
  { 0u, 0u, 64u, 0u, 0u, 255u, 1200u }
};

static const ws2812_pattern_def_t s_pattern_defs[WS2812_PATTERN_COUNT] = {
  { s_pattern_off, (uint8_t)(sizeof(s_pattern_off) / sizeof(s_pattern_off[0])) },
  { s_pattern_idle_breathe, (uint8_t)(sizeof(s_pattern_idle_breathe) / sizeof(s_pattern_idle_breathe[0])) },
  { s_pattern_streaming, (uint8_t)(sizeof(s_pattern_streaming) / sizeof(s_pattern_streaming[0])) },
  { s_pattern_sync_pulse, (uint8_t)(sizeof(s_pattern_sync_pulse) / sizeof(s_pattern_sync_pulse[0])) },
  { s_pattern_uart_rx, (uint8_t)(sizeof(s_pattern_uart_rx) / sizeof(s_pattern_uart_rx[0])) },
  { s_pattern_tune, (uint8_t)(sizeof(s_pattern_tune) / sizeof(s_pattern_tune[0])) },
  { s_pattern_recovery, (uint8_t)(sizeof(s_pattern_recovery) / sizeof(s_pattern_recovery[0])) },
  { s_pattern_hard_reset, (uint8_t)(sizeof(s_pattern_hard_reset) / sizeof(s_pattern_hard_reset[0])) },
  { s_pattern_off, (uint8_t)(sizeof(s_pattern_off) / sizeof(s_pattern_off[0])) },
  { s_pattern_off, (uint8_t)(sizeof(s_pattern_off) / sizeof(s_pattern_off[0])) },
  { s_pattern_off, (uint8_t)(sizeof(s_pattern_off) / sizeof(s_pattern_off[0])) },
  { s_pattern_off, (uint8_t)(sizeof(s_pattern_off) / sizeof(s_pattern_off[0])) },
  { s_pattern_off, (uint8_t)(sizeof(s_pattern_off) / sizeof(s_pattern_off[0])) },
  { s_pattern_off, (uint8_t)(sizeof(s_pattern_off) / sizeof(s_pattern_off[0])) },
  { s_pattern_test_scope_rgb, (uint8_t)(sizeof(s_pattern_test_scope_rgb) / sizeof(s_pattern_test_scope_rgb[0])) },
  { s_pattern_test_blue, (uint8_t)(sizeof(s_pattern_test_blue) / sizeof(s_pattern_test_blue[0])) },
  { s_pattern_test_color_cycle, (uint8_t)(sizeof(s_pattern_test_color_cycle) / sizeof(s_pattern_test_color_cycle[0])) }
};

static void ws2812_scope_pin_init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOA_CLK_ENABLE();
  GPIO_InitStruct.Pin = GPIO_PIN_3;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_3, GPIO_PIN_RESET);
}

static void ws2812_scope_pulse_count(uint32_t pulse_count)
{
#if WS2812_SPI_DIAG_PULSES
  uint32_t pulse = 0u;

  ws2812_scope_pin_init();
  for (pulse = 0u; pulse < pulse_count; ++pulse) {
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_3, GPIO_PIN_SET);
    for (volatile uint32_t i = 0u; i < 48u; ++i) {
      __NOP();
    }
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_3, GPIO_PIN_RESET);
    for (volatile uint32_t i = 0u; i < 48u; ++i) {
      __NOP();
    }
  }
#else
  (void)pulse_count;
#endif
}

static void ws2812_scope_sync_begin(void)
{
  ws2812_scope_pulse_count(1u);
}

static HAL_StatusTypeDef ws2812_wait_spi_flush(uint32_t timeout_ms)
{
  uint32_t start = HAL_GetTick();

  while (__HAL_SPI_GET_FLAG(&hspi3, SPI_FLAG_TXC) == RESET) {
    if ((HAL_GetTick() - start) >= timeout_ms) {
      return HAL_TIMEOUT;
    }
  }

  while (__HAL_SPI_GET_FLAG(&hspi3, SPI_FLAG_EOT) == RESET) {
    if ((HAL_GetTick() - start) >= timeout_ms) {
      return HAL_TIMEOUT;
    }
  }

  __HAL_SPI_CLEAR_EOTFLAG(&hspi3);
  return HAL_OK;
}

static void ws2812_pin_spi_mode(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOB_CLK_ENABLE();
  GPIO_InitStruct.Pin = GPIO_PIN_2;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  GPIO_InitStruct.Alternate = GPIO_AF7_SPI3;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
}

static void ws2812_pin_gpio_low_mode(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOB_CLK_ENABLE();
  GPIO_InitStruct.Pin = GPIO_PIN_2;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_2, GPIO_PIN_RESET);
}

static void ws2812_encode_byte(uint8_t src, uint8_t *dst)
{
  uint32_t packed = 0u;
  uint8_t bit = 0u;

  for (bit = 0u; bit < 8u; ++bit) {
    uint32_t code = ((src & 0x80u) != 0u) ? WS2812_SPI_CODE_1 : WS2812_SPI_CODE_0;

    packed = (packed << 4) | code;
    src <<= 1;
  }

  dst[0] = (uint8_t)(packed >> 24);
  dst[1] = (uint8_t)(packed >> 16);
  dst[2] = (uint8_t)(packed >> 8);
  dst[3] = (uint8_t)packed;
}

static void ws2812_encode_led_rgb(uint8_t *dst, uint8_t r, uint8_t g, uint8_t b)
{
#if WS2812_COLOR_ORDER_RGB
  ws2812_encode_byte(r, dst + (0u * WS2812_BYTES_PER_COLOR)); /* R */
  ws2812_encode_byte(g, dst + (1u * WS2812_BYTES_PER_COLOR)); /* G */
  ws2812_encode_byte(b, dst + (2u * WS2812_BYTES_PER_COLOR)); /* B */
#else
  ws2812_encode_byte(g, dst + (0u * WS2812_BYTES_PER_COLOR)); /* G */
  ws2812_encode_byte(r, dst + (1u * WS2812_BYTES_PER_COLOR)); /* R */
  ws2812_encode_byte(b, dst + (2u * WS2812_BYTES_PER_COLOR)); /* B */
#endif
}

static void ws2812_fill_encoded_buffer(uint8_t *buf,
                                       uint8_t onboard_r, uint8_t onboard_g, uint8_t onboard_b,
                                       uint8_t strip_r, uint8_t strip_g, uint8_t strip_b)
{
  uint32_t led = 0u;
  uint8_t *dst = buf + WS2812_PREFIX_BYTES;

  memset(buf, 0, WS2812_PREFIX_BYTES);

  for (led = 0u; led < WS2812_LED_COUNT; ++led) {
    if (led < WS2812_ONBOARD_LED_COUNT) {
      ws2812_encode_led_rgb(dst, onboard_r, onboard_g, onboard_b);
    } else {
      ws2812_encode_led_rgb(dst, strip_r, strip_g, strip_b);
    }
    dst += WS2812_BYTES_PER_LED;
  }

  memset(dst, 0, WS2812_RESET_BYTES);
}

static void ws2812_build_tx_buffer(void)
{
  uint32_t led = 0u;
  uint8_t *dst = s_tx_buf + WS2812_PREFIX_BYTES;

  memset(s_tx_buf, 0, WS2812_PREFIX_BYTES);
  for (led = 0u; led < WS2812_LED_COUNT; ++led) {
#if WS2812_COLOR_ORDER_RGB
    ws2812_encode_byte(s_pixels[led][0], dst + (0u * WS2812_BYTES_PER_COLOR)); /* R */
    ws2812_encode_byte(s_pixels[led][1], dst + (1u * WS2812_BYTES_PER_COLOR)); /* G */
    ws2812_encode_byte(s_pixels[led][2], dst + (2u * WS2812_BYTES_PER_COLOR)); /* B */
#else
    ws2812_encode_byte(s_pixels[led][1], dst + (0u * WS2812_BYTES_PER_COLOR)); /* G */
    ws2812_encode_byte(s_pixels[led][0], dst + (1u * WS2812_BYTES_PER_COLOR)); /* R */
    ws2812_encode_byte(s_pixels[led][2], dst + (2u * WS2812_BYTES_PER_COLOR)); /* B */
#endif
    dst += WS2812_BYTES_PER_LED;
  }
  memset(dst, 0, WS2812_RESET_BYTES);
}

static void ws2812_clean_dcache_region(void *ptr, uint32_t size)
{
#if defined(SCB_CleanDCache_by_Addr)
  uintptr_t start = ((uintptr_t)ptr) & ~(uintptr_t)31u;
  uintptr_t end = (((uintptr_t)ptr + (uintptr_t)size) + 31u) & ~(uintptr_t)31u;
  SCB_CleanDCache_by_Addr((uint32_t *)start, (int32_t)(end - start));
#else
  (void)ptr;
  (void)size;
#endif
}

static void ws2812_build_pattern_buffers(void)
{
  /* Precomputed full DMA frames for every pattern do not fit RAM_D2 once the strip length is real.
     We keep the pattern tables in flash and encode only the current frame into the DMA buffer. */
}

static void ws2812_pixels_clear_all(void)
{
  memset(s_pixels, 0, sizeof(s_pixels));
}

static void ws2812_pixels_set_onboard_rgb(uint8_t r, uint8_t g, uint8_t b)
{
  if (WS2812_ONBOARD_LED_COUNT == 0u) {
    return;
  }

  s_pixels[0][0] = r;
  s_pixels[0][1] = g;
  s_pixels[0][2] = b;
}

static void ws2812_pixels_fill_strip_rgb(uint8_t r, uint8_t g, uint8_t b)
{
  uint32_t led = 0u;

  for (led = WS2812_ONBOARD_LED_COUNT; led < WS2812_LED_COUNT; ++led) {
    s_pixels[led][0] = r;
    s_pixels[led][1] = g;
    s_pixels[led][2] = b;
  }
}

static void ws2812_pixels_set_strip_led_rgb(uint16_t strip_index, uint8_t r, uint8_t g, uint8_t b)
{
  uint16_t led_index = (uint16_t)(WS2812_ONBOARD_LED_COUNT + strip_index);

  if (led_index >= WS2812_LED_COUNT) {
    return;
  }

  s_pixels[led_index][0] = r;
  s_pixels[led_index][1] = g;
  s_pixels[led_index][2] = b;
}

static uint16_t ws2812_pattern_step_frames(ws2812_pattern_t pattern)
{
  switch (pattern) {
    case WS2812_PATTERN_STREAMING:
      return WS2812_STREAM_STEP_FRAMES;
    case WS2812_PATTERN_SYNC_PULSE:
      return WS2812_SYNC_STEP_FRAMES;
    case WS2812_PATTERN_UART_RX:
      return WS2812_UART_STEP_FRAMES;
    case WS2812_PATTERN_TUNE:
      return WS2812_TUNE_STEP_FRAMES;
    case WS2812_PATTERN_RECOVERY:
    case WS2812_PATTERN_HARD_RESET:
      return WS2812_ALERT_STEP_FRAMES;
    case WS2812_PATTERN_EVENT_B_UP:
    case WS2812_PATTERN_EVENT_A_DOWN:
    case WS2812_PATTERN_EVENT_BOTH_ALT:
    case WS2812_PATTERN_EVENT_SPLIT_IN:
    case WS2812_PATTERN_EVENT_SPLIT_OUT:
      return 40u;
    case WS2812_PATTERN_TEST_DRIP:
      return WS2812_DRIP_STEP_FRAMES;
    case WS2812_PATTERN_IDLE_BREATHE:
    case WS2812_PATTERN_OFF:
    case WS2812_PATTERN_TEST_SCOPE_RGB:
    case WS2812_PATTERN_TEST_BLUE:
    case WS2812_PATTERN_TEST_COLOR_CYCLE:
      return WS2812_TEST_STEP_FRAMES;
    default:
      return WS2812_IDLE_STEP_FRAMES;
  }
}

static void ws2812_render_strip_comet(uint16_t head, uint8_t r, uint8_t g, uint8_t b)
{
  if (WS2812_STRIP_LED_COUNT == 0u) {
    return;
  }

  ws2812_pixels_set_strip_led_rgb((uint16_t)(head % WS2812_STRIP_LED_COUNT), r, g, b);
  ws2812_pixels_set_strip_led_rgb((uint16_t)((head + WS2812_STRIP_LED_COUNT - 1u) % WS2812_STRIP_LED_COUNT),
                                  (uint8_t)(r / 3u), (uint8_t)(g / 3u), (uint8_t)(b / 3u));
  ws2812_pixels_set_strip_led_rgb((uint16_t)((head + WS2812_STRIP_LED_COUNT - 2u) % WS2812_STRIP_LED_COUNT),
                                  (uint8_t)(r / 8u), (uint8_t)(g / 8u), (uint8_t)(b / 8u));
}

static void ws2812_render_strip_moving_blocks(uint16_t anim_step, uint8_t towards_high)
{
  uint16_t pos = 0u;
  uint16_t phase_offset = (uint16_t)(anim_step % 8u);

  for (pos = 0u; pos < WS2812_STRIP_LED_COUNT; ++pos) {
    uint16_t phase = towards_high != 0u
      ? (uint16_t)((pos + 8u - phase_offset) % 8u)
      : (uint16_t)((pos + phase_offset) % 8u);

    if (phase < 4u) {
      ws2812_pixels_set_strip_led_rgb(pos, 255u, 0u, 0u);
    }
  }
}

static void ws2812_render_strip_moving_blocks_alternating(uint16_t anim_step)
{
  uint16_t cycle_step = (uint16_t)(anim_step % 16u);
  uint8_t towards_high = (cycle_step >= 8u) ? 1u : 0u;
  uint16_t local_step = (uint16_t)(cycle_step % 8u);

  ws2812_render_strip_moving_blocks(local_step, towards_high);
}

static void ws2812_render_strip_split_moving_blocks(uint16_t anim_step,
                                                    uint8_t upper_towards_high,
                                                    uint8_t lower_towards_high)
{
  const uint16_t half_len = (uint16_t)(WS2812_STRIP_LED_COUNT / 2u);
  const uint16_t block_len = 4u;
  uint16_t travel = 0u;
  uint16_t step = 0u;
  uint16_t lower_start = 0u;
  uint16_t upper_start = 0u;
  uint16_t i = 0u;

  if (half_len < block_len) {
    ws2812_pixels_fill_strip_rgb(255u, 0u, 0u);
    return;
  }

  travel = (uint16_t)(half_len - block_len + 1u);
  if (travel == 0u) {
    return;
  }

  step = (uint16_t)(anim_step % travel);
  lower_start = (lower_towards_high != 0u) ? step : (uint16_t)(travel - 1u - step);
  upper_start = (upper_towards_high != 0u)
    ? (uint16_t)(half_len + step)
    : (uint16_t)(half_len + (travel - 1u - step));

  for (i = 0u; i < block_len; ++i) {
    ws2812_pixels_set_strip_led_rgb((uint16_t)(lower_start + i), 255u, 0u, 0u);
    ws2812_pixels_set_strip_led_rgb((uint16_t)(upper_start + i), 255u, 0u, 0u);
  }
}

static uint8_t ws2812_drip_tail_visible(uint16_t anim_step)
{
  uint32_t value = (((uint32_t)anim_step + 1u) * 1103515245u) + 12345u;
  return (uint8_t)((value >> 30) & 0x1u);
}

static void ws2812_render_strip_drip(uint16_t anim_step)
{
  static const uint8_t drip_rgb[4][3] = {
    { 8u, 46u, 72u },
    { 4u, 28u, 42u },
    { 2u, 14u, 22u },
    { 1u, 6u, 10u }
  };
  const uint16_t tail_len = 4u;
  const uint16_t gap_len = 5u;
  const uint16_t cycle_len = (uint16_t)(WS2812_STRIP_LED_COUNT + tail_len + gap_len);
  uint16_t head = 0u;
  uint16_t tail_idx = 0u;

  if ((WS2812_STRIP_LED_COUNT == 0u) || (cycle_len == 0u)) {
    return;
  }

  head = (uint16_t)(anim_step % cycle_len);
  for (tail_idx = 0u; tail_idx < tail_len; ++tail_idx) {
    uint16_t pos = 0u;

    if ((tail_idx == (tail_len - 1u)) && (ws2812_drip_tail_visible(anim_step) == 0u)) {
      continue;
    }
    if (head < tail_idx) {
      continue;
    }

    pos = (uint16_t)(head - tail_idx);
    if (pos >= WS2812_STRIP_LED_COUNT) {
      continue;
    }

    ws2812_pixels_set_strip_led_rgb(pos,
                                    drip_rgb[tail_idx][0],
                                    drip_rgb[tail_idx][1],
                                    drip_rgb[tail_idx][2]);
  }
}

static void ws2812_apply_onboard_status_overlay(void)
{
  extern volatile uint8_t vnd_sync_mode_public;
  extern uint8_t ws2812_onboard_slave_blue_active(uint8_t dark_phase, uint32_t now_ms);
  extern volatile uint8_t need_recovery;
  extern volatile uint8_t need_hard_reset;
  enum {
    WS2812_STATUS_TX_ACTIVE_MS = 500u,
    WS2812_STATUS_HOST_ACK_MS = 1500u,
    WS2812_STATUS_GREEN_MAX = 96u,
    WS2812_STATUS_RED_MAX = 96u,
    WS2812_STATUS_BLUE_MAX = 96u,
    WS2812_STATUS_BLUE_PULSE = 120u
  };
  const uint32_t breathe_phase = s_pattern_frame_counter % 800u;
  uint32_t ramp = 0u;
  uint8_t breathe_level = 0u;
  uint8_t dark_phase = 0u;
  uint32_t now_ms = HAL_GetTick();
  uint32_t last_frame_txcplt_ms = vnd_get_last_frame_txcplt_ms();
  uint32_t last_host_rx_ack_ms = vnd_get_last_host_rx_ack_ms();
  uint32_t last_error = vnd_get_last_error();
  uint8_t red = 0u;
  uint8_t green = 0u;
  uint8_t blue = 0u;

  if (breathe_phase < 400u) {
    ramp = (breathe_phase * 255u) / 399u;
  } else {
    ramp = ((799u - breathe_phase) * 255u) / 399u;
  }
  breathe_level = (uint8_t)((ramp * ramp * WS2812_STATUS_GREEN_MAX) / 65025u);

  if (breathe_level <= 5u) {
    dark_phase = 1u;
  }

  if ((need_recovery != 0u) || (need_hard_reset != 0u) || (last_error != 0u)) {
    red = (uint8_t)((ramp * ramp * WS2812_STATUS_RED_MAX) / 65025u);
  } else if ((last_frame_txcplt_ms != 0u) &&
             (last_host_rx_ack_ms != 0u) &&
             ((uint32_t)(now_ms - last_host_rx_ack_ms) <= WS2812_STATUS_HOST_ACK_MS) &&
             ((uint32_t)(now_ms - last_frame_txcplt_ms) <= WS2812_STATUS_TX_ACTIVE_MS)) {
    green = breathe_level;
  } else {
    blue = (uint8_t)((ramp * ramp * WS2812_STATUS_BLUE_MAX) / 65025u);
    if ((vnd_sync_mode_public == VND_SYNC_MODE_SLAVE) &&
        (ws2812_onboard_slave_blue_active(dark_phase, now_ms) != 0u) &&
        (blue < WS2812_STATUS_BLUE_PULSE)) {
      blue = WS2812_STATUS_BLUE_PULSE;
    }
  }

  ws2812_pixels_set_onboard_rgb(red, green, blue);
}

static void ws2812_render_pattern(ws2812_pattern_t pattern, uint16_t anim_step)
{
  uint16_t pos = 0u;
  uint16_t phase = 0u;

  ws2812_pixels_clear_all();

  switch (pattern) {
    case WS2812_PATTERN_OFF:
      break;

    case WS2812_PATTERN_IDLE_BREATHE:
      if (WS2812_STRIP_LED_COUNT != 0u) {
        pos = (uint16_t)(anim_step % WS2812_STRIP_LED_COUNT);
        ws2812_render_strip_comet(pos, 0u, 20u, 8u);
      }
      break;

    case WS2812_PATTERN_STREAMING:
      if (WS2812_STRIP_LED_COUNT != 0u) {
        pos = (uint16_t)(anim_step % WS2812_STRIP_LED_COUNT);
        ws2812_render_strip_comet(pos, 0u, 48u, 0u);
        ws2812_render_strip_comet((uint16_t)((pos + (WS2812_STRIP_LED_COUNT / 2u)) % WS2812_STRIP_LED_COUNT), 0u, 12u, 0u);
      }
      break;

    case WS2812_PATTERN_SYNC_PULSE:
      phase = (uint16_t)(anim_step % 6u);
      if (WS2812_STRIP_LED_COUNT != 0u) {
        uint16_t center = (uint16_t)(WS2812_STRIP_LED_COUNT / 2u);
        if (phase == 0u) {
          ws2812_pixels_fill_strip_rgb(0u, 0u, 4u);
        } else {
          uint16_t radius = (uint16_t)(phase - 1u);
          if (center > radius) {
            ws2812_pixels_set_strip_led_rgb((uint16_t)(center - 1u - radius), 0u, 0u, 32u);
          }
          ws2812_pixels_set_strip_led_rgb((uint16_t)((center + radius) % WS2812_STRIP_LED_COUNT), 0u, 0u, 32u);
        }
      }
      break;

    case WS2812_PATTERN_UART_RX:
      if ((anim_step & 1u) == 0u) {
        ws2812_pixels_fill_strip_rgb(8u, 8u, 8u);
      }
      break;

    case WS2812_PATTERN_TUNE:
      if (WS2812_STRIP_LED_COUNT != 0u) {
        pos = (uint16_t)(anim_step % WS2812_STRIP_LED_COUNT);
        ws2812_render_strip_comet(pos, 28u, 10u, 0u);
      }
      break;

    case WS2812_PATTERN_RECOVERY:
      if ((anim_step & 1u) == 0u) {
        ws2812_pixels_fill_strip_rgb(28u, 0u, 0u);
      }
      break;

    case WS2812_PATTERN_HARD_RESET:
      if ((anim_step & 1u) == 0u) {
        ws2812_pixels_fill_strip_rgb(28u, 0u, 20u);
      }
      break;

    case WS2812_PATTERN_EVENT_B_UP:
      ws2812_render_strip_moving_blocks(anim_step, 1u);
      break;

    case WS2812_PATTERN_EVENT_A_DOWN:
      ws2812_render_strip_moving_blocks(anim_step, 0u);
      break;

    case WS2812_PATTERN_EVENT_BOTH_ALT:
      ws2812_render_strip_moving_blocks_alternating(anim_step);
      break;

    case WS2812_PATTERN_EVENT_SPLIT_IN:
      ws2812_render_strip_split_moving_blocks(anim_step, 0u, 1u);
      break;

    case WS2812_PATTERN_EVENT_SPLIT_OUT:
      ws2812_render_strip_split_moving_blocks(anim_step, 1u, 0u);
      break;

    case WS2812_PATTERN_TEST_DRIP:
      ws2812_render_strip_drip(anim_step);
      break;

    case WS2812_PATTERN_TEST_SCOPE_RGB:
      phase = (uint16_t)(anim_step % 3u);
      if (phase == 0u) {
        ws2812_pixels_set_onboard_rgb(40u, 0u, 0u);
        ws2812_pixels_fill_strip_rgb(40u, 0u, 0u);
      } else if (phase == 1u) {
        ws2812_pixels_set_onboard_rgb(0u, 40u, 0u);
        ws2812_pixels_fill_strip_rgb(0u, 40u, 0u);
      } else {
        ws2812_pixels_set_onboard_rgb(0u, 0u, 40u);
        ws2812_pixels_fill_strip_rgb(0u, 0u, 40u);
      }
      break;

    case WS2812_PATTERN_TEST_BLUE:
      for (pos = 0u; pos < WS2812_STRIP_LED_COUNT; ++pos) {
        uint8_t first_half = (uint8_t)(pos < (WS2812_STRIP_LED_COUNT / 2u));
        uint8_t phase = (uint8_t)(anim_step & 1u);

        if ((first_half ^ phase) != 0u) {
          ws2812_pixels_set_strip_led_rgb(pos, 64u, 0u, 0u);
        } else {
          ws2812_pixels_set_strip_led_rgb(pos, 0u, 0u, 64u);
        }
      }
      break;

    case WS2812_PATTERN_TEST_COLOR_CYCLE:
      phase = (uint16_t)(anim_step % 3u);
      if (phase == 0u) {
        ws2812_pixels_set_onboard_rgb(32u, 0u, 0u);
        ws2812_pixels_fill_strip_rgb(64u, 0u, 0u);
      } else if (phase == 1u) {
        ws2812_pixels_set_onboard_rgb(0u, 32u, 0u);
        ws2812_pixels_fill_strip_rgb(0u, 64u, 0u);
      } else {
        ws2812_pixels_set_onboard_rgb(0u, 0u, 32u);
        ws2812_pixels_fill_strip_rgb(0u, 0u, 64u);
      }
      break;

    default:
      break;
  }

  ws2812_apply_onboard_status_overlay();
}

static uint8_t ws2812_start_transfer(uint8_t *tx_buf, uint16_t tx_len)
{
  HAL_StatusTypeDef st;

  if ((hspi3.Instance != SPI3) || (s_busy != 0u)) {
    return 0u;
  }

  s_busy = 1u;
  ws2812_scope_sync_begin();
  ws2812_pin_spi_mode();
#if WS2812_SPI_USE_DMA
  st = HAL_SPI_Transmit_DMA(&hspi3, tx_buf, tx_len);
  if (st != HAL_OK) {
    ws2812_scope_pulse_count(5u);
    ws2812_pin_gpio_low_mode();
    s_busy = 0u;
    return 0u;
  }
#else
#if WS2812_SPI_IRQ_BLOCK_TEST
  __disable_irq();
#endif
  st = HAL_SPI_Transmit(&hspi3, tx_buf, tx_len, 100u);
  if (st == HAL_OK) {
    st = ws2812_wait_spi_flush(2u);
  }
#if WS2812_SPI_IRQ_BLOCK_TEST
  __enable_irq();
#endif
  ws2812_pin_gpio_low_mode();
  s_busy = 0u;
  if (st != HAL_OK) {
    return 0u;
  }
#endif

  return 1u;
}

void ws2812_spi_init(void)
{
  ws2812_scope_pin_init();
  ws2812_pin_gpio_low_mode();
  ws2812_build_pattern_buffers();
  s_active_pattern = WS2812_PATTERN_OFF;
  s_requested_pattern = WS2812_PATTERN_OFF;
  s_pattern_force_send = 1u;
  s_frame_period_cycles = SystemCoreClock / WS2812_FRAME_RATE_HZ;
  if (s_frame_period_cycles == 0u) {
    s_frame_period_cycles = 1u;
  }
  s_last_frame_cycles = DWT->CYCCNT;
  s_pattern_anim_step = 0u;
  s_pattern_frame_repeat = 0u;
  s_override_pattern = WS2812_PATTERN_OFF;
  s_override_until_ms = 0u;
}

void ws2812_spi_clear(void)
{
  memset(s_pixels, 0, sizeof(s_pixels));
}

void ws2812_spi_fill_rgb(uint8_t r, uint8_t g, uint8_t b)
{
  uint32_t led = 0u;

  for (led = 0u; led < WS2812_LED_COUNT; ++led) {
    s_pixels[led][0] = r;
    s_pixels[led][1] = g;
    s_pixels[led][2] = b;
  }
}

void ws2812_spi_set_rgb(uint16_t index, uint8_t r, uint8_t g, uint8_t b)
{
  if (index >= WS2812_LED_COUNT) {
    return;
  }

  s_pixels[index][0] = r;
  s_pixels[index][1] = g;
  s_pixels[index][2] = b;
}

uint8_t ws2812_spi_show(void)
{
  ws2812_build_tx_buffer();
  ws2812_clean_dcache_region(s_tx_buf, (uint32_t)sizeof(s_tx_buf));
  return ws2812_start_transfer(s_tx_buf, (uint16_t)sizeof(s_tx_buf));
}

uint8_t ws2812_spi_is_busy(void)
{
  return s_busy;
}

void ws2812_spi_set_pattern(ws2812_pattern_t pattern)
{
  if ((uint32_t)pattern >= (uint32_t)WS2812_PATTERN_COUNT) {
    pattern = WS2812_PATTERN_OFF;
  }

  s_requested_pattern = pattern;
}

ws2812_pattern_t ws2812_spi_get_pattern(void)
{
  return s_requested_pattern;
}

void ws2812_spi_trigger_event(ws2812_event_t event, uint16_t duration_ms)
{
  ws2812_pattern_t pattern = WS2812_PATTERN_OFF;

  switch (event) {
    case WS2812_EVENT_CHANNEL_B:
      pattern = WS2812_PATTERN_EVENT_B_UP;
      break;

    case WS2812_EVENT_CHANNEL_A:
      pattern = WS2812_PATTERN_EVENT_A_DOWN;
      break;

    case WS2812_EVENT_CHANNEL_BOTH:
      pattern = WS2812_PATTERN_EVENT_BOTH_ALT;
      break;

    case WS2812_EVENT_SPLIT_IN:
      pattern = WS2812_PATTERN_EVENT_SPLIT_IN;
      break;

    case WS2812_EVENT_SPLIT_OUT:
      pattern = WS2812_PATTERN_EVENT_SPLIT_OUT;
      break;

    case WS2812_EVENT_NONE:
    default:
      pattern = WS2812_PATTERN_OFF;
      break;
  }

  if ((pattern == WS2812_PATTERN_OFF) || (duration_ms == 0u)) {
    s_override_pattern = WS2812_PATTERN_OFF;
    s_override_until_ms = 0u;
    s_pattern_force_send = 1u;
    return;
  }

  if (duration_ms > 10000u) {
    duration_ms = 10000u;
  }

  s_override_pattern = pattern;
  s_override_until_ms = HAL_GetTick() + duration_ms;
  s_pattern_force_send = 1u;
}

void ws2812_spi_service(uint32_t now_ms)
{
  uint16_t step_frames;
  uint32_t now_cycles = DWT->CYCCNT;
  ws2812_pattern_t effective_pattern = s_requested_pattern;

  if (s_busy != 0u) {
    return;
  }

  if ((s_override_pattern != WS2812_PATTERN_OFF) &&
      ((int32_t)(s_override_until_ms - now_ms) > 0)) {
    effective_pattern = s_override_pattern;
  } else if (s_override_pattern != WS2812_PATTERN_OFF) {
    s_override_pattern = WS2812_PATTERN_OFF;
    s_override_until_ms = 0u;
    s_pattern_force_send = 1u;
  }

  if (effective_pattern != s_active_pattern) {
    s_active_pattern = effective_pattern;
    s_pattern_anim_step = 0u;
    s_pattern_frame_repeat = 0u;
    s_pattern_force_send = 1u;
  }

  if ((s_pattern_force_send == 0u) &&
      ((uint32_t)(now_cycles - s_last_frame_cycles) < s_frame_period_cycles)) {
    return;
  }

  ws2812_render_pattern(s_active_pattern, s_pattern_anim_step);
  ws2812_build_tx_buffer();
  ws2812_clean_dcache_region(s_tx_buf, (uint32_t)sizeof(s_tx_buf));
  if (ws2812_start_transfer(s_tx_buf, (uint16_t)WS2812_TX_BUF_SIZE) == 0u) {
    return;
  }

  s_last_frame_cycles = now_cycles;
  s_pattern_force_send = 0u;
  s_pattern_frame_counter++;
  step_frames = ws2812_pattern_step_frames(s_active_pattern);
  s_pattern_frame_repeat++;
  if (s_pattern_frame_repeat >= step_frames) {
    s_pattern_frame_repeat = 0u;
    s_pattern_anim_step++;
  }
}

void HAL_SPI_TxCpltCallback(SPI_HandleTypeDef *hspi)
{
  if ((hspi != NULL) && (hspi->Instance == SPI3)) {
    ws2812_scope_pulse_count(4u);
    if (__HAL_SPI_GET_FLAG(hspi, SPI_FLAG_EOT) != RESET) {
      __HAL_SPI_CLEAR_EOTFLAG(hspi);
    }
    ws2812_pin_gpio_low_mode();
    s_busy = 0u;
  }
}

void HAL_SPI_ErrorCallback(SPI_HandleTypeDef *hspi)
{
  if ((hspi != NULL) && (hspi->Instance == SPI3)) {
    ws2812_scope_pulse_count(5u);
    ws2812_pin_gpio_low_mode();
    s_busy = 0u;
  }
}

void ws2812_spi_dma_irq_trace(void)
{
#if WS2812_SPI_USE_DMA
  ws2812_scope_pulse_count(2u);
#endif
}

void ws2812_spi_spi_irq_trace(void)
{
#if WS2812_SPI_USE_DMA
  ws2812_scope_pulse_count(3u);
#endif
}
