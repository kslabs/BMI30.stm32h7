# Monitor COM9 for PERF metrics during long USB test
# Usage: .\monitor-com9-during-test.ps1 -TestDurationSec 20

param(
    [int]$TestDurationSec = 20,
    [int]$Pairs = 200
)

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "LONG USB TEST WITH COM9 MONITORING" -ForegroundColor Cyan
Write-Host "Test duration: $TestDurationSec sec, Pairs: $Pairs" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# Start USB test in background
Write-Host "[INFO] Starting USB test in background..." -ForegroundColor Green
$usbJob = Start-Job -ScriptBlock {
    param($pairs, $window)
    Set-Location "C:\Users\TEST\Documents\Work\BMI20\STM32\BMI30.stm32h7"
    py -3 HostTools/vendor_usb_start_and_read.py --pairs $pairs --window-sec $window 2>&1
} -ArgumentList $Pairs, $TestDurationSec

Write-Host "[INFO] USB test job started (ID: $($usbJob.Id))" -ForegroundColor Green
Write-Host "[INFO] Waiting 2 seconds for USB enumeration..." -ForegroundColor Yellow
Start-Sleep -Seconds 2

# Monitor COM9 for PERF output
Write-Host "`n[INFO] Opening COM9 to monitor PERF metrics..." -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Cyan

$port = New-Object System.IO.Ports.SerialPort
$port.PortName = "COM9"
$port.BaudRate = 115200
$port.DataBits = 8
$port.Parity = [System.IO.Ports.Parity]::None
$port.StopBits = [System.IO.Ports.StopBits]::One
$port.ReadTimeout = 500

$perfLines = @()
$rateLines = @()
$startTime = Get-Date

try {
    $port.Open()
    Write-Host "[COM9] Port opened successfully" -ForegroundColor Green
    
    $monitorDuration = $TestDurationSec + 5
    while (((Get-Date) - $startTime).TotalSeconds -lt $monitorDuration) {
        try {
            if ($port.BytesToRead -gt 0) {
                $line = $port.ReadLine()
                
                if ($line -match '\[PERF\]') {
                    Write-Host $line -ForegroundColor Magenta
                    $perfLines += $line
                }
                elseif ($line -match '\[RATE\]') {
                    Write-Host $line -ForegroundColor Green
                    $rateLines += $line
                }
                elseif ($line -match 'START_STREAM|STOP_STREAM|EVT') {
                    Write-Host $line -ForegroundColor Yellow
                }
                # Suppress other output during monitoring
            }
        }
        catch [System.TimeoutException] {
            # Read timeout is normal
        }
        Start-Sleep -Milliseconds 10
    }
}
catch {
    Write-Host "[COM9][ERR] $_" -ForegroundColor Red
}
finally {
    if ($port.IsOpen) {
        $port.Close()
        Write-Host "[COM9] Port closed" -ForegroundColor Green
    }
}

Write-Host "`n==================================================================" -ForegroundColor Cyan
Write-Host "COM9 MONITORING COMPLETE" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# Wait for USB test to finish
Write-Host "`n[INFO] Waiting for USB test to complete..." -ForegroundColor Green
$usbResult = Wait-Job -Job $usbJob -Timeout 30
$usbOutput = Receive-Job -Job $usbJob

Write-Host "`n==================================================================" -ForegroundColor Cyan
Write-Host "USB TEST OUTPUT (last 30 lines):" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
$usbOutput | Select-Object -Last 30 | ForEach-Object {
    Write-Host $_ -ForegroundColor Yellow
}

# Summary
Write-Host "`n==================================================================" -ForegroundColor Cyan
Write-Host "SUMMARY" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "PERF lines captured: $($perfLines.Count)" -ForegroundColor Cyan
Write-Host "RATE lines captured: $($rateLines.Count)" -ForegroundColor Cyan

if ($perfLines.Count -gt 0) {
    Write-Host "`nLast 5 PERF lines:" -ForegroundColor Cyan
    $perfLines | Select-Object -Last 5 | ForEach-Object {
        Write-Host "  $_" -ForegroundColor Magenta
    }
}

if ($rateLines.Count -gt 0) {
    Write-Host "`nAll RATE lines:" -ForegroundColor Cyan
    $rateLines | ForEach-Object {
        Write-Host "  $_" -ForegroundColor Green
    }
}

# Cleanup
Remove-Job -Job $usbJob -Force
Write-Host "`n[INFO] Test complete!" -ForegroundColor Green
