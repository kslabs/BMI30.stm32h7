"""Record the verified release in each installation's existing version journal."""
import base64
import json
from pathlib import Path
from build_fleet_release import WORK, VERSION
from deploy_fleet_release import HOSTS, remote

ENTRY = {
    '№ в меню':'1','ID версии':VERSION,
    'Статус':'Установлена: 5 eMMC + USB M01/S10; опубликована на сервере 02.10.2026 10:26 CEST',
    'Коротко':'HTTPS: незавершённый TLS-клиент больше не блокирует приём остальных клиентов.',
    'Что сделано / зачем нужна':'TLS-рукопожатие вынесено из accept в поток клиента; таймаут сокета 15 с, очередь 128. Новый неизменяемый полный bundle. Настройки всех семи установок сохранены, процессы core не перезапускались.',
    'Core':'host/BMI30.001.py.2026-09-30-1650 (без изменений)',
    'Engine':'host/BMI30.200.py.2026-09-30-1650 (без изменений)',
    'GUI':'host/BMI30.GUI.001.py.2026-09-30-1650 (без изменений)',
    'Portal':'hotspot_info_server.py; установленный /usr/local/bin/bmi30-hotspot-info-server.py и active runtime',
    'Режимы':'HTTP/HTTPS. Детекция, режимы, оптика, реле, USB/RS485 и STM32 сохранены.',
    'Проверка':'5 изолированных TLS/HTTP тестов PASS. HTTP/HTTPS 200 на всех 5 приборах; HTTPS работает при зависшем рукопожатии другого клиента. 16 снимков/30 с каждого: поток свежий, worker жив, новых seq_gap/CRC/magic/ACK/overflow нет. 21 Python-компонент идентичен, PID core сохранены. Обе неактивные установки проверены по SHA-256 без загрузки.',
    'Важные примечания':'Release 2026-10-02-094828; created 2026-10-02T07:48:28Z. Все 7 root обновлены. S10 USB serial 25072933770324 появился 09:48. RAM 546.5–643.3 МиБ, swap 0. Причина прежнего отказа оптики M01 не доказана; после перезагрузки работа подтверждена пользователем. После первоначального HTTP 403 пользователь зарегистрировал приборы: публикация исходного архива успешна 2026-10-02T08:26:38Z, latest_updated=true, verified_download=true. Все 5 приборов подтвердили версию/SHA-256; S10 скачал архив без установки. SHA-256 97412b1c3604e460fee27cd57d22e79b486bdd50de25b42f62b70b047e46caf0.',
    'Следующий шаг':'Наблюдать длительную работу портала; при последующих изменениях создавать новый неизменяемый выпуск.',
    'Обновлено':'2026-10-02T08:26:38+00:00',
}

SCRIPT = r'''
import base64,csv,hashlib,io,json,os,pathlib,shutil,subprocess,sys,time
p=json.load(sys.stdin);run=lambda *a:subprocess.check_output(a,text=True).strip()
assert run('hostname')==p['host'];root=pathlib.Path('/');mounted=False
if p.get('device'):
 assert run('findmnt','-n','-o','SOURCE','/')!=p['device']
 assert run('blkid','-s','UUID','-o','value',p['device'])==p['uuid']
 assert subprocess.run(['findmnt','-rn','-S',p['device']],stdout=subprocess.DEVNULL).returncode!=0
 root=pathlib.Path('/mnt/bmi30-portal-journal-20261002');root.mkdir(exist_ok=False)
 run('mount',p['device'],str(root));mounted=True
try:
 d=root/'home/techaid/Documents';f=d/'docs/BMI30_version_registry_google_sheet.csv';st=f.stat()
 assert (root/'etc/hostname').read_text().strip()==p['host']
 assert 'BMI30_SPLIT_VERSION='+p['version'] in (d/'host/bmi30_split_active_version.env').read_text()
 for portal in [root/'usr/local/bin/bmi30-hotspot-info-server.py',d/'hotspot_info_server.py',d/'host/bmi30_active_runtime/project/hotspot_info_server.py']:
  assert hashlib.sha256(portal.read_bytes()).hexdigest()==p['portal_sha256']
 with f.open(encoding='utf-8-sig',newline='') as stream:
  reader=csv.DictReader(stream);fields=reader.fieldnames;rows=list(reader)
 backup=d/'.tmp/fleet-portal-20261002';backup.mkdir(exist_ok=True)
 saved=backup/'version-registry-before-portal.csv'
 if not saved.exists():shutil.copy2(f,saved)
 rows=[r for r in rows if r.get('ID версии')!=p['version']]
 for r in rows:
  if r.get('ID версии')=='2026-09-30-1650':r['Статус']='Предыдущий полный bundle; заменён на 2026-10-02-0948, acquisition-компоненты сохранены'
 rows.insert(0,{key:p['entry'].get(key,'') for key in fields})
 buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=fields);writer.writeheader();writer.writerows(rows)
 temp=f.with_name(f.name+'.portal-journal-new');temp.write_bytes(buf.getvalue().encode('utf-8-sig'))
 os.chmod(temp,st.st_mode);os.chown(temp,st.st_uid,st.st_gid);temp.replace(f)
 doc=d/'docs/PORTAL_FLEET_2026-10-02_RU.md';doc.write_bytes(base64.b64decode(p['doc']));os.chown(doc,st.st_uid,st.st_gid)
 with f.open(encoding='utf-8-sig',newline='') as stream:
  assert sum(r['ID версии']==p['version'] for r in csv.DictReader(stream))==1
 run('sync','-f',str(root));print(json.dumps(dict(host=p['host'],root=p.get('device') or '/',journal_updated=True,portal_readback_ok=True)))
finally:
 if mounted:run('umount',str(root));root.rmdir()
'''

if __name__=='__main__':
    doc=Path('HostTools/PORTAL_FLEET_2026-10-02_RU.md').read_bytes()
    release=json.loads((WORK/'release.json').read_text())
    roots=[(board,None,None) for board in HOSTS]
    roots += [('M01','/dev/mmcblk0p2','fb90e995-3446-4991-8468-02d2544a3521'),
              ('S10','/dev/sda2','d24efc0b-47e6-444f-bc0a-aff5691c7266')]
    results=[]
    for board,device,uuid in roots:
        payload=dict(host=HOSTS[board],version=VERSION,entry=ENTRY,doc=base64.b64encode(doc).decode(),
            portal_sha256=release['hashes']['hotspot_info_server.py'],device=device,uuid=uuid)
        result=json.loads(remote(HOSTS[board],SCRIPT,payload));results.append(result);print(json.dumps(result),flush=True)
    (WORK/'journal-results.json').write_text(json.dumps(results,indent=2))
    script=r'''
import json,subprocess
r=subprocess.run(['sudo','-n','-u','techaid','python3','utilities/sync_bmi30_version_registry.py','--replace'],cwd='/home/techaid/Documents',capture_output=True,text=True,timeout=90)
print(json.dumps({'exit_code':r.returncode,'output':r.stdout[-1500:],'error':r.stderr[-500:]}))
'''
    result=json.loads(remote(HOSTS['S13'],script,timeout=100))
    (WORK/'journal-sync.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
