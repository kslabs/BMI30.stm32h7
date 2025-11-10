param(
    [int]$Secs = 10
)

$ErrorActionPreference = 'Stop'

$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Resolve-Path (Join-Path $base '..')
$script = Join-Path $root 'HostTools/vendor_quick_status.py'
if (-not (Test-Path $script)) {
    Write-Error "Script not found: $script"
    exit 1
}

Write-Host "[RUN] py -3 -u $script --secs $Secs" 
$logFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'quick_status_last.log'
Write-Host "[LOG] Output -> $logFile"
& py -3 -u $script --secs $Secs 2>&1 | Tee-Object -FilePath $logFile

if ($LASTEXITCODE -ne 0) {
    Write-Error "quick status exited with code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "[DONE] quick status ok"
