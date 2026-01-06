@echo off
cd /d c:\Users\TEST\Documents\Work\BMI20\STM32\BMI30.stm32h7
py -3 HostTools\vendor_ctrl_start_only.py --async 1 --chmode 0 --full 1 --profile 0
timeout /t 1 /nobreak >nul
py -3 read_uart_debug.py
