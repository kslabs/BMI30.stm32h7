"""Journal the M02 native-level release and sync the existing version sheet."""
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
assert 'BMI30_SPLIT_VERSION=' + r['version'] + '\n' in (D/'host/bmi30_split_active_version.env').read_text()
registry = D/'docs/BMI30_version_registry_google_sheet.csv'
reader = csv.DictReader(io.StringIO(registry.read_text(encoding='utf-8-sig')))
fields, rows = reader.fieldnames, list(reader)
values = ['1', r['version'], 'Установлена M02: активная USB-система',
    'Исходные уровни сигнала/шума, точность64 бит и запрет подтверждения при нулевом шуме',
    'Проверка показала: произведение уже рассчитывалось в int64, но показания осциллографа делились примерно на16384 и округлялись до0/1. Пример прежнего события: peak36952, текущий шум7140, оценка3744.95, порог11149. Убрана компрессия числовых показаний Live/Freeze/AutoFreeze: peak/noise/trigger и подписи A/THR/max показаны в исходных единицах. Только графические координаты остаются0..65535. Временные вещественные произведения и исходные буферы переведены float32→float64. Удалён стартовый запасной порог, вычисляемый от самого сигнала. Нулевой/нечисловой/недостоверный текущий или накопленный шум запрещает кандидата и разрывает цепочку; актуальная проверка после worker также не допускает нулевой шум. API и решения отмечают raw-product, int64/float64 и noise_valid. Заголовок p/n/t/SN показывает пик, оценку шума, порог и их текущее отношение.',
    r['paths']['core'], r['paths']['engine'], r['paths']['gui'],
    'hotspot_info_server.py; /usr/local/bin/bmi30-hotspot-info-server.py',
    'Режим6 AVG_ROI, оба канала. max(mean,P95), окно5с, область66%, STM32avg16/Raspberryavg8 и текущие настройки сохранены. Геометрия и заданная кривая S/N не менялись.',
    '56 Python-тестов на M02, включая8 новых для int64 произведения, float64 точности, нулевого/недостоверного шума и сброса цепочки. JavaScript:8 форматов исходных уровней, сохранённое событие, нулевой шум без бесконечности, AUTO и неизменность координаты графика. В браузере живой M02 показывает ненулевые исходные уровни; строки не обрезаются. API:468 новых результатов примерно за10с, без прироста gap/dup/CRC/magic/ACK/overflows и >100мс.',
    'Только активная USB M02 /dev/sda2, резервная копия кода и настроек сохранена. STM32, другие платы и неактивная eMMC не изменены. У последнего изученного ложного события шум7140, а не0: масштабирование интерфейса объясняет нули, но не доказывает причину всех ложных детекций. Новая длительная фоновая запись и реальные метки ещё не проверены.',
    'Снять фон и проходы меток в исходных единицах; оценить оставшиеся ложные события при положительном шуме и окна1/2/5с.',
    datetime.datetime.now(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=2))).isoformat()]
assert len(fields) == len(values)
backup = Path(p['installed']['backup'])/'registry-before.csv'
assert not backup.exists()
shutil.copy2(registry, backup)
rows = [dict(zip(fields, values))]+[row for row in rows if row[fields[1]] != r['version']]
buffer = io.StringIO()
writer = csv.DictWriter(buffer, fieldnames=fields)
writer.writeheader()
writer.writerows(rows)
stat = registry.stat()
temp = registry.with_name(registry.name+'.noise-scale-tmp')
temp.write_text(buffer.getvalue(), encoding='utf-8-sig')
os.chown(temp, stat.st_uid, stat.st_gid)
os.chmod(temp, stat.st_mode & 0o777)
temp.replace(registry)
proc = subprocess.run(['python3', 'utilities/sync_bmi30_version_registry.py', '--replace'],
                      cwd=D, capture_output=True, text=True, timeout=45)
print(json.dumps({'registry_written': True, 'rows': len(rows), 'cloud_sync_rc': proc.returncode,
                  'cloud_sync_stdout': proc.stdout, 'cloud_sync_stderr': proc.stderr}))
