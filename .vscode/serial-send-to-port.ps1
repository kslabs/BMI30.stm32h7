param(
  [Parameter(Mandatory=$true)][string]$Port,
  [string]$Baud = "115200",
  [Parameter(Mandatory=$true)][string]$Hex
)
# Opens the specified COM port, sends raw bytes given as hex string (e.g. "20" or "30 01 02"), then closes.

try {
  $clean = ($Hex -replace "0x", "" -replace "[^0-9A-Fa-f]", "")
  if (($clean.Length % 2) -ne 0) { throw "Hex string must have even number of nibbles" }
  $bytes = New-Object byte[] ($clean.Length / 2)
  for ($i=0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [Convert]::ToByte($clean.Substring($i*2,2),16) }

  $sp = New-Object System.IO.Ports.SerialPort $Port, [int]$Baud, 'None', 8, 'One'
  $sp.Handshake = [System.IO.Ports.Handshake]::None
  $sp.NewLine = "`r`n"
  $sp.ReadTimeout = 300
  $sp.Open()
  Write-Host ("[Serial-SendPort] Opened {0} @ {1}" -f $sp.PortName, $Baud)
  $sp.Write($bytes, 0, $bytes.Length)
  $sp.BaseStream.Flush()
  Write-Host ("[Serial-SendPort] Sent {0} byte(s) to {1}" -f $bytes.Length, $Port)
  try { $sp.Close() } catch {}
  $sp.Dispose()
  exit 0
}
catch {
  Write-Error ("[Serial-SendPort] Failed: {0}" -f $_.Exception.Message)
  exit 1
}
