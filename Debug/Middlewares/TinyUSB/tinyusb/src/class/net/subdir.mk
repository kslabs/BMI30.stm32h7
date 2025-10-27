################################################################################
# Automatically-generated file. Do not edit!
# Toolchain: GNU Tools for STM32 (13.3.rel1)
################################################################################

# Add inputs and outputs from these tool invocations to the build variables 
C_SRCS += \
../Middlewares/TinyUSB/tinyusb/src/class/net/ecm_rndis_device.c \
../Middlewares/TinyUSB/tinyusb/src/class/net/ncm_device.c 

OBJS += \
./Middlewares/TinyUSB/tinyusb/src/class/net/ecm_rndis_device.o \
./Middlewares/TinyUSB/tinyusb/src/class/net/ncm_device.o 

C_DEPS += \
./Middlewares/TinyUSB/tinyusb/src/class/net/ecm_rndis_device.d \
./Middlewares/TinyUSB/tinyusb/src/class/net/ncm_device.d 


# Each subdirectory must supply rules for building sources it contributes
Middlewares/TinyUSB/tinyusb/src/class/net/%.o Middlewares/TinyUSB/tinyusb/src/class/net/%.su Middlewares/TinyUSB/tinyusb/src/class/net/%.cyclo: ../Middlewares/TinyUSB/tinyusb/src/class/net/%.c Middlewares/TinyUSB/tinyusb/src/class/net/subdir.mk
	arm-none-eabi-gcc "$<" -mcpu=cortex-m7 -std=gnu11 -g3 -DDEBUG -DUSE_PWR_LDO_SUPPLY -DUSE_HAL_DRIVER -DSTM32H723xx -DUSE_TINYUSB -c -I../Core/Inc -I"C://Users//TEST//STM32Cube//Repository//STM32Cube_FW_H7_V1.12.1//Drivers//STM32H7xx_HAL_Driver//Inc" -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/STM32H7xx_HAL_Driver/Inc/Legacy -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/CMSIS/Device/ST/STM32H7xx/Include -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/CMSIS/Include -I../Middlewares/TinyUSB/tinyusb/src -I../Middlewares/TinyUSB/tinyusb/hw -I../Drivers/BSP/ST7735 -O0 -ffunction-sections -fdata-sections -Wall -fstack-usage -MMD -MP -MF"$(@:%.o=%.d)" -MT"$@" --specs=nano.specs -mfpu=fpv5-d16 -mfloat-abi=hard -mthumb -o "$@"

clean: clean-Middlewares-2f-TinyUSB-2f-tinyusb-2f-src-2f-class-2f-net

clean-Middlewares-2f-TinyUSB-2f-tinyusb-2f-src-2f-class-2f-net:
	-$(RM) ./Middlewares/TinyUSB/tinyusb/src/class/net/ecm_rndis_device.cyclo ./Middlewares/TinyUSB/tinyusb/src/class/net/ecm_rndis_device.d ./Middlewares/TinyUSB/tinyusb/src/class/net/ecm_rndis_device.o ./Middlewares/TinyUSB/tinyusb/src/class/net/ecm_rndis_device.su ./Middlewares/TinyUSB/tinyusb/src/class/net/ncm_device.cyclo ./Middlewares/TinyUSB/tinyusb/src/class/net/ncm_device.d ./Middlewares/TinyUSB/tinyusb/src/class/net/ncm_device.o ./Middlewares/TinyUSB/tinyusb/src/class/net/ncm_device.su

.PHONY: clean-Middlewares-2f-TinyUSB-2f-tinyusb-2f-src-2f-class-2f-net

