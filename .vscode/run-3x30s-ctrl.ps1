param(
  [int]$ProfileId = 0,
  [double]$WindowSec = 30,
  [double]$PauseBetweenSec = 1.0,
  [string]$StatusMode = 'ctrl'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Resolve-Path "$PSScriptRoot/.."
$script = Join-Path $root 'HostTools/vendor_usb_start_and_read.py'
$python = 'py'
$launcherArgs = {
  param([int]$runIdx)
  $summary = Join-Path $root ("HostTools/summaries/run${runIdx}.json")
  $env:VND_MIN_STAT_SEC = "0.5"
  return "-3 `"$script`" --profile $ProfileId --status-mode $StatusMode --pairs 0 --window-sec $WindowSec --log-interval 5.0 --abort-no-rx-sec 10 --start-retries 5 --start-check-sec 3.5 --summary-json `"$summary`""
}

function Invoke-Run([int]$idx){
  $argLine = & $launcherArgs $idx
  Write-Host ("[RUN#{0}] {1} {2}" -f $idx, $python, $argLine)
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $python
  $psi.Arguments = $argLine
  $psi.WorkingDirectory = $root
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.UseShellExecute = $false
  $p = New-Object System.Diagnostics.Process
  $p.StartInfo = $psi
  [void]$p.Start()
  $p.WaitForExit()
  Write-Host ($p.StandardOutput.ReadToEnd())
  $err = $p.StandardError.ReadToEnd()
  if($err){ Write-Host $err }
  return $p.ExitCode
}

# Ensure summaries folder exists
$summDir = Join-Path $root 'HostTools/summaries'
if(!(Test-Path $summDir)){ New-Item -ItemType Directory -Path $summDir | Out-Null }

$allOk = $true
for($i=1; $i -le 3; $i++){
  $rc = Invoke-Run -idx $i
  if($rc -ne 0){
    Write-Host ("[RUN#{0}][FAIL] exit code {1}" -f $i, $rc)
    $allOk = $false
    break
  }
  if($i -lt 3){ Start-Sleep -Seconds $PauseBetweenSec }
}

# Gate: check JSON summaries for A/B > 0
if($allOk){
  $okFrames = $true
  for($i=1; $i -le 3; $i++){
    $jf = Join-Path $summDir ("run${i}.json")
    if(!(Test-Path $jf)){ Write-Host ("[RUN#{0}][WARN] summary not found: {1}" -f $i, $jf); $okFrames = $false; break }
    $obj = Get-Content $jf -Raw | ConvertFrom-Json
    $a = [int]$obj.A
    $b = [int]$obj.B
    if(($a + $b) -le 0){ Write-Host ("[RUN#{0}][FAIL] no A/B frames (A={1} B={2})" -f $i, $a, $b); $okFrames = $false; break }
  }
  if(-not $okFrames){ $allOk = $false }
}

if(-not $allOk){ exit 2 }
Write-Host "[3x30s] All runs OK with A/B frames present."
exit 0
