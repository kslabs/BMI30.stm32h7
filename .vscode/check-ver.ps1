param(
  [string]$Port = "COM11",
  [int]$Baud = 115200,
  [int]$OpenTimeoutSec = 15,
  [int]$ReadTimeoutSec = 5,
  [string]$OutPath = ".vscode/ver_out.txt",
  [string]$ExpectGit = "",
  [switch]$FallbackUart
)

# Open CDC port, send VER, capture and print version block; warn on stale or mismatched info.
$ErrorActionPreference = 'Stop'

function New-SerialPort([string]$p, [int]$b){
  $sp = New-Object System.IO.Ports.SerialPort $p, $b, 'None', 8, 'One'
  $sp.Handshake = [System.IO.Ports.Handshake]::None
  $sp.NewLine = "`r`n"
  $sp.ReadTimeout = 500
  $sp.WriteTimeout = 500
  return $sp
}

# Try to open port with retries
$sp = $null
$deadline = (Get-Date).AddSeconds($OpenTimeoutSec)
while (-not $sp -and (Get-Date) -lt $deadline) {
  try {
    $sp = New-SerialPort -p $Port -b $Baud
    $sp.Open()
  } catch {
    Start-Sleep -Milliseconds 300
    $sp = $null
  }
}

if (-not $sp -or -not $sp.IsOpen) {
  Write-Error ("[VER] Failed to open {0} within {1}s" -f $Port, $OpenTimeoutSec)
  exit 2
}

try {
  Write-Host ("[VER] Opened {0} @ {1}" -f $Port, $Baud)
  # Flush any pending data
  try {
    while ($sp.BytesToRead -gt 0) { [void]$sp.ReadExisting() }
  } catch {}

  # Send ASCII command
  $cmd = "VER`r`n"
  $sp.Write($cmd)
  $sp.BaseStream.Flush()
  Write-Host ("[VER] Sent: {0}" -f ($cmd.Trim()))

  # Read lines for up to ReadTimeoutSec seconds, collect block
  $t0 = Get-Date
  $lines = @()
  $inBlock = $false
  while (((Get-Date) - $t0).TotalSeconds -lt $ReadTimeoutSec) {
    try {
      if ($sp.BytesToRead -le 0) { Start-Sleep -Milliseconds 50; continue }
      $line = $sp.ReadLine()
      if ($line -match '^===\s*FIRMWARE VERSION') { $inBlock = $true }
      if ($inBlock) { $lines += $line }
      if ($inBlock -and $line -match '^=+') { break }
    } catch {
      # Ignore timeouts during window
    }
  }

    if (-not $lines -or $lines.Count -lt 2) {
      Write-Warning "[VER] No version block captured on CDC."
      $needFallback = $FallbackUart.IsPresent
    } else {
    Write-Host "[VER] ------------------------------"
    $lines | ForEach-Object { Write-Host $_ }
    Write-Host "[VER] ------------------------------"

    # Parse fields
    $ver = ($lines | Where-Object { $_ -match '^Version:\s*(.+)$' } | ForEach-Object { ($Matches[1]).Trim() } | Select-Object -First 1)
    $git = ($lines | Where-Object { $_ -match '^Git:\s*(.+)$' } | ForEach-Object { ($Matches[1]).Trim() } | Select-Object -First 1)
    $built = ($lines | Where-Object { $_ -match '^Built:\s*(.+)$' } | ForEach-Object { ($Matches[1]).Trim() } | Select-Object -First 1)

    # Save to file
    try {
      $out = @()
      $out += "Port: $Port"
      $out += "Version: $ver"
      $out += "Git: $git"
      $out += "Built: $built"
      $out | Set-Content -Path $OutPath -Encoding UTF8
      Write-Host ("[VER] Saved to {0}" -f $OutPath)
    } catch {
      Write-Warning ("[VER] Failed to save: {0}" -f $_.Exception.Message)
    }

    # Optional checks
    if ($ExpectGit -and $git) {
      if ($git -ne $ExpectGit) {
        Write-Warning ("[VER] Git hash mismatch. Expected {0}, got {1}" -f $ExpectGit, $git)
      } else {
        Write-Host ("[VER] Git hash OK: {0}" -f $git)
      }
    }

    # Warn if build looks stale (> 2 hours old)
    if ($built -match '^(\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\d{4})') {
      $dtStr = $Matches[1] + ' ' + $Matches[2]
      $dt = $null
      if ([DateTime]::TryParse($dtStr, [ref]$dt)) {
        if ((Get-Date) - $dt -gt [TimeSpan]::FromHours(2)) {
          Write-Warning ("[VER] Build timestamp seems old: {0}" -f $dtStr)
        }
      }
    }
  }

  if ($needFallback) {
    Write-Host "[VER][FALLBACK] Trying UART1 direct (same Port assumed)" -ForegroundColor Yellow
    try {
      # Reuse existing port object (already open) to send plain 'VER' without CRLF trimming
      $sp.Write("VER`r`n")
      $sp.BaseStream.Flush()
      $t1 = Get-Date
      $uartLines = @()
      $inBlock2 = $false
      while(((Get-Date) - $t1).TotalSeconds -lt $ReadTimeoutSec){
        try {
          if ($sp.BytesToRead -le 0){ Start-Sleep -Milliseconds 50; continue }
          $l = $sp.ReadLine()
          if ($l -match '^===\s*FIRMWARE VERSION') { $inBlock2 = $true }
          if ($inBlock2) { $uartLines += $l }
          if ($inBlock2 -and $l -match '^=+') { break }
        } catch {}
      }
      if ($uartLines.Count -gt 1){
        Write-Host "[VER][UART] ------------------------------"
        $uartLines | ForEach-Object { Write-Host $_ }
        Write-Host "[VER][UART] ------------------------------"
      } else {
        Write-Warning "[VER][UART] Fallback also failed to capture block."
      }
    } catch { Write-Warning ("[VER][UART] Fallback error: {0}" -f $_.Exception.Message) }
  }
  exit 0
}
finally {
  if ($sp) { try { if ($sp.IsOpen) { $sp.Close() } } catch {}; $sp.Dispose() }
}

