# Окружение сборки/прошивки

- MCU: STM32H723 (серия STM32H7, плата BMI30.stm32h7)
- Toolchain: xPack GNU Arm Embedded Toolchain 14.2.1-1.1 (arm-none-eabi-gcc)
- Build system: GNU Make (CubeIDE‑генерируемый Debug/Makefile)
- CubeMX: конфигурация в BMI30.stm32h7.ioc
- Flashing (основной путь): STM32CubeProgrammer CLI
  - Путь по умолчанию: C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe
- Flashing (альтернативно): OpenOCD 0.12.0 (xPack)
- OS: Windows 10/11 (проверено). Linux/Raspberry Pi OS — требует аналогичный toolchain и OpenOCD/STM32CubeProgrammer.

## Примечания
- Проект хранится в Git, без внешней облачной синхронизации.
- Артефакты сборки и локальные логи исключены через .gitignore.
