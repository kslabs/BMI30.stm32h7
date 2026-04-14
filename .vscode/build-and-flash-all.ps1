# Build once, then flash all configured ST-LINK probes sequentially.

param(
    [switch]$CleanBuild = $false
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildScript = Join-Path $PSScriptRoot "build-and-flash.ps1"
$FlashScript = Join-Path $PSScriptRoot "flash-cubecli.ps1"
$ElfFile = Join-Path $ProjectRoot "Debug\BMI30.stm32h7.elf"
$StlinkTargets = @(
    @{
        Name = "ST-Link #1"
        Sn = "0667FF514953667287233516"
        Com = "COM18"
    },
    @{
        Name = "ST-Link #2"
        Sn = "066FFF565556857187224249"
        Com = "COM11"
    }
)

if (!(Test-Path $BuildScript)) {
    Write-Error "Build script not found: $BuildScript"
    exit 1
}
if (!(Test-Path $FlashScript)) {
    Write-Error "Flash script not found: $FlashScript"
    exit 1
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "STM32 BUILD AND FLASH ALL ST-LINK" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$buildArgs = @()
if ($CleanBuild) {
    $buildArgs += "-CleanBuild"
}

Write-Host "[1/2] Building firmware once..." -ForegroundColor Yellow
& $BuildScript @buildArgs -NoFlash
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (!(Test-Path $ElfFile)) {
    Write-Error "ELF not found after build: $ElfFile"
    exit 1
}

Write-Host "[2/2] Flashing configured probes..." -ForegroundColor Yellow
foreach ($target in $StlinkTargets) {
    Write-Host ("  -> {0} | SN={1} | {2}" -f $target.Name, $target.Sn, $target.Com) -ForegroundColor Cyan
    & $FlashScript -Elf $ElfFile -StlinkSn $target.Sn
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("  [ERROR] Flash failed for {0} ({1})" -f $target.Name, $target.Sn) -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host ("  [OK] Flash completed for {0}" -f $target.Name) -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "SUCCESS: All configured ST-LINK probes flashed." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
