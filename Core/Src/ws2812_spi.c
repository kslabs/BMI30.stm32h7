#include "ws2812_spi.h"
#include "usb_vendor_app.h"

#include <string.h>

extern SPI_HandleTypeDef hspi3;

typedef char ws2812_pattern_count_must_be_16[
    (WS2812_PATTERN_COUNT == 16) ? 1 : -1];

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
  /* The output is forced to GPIO-low after DMA completion and remains low for
     the rest of the 2.5-ms half-period. 16 zero bytes therefore provide the
     beginning of the reset interval; the following GPIO-low time completes it.
     Keeping 64 bytes here made the SPI clock run almost to the one-third
     boundary and left no usable start-time margin. */
  WS2812_RESET_BYTES = 16u,
  WS2812_BYTES_PER_COLOR = 4u,
  WS2812_BYTES_PER_LED = 12u,
  WS2812_TX_BUF_SIZE = WS2812_PREFIX_BYTES + (WS2812_LED_COUNT * WS2812_BYTES_PER_LED) + WS2812_RESET_BYTES,
  /* SPI123 = PLL3P = 100 MHz, SPI3 prescaler = 32. A complete LED DMA frame
     takes 694 us. The 800-us limit is deliberately inside one third of the
     nominal 2.5-ms TX half-period (833 us). */
  WS2812_SPI_WIRE_HZ = 3125000u,
  WS2812_HALF_PERIOD_US = 2500u,
  WS2812_FIRST_THIRD_US = WS2812_HALF_PERIOD_US / 3u,
  WS2812_SAFE_WINDOW_US = 800u,
  WS2812_START_GUARD_US = 20u,
  WS2812_TX_WIRE_US =
      (((WS2812_TX_BUF_SIZE * 8000000u) + WS2812_SPI_WIRE_HZ - 1u) /
       WS2812_SPI_WIRE_HZ),
  WS2812_START_DEADLINE_US =
      WS2812_SAFE_WINDOW_US - WS2812_TX_WIRE_US - WS2812_START_GUARD_US,
  WS2812_FRAME_RATE_HZ = 400u,
  WS2812_IDLE_STEP_FRAMES = 20u,
  WS2812_TEST_STEP_FRAMES = 200u,
  WS2812_CHASE_STEP_FRAMES = 20u,
  WS2812_CHASE_FAST_STEP_FRAMES = 14u,
  WS2812_UA_DEMO_STEP_FRAMES = 24u,
  WS2812_STATUS_BLINK_HALF_FRAMES = (WS2812_FRAME_RATE_HZ * 3u) / 4u,
  WS2812_DMA_TIMEOUT_MS = 100u,
  WS2812_SPI_CODE_0 = 0x8u, /* 1000 */
  WS2812_SPI_CODE_1 = 0xEu  /* 1110 */
};

typedef char ws2812_frame_must_fit_first_third[
    (WS2812_TX_WIRE_US + WS2812_START_GUARD_US <
     WS2812_FIRST_THIRD_US) ? 1 : -1];

static uint8_t s_pixels[WS2812_LED_COUNT][3];
static uint8_t s_tx_buf[WS2812_TX_BUF_SIZE]
  __attribute__((section(".ram_d2"), aligned(32)));
static volatile uint8_t s_busy = 0u;
static volatile uint8_t s_tx_frame_ready = 0u;
static volatile uint8_t s_tx_buffer_building = 0u;
static volatile ws2812_pattern_t s_active_pattern = WS2812_PATTERN_OFF;
static volatile ws2812_pattern_t s_requested_pattern = WS2812_PATTERN_OFF;
static volatile uint8_t s_pattern_force_send = 1u;
static volatile uint32_t s_frame_period_cycles = 1u;
static volatile uint32_t s_last_frame_cycles = 0u;
static volatile uint32_t s_busy_since_ms = 0u;
static volatile uint32_t s_recovery_count = 0u;
static volatile uint32_t s_phase_late_skip_count = 0u;
static volatile uint32_t s_phase_start_delay_cycles_max = 0u;
static volatile uint32_t s_phase_start_deadline_cycles = 1u;
static volatile uint16_t s_pattern_anim_step = 0u;
static volatile uint16_t s_pattern_frame_repeat = 0u;
static volatile uint32_t s_pattern_frame_counter = 0u;
static volatile ws2812_pattern_t s_override_pattern = WS2812_PATTERN_OFF;
static volatile uint32_t s_override_until_ms = 0u;
static ws2812_pattern_t s_rendered_pattern = WS2812_PATTERN_COUNT;
static uint16_t s_rendered_anim_step = 0xFFFFu;
static uint32_t s_rendered_status_key = 0xFFFFFFFFu;
/* Updated in main-loop service and read by the ADC-completion LED preparation
   path. Do not scan the 32-node RS485 table from the phase-critical ISR. */
static volatile uint8_t s_optic_reaction_source_id =
    WS2812_OPTIC_REACTION_SOURCE_DISABLED;
static volatile uint8_t s_optic_reaction_active_cached = 0u;
static volatile uint8_t s_optic_reaction_remote_cached = 0u;

static void ws2812_pin_gpio_low_mode(void);

static uint32_t ws2812_irq_save(void)
{
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  return primask;
}

static void ws2812_irq_restore(uint32_t primask)
{
  __set_PRIMASK(primask);
}

static uint8_t ws2812_begin_tx_buffer_update(void)
{
  uint32_t primask = ws2812_irq_save();

  if ((s_busy != 0u) || (s_tx_buffer_building != 0u)) {
    ws2812_irq_restore(primask);
    return 0u;
  }

  s_tx_buffer_building = 1u;
  ws2812_irq_restore(primask);
  return 1u;
}

static void ws2812_end_tx_buffer_update(void)
{
  uint32_t primask = ws2812_irq_save();
  s_tx_buffer_building = 0u;
  ws2812_irq_restore(primask);
}

static uint8_t ws2812_try_mark_busy(void)
{
  uint32_t primask = ws2812_irq_save();

  if ((hspi3.Instance != SPI3) ||
      (s_busy != 0u) ||
      (s_tx_buffer_building != 0u) ||
      (s_tx_frame_ready == 0u)) {
    ws2812_irq_restore(primask);
    return 0u;
  }

  s_busy = 1u;
  s_busy_since_ms = HAL_GetTick();
  ws2812_irq_restore(primask);
  return 1u;
}

static void ws2812_clear_busy(void)
{
  uint32_t primask = ws2812_irq_save();
  s_busy = 0u;
  s_busy_since_ms = 0u;
  ws2812_irq_restore(primask);
}

static void ws2812_recover_stalled_transfer(uint32_t now_ms)
{
  uint32_t primask;

  /* Any value other than 0/1 is memory corruption, not a valid busy state.
     Recover immediately instead of applying a timeout to a corrupted
     s_busy_since_ms value. */
  if (s_busy > 1u) {
    primask = ws2812_irq_save();
    s_busy = 0u;
    s_busy_since_ms = 0u;
    s_tx_buffer_building = 0u;
    s_tx_frame_ready = 1u;
    s_pattern_force_send = 1u;
    s_rendered_pattern = WS2812_PATTERN_COUNT;
    s_recovery_count++;
    ws2812_irq_restore(primask);
    (void)HAL_SPI_Abort(&hspi3);
    ws2812_pin_gpio_low_mode();
    return;
  }

  if ((s_busy == 0u) ||
      ((int32_t)(now_ms - s_busy_since_ms) <
       (int32_t)WS2812_DMA_TIMEOUT_MS)) {
    return;
  }

  /* Lock both normal update paths before aborting the stalled DMA/SPI
     transaction. A missed DMA/EOT callback must never freeze the system LED
     permanently. */
  primask = ws2812_irq_save();
  if ((s_busy == 0u) ||
      ((int32_t)(now_ms - s_busy_since_ms) <
       (int32_t)WS2812_DMA_TIMEOUT_MS)) {
    ws2812_irq_restore(primask);
    return;
  }
  s_busy = 0u;
  s_busy_since_ms = 0u;
  s_tx_buffer_building = 1u;
  ws2812_irq_restore(primask);

  (void)HAL_SPI_Abort(&hspi3);
  ws2812_pin_gpio_low_mode();

  primask = ws2812_irq_save();
  s_tx_buffer_building = 0u;
  s_tx_frame_ready = 1u;
  s_pattern_force_send = 1u;
  s_rendered_pattern = WS2812_PATTERN_COUNT;
  s_recovery_count++;
  ws2812_irq_restore(primask);
}

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
  __HAL_RCC_GPIOB_CLK_ENABLE();

  GPIOB->OTYPER &= ~GPIO_PIN_2;
  GPIOB->OSPEEDR = (GPIOB->OSPEEDR & ~(3u << (2u * 2u))) | (3u << (2u * 2u));
  GPIOB->PUPDR &= ~(3u << (2u * 2u));
  GPIOB->AFR[0] = (GPIOB->AFR[0] & ~(0xFu << (2u * 4u))) | (GPIO_AF7_SPI3 << (2u * 4u));
  GPIOB->MODER = (GPIOB->MODER & ~(3u << (2u * 2u))) | (2u << (2u * 2u));
}

static void ws2812_pin_gpio_low_mode(void)
{
  __HAL_RCC_GPIOB_CLK_ENABLE();

  GPIOB->BSRR = ((uint32_t)GPIO_PIN_2 << 16);
  GPIOB->OTYPER &= ~GPIO_PIN_2;
  GPIOB->OSPEEDR = (GPIOB->OSPEEDR & ~(3u << (2u * 2u))) | (3u << (2u * 2u));
  GPIOB->PUPDR &= ~(3u << (2u * 2u));
  GPIOB->MODER = (GPIOB->MODER & ~(3u << (2u * 2u))) | (1u << (2u * 2u));
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
    case WS2812_PATTERN_UP_RED_2:
    case WS2812_PATTERN_UP_YELLOW_2:
    case WS2812_PATTERN_DOWN_RED_2:
    case WS2812_PATTERN_DOWN_YELLOW_2:
    case WS2812_PATTERN_IN_RED_2:
    case WS2812_PATTERN_IN_YELLOW_2:
      return WS2812_CHASE_FAST_STEP_FRAMES;

    case WS2812_PATTERN_UP_RED_1:
    case WS2812_PATTERN_UP_YELLOW_1:
    case WS2812_PATTERN_DOWN_RED_1:
    case WS2812_PATTERN_DOWN_YELLOW_1:
    case WS2812_PATTERN_IN_RED_1:
    case WS2812_PATTERN_IN_YELLOW_1:
    case WS2812_PATTERN_OUT_RED:
    case WS2812_PATTERN_OUT_YELLOW:
      return WS2812_CHASE_STEP_FRAMES;

    case WS2812_PATTERN_UA_DEMO:
      return WS2812_UA_DEMO_STEP_FRAMES;

    case WS2812_PATTERN_OFF:
      return WS2812_TEST_STEP_FRAMES;

    default:
      return WS2812_IDLE_STEP_FRAMES;
  }
}

static uint8_t ws2812_scale_component(uint8_t value, uint8_t level)
{
  return (uint8_t)((((uint16_t)value * (uint16_t)level) + 127u) / 255u);
}

static void ws2812_pixels_blend_strip_led_rgb(uint16_t strip_index,
                                               uint8_t r,
                                               uint8_t g,
                                               uint8_t b)
{
  uint16_t led_index = (uint16_t)(WS2812_ONBOARD_LED_COUNT + strip_index);

  if (led_index >= WS2812_LED_COUNT) {
    return;
  }

  if (r > s_pixels[led_index][0]) {
    s_pixels[led_index][0] = r;
  }
  if (g > s_pixels[led_index][1]) {
    s_pixels[led_index][1] = g;
  }
  if (b > s_pixels[led_index][2]) {
    s_pixels[led_index][2] = b;
  }
}

static void ws2812_render_linear_comet(int32_t head,
                                        int8_t direction,
                                        uint8_t r,
                                        uint8_t g,
                                        uint8_t b,
                                        uint8_t wrap)
{
  static const uint8_t tail_level[] = { 255u, 126u, 54u, 18u };
  int32_t count = (int32_t)WS2812_STRIP_LED_COUNT;
  uint32_t tail;

  if (count <= 0) {
    return;
  }

  for (tail = 0u; tail < (sizeof(tail_level) / sizeof(tail_level[0])); ++tail) {
    int32_t pos = head - ((int32_t)direction * (int32_t)tail);

    if (wrap != 0u) {
      while (pos < 0) {
        pos += count;
      }
      pos %= count;
    } else if ((pos < 0) || (pos >= count)) {
      continue;
    }

    ws2812_pixels_blend_strip_led_rgb((uint16_t)pos,
        ws2812_scale_component(r, tail_level[tail]),
        ws2812_scale_component(g, tail_level[tail]),
        ws2812_scale_component(b, tail_level[tail]));
  }
}

static void ws2812_render_directional_comet(uint16_t anim_step,
                                             uint8_t towards_high,
                                             uint8_t r,
                                             uint8_t g,
                                             uint8_t b)
{
  int32_t count = (int32_t)WS2812_STRIP_LED_COUNT;
  int32_t head;

  if (count <= 0) {
    return;
  }

  head = (int32_t)(anim_step % (uint16_t)count);
  if (towards_high == 0u) {
    head = count - 1 - head;
  }
  ws2812_render_linear_comet(head,
      (towards_high != 0u) ? 1 : -1, r, g, b, 1u);
}

static void ws2812_render_directional_pulses(uint16_t anim_step,
                                              uint8_t towards_high,
                                              uint8_t r,
                                              uint8_t g,
                                              uint8_t b)
{
  static const uint8_t pulse_level[] = { 255u, 150u, 58u };
  const uint16_t period = 7u;
  uint16_t shift = (uint16_t)(anim_step % period);
  uint16_t pos;

  for (pos = 0u; pos < WS2812_STRIP_LED_COUNT; ++pos) {
    uint16_t phase = (towards_high != 0u)
      ? (uint16_t)((pos + period - shift) % period)
      : (uint16_t)((pos + shift) % period);

    if (phase < (sizeof(pulse_level) / sizeof(pulse_level[0]))) {
      ws2812_pixels_set_strip_led_rgb(pos,
          ws2812_scale_component(r, pulse_level[phase]),
          ws2812_scale_component(g, pulse_level[phase]),
          ws2812_scale_component(b, pulse_level[phase]));
    }
  }
}

static void ws2812_render_split_comets(uint16_t anim_step,
                                        uint8_t towards_center,
                                        uint8_t r,
                                        uint8_t g,
                                        uint8_t b)
{
  int32_t count = (int32_t)WS2812_STRIP_LED_COUNT;
  int32_t half = count / 2;
  uint16_t cycle_len;
  int32_t distance;

  if (half <= 0) {
    return;
  }

  cycle_len = (uint16_t)(half + 3);
  distance = (int32_t)(anim_step % cycle_len);
  if (distance >= half) {
    return;
  }

  if (towards_center != 0u) {
    ws2812_render_linear_comet(distance, 1, r, g, b, 0u);
    ws2812_render_linear_comet(count - 1 - distance, -1, r, g, b, 0u);
  } else {
    ws2812_render_linear_comet(half - 1 - distance, -1, r, g, b, 0u);
    ws2812_render_linear_comet(half + distance, 1, r, g, b, 0u);
  }
}

static void ws2812_render_split_pulses(uint16_t anim_step,
                                        uint8_t towards_center,
                                        uint8_t r,
                                        uint8_t g,
                                        uint8_t b)
{
  static const uint8_t pulse_level[] = { 255u, 142u, 44u };
  const uint16_t period = 6u;
  uint16_t half = (uint16_t)(WS2812_STRIP_LED_COUNT / 2u);
  uint16_t shift = (uint16_t)(anim_step % period);
  uint16_t pos;

  for (pos = 0u; pos < WS2812_STRIP_LED_COUNT; ++pos) {
    uint8_t lower_half = (uint8_t)(pos < half);
    uint16_t local = (lower_half != 0u) ? pos : (uint16_t)(pos - half);
    uint8_t towards_high = (uint8_t)(((lower_half != 0u) ==
                                      (towards_center != 0u)) ? 1u : 0u);
    uint16_t phase = (towards_high != 0u)
      ? (uint16_t)((local + period - shift) % period)
      : (uint16_t)((local + shift) % period);

    if (phase < (sizeof(pulse_level) / sizeof(pulse_level[0]))) {
      ws2812_pixels_set_strip_led_rgb(pos,
          ws2812_scale_component(r, pulse_level[phase]),
          ws2812_scale_component(g, pulse_level[phase]),
          ws2812_scale_component(b, pulse_level[phase]));
    }
  }
}

static void ws2812_render_ukraine_demo(uint16_t anim_step)
{
  const uint16_t half = (uint16_t)(WS2812_STRIP_LED_COUNT / 2u);
  const uint16_t wave_period = 24u;
  uint16_t pos;

  for (pos = 0u; pos < WS2812_STRIP_LED_COUNT; ++pos) {
    uint16_t phase = (uint16_t)((anim_step + (pos * 2u)) % wave_period);
    uint16_t triangle = (phase <= (wave_period / 2u))
      ? phase
      : (uint16_t)(wave_period - phase);
    uint8_t level = (uint8_t)(96u + (triangle * 12u));

    if (pos < half) {
      /* Lower half: rich yellow with a travelling fabric-like highlight. */
      ws2812_pixels_set_strip_led_rgb(pos,
          ws2812_scale_component(255u, level),
          ws2812_scale_component(156u, level),
          0u);
    } else {
      /* Upper half: saturated blue; the same wave visually joins the flag. */
      ws2812_pixels_set_strip_led_rgb(pos,
          0u,
          ws2812_scale_component(72u, level),
          ws2812_scale_component(255u, level));
    }
  }
}

static uint32_t ws2812_get_onboard_status_rgb(uint8_t *red_out,
                                              uint8_t *green_out,
                                              uint8_t *blue_out)
{
  extern volatile uint8_t need_recovery;
  extern volatile uint8_t need_hard_reset;
  enum {
    WS2812_STATUS_GREEN_R = 0u,
    WS2812_STATUS_GREEN_G = 96u,
    WS2812_STATUS_GREEN_B = 0u,
    WS2812_STATUS_BLUE_R = 0u,
    WS2812_STATUS_BLUE_G = 0u,
    WS2812_STATUS_BLUE_B = 96u,
    WS2812_STATUS_LIGHT_BLUE_R = 0u,
    WS2812_STATUS_LIGHT_BLUE_G = 36u,
    WS2812_STATUS_LIGHT_BLUE_B = 96u,
    WS2812_STATUS_YELLOW_R = 160u,
    WS2812_STATUS_YELLOW_G = 80u,
    WS2812_STATUS_YELLOW_B = 0u,
    WS2812_STATUS_MAGENTA_R = 128u,
    WS2812_STATUS_MAGENTA_G = 0u,
    WS2812_STATUS_MAGENTA_B = 80u,
    WS2812_STATUS_WHITE_R = 72u,
    WS2812_STATUS_WHITE_G = 72u,
    WS2812_STATUS_WHITE_B = 72u,
    WS2812_STATUS_ALARM_R = 128u,
    WS2812_STATUS_ALARM_G = 0u,
    WS2812_STATUS_ALARM_B = 0u
  };
  const uint32_t blink_half_frames = (WS2812_STATUS_BLINK_HALF_FRAMES != 0u)
    ? WS2812_STATUS_BLINK_HALF_FRAMES
    : 1u;
  vnd_lcd_sync_snapshot_t sync_snapshot;
  uint32_t last_error = vnd_get_last_error();
  uint8_t alarm_active = (uint8_t)(((need_recovery != 0u) ||
                                    (need_hard_reset != 0u) ||
                                    (last_error != 0u)) ? 1u : 0u);
  uint8_t display_slave_mode;
  uint8_t master_sync_active;
  uint8_t local_optic_active =
      (uint8_t)((optic_sensor_get_state() != 0u) ? 1u : 0u);
  uint8_t selected_optic_active = s_optic_reaction_active_cached;
  uint8_t optic_remote = s_optic_reaction_remote_cached;
  uint8_t remote_optic_active =
      (uint8_t)(((selected_optic_active != 0u) && (optic_remote != 0u)) ? 1u : 0u);
  uint8_t optic_active =
      (uint8_t)(((local_optic_active != 0u) || (remote_optic_active != 0u)) ? 1u : 0u);
  uint8_t optic_source_enabled =
      (uint8_t)((s_optic_reaction_source_id <= 31u) ? 1u : 0u);
  uint8_t tx_enabled = (uint8_t)((vnd_is_tx_enabled() != 0u) ? 1u : 0u);
  uint8_t alarm_gate_on = 1u;
  uint8_t smooth_level = 255u;
  uint8_t red = 0u;
  uint8_t green = 0u;
  uint8_t blue = 0u;

  vnd_get_lcd_sync_snapshot(&sync_snapshot);
  display_slave_mode = (uint8_t)((sync_snapshot.raw_mode == VND_SYNC_MODE_SLAVE) ? 1u : 0u);
  master_sync_active = (uint8_t)(((sync_snapshot.raw_mode == VND_SYNC_MODE_MASTER) &&
                                  (sync_snapshot.sync_signal_alive != 0u)) ? 1u : 0u);
  if ((alarm_active != 0u) &&
      (((s_pattern_frame_counter / blink_half_frames) & 1u) != 0u)) {
    alarm_gate_on = 0u;
  }

  if ((tx_enabled != 0u) &&
      ((alarm_active == 0u) || (optic_active != 0u))) {
    uint32_t period_frames = blink_half_frames * 2u;
    uint32_t phase = (period_frames != 0u) ? (s_pattern_frame_counter % period_frames) : 0u;
    uint32_t ramp = 0u;

    if (phase < blink_half_frames) {
      ramp = (blink_half_frames > 1u) ? ((phase * 255u) / (blink_half_frames - 1u)) : 255u;
    } else {
      uint32_t fall_phase = phase - blink_half_frames;
      ramp = (blink_half_frames > 1u) ? (((blink_half_frames - 1u - fall_phase) * 255u) / (blink_half_frames - 1u)) : 0u;
    }

    smooth_level = (uint8_t)(((ramp * ramp * (765u - (2u * ramp))) + 32512u) / 65025u);
  }

  /* Preserve the original local receiver indication independently of the RSP
     remote-source selection. A local hit has priority; 0x44 only replaces the
     old hard-coded MASTER neighbour with an explicitly selected remote ID. */
  if (local_optic_active != 0u) {
    if (display_slave_mode != 0u) {
      red = WS2812_STATUS_YELLOW_R;
      green = WS2812_STATUS_YELLOW_G;
      blue = WS2812_STATUS_YELLOW_B;
    } else {
      red = WS2812_STATUS_GREEN_R;
      green = WS2812_STATUS_GREEN_G;
      blue = WS2812_STATUS_GREEN_B;
    }
  } else if (remote_optic_active != 0u) {
    red = WS2812_STATUS_MAGENTA_R;
    green = WS2812_STATUS_MAGENTA_G;
    blue = WS2812_STATUS_MAGENTA_B;
  } else if (alarm_active != 0u) {
    if (alarm_gate_on != 0u) {
      red = WS2812_STATUS_ALARM_R;
      green = WS2812_STATUS_ALARM_G;
      blue = WS2812_STATUS_ALARM_B;
    }
  } else {
    /* Assigned role is configuration, not proof of a live RS-485 link.
       Show the same unambiguous light-blue offline color on every role. */
    if (sync_snapshot.sync_signal_alive == 0u) {
      red = WS2812_STATUS_LIGHT_BLUE_R;
      green = WS2812_STATUS_LIGHT_BLUE_G;
      blue = WS2812_STATUS_LIGHT_BLUE_B;
    } else if (display_slave_mode != 0u) {
      red = WS2812_STATUS_WHITE_R;
      green = WS2812_STATUS_WHITE_G;
      blue = WS2812_STATUS_WHITE_B;
    } else {
      if (master_sync_active != 0u) {
        red = WS2812_STATUS_BLUE_R;
        green = WS2812_STATUS_BLUE_G;
        blue = WS2812_STATUS_BLUE_B;
      } else {
        red = WS2812_STATUS_LIGHT_BLUE_R;
        green = WS2812_STATUS_LIGHT_BLUE_G;
        blue = WS2812_STATUS_LIGHT_BLUE_B;
      }
    }

  }

  /* Optic changes only RGB. TX remains an independent brightness/breathing
     layer for local and remote optical states alike. Keep the dedicated alarm
     blink unchanged unless a higher-priority local optic state is displayed. */
  if ((tx_enabled != 0u) &&
      ((alarm_active == 0u) || (optic_active != 0u))) {
    red = (uint8_t)(((uint32_t)red * smooth_level) / 255u);
    green = (uint8_t)(((uint32_t)green * smooth_level) / 255u);
    blue = (uint8_t)(((uint32_t)blue * smooth_level) / 255u);
  }

  if (red_out != NULL) {
    *red_out = red;
  }
  if (green_out != NULL) {
    *green_out = green;
  }
  if (blue_out != NULL) {
    *blue_out = blue;
  }

  return ((uint32_t)alarm_active << 0) |
      ((uint32_t)display_slave_mode << 1) |
         ((uint32_t)optic_active << 2) |
         ((uint32_t)tx_enabled << 3) |
         ((uint32_t)alarm_gate_on << 4) |
         ((uint32_t)master_sync_active << 5) |
         ((uint32_t)optic_remote << 6) |
         ((uint32_t)optic_source_enabled << 7) |
         ((uint32_t)red << 8) |
         ((uint32_t)green << 16) |
         ((uint32_t)blue << 24);
}

static void ws2812_update_onboard_status_in_tx_buffer(void)
{
  uint8_t red = 0u;
  uint8_t green = 0u;
  uint8_t blue = 0u;

  (void)ws2812_get_onboard_status_rgb(&red, &green, &blue);
  ws2812_encode_led_rgb(s_tx_buf + WS2812_PREFIX_BYTES, red, green, blue);
  ws2812_clean_dcache_region(s_tx_buf,
                             (uint32_t)(WS2812_PREFIX_BYTES + WS2812_BYTES_PER_LED));
}

static void ws2812_apply_onboard_status_overlay(void)
{
  uint8_t red = 0u;
  uint8_t green = 0u;
  uint8_t blue = 0u;

  (void)ws2812_get_onboard_status_rgb(&red, &green, &blue);
  ws2812_pixels_set_onboard_rgb(red, green, blue);
}

static void ws2812_render_pattern(ws2812_pattern_t pattern, uint16_t anim_step)
{
  enum {
    EFFECT_RED_R = 255u,
    EFFECT_RED_G = 0u,
    EFFECT_RED_B = 0u,
    EFFECT_YELLOW_R = 255u,
    EFFECT_YELLOW_G = 120u,
    EFFECT_YELLOW_B = 0u
  };

  ws2812_pixels_clear_all();

  switch (pattern) {
    case WS2812_PATTERN_OFF:
      break;

    case WS2812_PATTERN_UP_RED_1:
      ws2812_render_directional_comet(anim_step, 1u,
          EFFECT_RED_R, EFFECT_RED_G, EFFECT_RED_B);
      break;

    case WS2812_PATTERN_UP_RED_2:
      ws2812_render_directional_pulses(anim_step, 1u,
          EFFECT_RED_R, EFFECT_RED_G, EFFECT_RED_B);
      break;

    case WS2812_PATTERN_UP_YELLOW_1:
      ws2812_render_directional_comet(anim_step, 1u,
          EFFECT_YELLOW_R, EFFECT_YELLOW_G, EFFECT_YELLOW_B);
      break;

    case WS2812_PATTERN_UP_YELLOW_2:
      ws2812_render_directional_pulses(anim_step, 1u,
          EFFECT_YELLOW_R, EFFECT_YELLOW_G, EFFECT_YELLOW_B);
      break;

    case WS2812_PATTERN_DOWN_RED_1:
      ws2812_render_directional_comet(anim_step, 0u,
          EFFECT_RED_R, EFFECT_RED_G, EFFECT_RED_B);
      break;

    case WS2812_PATTERN_DOWN_RED_2:
      ws2812_render_directional_pulses(anim_step, 0u,
          EFFECT_RED_R, EFFECT_RED_G, EFFECT_RED_B);
      break;

    case WS2812_PATTERN_DOWN_YELLOW_1:
      ws2812_render_directional_comet(anim_step, 0u,
          EFFECT_YELLOW_R, EFFECT_YELLOW_G, EFFECT_YELLOW_B);
      break;

    case WS2812_PATTERN_DOWN_YELLOW_2:
      ws2812_render_directional_pulses(anim_step, 0u,
          EFFECT_YELLOW_R, EFFECT_YELLOW_G, EFFECT_YELLOW_B);
      break;

    case WS2812_PATTERN_IN_RED_1:
      ws2812_render_split_comets(anim_step, 1u,
          EFFECT_RED_R, EFFECT_RED_G, EFFECT_RED_B);
      break;

    case WS2812_PATTERN_IN_RED_2:
      ws2812_render_split_pulses(anim_step, 1u,
          EFFECT_RED_R, EFFECT_RED_G, EFFECT_RED_B);
      break;

    case WS2812_PATTERN_IN_YELLOW_1:
      ws2812_render_split_comets(anim_step, 1u,
          EFFECT_YELLOW_R, EFFECT_YELLOW_G, EFFECT_YELLOW_B);
      break;

    case WS2812_PATTERN_IN_YELLOW_2:
      ws2812_render_split_pulses(anim_step, 1u,
          EFFECT_YELLOW_R, EFFECT_YELLOW_G, EFFECT_YELLOW_B);
      break;

    case WS2812_PATTERN_OUT_RED:
      ws2812_render_split_comets(anim_step, 0u,
          EFFECT_RED_R, EFFECT_RED_G, EFFECT_RED_B);
      break;

    case WS2812_PATTERN_OUT_YELLOW:
      ws2812_render_split_comets(anim_step, 0u,
          EFFECT_YELLOW_R, EFFECT_YELLOW_G, EFFECT_YELLOW_B);
      break;

    case WS2812_PATTERN_UA_DEMO:
      ws2812_render_ukraine_demo(anim_step);
      break;

    default:
      break;
  }

  ws2812_apply_onboard_status_overlay();
}

static uint8_t ws2812_start_transfer(uint8_t *tx_buf, uint16_t tx_len)
{
  HAL_StatusTypeDef st;

  if (ws2812_try_mark_busy() == 0u) {
    return 0u;
  }

  ws2812_scope_sync_begin();
  ws2812_pin_spi_mode();
#if WS2812_SPI_USE_DMA
  st = HAL_SPI_Transmit_DMA(&hspi3, tx_buf, tx_len);
  if (st != HAL_OK) {
    ws2812_scope_pulse_count(5u);
    ws2812_pin_gpio_low_mode();
    ws2812_clear_busy();
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
  ws2812_clear_busy();
  if (st != HAL_OK) {
    return 0u;
  }
#endif

  return 1u;
}

void ws2812_spi_init(void)
{
  /* ADC can begin producing frame interrupts before main() reaches its
     diagnostic DWT setup. Enable CYCCNT here because phase-window validation
     depends on it from the first LED frame. */
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->LAR = 0xC5ACCE55u;
  DWT->CYCCNT = 0u;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

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
  s_pattern_frame_counter = 0u;
  s_busy_since_ms = 0u;
  s_recovery_count = 0u;
  s_phase_late_skip_count = 0u;
  s_phase_start_delay_cycles_max = 0u;
  s_phase_start_deadline_cycles =
      (uint32_t)(((uint64_t)SystemCoreClock *
                  (uint64_t)WS2812_START_DEADLINE_US) / 1000000ULL);
  if (s_phase_start_deadline_cycles == 0u) {
    s_phase_start_deadline_cycles = 1u;
  }
  s_override_pattern = WS2812_PATTERN_OFF;
  s_override_until_ms = 0u;
  s_busy = 0u;
  s_tx_frame_ready = 0u;
  s_tx_buffer_building = 0u;
  s_rendered_pattern = WS2812_PATTERN_COUNT;
  s_rendered_anim_step = 0xFFFFu;
  s_rendered_status_key = 0xFFFFFFFFu;
  s_optic_reaction_source_id = WS2812_OPTIC_REACTION_SOURCE_DISABLED;
  s_optic_reaction_active_cached = 0u;
  s_optic_reaction_remote_cached = 0u;
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
  if (ws2812_begin_tx_buffer_update() == 0u) {
    return 0u;
  }

  ws2812_build_tx_buffer();
  ws2812_clean_dcache_region(s_tx_buf, (uint32_t)sizeof(s_tx_buf));
  s_tx_frame_ready = 1u;
  ws2812_end_tx_buffer_update();
  /* Never start SPI from an arbitrary caller time. The prepared frame will be
     transmitted at the next ADC/TX phase edge. */
  return 1u;
}

uint8_t ws2812_spi_is_busy(void)
{
  return s_busy;
}

uint32_t ws2812_spi_get_frame_count(void)
{
  return s_pattern_frame_counter;
}

uint32_t ws2812_spi_get_recovery_count(void)
{
  return s_recovery_count;
}

uint32_t ws2812_spi_get_phase_late_skip_count(void)
{
  return s_phase_late_skip_count;
}

uint32_t ws2812_spi_get_phase_start_delay_max_us(void)
{
  if (SystemCoreClock == 0u) {
    return 0u;
  }
  return (uint32_t)((((uint64_t)s_phase_start_delay_cycles_max * 1000000ULL) +
                     (uint64_t)SystemCoreClock - 1ULL) /
                    (uint64_t)SystemCoreClock);
}

uint32_t ws2812_spi_get_wire_time_us(void)
{
  return WS2812_TX_WIRE_US;
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

ws2812_pattern_t ws2812_spi_get_active_pattern(void)
{
  return s_active_pattern;
}

void ws2812_spi_trigger_pattern(ws2812_pattern_t pattern, uint16_t duration_ms)
{
  if (((uint32_t)pattern >= (uint32_t)WS2812_PATTERN_COUNT) ||
      (pattern == WS2812_PATTERN_OFF) ||
      (duration_ms == 0u)) {
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

void ws2812_spi_trigger_event(ws2812_event_t event, uint16_t duration_ms)
{
  ws2812_pattern_t pattern = WS2812_PATTERN_OFF;

  switch (event) {
    case WS2812_EVENT_CHANNEL_B:
      pattern = WS2812_PATTERN_UP_RED_1;
      break;

    case WS2812_EVENT_CHANNEL_A:
      pattern = WS2812_PATTERN_DOWN_RED_1;
      break;

    case WS2812_EVENT_CHANNEL_BOTH:
      pattern = WS2812_PATTERN_IN_RED_1;
      break;

    case WS2812_EVENT_SPLIT_IN:
      pattern = WS2812_PATTERN_IN_RED_1;
      break;

    case WS2812_EVENT_SPLIT_OUT:
      pattern = WS2812_PATTERN_OUT_RED;
      break;

    case WS2812_EVENT_NONE:
    default:
      pattern = WS2812_PATTERN_OFF;
      break;
  }

  ws2812_spi_trigger_pattern(pattern, duration_ms);
}

uint8_t ws2812_spi_set_optic_reaction_source(uint8_t source_id)
{
  if (source_id > 31u) {
    source_id = WS2812_OPTIC_REACTION_SOURCE_DISABLED;
  }

  s_optic_reaction_source_id = source_id;
  s_optic_reaction_active_cached = 0u;
  s_optic_reaction_remote_cached = 0u;
  s_pattern_force_send = 1u;
  return source_id;
}

uint8_t ws2812_spi_get_optic_reaction_source(void)
{
  return s_optic_reaction_source_id;
}

uint8_t ws2812_spi_get_optic_reaction_active(void)
{
  return s_optic_reaction_active_cached;
}

uint8_t ws2812_spi_get_optic_reaction_remote(void)
{
  return s_optic_reaction_remote_cached;
}

static void ws2812_refresh_optic_reaction_cache(void)
{
  enum { WS2812_OPTIC_STATUS_BIT = 0x20u };
  uint8_t source_id = s_optic_reaction_source_id;
  uint8_t status_bytes[32] = {0u};
  uint32_t seen_mask = 0u;
  vnd_lcd_sync_snapshot_t sync_snapshot;
  uint8_t source_active = 0u;
  uint8_t source_remote = 0u;

  if (source_id > 31u) {
    s_optic_reaction_active_cached = 0u;
    s_optic_reaction_remote_cached = 0u;
    return;
  }

  memset(&sync_snapshot, 0, sizeof(sync_snapshot));
  vnd_get_lcd_sync_snapshot(&sync_snapshot);

  if ((sync_snapshot.node_id_assigned != 0u) &&
      (source_id == sync_snapshot.node_id)) {
    source_active = (uint8_t)((optic_sensor_get_state() != 0u) ? 1u : 0u);
  } else {
    source_remote = 1u;
    (void)rs485_status_get_snapshot(NULL,
                                    NULL,
                                    &seen_mask,
                                    status_bytes,
                                    (uint8_t)sizeof(status_bytes));
    if ((seen_mask & (1u << source_id)) != 0u) {
      /* A missing/stale peer is not an optical LOW update. Keep the last
         accepted state across SYNC loss and change it only when a fresh status
         byte for the configured source ID is actually present. */
      source_active = (uint8_t)(
          ((status_bytes[source_id] & WS2812_OPTIC_STATUS_BIT) != 0u) ? 1u : 0u);
    } else {
      source_active = s_optic_reaction_active_cached;
    }
  }

  s_optic_reaction_active_cached = source_active;
  s_optic_reaction_remote_cached = source_remote;
}

void ws2812_spi_service(uint32_t now_ms)
{
  ws2812_pattern_t effective_pattern = s_requested_pattern;
  uint32_t status_key = 0u;

  ws2812_recover_stalled_transfer(now_ms);
  ws2812_refresh_optic_reaction_cache();

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

  status_key = ws2812_get_onboard_status_rgb(NULL, NULL, NULL);
  if ((s_pattern_force_send == 0u) &&
      (s_rendered_pattern == s_active_pattern) &&
      (s_rendered_anim_step == s_pattern_anim_step) &&
      (s_rendered_status_key == status_key)) {
    return;
  }

  if (ws2812_begin_tx_buffer_update() == 0u) {
    return;
  }

  status_key = ws2812_get_onboard_status_rgb(NULL, NULL, NULL);
  ws2812_render_pattern(s_active_pattern, s_pattern_anim_step);
  ws2812_build_tx_buffer();
  ws2812_clean_dcache_region(s_tx_buf, (uint32_t)sizeof(s_tx_buf));
  s_tx_frame_ready = 1u;
  s_rendered_pattern = s_active_pattern;
  s_rendered_anim_step = s_pattern_anim_step;
  s_rendered_status_key = status_key;
  s_pattern_force_send = 0u;
  ws2812_end_tx_buffer_update();
}

static void ws2812_note_transfer_started(void)
{
  uint16_t step_frames;

  s_last_frame_cycles = DWT->CYCCNT;
  s_pattern_frame_counter++;
  step_frames = ws2812_pattern_step_frames(s_active_pattern);
  if (step_frames == 0u) {
    step_frames = 1u;
  }

  s_pattern_frame_repeat++;
  if (s_pattern_frame_repeat >= step_frames) {
    s_pattern_frame_repeat = 0u;
    s_pattern_anim_step++;
    s_pattern_force_send = 1u;
  }

  if ((WS2812_STATUS_BLINK_HALF_FRAMES != 0u) &&
      ((s_pattern_frame_counter % WS2812_STATUS_BLINK_HALF_FRAMES) == 0u)) {
    s_pattern_force_send = 1u;
  }
}

void ws2812_spi_prepare_phase_frame(void)
{
  if ((s_busy != 0u) || (s_tx_buffer_building != 0u) || (s_tx_frame_ready == 0u)) {
    return;
  }

  /* Do status sampling, encoding and D-cache maintenance before the marker
     changes. The phase-edge path then contains only the bounded DMA start. */
  ws2812_update_onboard_status_in_tx_buffer();
}

void ws2812_spi_on_phase_start(uint32_t phase_start_cycles)
{
  uint32_t delay_cycles;

  if ((s_busy != 0u) || (s_tx_buffer_building != 0u) || (s_tx_frame_ready == 0u)) {
    return;
  }

  delay_cycles = (uint32_t)(DWT->CYCCNT - phase_start_cycles);
  if (delay_cycles > s_phase_start_deadline_cycles) {
    /* A late LED frame is less important than clean ADC reception. Leave PB2
       low and retry on the next half-period instead of entering the protected
       final two thirds of this one. */
    s_phase_late_skip_count++;
    return;
  }

  if (ws2812_start_transfer(s_tx_buf, (uint16_t)WS2812_TX_BUF_SIZE) != 0u) {
    delay_cycles = (uint32_t)(DWT->CYCCNT - phase_start_cycles);
    if (delay_cycles > s_phase_start_delay_cycles_max) {
      s_phase_start_delay_cycles_max = delay_cycles;
    }
    ws2812_note_transfer_started();
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
    ws2812_clear_busy();
  }
}

void HAL_SPI_ErrorCallback(SPI_HandleTypeDef *hspi)
{
  if ((hspi != NULL) && (hspi->Instance == SPI3)) {
    ws2812_scope_pulse_count(5u);
    ws2812_pin_gpio_low_mode();
    ws2812_clear_busy();
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
