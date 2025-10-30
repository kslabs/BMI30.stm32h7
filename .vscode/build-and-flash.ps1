# Build and Flash script with timestamp update
# Ensures build time is always current

param(
    [switch]$CleanBuild = $false
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DebugDir = Join-Path $ProjectRoot "Debug"
$BuildInfoC = Join-Path $ProjectRoot "Core\Src\build_info.c"
$ElfFile = Join-Path $DebugDir "BMI30.stm32h7.elf"
$CubeProgrammer = "C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"

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
    make -C Debug clean 2>&1 | Out-Null
    Pop-Location
    Write-Host "  [OK] Clean completed" -ForegroundColor Green
} else {
    Write-Host "[2/4] Incremental build (use -CleanBuild for full rebuild)" -ForegroundColor Yellow
}

# Step 3: Build
Write-Host "[3/4] Building..." -ForegroundColor Yellow
Push-Location $ProjectRoot
$buildOutput = make -C Debug all 2>&1
$buildExit = $LASTEXITCODE
Pop-Location

if ($buildExit -ne 0) {
    Write-Host "  [ERROR] Build failed!" -ForegroundColor Red
    Write-Host $buildOutput
    exit 1
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
Write-Host "[4/4] Flashing via STM32CubeProgrammer..." -ForegroundColor Yellow
if (!(Test-Path $CubeProgrammer)) {
    Write-Host "  [ERROR] STM32CubeProgrammer not found at: $CubeProgrammer" -ForegroundColor Red
    exit 1
}

$flashOutput = & $CubeProgrammer -c port=SWD freq=4000 -w $ElfFile -v -rst 2>&1
$flashExit = $LASTEXITCODE

if ($flashOutput -match "Download verified successfully") {
    Write-Host "  [OK] Flash verified successfully" -ForegroundColor Green
    Write-Host "  [OK] MCU reset performed" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Flash failed!" -ForegroundColor Red
    Write-Host $flashOutput
    exit 1
}

Write-Host "" 
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "SUCCESS: Build $($elfInfo.LastWriteTime.ToString('HH:mm:ss')) flashed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
