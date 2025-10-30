# Run USB test and monitor COM9 simultaneously
# Shows performance metrics from firmware during transmission

param(
    [int]$TestDurationSec = 10
)

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "USB TEST + COM9 MONITOR" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# Start COM9 monitor in background job
$comJob = Start-Job -ScriptBlock {
    param($duration)
    
    $port = New-Object System.IO.Ports.SerialPort
    $port.PortName = "COM9"
    $port.BaudRate = 115200
    $port.DataBits = 8
    $port.Parity = [System.IO.Ports.Parity]::None
    $port.StopBits = [System.IO.Ports.StopBits]::One
    $port.Handshake = [System.IO.Ports.Handshake]::None
    $port.ReadTimeout = 500
    $port.WriteTimeout = 500
    
    try {
        $port.Open()
        Write-Output "[COM9] Port opened"
        
        $startTime = Get-Date
        while (((Get-Date) - $startTime).TotalSeconds -lt $duration) {
            if ($port.BytesToRead -gt 0) {
                $line = $port.ReadLine()
                Write-Output "[COM9] $line"
            }
            Start-Sleep -Milliseconds 10
        }
    }
    catch {
        Write-Output "[COM9][ERR] $_"
    }
    finally {
        if ($port.IsOpen) {
            $port.Close()
        }
    }
} -ArgumentList $TestDurationSec

Write-Host "[INFO] COM9 monitor started (job ID: $($comJob.Id))" -ForegroundColor Green
Start-Sleep -Seconds 1

# Run USB test
Write-Host "[INFO] Starting USB test..." -ForegroundColor Green
$testStart = Get-Date
py -3 HostTools/vendor_usb_start_and_read.py --pairs 20 --window-sec $TestDurationSec 2>&1 | ForEach-Object {
    Write-Host "[HOST] $_" -ForegroundColor Yellow
}
$testDuration = ((Get-Date) - $testStart).TotalSeconds

Write-Host "[INFO] USB test completed in $([math]::Round($testDuration, 2))s" -ForegroundColor Green

# Wait for COM9 job to finish
Write-Host "[INFO] Waiting for COM9 monitor to finish..." -ForegroundColor Green
Wait-Job -Job $comJob -Timeout ($TestDurationSec + 5) | Out-Null

# Get COM9 output
Write-Host "`n==================================================================" -ForegroundColor Cyan
Write-Host "COM9 OUTPUT:" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Receive-Job -Job $comJob | ForEach-Object {
    if ($_ -match '\[PERF\]') {
        Write-Host $_ -ForegroundColor Magenta
    } elseif ($_ -match '\[RATE\]') {
        Write-Host $_ -ForegroundColor Green
    } else {
        Write-Host $_
    }
}

# Cleanup
Remove-Job -Job $comJob -Force

Write-Host "`n==================================================================" -ForegroundColor Cyan
Write-Host "TEST COMPLETE" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
