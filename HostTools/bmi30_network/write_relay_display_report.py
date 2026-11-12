"""Document verified S10 results and copy them to both guarded roots."""
import base64,json
from pathlib import Path
from build_relay_display_release import VERSION,NETWORK_RELEASE,ROOT
from prepare_relay_display import HERE,WORK
from deploy import remote
from write_stability_report import COPY
REPORT=HERE.parent/'RELAY_DISPLAY_2026-10-05_RU.md'
def main():
    result=json.loads((WORK/'verification-summary.json').read_text())
    rows=['# Статус своего реле на S10 — 05.10.2026','',
      'Исправлена ошибка интерфейса, появившаяся при переходе на WebSocket. '
      'Raspberry release **'+result['firmware']+'**, bundle **'+VERSION+'**, сеть **0.1.12** '
      'установлены и прочитаны обратно на USB и eMMC S10.','',
      '## Причина','',
      'При живом WebSocket общий HTTP-опрос выполнялся раз в 5 секунд, а блок своего реле '
      'объявлял данные устаревшими через 2,5 секунды. Обновление WebSocket меняло групповую таблицу и ADC, '
      'но не вызывало обновление собственных элементов реле. Групповая запись также не содержала '
      '`sequence`, `pc1` и `saved_enabled`, необходимых этому блоку. Поэтому интерфейс периодически '
      'показывал `Relay: unavailable` и отключал редактирование, несмотря на свежие чтения реле в службе.','',
      'Пользователь подтвердил: меняются только статус/параметры страницы, физических щелчков нет. '
      'До исправления: 449 чтений `/api/relay` за 45 секунд, во всех `available=true`, '
      '`enabled=false`, `active=false`, `test_enabled=false`; ошибок чтения 0. '
      'Регрессионная проверка на прежнем JavaScript воспроизводит ложное `Relay: unavailable`.','',
      '## Исправление','',
      '- Полное собственное состояние реле передаётся в блок управления через WebSocket.',
      '- Свежесть определяется последовательностью реальных чтений реле и возрастом исходного чтения. '
      'ADC-события и групповые повторы не продлевают жизнь старого состояния реле.',
      '- Поздний HTTP-снимок не может перезаписать более новое свежее состояние WebSocket. '
      'Редактируемое поле длительности не перезаписывается.',
      '- При прекращении чтений работает прежнее истечение через 2,5 секунды; '
      'сохранённое разрешение реле остаётся видимым, редактирование недоступно.','',
      'Правила управления физическим реле, прошивка STM32, RS485, детекция и USB-код не менялись. '
      'Все **22 модуля сбора данных** совпадают побайтово с выпуском 1540. '
      'PID `bmi30-core` остался **1808504**. Перезапущены только сеть и портал. '
      'Настройки приложения, выбора версии, реле и сетевой идентичности каждого носителя '
      'сверены до/после и сохранены; обновлены только метаданные выпуска.','',
      '## Проверки','',
      '- 45 Python-тестов PASS на каждом из двух разделов S10, включая криптографию. '
      'На Windows: 44 PASS и один пропуск Linux-криптографии.',
      '- JavaScript: порядок событий, HTTP/RS485-восстановление, сохранение редактирования, '
      'истечение реального источника, поздний HTTP и восстановление реле. '
      'Все семь скриптов отрендеренной страницы разбираются; тест реле проходит на фактическом отрендеренном коде.',
      '- После установки: **897 чтений за 89,9 секунды**, реле доступно во всех, '
      'разрешение выключено, выход выключен, длительность 1000 мс, ошибок чтения 0.',
      '- В настоящем браузере на видимой групповой странице: **60 снимков за 31,1 секунды**, '
      '`Relay: OFF`, разрешение выключено, поля доступны, 1000 мс, `Live · WebSocket`; '
      'ни одного ложного исчезновения статуса. Физическое реле командами теста не включали.',
      '- USB за 90 секунд: +1437 кадров на каждом канале, новых gaps/CRC/magic/ACK ошибок **0**.','',
      '## Носители и доступность','',
      '| Прибор | Носитель | Выпуск | Проверка | Резервная копия |',
      '|---|---|---|---|---|']
    for root in result['roots']:
        rows.append('| S10 | '+root['device']+' | '+VERSION+' + сеть 0.1.12 | '+
                    ('Действующая загрузка USB' if root['active'] else 'Файлы и тесты; новую загрузку eMMC не выполняли')+
                    ' | /'+root['backup']+' |')
    rows += ['', 'M01, S11, S12 и S13 не отвечают по доверенным SSH-адресам, '
      'а действующий S10 не видит их по быстрому каналу. Причина их недоступности в этом задании '
      'не установлена. **Этот новый выпуск на остальных четырёх приборах не установлен.** '
      'Последняя предыдущая проверка пяти приборов относится к выпуску 1540/сети 0.1.11; '
      'она не подтверждает их текущее состояние.','',
      'Для проверки на уже открытой странице S10 требуется перезагрузка страницы, чтобы получить новый JavaScript.','',
      '## Артефакты','',
      'Пакеты: `FirmwareReleases/RaspberryPi/'+VERSION+'/` и '
      '`FirmwareReleases/RaspberryPi/'+NETWORK_RELEASE+'/`. Они подготовлены локально; '
      'на сервер обновлений не опубликованы. Архивы прежних выпусков сохранены.','',
      'Измерения и readback: `.tmp/relay-display-20261005/`. '
      'Итог: `verification-summary.json`; видимая страница: `S10-group-final.png`. '
      'Документ на обоих носителях S10: `docs/BMI30_RELAY_DISPLAY_2026-10-05_RU.md`.','']
    data='\n'.join(rows).encode('utf-8');REPORT.write_bytes(data)
    for folder in (ROOT/VERSION,ROOT/NETWORK_RELEASE):(folder/'README.md').write_bytes(data)
    script=COPY.replace("=='0.1.2'","=='0.1.12'").replace('BMI30_NETWORK_STABILITY_2026-10-05_RU.md','BMI30_RELAY_DISPLAY_2026-10-05_RU.md')
    inv=json.loads((WORK/'S10-inventory-after.json').read_text());copies=[]
    for root in inv['roots']:
        copied=json.loads(remote(inv['host'],script,{'host':inv['host'],'live':inv['root'],
            'device':root['device'],'uuid':root.get('uuid'),'doc':base64.b64encode(data).decode()},timeout=35))
        copies.append(copied);print(json.dumps(copied),flush=True)
    (WORK/'S10-report-copied.json').write_text(json.dumps(copies,indent=2))
    note=('> Дополнение 05.10.2026: в этом выпуске обнаружено ложное устаревание блока своего реле '
         'между 5-секундными HTTP-опросами. Исправлено в '+VERSION+'/сети 0.1.12 и установлено на USB/eMMC S10. '
         'Остальные четыре прибора сейчас недоступны; новый выпуск на них ещё не установлен. '
         '[Подробности](../../HostTools/RELAY_DISPLAY_2026-10-05_RU.md).\n\n')
    previous=HERE.parent/'GROUP_REALTIME_2026-10-05_RU.md'
    old=previous.read_text(encoding='utf-8')
    if not old.startswith('> Дополнение 05.10.2026:'):
        previous.write_text(note.replace('../../HostTools/RELAY_DISPLAY_2026-10-05_RU.md','RELAY_DISPLAY_2026-10-05_RU.md')+old,encoding='utf-8')
    for folder in (ROOT/'2026-10-05-1540',ROOT/'2026-10-05-network-11'):
        readme=folder/'README.md'
        if readme.exists():
            old=readme.read_text(encoding='utf-8')
            if not old.startswith('> Дополнение 05.10.2026:'):
                readme.write_text(note.replace('../../HostTools/RELAY_DISPLAY_2026-10-05_RU.md','../../../HostTools/RELAY_DISPLAY_2026-10-05_RU.md')+old,encoding='utf-8')
    print(str(REPORT))
if __name__=='__main__':main()
