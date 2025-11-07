param(
  [string]$ScriptPath = "HostTools/vendor_usb_start_and_read.py",
  [string]$WorkingDir = "$PSScriptRoot/.."
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
  # Default args for high-throughput run: profile0 (200Hz/1360), status via ctrl, unlimited pairs with 60s window
  $argsLine = "`"$ScriptPath`" --profile 0 --status-mode bulk --pairs 0 --window-sec 60 --log-interval 5.0 --abort-no-rx-sec 15"
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
