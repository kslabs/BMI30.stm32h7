cd "c:\Users\TEST\Documents\Work\BMI20\STM32\BMI30.stm32h7\Debug"
& "c:\Users\TEST\Documents\Work\BMI20\STM32\xpack-openocd-0.12.0-2-win32-x64\xpack-openocd-0.12.0-2\bin\openocd.exe" -s "c:/Users/TEST/Documents/Work/BMI20/STM32/xpack-openocd-0.12.0-2-win32-x64/xpack-openocd-0.12.0-2/scripts" -f interface/stlink.cfg -f target/stm32h7x.cfg -c "program {BMI30.stm32h7.elf} verify reset exit"
