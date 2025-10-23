@echo off
cd /d "c:\Users\TEST\Documents\Work\BMI20\STM32\BMI30.stm32h7"
"c:\Users\TEST\Documents\Work\BMI20\STM32\xpack-openocd-0.12.0-2-win32-x64\xpack-openocd-0.12.0-2-win32-x64\bin\openocd.exe" -s "c:/Users/TEST/Documents/Work/BMI20/STM32/xpack-openocd-0.12.0-2-win32-x64/xpack-openocd-0.12.0-2-win32-x64/scripts" -f interface/stlink.cfg -f target/stm32h7x.cfg -c "program {C:\Users\TEST\Documents\Work\BMI20\STM32\BMI30.stm32h7\Debug\BMI30.stm32h7.elf} verify reset exit"
pause
