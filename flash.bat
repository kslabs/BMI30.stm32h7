@echo off
cd /d "D:\Users\Admin\Documents\Work\BMI20\STM32\BMI30.stm32h7\Debug"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\Users\Admin\Documents\Work\BMI20\STM32\BMI30.stm32h7\.vscode\flash-openocd.ps1" -Elf "D:\Users\Admin\Documents\Work\BMI20\STM32\BMI30.stm32h7\Debug\BMI30.stm32h7.elf"
pause
