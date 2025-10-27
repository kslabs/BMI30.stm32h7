/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file    stm32h7xx_it.c
  * @brief   Interrupt Service Routines.
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

/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "stm32h7xx_it.h"
/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdint.h>
#include "lcd.h" // добавлено для вывода на экран при HardFault
extern volatile uint32_t systick_heartbeat; // добавлено: глобальный счётчик из main.c
/* Прототип низкоуровневого вывода UART1 из main.c */
extern void uart1_raw_putc(char c);
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN TD */

/* USER CODE END TD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN PV */
// Переменная для подсчета прерываний SPI2
static volatile uint32_t spi2_interrupt_counter = 0;

// Диагностика HardFault (определения)
volatile uint32_t hardfault_r0=0, hardfault_r1=0, hardfault_r2=0, hardfault_r3=0;
volatile uint32_t hardfault_r12=0, hardfault_lr=0, hardfault_pc=0, hardfault_psr=0;
volatile uint32_t hardfault_cfsr=0, hardfault_hfsr=0, hardfault_bfar=0, hardfault_mmfar=0; // добавлено
volatile uint32_t hardfault_active = 0; // 1 когда данные заполнены
// Прототип обработчика захвата стека
void HardFault_Capture(uint32_t *stack_addr);
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/* External variables --------------------------------------------------------*/
extern PCD_HandleTypeDef hpcd_USB_OTG_HS;
extern DMA_HandleTypeDef hdma_adc1;
extern DMA_HandleTypeDef hdma_adc2;
extern DAC_HandleTypeDef hdac1;
extern TIM_HandleTypeDef htim6;
/* USER CODE BEGIN EV */

/* USER CODE END EV */

/******************************************************************************/
/*           Cortex Processor Interruption and Exception Handlers          */
/******************************************************************************/
/**
  * @brief This function handles Non maskable interrupt.
  */
void NMI_Handler(void)
{
  /* USER CODE BEGIN NonMaskableInt_IRQn 0 */

  /* USER CODE END NonMaskableInt_IRQn 0 */
  /* USER CODE BEGIN NonMaskableInt_IRQn 1 */
   while (1)
  {
  }
  /* USER CODE END NonMaskableInt_IRQn 1 */
}

/**
  * @brief This function handles Hard fault interrupt.
  */
void HardFault_Handler(void)
{
  /* USER CODE BEGIN HardFault_IRQn 0 */
  __asm volatile(
    "tst lr, #4            \n"
    "ite eq                \n"
    "mrseq r0, msp         \n"
    "mrsne r0, psp         \n"
    "b HardFault_Capture   \n"
  );
  /* USER CODE END HardFault_IRQn 0 */
  while (1)
  {
    /* USER CODE BEGIN W1_HardFault_IRQn 0 */
    /* USER CODE END W1_HardFault_IRQn 0 */
  }
}

/**
  * @brief This function handles Memory management fault.
  */
void MemManage_Handler(void)
{
  /* USER CODE BEGIN MemoryManagement_IRQn 0 */

  /* USER CODE END MemoryManagement_IRQn 0 */
  while (1)
  {
    /* USER CODE BEGIN W1_MemoryManagement_IRQn 0 */
    /* USER CODE END W1_MemoryManagement_IRQn 0 */
  }
}

/**
  * @brief This function handles Pre-fetch fault, memory access fault.
  */
void BusFault_Handler(void)
{
  /* USER CODE BEGIN BusFault_IRQn 0 */

  /* USER CODE END BusFault_IRQn 0 */
  while (1)
  {
    /* USER CODE BEGIN W1_BusFault_IRQn 0 */
    /* USER CODE END W1_BusFault_IRQn 0 */
  }
}

/**
  * @brief This function handles Undefined instruction or illegal state.
  */
void UsageFault_Handler(void)
{
  /* USER CODE BEGIN UsageFault_IRQn 0 */

  /* USER CODE END UsageFault_IRQn 0 */
  while (1)
  {
    /* USER CODE BEGIN W1_UsageFault_IRQn 0 */
    /* USER CODE END W1_UsageFault_IRQn 0 */
  }
}

/**
  * @brief This function handles System service call via SWI instruction.
  */
void SVC_Handler(void)
{
  /* USER CODE BEGIN SVCall_IRQn 0 */

  /* USER CODE END SVCall_IRQn 0 */
  /* USER CODE BEGIN SVCall_IRQn 1 */

  /* USER CODE END SVCall_IRQn 1 */
}

/**
  * @brief This function handles Debug monitor.
  */
void DebugMon_Handler(void)
{
  /* USER CODE BEGIN DebugMonitor_IRQn 0 */

  /* USER CODE END DebugMonitor_IRQn 0 */
  /* USER CODE BEGIN DebugMonitor_IRQn 1 */

  /* USER CODE END DebugMonitor_IRQn 1 */
}

/**
  * @brief This function handles Pendable request for system service.
  */
void PendSV_Handler(void)
{
  /* USER CODE BEGIN PendSV_IRQn 0 */

  /* USER CODE END PendSV_IRQn 0 */
  /* USER CODE BEGIN PendSV_IRQn 1 */

  /* USER CODE END PendSV_IRQn 1 */
}

/**
  * @brief This function handles System tick timer.
  */
void SysTick_Handler(void)
{
  /* USER CODE BEGIN SysTick_IRQn 0 */
  extern volatile uint32_t systick_heartbeat; // локальное extern для совместимости
  systick_heartbeat++; // инкремент счётчика
  /* USER CODE END SysTick_IRQn 0 */
  HAL_IncTick();
  /* USER CODE BEGIN SysTick_IRQn 1 */

  /* USER CODE END SysTick_IRQn 1 */
}

/******************************************************************************/
/* STM32H7xx Peripheral Interrupt Handlers                                    */
/* Add here the Interrupt Handlers for the used peripherals.                  */
/* For the available peripheral interrupt handler names,                      */
/* please refer to the startup file (startup_stm32h7xx.s).                    */
/******************************************************************************/

/**
  * @brief This function handles DMA1 stream0 global interrupt.
  */
void DMA1_Stream0_IRQHandler(void)
{
  /* USER CODE BEGIN DMA1_Stream0_IRQn 0 */
  /* minimized: no UART in IRQ */
  /* USER CODE END DMA1_Stream0_IRQn 0 */
  HAL_DMA_IRQHandler(&hdma_adc1);
  /* USER CODE BEGIN DMA1_Stream0_IRQn 1 */

  /* USER CODE END DMA1_Stream0_IRQn 1 */
}

/**
  * @brief This function handles DMA1 stream1 global interrupt.
  */
void DMA1_Stream1_IRQHandler(void)
{
  /* USER CODE BEGIN DMA1_Stream1_IRQn 0 */
  /* minimized: no UART in IRQ */
  /* USER CODE END DMA1_Stream1_IRQn 0 */
  HAL_DMA_IRQHandler(&hdma_adc2);
  /* USER CODE BEGIN DMA1_Stream1_IRQn 1 */

  /* USER CODE END DMA1_Stream1_IRQn 1 */
}

/**
  * @brief This function handles TIM6 global interrupt, DAC1_CH1 and DAC1_CH2 underrun error interrupts.
  */
void TIM6_DAC_IRQHandler(void)
{
  /* USER CODE BEGIN TIM6_DAC_IRQn 0 */
  /* minimized: no UART in IRQ */
  /* USER CODE END TIM6_DAC_IRQn 0 */
  HAL_DAC_IRQHandler(&hdac1);
  HAL_TIM_IRQHandler(&htim6);
  /* USER CODE BEGIN TIM6_DAC_IRQn 1 */

  /* USER CODE END TIM6_DAC_IRQn 1 */
}

/**
  * @brief This function handles USB On The Go HS global interrupt.
  */
void OTG_HS_IRQHandler(void)
{
  /* USER CODE BEGIN OTG_HS_IRQn 0 */
  /* minimized: no UART in IRQ */
  /* USER CODE END OTG_HS_IRQn 0 */
  HAL_PCD_IRQHandler(&hpcd_USB_OTG_HS);
  /* USER CODE BEGIN OTG_HS_IRQn 1 */

  /* USER CODE END OTG_HS_IRQn 1 */
}

/* USER CODE BEGIN 1 */
// Вспомогательная функция форматирования 32-битного значения в HEX (8 символов)
static void hf_hex(char *dst, uint32_t v){
    static const char *hx = "0123456789ABCDEF";
    for(int i=0;i<8;i++){ dst[7-i] = hx[v & 0xF]; v >>= 4; }
    dst[8] = 0;
}
// Печать регистра на заданной строке: label + ':' + hex
static void hf_print_line(uint16_t y, const char *label, uint32_t val){
    char buf[20];
    char hex[9];
    hf_hex(hex,val);
    // Формат: LBL:XXXXXXXX
    int i=0; while(label[i] && i<5){ buf[i]=label[i]; i++; }
    buf[i++]=':'; for(int j=0;j<8 && i<sizeof(buf)-1;j++) buf[i++]=hex[j];
    buf[i]=0;
    LCD_ShowString_Size(0,y,buf,12,WHITE,BLACK);
}
static void HardFault_Display(void){
    // очистим область
    LCD_FillRect(0,0,160,80,BLACK);
    LCD_ShowString_Size(0,0,"HARDFAULT",12,RED,BLACK);
    hf_print_line(12,"PC",hardfault_pc);
    hf_print_line(24,"LR",hardfault_lr);
    hf_print_line(36,"CFSR",hardfault_cfsr);
    hf_print_line(48,"BFAR",hardfault_bfar);
    hf_print_line(60,"HFSR",hardfault_hfsr);
}
// Реализация захвата контекста HardFault
void HardFault_Capture(uint32_t *stack_addr)
{
  hardfault_r0  = stack_addr[0];
  hardfault_r1  = stack_addr[1];
  hardfault_r2  = stack_addr[2];
  hardfault_r3  = stack_addr[3];
  hardfault_r12 = stack_addr[4];
  hardfault_lr  = stack_addr[5];
  hardfault_pc  = stack_addr[6];
  hardfault_psr = stack_addr[7];
  // Чтение системных регистров Fault
  hardfault_cfsr = SCB->CFSR;
  hardfault_hfsr = SCB->HFSR;
  hardfault_bfar = SCB->BFAR;
  hardfault_mmfar= SCB->MMFAR;
  hardfault_active = 1;
  // Пытаемся вывести на LCD (если инициализирован). Даже если нет — SPI просто не даст эффекта.
  HardFault_Display();
  // Мигание LED для индикации HardFault
  while(1){
    HAL_GPIO_TogglePin(Led_Test_GPIO_Port, Led_Test_Pin);
    for(volatile uint32_t d=0; d<500000; ++d){ __NOP(); }
  }
}
/* USER CODE END 1 */
