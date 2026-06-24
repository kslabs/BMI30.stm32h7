# Build once, then flash all configured ST-LINK probes sequentially.

param(
    [switch]$CleanBuild = $false,
    [string[]]$StlinkSn = @()
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildScript = Join-Path $PSScriptRoot "build-and-flash.ps1"
$FlashScript = Join-Path $PSScriptRoot "flash-cubecli.ps1"
$ElfFile = Join-Path $ProjectRoot "Debug\BMI30.stm32h7.elf"
$CubeProgrammer = "C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
$ConfiguredTargets = @(
    @{
        Name = "ST-Link #1"
        Sn = "0667FF514953667287233516"
        Com = "COM18"
    },
    @{
        Name = "ST-Link #2"
        Sn = "066BFF514953667287243650"
        Com = "COM22"
    }
)

function Get-ConnectedStlinkTargets {
    if ($StlinkSn.Count -gt 0) {
        $targets = @()
        $idx = 1
        foreach ($sn in $StlinkSn) {
            $targets += @{
                Name = "ST-Link #$idx"
                Sn = $sn
                Com = ""
            }
            $idx++
        }
        return $targets
    }

    $cli = $CubeProgrammer
    if (!(Test-Path $cli)) {
        $cmd = Get-Command STM32_Programmer_CLI.exe -ErrorAction SilentlyContinue
        if ($cmd) {
            $cli = $cmd.Source
        }
    }
    if (!(Test-Path $cli)) {
        Write-Host "  [WARN] STM32CubeProgrammer CLI not found, using configured ST-LINK list." -ForegroundColor Yellow
        return $ConfiguredTargets
    }

    $listOutput = & $cli -l 2>&1 | Out-String
    $snList = @()
    $comBySn = @{}
    $currentSn = ""

    foreach ($line in ($listOutput -split "`r?`n")) {
        if ($line -match 'ST-LINK SN\s*:\s*(\S+)') {
            $currentSn = $matches[1]
            if ($snList -notcontains $currentSn) {
                $snList += $currentSn
            }
        } elseif (($line -match 'Port:\s*(COM\d+)') -and $currentSn) {
            $comBySn[$currentSn] = $matches[1]
        }
    }

    if ($snList.Count -eq 0) {
        Write-Host "  [WARN] No connected ST-LINK probes detected, using configured ST-LINK list." -ForegroundColor Yellow
        return $ConfiguredTargets
    }

    $targets = @()
    for ($i = 0; $i -lt $snList.Count; $i++) {
        $sn = $snList[$i]
        $targets += @{
            Name = "ST-Link #$($i + 1)"
            Sn = $sn
            Com = $(if ($comBySn.ContainsKey($sn)) { $comBySn[$sn] } else { "" })
        }
    }
    return $targets
}

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

$StlinkTargets = @(Get-ConnectedStlinkTargets)
Write-Host ("Detected/selected ST-LINK probes: {0}" -f $StlinkTargets.Count) -ForegroundColor Cyan
foreach ($target in $StlinkTargets) {
    $comText = if ($target.Com) { $target.Com } else { "COM:n/a" }
    Write-Host ("  - {0} | SN={1} | {2}" -f $target.Name, $target.Sn, $comText) -ForegroundColor Cyan
}

$buildArgs = @()
if ($CleanBuild) {
    $buildArgs += "-CleanBuild"
}

Write-Host "[1/2] Building firmware once..." -ForegroundColor Yellow
$oldEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $BuildScript @buildArgs -NoFlash
$ErrorActionPreference = $oldEap
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
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $FlashScript -Elf $ElfFile -StlinkSn $target.Sn
    $ErrorActionPreference = $oldEap
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
