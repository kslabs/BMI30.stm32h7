param(
  [string]$Port = "COM11",
  [int]$Baud = 115200,
  [int]$PerCommandReadMs = 1500,
  [string[]]$Commands = @("HELP","VER","STATUS","FPS","PERF"),
  [string]$OutPath = ".vscode/diag_out.txt"
)

# Sends text commands over the specified COM port and captures responses per command.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Open-Port([string]$p,[int]$b){
  $sp = New-Object System.IO.Ports.SerialPort $p, $b, 'None', 8, 'One'
  $sp.Handshake = [System.IO.Ports.Handshake]::None
  $sp.NewLine = "`r`n"
  $sp.ReadTimeout = 200
  $sp.WriteTimeout = 500
  $sp.Open()
  return $sp
}

function Read-For([System.IO.Ports.SerialPort]$sp, [int]$ms){
  $deadline = [DateTime]::UtcNow.AddMilliseconds($ms)
  $buf = New-Object System.Collections.Generic.List[string]
  while([DateTime]::UtcNow -lt $deadline){
  try { $line = $sp.ReadLine(); if ($null -ne $line) { $buf.Add($line) } }
    catch {
      $base = $_.Exception; while($base.InnerException){ $base = $base.InnerException }
      if ($base -is [System.TimeoutException]) { continue } else { break }
    }
  }
  return $buf
}

$logLines = New-Object System.Collections.Generic.List[string]
$logLines.Add("[DIAG] Port=$Port Baud=$Baud")
$sp = $null
try {
  $sp = Open-Port -p $Port -b $Baud
  foreach($cmd in $Commands){
    $logLines.Add(("[DIAG] >>> {0}" -f $cmd))
    $sp.WriteLine($cmd)
    $sp.BaseStream.Flush()
    $resp = Read-For -sp $sp -ms $PerCommandReadMs
    if ($resp.Count -eq 0) { $logLines.Add("[DIAG] (no response)") }
    foreach($l in $resp){ $logLines.Add($l) }
  }
}
catch {
  $logLines.Add(("[DIAG][ERROR] {0}" -f $_.Exception.Message))
}
finally {
  if ($sp) { try { if ($sp.IsOpen) { $sp.Close() } } catch {} ; $sp.Dispose() }
}

# Write output
try { $dir = Split-Path -Parent $OutPath; if ($dir -and !(Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null } } catch {}
$logLines | Set-Content -Path $OutPath -Encoding UTF8
$logLines | ForEach-Object { Write-Host $_ }

exit 0

