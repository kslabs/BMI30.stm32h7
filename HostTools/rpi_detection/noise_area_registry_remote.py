"""Write the active M02 version journal and sync it with the existing utility."""
import csv
import datetime
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

p = json.load(sys.stdin)
r = p['release']
D = Path('/home/techaid/Documents')
assert subprocess.check_output(['hostname'], text=True).strip() == 'BMI30-E29805C7C'
assert 'BMI30_SPLIT_VERSION=' + r['version'] + '\n' in (D / 'host/bmi30_split_active_version.env').read_text()
registry = D / 'docs/BMI30_version_registry_google_sheet.csv'
reader = csv.DictReader(io.StringIO(registry.read_text(encoding='utf-8-sig')))
fields, rows = reader.fieldnames, list(reader)
values = ['1', r['version'], 'Установлена M02: активная USB-система',
    'Регулируемые границы измерения шума относительно максимального импульса',
    '2 Tag Detection: Noise measurement area 5–95%, шаг 1%, начальные 50%. Процент сдвигает границы исключаемой зоны около пика в круговом буфере; измерение максимума шума и заданные коэффициенты S/N сохранены. При 50% область совпадает с прежней для 200/600 отсчётов. Изменение области сбрасывает накопленный шум/SNR, подтверждения и поколение обработчика. Подсветка и Freeze используют процент кадра.',
    r['paths']['core'], r['paths']['engine'], r['paths']['gui'],
    'hotspot_info_server.py; /usr/local/bin/bmi30-hotspot-info-server.py',
    'AVG_ROI / режим 6; верхний и нижний каналы; пороги и усреднение без изменений',
    '38 тестов на M02; 12 новых локальных тестов; JavaScript 546 вариантов; рабочий API 50→80→50 с сохранением коэффициентов, доступные кадры и продолжающий работу обработчик. За 6 с без прироста gap/CRC/magic/ACK/overflows. Установленная форма отрисована серверным кодом; живой браузер требует входа.',
    'Только M02 /dev/sda2. Настройки и исходные файлы сохранены в резервной копии. Неактивная eMMC, S07 и STM32 не обновлялись. Физические проходы метки не проверены. Мгновенный S/N=4; существующие ручные коэффициенты low/high=4/3 сохранены.',
    'Проверить реальные проходы, изменяя процент; оценить пропуски и ложные срабатывания.',
    datetime.datetime.now(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=2))).isoformat()]
assert len(fields) == len(values)
backup = Path(p['installed']['backup']) / 'registry-before.csv'
shutil.copy2(registry, backup)
rows = [dict(zip(fields, values))] + [row for row in rows if row[fields[1]] != r['version']]
buffer = io.StringIO()
writer = csv.DictWriter(buffer, fieldnames=fields)
writer.writeheader()
writer.writerows(rows)
stat = registry.stat()
temp = registry.with_name(registry.name + '.noise-area-tmp')
temp.write_text(buffer.getvalue(), encoding='utf-8-sig')
os.chown(temp, stat.st_uid, stat.st_gid)
os.chmod(temp, stat.st_mode & 0o777)
temp.replace(registry)
proc = subprocess.run(['python3', 'utilities/sync_bmi30_version_registry.py', '--replace'],
                      cwd=D, capture_output=True, text=True, timeout=45)
print(json.dumps({'registry_written':True,'rows':len(rows),'cloud_sync_rc':proc.returncode,
                  'cloud_sync_stdout':proc.stdout,'cloud_sync_stderr':proc.stderr}))
