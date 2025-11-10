param(
  [string]$ScriptPath = "HostTools/vendor_usb_start_and_read.py",
  [string]$WorkingDir = "$PSScriptRoot/..",
  [int]$ProfileId = 0,
  [double]$WindowSec = 60,
  [ValidateSet('none','ctrl','bulk')]
  [string]$StatusMode = 'bulk',
  [double]$LogInterval = 5.0,
  [double]$AbortNoRxSec = 20,
  [int]$StartRetries = 5,
  [double]$StartCheckSec = 3.5
)

# Robust launcher for HostTools/vendor_usb_start_and_read.py
# Tries: py -3, python, then py. Prints clear diagnostics on failure.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Cmd([string]$cmd, [string]$arguments){
  try {
  Write-Host ("[HOST][RUN] {0} {1}" -f $cmd, $arguments)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $cmd
  $psi.Arguments = $arguments
    $psi.WorkingDirectory = (Resolve-Path $WorkingDir)
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    [void]$p.Start()
    $p.WaitForExit()
    Write-Host ($p.StandardOutput.ReadToEnd())
    $err = $p.StandardError.ReadToEnd()
    if ($err) { Write-Host $err }
    return $p.ExitCode
  } catch {
    Write-Host ("[HOST][ERR] {0}" -f $_.Exception.Message)
    return 999
  }
}

Push-Location (Resolve-Path $WorkingDir)
try {
  # Prefer Python Launcher if present
  # Build arguments from parameters
  $env:VND_MIN_STAT_SEC = "0.5"  # throttle STAT polls
  $argsLine = "`"$ScriptPath`" --profile $ProfileId --status-mode $StatusMode --pairs 0 --window-sec $WindowSec --log-interval $LogInterval --abort-no-rx-sec $AbortNoRxSec --start-retries $StartRetries --start-check-sec $StartCheckSec"
  $cmds = @(
    @{cmd='py'; arguments="-3 $argsLine"},
    @{cmd='python'; arguments=$argsLine},
    @{cmd='py'; arguments=$argsLine}
  )
  $ok = $false
  foreach($c in $cmds){
    $code = Invoke-Cmd -cmd $c.cmd -arguments $c.arguments
    if ($code -eq 0) { $ok = $true; break }
  }
  if (-not $ok) {
    Write-Host "[HOST][FATAL] Failed to run vendor_usb_start_and_read.py. Ensure Python 3 and PyUSB+libusb are installed."
    Write-Host "[HOST][HINT] pip install pyusb libusb-package"
    exit 1
  }
} finally {
  Pop-Location
}
