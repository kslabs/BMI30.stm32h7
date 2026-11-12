"""Record the installed M02 mean/P95 release in its required version journal."""
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
    'Оценка шума max(среднее, P95) по скользящей истории',
    'Верхний и нижний каналы: максимум среднего и линейно интерполированного 95-го процентиля измерений шума за выбранное окно. Текущие 5 с и область 66% сохранены. Пространственный максимум вне зоны около импульса, мгновенная проверка S/N, заданные коэффициенты и существующая формула порога не менялись. На 2 Tag Detection метод показан только для чтения, Noise history window остаётся регулируемым. В API и снимках решения добавлены среднее, P95, число измерений и название метода; legacy noise_avg теперь содержит итоговую оценку.',
    r['paths']['core'], r['paths']['engine'], r['paths']['gui'],
    'hotspot_info_server.py; /usr/local/bin/bmi30-hotspot-info-server.py',
    'AVG_ROI / режим 6; оба канала; STM32 avg16 / Raspberry avg8, S/N low3 high1.5, подтверждения5 сохранены',
    '48 тестов на M02, включая 10 новых; проверены реальные функции обработки, независимость каналов, срок истории, сброс, исключительные значения и неизменность геометрии/мгновенной проверки/формулы коэффициента. Изолированный расчёт на M02 около 0.14–0.15 мс. После установки API подтверждает max(mean,P95) на обоих каналах; 10 с без прироста gap/dup/CRC/magic/ACK, переполнений и задержек >100 мс; форма отрисована установленным сервером.',
    'Только активная M02 /dev/sda2. Настройки и исходные файлы сохранены в резервной копии. Неактивная eMMC, S07 и STM32 не обновлялись. Чувствительность к реальным меткам ещё не проверена; короткая проверка исправности не доказывает снижение частоты ложных тревог.',
    'Записать проходы меток и фон; сравнить окна 1, 2 и 5 с по пропускам и ложным срабатываниям.',
    datetime.datetime.now(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=2))).isoformat()]
assert len(fields) == len(values)
backup = Path(p['installed']['backup']) / 'registry-before.csv'
assert not backup.exists()
shutil.copy2(registry, backup)
rows = [dict(zip(fields, values))] + [row for row in rows if row[fields[1]] != r['version']]
buffer = io.StringIO()
writer = csv.DictWriter(buffer, fieldnames=fields)
writer.writeheader()
writer.writerows(rows)
stat = registry.stat()
temp = registry.with_name(registry.name + '.noise-percentile-tmp')
temp.write_text(buffer.getvalue(), encoding='utf-8-sig')
os.chown(temp, stat.st_uid, stat.st_gid)
os.chmod(temp, stat.st_mode & 0o777)
temp.replace(registry)
proc = subprocess.run(['python3', 'utilities/sync_bmi30_version_registry.py', '--replace'],
                      cwd=D, capture_output=True, text=True, timeout=45)
print(json.dumps({'registry_written': True, 'rows': len(rows), 'cloud_sync_rc': proc.returncode,
                  'cloud_sync_stdout': proc.stdout, 'cloud_sync_stderr': proc.stderr}))
