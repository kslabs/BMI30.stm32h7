param([string]$OutputPath,[switch]$NoUI)
$ErrorActionPreference='Continue'
$r=New-Object 'System.Collections.Generic.List[string]'
function Add($s){$r.Add([string]$s)}
function ReadNet($a){(& netsh.exe @a 2>&1 | Out-String)}
Add 'BMI30 Wi-Fi diagnostics / Диагностика Wi-Fi'
Add ([DateTimeOffset]::Now.ToString('o'))
Add 'Пароли не запрашиваются. Настройки не изменяются.'
try{$o=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop;Add ($o.Caption+' / '+$o.Version+' / '+$o.BuildNumber)}catch{Add $_.Exception.Message}
Add "`r`n=== ADAPTER / АДАПТЕР ==="
Add (ReadNet @('wlan','show','drivers'))
Add "`r`n=== CONNECTION / ПОДКЛЮЧЕНИЕ ==="
Add (ReadNet @('wlan','show','interfaces'))
Add "`r`n=== BMI30 ACCESS POINTS / ТОЧКИ ДОСТУПА ==="
$scan=ReadNet @('wlan','show','networks','mode=bssid')
$keep=$true;$found=$false
foreach($l in ($scan -split '\r?\n')){
 if($l -match '^\s*SSID\s+\d+\s*:\s*(.*)$'){$keep=$Matches[1] -match '^BMI30';if($keep){$found=$true}}
 if($keep){Add $l}
}
if(!$found){Add 'BMI30 не обнаружены; выше указана возможная ошибка сканирования.'}
Add "`r`n=== LAST 30 MINUTES / ПОСЛЕДНИЕ 30 МИНУТ ==="
try{
 $events=Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational';StartTime=(Get-Date).AddMinutes(-30)} -MaxEvents 80 -ErrorAction Stop
 $count=0
 foreach($e in $events){
  [xml]$x=$e.ToXml();$data=@($x.Event.EventData.Data)
  if(($data.InnerText -join ' ') -notmatch 'BMI30'){continue}
  $count++;Add ($e.TimeCreated.ToString('o')+' EventID='+$e.Id)
  foreach($d in $data){$n=$d.GetAttribute('Name');if($n -match '^(SSID|BSSID|Reason|ReasonCode|ReasonText|FailureReason|FailureCode|ErrorCode|AuthenticationAlgorithm|CipherAlgorithm|ConnectionMode|InterfaceDescription)$'){Add ($n+'='+$d.InnerText)}}
 }
 if(!$count){Add 'Событий BMI30 нет. Повторите попытку подключения и запустите файл снова.'}
}catch{Add ('Журнал недоступен: '+$_.Exception.Message)}
if(!$OutputPath){$OutputPath=Join-Path ([Environment]::GetFolderPath('Desktop')) ('BMI30-WiFi-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.txt')}
try{
 $r | Set-Content -LiteralPath $OutputPath -Encoding UTF8 -ErrorAction Stop
 if(!$NoUI){Add-Type -AssemblyName System.Windows.Forms;[void][Windows.Forms.MessageBox]::Show("Диагностика сохранена:`r`n$OutputPath`r`nПередайте TXT-файл специалисту.",'BMI30')}
}catch{
 if($NoUI){throw}
 Add-Type -AssemblyName System.Windows.Forms;[void][Windows.Forms.MessageBox]::Show($_.Exception.Message,'BMI30: ошибка сохранения')
}
