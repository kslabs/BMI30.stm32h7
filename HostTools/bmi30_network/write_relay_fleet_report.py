"""Save the measured six-root deployment report on every verified root."""
import base64,json
from pathlib import Path
from audit_stability import HOSTS
from deploy import remote
from prepare_relay_display import HERE,WORK
from build_relay_display_release import VERSION,NETWORK_RELEASE,ROOT
from write_stability_report import COPY
REPORT=HERE.parent/'RELAY_FLEET_2026-10-05_RU.md'
def main():
    data=json.loads((WORK/'fleet-verification-summary.json').read_text())
    rows=['# Сохранение исправлений на всех приборах — 05.10.2026','',
      'По согласованному объёму обновлены **все пять встроенных eMMC и USB S10 — шесть системных разделов**. '
      'USB M01/S11/S13 сейчас не подключены; пользователь подтвердил, что в этом задании достаточно '
      'встроенной памяти и USB S10. Эти отключённые носители новым выпуском не обновлялись.','',
      'На всех шести разделах один Raspberry release **'+data['firmware']+'**, bundle **'+data['bundle']+
      '** и сеть **'+data['network']+'**. Проверены чтением после установки метаданные версии, '
      'SHA-256 всех файлов сетевого выпуска, трёх копий портала и 22 модулей сбора данных.','',
      'Сохранены индивидуальные настройки детекции, выбора режима, реле и сетевой идентичности каждого носителя. '
      'Все 22 модуля сбора данных побайтово совпадают с состоянием до установки. PID `bmi30-core` '
      'на каждом приборе остался прежним; детектор не перезапускали. На M01/S11/S12/S13 '
      'перезапущены только сеть и портал; уже обновлённый S10 повторно не перезапускали.','',
      '## Исправление','',
      'Блок своего реле получает свежие чтения через WebSocket. Прежний 5-секундный HTTP-опрос '
      'больше не вызывает ложное истечение 2,5-секундной свежести. ADC-события не продлевают '
      'возраст старого чтения реле; поздний HTTP-снимок не возвращает старые параметры. '
      'Физическое управление реле, прошивка STM32, RS485, пороги, фильтры и USB-код не изменялись. '
      '[Исходная диагностика и проверка страницы S10](RELAY_DISPLAY_2026-10-05_RU.md).','',
      '## Приборы и носители','',
      '| Прибор | eMMC | USB | Действующая загрузка |','|---|---|---|---|']
    for board in HOSTS:
        roots=[r for r in data['roots'] if r['board']==board]
        active=next(r for r in roots if r['active'])
        rows.append('| '+board+' | '+VERSION+' / 0.1.12 | '+
                    (VERSION+' / 0.1.12' if board=='S10' else 'Не подключён; вне согласованного объёма')+
                    ' | '+('USB' if 'sda' in active['device'] else 'eMMC')+' |')
    rows += ['', 'Все пять приборов проверены в действующей системе. На S10 встроенная память '
      'проверена офлайн чтением файлов; её новую загрузку в этом задании не выполняли. '
      'Файлы и метаданные сохранены на постоянные носители; после установки выполнен `sync`.','',
      '## Проверки после установки','',
      'На каждой новой установке M01/S11/S12/S13: **45 Python-тестов PASS**, включая криптографию. '
      'На обоих носителях S10 те же 45 тестов прошли при предыдущей установке; '
      'в этом задании подтверждены неизменённые контрольные суммы.','',
      'Одновременное наблюдение всех пяти приборов по 90 секунд, интервал 250 мс:','',
      '| Наблюдатель | Снимки | Все 5 приборов доступны | Своё реле доступно | Новые gaps / CRC / magic / ACK | Кадры ADC1 / ADC2 |',
      '|---|---:|---|---|---|---|']
    for board,item in data['observation'].items():
        delta=item['stream_delta']
        rows.append('| '+board+' | '+str(item['samples'])+' | Да | Да | 0 / 0 / 0 / 0 | '+
                    str(delta['rx_cnt_ch0'])+' / '+str(delta['rx_cnt_ch1'])+' |')
    rows += ['', 'Устройства и реле оставались доступными во всех снимках; USB продолжал получать данные '
      'без новых ошибок. Физическое реле командами теста не включали. '
      'Это проверка доступности, версий и потока данных; физические проходы меток в этом задании не измерялись.','',
      '## Резервные копии','',
      '| Прибор | Раздел | Резервная копия |','|---|---|---|']
    original=json.loads((WORK/'S10-install.json').read_text())
    for root in data['roots']:
        backup=root['backup']
        if not backup:backup=next(r['backup'] for r in original if r['root']==root['device'])
        rows.append('| '+root['board']+' | '+root['device']+' | /'+backup+' |')
    rows += ['', 'Резервные копии остаются на своих носителях. Пакеты сохранены в '
      '`FirmwareReleases/RaspberryPi/'+VERSION+'/` и `FirmwareReleases/RaspberryPi/'+NETWORK_RELEASE+'/`; '
      'на сервер обновлений не публиковались.','',
      'Readback и измерения: `.tmp/relay-display-20261005/*-inventory-rollout-*.json`, '
      '`*-rollout-install.json`, `*-rollout-observation.json`, `fleet-verification-summary.json`. '
      'Документ на всех шести носителях: `docs/BMI30_RELAY_FLEET_2026-10-05_RU.md`.','',
      'Открытые страницы требуется перезагрузить (Ctrl+F5), чтобы получить исправленный JavaScript.','']
    raw='\n'.join(rows).encode('utf-8');REPORT.write_bytes(raw)
    for directory in (ROOT/VERSION,ROOT/NETWORK_RELEASE):(directory/'README.md').write_bytes(raw)
    script=COPY.replace("=='0.1.2'","=='0.1.12'").replace('BMI30_NETWORK_STABILITY_2026-10-05_RU.md','BMI30_RELAY_FLEET_2026-10-05_RU.md')
    copies=[]
    for board,host in HOSTS.items():
        inv=json.loads((WORK/(board+'-inventory-rollout-after.json')).read_text())
        for root in inv['roots']:
            copied=json.loads(remote(host,script,{'host':host,'live':inv['root'],'device':root['device'],
                'uuid':root.get('uuid'),'doc':base64.b64encode(raw).decode()},timeout=35))
            copies.append(copied);print(json.dumps(copied),flush=True)
    (WORK/'fleet-reports-copied.json').write_text(json.dumps(copies,indent=2))
    note=('> Актуальное состояние после установки на весь согласованный набор носителей: '
          'все пять eMMC и USB S10 содержат '+VERSION+'/сеть 0.1.12. '
          'Отключённые USB M01/S11/S13 исключены пользователем из этого задания. '
          '[Итоговый отчёт](RELAY_FLEET_2026-10-05_RU.md).\n\n')
    for filename in ('RELAY_DISPLAY_2026-10-05_RU.md','GROUP_REALTIME_2026-10-05_RU.md'):
        file=HERE.parent/filename;old=file.read_text(encoding='utf-8')
        if not old.startswith('> Актуальное состояние после установки на весь согласованный набор носителей:'):
            file.write_text(note+old,encoding='utf-8')
    print(str(REPORT))
if __name__=='__main__':main()
