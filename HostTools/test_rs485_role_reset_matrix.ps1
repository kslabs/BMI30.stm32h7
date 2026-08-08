param(
    [int[]]$Masks = (1..15),
    [int]$SettleSeconds = 25,
    [switch]$ContinueOnFailure
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Elf = Join-Path $ProjectRoot "Debug\BMI30.stm32h7.elf"
$CubeCli = "C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
$Nm = "arm-none-eabi-nm"

$Targets = @(
    [pscustomobject]@{ Bit = 1; Name = "C27"; Serial = "0670FF535548877187163910"; Mode = 0; Node = 0; High = 3; Last = 0 },
    # The enumerator picks the lexicographically greatest 12 UID bytes first.
    [pscustomobject]@{ Bit = 2; Name = "C28"; Serial = "066DFF514953667287234159"; Mode = 1; Node = 2; High = 2; Last = 2 },
    [pscustomobject]@{ Bit = 4; Name = "C30"; Serial = "066FFF514953667287243651"; Mode = 1; Node = 3; High = 3; Last = 3 },
    [pscustomobject]@{ Bit = 8; Name = "C31"; Serial = "066BFF514953667287233910"; Mode = 1; Node = 1; High = 1; Last = 1 }
)

if (!(Test-Path -LiteralPath $Elf)) {
    throw "ELF not found: $Elf"
}
if (!(Test-Path -LiteralPath $CubeCli)) {
    throw "STM32CubeProgrammer CLI not found: $CubeCli"
}

$NmOutput = (& $Nm -n $Elf 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "arm-none-eabi-nm failed for $Elf"
}

function Get-SymbolAddress([string]$Name) {
    $match = [regex]::Match(
        $NmOutput,
        "(?im)^([0-9a-f]+)\s+[a-z]\s+$([regex]::Escape($Name))\s*$"
    )
    if (!$match.Success) {
        throw "Symbol not found in ELF: $Name"
    }
    return $match.Groups[1].Value.ToUpperInvariant()
}

$Addresses = [ordered]@{
    Mode      = Get-SymbolAddress "vnd_sync_mode_public"
    Node      = Get-SymbolAddress "rs485_local_node_id"
    High      = Get-SymbolAddress "rs485_role_slave_id_high_water"
    Probe     = Get-SymbolAddress "rs485_role_auto_probe_active"
    Heartbeat = Get-SymbolAddress "rs485_role_auto_heartbeat_tx_remaining"
    Enum      = Get-SymbolAddress "rs485_role_enum_state"
    Target    = Get-SymbolAddress "rs485_role_enum_target_id"
    Candidate = Get-SymbolAddress "rs485_role_enum_candidate_valid"
    Waiting   = Get-SymbolAddress "rs485_role_enum_waiting_assignment"
    Last      = Get-SymbolAddress "rs485_role_last_confirmed_node_id"
    ResetTx   = Get-SymbolAddress "rs485_role_enum_reset_tx_remaining"
}

function Reset-Subset([int]$Mask) {
    $selected = @($Targets | Where-Object { ($Mask -band $_.Bit) -ne 0 })
    $jobs = @()
    foreach ($target in $selected) {
        $jobs += Start-Job -ScriptBlock {
            param($Tool, $Serial)
            & $Tool -c port=SWD freq=100 ap=0 mode=UR reset=HWrst "sn=$Serial" -rst 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "CubeProgrammer reset failed for $Serial"
            }
        } -ArgumentList $CubeCli, $target.Serial
    }

    try {
        $jobs | Wait-Job | Out-Null
        $failed = @($jobs | Where-Object { $_.State -ne "Completed" })
        $resetOutput = ($jobs | Receive-Job 2>&1 | Out-String)
        if (($failed.Count -ne 0) -or
            (([regex]::Matches($resetOutput, "(?i)reset is performed")).Count -ne $selected.Count)) {
            throw "Reset failed for mask $($Mask.ToString('X1'))`n$resetOutput"
        }
    }
    finally {
        $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
    }
}

function Read-TargetState($Target) {
    $args = @(
        "-c", "port=SWD", "freq=100", "ap=0", "mode=HOTPLUG",
        "sn=$($Target.Serial)"
    )
    foreach ($address in $Addresses.Values) {
        $args += @("-r8", "0x$address", "1")
    }

    $output = (& $CubeCli @args 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "SWD read failed for $($Target.Name)`n$output"
    }

    $state = [ordered]@{ Name = $Target.Name }
    foreach ($entry in $Addresses.GetEnumerator()) {
        $match = [regex]::Match(
            $output,
            "(?im)^0x$($entry.Value)\s*:\s*([0-9a-f]{2})"
        )
        if (!$match.Success) {
            throw "No value for $($entry.Key) at 0x$($entry.Value) on $($Target.Name)"
        }
        $state[$entry.Key] = [Convert]::ToByte($match.Groups[1].Value, 16)
    }
    return [pscustomobject]$state
}

function Format-State($State) {
    $role = if ($State.Mode -eq 0) {
        "M"
    }
    elseif ($State.Mode -eq 1) {
        "S{0:D2}" -f $State.Node
    }
    else {
        "?mode$($State.Mode)"
    }
    return ("{0}:{1}(h{2},e{3}/t{4}/c{5},probe{6},hb{7},wait{8},last{9},rr{10})" -f
        $State.Name, $role, $State.High, $State.Enum, $State.Target,
        $State.Candidate, $State.Probe, $State.Heartbeat, $State.Waiting,
        $State.Last, $State.ResetTx)
}

function Test-TargetState($Target, $State) {
    if (($State.Mode -ne $Target.Mode) -or
        ($State.Node -ne $Target.Node) -or
        ($State.High -ne $Target.High) -or
        ($State.Last -ne $Target.Last) -or
        ($State.Probe -ne 0) -or
        ($State.Heartbeat -ne 0) -or
        ($State.Waiting -ne 0) -or
        ($State.ResetTx -ne 0)) {
        return $false
    }

    if ($Target.Mode -eq 0) {
        # An idle scan for the absent S04 is valid, but it may not contain a
        # candidate. All completed/other enumeration states must be idle.
        return (($State.Enum -eq 0) -or
                ((($State.Enum -eq 1) -or ($State.Enum -eq 2)) -and
                 ($State.Target -eq 4) -and ($State.Candidate -eq 0)))
    }
    return ($State.Enum -eq 0)
}

$passed = 0
$failedMasks = @()
foreach ($mask in $Masks) {
    if (($mask -lt 1) -or ($mask -gt 15)) {
        throw "Mask must be in the range 1..15: $mask"
    }

    $binary = [Convert]::ToString($mask, 2).PadLeft(4, "0")
    Write-Host "RESET mask=$binary (hex=$($mask.ToString('X1')))" -ForegroundColor Cyan
    Reset-Subset $mask
    Start-Sleep -Seconds $SettleSeconds

    $states = @()
    $ok = $true
    for ($i = 0; $i -lt $Targets.Count; $i++) {
        $state = Read-TargetState $Targets[$i]
        $states += $state
        if (!(Test-TargetState $Targets[$i] $state)) {
            $ok = $false
        }
    }

    $snapshot = ($states | ForEach-Object { Format-State $_ }) -join " "
    if ($ok) {
        $passed++
        Write-Host "PASS mask=$binary $snapshot" -ForegroundColor Green
    }
    else {
        $failedMasks += $mask
        Write-Host "FAIL mask=$binary $snapshot" -ForegroundColor Red
        if (!$ContinueOnFailure) {
            break
        }
    }
}

Write-Host ("RESULT passed={0}/{1} failed={2}" -f
    $passed, $Masks.Count,
    $(if ($failedMasks.Count -eq 0) { "none" } else { ($failedMasks -join ",") }))
if ($failedMasks.Count -ne 0) {
    exit 1
}
