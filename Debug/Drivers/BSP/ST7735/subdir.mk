################################################################################
# Automatically-generated file. Do not edit!
# Toolchain: GNU Tools for STM32 (13.3.rel1)
################################################################################

# Add inputs and outputs from these tool invocations to the build variables 
C_SRCS += \
../Drivers/BSP/ST7735/board.c \
../Drivers/BSP/ST7735/font.c \
../Drivers/BSP/ST7735/lcd.c \
../Drivers/BSP/ST7735/st7735.c 

OBJS += \
./Drivers/BSP/ST7735/board.o \
./Drivers/BSP/ST7735/font.o \
./Drivers/BSP/ST7735/lcd.o \
./Drivers/BSP/ST7735/st7735.o 

C_DEPS += \
./Drivers/BSP/ST7735/board.d \
./Drivers/BSP/ST7735/font.d \
./Drivers/BSP/ST7735/lcd.d \
./Drivers/BSP/ST7735/st7735.d 


# Each subdirectory must supply rules for building sources it contributes
Drivers/BSP/ST7735/%.o Drivers/BSP/ST7735/%.su Drivers/BSP/ST7735/%.cyclo: ../Drivers/BSP/ST7735/%.c Drivers/BSP/ST7735/subdir.mk
	arm-none-eabi-gcc "$<" -mcpu=cortex-m7 -std=gnu11 -g3 -DDEBUG -DUSE_PWR_LDO_SUPPLY -DUSE_HAL_DRIVER -DSTM32H723xx -c -I../Core/Inc -I"C://Users//TEST//STM32Cube//Repository//STM32Cube_FW_H7_V1.12.1//Drivers//STM32H7xx_HAL_Driver//Inc" -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/STM32H7xx_HAL_Driver/Inc/Legacy -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/CMSIS/Device/ST/STM32H7xx/Include -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/CMSIS/Include -I../Drivers/BSP/ST7735 -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/STM32H7xx_HAL_Driver/Inc -I../USB_DEVICE/App -I../USB_DEVICE/Target -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Middlewares/ST/STM32_USB_Device_Library/Core/Inc -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Middlewares/ST/STM32_USB_Device_Library/Class/CDC/Inc -O0 -ffunction-sections -fdata-sections -Wall -fstack-usage -MMD -MP -MF"$(@:%.o=%.d)" -MT"$@" --specs=nano.specs -mfpu=fpv5-d16 -mfloat-abi=hard -mthumb -o "$@"

clean: clean-Drivers-2f-BSP-2f-ST7735

clean-Drivers-2f-BSP-2f-ST7735:
	-$(RM) ./Drivers/BSP/ST7735/board.cyclo ./Drivers/BSP/ST7735/board.d ./Drivers/BSP/ST7735/board.o ./Drivers/BSP/ST7735/board.su ./Drivers/BSP/ST7735/font.cyclo ./Drivers/BSP/ST7735/font.d ./Drivers/BSP/ST7735/font.o ./Drivers/BSP/ST7735/font.su ./Drivers/BSP/ST7735/lcd.cyclo ./Drivers/BSP/ST7735/lcd.d ./Drivers/BSP/ST7735/lcd.o ./Drivers/BSP/ST7735/lcd.su ./Drivers/BSP/ST7735/st7735.cyclo ./Drivers/BSP/ST7735/st7735.d ./Drivers/BSP/ST7735/st7735.o ./Drivers/BSP/ST7735/st7735.su

.PHONY: clean-Drivers-2f-BSP-2f-ST7735

