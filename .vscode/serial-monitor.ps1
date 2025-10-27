param(
  [string]$Port = "COM4",
  [int]$Baud = 115200,
  [string]$FilterRegex = "",
  [string]$LogPath = "",
  [string[]]$Ports = @(), # optional list of candidate ports to auto-scan (e.g., COM4,COM5)
  [int]$ReadTimeoutMs = 2000 # longer read timeout to avoid churn
)

# Simple resilient serial monitor for Windows PowerShell (v5+)
# - Opens $Port at $Baud, 8N1, no flow control
# - Reconnects every 2 seconds if the port is busy or disconnected
# - Prints lines as-is; press Ctrl+C to stop

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# PID file path to allow external Stop task to terminate this monitor
$PidFile = Join-Path $PSScriptRoot "serial-monitor.pid"
# File used to inject raw bytes into the open SerialPort from a separate task without reopening the COM port
$CmdFile = Join-Path $PSScriptRoot "serial-cmd.bin"
try { Set-Content -Path $PidFile -Value $PID -NoNewline -ErrorAction SilentlyContinue } catch {}

# Throttle settings for repeated error prints
$global:lastErrMsg = ""
$global:lastErrTime = Get-Date -Date "2000-01-01"
$ErrThrottleSeconds = 10

function Write-ErrThrottled {
  param([string]$msg)
  $now = Get-Date
  if ($msg -ne $global:lastErrMsg -or ($now - $global:lastErrTime).TotalSeconds -ge $ErrThrottleSeconds) {
    Write-Host $msg
    $global:lastErrMsg = $msg
    $global:lastErrTime = $now
  }
}

function Select-CandidatePort {
  param([string]$fallback, [string[]]$candidates)
  $names = [System.IO.Ports.SerialPort]::GetPortNames()
  if ($candidates -and $candidates.Length -gt 0) {
    foreach($p in $candidates){ if ($names -contains $p) { return $p } }
  }
  if ($fallback -and ($names -contains $fallback)) { return $fallback }
  return $null
}

while ($true) {
  try {
    $curPort = Select-CandidatePort -fallback $Port -candidates $Ports
    if (-not $curPort) { throw "No matching COM port is present right now." }
    $sp = New-Object System.IO.Ports.SerialPort $curPort, $Baud, 'None', 8, 'One'
    $sp.Handshake = [System.IO.Ports.Handshake]::None
  $sp.ReadTimeout = $ReadTimeoutMs
    $sp.NewLine = "`r`n"
    $sp.Open()
    Write-Host ("[Serial] Opened {0} at {1} (8N1). Ctrl+C to exit." -f $sp.PortName, $Baud)

  while ($sp.IsOpen) {
      try {
        # Check for pending command injection file and send bytes if present
        if (Test-Path $CmdFile) {
          try {
            $bytes = [System.IO.File]::ReadAllBytes($CmdFile)
            if ($bytes -and $bytes.Length -gt 0) {
              $sp.Write($bytes, 0, $bytes.Length)
              $sp.BaseStream.Flush()
              Write-Host ("[Serial] Sent {0} byte(s)" -f $bytes.Length)
            }
          } catch {
            Write-Warning ("[Serial] Failed to send cmd bytes: {0}" -f $_.Exception.Message)
          } finally {
            try { Remove-Item -Force $CmdFile -ErrorAction SilentlyContinue } catch {}
          }
        }
        $line = $sp.ReadLine()
        # Print the line as-is; adjust if your firmware uses LF-only
        $toPrint = $true
        if (-not [string]::IsNullOrWhiteSpace($FilterRegex)) { $toPrint = ($line -match $FilterRegex) }
        if ($toPrint) {
          Write-Host $line
          if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
            try { Add-Content -Path $LogPath -Value $line -ErrorAction SilentlyContinue } catch {}
          }
        }
      }
      catch {
        # Unwrap exceptions to detect real TimeoutException (often wrapped by TargetInvocationException)
        $base = $_.Exception
        while ($base.InnerException) { $base = $base.InnerException }
        if ($base -is [System.TimeoutException]) {
          continue
        } else {
          Write-ErrThrottled ("[Serial] Read error: {0}" -f $_.Exception.Message)
          break
        }
      }
    }
  }
  catch {
  $pn = if ($curPort) { $curPort } else { $Port }
  Write-ErrThrottled ("[Serial] Port {0} not ready: {1}" -f $pn, $_.Exception.Message)
  }
  finally {
    if ($sp) {
      try { if ($sp.IsOpen) { $sp.Close() } } catch {}
      $sp.Dispose()
    }
    # Remove PID file if it belongs to current process
    try {
      if (Test-Path $PidFile) {
        $curr = try { Get-Content -Raw $PidFile } catch { '' }
        if ($curr -eq ("{0}" -f $PID)) { Remove-Item -Force $PidFile -ErrorAction SilentlyContinue }
      }
    } catch {}
  }

  Start-Sleep -Seconds 2
}
