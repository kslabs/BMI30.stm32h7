"""Record the completed deployment without changing acquisition settings."""
import json
from pathlib import Path
from expand12 import W,HERE,call
REMOTE=r'''
import csv,datetime,io,json,os,pathlib,runpy,shutil,subprocess,sys
p=json.load(sys.stdin);i=p['before'];r=p['release'];P=pathlib.Path;D=P('/home/techaid/Documents');M=P('/mnt/bmi30-average12-registry')
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')==i['hostname'] and run('findmnt','-n','-o','SOURCE','/')=='/dev/sda2'
assert P('/sys/block/mmcblk0/device/cid').read_text()==i['emmc_cid']
assert subprocess.run(['findmnt','-rn','-S','/dev/mmcblk0p2'],stdout=subprocess.DEVNULL).returncode!=0
registry=D/'docs/BMI30_version_registry_google_sheet.csv'
reader=csv.DictReader(io.StringIO(registry.read_text(encoding='utf-8-sig')));fields=reader.fieldnames;rows=list(reader)
values=['1',r['version'],'Установлена S07/M02: USB + eMMC','Raspberry averaging 1–12',
 'Диапазон расширен с 1–8 до 1–12 в валидаторах, форме Operating Mode и Player. Скользящее усреднение по каналам и фазам сохранено; индикатор STM32/Raspberry остаётся центрированным.',
 r['paths']['core'],r['paths']['engine'],'host/BMI30.GUI.001.py.2026-09-30-1650','hotspot_info_server.py; installed /usr/local/bin/bmi30-hotspot-info-server.py',
 'AVG_ROI; raw/full bypass; N=1 без дополнительного усреднения',
 '10 тестов локально и на S07/M02; живые окна 9–12, отказ для 13; без новых gap/CRC/ACK; Chrome: опции 1–12; SHA256 и единый релиз '+r['firmware_version']+' на 4 корнях.',
 'Настройки сохранены. Окно 12: 44800 байт/200 отсчётов. Более длинное окно увеличивает задержку. eMMC проверена офлайн, отдельной перезагрузки с неё не было.',
 'Пользовательская проверка проходов метки с новыми окнами.',datetime.datetime.now().astimezone().isoformat()]
assert len(fields)==len(values)
rows=[dict(zip(fields,values))]+[row for row in rows if row[fields[1]]!=r['version']]
buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=fields);writer.writeheader();writer.writerows(rows);raw=buf.getvalue().encode('utf-8-sig')
M.mkdir(exist_ok=False)
try:
 run('mount','/dev/mmcblk0p2',str(M))
 for root in [P('/'),M]:
  target=root/'home/techaid/Documents/docs/BMI30_version_registry_google_sheet.csv'
  backup=root/'var/backups/bmi30-averaging12'/r['version']/'registry-before.csv';shutil.copy2(target,backup)
  s=target.stat();tmp=target.with_name(target.name+'.avg12-tmp');tmp.write_bytes(raw);os.chown(tmp,s.st_uid,s.st_gid);os.chmod(tmp,s.st_mode&0o777);tmp.replace(target)
 run('sync','-f',str(M))
finally:
 if subprocess.run(['mountpoint','-q',str(M)]).returncode==0:run('umount',str(M))
 M.rmdir()
ns=runpy.run_path(str(D/'utilities/sync_bmi30_version_registry.py'))
os.chdir(D)
available=bool(ns['token_from_sources']('',ns['DEFAULT_TOKEN_FILE']))
print(json.dumps({'registry_written':['usb','emmc'],'rows':len(rows),'cloud_sync_token_available':available}))
'''
for board in ['S07','M02']:
    r=call(board,REMOTE,'registry',{'before':json.loads((W/(board+'-before.json')).read_text()),'release':json.loads((W/'release.json').read_text())},timeout=30)
    print(json.dumps({'board':board,**r}))
