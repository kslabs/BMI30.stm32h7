#!/usr/bin/env powershell
<#
.SYNOPSIS
    Захват загрузочных логов устройства через COM4
.DESCRIPTION
    Открывает COM4, отправляет команду RESET, затем читает и выводит загрузочные логи
#>

param(
    [string]$Port = "COM4",
    [int]$Baud = 115200,
    [int]$CaptureSec = 20
)

try {
    $serialPort = New-Object System.IO.Ports.SerialPort
    $serialPort.PortName = $Port
    $serialPort.BaudRate = $Baud
    $serialPort.DataBits = 8
    $serialPort.Parity = [System.IO.Ports.Parity]::None
    $serialPort.StopBits = [System.IO.Ports.StopBits]::One
    $serialPort.ReadTimeout = 500
    $serialPort.WriteTimeout = 1000
    
    Write-Host "[CAPTURE] Opening $Port at $Baud baud..." -ForegroundColor Yellow
    $serialPort.Open()
    
    Start-Sleep -Milliseconds 100
    
    # Очистка буфера
    if ($serialPort.BytesToRead -gt 0) {
        $null = $serialPort.ReadExisting()
    }
    
    # Отправка команды RESET
    Write-Host "[CAPTURE] Sending RESET command..." -ForegroundColor Yellow
    $serialPort.WriteLine("RESET")
    $serialPort.BaseStream.Flush()
    
    Write-Host "[CAPTURE] Capturing boot logs for $CaptureSec seconds..." -ForegroundColor Cyan
    Write-Host "==================== BOOT LOG START ====================" -ForegroundColor Green
    
    $timeout = (Get-Date).AddSeconds($CaptureSec)
    while ((Get-Date) -lt $timeout) {
        try {
            if ($serialPort.BytesToRead -gt 0) {
                $data = $serialPort.ReadExisting()
                Write-Host $data -NoNewline
            } else {
                Start-Sleep -Milliseconds 30
            }
        } catch {
            # Игнорируем таймауты чтения
        }
    }
    
    Write-Host ""
    Write-Host "==================== BOOT LOG END   ====================" -ForegroundColor Green
    
    $serialPort.Close()
    Write-Host "[CAPTURE] Port closed" -ForegroundColor Yellow
    exit 0
}
catch {
    Write-Host "[CAPTURE] ERROR: $_" -ForegroundColor Red
    if ($serialPort -and $serialPort.IsOpen) {
        $serialPort.Close()
    }
    exit 1
}
