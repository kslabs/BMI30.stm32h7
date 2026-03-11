param(
    [string]$Elf = "$PSScriptRoot/../Debug/BMI30.stm32h7.elf",
    [switch]$MassErase
)

$ErrorActionPreference = 'Stop'

if (!(Test-Path $Elf)) {
    Write-Error "ELF not found: $Elf"
    exit 1
}

$openocdCandidates = @(
    'C:\Users\Admin\Documents\Work\BMI20\STM32\xpack-openocd-0.12.0-2-win32-x64\xpack-openocd-0.12.0-2\bin\openocd.exe',
    'C:\Users\TEST\Documents\Work\BMI20\STM32\xpack-openocd-0.12.0-2-win32-x64\xpack-openocd-0.12.0-2\bin\openocd.exe',
    'C:\xpack-openocd\bin\openocd.exe'
)

$scriptsCandidates = @(
    'C:\Users\Admin\Documents\Work\BMI20\STM32\xpack-openocd-0.12.0-2-win32-x64\xpack-openocd-0.12.0-2\scripts',
    'C:\Users\TEST\Documents\Work\BMI20\STM32\xpack-openocd-0.12.0-2-win32-x64\xpack-openocd-0.12.0-2\scripts'
)

$openocd = $null
foreach ($candidate in $openocdCandidates) {
    if (Test-Path $candidate) {
        $openocd = $candidate
        break
    }
}
if (-not $openocd) {
    $cmd = Get-Command openocd -ErrorAction SilentlyContinue
    if ($cmd) {
        $openocd = $cmd.Source
    }
}
if (-not $openocd) {
    $cubeCli = 'C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe'
    if (!(Test-Path $cubeCli)) {
        $cmd = Get-Command STM32_Programmer_CLI.exe -ErrorAction SilentlyContinue
        if ($cmd) {
            $cubeCli = $cmd.Source
        }
    }
    if (!(Test-Path $cubeCli)) {
        Write-Error 'Neither OpenOCD nor STM32CubeProgrammer CLI was found'
        exit 1
    }

    Write-Host "OpenOCD not found, fallback to CubeProgrammer: $cubeCli"
    & $cubeCli -c port=SWD freq=4000 -w $Elf -v -rst
    exit $LASTEXITCODE
}

$scriptsDir = $null
foreach ($candidate in $scriptsCandidates) {
    if (Test-Path $candidate) {
        $scriptsDir = $candidate
        break
    }
}
if (-not $scriptsDir) {
    $candidate = Join-Path (Split-Path -Parent (Split-Path -Parent $openocd)) 'scripts'
    if (Test-Path $candidate) {
        $scriptsDir = $candidate
    }
}
if (-not $scriptsDir) {
    Write-Error 'OpenOCD scripts directory not found'
    exit 1
}

$programCmd = if ($MassErase) {
    "init; halt; stm32h7x mass_erase 0; program {$Elf} verify reset exit"
} else {
    "program {$Elf} verify reset exit"
}

Write-Host "Using OpenOCD: $openocd"
Write-Host "Using scripts: $scriptsDir"
& $openocd -s $scriptsDir -f interface/stlink.cfg -f target/stm32h7x.cfg -c $programCmd
exit $LASTEXITCODE