################################################################################
# Automatically-generated file. Do not edit!
# Toolchain: GNU Tools for STM32 (13.3.rel1)
################################################################################

# Add inputs and outputs from these tool invocations to the build variables 
C_SRCS += \
../Middlewares/TinyUSB/tinyusb/src/tusb.c \
../Middlewares/TinyUSB/tinyusb/src/common/tusb_fifo.c \
../Middlewares/TinyUSB/tinyusb/src/device/usbd.c \
../Middlewares/TinyUSB/tinyusb/src/device/usbd_control.c \
../Middlewares/TinyUSB/tinyusb/src/class/cdc/cdc_device.c \
../Middlewares/TinyUSB/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c \

OBJS += \
./Middlewares/TinyUSB/tinyusb/src/tusb.o \
./Middlewares/TinyUSB/tinyusb/src/common/tusb_fifo.o \
./Middlewares/TinyUSB/tinyusb/src/device/usbd.o \
./Middlewares/TinyUSB/tinyusb/src/device/usbd_control.o \
./Middlewares/TinyUSB/tinyusb/src/class/cdc/cdc_device.o \
./Middlewares/TinyUSB/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.o \

C_DEPS += \
./Middlewares/TinyUSB/tinyusb/src/tusb.d \
./Middlewares/TinyUSB/tinyusb/src/common/tusb_fifo.d \
./Middlewares/TinyUSB/tinyusb/src/device/usbd.d \
./Middlewares/TinyUSB/tinyusb/src/device/usbd_control.d \
./Middlewares/TinyUSB/tinyusb/src/class/cdc/cdc_device.d \
./Middlewares/TinyUSB/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.d \

# Общие инклюды TinyUSB
TINYUSB_INCS = -I../Middlewares/TinyUSB -I../Middlewares/TinyUSB/tinyusb/src

# Базовые флаги (повторяем из автогенерации для единообразия)
TINYUSB_CFLAGS = -mcpu=cortex-m7 -std=gnu11 -g3 -DDEBUG -DUSE_PWR_LDO_SUPPLY -DUSE_HAL_DRIVER -DSTM32H723xx -DUSE_TINYUSB -c $(TINYUSB_INCS) -I../Core/Inc -I"C://Users//TEST//STM32Cube//Repository//STM32Cube_FW_H7_V1.12.1//Drivers//STM32H7xx_HAL_Driver//Inc" -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/STM32H7xx_HAL_Driver/Inc/Legacy -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/CMSIS/Device/ST/STM32H7xx/Include -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/CMSIS/Include -I../Middlewares/TinyUSB/tinyusb/hw -I../Drivers/BSP/ST7735 -O0 -ffunction-sections -fdata-sections -Wall -fstack-usage -MMD -MP --specs=nano.specs -mfpu=fpv5-d16 -mfloat-abi=hard -mthumb

# Правила компиляции
Middlewares/TinyUSB/tinyusb/src/tusb.o: ../Middlewares/TinyUSB/tinyusb/src/tusb.c Middlewares/TinyUSB/tinyusb/src/subdir.mk
	arm-none-eabi-gcc $(TINYUSB_CFLAGS) -MF"$(@:%.o=%.d)" -MT"$@" -o "$@" "$<"
Middlewares/TinyUSB/tinyusb/src/common/tusb_fifo.o: ../Middlewares/TinyUSB/tinyusb/src/common/tusb_fifo.c Middlewares/TinyUSB/tinyusb/src/subdir.mk
	arm-none-eabi-gcc $(TINYUSB_CFLAGS) -MF"$(@:%.o=%.d)" -MT"$@" -o "$@" "$<"
Middlewares/TinyUSB/tinyusb/src/device/usbd.o: ../Middlewares/TinyUSB/tinyusb/src/device/usbd.c Middlewares/TinyUSB/tinyusb/src/subdir.mk
	arm-none-eabi-gcc $(TINYUSB_CFLAGS) -MF"$(@:%.o=%.d)" -MT"$@" -o "$@" "$<"
Middlewares/TinyUSB/tinyusb/src/device/usbd_control.o: ../Middlewares/TinyUSB/tinyusb/src/device/usbd_control.c Middlewares/TinyUSB/tinyusb/src/subdir.mk
	arm-none-eabi-gcc $(TINYUSB_CFLAGS) -MF"$(@:%.o=%.d)" -MT"$@" -o "$@" "$<"
Middlewares/TinyUSB/tinyusb/src/class/cdc/cdc_device.o: ../Middlewares/TinyUSB/tinyusb/src/class/cdc/cdc_device.c Middlewares/TinyUSB/tinyusb/src/subdir.mk
	arm-none-eabi-gcc $(TINYUSB_CFLAGS) -MF"$(@:%.o=%.d)" -MT"$@" -o "$@" "$<"
Middlewares/TinyUSB/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.o: ../Middlewares/TinyUSB/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c Middlewares/TinyUSB/tinyusb/src/subdir.mk
	arm-none-eabi-gcc $(TINYUSB_CFLAGS) -MF"$(@:%.o=%.d)" -MT"$@" -o "$@" "$<"

clean: clean-Middlewares-2f-TinyUSB-2f-tinyusb-2f-src

clean-Middlewares-2f-TinyUSB-2f-tinyusb-2f-src:
	-$(RM) ./Middlewares/TinyUSB/tinyusb/src/tusb.cyclo ./Middlewares/TinyUSB/tinyusb/src/tusb.d ./Middlewares/TinyUSB/tinyusb/src/tusb.o ./Middlewares/TinyUSB/tinyusb/src/tusb.su \
	./Middlewares/TinyUSB/tinyusb/src/common/tusb_fifo.cyclo ./Middlewares/TinyUSB/tinyusb/src/common/tusb_fifo.d ./Middlewares/TinyUSB/tinyusb/src/common/tusb_fifo.o ./Middlewares/TinyUSB/tinyusb/src/common/tusb_fifo.su \
	./Middlewares/TinyUSB/tinyusb/src/device/usbd.cyclo ./Middlewares/TinyUSB/tinyusb/src/device/usbd.d ./Middlewares/TinyUSB/tinyusb/src/device/usbd.o ./Middlewares/TinyUSB/tinyusb/src/device/usbd.su \
	./Middlewares/TinyUSB/tinyusb/src/device/usbd_control.cyclo ./Middlewares/TinyUSB/tinyusb/src/device/usbd_control.d ./Middlewares/TinyUSB/tinyusb/src/device/usbd_control.o ./Middlewares/TinyUSB/tinyusb/src/device/usbd_control.su \
	./Middlewares/TinyUSB/tinyusb/src/class/cdc/cdc_device.cyclo ./Middlewares/TinyUSB/tinyusb/src/class/cdc/cdc_device.d ./Middlewares/TinyUSB/tinyusb/src/class/cdc/cdc_device.o ./Middlewares/TinyUSB/tinyusb/src/class/cdc/cdc_device.su \
	./Middlewares/TinyUSB/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.cyclo ./Middlewares/TinyUSB/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.d ./Middlewares/TinyUSB/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.o ./Middlewares/TinyUSB/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.su

.PHONY: clean-Middlewares-2f-TinyUSB-2f-tinyusb-2f-src
