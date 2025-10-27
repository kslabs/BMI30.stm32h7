# Stops the running serial monitor started by serial-monitor.ps1
# Looks for serial-monitor.pid in the same folder, reads PID, and kills the process.

$PidFile = Join-Path $PSScriptRoot "serial-monitor.pid"
if (!(Test-Path $PidFile)) {
  Write-Host "[Serial-Stop] No PID file found. Trying to find monitor process..."
  try {
    $procs = Get-Process -Name powershell -ErrorAction SilentlyContinue | Where-Object {
      $_.Path -like "*powershell*.exe"
    }
    $killed = 0
    foreach ($p in $procs) {
      try {
        $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)").CommandLine
        if ($cmdline -and $cmdline -match "serial-monitor.ps1") {
          Write-Host ("[Serial-Stop] Killing orphan monitor PID={0}" -f $p.Id)
          Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
          $killed++
        }
      } catch {}
    }
    if ($killed -eq 0) { Write-Host "[Serial-Stop] No running monitor found." }
  } catch {}
  exit 0
}

$pidText = Get-Content -Raw $PidFile -ErrorAction SilentlyContinue
if ([string]::IsNullOrWhiteSpace($pidText)) {
  Write-Host "[Serial-Stop] PID file is empty."
  Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
  exit 0
}

$pidNum = 0
if (-not [int]::TryParse($pidText, [ref]$pidNum)) {
  Write-Host "[Serial-Stop] Invalid PID: $pidText"
  Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
  exit 1
}

try {
  $proc = Get-Process -Id $pidNum -ErrorAction Stop
  if ($proc.Path -like "*powershell*.exe") {
    Write-Host "[Serial-Stop] Stopping monitor PID=$pidNum"
    Stop-Process -Id $pidNum -Force -ErrorAction SilentlyContinue
  } else {
    Write-Host "[Serial-Stop] PID belongs to another app: $($proc.Path)"
  }
} catch {
  Write-Host "[Serial-Stop] Process not found: PID=$pidNum"
  # Fallback scan if PID is stale
  try {
    $procs = Get-Process -Name powershell -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
      try {
        $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)").CommandLine
        if ($cmdline -and $cmdline -match "serial-monitor.ps1") {
          Write-Host ("[Serial-Stop] Killing monitor by scan PID={0}" -f $p.Id)
          Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
      } catch {}
    }
  } catch {}
}

Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
