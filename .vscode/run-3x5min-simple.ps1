param(
  [string]$ScriptPath = "HostTools/vendor_usb_start_and_read.py",
  [string]$WorkingDir = "$PSScriptRoot/..",
  [int]$Runs = 3,
  [int]$WindowSec = 300,
  [int]$AbortNoRxSec = 20,
  [int]$PauseBetweenRunsSec = 5,
  [int]$ProfileId = 0,
  [ValidateSet('ctrl','bulk')] [string]$StatusMode = 'ctrl',
  [int]$FrameSamples = 10
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED','1','Process')

Push-Location (Resolve-Path $WorkingDir)
try {
  for ($i=1; $i -le $Runs; $i++) {
    Write-Host ("================ RUN {0}/{1} ================" -f $i, $Runs) -ForegroundColor Cyan
    $argList = @(
      '-3','-u', $ScriptPath,
      '--profile', $ProfileId,
      '--status-mode', $StatusMode,
      '--pairs','0',
      '--window-sec', $WindowSec,
      '--log-interval','5.0',
      '--abort-no-rx-sec', $AbortNoRxSec
    )
    if ($FrameSamples -gt 0) { $argList += @('--frame-samples', $FrameSamples) }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'py'
    $psi.WorkingDirectory = (Resolve-Path '.')
  $psi.Arguments = ($argList -join ' ')
  $psi.UseShellExecute = $true
  $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.WaitForExit()
    if ($proc.ExitCode -ne 0) {
      Write-Host ("[HOST][FAIL] Run {0} failed with code {1}" -f $i, $proc.ExitCode) -ForegroundColor Red
      exit 1
    }
    if ($i -lt $Runs) { Start-Sleep -Seconds $PauseBetweenRunsSec }
  }
  Write-Host "[HOST][OK] All runs completed successfully." -ForegroundColor Green
  exit 0
}
finally {
  Pop-Location
}
