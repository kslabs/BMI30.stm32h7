/* Simple ADC continuous test to verify ADC+DMA chain without external trigger */
#ifndef ADC_SIMPLE_TEST_H
#define ADC_SIMPLE_TEST_H

#include "stm32h7xx_hal.h"

/* Test mode: 1=continuous software trigger, 0=normal external trigger (TIM15) */
#define ADC_TEST_CONTINUOUS_MODE 1

void adc_simple_test_init(ADC_HandleTypeDef *hadc);
void adc_simple_test_start(ADC_HandleTypeDef *hadc, uint16_t *buffer, uint32_t length);

#endif /* ADC_SIMPLE_TEST_H */
