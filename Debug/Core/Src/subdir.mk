################################################################################
# Automatically-generated file. Do not edit!
# Toolchain: GNU Tools for STM32 (13.3.rel1)
################################################################################

# Add inputs and outputs from these tool invocations to the build variables 
C_SRCS += \
../Core/Src/adc_stream.c \
../Core/Src/main.c \
../Core/Src/build_info.c \
../Core/Src/spi2_fix.c \
../Core/Src/stm32h7xx_hal_msp.c \
../Core/Src/stm32h7xx_it.c \
../Core/Src/syscalls.c \
../Core/Src/sysmem.c \
../Core/Src/system_stm32h7xx.c \
../Core/Src/tinyusb_all.c \
../Core/Src/tinyusb_stubs.c \
../Core/Src/usb_cdc_ll_st.c \
../Core/Src/usb_cdc_proto.c \
../Core/Src/usb_descriptors.c \
../Core/Src/usb_descriptors_tusb.c \
../Core/Src/usb_stream.c \
../Core/Src/usb_tinyusb_bridge.c \
../Core/Src/usbd_cdc.c

OBJS += \
./Core/Src/adc_stream.o \
./Core/Src/main.o \
./Core/Src/build_info.o \
./Core/Src/spi2_fix.o \
./Core/Src/stm32h7xx_hal_msp.o \
./Core/Src/stm32h7xx_it.o \
./Core/Src/syscalls.o \
./Core/Src/sysmem.o \
./Core/Src/system_stm32h7xx.o \
./Core/Src/tinyusb_all.o \
./Core/Src/tinyusb_stubs.o \
./Core/Src/usb_cdc_ll_st.o \
./Core/Src/usb_cdc_proto.o \
./Core/Src/usb_descriptors.o \
./Core/Src/usb_descriptors_tusb.o \
./Core/Src/usb_stream.o \
./Core/Src/usb_tinyusb_bridge.o \
./Core/Src/usbd_cdc.o

C_DEPS += \
./Core/Src/adc_stream.d \
./Core/Src/main.d \
./Core/Src/build_info.d \
./Core/Src/spi2_fix.d \
./Core/Src/stm32h7xx_hal_msp.d \
./Core/Src/stm32h7xx_it.d \
./Core/Src/syscalls.d \
./Core/Src/sysmem.d \
./Core/Src/system_stm32h7xx.d \
./Core/Src/tinyusb_all.d \
./Core/Src/tinyusb_stubs.d \
./Core/Src/usb_cdc_ll_st.d \
./Core/Src/usb_cdc_proto.d \
./Core/Src/usb_descriptors.d \
./Core/Src/usb_descriptors_tusb.d \
./Core/Src/usb_stream.d \
./Core/Src/usb_tinyusb_bridge.d \
./Core/Src/usbd_cdc.d


# Each subdirectory must supply rules for building sources it contributes
Core/Src/%.o Core/Src/%.su Core/Src/%.cyclo: ../Core/Src/%.c Core/Src/subdir.mk
	arm-none-eabi-gcc "$<" -mcpu=cortex-m7 -std=gnu11 -g3 -DDEBUG -DUSE_PWR_LDO_SUPPLY -DUSE_HAL_DRIVER -DSTM32H723xx -c -I../Core/Inc -I"C://Users//TEST//STM32Cube//Repository//STM32Cube_FW_H7_V1.12.1//Drivers//STM32H7xx_HAL_Driver//Inc" -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/STM32H7xx_HAL_Driver/Inc/Legacy -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/CMSIS/Device/ST/STM32H7xx/Include -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/CMSIS/Include -I../Drivers/BSP/ST7735 -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Drivers/STM32H7xx_HAL_Driver/Inc -I../USB_DEVICE/App -I../USB_DEVICE/Target -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Middlewares/ST/STM32_USB_Device_Library/Core/Inc -IC:/Users/TEST/STM32Cube/Repository/STM32Cube_FW_H7_V1.12.1/Middlewares/ST/STM32_USB_Device_Library/Class/CDC/Inc -O0 -ffunction-sections -fdata-sections -Wall -fstack-usage -MMD -MP -MF"$(@:%.o=%.d)" -MT"$@" --specs=nano.specs -mfpu=fpv5-d16 -mfloat-abi=hard -mthumb -o "$@"

clean: clean-Core-2f-Src

clean-Core-2f-Src:
	-$(RM) ./Core/Src/adc_stream.cyclo ./Core/Src/adc_stream.d ./Core/Src/adc_stream.o ./Core/Src/adc_stream.su ./Core/Src/main.cyclo ./Core/Src/main.d ./Core/Src/main.o ./Core/Src/main.su ./Core/Src/spi2_fix.cyclo ./Core/Src/spi2_fix.d ./Core/Src/spi2_fix.o ./Core/Src/spi2_fix.su ./Core/Src/stm32h7xx_hal_msp.cyclo ./Core/Src/stm32h7xx_hal_msp.d ./Core/Src/stm32h7xx_hal_msp.o ./Core/Src/stm32h7xx_hal_msp.su ./Core/Src/stm32h7xx_it.cyclo ./Core/Src/stm32h7xx_it.d ./Core/Src/stm32h7xx_it.o ./Core/Src/stm32h7xx_it.su ./Core/Src/syscalls.cyclo ./Core/Src/syscalls.d ./Core/Src/syscalls.o ./Core/Src/syscalls.su ./Core/Src/sysmem.cyclo ./Core/Src/sysmem.d ./Core/Src/sysmem.o ./Core/Src/sysmem.su ./Core/Src/system_stm32h7xx.cyclo ./Core/Src/system_stm32h7xx.d ./Core/Src/system_stm32h7xx.o ./Core/Src/system_stm32h7xx.su ./Core/Src/tinyusb_all.cyclo ./Core/Src/tinyusb_all.d ./Core/Src/tinyusb_all.o ./Core/Src/tinyusb_all.su ./Core/Src/tinyusb_stubs.cyclo ./Core/Src/tinyusb_stubs.d ./Core/Src/tinyusb_stubs.o ./Core/Src/tinyusb_stubs.su ./Core/Src/usb_cdc_ll_st.cyclo ./Core/Src/usb_cdc_ll_st.d ./Core/Src/usb_cdc_ll_st.o ./Core/Src/usb_cdc_ll_st.su ./Core/Src/usb_cdc_proto.cyclo ./Core/Src/usb_cdc_proto.d ./Core/Src/usb_cdc_proto.o ./Core/Src/usb_cdc_proto.su ./Core/Src/usb_descriptors.cyclo ./Core/Src/usb_descriptors.d ./Core/Src/usb_descriptors.o ./Core/Src/usb_descriptors.su ./Core/Src/usb_descriptors_tusb.cyclo ./Core/Src/usb_descriptors_tusb.d ./Core/Src/usb_descriptors_tusb.o ./Core/Src/usb_descriptors_tusb.su ./Core/Src/usb_stream.cyclo ./Core/Src/usb_stream.d ./Core/Src/usb_stream.o ./Core/Src/usb_stream.su ./Core/Src/usb_tinyusb_bridge.cyclo ./Core/Src/usb_tinyusb_bridge.d ./Core/Src/usb_tinyusb_bridge.o ./Core/Src/usb_tinyusb_bridge.su ./Core/Src/usbd_cdc.cyclo ./Core/Src/usbd_cdc.d ./Core/Src/usbd_cdc.o ./Core/Src/usbd_cdc.su

.PHONY: clean-Core-2f-Src

