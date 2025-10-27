/*---------------------------------------

---------------------------------------*/

#include "board.h"

void board_led_toggle(void)
{
    HAL_GPIO_TogglePin(LED_GPIO_Port, LED_Pin);
}

void board_led_set(uint8_t set)
{
    HAL_GPIO_WritePin(LED_GPIO_Port, LED_Pin, (GPIO_PinState)set);
}
