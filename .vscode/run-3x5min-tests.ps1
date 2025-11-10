param(
  [string]$ScriptPath = "HostTools/vendor_usb_start_and_read.py",
  [string]$WorkingDir = "$PSScriptRoot/..",
  [int]$Runs = 3,
  [int]$WindowSec = 300,
  [int]$AbortNoRxSec = 20,
  [int]$PauseBetweenRunsSec = 5,
  [int]$ProfileId = 0,
  [ValidateSet('ctrl','bulk')] [string]$StatusMode = 'ctrl',
  [int]$FrameSamples = 10,
  [string]$DiagPort = 'COM4',
  [int]$DiagBaud = 115200,
  [switch]$NoInterpreterDiag,
  [string]$PythonExe
)

# Runs vendor_usb_start_and_read.py N times for 5 minutes each, fail-fast on any error.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED','1','Process')
$global:RunLogPath = Join-Path (Resolve-Path $PSScriptRoot) 'last-3x5-run.log'
try { if(Test-Path $global:RunLogPath){ Remove-Item -Path $global:RunLogPath -ErrorAction SilentlyContinue } } catch {}
Write-Host ("[HOST] Log file: {0}" -f $global:RunLogPath) -ForegroundColor DarkGray
try { New-Item -ItemType File -Path $global:RunLogPath -Force | Out-Null } catch { Write-Host ("[HOST][WARN] Cannot create log file: {0}" -f $_.Exception.Message) -ForegroundColor Yellow }

# Trap any terminating error to ensure it's logged
trap {
  $msg = ("[TRAP] {0}" -f $_.Exception.Message)
  try { Add-Content -Path $global:RunLogPath -Value $msg } catch {}
  Write-Host $msg -ForegroundColor Red
  exit 1
}

# Early banner and environment logging to help diagnose early exits
try {
  $resolvedRoot = Resolve-Path $PSScriptRoot
  $resolvedWD = Resolve-Path $WorkingDir
  $combinedScript = Join-Path $resolvedWD $ScriptPath
  $banner = @(
    '================ 3x5 TEST HARNESS START ================',
    ("Timestamp: {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')),
    ("PSVersion: {0}" -f $PSVersionTable.PSVersion),
    ("PSScriptRoot: {0}" -f $resolvedRoot),
    ("WorkingDir (arg): {0}" -f $WorkingDir),
    ("WorkingDir (resolved): {0}" -f $resolvedWD),
    ("ScriptPath (arg): {0}" -f $ScriptPath),
    ("ScriptPath (resolved): {0}" -f $combinedScript),
    ("Env:PYTHONUNBUFFERED={0}" -f $env:PYTHONUNBUFFERED),
    ("Env:PATH={0}" -f $env:PATH)
  )
  $banner | Set-Content -Path $global:RunLogPath -Encoding UTF8
} catch {
  Write-Host ("[HOST][WARN] Failed to create early log: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
}

function Write-Log([string]$msg){
  try { Add-Content -Path $global:RunLogPath -Value $msg } catch {}
}

function Invoke-Cmd([string]$cmd, [string]$arguments, [string]$wd, [switch]$LogOutput){
  Write-Host ("[HOST][RUN] {0} {1}" -f $cmd, $arguments)
  try {
    $resolvedWd = Resolve-Path $wd
    $proc = $null
    $code = $null
    try {
      $proc = Start-Process -FilePath $cmd -ArgumentList $arguments -WorkingDirectory $resolvedWd -NoNewWindow -Wait -PassThru -ErrorAction Stop
      $code = $proc.ExitCode
    } catch {
      Write-Host ("[HOST][ERR] Failed to start process: {0} {1} -> {2}" -f $cmd, $arguments, $_.Exception.Message) -ForegroundColor Red
      Write-Log ("[HOST][EXIT] code=127 out_len=0 err_len=0")
      return 127
    }
    if ($null -eq $code) { $code = 999 }
    Write-Log ("[HOST][EXIT] code={0} out_len=- err_len=-" -f $code)
    return $code
  } catch {
    Write-Host ("[HOST][ERR] Invoke-Cmd exception: {0}" -f $_.Exception.Message) -ForegroundColor Red
    return 998
  }
}

Push-Location (Resolve-Path $WorkingDir)
try {
  if (-not $NoInterpreterDiag) {
    # Check interpreter availability upfront and log it
    $interpreterConfigs = @(
      @{cmd='py'; arguments=('-3 -u')},
      @{cmd='py'; arguments=('-u')},
      @{cmd='C:\\Windows\\py.exe'; arguments=('-3 -u')},
      @{cmd='python'; arguments=('-u')},
      @{cmd='python3'; arguments=('-u')},
      @{cmd=(Join-Path $WorkingDir 'python.bat'); arguments=('')},
      @{cmd=(Join-Path $WorkingDir 'python.cmd'); arguments=('')}
    )
    try {
      foreach($cfg in $interpreterConfigs){
        $gc = $null
        try { $gc = Get-Command $cfg.cmd -ErrorAction Stop } catch {}
        $found = if ($null -ne $gc) { if ($gc.Source) { $gc.Source } else { $gc.Definition } } else { 'NOT FOUND' }
        Add-Content -Path $global:RunLogPath -Value ("[CHECK] Interpreter '{0}' -> {1}" -f $cfg.cmd, $found)
      }
    } catch {}

    # Version diagnostics to capture common failure cases (WindowsApps stubs, missing Python)
    try { Write-Log "[CHECK] Running 'py -0p'"; [void](Invoke-Cmd -cmd 'py' -arguments '-0p' -wd $WorkingDir -LogOutput) } catch { Write-Log ("[CHECK] py -0p threw: {0}" -f $_.Exception.Message) }
    try { Write-Log "[CHECK] Running 'py -3 -V'"; [void](Invoke-Cmd -cmd 'py' -arguments '-3 -V' -wd $WorkingDir -LogOutput) } catch { Write-Log ("[CHECK] py -3 -V threw: {0}" -f $_.Exception.Message) }
    try { Write-Log "[CHECK] Running 'python -V'"; [void](Invoke-Cmd -cmd 'python' -arguments '-V' -wd $WorkingDir -LogOutput) } catch { Write-Log ("[CHECK] python -V threw: {0}" -f $_.Exception.Message) }
    try { Write-Log "[CHECK] Running 'python3 -V'"; [void](Invoke-Cmd -cmd 'python3' -arguments '-V' -wd $WorkingDir -LogOutput) } catch { Write-Log ("[CHECK] python3 -V threw: {0}" -f $_.Exception.Message) }
  } else {
    Write-Log "[CHECK] Interpreter diagnostics skipped by '-NoInterpreterDiag'"
  }

  for ($i=1; $i -le $Runs; $i++) {
    Write-Host ("================ RUN {0}/{1} ================" -f $i, $Runs) -ForegroundColor Cyan
    # Throttle STAT polling inside host reader to reduce contention
    $env:VND_MIN_STAT_SEC = "0.5"
    $argsLine = "`"$ScriptPath`" --profile $ProfileId --status-mode $StatusMode --pairs 0 --window-sec $WindowSec --log-interval 5.0 --abort-no-rx-sec $AbortNoRxSec"
    if ($FrameSamples -gt 0) {
      $argsLine += " --frame-samples $FrameSamples"
    }
    $tried = 0
    $ok = $false
    $attempts = @()
    if ($PythonExe) {
      $attempts += @(
        @{cmd=$PythonExe; arguments=("-3 -u " + $argsLine)},
        @{cmd=$PythonExe; arguments=("-u " + $argsLine)}
      )
    }
    # Prefer py launcher and common fallbacks
    $attempts += @(
      @{cmd='py'; arguments=("-3 -u " + $argsLine)},
      @{cmd='py'; arguments=("-u " + $argsLine)},
      @{cmd='C:\\Users\\TEST\\AppData\\Local\\Programs\\Python\\Launcher\\py.exe'; arguments=("-3 -u " + $argsLine)},
      @{cmd='C:\\Windows\\py.exe'; arguments=("-3 -u " + $argsLine)},
      @{cmd='python'; arguments=("-u " + $argsLine)},
      @{cmd='python3'; arguments=("-u " + $argsLine)},
      @{cmd=(Join-Path $WorkingDir 'python.bat'); arguments=($argsLine)},
      @{cmd=(Join-Path $WorkingDir 'python.cmd'); arguments=($argsLine)}
    )
    foreach($cfg in $attempts){
      $tried++
      try { Add-Content -Path $global:RunLogPath -Value ("[HOST][ATTEMPT] run={0} attempt={1} cmd={2} args={3}" -f $i, $tried, $cfg.cmd, $cfg.arguments) } catch {}
  $code = Invoke-Cmd -cmd $cfg.cmd -arguments $cfg.arguments -wd $WorkingDir -LogOutput
      if ($code -eq 0) { $ok = $true; break }
      Write-Host ("[HOST] Attempt {0} failed with code {1} (cmd={2})" -f $tried, $code, $cfg.cmd) -ForegroundColor Yellow
      try { "[HOST][ATTEMPT_FAIL] run={0} attempt={1} code={2} cmd={3}" -f $i, $tried, $code, $cfg.cmd | Add-Content -Path $global:RunLogPath } catch {}
    }
    if (-not $ok) {
      Write-Host ("[HOST][FAIL] Run {0} FAILED. Collecting diagnostics..." -f $i) -ForegroundColor Red
      try { "[HOST][RUN_FAIL] run={0}" -f $i | Add-Content -Path $global:RunLogPath } catch {}
      try {
        powershell -NoProfile -ExecutionPolicy Bypass -File ".vscode/collect-diagnostics.ps1" -Port $DiagPort -Baud $DiagBaud | Write-Host
      } catch { Write-Host ("[HOST][DIAG] Failed to collect diagnostics: {0}" -f $_.Exception.Message) -ForegroundColor Yellow }
      Write-Host ("[HOST] Stopping after failure.") -ForegroundColor Red
      Write-Host ("[HOST] See log: {0}" -f $global:RunLogPath) -ForegroundColor Yellow
      exit 1
    }
    # Between runs, gently kick WinUSB pipes and altsetting to avoid lingering stalls
    if ($i -lt $Runs) {
      try {
        Write-Host "[HOST] Inter-run bus kick (alt 0->1, clear_halt)" -ForegroundColor DarkCyan
        $null = & py -3 "HostTools/vendor_bus_kick.py"
      } catch {
        Write-Host ("[HOST][WARN] Bus kick failed: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
      }
      Start-Sleep -Seconds $PauseBetweenRunsSec
    }
  }
  Write-Host "[HOST][OK] All runs completed successfully." -ForegroundColor Green
  exit 0
}
finally {
  Pop-Location
}
