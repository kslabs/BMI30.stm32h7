param(
    [string]$Elf = "$PSScriptRoot/../Debug/BMI30.stm32h7.elf",
    [string]$StlinkSn = ""
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $Elf)) {
    Write-Error "ELF not found: $Elf"
    exit 1
}

$cli = "C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
if (!(Test-Path $cli)) {
    $cli = "STM32_Programmer_CLI.exe"
}

$profiles = @(
    @{ Name = "SWD-4000"; Args = @("port=SWD", "freq=4000") },
    @{ Name = "SWD-1000"; Args = @("port=SWD", "freq=1000") },
    @{ Name = "UR-HWrst-1000"; Args = @("port=SWD", "freq=1000", "mode=UR", "reset=HWrst") },
    @{ Name = "UR-HWrst-100"; Args = @("port=SWD", "freq=100", "mode=UR", "reset=HWrst") },
    @{ Name = "HotPlug-1000"; Args = @("port=SWD", "freq=1000", "mode=HotPlug") },
    @{ Name = "HotPlug-100"; Args = @("port=SWD", "freq=100", "mode=HotPlug") }
)

$attempts = @()

Write-Output "Using CLI: $cli"
if ($StlinkSn) {
    Write-Output "Target ST-LINK SN: $StlinkSn"
}

foreach ($profile in $profiles) {
    $connectArgs = @($profile.Args)
    if ($StlinkSn) {
        $connectArgs += "sn=$StlinkSn"
    }

    $cmdArgs = @("-c") + $connectArgs + @("-w", $Elf, "-v", "-rst")
    Write-Output ("Attempt [{0}] connect args: {1}" -f $profile.Name, ($connectArgs -join " "))

    $output = & $cli @cmdArgs 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    $attempts += [pscustomobject]@{
        Name = $profile.Name
        ExitCode = $exitCode
        Output = $output
    }

    if ($output -match "Download verified successfully") {
        Write-Output $output
        exit 0
    }

    Write-Output ("Attempt [{0}] failed with exit code {1}" -f $profile.Name, $exitCode)
}

Write-Error "All CubeProgrammer connection profiles failed."
foreach ($attempt in $attempts) {
    Write-Output ("========== {0} (exit {1}) ==========" -f $attempt.Name, $attempt.ExitCode)
    Write-Output $attempt.Output
}
exit 1
