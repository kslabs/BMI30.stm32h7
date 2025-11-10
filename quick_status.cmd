@echo off
setlocal
set SCRIPT=%~dp0HostTools\vendor_quick_status.py
if not exist "%SCRIPT%" (
  echo Script not found: %SCRIPT%
  exit /b 1
)
if "%~1"=="" (
  set ARGS=--secs 10
) else (
  set ARGS=%*
)
py -3 -u "%SCRIPT%" %ARGS%
