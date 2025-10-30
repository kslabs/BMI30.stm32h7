#include <stdint.h>
#include <string.h>
#include "usb_vendor_app.h"  /* для VND_* констант и vnd_tx_kick */
#include "adc_stream.h"

/* Внутренние буферы для тестового режима */
static int16_t s_test_buf0[VND_MAX_SAMPLES];
static int16_t s_test_buf1[VND_MAX_SAMPLES];
static uint16_t s_test_sawtooth_phase = 0;
static volatile uint32_t s_test_frame_counter = 0;
static uint32_t s_test_frame_consumed = 0;

/* Экспортируемый флаг пробуждения таска */
extern volatile uint8_t vnd_tx_kick;

int vnd_testgen_try_consume_latest(uint16_t **out_ch0, uint16_t **out_ch1, uint16_t *out_samples)
{
    if(!out_ch0 || !out_ch1 || !out_samples) return 0;
    uint32_t current = s_test_frame_counter;
    if(current == s_test_frame_consumed){ return 0; }
    s_test_frame_consumed = current; /* last-buffer-wins */
    *out_ch0 = (uint16_t*)s_test_buf0;
    *out_ch1 = (uint16_t*)s_test_buf1;
    uint16_t act = adc_stream_get_active_samples();
    if(act == 0 || act > VND_MAX_SAMPLES) act = VND_FULL_DEFAULT_SAMPLES;
    *out_samples = act;
    return 1;
}

void vnd_generate_test_sawtooth(void)
{
    /* Выбор числа сэмплов: в тестовом режиме берём активное значение/дефолт */
    uint16_t samples = adc_stream_get_active_samples();
    if(samples == 0 || samples > VND_MAX_SAMPLES) samples = VND_FULL_DEFAULT_SAMPLES;

    /* Последовательность проверки: счёт 1..N в каждом канале */
    for(uint16_t i = 0; i < samples; i++){
        int16_t v = (int16_t)(i + 1); /* 1..N */
        s_test_buf0[i] = v;
        s_test_buf1[i] = v;
    }
    /* Фаза не используется в этом режиме — оставлена для совместимости */

    s_test_frame_counter++;
    vnd_tx_kick = 1; /* разбудить пайплайн */
}
