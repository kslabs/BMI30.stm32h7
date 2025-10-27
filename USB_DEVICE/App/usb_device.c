/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : usb_device.c
  * @version        : v1.0_Cube
  * @brief          : This file implements the USB Device
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

#include "usb_device.h"
#include "usbd_core.h"
#include "usbd_desc.h"
#include "usbd_cdc.h"
#include "usbd_cdc_if.h"
#include "usbd_cdc_custom.h"  /* Composite CDC+Vendor class (adds IF2 with EP 0x03/0x83) */

/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* USER CODE BEGIN PV */
/* Private variables ---------------------------------------------------------*/

/* USER CODE END PV */

/* USER CODE BEGIN PFP */
/* Private function prototypes -----------------------------------------------*/

/* USER CODE END PFP */

/* USB Device Core handle declaration. */
USBD_HandleTypeDef hUsbDeviceHS;

/*
 * -- Insert your variables declaration here --
 */
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/*
 * -- Insert your external function declaration here --
 */
/* USER CODE BEGIN 1 */

/* USER CODE END 1 */

/**
  * Init USB device Library, add supported class and start the library
  * @retval None
  */
void MX_USB_DEVICE_Init(void)
{
  /* USER CODE BEGIN USB_DEVICE_Init_PreTreatment */

  /* USER CODE END USB_DEVICE_Init_PreTreatment */

  uint32_t now_ms = HAL_GetTick();
  uint32_t mins = (now_ms / 1000) / 60;
  uint32_t secs = (now_ms / 1000) % 60;
  uint32_t ms   = now_ms % 1000;
  
  printf("[%02lu:%02lu.%03lu][USB_INIT] Starting MX_USB_DEVICE_Init...\r\n", mins, secs, ms);

  /* Init Device Library, add supported class and start the library. */
  if (USBD_Init(&hUsbDeviceHS, &HS_Desc, DEVICE_HS) != USBD_OK)
  {
    printf("[%02lu:%02lu.%03lu][USB_INIT] USBD_Init FAILED\r\n", mins, secs, ms);
    Error_Handler();
  }
  printf("[%02lu:%02lu.%03lu][USB_INIT] USBD_Init OK\r\n", mins, secs, ms);

  /* Register composite class: CDC (IF0/1) + Vendor (IF2) */
  if (USBD_RegisterClass(&hUsbDeviceHS, &USBD_CDC_VENDOR) != USBD_OK)
  {
    printf("[%02lu:%02lu.%03lu][USB_INIT] USBD_RegisterClass FAILED\r\n", mins, secs, ms);
    Error_Handler();
  }
  printf("[%02lu:%02lu.%03lu][USB_INIT] USBD_RegisterClass OK\r\n", mins, secs, ms);

  if (USBD_CDC_RegisterInterface(&hUsbDeviceHS, &USBD_Interface_fops_HS) != USBD_OK)
  {
    printf("[%02lu:%02lu.%03lu][USB_INIT] USBD_CDC_RegisterInterface FAILED\r\n", mins, secs, ms);
    Error_Handler();
  }
  printf("[%02lu:%02lu.%03lu][USB_INIT] USBD_CDC_RegisterInterface OK\r\n", mins, secs, ms);

  if (USBD_Start(&hUsbDeviceHS) != USBD_OK)
  {
    printf("[%02lu:%02lu.%03lu][USB_INIT] USBD_Start FAILED\r\n", mins, secs, ms);
    Error_Handler();
  }
  printf("[%02lu:%02lu.%03lu][USB_INIT] USBD_Start OK - device should enumerate now\r\n", mins, secs, ms);

  /* USER CODE BEGIN USB_DEVICE_Init_PostTreatment */
  HAL_PWREx_EnableUSBVoltageDetector();

  /* USER CODE END USB_DEVICE_Init_PostTreatment */
}

/**
  * @}
  */

/**
  * @}
  */

