# Build and Flash script with timestamp update
# Ensures build time is always current

param(
    [switch]$CleanBuild = $false,
    [switch]$NoFlash = $false,
    [string]$StlinkSn = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DebugDir = Join-Path $ProjectRoot "Debug"
$BuildInfoC = Join-Path $ProjectRoot "Core\Src\build_info.c"
$ElfFile = Join-Path $DebugDir "BMI30.stm32h7.elf"
$CubeProgrammer = "C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
$FlashScript = Join-Path $PSScriptRoot "flash-cubecli.ps1"
$MakeExeCandidates = @(
    "C:\msys64\usr\bin\make.exe",
    "C:\Program Files\Git\usr\bin\make.exe"
)
$BashExeCandidates = @(
    "C:\msys64\usr\bin\bash.exe",
    "C:\Program Files\Git\bin\bash.exe"
)
$MakeExe = $null
foreach ($candidate in $MakeExeCandidates) {
    if (Test-Path $candidate) {
        $MakeExe = $candidate
        break
    }
}
if (-not $MakeExe) {
    $MakeExe = "make"
}
$BashExe = $null
foreach ($candidate in $BashExeCandidates) {
    if (Test-Path $candidate) {
        $BashExe = $candidate
        break
    }
}

function Convert-ToMsysPath([string]$winPath) {
    $p = $winPath -replace '\\', '/'
    return [regex]::Replace($p, '^([A-Za-z]):', { param($m) '/' + $m.Groups[1].Value.ToLower() })
}

function Invoke-Make {
    param([string[]]$MakeArgs)
    if ($BashExe) {
        $projectRootMsys = Convert-ToMsysPath $ProjectRoot
        $argsLine = ($MakeArgs -join ' ')
        $armGcc = (Get-Command arm-none-eabi-gcc -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
        $armPathMsys = ""
        if ($armGcc) {
            $armDir = Split-Path -Parent $armGcc
            $armPathMsys = Convert-ToMsysPath $armDir
        }
        if ($armPathMsys) {
            $cmd = 'export PATH="/c/Users/Admin/AppData/Local/Programs/Python/Launcher:' + $armPathMsys + ':$PATH"; cd ''' + $projectRootMsys + ''' && make ' + $argsLine
        } else {
            $cmd = 'export PATH="/c/Users/Admin/AppData/Local/Programs/Python/Launcher:$PATH"; cd ''' + $projectRootMsys + ''' && make ' + $argsLine
        }
        # gcc пишет warning в stderr; перенаправляем в stdout,
        # чтобы PowerShell не поднимал NativeCommandError как фатальную ошибку задачи.
        & $BashExe -lc $cmd 2>&1
    } else {
        & $MakeExe @MakeArgs
    }
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "STM32 BUILD AND FLASH" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Step 1: Force rebuild of build_info.c and main.c to update timestamp
Write-Host "[1/4] Updating build timestamp..." -ForegroundColor Yellow
if (Test-Path $BuildInfoC) {
    # Touch the file to update its modification time
    (Get-Item $BuildInfoC).LastWriteTime = Get-Date
    Write-Host "  [OK] build_info.c timestamp updated" -ForegroundColor Green
} else {
    Write-Host "  [WARN] build_info.c not found" -ForegroundColor Red
}

# Remove build_info.o to force recompilation
$BuildInfoO = Join-Path $DebugDir "Core\Src\build_info.o"
if (Test-Path $BuildInfoO) {
    Remove-Item $BuildInfoO -Force
    Write-Host "  [OK] Removed build_info.o" -ForegroundColor Green
}

# Remove main.o to force recompilation (main.c includes build_info)
$MainO = Join-Path $DebugDir "Core\Src\main.o"
if (Test-Path $MainO) {
    Remove-Item $MainO -Force
    Write-Host "  [OK] Removed main.o" -ForegroundColor Green
}

# Step 2: Clean build if requested
if ($CleanBuild) {
    Write-Host "[2/4] Clean build..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"  # gcc пишет warning в stderr; не считаем это фатальным
    Invoke-Make -MakeArgs @("-C", "Debug", "clean") 2>&1 | Out-Null
    $ErrorActionPreference = $oldEap
    Pop-Location
    Write-Host "  [OK] Clean completed" -ForegroundColor Green
} else {
    Write-Host "[2/4] Incremental build (use -CleanBuild for full rebuild)" -ForegroundColor Yellow
}

# Step 3: Build
Write-Host "[3/4] Building..." -ForegroundColor Yellow
Push-Location $ProjectRoot
$oldEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"  # gcc warnings -> stderr, но сборка может быть успешной
$buildOutput = Invoke-Make -MakeArgs @("-C", "Debug", "all") 2>&1 | Out-String
$buildExit = $LASTEXITCODE
$ErrorActionPreference = $oldEap
Pop-Location

if ($buildExit -ne 0) {
    Write-Host "  [ERROR] Build failed (exit code $buildExit)!" -ForegroundColor Red
    Write-Host "========== FULL BUILD OUTPUT ==========" -ForegroundColor Red
    Write-Host $buildOutput -ForegroundColor Red
    Write-Host "=======================================" -ForegroundColor Red
    exit $buildExit
}

# Show size
$sizeInfo = $buildOutput | Select-String -Pattern "text\s+data\s+bss"
if ($sizeInfo) {
    Write-Host "  $sizeInfo" -ForegroundColor Cyan
}

# Get ELF file info
if (Test-Path $ElfFile) {
    $elfInfo = Get-Item $ElfFile
    Write-Host "  [OK] Built: $($elfInfo.LastWriteTime.ToString('HH:mm:ss'))" -ForegroundColor Green
    Write-Host "  [OK] Size: $([math]::Round($elfInfo.Length/1KB, 1)) KB" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] ELF file not found!" -ForegroundColor Red
    exit 1
}

# Step 4: Flash
if ($NoFlash) {
    Write-Host "[4/4] Flash skipped (-NoFlash)" -ForegroundColor Yellow
} else {
    Write-Host "[4/4] Flashing via STM32CubeProgrammer..." -ForegroundColor Yellow
    if (!(Test-Path $FlashScript)) {
        Write-Host "  [ERROR] Flash script not found at: $FlashScript" -ForegroundColor Red
        exit 1
    }

    $flashParams = @{ Elf = $ElfFile }
    if ($StlinkSn) {
        $flashParams.StlinkSn = $StlinkSn
        Write-Host "  [OK] Target ST-LINK: $StlinkSn" -ForegroundColor Cyan
    }

    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"  # CubeCLI тоже может писать в stderr без фатального кода
    $flashOutput = & $FlashScript @flashParams 2>&1 | Out-String
    $flashExit = $LASTEXITCODE
    $ErrorActionPreference = $oldEap

    if ($flashOutput -match "Download verified successfully") {
        Write-Host "  [OK] Flash verified successfully" -ForegroundColor Green
        Write-Host "  [OK] MCU reset performed" -ForegroundColor Green
    } elseif ($flashExit -ne 0) {
        Write-Host "  [ERROR] Flash failed (exit code $flashExit)!" -ForegroundColor Red
        Write-Host "========== FULL FLASH OUTPUT ==========" -ForegroundColor Red
        Write-Host $flashOutput -ForegroundColor Red
        Write-Host "=======================================" -ForegroundColor Red
        exit $flashExit
    } else {
        Write-Host "  [WARN] Flash completed but verification message not found" -ForegroundColor Yellow
        Write-Host $flashOutput -ForegroundColor Yellow
    }
}

Write-Host "" 
Write-Host "========================================" -ForegroundColor Cyan
if ($NoFlash) {
    Write-Host "SUCCESS: Build $($elfInfo.LastWriteTime.ToString('HH:mm:ss')) completed!" -ForegroundColor Green
} else {
    Write-Host "SUCCESS: Build $($elfInfo.LastWriteTime.ToString('HH:mm:ss')) flashed!" -ForegroundColor Green
}
Write-Host "========================================" -ForegroundColor Cyan
