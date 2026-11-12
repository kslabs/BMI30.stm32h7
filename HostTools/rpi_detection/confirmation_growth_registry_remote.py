"""Append the required Russian version journal using the existing sync utility."""
import csv
import datetime
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

p = json.load(sys.stdin)
r, installed, checked = p['release'], p['installed'], p['verification']
D = Path('/home/techaid/Documents')
assert subprocess.check_output(['hostname'],text=True).strip() == 'BMI30-E29805C7C'
assert 'BMI30_SPLIT_VERSION='+r['version']+'\n' in (D/'host/bmi30_split_active_version.env').read_text()
registry = D/'docs/BMI30_version_registry_google_sheet.csv'
reader = csv.DictReader(io.StringIO(registry.read_text(encoding='utf-8-sig')))
fields, rows = reader.fieldnames, list(reader)
values = ['1', r['version'], 'Установлена M02: текущая активная система '+checked['active_root'],
    'Рост амплитуды подтверждений: галочка и коэффициент в одной строке',
    'После C последовательных подтверждений последний пик произведения должен быть не меньше первого, умноженного на коэффициент. Коэффициент по умолчанию 2.0, диапазон 1–100; общая галочка по умолчанию выключена, цепочки антенн независимы. Сравнение неокруглённое, промежуточный монотонный рост не требуется. При отказе проверяется следующее окно из C подтверждений; пропуск/разрыв/сброс фазы удаляет старое начало. Требуется C>=2. Фильтр разрешает начало события; существующие правила HOLD сохранены. Настройки сохраняются через портал/API и загружаются Core; изменение очищает цепочки и поколение worker без сброса шума. Добавлены русские подписи и подсказка. По уточнению пользователя галочка и поле коэффициента объединены в одной строке; финальное изменение портала установлено без перезапуска Core, PID сохранён.',
    r['paths']['core'],r['paths']['engine'],r['paths']['gui'],
    'hotspot_info_server.py; /usr/local/bin/bmi30-hotspot-info-server.py',
    'Активный websplit, обе антенны; усреднение, коэффициенты, фильтры и существующие настройки сохранены',
    p['tests']+'; установленная форма проверена серверным рендером и DOM, включая русские подписи; живой M02: '+str(checked['new_worker_results'])+' результатов за 10 с, прирост gap/duplicate/CRC/magic/ACK/overflows 0.',
    'Только M02 '+checked['active_root']+'. Другая загрузочная система, другие платы и STM32 не изменялись. Новая галочка оставлена выключенной. Реальные проходы и эффективность подавления ложных срабатываний пока не проверены. Резервная копия: '+installed['backup'],
    'Сравнить реальные проходы и длительные записи без метки при одинаковых остальных настройках с фильтром OFF/ON.',
    datetime.datetime.now(ZoneInfo('Europe/Prague')).isoformat()]
assert len(fields) == len(values)
backup = Path(installed['backup'])/'registry-before.csv'
if not backup.exists():
    shutil.copy2(registry, backup)
new_rows = [dict(zip(fields,values))]
if p.get('previous_release'):
    previous = list(values)
    previous[1] = p['previous_release']['version']
    previous[2] = 'Установлена M02; заменена версией '+r['version']
    previous[3] = 'Реализован и проверен фильтр роста первого/последнего пика'
    previous[4] += ' В этой промежуточной версии галочка и коэффициент размещались на двух строках; финальная версия объединяет их.'
    previous[12] = 'Использовать финальную версию '+r['version']+'.'
    new_rows.append(dict(zip(fields,previous)))
replaced = {row[fields[1]] for row in new_rows}
rows = new_rows+[row for row in rows if row[fields[1]] not in replaced]
buffer = io.StringIO()
writer = csv.DictWriter(buffer,fieldnames=fields)
writer.writeheader()
writer.writerows(rows)
stat = registry.stat()
temp = registry.with_name(registry.name+'.confirmation-growth-tmp')
temp.write_text(buffer.getvalue(),encoding='utf-8-sig')
os.chown(temp,stat.st_uid,stat.st_gid)
os.chmod(temp,stat.st_mode & 0o777)
temp.replace(registry)
proc = subprocess.run(['python3','utilities/sync_bmi30_version_registry.py','--replace'],cwd=D,capture_output=True,text=True,timeout=45)
print(json.dumps({'registry_written':True,'rows':len(rows),'cloud_sync_rc':proc.returncode,
                  'cloud_sync_stdout':proc.stdout,'cloud_sync_stderr':proc.stderr}))
