import ast
import base64
import json
from pathlib import Path
import shutil
from patch_access import portal, service, communication, watchdog, hotspot
from patch_access_assets import patch as portable_assets
from patch_privacy_submenu import patch as privacy_submenu

ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'.tmp/wifi-access-20261021'
SOURCE=WORK/'M02'
DEST=WORK/'candidate'
DEST.mkdir(exist_ok=True)
HERE=Path(__file__).resolve().parent

PS=r'''Add-Type -AssemblyName System.Windows.Forms
$f=New-Object Windows.Forms.Form
$f.Text='Открыть BMI30';$f.Width=510;$f.Height=215;$f.StartPosition='CenterScreen'
$l=New-Object Windows.Forms.Label;$l.SetBounds(18,20,460,65);$l.Text='Выберите Wi-Fi сеть вашего прибора BMI30 и введите пароль. Интернет не требуется.';$f.Controls.Add($l)
$b=New-Object Windows.Forms.Button;$b.SetBounds(18,100,235,35);$b.Text='Выбрать сеть Wi-Fi';$f.Controls.Add($b)
$b.Add_Click({Start-Process 'ms-availablenetworks:'})
$d=New-Object Windows.Forms.Button;$d.SetBounds(265,100,215,35);$d.Text='Сохранить диагностику';$f.Controls.Add($d)
$d.Add_Click({
 $data=@();foreach($n in [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()){
  if($n.NetworkInterfaceType -eq 'Wireless80211'){$i=$n.GetIPProperties();$data+=@{name=$n.Name;state=$n.OperationalStatus.ToString();ip=@($i.UnicastAddresses|ForEach-Object {$_.Address.ToString()});gateway=@($i.GatewayAddresses|ForEach-Object {$_.Address.ToString()});dns=@($i.DnsAddresses|ForEach-Object {$_.ToString()})}}
 }
 $p=Join-Path ([Environment]::GetFolderPath('Desktop')) ('BMI30-diagnostics-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.json')
 @{time=(Get-Date).ToString('o');wifi=$data}|ConvertTo-Json -Depth 5|Set-Content -LiteralPath $p -Encoding UTF8
 $l.Text='Диагностика сохранена на рабочем столе. Передайте файл специалисту.'
})
$t=New-Object Windows.Forms.Timer;$t.Interval=3000
$t.Add_Tick({
 foreach($n in [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()){
  if($n.NetworkInterfaceType -ne 'Wireless80211' -or $n.OperationalStatus -ne 'Up'){continue}
  foreach($g in $n.GetIPProperties().GatewayAddresses){
   $a=$g.Address.ToString();if($a -notmatch '^10\.|^192\.168\.|^172\.(1[6-9]|2\d|3[01])\.'){continue}
   try{
    $r=[Net.HttpWebRequest]::Create('http://'+$a+'/api/status');$r.Proxy=$null;$r.Timeout=1200;$r.ReadWriteTimeout=1200
    $v=$r.GetResponse();$s=New-Object IO.StreamReader($v.GetResponseStream());$j=$s.ReadToEnd()|ConvertFrom-Json;$s.Close();$v.Close()
    if($j.hostname -match '^BMI30-'){$t.Stop();Start-Process ('http://'+$a+'/');$f.Close();return}
   }catch{}
  }
 }
})
$f.Add_Shown({$t.Start()});$f.Add_FormClosed({$t.Stop();$t.Dispose()})
[Windows.Forms.Application]::Run($f)
'''

def build():
    for f in SOURCE.glob('opt__bmi30-network__*.py'):
        (DEST/f.name.removeprefix('opt__bmi30-network__')).write_bytes(f.read_bytes())
    modifications={'service.py':service,'communication_recovery.py':communication}
    for name, patch in modifications.items():
        f=DEST/name;f.write_text(patch(f.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
    for name in ('access.py','access_policy.py','access_power.py','access_ui.js','bmi30-access.service','bmi30-captive-detection.conf'):
        shutil.copy2(HERE/name,DEST/name)
    for f in (HERE/'vendor').rglob('*'):
        if f.is_file() and '__pycache__' not in f.parts and f.suffix != '.pyc' and 'bin' not in f.relative_to(HERE/'vendor').parts:
            target=DEST/'vendor'/f.relative_to(HERE/'vendor')
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,target)
    p=(SOURCE/'usr__local__bin__bmi30-hotspot-info-server.py').read_text(encoding='utf-8')
    p=portal(p).replace("token = json.loads(Path('/run/bmi30-access/", "token = json.loads(pathlib.Path('/run/bmi30-access/")
    p=p.replace("payload = Path('/opt/bmi30-network/Open-BMI30.cmd')", "payload = pathlib.Path('/opt/bmi30-network/Open-BMI30.cmd')")
    p=privacy_submenu(p)
    (DEST/'hotspot_info_server.py').write_text(p,encoding='utf-8',newline='\n')
    (DEST/'bmi30-hotspot.sh').write_text(hotspot((SOURCE/'usr__local__bin__bmi30-hotspot.sh').read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
    (DEST/'bmi30_wifi_watchdog.py').write_text(watchdog((SOURCE/'usr__local__sbin__bmi30-wifi-watchdog.py').read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
    unit=json.loads((WORK/'M02-inspect.json').read_text())['units']['bmi30-core']
    assert unit.count('After=network-online.target\nWants=network-online.target')==1
    unit=unit.replace('After=network-online.target\nWants=network-online.target','After=local-fs.target')
    (DEST/'bmi30-core.service').write_text(unit,encoding='utf-8',newline='\n')
    encoded=base64.b64encode(PS.encode('utf-16le')).decode()
    cmd='@echo off\r\npowershell.exe -NoLogo -NoProfile -STA -WindowStyle Hidden -EncodedCommand '+encoded+'\r\n'
    assert len(cmd)<8000
    (DEST/'Open-BMI30.cmd').write_bytes(cmd.encode('ascii'))
    (DEST/'Open-BMI30.ps1').write_bytes(b'\xef\xbb\xbf'+PS.encode('utf-8'))
    shutil.copy2(HERE/'test_access.py',DEST/'test_access.py')
    vendor_files=[f.relative_to(DEST/'vendor').as_posix() for f in (DEST/'vendor').rglob('*') if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc']
    (DEST/'bmi30_portable_assets.py').write_text(portable_assets((SOURCE/'utilities__bmi30_portable_assets.py').read_text(),vendor_files),encoding='utf-8')
    for f in DEST.glob('*.py'):ast.parse(f.read_text(encoding='utf-8'))
    print('Candidates ready; Windows launcher command length:',len(cmd))

if __name__=='__main__':build()
