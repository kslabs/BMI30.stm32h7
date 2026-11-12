"""Record the release using the project's established CSV/Sheet workflow."""
import base64
import json
from pathlib import Path

from build_release import VERSION
from deploy import HOSTS, WORK, remote

SCRIPT = '''
import base64,csv,datetime,io,json,os,pathlib,shutil,subprocess,sys
p=json.load(sys.stdin);docs=pathlib.Path('/home/techaid/Documents');path=docs/'docs/BMI30_version_registry_google_sheet.csv'
with path.open(encoding='utf-8-sig',newline='') as f:reader=csv.DictReader(f);headers=reader.fieldnames;rows=list(reader)
backup=docs/'.tmp/network-20260930/version-registry-before-network.csv'
if not backup.exists():shutil.copy2(path,backup)
rows=[r for r in rows if r.get('ID версии')!=p['version']]
for r in rows:
 if r.get('ID версии')=='2026-09-30-1135':r['Статус']='Предыдущий полный bundle; acquisition-компоненты сохранены в 2026-09-30-1449; USB-копии не обновлены новым portal'
rows.insert(0,{key:p['entry'].get(key,'') for key in headers})
for i,row in enumerate(rows,1):row['№ в меню']=str(i)
temp=path.with_name('.'+path.name+'.network')
with temp.open('w',encoding='utf-8',newline='') as f:writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader();writer.writerows(rows)
st=path.stat();os.chmod(temp,st.st_mode);os.chown(temp,st.st_uid,st.st_gid);temp.replace(path)
doc=docs/'docs/BMI30_FAST_NETWORK_2026-09-30_RU.md';doc.write_bytes(base64.b64decode(p['doc']));os.chown(doc,st.st_uid,st.st_gid)
print(json.dumps({'rows':len(rows),'csv':base64.b64encode(path.read_bytes()).decode()}))
'''

if __name__=='__main__':
    entry={
        'ID версии':VERSION,'Статус':'Активный portal-only bundle на M01, S10, S11, S12, S13; core без перезапуска',
        'Коротко':'BMI30: источник связи в группе, удалённое управление, состояния, автоматический возврат на RS485.',
        'Что сделано / зачем нужна':'Удалены Assigned и Current role. Добавлены Group channel (Local/BMI30 Direct/via/RS485/Offline), uplink/диапазон/сигнал, интернет, реле/LED/звук/ошибка. Роль, ID и параметры оптики доступны для каждого свежего BMI30-узла. Адресация по CM5/STM32, не по ID шины. При потере BMI30 возвращается RS485; удалённое управление отключается. Отдельный сервис /opt/bmi30-network: подписанные состояния, AES-GCM команды/секреты, TTL, дедупликация, маршрутизация через соседей. Автовыбор интернета разрешён с защитой здоровых RDP/AP соединений. Координатор Master, минимальный ID, уникальный CM5.',
        'Core':'host/BMI30.001.py.2026-09-30-1135 (байты и PID сохранены)',
        'Engine':'host/BMI30.200.py.2026-09-30-1135 (без изменений)',
        'GUI':'host/BMI30.GUI.001.py.2026-09-30-1135 (без изменений)',
        'Portal':'hotspot_info_server.py; /usr/local/bin/bmi30-hotspot-info-server.py; network service отдельно',
        'Режимы':'Групповая страница и CM5 сеть. Режим детекции, STM32, RS485/USB, параметры приборов сохранены.',
        'Проверка':'22 Python-теста CM5 PASS. JS compile 7 scripts PASS, проверки идентичности/повторяющихся RS485 ID/fallback PASS. Пять участников на каждой плате. Chrome S13: удалённый hold S10 3->4->3 с. Остановка сети S10: RS485 и отключение remote controls; восстановление BMI30; core PID 86569 неизменен. Физический relay M01->S11: обе HTTPS пробы успешны, родитель аутентифицирован, прежний UUID/BSSID восстановлены. Штатная валидация bundle PASS; настройки и все пять PID core сохранены, кадры свежие.',
        'Важные примечания':'Release 2026-09-30-144933; created 2026-09-30T12:49:33Z. Portal-only live update: core/engine/GUI/USB идентичны предыдущему bundle и не перезапускались. Network service устанавливается отдельно, ключи в bundle не входят. Новые неизвестные устройства и полностью офлайн multihop bootstrap ещё не завершены. Нижние панели LED/Relay локальные. 50 мс не реализованы: текущий файловый кэш имеет минимум 500 мс. Не проверена длительная детекция реальными метками под сетевой нагрузкой. Неактивные USB-копии не обновлены; серверной публикации нет.',
        'Следующий шаг':'Асинхронная очередь событий до файлового кэша; немедленная отправка изменений и heartbeat 0,5–1 с; измерение p50/p95/p99 и превышений 50 мс. Доверенное добавление новых устройств и полностью офлайн дерево.',
        'Обновлено':'2026-09-30T12:49:33+00:00'}
    doc=Path('HostTools/BMI30_FAST_NETWORK_2026-09-30_RU.md').read_bytes()
    result=json.loads(remote(HOSTS[-1],SCRIPT,{'version':VERSION,'entry':entry,'doc':base64.b64encode(doc).decode()}))
    (WORK/'version-registry.csv').write_bytes(base64.b64decode(result['csv']))
    copy='''import base64,json,os,pathlib,sys,shutil
p=json.load(sys.stdin);d=pathlib.Path('/home/techaid/Documents');path=d/'docs/BMI30_version_registry_google_sheet.csv';st=path.stat()
backup=d/'.tmp/network-20260930/version-registry-before-network.csv'
if not backup.exists():shutil.copy2(path,backup)
path.write_bytes(base64.b64decode(p['csv']));os.chown(path,st.st_uid,st.st_gid)
doc=d/'docs/BMI30_FAST_NETWORK_2026-09-30_RU.md';doc.write_bytes(base64.b64decode(p['doc']));os.chown(doc,st.st_uid,st.st_gid)
print('Journal and documentation updated')'''
    for host in HOSTS[:-1]:
        print(host,remote(host,copy,{'csv':result['csv'],'doc':base64.b64encode(doc).decode()}).decode().strip())
    sync='''import subprocess,json,pwd,os
u=pwd.getpwnam('techaid')
r=subprocess.run(['sudo','-n','-u','techaid','python3','utilities/sync_bmi30_version_registry.py','--replace'],cwd='/home/techaid/Documents',capture_output=True,text=True,timeout=90)
print(json.dumps({'sync_exit_code':r.returncode,'output':r.stdout[-1200:],'error':r.stderr[-400:]}))'''
    raw=remote(HOSTS[-1],sync,timeout=100)
    (WORK/'journal-sync-result.json').write_bytes(raw)
    print(raw.decode())
