param(
  [int]$Runs = 10
)
$ok = 0
$fail = 0
for ($i = 1; $i -le $Runs; $i++) {
  Write-Host "=== RUN $i ==="
  py -3 HostTools/vendor_usb_start_and_read.py --pairs 50 --full-mode 1 --frame-samples 300 --use-ctrl-status
  if ($LASTEXITCODE -eq 0) { $ok++ } else { $fail++ }
  Start-Sleep -Milliseconds 200
}
Write-Host "RESULT ok=$ok fail=$fail"
exit 0
