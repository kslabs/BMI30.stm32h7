param(
  [Parameter(Mandatory=$true)][string]$Hex,
  [string]$CmdFilePath = ""
)
# Writes raw bytes specified as hex string (e.g. "20" or "30" or "20 01 02")
# into the serial injection file used by serial-monitor.ps1 (serial-cmd.bin)
# so the already-open monitor can transmit them without reopening the port.

if ([string]::IsNullOrWhiteSpace($CmdFilePath)) {
  $CmdFilePath = Join-Path $PSScriptRoot "serial-cmd.bin"
}

# Normalize hex string: remove 0x, spaces, commas, semicolons
$clean = ($Hex -replace "0x", "" -replace "[^0-9A-Fa-f]", "")
if (($clean.Length % 2) -ne 0) {
  Write-Error "Hex string must have even number of nibbles"
  exit 1
}

$bytes = New-Object byte[] ($clean.Length / 2)
for ($i=0; $i -lt $bytes.Length; $i++) {
  $bytes[$i] = [Convert]::ToByte($clean.Substring($i*2,2),16)
}

[System.IO.File]::WriteAllBytes($CmdFilePath, $bytes)
Write-Host ("[Serial-Send] Wrote {0} bytes to {1}" -f $bytes.Length, $CmdFilePath)
