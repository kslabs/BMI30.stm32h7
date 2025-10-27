param(
    [string]$Port = "COM9",
    [int]$Baud = 115200
)

try {
    $serialPort = New-Object System.IO.Ports.SerialPort
    $serialPort.PortName = $Port
    $serialPort.BaudRate = $Baud
    $serialPort.DataBits = 8
    $serialPort.Parity = [System.IO.Ports.Parity]::None
    $serialPort.StopBits = [System.IO.Ports.StopBits]::One
    $serialPort.ReadTimeout = 1000
    $serialPort.WriteTimeout = 1000
    
    Write-Host "[Serial-Reset] Opening $Port at $Baud baud..."
    $serialPort.Open()
    
    # Отправляем команду 0x50 (Software Reset) - если такая команда есть
    # Или просто текстовую команду "RESET"
    $serialPort.WriteLine("RESET")
    Write-Host "[Serial-Reset] Sent RESET command to $Port"
    
    Start-Sleep -Milliseconds 500
    
    $serialPort.Close()
    Write-Host "[Serial-Reset] Port closed"
    exit 0
}
catch {
    Write-Host "[Serial-Reset] ERROR: $_" -ForegroundColor Red
    if ($serialPort -and $serialPort.IsOpen) {
        $serialPort.Close()
    }
    exit 1
}
