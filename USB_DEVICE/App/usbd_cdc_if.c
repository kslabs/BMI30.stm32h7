/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : usbd_cdc_if.c
  * @version        : v1.0_Cube
  * @brief          : Usb device for Virtual Com Port.
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
#include "usbd_cdc_if.h"

/* USER CODE BEGIN INCLUDE */
#include "usb_cdc_proto.h"       /* usb_stream_init, usb_stream_on_rx_bytes */
#include "usbd_cdc_custom.h"     /* USBD_VND_DataReceived prototype */
#include "main.h"                /* Led_Test_GPIO_Port, Led_Test_Pin */
#include "build_info.h"          /* fw_git_hash, fw_build_date, fw_build_time */
#include "usb_vendor_app.h"      /* vnd_report_fps_stats, vnd_print_perf_stats */

/* USER CODE END INCLUDE */

/* Private typedef -----------------------------------------------------------*/
/* Private define ------------------------------------------------------------*/
/* Private macro -------------------------------------------------------------*/

/* USER CODE BEGIN PV */
/* Private variables ---------------------------------------------------------*/
static volatile uint32_t led_off_tick = 0; // Время выключения LED после индикации UART RX

/* USER CODE END PV */

/** @addtogroup STM32_USB_OTG_DEVICE_LIBRARY
  * @brief Usb device library.
  * @{
  */

/** @addtogroup USBD_CDC_IF
  * @{
  */

/** @defgroup USBD_CDC_IF_Private_TypesDefinitions USBD_CDC_IF_Private_TypesDefinitions
  * @brief Private types.
  * @{
  */

/* USER CODE BEGIN PRIVATE_TYPES */

/* USER CODE END PRIVATE_TYPES */

/**
  * @}
  */

/** @defgroup USBD_CDC_IF_Private_Defines USBD_CDC_IF_Private_Defines
  * @brief Private defines.
  * @{
  */

/* USER CODE BEGIN PRIVATE_DEFINES */
/* USER CODE END PRIVATE_DEFINES */

/**
  * @}
  */

/** @defgroup USBD_CDC_IF_Private_Macros USBD_CDC_IF_Private_Macros
  * @brief Private macros.
  * @{
  */

/* USER CODE BEGIN PRIVATE_MACRO */

/* USER CODE END PRIVATE_MACRO */

/**
  * @}
  */

/** @defgroup USBD_CDC_IF_Private_Variables USBD_CDC_IF_Private_Variables
  * @brief Private variables.
  * @{
  */

/* Create buffer for reception and transmission           */
/* It's up to user to redefine and/or remove those define */
/** Received data over USB are stored in this buffer      */
uint8_t UserRxBufferHS[APP_RX_DATA_SIZE];

/** Data to send over USB CDC are stored in this buffer   */
uint8_t UserTxBufferHS[APP_TX_DATA_SIZE];

/* USER CODE BEGIN PRIVATE_VARIABLES */

/* USER CODE END PRIVATE_VARIABLES */

/**
  * @}
  */

/** @defgroup USBD_CDC_IF_Exported_Variables USBD_CDC_IF_Exported_Variables
  * @brief Public variables.
  * @{
  */

extern USBD_HandleTypeDef hUsbDeviceHS;

/* USER CODE BEGIN EXPORTED_VARIABLES */

/* USER CODE END EXPORTED_VARIABLES */

/**
  * @}
  */

/** @defgroup USBD_CDC_IF_Private_FunctionPrototypes USBD_CDC_IF_Private_FunctionPrototypes
  * @brief Private functions declaration.
  * @{
  */

static int8_t CDC_Init_HS(void);
static int8_t CDC_DeInit_HS(void);
static int8_t CDC_Control_HS(uint8_t cmd, uint8_t* pbuf, uint16_t length);
static int8_t CDC_Receive_HS(uint8_t* pbuf, uint32_t *Len);
static int8_t CDC_TransmitCplt_HS(uint8_t *pbuf, uint32_t *Len, uint8_t epnum);

/* USER CODE BEGIN PRIVATE_FUNCTIONS_DECLARATION */

/* USER CODE END PRIVATE_FUNCTIONS_DECLARATION */

/**
  * @}
  */

USBD_CDC_ItfTypeDef USBD_Interface_fops_HS =
{
  CDC_Init_HS,
  CDC_DeInit_HS,
  CDC_Control_HS,
  CDC_Receive_HS,
  CDC_TransmitCplt_HS
};

/* Private functions ---------------------------------------------------------*/

/**
  * @brief  Initializes the CDC media low layer over the USB HS IP
  * @retval USBD_OK if all operations are OK else USBD_FAIL
  */
static int8_t CDC_Init_HS(void)
{
  /* USER CODE BEGIN 8 */
  /* Set Application Buffers */
  USBD_CDC_SetTxBuffer(&hUsbDeviceHS, UserTxBufferHS, 0);
  USBD_CDC_SetRxBuffer(&hUsbDeviceHS, UserRxBufferHS);
  usb_stream_init(); // инициализация протокол/стрим
  return (USBD_OK);
  /* USER CODE END 8 */
}

/**
  * @brief  DeInitializes the CDC media low layer
  * @param  None
  * @retval USBD_OK if all operations are OK else USBD_FAIL
  */
static int8_t CDC_DeInit_HS(void)
{
  /* USER CODE BEGIN 9 */
  return (USBD_OK);
  /* USER CODE END 9 */
}

/**
  * @brief  Manage the CDC class requests
  * @param  cmd: Command code
  * @param  pbuf: Buffer containing command data (request parameters)
  * @param  length: Number of data to be sent (in bytes)
  * @retval Result of the operation: USBD_OK if all operations are OK else USBD_FAIL
  */
static int8_t CDC_Control_HS(uint8_t cmd, uint8_t* pbuf, uint16_t length)
{
  /* USER CODE BEGIN 10 */
  switch(cmd)
  {
  case CDC_SEND_ENCAPSULATED_COMMAND:

    break;

  case CDC_GET_ENCAPSULATED_RESPONSE:

    break;

  case CDC_SET_COMM_FEATURE:

    break;

  case CDC_GET_COMM_FEATURE:

    break;

  case CDC_CLEAR_COMM_FEATURE:

    break;

  /*******************************************************************************/
  /* Line Coding Structure                                                       */
  /*-----------------------------------------------------------------------------*/
  /* Offset | Field       | Size | Value  | Description                          */
  /* 0      | dwDTERate   |   4  | Number |Data terminal rate, in bits per second*/
  /* 4      | bCharFormat |   1  | Number | Stop bits                            */
  /*                                        0 - 1 Stop bit                       */
  /*                                        1 - 1.5 Stop bits                    */
  /*                                        2 - 2 Stop bits                      */
  /* 5      | bParityType |  1   | Number | Parity                               */
  /*                                        0 - None                             */
  /*                                        1 - Odd                              */
  /*                                        2 - Even                             */
  /*                                        3 - Mark                             */
  /*                                        4 - Space                            */
  /* 6      | bDataBits  |   1   | Number Data bits (5, 6, 7, 8 or 16).          */
  /*******************************************************************************/
  case CDC_SET_LINE_CODING:

    break;

  case CDC_GET_LINE_CODING:

    break;

  case CDC_SET_CONTROL_LINE_STATE:

    break;

  case CDC_SEND_BREAK:

    break;

  default:
    break;
  }

  return (USBD_OK);
  /* USER CODE END 10 */
}

/**
  * @brief Data received over USB OUT endpoint are sent over CDC interface
  *         through this function.
  *
  *         @note
  *         This function will issue a NAK packet on any OUT packet received on
  *         USB endpoint until exiting this function. If you exit this function
  *         before transfer is complete on CDC interface (ie. using DMA controller)
  *         it will result in receiving more data while previous ones are still
  *         not sent.
  *
  * @param  Buf: Buffer of data to be received
  * @param  Len: Number of data received (in bytes)
  * @retval Result of the operation: USBD_OK if all operations are OK else USBD_FAILL
  */
static int8_t CDC_Receive_HS(uint8_t* Buf, uint32_t *Len)
{
  /* USER CODE BEGIN 11 */
  // ДИАГНОСТИКА: безопасный вывод через переменную (не printf в прерывании)
  static volatile uint32_t cdc_rx_count = 0;
  static volatile uint8_t last_cmd = 0;
  static volatile uint32_t last_len = 0;
  
  cdc_rx_count++;
  if(*Len > 0) last_cmd = Buf[0];
  last_len = *Len;
  (void)cdc_rx_count; (void)last_cmd; (void)last_len; // suppress warnings
  
  // Индикация приёма UART данных через LED - мигать при любом событии RX
  HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_SET); // Включить LED
  led_off_tick = HAL_GetTick() + 100; // Выключить через 100ms
  
  // Проверка текстовых команд для отладки через UART (COM4)
  
  // HELP - список доступных команд
  if (*Len >= 4 && Buf[0] == 'H' && Buf[1] == 'E' && Buf[2] == 'L' && Buf[3] == 'P') {
    printf("\r\n=== DEBUG COMMANDS ===\r\n");
    printf("VER     - firmware version (git commit, build date)\r\n");
    printf("STATUS  - current state (streaming, counters, fps)\r\n");
    printf("PERF    - performance stats (prepare/tx/interval timings)\r\n");
    printf("FPS     - FPS statistics only\r\n");
    printf("RESET   - software reset (reboot device)\r\n");
    printf("HELP    - this help message\r\n");
    printf("=====================\r\n");
  }
  
  // VER/VERSION - версия firmware (унифицированный формат с UART1)
  if ((*Len >= 3 && Buf[0] == 'V' && Buf[1] == 'E' && Buf[2] == 'R') ||
      (*Len >= 7 && Buf[0] == 'V' && Buf[1] == 'E' && Buf[2] == 'R' && 
                    Buf[3] == 'S' && Buf[4] == 'I' && Buf[5] == 'O' && Buf[6] == 'N')) {
    printf("\r\n=== FIRMWARE VERSION (CDC) ===\r\n");
    printf("Version: %s\r\n", FW_VERSION_STR);
    printf("Git:     %s\r\n", fw_git_hash);
    printf("Built:   %s %s\r\n", fw_build_date, fw_build_time);
    printf("VND_PAIR_BUFFERS: %d\r\n", 8);
    printf("================================\r\n");
  }
  
  // STATUS - текущее состояние устройства (унифицированный формат)
  if (*Len >= 6 && Buf[0] == 'S' && Buf[1] == 'T' && Buf[2] == 'A' && 
                   Buf[3] == 'T' && Buf[4] == 'U' && Buf[5] == 'S') {
    printf("\r\n=== DEVICE STATUS (CDC) ===\r\n");
    printf("Uptime: %lu ms\r\n", HAL_GetTick());
    printf("Use 'PERF' or 'FPS' for detailed statistics\r\n");
    printf("==============================\r\n");
  }
  
  // FPS - только FPS статистика (легковесная версия PERF)
  if (*Len >= 3 && Buf[0] == 'F' && Buf[1] == 'P' && Buf[2] == 'S') {
    // Re-enabled FPS stats reporting
    vnd_report_fps_stats();
  }
  
  // PERF - полная статистика производительности
  if (*Len >= 4 && Buf[0] == 'P' && Buf[1] == 'E' && Buf[2] == 'R' && Buf[3] == 'F') {
    // Re-enabled detailed performance stats reporting
    vnd_print_perf_stats();
  }
  
  // RESET - перезагрузка устройства
  if (*Len >= 5 && Buf[0] == 'R' && Buf[1] == 'E' && Buf[2] == 'S' && Buf[3] == 'E' && Buf[4] == 'T') {
    printf("[CDC] RESET command received - performing software reset\r\n");
    HAL_Delay(100); // Дать время на отправку сообщения
    NVIC_SystemReset(); // Программный сброс
  }
  
  // Проксируем команды протокола в Vendor и отключаем CDC-протокол для этих команд,
  // чтобы не запускалась параллельная передача кадров по CDC.
  uint8_t vendor_forwarded = 0;
  if (*Len >= 1) {
    uint8_t cmd = Buf[0];
    switch (cmd) {
      case 0x13u: // VND_CMD_SET_FULL_MODE
      case 0x14u: // VND_CMD_SET_PROFILE
      case 0x18u: // VND_CMD_SET_ASYNC_MODE
      case 0x19u: // VND_CMD_SET_CHMODE
      case 0x15u: // VND_CMD_SET_ROI_US
      case 0x20u: // VND_CMD_START_STREAM
      case 0x21u: // VND_CMD_STOP_STREAM
      case 0x30u: // VND_CMD_GET_STATUS
        USBD_VND_DataReceived(Buf, *Len);
        vendor_forwarded = 1;
        break;
      default:
        break;
    }
  }
  // В CDC-протокол НЕ передаём пакеты, которые были распознаны как Vendor команды
  if (!vendor_forwarded) {
    usb_stream_on_rx_bytes(Buf, *Len);
  }
  USBD_CDC_SetRxBuffer(&hUsbDeviceHS, &Buf[0]);
  USBD_CDC_ReceivePacket(&hUsbDeviceHS);
  return (USBD_OK);
  /* USER CODE END 11 */
}

/**
  * @brief  Data to send over USB IN endpoint are sent over CDC interface
  *         through this function.
  * @param  Buf: Buffer of data to be sent
  * @param  Len: Number of data to be sent (in bytes)
  * @retval Result of the operation: USBD_OK if all operations are OK else USBD_FAIL or USBD_BUSY
  */
uint8_t CDC_Transmit_HS(uint8_t* Buf, uint16_t Len)
{
  uint8_t result = USBD_OK;
  /* USER CODE BEGIN 12 */
  USBD_CDC_HandleTypeDef *hcdc = (USBD_CDC_HandleTypeDef*)hUsbDeviceHS.pClassData;
  if (hcdc->TxState != 0){
    return USBD_BUSY;
  }
  USBD_CDC_SetTxBuffer(&hUsbDeviceHS, Buf, Len);
  result = USBD_CDC_TransmitPacket(&hUsbDeviceHS);
  /* USER CODE END 12 */
  return result;
}

/**
  * @brief  CDC_TransmitCplt_HS
  *         Data transmitted callback
  *
  *         @note
  *         This function is IN transfer complete callback used to inform user that
  *         the submitted Data is successfully sent over USB.
  *
  * @param  Buf: Buffer of data to be received
  * @param  Len: Number of data received (in bytes)
  * @retval Result of the operation: USBD_OK if all operations are OK else USBD_FAIL
  */
static int8_t CDC_TransmitCplt_HS(uint8_t *Buf, uint32_t *Len, uint8_t epnum)
{
  uint8_t result = USBD_OK;
  /* USER CODE BEGIN 14 */
  UNUSED(Buf);
  UNUSED(Len);
  UNUSED(epnum);
  /* USER CODE END 14 */
  return result;
}

/* USER CODE BEGIN PRIVATE_FUNCTIONS_IMPLEMENTATION */

// Проверка и выключение LED по таймауту (вызывается из main loop)
void CDC_LED_Process(void)
{
  if(led_off_tick != 0 && HAL_GetTick() >= led_off_tick) {
    HAL_GPIO_WritePin(Led_Test_GPIO_Port, Led_Test_Pin, GPIO_PIN_RESET); // Выключить LED
    led_off_tick = 0;
  }
}

/* USER CODE END PRIVATE_FUNCTIONS_IMPLEMENTATION */

/**
  * @}
  */

/**
  * @}
  */
