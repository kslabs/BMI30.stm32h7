param(
    [ValidateSet('setup', 'discover', 'check', 'enroll', 'open', 'watch', 'start', 'stop')]
    [string]$Mode = 'start'
)
$ErrorActionPreference = 'Stop'
$clientScript = Join-Path $PSScriptRoot 'bmi30_remote_ssh.py'
$stateRoot = Join-Path $env:LOCALAPPDATA 'BMI30-RemoteSSH'
$pidFile = Join-Path $stateRoot 'watch-process.json'
if ($Mode -in @('start', 'stop')) {
    $existing = $null
    if (Test-Path -LiteralPath $pidFile) {
        $saved = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
        $candidate = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$saved.pid)
        if ($candidate -and $candidate.CommandLine.IndexOf($clientScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            $candidate.CommandLine -match '\bwatch\b' -and
            $candidate.CreationDate.ToUniversalTime().ToString('o') -eq $saved.created) {
            $existing = $candidate
        }
    }
    if ($Mode -eq 'stop') {
        if ($existing) { Stop-Process -Id $existing.ProcessId }
        if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile }
        Write-Host 'BMI30 discovery stopped. Existing VS Code windows remain open.'
        exit 0
    }
    if ($existing) { Write-Host 'BMI30 discovery is already running.'; exit 0 }
    New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
    $pythonExe = (& py -3 -c 'import sys; print(sys.executable)').Trim()
    $arguments = '"' + $clientScript + '" watch --while-code'
    $process = Start-Process -FilePath $pythonExe -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $stateRoot 'watch.log') `
        -RedirectStandardError (Join-Path $stateRoot 'watch-errors.log')
    $info = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $process.Id)
    @{pid=$process.Id; created=$info.CreationDate.ToUniversalTime().ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8
    Write-Host ('BMI30 discovery started; log: ' + (Join-Path $stateRoot 'watch.log'))
    exit 0
}
& py -3 (Join-Path $PSScriptRoot 'bmi30_remote_ssh.py') $Mode
$result = $LASTEXITCODE
if ($Mode -eq 'enroll') {
    if ($result -eq 0) {
        Write-Host "All discovered boards accept this computer's key." -ForegroundColor Green
        & $PSCommandPath -Mode start
        exit $LASTEXITCODE
    } else {
        Write-Host 'Some boards still need enrollment. You can run this task again.' -ForegroundColor Yellow
    }
    Read-Host 'Press Enter to close'
}
exit $result
