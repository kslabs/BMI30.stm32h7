################################################################################
# Automatically-generated file. Do not edit!
# Toolchain: GNU Tools for STM32 (13.3.rel1)
################################################################################

-include ../makefile.init

ifeq ($(OS),Windows_NT)
# Use PowerShell Remove-Item to handle forward slashes and multiple paths safely
RM := powershell -NoProfile -Command "& { param([string[]]$$paths) Remove-Item -Force -ErrorAction SilentlyContinue -Path $$paths }"
else
RM := rm -rf
endif

# All of the sources participating in the build are defined here
-include sources.mk
-include USB_DEVICE/Target/subdir.mk
-include USB_DEVICE/App/subdir.mk
-include Middlewares/USB_Device_Library/subdir.mk
-include Drivers/STM32H7xx_HAL_Driver/subdir.mk
-include Drivers/BSP/ST7735/subdir.mk
-include Core/Startup/subdir.mk
-include Core/Src/subdir.mk
-include objects.mk

ifneq ($(MAKECMDGOALS),clean)
ifneq ($(strip $(S_DEPS)),)
-include $(S_DEPS)
endif
ifneq ($(strip $(S_UPPER_DEPS)),)
-include $(S_UPPER_DEPS)
endif
ifneq ($(strip $(C_DEPS)),)
-include $(C_DEPS)
endif
endif

-include ../makefile.defs

OPTIONAL_TOOL_DEPS := \
$(wildcard ../makefile.defs) \
$(wildcard ../makefile.init) \
$(wildcard ../makefile.targets) \


BUILD_ARTIFACT_NAME := BMI30.stm32h7
BUILD_ARTIFACT_EXTENSION := elf
BUILD_ARTIFACT_PREFIX :=
BUILD_ARTIFACT := $(BUILD_ARTIFACT_PREFIX)$(BUILD_ARTIFACT_NAME)$(if $(BUILD_ARTIFACT_EXTENSION),.$(BUILD_ARTIFACT_EXTENSION),)

# Add inputs and outputs from these tool invocations to the build variables 
EXECUTABLES += \
BMI30.stm32h7.elf \

MAP_FILES += \
BMI30.stm32h7.map \

SIZE_OUTPUT += \
default.size.stdout \

OBJDUMP_LIST += \
BMI30.stm32h7.list \


# All Target
all: main-build

# Main-build Target
main-build: BMI30.stm32h7.elf secondary-outputs

# Tool invocations
BMI30.stm32h7.elf BMI30.stm32h7.map: $(OBJS) $(USER_OBJS) ../STM32H723VGTX_FLASH.ld makefile objects.list $(OPTIONAL_TOOL_DEPS)
	arm-none-eabi-gcc -o "BMI30.stm32h7.elf" @"objects.list" $(USER_OBJS) $(LIBS) -mcpu=cortex-m7 -T"../STM32H723VGTX_FLASH.ld" --specs=nosys.specs -Wl,-Map="BMI30.stm32h7.map" -Wl,--gc-sections -static --specs=nano.specs -mfpu=fpv5-d16 -mfloat-abi=hard -mthumb -Wl,--start-group -lc -lm -Wl,--end-group
	@echo 'Finished building target: $@'
	@echo ' '

default.size.stdout: $(EXECUTABLES) makefile objects.list $(OPTIONAL_TOOL_DEPS)
	arm-none-eabi-size  $(EXECUTABLES)
	@echo 'Finished building: $@'
	@echo ' '

BMI30.stm32h7.list: $(EXECUTABLES) makefile objects.list $(OPTIONAL_TOOL_DEPS)
	arm-none-eabi-objdump -h -S $(EXECUTABLES) > "BMI30.stm32h7.list"
	@echo 'Finished building: $@'
	@echo ' '

# Other Targets
clean:
	-$(RM) BMI30.stm32h7.elf BMI30.stm32h7.list BMI30.stm32h7.map default.size.stdout
	-@echo ' '

secondary-outputs: $(SIZE_OUTPUT) $(OBJDUMP_LIST)

# Full flash (mass erase + program + verify + reset)
flash_full: main-build
	@echo 'Flashing with mass erase...'
	C:/Users/TEST/Documents/Work/BMI20/STM32/xpack-openocd-0.12.0-2-win32-x64/xpack-openocd-0.12.0-2/bin/openocd.exe -s C:/Users/TEST/Documents/Work/BMI20/STM32/xpack-openocd-0.12.0-2-win32-x64/xpack-openocd-0.12.0-2/scripts -f interface/stlink.cfg -f target/stm32h7x.cfg -c "init; halt; stm32h7x mass_erase 0; program {$(CURDIR)/BMI30.stm32h7.elf} verify reset exit"
	@echo 'Flash done.'

fail-specified-linker-script-missing:
	@echo 'Error: Cannot find the specified linker script. Check the linker settings in the build configuration.'
	@exit 2

warn-no-linker-script-specified:
	@echo 'Warning: No linker script specified. Check the linker settings in the build configuration.'

.PHONY: all clean dependents main-build fail-specified-linker-script-missing warn-no-linker-script-specified

-include ../makefile.targets
