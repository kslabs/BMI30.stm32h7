"""Write the measured fleet report and copy it to the verified installations."""
import argparse
import base64
import json
from pathlib import Path

from audit_stability import HOSTS, WORK
from deploy import remote
from deploy_stability import DEST, VERSION

REPORT = Path(__file__).resolve().parents[1] / 'GROUP_STABILITY_2026-10-05_RU.md'


def write():
    result=json.loads((WORK/'verification-summary.json').read_text())
    live={b:json.loads((WORK/(b+'-live.json')).read_text()) for b in HOSTS}
    assert {v['portal_firmware']['stm32_firmware_version'] for v in live.values()}=={'1.2.53'}
    assert {v['portal_firmware']['raspberry_firmware_version'] for v in live.values()}=={'2026-10-02-094828'}
    lines=['# Стабильность групповой страницы BMI30 — 05.10.2026','',
           'Исправлены автоматические колебания диапазона Wi-Fi и краткая ложная недоступность здорового сборщика данных. '
           'Сетевой компонент **0.1.2**, выпуск **2026-10-05-network-02**, установлен и прочитан обратно на **5 eMMC + 4 USB**. '
           'Приложение: bundle **2026-10-02-0948**, release **2026-10-02-094828**. Все пять работающих STM32 сообщают **1.2.53**.','',
           '## Наблюдение до исправления','',
           'В журнале M01/S10/S13 многократные переходы 5180 ↔ 2417 МГц, часто через 340–400 с. '
           'В текущей серии из 360 снимков за 180 с S13 отсутствовал в группе на четырёх других устройствах '
           '25–29 снимков, то есть примерно 12,5–14,5 с. В это время S13 сменил диапазон: '
           'запись `Route switched: direct None 2417 MHz` от 12:50:05 CEST. SSH-наблюдение S13 прервалось '
           'с `Connection reset`; остальные четыре наблюдения завершены. Их core оставался свежим, worker работал.','',
           'Отдельно зафиксированы ложные `available=false` с `cache_age_s=-0.001` '
           '(два снимка M01 и один S13). Время брали до запроса core, затем читали более новый кэш.','',
           '## Исправление','',
           '- Неизвестная WAN-задержка альтернативного SSID больше не оценивается как преимущество. '
           'Все прямые кандидаты сравниваются с одинаковой текущей оценкой WAN; реальный интернет проверяется при активации.',
           '- Вход в предпочтительный 5 ГГц при сигнале не ниже 55%; удержание до 40%. '
           'Раздельные границы исключают колебания около одного порога. Слабый 5 ГГц и отказ интернета позволяют переход на 2,4 ГГц.',
           '- Сохранены подтверждение тремя наблюдениями, выдержка 300 с, запрет улучшений при подключённых AP-клиентах, '
           'защита здоровой RDP-сессии, проверка маршрута и NetworkManager rollback.',
           '- Время групповой записи берётся после чтения core и кэша. Свежий кэш, появившийся во время HTTP-запроса, '
           'больше не объявляется будущим.',
           '- TTL групповой телеметрии остаётся 4 с. Устаревшие состояния и удалённые команды по-прежнему отключаются; RS485 остаётся резервом.','',
           '## Версии и носители','',
           '| Прибор | eMMC | USB | Текущая загрузка | STM32 |',
           '|---|---|---|---|---|']
    for board in HOSTS:
        rows=[r for r in result['roots'] if r['board']==board]
        active=next(r for r in rows if r['active'])
        lines.append('| '+board+' | 2026-10-02-0948 + сеть 0.1.2 | '+
                     ('Не подключён' if board=='S12' else '2026-10-02-0948 + сеть 0.1.2')+' | '+
                     ('eMMC' if 'mmcblk' in active['device'] else 'USB')+' | 1.2.53 |')
    lines += ['', 'Проверены SHA-256 **24 файлов приложения** и **10 файлов сетевого пакета** на каждом из девяти root. '
              'Сохранены отдельные настройки/идентичности каждого прибора и носителя: после предыдущего копирования '
              'некоторые настройки активного USB отличаются от eMMC, эти различия не перезаписывались. '
              'Core/engine/GUI/USB stream/portal побайтово сохранены. PID core всех пяти приборов прежние. '
              'Перезапускалась только `bmi30-network`.','',
              'M01/S10/S11/S13 проверены в реальной работающей системе с `/dev/sda2`; S12 с `/dev/mmcblk0p2`. '
              'Неактивные eMMC четырёх приборов проверены чтением файлов и запуском тестов их сетевого кода '
              'из действующей Linux-системы. Новых перезагрузок на eMMC не выполняли. '
              'Это проверка версии приложения и сетевого компонента, не утверждение о полной идентичности всех файлов ОС.','',
              '## Проверки','',
              'На всех девяти root **29 Python-тестов PASS** (включая криптографию на Linux). '
              'На Windows 28 PASS + один пропуск из-за отсутствия cryptography. '
              'Два новых сценария воспроизводят ошибки на исходных файлах, прочитанных из резервной копии M01; '
              'оба проходят на исправленных файлах. UI JavaScript не менялся.','']
    if len(result['after'])==5:
        lines += ['После начальной стабилизации выполнено параллельное наблюдение всех пяти приборов '
                  'по 480 с с интервалом 0,5 с. Первые 180 с отдельной 660-секундной серии исключены: '
                  'там допустим однократный выбор предпочтительного диапазона после перезапуска.','',
                  '| Наблюдатель | Снимки | Пропавшие / недоступные участники | Новые gaps / CRC / magic / ACK | Кадры A / B |',
                  '|---|---:|---|---|---|']
        for board in HOSTS:
            s=result['after'][board];d=s['stream_delta']
            assert not s['missing'] and not s['unavailable'] and not s['core_issues'],board+': observation issues'
            errors=[d[k] for k in ('seq_gap_ch0','seq_gap_ch1','crc_bad','magic_bad','host_rx_ack_fail')]
            assert errors==[0]*5,board+': stream errors'
            lines.append('| '+board+' | '+str(s['samples'])+' | 0 / 0 | 0 / 0 / 0 / 0 | '+str(d['rx_cnt_ch0'])+' / '+str(d['rx_cnt_ch1'])+' |')
        lines += ['', 'Координатор M01, каждый прибор видит пять участников. '
                  'M01/S11/S12/S13 удерживают 5 ГГц. S10 сохраняет 2,4 ГГц при подключённом AP-клиенте; '
                  'это предусмотренная защита действующего соединения. '
                  'Результат ограничен этой серией наблюдений; длительная работа и физические проходы меток '
                  'в данном задании не измерялись.','']
    else:
        lines += ['Длительное наблюдение после установки ещё выполняется.','']
    lines += ['## Резервные копии и артефакты','',
              'До замены каждого root сохранены сетевые исходники и метаданные в локальном '
              '`/var/backups/bmi30-network-stability/<timestamp>/`. Резервные копии остаются на соответствующем носителе.','',
              '| Прибор | Носитель | Резервная копия |','|---|---|---|']
    for r in result['roots']:lines.append('| '+r['board']+' | '+r['device']+' | /'+r['backup']+' |')
    lines += ['', 'Пакет: `FirmwareReleases/RaspberryPi/2026-10-05-network-02/`. '
              'Это отдельный сетевой пакет; он не изменяет номер полного websplit bundle и не является полным образом ОС. '
              'Архив не опубликован на сервере. Контрольная сумма архива: '
              '`'+(DEST/'SHA256SUMS').read_text().split()[0]+'`.','',
              'Полные измерения: `.tmp/group-stability-20261005/`; первоначальные данные в `baseline/`; '
              'итоговые проверки `verification-summary.json`, `*-after-summary.json`, `*-after-observe.json`.','']
    REPORT.write_text('\n'.join(lines),encoding='utf-8')
    (DEST/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    print(str(REPORT))


COPY = r'''
import base64,json,os,pathlib,subprocess,sys
p=json.load(sys.stdin);run=lambda *a:subprocess.check_output(a,text=True,stderr=subprocess.PIPE).strip()
assert run('hostname')==p['host'];assert run('findmnt','-n','-o','SOURCE','/')==p['live']
root=pathlib.Path('/');mounted=False
if p['device']!=p['live']:
 assert run('blkid','-s','UUID','-o','value',p['device'])==p['uuid']
 assert subprocess.run(['findmnt','-rn','-S',p['device']],stdout=subprocess.DEVNULL).returncode!=0
 root=pathlib.Path('/mnt/bmi30-network-doc-20261005');root.mkdir(exist_ok=False)
 run('mount',p['device'],str(root));mounted=True
try:
 assert (root/'etc/hostname').read_text().strip()==p['host']
 assert json.loads((root/'opt/bmi30-network/release.json').read_text())['version']=='0.1.2'
 docs=root/'home/techaid/Documents/docs';st=docs.stat();f=docs/'BMI30_NETWORK_STABILITY_2026-10-05_RU.md'
 data=base64.b64decode(p['doc']);f.write_bytes(data);os.chown(f,st.st_uid,st.st_gid);os.chmod(f,0o644)
 assert f.read_bytes()==data
 print(json.dumps({'host':p['host'],'device':p['device'],'report_copied':True}))
finally:
 if mounted:run('umount',str(root));root.rmdir()
'''


def copy():
    for board,host in HOSTS.items():
        audit=json.loads((WORK/(board+'-inventory.json')).read_text())
        for r in audit['roots']:
            print(remote(host,COPY,{'host':host,'live':audit['root'],'device':r['device'],'uuid':r.get('uuid'),
                                  'doc':base64.b64encode(REPORT.read_bytes()).decode()},timeout=35).decode().strip())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--copy',action='store_true');args=parser.parse_args()
    write()
    if args.copy:copy()
