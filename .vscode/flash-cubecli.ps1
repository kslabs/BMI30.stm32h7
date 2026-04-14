param(
    [string]$Elf = "$PSScriptRoot/../Debug/BMI30.stm32h7.elf",
    [string]$StlinkSn = ""
)
$ErrorActionPreference = 'Stop'
if (!(Test-Path $Elf)) {
    Write-Error "ELF not found: $Elf"
    exit 1
}
$cli = "C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
if (!(Test-Path $cli)) {
    $cli = "STM32_Programmer_CLI.exe"
}
Write-Output "Using CLI: $cli"
$connectArgs = @("port=SWD", "freq=4000")
if ($StlinkSn) {
    $connectArgs += "sn=$StlinkSn"
}

Write-Output ("Connect args: " + ($connectArgs -join " "))
& $cli -c @connectArgs -w $Elf -v -rst
exit $LASTEXITCODE
