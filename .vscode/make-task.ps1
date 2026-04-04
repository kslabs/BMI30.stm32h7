param(
  [ValidateSet('all','clean','flash_full')]
  [string]$Target = 'all'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

$tmpDir = Join-Path $ProjectRoot '.tmp'
if (-not (Test-Path $tmpDir)) {
  New-Item -ItemType Directory -Path $tmpDir | Out-Null
}
$env:TMP = $tmpDir
$env:TEMP = $tmpDir
$env:TMPDIR = $tmpDir

$makeCmd = Get-Command make -ErrorAction SilentlyContinue

$makeExe = $null
if ($makeCmd) {
  $makeExe = $makeCmd.Source
} else {
  $makeCandidates = @(
    'C:\msys64\usr\bin\make.exe',
    'C:\Program Files\Git\usr\bin\make.exe'
  )
  foreach ($candidate in $makeCandidates) {
    if (Test-Path $candidate) { $makeExe = $candidate; break }
  }
}

if ($makeExe) {
  $makeDir = Split-Path -Parent $makeExe
  if ($makeDir -and -not (($env:PATH -split ';') -contains $makeDir)) {
    $env:PATH = "$makeDir;$env:PATH"
  }
}

if ($makeExe) {
  Push-Location $ProjectRoot
  & $makeExe -C Debug $Target
  $code = $LASTEXITCODE
  Pop-Location
  exit $code
}

Write-Error "Neither 'bash' nor 'make' was found in PATH"
exit 1
