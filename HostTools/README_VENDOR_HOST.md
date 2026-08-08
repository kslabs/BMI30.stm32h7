# Vendor USB host: актуальная справка (Windows / PowerShell)

Документ синхронизирован с текущей прошивкой.

Важно
- В Vendor протоколе на IF#2 используется несколько путей управления: Bulk OUT, EP0 control (vendor requests), а также часть диагностических команд в CDC протоколе.
- Команды 0x31 и 0x32 в текущем Vendor коде не являются GET_TEMP/GET_VERSION.
  - 0x31 зарезервирована в Vendor модуле как GET_STATUS_IMM (служебно).
  - 0x32 в Vendor модуле используется как TOGGLE_TIM2CH3_INV.
  - GET_TEMP и GET_VERSION сейчас реализованы в CDC протоколе.
- Правило отчетов по прошивке: всегда указывать связку `ST-LINK SN -> COM -> роль/узел` для каждого устройства. Если прошивка не удалась, в ошибке обязательно писать роль/узел устройства за этим программатором; если COM не отвечает и роль определить нельзя, явно писать `роль неизвестна, COM не отвечает`.
- Правило контроля на этой установке: у Codex нет доступа к USB Vendor/PyUSB. Проверку реакции прошивки, статуса оптики и post-flash диагностику выполнять только через COM-порты диагностического UART/CDC.

## 1. Базовая конфигурация устройства

- VID/PID: 0xCAFE / 0x4001
- Vendor интерфейс: IF#2
- Endpoint (Vendor alt=1): OUT 0x03, IN 0x83
- Alt-setting:
  - alt 0: idle, без endpoint
  - alt 1: активный stream по 0x03/0x83

Минимальная последовательность запуска потока (Bulk)
1. SetInterface(IF#2, alt=1)
2. SET_WINDOWS (0x10), если нужен явный ROI
3. SET_STREAM_MODE (0x1A)
4. При необходимости SET_ASYNC (0x18), SET_PROFILE (0x14), SET_CHMODE (0x19)
5. START_STREAM (0x20)

Примечание
- Ранее в некоторых документах встречалось SET_STREAM_MODE=0x15, это устарело.
- Актуально: SET_STREAM_MODE=0x1A.

## 2. Каналы управления

### 2.1 Bulk OUT (основной путь команд)

Поддерживаемые команды в текущем обработчике:
- 0x10 SET_WINDOWS
- 0x11 SET_BLOCK_HZ
- 0x13 SET_FULL_MODE
- 0x14 SET_PROFILE
- 0x15 SET_ROI_US
- 0x16 SET_TRUNC_SAMPLES
- 0x17 SET_FRAME_SAMPLES
- 0x18 SET_ASYNC_MODE
- 0x19 SET_CHMODE
- 0x1A SET_STREAM_MODE
- 0x1C SET_BUF_RATE_FINE
- 0x1D SET_SYNC_MODE
- 0x1F SET_DC_SPEED
- 0x20 START_STREAM
- 0x21 STOP_STREAM
- 0x22 DEVICE_RESET (режим зависит от сборки: soft/hard)
- 0x2B SAVE_DC_TO_FLASH
- 0x30 GET_STATUS
- 0x32 TOGGLE_TIM2CH3_INV
- 0x33 SET_TX_ENABLE
- 0x34 SET_OPTIC_POWER
- 0x35 LED_EVENT
- 0x36 HOST_RX_ACK
- 0x37 HOST_RX_CLEAR
- 0x39 SET_OPTIC_HOLD
- 0x3A GET_DC_CONFIG
- 0x3B SET_LED_PATTERN
- 0x3C SET_DET_ADC
- 0x3D SET_RS485_ID
- 0x3E SET_RS485_IP
- 0x3F REQUEST_RS485_IDENT
- 0x40 GET_RS485_IDENT
- 0x41 SET_LCD_ROLE_OVERLAY
- 0x42 SET_RPI_INFO
- 0x43 GET_RS485_SENSOR

### 2.2 EP0 Control (vendor requests)

Vendor IN (чтение):
- 0x30 GET_STATUS
- 0x38 GET_LCD_STATUS
- 0x3A GET_DC_CONFIG
- 0x40 GET_RS485_IDENT
- 0x43 GET_RS485_SENSOR

Vendor OUT без data stage:
- 0x7E SOFT_RESET
- 0x7F DEEP_RESET
- 0x20 START_STREAM
- 0x21 STOP_STREAM
- 0x3F REQUEST_RS485_IDENT

Vendor OUT с параметром в wValue (без data stage):
- 0x13, 0x14, 0x18, 0x19, 0x1D, 0x33, 0x34, 0x3B, 0x3C, 0x3D, 0x41 (u8 в младшем байте wValue; для 0x1D режимы MASTER/SLAVE запрещены без timestamp)
- 0x17 (u16 в wValue)
- 0x39 (u16 hold_ds в wValue)

Vendor OUT с data stage:
- 0x13, 0x14, 0x18, 0x19, 0x1D, 0x33, 0x34, 0x39, 0x3B, 0x3C, 0x1F, 0x3D, 0x3E, 0x41, 0x42

### 2.3 CDC протокол (отдельный диагностический канал)

В CDC обработчике присутствуют:
- 0x31 CMD_GET_TEMP
- 0x32 CMD_GET_VERSION

Это не команды Vendor bulk протокола IF#2.

## 3. Форматы получаемых данных

### 3.1 STAT (GET_STATUS, 0x30)

- Сигнатура: STAT
- Актуальный размер структуры: 137 байт
- Версия в структуре: v6

Ключевые поля:
- Базовые счетчики стрима и диагностики
- flags_runtime (битовые флаги состояния)
- reserved3 с optic_power/optic_hold_legacy/tx_enable/optic_active
- v5-поля:
  - optic_hold_ds
  - led_pattern
  - sync_local_status
  - sync_seen_mask
  - sync_node_count
  - sync_status_bytes[32]

Практика
- Для полного набора полей запрашивайте 137 байт. Запрос старого размера 136 байт
  возвращает настоящий совместимый формат v5: bit/index `0..30` соответствует
  `device_id 1..31`; `device_id 0` доступен только в v6.
- Старые хосты, читающие 64 байта, получают только базовую часть.
- Асинхронный Bulk `STAT`, который firmware ставит в очередь при изменении датчика,
  имеет размер 136 байт и сохраняет v5-разметку, поэтому существующий RPI получает
  всю групповую таблицу, а не только 96-байтовый диагностический префикс.
- `GET_STATUS` через EP0 control является диагностическим каналом. Он может отвечать даже тогда, когда Bulk IN поток 0x83 не доходит до reader на Raspberry.
- `GET_STATUS` через Bulk OUT/IN использует тот же Bulk IN путь, что и поток. Для диагностики зависания потока используйте EP0 control, а Bulk-статус считайте только вспомогательным.
- Единый обработанный сигнал оптического датчика `optic_active` хост читает через
  `GET_STATUS` (`0x30`):
  - `flags_runtime & 0x0020` = локальный `optic_active`.
  - `flags_runtime & 0x0080` = master optic активен: локально на master или принят по RS485 на slave.
  - `flags_runtime & 0x0100` = активен любой локальный/RS485 optic в группе.
  - `flags_runtime & 0x0200/0x0400/0x0800` = устаревшие selected-slave flags; в новой
    фиксированной топологии не используются.
  - `flags_runtime & 0x1000` = локальная роль явно сохранена во Flash.
  - `flags_runtime & 0x2000` = в группе есть хотя бы один конфликт `node_id`.
  - `flags_runtime & 0x4000` = локальный master недавно принял sync другого master.
  - `flags_runtime & 0x8000` вместе с `0x1000` = сохранённая роль `MASTER`;
    `0x1000` без `0x8000` = сохранённая роль `SLAVE`.
  - `reserved3 bit0` = тот же локальный `optic_active` в legacy packed-поле.
  - `sync_local_status bit5` = локальный `optic_active`.
  - `sync_local_status bit6` = локальный `DetADC1`, `bit7` = локальный `DetADC2`.
  - `sync_status_bytes[device_id] bit5` = переданный `optic_active` удаленной антенны.
  - `sync_status_bytes[device_id] bit6/bit7` = `DetADC1/DetADC2` удаленной антенны.
- При изменении обработанного `optic_active` устройство должно немедленно
  отправить/поставить в очередь `EVT1 SENSOR_MAP` и `STAT`, чтобы хост не ждал
  следующего опроса. Хост также может опрашивать `GET_STATUS` в любой момент.
- Обработанный сигнал вычисляется одинаково для всех потребителей:
  `optic_active = PD0 || hold_after_last_high`.
  Уровень `PD0=1` немедленно устанавливает `optic_active=1` и повторно запускает
  `optic_hold_ds`. Уровень `PD0=0` сам не запускает и не продлевает таймер.
  После последней принятой `1` сигнал остаётся равен `1` заданное время, затем
  переходит в `0`.
- `PD0` контролируется по обоим EXTI-фронтам и дополнительно опрашивается в основном
  цикле. Если датчик не подключен, аппаратная подтяжка удерживает сырой вход в `0`.

Индикация master optic на slave:
- Запросить `GET_STATUS` длиной 137 байт, лучше через EP0 control: `ctrl_transfer(0xC0, 0x30, 0, 0, 137)`.
- `sync_local_status` находится на offset `99`: это только локальный статус той платы, к которой подключен USB.
- `sync_seen_mask` находится на offset `100..103`, `sync_status_bytes[32]` на offset `105..136`.
- Для каждого `device_id` 0..31 сначала проверить `sync_seen_mask & (1 << device_id)`.
- `sync_seen_mask` и `sync_status_bytes` описывают все видимые узлы с постоянными ID, включая master.
  Маска может быть разреженной: отсутствие ID между двумя занятыми ID не заполняется искусственно.
- Master optic уже передается по RS485 в `master_status0 bit5`; firmware на slave использует этот свежий бит и выставляет для хоста `flags_runtime & 0x0080`.
- Onboard/system address WS2812 при `sync_signal_alive=0` показывает единый голубой
  цвет для любой назначенной роли. Назначенная роль не является подтверждением
  реальной RS485-связи. При живой связи базовый цвет `MASTER` синий, `SLAVE` белый.
  LED учитывает удержанный локальный сигнал и свежие сигналы группы:
  `MASTER` меняет базовый синий на зеленый при локальном или свежем принятом по RS485
  `optic_active=1`; `SLAVE` меняет белый на желтый при локальном
  `optic_active=1`, а при свежем `master_status0 bit5=1` и локальном `optic_active=0`
  меняет белый на магента/фиолетовый.
- Локальный `optic_active=1` имеет на системном WS2812 высший приоритет и немедленно
  меняет только цвет. Индикация включенного TX остаётся независимой: выбранный цвет
  продолжает плавно мигать (`TX-breathe`) весь интервал `optic_active=1`.
- LCD-поле `OPT:0/1` показывает тот же удержанный `optic_active`; сырой `PD0` оставлен
  только в расширенной COM-диагностике `OPTIC`.
- Host на slave должен численно читать master optic из `flags_runtime & 0x0080`.

### 3.2 RS485 identity/IP (GET_RS485_IDENT, 0x40)

У каждой платы есть четыре независимых значения:

- роль `MASTER/SLAVE`;
- `device_id` STM32 в диапазоне `0..31`;
- номер локального RPI `rpi_number`;
- IPv4 локального RPI.

Роль и `device_id` сохраняются во Flash STM32. `rpi_number` и IP сообщает локальный RPI при
загрузке/изменении сети. Master фоново опрашивает все `device_id 0..31`, а каждый слушающий узел
сохраняет услышанные строки. Поэтому любой RPI может прочитать у своей STM32 реплицированную карту
`device_id -> UID STM32 + rpi_number + IP + role`.

Для присутствия устройства всегда использовать `sync_seen_mask`: без status-кадров удалённый ID
исчезает из неё через 1000 ms. `RID1` хранит UID/RPI/IP как фоновый кэш и не является признаком
живой связи. В firmware 1.2.13 строка удалённого устройства полностью очищается после 30 секунд без
обновления; локальная строка не стареет. Хост должен убрать устройство со страницы группы сразу
после очистки соответствующего бита `sync_seen_mask`, а не продолжать показывать сохранённый
`RID1`.

Новый удалённый ID считается реальным после трёх корректных ответов в своём адресном окне master
за время не более 500 ms. Поэтому одиночный повреждённый status/event не создаёт фантомную строку.
Для уже подтверждённого узла sensor-event применяется сразу. `GET_RS485_IDENT` для удалённого ID
экспортирует UID/RPI/IP только пока этот ID жив в `sync_seen_mask`; внутренний 30-секундный кэш
может ускорить возврат устройства, но не должен появляться на странице как подключённый.

#### Обязательная дедупликация на host по STM32 UID96

Полный `STM32 UID96` является постоянным уникальным ключом физической платы. `device_id`, роль,
номер RPI и IP являются изменяемыми атрибутами этой записи и не могут использоваться как primary
key устройства. Требования к backend и frontend портала:

- нормализовать UID96 в один формат (24 hex-символа) и хранить не более одной текущей записи на UID;
- при получении уже известного UID с новым `device_id` атомарно перенести существующую запись на
  новый ID, удалить старую привязку `device_id -> UID` и не создавать вторую карточку;
- если во входном snapshot временно присутствуют два ID с одним UID, оставить только самый свежий
  живой ID; один UID запрещено одновременно показывать как два подключённых устройства;
- строить список подключённых устройств только по `sync_seen_mask`. Сохранённый `RID1`, database
  history, `last_seen` и offline TTL не разрешают оставлять строку в активном списке;
- offline TTL допустим только для журнала/истории. При переносе уже известного UID на новый ID
  старую карточку нужно убрать при обработке того же snapshot/event, не дожидаясь TTL;
- во frontend использовать UID96 как key строки и заменять состояние списка актуальным snapshot,
  иначе локальный UI-кэш может сохранить карточку со старым `device_id`.

Критерий приёмки: после изменения ID на локальном RPI портал ни в одном обновлении не показывает
две активные строки с одинаковым UID96; одна существующая карточка меняет `device_id`. Старый ID
исчезает из `sync_seen_mask` не позднее 1000 ms после прекращения его status-кадров.

Команды хоста:

- `0x3D SET_RS485_ID`: только через локальный USB этой платы, `u8 device_id 0..31`.
- `0x42 SET_RPI_INFO`: payload `u16 rpi_number LE + u8 ip[4]`.
- `0x3E SET_RS485_IP`: legacy payload `u8 ip[4]`; меняет только IP.
- `0x3F REQUEST_RS485_IDENT`: без payload. Отправлять текущему master; master запускает один полный проход страниц identity.
- `0x40 GET_RS485_IDENT`: чтение результата. EP0 IN:
  `ctrl_transfer(0xC0, 0x40, selector, 0, 32)`, где `selector=0..31` означает конкретный
  `device_id`, а `selector=0xFF` — локальную STM32 независимо от её ID.
  Bulk-вариант `[0x40, selector]` допустим только вне stream;
  во время stream используйте EP0.

Назначение нескольких новых плат не выполняется по общей RS485: неназначенные устройства там
нельзя однозначно адресовать. Каждый RPI посылает `SET_RS485_ID` только своей STM32 по локальному
USB. На сервисном ПК выбирается конкретный COM/USB-порт; правильность платы можно дополнительно
проверить по уникальному UID. Пока `DEVICE_ID_ASSIGNED=0`, плата не передаёт sync/status/map/sensor
кадры в RS485, но её ADC и USB продолжают работать.

Готовая настройка локальной платы и чтение JSON:

```text
python HostTools/vendor_rs485_device_list.py --serial <USB_SERIAL> \
  --device-id 0 --rpi-number 12 --ip 192.168.1.12
```

Команду без `--no-scan` направляйте текущему master. После уже выполненного master-scan каталог
на любом slave можно прочитать тем же скриптом с `--no-scan`.

Ответ `RID1`, 32 байта:
- offset `0..3`: ASCII `RID1`
- `4`: version = `1`
- `5`: `device_id` строки `0..31`; наличие назначения определяется флагом, а не значением
- `6..7`: flags LE
- `8..17`: `short_id[10]`, 9 hex-символов и `NUL`; если неполно, заполнено `?`
- `18..21`: `ip4[4]` в порядке `a.b.c.d`
- `22..25`: `seen_page_mask` LE, bit0..bit20
- `26..29`: `last_ms` LE, `HAL_GetTick()` последнего обновления строки
- `30..31`: `rpi_number` LE

Флаги `RID1.flags`:
- `0x0001 SHORT_VALID`
- `0x0002 IP_VALID`
- `0x0004 COMPLETE`
- `0x0008 LOCAL`
- `0x0010 RECENT` (обновление за последние 30 секунд)
- `0x0020 SCAN_ACTIVE`
- `0x0040 SELECTED_SLAVE`
- `0x0080 MASTER`
- `0x0100 NODE_CONFLICT` (для этого постоянного ID услышаны разные UID96)
- `0x0200 RPI_NUMBER_VALID`
- `0x0400 DEVICE_ID_ASSIGNED`

RS485 wire-format внутри существующего sync+2 байта:
- Sync-пакет остается всегда прежним.
- `master_status0 bits0..4` = selector `device_id 0..31`; `bits5..7` остаются статусными флагами master.
- `master_status1 = 0xC0 | master_id` в штатном режиме, чтобы slave знали публичный id master.
- Значение `master_status1 = 0xA0 | new_id` (старый compact-assignment) новой прошивкой игнорируется.
- `master_status1 = 0x80 | page` при identity-запросе; адрес в `master_status0 bits0..4` указывает, какой slave должен ответить.
- Ответ выбранного slave: первый байт = обычный RS485 status byte slave, второй байт = `0x40 | nibble`.
- Страницы `page 0..8` = 9 hex-nibble `short_id`; `9..12` = `rpi_number`;
  `13..20` = IPv4 nibble в порядке `a.b.c.d`.
- Во время identity-запросов невыбранные slave не отвечают, но слушают ответ выбранного slave и также обновляют свою таблицу.
- Служебные слова собственных страниц master имеют отдельные типы и не занимают `device_id 0`.

### 3.3 Срочные состояния контролируемых датчиков

`optic`, `DetADC1`, `DetADC2` и будущие датчики (например radar) используют единый набор из
16 бит на каждый `device_id`. Любое изменение ставится в приоритетную очередь и отправляется
в ближайшем цикле 200 Hz; первая попытка происходит не позднее 5 ms, событие повторяется трижды.
Фоновая identity-карта всегда уступает событию датчика.

- sensor `0` = optic;
- sensor `1` = DetADC1;
- sensor `2` = DetADC2;
- sensor `3..15` зарезервированы для назначаемых контролируемых датчиков.

EP0 `0x43 GET_RS485_SENSOR`, `wValue=0..31` для конкретного устройства или `0xFF` для локального,
возвращает `SNS1` длиной 16 байт:

- `0..3`: `SNS1`;
- `4`: version `1`;
- `5`: `device_id`;
- `6..7`: flags (`VALID=1`, `LOCAL=2`, `RECENT=4`, `MASTER=8`);
- `8..9`: `sensor_bits`;
- `10`: индекс последнего изменившегося датчика;
- `11`: reserved;
- `12..15`: `last_change_ms`.

### 3.4 Диагностика пропажи потока на Raspberry

Важное различие:
- EP0 `GET_STATUS` отвечает: Raspberry видит USB device/control path.
- Bulk IN 0x83 не дает A/B кадров: это может быть проблема STM32 stream pipeline, но также может быть проблема reader/libusb/reconnect на Raspberry.

Поэтому при `usb_disconnected`, длительном отсутствии A/B кадров или перед автоматическим fallback/reconnect Raspberry должен сначала снять два снимка `STAT` через EP0 control:

```text
STAT0 = GET_STATUS через EP0, 137 байт
sleep 1.0..2.0 s
STAT1 = GET_STATUS через EP0, 137 байт
```

Минимальный лог при fault:

```text
[fault_probe] reason=<usb_disconnected/no_frames/no_pairs>
[fault_probe] stat0 len=<n> flags_rt=0x.... flags2=0x.... cur=<cur_samples> frame_bytes=<frame_bytes> sentA/B=<sent0>/<sent1> txcplt=<dbg_tx_cplt> dma=<dma_done0>/<dma_done1> wr=<frame_wr_seq> seq=<cur_stream_seq> lastTX=<last_tx_len> now=<now_ms> last_full=<last_full0_ms>/<last_full1_ms>
[fault_probe] stat1 ...
[fault_probe] delta sentA/B=<dA>/<dB> txcplt=<dTx> dma=<dDma0>/<dDma1> wr=<dWr> seq=<dSeq>
[fault_probe] verdict=<host_bulk_lost|stm32_tx_stalled|stm32_adc_stalled|stream_stopped|usb_control_lost>
```

Ключевые поля `STAT`:
- `flags_runtime & 0x0001`: `STREAMING`, STM32 получил `START_STREAM` и считает поток включенным.
- `flags_runtime & 0x0008`: `STREAM_ACTIVE`, были успешные отправки рабочих A/B кадров после старта.
- `flags_runtime & 0x0040`: `HOST_RX_ALIVE`, STM32 недавно получил `HOST_RX_ACK` от host reader. Этот бит полезен только если Raspberry реально отправляет `0x36 HOST_RX_ACK` при чтении A/B кадров.
- `sent0`, `sent1`: сколько A/B кадров STM32 поставил на USB IN.
- `dbg_tx_cplt`: сколько передач завершилось callback-ом `TxCplt` на STM32.
- `dma_done0`, `dma_done1`, `frame_wr_seq`: жив ли ADC/DMA pipeline.
- `cur_stream_seq`: счетчик stream-пар.
- `cur_samples`, `frame_bytes`, `last_tx_len`: текущий размер кадра. `last_tx_len=1232` обычно соответствует 600 samples (`32 + 600*2`), `last_tx_len=432` соответствует ROI/AVG 200 samples (`32 + 200*2`).
- `flags2 bit0`: USB IN busy.
- `flags2 bit2`: `pending_B`, STM32 ждет/держит B после A.
- `flags2 bit7`: `pending_status`, STAT отложен.
- `flags2 bit10`: первая полноценная A/B пара уже завершалась.
- `flags2 bit11/12`: A/B ready в активном slot.
- `flags2 bit13/14`: A/B сейчас в состоянии sending.
- `now_ms`, `last_full0_ms`, `last_full1_ms`: возраст последних DMA full кадров считается на стороне Raspberry как `now_ms - last_full*_ms` с учетом uint32 wrap.

Вердикты по двум снимкам:
- EP0 `GET_STATUS` не отвечает: `usb_control_lost`. Raspberry потерял устройство/control path, либо устройство переэнумерируется/перезагружается. Логировать USB topology и делать обычный reconnect.
- EP0 отвечает, но `STREAMING=0`: `stream_stopped`. Остановлена только передача по USB; ADC/DMA продолжает непрерывно заполнять rolling FIFO. Проверить, кто отправил `STOP_STREAM`; без команды `START_STREAM` USB-передача сама не возобновится.
- EP0 отвечает, `STREAMING=1`, `sent0/sent1/dbg_tx_cplt` растут, но Raspberry не получает A/B: `host_bulk_lost`. STM32 продолжает отдавать поток, проблема на стороне Raspberry/libusb/Bulk reader. В этом случае нельзя делать fallback в 600 samples; нужно закрыть/reopen Bulk reader и восстановить последний выбранный режим.
- EP0 отвечает, `dma_done0/dma_done1/frame_wr_seq` или `cur_stream_seq` растут, но `sent0/sent1/dbg_tx_cplt` не растут: `stm32_tx_stalled`. ADC жив, но USB TX pipeline STM32 застрял. Логировать `flags2`, `lastTX`, `pending_B`, `USB IN busy`; дальше можно делать STM32 recovery.
- EP0 отвечает, но `dma_done0/dma_done1/frame_wr_seq` не растут: `stm32_adc_stalled`. Проблема ниже USB TX: ADC/DMA pipeline или синхронизация.
- EP0 отвечает, `sent0` растет, а `sent1` нет или наоборот: канал/парирование. Проверить host-side `CHMODE` в журнале команд, а в `STAT` смотреть `pending_B` и `flags2 bit11..14`.

Политика восстановления на Raspberry:
- Перед любым fallback/reconnect сначала записать два EP0 `STAT` и verdict.
- Reconnect/init sequence не должен безусловно отправлять `SET_STREAM_MODE=0`. После восстановления надо вернуть последний желаемый режим пользователя: например AVG_ROI/200 samples, если он был активен до fault.
- Переход в 600 samples считать критическим аварийным fallback, а не штатным recovery. До него надо сделать все возможное, чтобы продолжить работу в последнем рабочем режиме, например AVG_ROI/200 samples.
- Временный переход в 600 samples допустим только если восстановление последнего режима без reset не удалось или `STAT` показывает реальный сбой STM32 pipeline. Такой переход должен быть явно залогирован как host-side аварийное решение.
- Если verdict=`host_bulk_lost`, Raspberry не должен считать STM32 виновным: STM32 продолжал увеличивать счетчики отправки.
- Если verdict=`stm32_tx_stalled` или `stm32_adc_stalled`, Raspberry должен сохранить полный порядок команд перед fault и два `STAT`, чтобы это можно было чинить в прошивке.

### 3.4 Что делать при `USB error 5` и переходе на 600 samples

Типичный проблемный лог Raspberry:

```text
[disconnect] USB error 5 => stop loop
[RUN] fault ... reason=usb_disconnected
[fallback] stall/usb fault => temporary mode 3 ...
[open] exact 0xcafe:0x4001 ...
[ep0] status len=64
[tx] cmd=0x1A n=2
[tx] cmd=0x20 n=1
[mode-detect] BUF=600 ...
```

Такой лог сам по себе не доказывает сбой STM32. Если перед этим в `STAT` или UART-логе STM32 видно, что `STREAMING=1`, `sent0/sent1`, `dbg_tx_cplt`, `dma_done0/dma_done1` и `frame_wr_seq` продолжают расти, значит STM32 продолжал формировать и отдавать поток. В этом случае `USB error 5` надо трактовать как потерю Bulk IN reader/libusb path на Raspberry до тех пор, пока два EP0 `STAT` не покажут обратное.

Отдельно: переход на 600 samples обычно появляется не сам по себе, а после команды хоста `SET_STREAM_MODE=0` (`0x1A`, payload `00`) в reconnect/init/fallback sequence. Если до fault был пользовательский режим 200 samples, хост обязан хранить его как `desired_stream_mode` и восстанавливать именно его, а не сбрасываться в default/latest mode. Для текущей системы 600 samples считаем критическим режимом: он допустим только после неудачных попыток продолжить штатную работу на 200 samples или когда диагностика уже показала сбой STM32.

Обязательный порядок при fault:

1. Зафиксировать причину: `usb_disconnected`, `no_frames`, `no_pairs`, `timeout` или другое имя fault.
2. Не отправлять сразу `SET_STREAM_MODE=0`, `START_STREAM`, soft reset или полный reconnect.
3. Снять два `STAT` через EP0 control по 137 байт с паузой 1..2 с.
4. Записать в лог расшифрованные поля и delta между двумя снимками.
5. Выставить verdict по правилам из раздела 3.1.1.
6. Только после этого выполнять recovery.

Recovery по verdict:

- `host_bulk_lost`: закрыть и открыть заново Bulk reader/interface, затем восстановить последний желаемый режим. Не использовать fallback 600 samples и не делать reset, пока есть шанс продолжить поток на 200 samples.
- `stream_stopped`: найти в журнале, кто отправил `STOP_STREAM`; затем заново применить сохраненную конфигурацию и `START_STREAM`.
- `usb_control_lost`: устройство не отвечает даже по EP0. Логировать `dmesg`/`journalctl -k`, USB topology, факт переэнумерации, после reconnect восстановить последний желаемый режим.
- `stm32_tx_stalled` или `stm32_adc_stalled`: сохранить журнал команд, два `STAT`, kernel log и только потом делать soft reset/reconnect. Эти случаи уже относятся к диагностике прошивки STM32.

Рекомендуемая лестница восстановления без перехода на 600 samples:

1. Если EP0 отвечает и counters STM32 растут, закрыть только Bulk reader, освободить/заново claim IF#2, поставить `alt=1` и продолжить чтение. Команды режима не менять.
2. Если после reopen нет A/B кадров, повторно отправить только сохраненную рабочую конфигурацию: `SET_WINDOWS`, `SET_STREAM_MODE=<desired>`, `SET_ASYNC_MODE`, `SET_CHMODE`, `SET_PROFILE`, `START_STREAM`. Для режима 200 samples не отправлять `SET_STREAM_MODE=0`.
3. Если поток восстановился на 200 samples, записать recovery как успешный и продолжить работу без reset.
4. Если две попытки восстановления 200 samples не дали кадров, снять еще один EP0 `STAT`, сохранить kernel log и только тогда переходить к soft reset/reconnect.
5. После reset/reconnect снова сначала восстановить последний желаемый 200-sample режим. 600 samples использовать только как аварийную диагностику, если 200-sample режим не поднимается.

В текущей прошивке STM32 контроль ADC/DMA считает признаком исправной работы только публикацию
новой полной пары ADC1+ADC2. Возврат `HAL_OK` при запуске DMA сам по себе успехом не считается.
Если после запуска или во время работы публикация пары прекращается, watchdog атомарно останавливает
общий trigger TIM15, останавливает оба DMA, очищает DMA/NVIC pending-флаги, снова ставит на приём
сначала ADC2, затем ADC1 и только после этого возобновляет TIM15. Уже опубликованные кадры и их
sequence при этом не сбрасываются; отбрасывается только незавершённая пара. Счётчик
`restart_success` увеличивается лишь после фактического появления следующей опубликованной пары.
Применение роли `MASTER`/`SLAVE` этот recovery не запускает и не останавливает ADC, DMA или USB-поток.

ADC/DMA не имеет состояния ожидания Raspberry и не использует USB backpressure. FIFO на STM32
является rolling/latest-wins очередью:

- ADC продолжает публиковать новые пары при закрытом Bulk IN, USB busy, отсутствии чтения на RPI,
  `STOP_STREAM`, смене USB altsetting и soft/deep reset только USB-пайплайна.
- При заполнении FIFO самая старая непрочитанная пара удаляется, счётчик `adc_drop` увеличивается,
  а её слот немедленно используется для новой пары. Переполнение никогда не устанавливает паузу ADC.
- `START_STREAM` и `STOP_STREAM` управляют только USB-передачей. Они не перезапускают ADC/DMA,
  не сбрасывают ADC publication sequence и не меняют назначенную роль.
- Назначение `MASTER`/`SLAVE`, потеря/возврат RS-485 SYNC и коррекция фазы не вызывают
  stop/rearm DMA и не сбрасывают текущий цикл ADC.
- Указатели, передаваемые USB/DC consumer, ведут на стабильный CPU snapshot, поэтому DMA может
  перезаписать исходный rolling-слот, не повреждая уже начатую обработку.
- В используемом `DMA_NORMAL` следующая пара DMA подготавливается на каждой границе кадра по
  проверенной последовательности v99: `ADC1 stop/start`, затем `ADC2 stop/start`. Общий `TIM15`
  и его `TRGO` в штатном frame rearm не останавливаются, не сбрасываются и не блокируются.
  Остановка или блокировка `TIM15/TRGO` на каждой границе кадра запрещена: пропущенные такты
  делали период зависимым от времени выполнения HAL и срывали фазовую регулировку RS485.
  Полный watchdog recovery выполняется только после уже обнаруженной реальной остановки
  публикации; явная смена аппаратного профиля также требует отдельного атомарного rearm.

Через COM команда `OPTIC` позволяет проверить это без USB reader:

```text
adc_wr=<published pairs> adc_rd=<consumer seq> adc_drop=<discarded old pairs>
adc_pause=0 adc_pub_age_ms=<age> adc_restart=<attempts>/<confirmed successes>
adc_restart_reason=<0..4> adc_restart_ndtr=<A>/<B>
adc_restart_age_ms=<publication age at recovery> adc_tc_rearm_fail=<count>
```

В двух последовательных ответах `adc_wr` обязан расти, `adc_pause` обязан оставаться `0`, а
`adc_pub_age_ms` — оставаться близким к периоду пары. Рост `adc_drop` при продолжающем расти
`adc_wr` означает штатное отбрасывание старых данных из-за медленного/отсутствующего RPI, а не
остановку ADC. `adc_restart_reason`: `0` — recovery не было, `1` — нет пары после arm,
`2` — остановилась публикация пары, `3` — не двигался DMA B, `4` — не было DMA Full A.
`adc_tc_rearm_fail` увеличивается, если штатный запуск хотя бы одного ADC на следующий кадр
завершился ошибкой; дальнейшее восстановление выполняет watchdog по отсутствию новой полной пары.
`adc_restart_suppressed` считает случаи, когда предварительная проверка watchdog подозревала
остановку, но атомарная повторная проверка непосредственно перед HAL stop/start уже видела
живую публикацию или движущийся DMA и поэтому не прервала ADC/TX.

В сборке от 23.07.2026 исправлена гонка тиков watchdog: ISR мог записать
`adc_last_publish_ms = now_ms + 1` после того, как main уже прочитал `now_ms`. Старая
беззнаковая разность считала такую метку возрастом `4294967295` мс и выполняла ложный
`pair publication stalled` recovery при полностью живом ADC. Признаки ошибки в старой
прошивке: `adc_restart_reason=2`, `adc_restart_age_ms=4294967295`, NDTR около начала
нового 600-семплового кадра. Теперь малая будущая метка имеет возраст 0, а перед любым
аппаратным recovery состояние повторно проверяется с запрещёнными IRQ. Поэтому watchdog
не может разорвать работающий ADC-кадр и физический TX200.

В firmware 1.2.13 фазовая позиция DMA и полярность маркера исправляются раздельно.
Обычный переход через границу буфера (`sample 0/599`) больше не останавливает точную
доводку и не считается сам по себе причиной менять полярность. После четырёх
последовательных подтверждений `ANTI_PHASE` slave меняет полярность физического маркера
только когда DMA-фаза уже находится не далее четырёх UART-битов от цели. Поэтому истинная
противофаза исправляется один раз, а проход границы из-за частотного ухода завершается
обычным регулятором без ложного переключения. ADC, DMA, USB sequence и счётчики буферов
при этом не останавливаются и не сбрасываются. Удалён прежний плавный набор полного
полупериода через повторяющиеся `TIM15 ARR+64`: на M10/S11 он мог циклически входить в
фазу и снова выходить из неё. Поэтому в штатном состоянии `SYNCSTATE` показывает
`phase_slew=0 phase_slew_active=0 phase_slew_left=0`.

После подтверждения `IN_PHASE` точная доводка использует окно фиксации 3/4 UART-бита.
Тихая доводка ограничена шагом `ARR±8` и может выполняться раз в два ADC-буфера.
Проверка M10/S11 показала, что `ARR±4` не успевал за их постоянным частотным уходом и
примерно через 25 секунд допускал потерю целого буфера, а прежний шаг `ARR±16`
перескакивал через точку синхронизации. Обычная частота остаётся 600 семплов ×
400 буферов/с, то есть физический TX — 200 Гц.

### Резерв стека и системный WS2812

В сборке от 23.07.2026 устранено переполнение D1 RAM, которое повреждало последние `.bss`
переменные — первыми повреждались состояние и счётчики системного WS2812. Проявления:
светодиод отправлял 1–2 кадра и замирал, а `OPTIC` показывал невозможные значения
`ws_busy > 1` или скачки `ws_frames/ws_recoveries`.

- минимальный резерв stack в linker script увеличен с 1 до 8 КиБ;
- CPU-only ADC consumer snapshots и boot-log перенесены в DTCM;
- DMA-буферы в DTCM не переносятся;
- невозможное значение WS2812 busy немедленно сбрасывается защитой.

В исправной работе COM `OPTIC` показывает `ws_busy=0/1`, монотонно растущий `ws_frames`
примерно с частотой ADC-буферов и обычно `ws_recoveries=0`. При `tx200=1` системный светодиод
продолжает менять яркость; `optic_active` меняет только его цвет и не отключает TX-анимацию.

Сборка от 23.07.2026 дополнительно привязывает каждый WS2812 DMA-кадр к началу полупериода
TX/ADC. Сначала переключается фазовый маркер и запускается RS485 sync, сразу после этого
запускается заранее подготовленный LED-кадр. Произвольный вызов LED API только подготавливает
кадр и не может запустить SPI посреди полупериода. При SPI123=100 МГц и делителе SPI3 `/32`
кадр занимает `ws_wire_us=694` мкс; допустимое окно ограничено 800 мкс, то есть находится
внутри первой трети полупериода 2,5 мс. Рабочие 200 семплов 600-семплового буфера находятся
в области `280…479`; свободны области `0…279` и `480…599`. Программное LED-окно заканчивается
примерно до семпла 192, поэтому до начала рабочей области остаётся запас не менее 88 семплов.
После окончания LED DMA SPI больше не тактируется и PB2 удерживается в нуле. Если безопасный
срок запуска пропущен, кадр
откладывается до следующей границы и растёт `ws_late_skip`; передача в поздней части
полупериода не начинается. `ws_start_delay_max_us` показывает максимальную измеренную задержку
от фазовой границы до завершения запуска DMA.

Flash-журнал параметров DC и назначений ролей расположен в зарезервированной области, начиная с
`0x080E0000`. Если чтение этой области вызывает ECC BusFault, HardFault handler сохраняет причину и
автоматически перезагружает STM32. На следующем старте стирается только повреждённый журнал, после
чего загрузка продолжается без ручного второго reset. Сохранённые в повреждённом журнале скорость DC,
назначение роли и постоянный `node_id` могут быть потеряны: локальный RPI должен повторно передать
скорость, роль и ID. Поток ADC/USB после восстановления запускается штатно.

Минимум, который должен быть в host-логе для каждого fault:

```text
[fault] role=<master/slave> reason=<...> selected_mode=<GUI mode> desired_stream_mode=<0/1/2> expected_samples=<200/600/912/...>
[fault_probe] stat0 ...
[fault_probe] stat1 ...
[fault_probe] delta ...
[fault_probe] verdict=<...>
[recovery] action=<bulk_reopen|full_reconnect|soft_reset|skip> restore_stream_mode=<...> restore_samples=<...>
[tx] ts=<...> cmd=0x.. payload=<hex> comment=<name>
```

Для диагностики особенно важен полный журнал команд вокруг fault: `SET_INTERFACE alt=0/1`, `STOP_STREAM`, `SET_STREAM_MODE`, `SET_ASYNC_MODE`, `SET_CHMODE`, `SET_PROFILE`, `SET_WINDOWS`, `START_STREAM`, `SOFT_RESET`. Если после fault в логе есть `SET_STREAM_MODE=0`, а потом `BUF=600`, это host-side fallback/reinit decision, а не доказательство самостоятельного перехода STM32.

Минимальный PyUSB EP0-запрос:

```python
import usb.core

VID = 0xCAFE
PID = 0x4001
CMD_GET_STATUS = 0x30
STAT_LEN = 137

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    raise RuntimeError("BMI30 USB device not found")

data = bytes(dev.ctrl_transfer(0xC0, CMD_GET_STATUS, 0, 0, STAT_LEN, timeout=500))
if len(data) < 64 or data[:4] != b"STAT":
    raise RuntimeError(f"bad STAT len={len(data)}")
```

### 3.5 Host-side устойчивость потока без повторов

Цель хоста: вести поток как live/UDP-like stream. Хост не просит повторить старые кадры, не догоняет backlog и не подменяет текущие данные устаревшими. В штатном режиме gaps быть не должно; если сбой уже произошел, потерянные кадры только считаются и логируются, но не восстанавливаются повторной передачей.

Случай, который нужно отличать от reset STM32:
- EP0 `GET_STATUS` отвечает.
- `flags_runtime` показывает `STREAMING=1`.
- `sent0/sent1`, `dbg_tx_cplt`, `dma_done0/dma_done1`, `frame_wr_seq` растут между двумя STAT.
- UART COM платы продолжает печатать `USBSTAT`, нового `BOOT` нет.
- Хост при этом "видит перезагрузку" GUI/reader или перестает получать A/B.

Это классифицируется как `host_bulk_lost`: STM32 продолжает поток, а на Raspberry/PC потерян Bulk IN reader или локальное состояние чтения. В таком случае нельзя делать `SET_STREAM_MODE=0`, fallback на 600 samples, `STOP_STREAM`, soft reset или полный reset платы как первую реакцию.

Обязательные свойства reader loop:
- На EP `0x83` должен быть ровно один владелец. Не запускать второй reader, пока старый bulk transfer не отменен и интерфейс не освобожден.
- Bulk IN читать в отдельном легком цикле или callback-е. Не выполнять GUI, запись на диск, DC-расчеты, графики и длинные Python-операции в том же критическом участке, где выставляется следующий read.
- Следующий bulk read выставлять сразу после приема текущего кадра. Статусы читать через EP0 control, а не через тот же Bulk IN путь, если поток под нагрузкой.
- Размер IN buffer держать не меньше максимального ожидаемого кадра. Для текущих режимов: `432` байта для ROI/AVG 200 samples и `1232` байта для 600 samples; практично использовать запас `2048`.
- `TIMEOUT` одиночного bulk read не считать fault. Fault начинается только после отсутствия полной A/B пары дольше заданного окна, например 2..3 секунды, или после явной USB ошибки `NO_DEVICE`/`IO`/`PIPE`.
- `HOST_RX_ACK` (`0x36`) отправлять только после реально принятого валидного кадра или пары, не для synthetic/replayed данных. ACK не должен блокировать выставление следующего IN read.

Правила обработки кадров:
- Использовать sequence/channel из заголовка кадра как единственный источник порядка.
- Если пришел тот же `seq+channel`, что уже был принят, считать это duplicate и выбросить. В обработку и GUI duplicate не отдавать.
- Если `seq` перескочил вперед, увеличить счетчик loss/gap и продолжить с новым `seq`. Не запрашивать повтор и не откатывать UI к старому кадру.
- Если A пришел, а B той же пары не пришел за короткий TTL, например 2 периода пары, выбросить неполную пару и ждать новую. Не держать очередь старых неполных пар.
- Между reader и обработчиком держать маленькую fixed ring/latest-wins очередь. При переполнении выбрасывать старые кадры, а не блокировать reader.
- В GUI показывать последний валидный кадр как stale, если новых данных нет, но не перезапускать весь поток из-за одного пропуска.

Сохраненная конфигурация, которую хост обязан помнить:
- `desired_stream_mode`: 0/1/2, причем для штатного ROI/AVG 200 samples обычно это `2`.
- `avg_n`, `SET_WINDOWS`/ROI start+len, `SET_FULL_MODE`, `SET_PROFILE`, `SET_CHMODE`, `SET_ASYNC_MODE`, `SET_FRAME_SAMPLES`/`SET_TRUNC_SAMPLES`, если они применялись.
- Ожидаемый размер кадра (`expected_samples`, `frame_bytes`).
- Выбранное устройство: serial/topology/роль master/slave. Для двух плат состояние и recovery ведутся независимо.

Reconnect/init sequence не должен сбрасывать эту конфигурацию к default. После любой потери reader хост восстанавливает последний желаемый режим пользователя. `SET_STREAM_MODE=0` и переход на 600 samples разрешены только как явно залогированный аварийный режим после неудачного восстановления штатного 200-sample режима или когда два EP0 STAT доказали сбой STM32 pipeline.

Лестница реакции при отсутствии A/B кадров:
1. Зафиксировать timestamp, тип ошибки libusb/PyUSB, последний принятый `seq`, выбранный режим и expected frame size.
2. Снять два `GET_STATUS` через EP0 control по 137 байт с паузой 1..2 секунды.
3. Если EP0 отвечает и counters STM32 растут, выполнить только `bulk_reader_reopen`: отменить pending read, закрыть/release/claim IF#2, поставить `alt=1`, очистить host-side parser/ring, продолжить чтение. Команды режима не менять.
4. Если после `bulk_reader_reopen` A/B не пошли, повторно применить сохраненную рабочую конфигурацию и `START_STREAM`, но не отправлять `SET_STREAM_MODE=0`.
5. Если EP0 не отвечает, ждать переэнумерации устройства с backoff, открыть то же устройство/роль и восстановить сохраненную конфигурацию.
6. Если EP0 отвечает, но `sent0/sent1/dbg_tx_cplt` не растут при живом DMA, сохранить STAT/kernel log/журнал команд и только тогда делать soft recovery STM32.
7. Hard reset или fallback 600 samples - последняя ступень, не чаще заданного лимита, например один раз в минуту на устройство.

Минимальный лог host-side recovery:

```text
[reader] event=<timeout|usb_error|no_pair> ts=<iso> dev=<serial/path/role> err=<libusb_code> last_seq=<n> expected_bytes=<n>
[fault_probe] stat0 flags_rt=0x.... flags2=0x.... frame_bytes=<n> sentA/B=<a>/<b> txcplt=<n> dma=<d0>/<d1> wr=<n> seq=<n>
[fault_probe] stat1 ...
[fault_probe] delta sentA/B=<da>/<db> txcplt=<dt> dma=<dd0>/<dd1> wr=<dw> seq=<ds>
[fault_probe] verdict=<host_bulk_lost|usb_control_lost|stm32_tx_stalled|stm32_adc_stalled|stream_stopped>
[recovery] action=<bulk_reader_reopen|restore_config_start|wait_reenum|soft_reset|fallback_600> result=<ok|fail> restore_stream_mode=<n> restore_bytes=<n>
```

### 3.6 ADC frame

ADC-кадр идет по Vendor Bulk IN `0x83`, little-endian. Заголовок всегда 32 байта:

```text
offset  size  field
0       2     magic = 0xA55A, wire bytes 5A A5
2       1     version = 1
3       1     flags: bit0=ADC0/A, bit1=ADC1/B, bit2=crc16 present, bit7=TEST
4       4     seq
8       4     timestamp_ms
12      2     total_samples
14      2     zone_count
16      4     zone1_offset
20      4     zone1_length
24      4     reserved: source dma_seq/generation
28      2     reserved2: buffer_index + source snapshot diagnostics
30      2     crc16
32      N     payload: total_samples * uint16_t samples
```

Если `flags & 0x04 != 0`, `crc16` считается как CRC16-CCITT-FALSE (`poly=0x1021`, `init=0xFFFF`) по байтам `header[0..29]`, затем по payload `total_samples * 2`. Поле `crc16` в расчет не входит. Bit2 в `flags` уже должен быть установлен на момент расчета CRC.

`reserved2` сохраняет совместимость по нижним битам:

```text
bits 0..2   buffer_index / phase index
bits 3..7   source_slot = dma_seq % FIFO_FRAMES
bit  8      snapshot_used
bit  9      generation_changed_during_snapshot
bit  10     fifo_guard_near_reuse
bit  11     source_consumed_after_snapshot
bits 12..15 TxCplt counter low nibble at frame build
```

Если host видит физически невозможный in-frame jump, надо логировать вместе `reserved`, `reserved2`, `seq`, `flags`, `total_samples` и CRC status. В нормальном кадре ожидается `snapshot_used=1`; `generation_changed_during_snapshot` и `fifo_guard_near_reuse` должны оставаться 0.

### 3.7 DCCF (GET_DC_CONFIG, 0x3A)

- Сигнатура: DCCF
- Размер: 40 байт
- Поля:
  - version = 2
  - enabled (`settle_ms != 0`)
  - flags (`bit0=dirty`)
  - settle_ms (`1` fastest, `1000` = 1 second)
  - set_at_ms
  - adapt_updates
  - reserved[5]

### 3.8 LCDS (GET_LCD_STATUS, 0x38)

- Сигнатура: LCDS
- Размер: 24 байта
- Содержит состояние отображения sync-индикатора LCD:
  - raw/display mode
  - display value/char/color
  - rgb565
  - flags
  - sync_age_ms
  - text
- `flags bit5` (`0x0020`) = большой role overlay разрешен host-командой.
- `flags bit6` (`0x0040`) = большой role overlay сейчас активен на LCD.
- `flags bit7` (`0x0080`) = локальная роль сохранена во Flash.
- `flags bit8` (`0x0100`) = конфликт локального `node_id`.
- `flags bit9` (`0x0200`) = локальный master недавно обнаружил другой master.
- `flags bit10` (`0x0400`) = сохранённая роль `MASTER`; bit7=1 без bit10 означает `SLAVE`.
- Если overlay активен, `display_rgb565`/`display_color_id` показывают текущий цвет большого текста.
- `display_char`/`display_value`/`text` всегда описывают сохранённую роль и постоянный ID:
  `Mxx`/`Sxx`, а при ID=0 — `M--`/`S--`.

### 3.9 EVT1: поток изменений вместо частого опроса

Новый рекомендуемый путь для динамических параметров - service-события по тому же Vendor Bulk IN `0x83`.
Хост читает обычный поток и, кроме ADC-кадров и `STAT`, распознает маленькие пакеты с сигнатурой `EVT1`.

Назначение:
- `GET_STATUS` остается snapshot/recovery API: снять полное состояние после reconnect, fault-probe или при старте диагностики.
- `EVT1` используется для изменений состояния без постоянного request-response polling.
- Если изменений нет, firmware все равно отправляет редкий heartbeat: `MODE_STATE` примерно раз в 30 секунд, чтобы host видел живой service path.
- Устройство не копит большой backlog событий. Если host не успевает читать, старые события могут быть заменены более свежими.
- ADC кадры имеют приоритет. События отправляются только когда Vendor IN свободен и не разрывают A/B пару.
- Фоновый RS485 status/identity scan сам по себе не форсирует полный набор `EVT1` и
  `STAT`. Немедленно сравниваются и отправляются только `OPTIC_STATE`/`SENSOR_MAP`
  при реальном изменении controlled sensor. Роль и топология проверяются общим
  state-poll не чаще одного раза в 250 мс. Это исключает конкуренцию служебных
  пакетов с ADC-кадрами.

Формат `EVT1`, little-endian:

```text
offset  size  field
0       4     sig = "EVT1"
4       1     version = 1
5       1     event_type
6       2     payload_len
8       4     event_seq
12      4     device_tick_ms
16      N     payload
```

Сейчас firmware отправляет такие `event_type`:

```text
0x00 FW_INFO      версия прошивки, build time и STM32 UID96
0x01 TEMP_C       температура, совместимый короткий формат
0x02 MCU_ADC      внутренние ADC3 каналы: temperature/VREFINT/VBAT
0x10 OPTIC_STATE  оптический датчик, TX, power/hold
0x11 SYNC_STATE   роль, наличие sync, active node mask/count
0x12 MODE_STATE   режимы стрима и host-selected параметры
0x13 ERROR_STATE  ошибки и USB recovery counters
0x14 SENSOR_MAP   срочная полная карта controlled sensors для ID 0..31
```

`0x00 FW_INFO`, payload 47 байт. Firmware отправляет его в baseline после `START_STREAM`.

```text
offset  size  field
0       1     payload_version = 1
1       1     fw_major
2       1     fw_minor
3       1     fw_patch
4       1     fw_build, reserved сейчас 0
5       4     stm32_uid_w0, little-endian
9       4     stm32_uid_w1, little-endian
13      4     stm32_uid_w2, little-endian
17      10    build_date ASCII, "YYYY-MM-DD", zero-padded if shorter
27      8     build_time ASCII, "HH:MM:SS", zero-padded if shorter
35      12    git_hash ASCII prefix, zero-padded if shorter
```

STM32 UID96 для host key/identity удобно печатать как `uid_w2 uid_w1 uid_w0` в hex или как 12 raw bytes в порядке payload offset 5..16.

`0x01 TEMP_C`, payload 2 байта:

```text
offset  size  field
0       2     int16_t temp_c
```

`0x02 MCU_ADC`, payload 16 байт:

```text
offset  size  field
0       1     payload_version = 1
1       1     flags: bit0=temp valid, bit1=vdda valid, bit2=vbat valid
2       2     int16_t temp_c
4       2     uint16_t vdda_mv   ; VDDA по VREFINT calibration
6       2     uint16_t vbat_mv   ; VBAT pin, 0 если не валидно/не подключено
8       2     uint16_t raw_temp
10      2     uint16_t raw_vrefint
12      2     uint16_t raw_vbat  ; ADC видит VBAT/4
14      2     reserved
```

`0x10 OPTIC_STATE`, payload 8 байт:

```text
0       1     payload_version = 1
1       1     flags: bit0=optic_active, bit1=tx_enabled
2       1     optic_power 0..255
3       1     led_pattern
4       2     optic_hold_ds, 0.1 s units
6       2     reserved
```

`0x11 SYNC_STATE`, payload 16 байт:

```text
0       1     payload_version = 1
1       1     raw_mode: 0=master, 1=slave, 2=off
2       1     display_mode
3       1     display_char: 'M', 'S', 'O'
4       1     local device_id 0..31; назначение определяется payload[13] bit2
5       1     active_status_count = popcount(sync_seen_mask), включая локальный узел и master
6       1     total_devices = active_status_count (минимум 1 для локального USB-устройства)
7       1     flags: bit0=sync_signal_alive, bit1=sync_ok_visual, bit2=color_locked,
              bit3=host_forced, bit4=sync_ok_public, bit5=role_persisted,
              bit6=local_node_id_conflict, bit7=multiple_master
8       4     sync_seen_mask, bit N=device_id N, N=0..31; маска может быть разреженной
12      1     display_value
13      1     bits5..7 local status; bits0..1 saved_role: 0=none, 1=MASTER, 2=SLAVE;
              bit2=device_id_assigned
14      2     sync_age_ds, 0.1 s units, 0xFFFF если неизвестно
```

`0x14 SENSOR_MAP`, payload 40 байт:

```text
0       1     payload_version = 1
1       1     local device_id 0..31
2       1     flags: bit0=local device_id assigned, bit1=local role MASTER
3       1     node_count = popcount(seen_mask)
4       4     seen_mask LE, bit N=device_id N
8       32    status_bytes[device_id], bits5..7=optic/DetADC1/DetADC2
```

Firmware ставит это событие в очередь сразу при любом локальном или принятом по RS485
изменении controlled sensor. Одновременно ставится совместимый 136-байтовый `STAT`
для существующего RPI, который ещё не разбирает `SENSOR_MAP`.

Для реакции страницы хоста на оптический датчик рекомендуется принимать оба события:

- локальный быстрый индикатор устройства: `OPTIC_STATE (0x10)`, `payload[1] bit0`;
- состояние любого устройства группы по постоянному ID: `SENSOR_MAP (0x14)`,
  `status_bytes[id] bit5`.

Начиная с firmware 1.2.15 активное состояние оптического датчика передаётся RPI только при
достоверной синхронизации. В режимах `MASTER`/`SLAVE` должны одновременно выполняться
`sync_signal_alive=1`, `sync_ok_visual=1`, отсутствовать конфликт локального ID и второй master.
При нарушении любого условия firmware отправляет очищенное состояние, чтобы уже начатая
реакция/звуковое сопровождение на RPI прекратились:

- в `OPTIC_STATE` очищается `payload[1] bit0`;
- в `SYNC_STATE` очищается локальный optic bit5 в `payload[13]`;
- в `SENSOR_MAP` очищается optic bit5 у всех `status_bytes[id]`;
- в совместимом `STAT` очищаются локальный/master/group optic flags, legacy optic bit и optic
  bit5 в таблице sync status.

Перед непосредственной передачей `EVT1` firmware повторно проверяет синхронизацию и очищает
optic bit также в уже поставленном в очередь пакете. Поэтому событие, сформированное до срыва
sync, не может позднее выйти из очереди как новая активная сработка. После восстановления
синхронизации актуальное состояние публикуется заново.

Биты `DetADC1/DetADC2` этим фильтром не изменяются. Физический вход, локальная индикация и
RS485-диагностика оптики продолжают работать. В намеренном автономном режиме `OFF` оптика
передаётся без sync-фильтра.

При диагностике через COM команда `OPTIC` должна показать одновременное изменение `pd0`,
удерживаемого `rx`, `local_optic_bit`/`optic_mask`, рост `sensor_evt_tx` на три повтора и
рост `usb_evt_enq/usb_evt_cplt` без `usb_evt_drop`. Если эти признаки есть, а страница не
изменилась, событие уже сформировано и передано STM32 — проверять demux `EVT1` в reader RPI,
а не перезапускать ADC или менять роль.

`0x12 MODE_STATE`, payload 16 байт:

```text
0       1     payload_version = 1
1       1     flags: bit0=streaming, bit1=diag, bit2=pending_init, bit3=stream_active, bit4=full_mode, bit5=async_mode, bit6=tx_enabled, bit7=host_rx_alive
2       1     stream_mode: 0=LATEST, 1=LOSSLESS_ROI, 2=AVG_ROI
3       1     ch_mode: 0=A-only, 1=B-only, 2=both
4       1     host_profile
5       1     avg_n
6       2     cur_samples_per_frame
8       2     frame_samples_req
10      2     trunc_samples
12      1     sync_mode_public
13      1     sync_mode_host_forced
14      2     reserved
```

`0x13 ERROR_STATE`, payload 16 байт:

```text
0       1     payload_version = 1
1       1     flags: bit0=last_error_nonzero; остальные биты reserved = 0
2       2     last_error, saturated to 0xFFFF
4       4     error_counter
8       4     tx_force_idle_count
12      4     tx_drop_recovery_count
```

Нормальные короткие состояния `USB IN busy/inflight` не являются ошибкой и больше не
меняют `ERROR_STATE` или `error_counter`: это предотвращает ложные события во время
исправного потока. `error_counter` увеличивается только при некорректном кадре или
реальном отказе USB, отличном от штатного `USBD_BUSY`.

Для `stream_mode=2 AVG_ROI` поле `avg_n` поддерживает рабочие значения `8/16/24/32/40/48/56/64` в диапазоне `8..64`. Ожидаемая частота усредненных ROI-пар примерно `buf_rate / avg_n`: при `buf_rate=400` это около `50 fps` для `avg_n=8`, `25 fps` для `avg_n=16`, `16.6 fps` для `avg_n=24`.

Мониторинг идет низкоприоритетно из main-loop, не из критического ADC/USB пути. Внутренние ADC3 каналы (`TEMP`, `VREFINT`, `VBAT`) читаются примерно раз в 2 секунды. Быстрые state-снапшоты проверяются чаще, но события отправляются только при изменении. Если вообще нет изменений, `MODE_STATE` используется как heartbeat примерно раз в 30 секунд. Если основной поток занят, событие может прийти позже. После `START_STREAM` firmware форсирует baseline по всем типам событий.

Host-side правила:
- Reader на `0x83` должен различать три типа входящих данных:
  - ADC frame: `0x5A 0xA5 ...`
  - `STAT`
  - `EVT1`
- `EVT1` не является A/B кадром и не участвует в seq-проверке ADC.
- По `EVT1` хост обновляет локальный cache состояния и не обязан опрашивать эти поля через request-response.
- Если host не видит ни одного `EVT1` дольше 2 периодов heartbeat, но ADC кадры продолжают идти, это не повод сбрасывать stream; достаточно отметить service-event lag и ждать следующего безопасного IN-окна.
- Если после reconnect нужен полный baseline, сначала снять `GET_STATUS`, затем продолжить чтение `EVT1`; после нового `START_STREAM` baseline также придет событиями.

## 4. Актуальные команды sync, LCD, оптики, TX, LED и DetADC

### 4.1 Управление ролью sync по USB

Роль и номер являются двумя независимыми локальными настройками каждой платы:

- `role`: `MASTER` или `SLAVE`;
- `device_id`: постоянный номер STM32 `0..31`, одинаковый при обеих ролях.

Обе настройки задаёт RPI этой платы и обе сохраняются во Flash. Автоматического выбора master,
автоматической выдачи slave-номеров, compact-assignment и перенумерации после смены master нет.
Чужие role/enum кадры по RS485 не могут изменить локальные роль или ID. Если несколько плат
назначены master либо имеют одинаковый ID, пользователь/RPI исправляет конфигурацию явно.

`0x1D SET_SYNC_MODE` задаёт локальную сохраняемую роль:

- payload `[0x00] + struct.pack("<Q", unix_ms)` = `MASTER`;
- payload `[0x01] + struct.pack("<Q", unix_ms)` = `SLAVE`;
- payload `[0x02]` = временно `OFF`;
- payload `[0x03]` или `[0xFF]` = снять временный forced-режим и вернуться к сохранённой роли.

`unix_ms` — текущее UTC Unix time хоста в миллисекундах, little-endian. Практический Python-вариант:

```python
import struct, time

unix_ms = time.time_ns() // 1_000_000
role = 0  # 0=MASTER, 1=SLAVE
payload = bytes([role]) + struct.pack("<Q", unix_ms)
send_cmd(dev, 0x1D, payload)
```

`unix_ms` остаётся в 9-байтовом формате команды для совместимости. Команда принимается с первого
пакета; сохранённое время при необходимости локально продвигается на 1 ms. Оно не участвует в
голосовании и не распространяет роль на другие платы.

`0x3D SET_RS485_ID` задаёт постоянный `device_id` этой STM32:

- payload `[0..31]` = сохранить этот ID во Flash.

Команда разрешена и для `MASTER`, и для `SLAVE`. ID не меняется при переключении роли, reset,
power-cycle, появлении нового master или изменении состава RS485-группы. Номера не обязаны быть
непрерывными: группа `M07/S00/S19` корректна. Состояние «не назначен» хранится отдельным флагом,
поэтому `M00/S00` — реальные назначенные адреса, а `M--/S--` — неназначенная плата.

Назначение выполняется только через локальный USB. У каждой STM32 свой RPI, поэтому общий RS485
для комиссии не используется и неоднозначности между несколькими неназначенными платами нет.
Неназначенная плата молчит в RS485, но ADC/DMA и USB продолжают работать.

Роль и ID немедленно ставятся в очередь записи Flash из main-loop; USB interrupt не пишет Flash.
Применение роли/ID не останавливает и не перезапускает ADC/DMA, не сбрасывает ADC sequence и не
меняет host-request `TX200`.

Свободные много-байтовые объявления `node_id + UID96` в RS485 не передаются: при непрерывном
200 Hz SYNC они пересекались бы с двухбайтовыми status-окнами. Система управления должна читать
сохранённые `role + node_id` у каждого RPI и сравнивать их между платами. Так обнаруживаются
несколько `MASTER` и повторяющиеся ID; исправление выполняется только явными командами пользователя.
Поле `node_id_conflict` в текущей fixed-role сборке зарезервировано и равно нулю.

Применение или повторное подтверждение роли не очищает заданную RPI частоту, не перезапускает
ADC/DMA, не сбрасывает ADC frame/parity sequence и не меняет host-request `TX200`.

Специального «выбранного sensor-slave» больше нет. `optic`, `DetADC1` и `DetADC2` каждого RPI
читаются по его постоянному адресу `sync_status_bytes[device_id]`; поэтому источник можно явно
задать как `S02`, `M07` и т. п. Любое изменение sensor-битов передаётся приоритетным двухбайтовым
событием в ближайшем 200 Hz цикле (первая попытка не позднее 5 ms) и повторяется трижды.
Фоновая передача карты ID/IP использует второй байт обычного status-обмена и всегда уступает
sensor-событию.

Для EP0 MASTER и SLAVE обязательно использовать data stage из 9 байт. Короткие варианты
`wValue=0/1` возвращают STALL, потому что не содержат времени назначения.

Контроль состояния:
- `GET_LCD_STATUS` (`LCDS`) всегда возвращает `raw_mode` и `node_id`, даже без живого sync.
  `flags bit7=role_persisted`, `bit8=node_id_conflict` (зарезервирован, сейчас 0),
  `bit9=multiple_master`,
  `bit10=persisted_master` (bit7=1 без bit10 означает сохранённый `SLAVE`),
  `bit11=device_id_assigned`.
- `EVT1 SYNC_STATE`: `raw_mode` — сохранённая/текущая роль, `local node_id` — постоянный ID;
  flags `bit5=role_persisted`, `bit6=node_id_conflict` (зарезервирован), `bit7=multiple_master`;
  `payload[13] bits0..1` явно сообщают сохранённую роль: `1=MASTER`, `2=SLAVE`;
  `payload[13] bit2=device_id_assigned`.
- `GET_STATUS`: flags `0x1000/0x2000/0x4000/0x8000` дают те же признаки; таблица `sync_status_bytes`
  адресуется постоянными ID и включает master. Для сохранённой роли:
  `0x1000+0x8000=MASTER`, `0x1000` без `0x8000=SLAVE`.

COM-команды `VER` и `VERSION` печатают одну строку
`VERSION fw=<semver> git=<hash> build_date=<YYYY-MM-DD> build_time=<HH:MM:SS> pairs=<N>`.
Ответ передаётся напрямую в UART и доступен даже при выключенном подробном отладочном `printf`.

COM-команда `SYNCSTATE` печатает одну компактную строку для проверки всей группы. Для рабочего
slave ожидаются:

- `phase_rel=1`, `phase_score=-8`: подтверждена прямая фаза;
- `bufs_per_sync=1`: между соседними пакетами master опубликован ровно один локальный ADC-буфер;
- `sample=<около целевой точки>/600`: пакет master приходит возле одной и той же позиции кадра;
- `phase_pulse`: текущий краткий импульс коррекции `TIM15 ARR`; возле цели обычно малый;
- `period`: измеренный TIM5-период соседних sync-пакетов;
- `sync_reject`: число отброшенных слишком ранних ложных sync-байтов.

`SYNCSTATE` является диагностикой STM32 через COM и не требует GUI/RPI. Поля `sync_alive=1`,
`sync_ok=1`, `multiple_master=0`, `id_conflicts=0` и одинаковая `sync_seen_mask` на всех платах
подтверждают одну общую группу. `phase_locked` — строгий мгновенный deadband PLL; он может
переключаться на отдельных пакетах из-за джиттера, поэтому для устойчивости дополнительно смотреть
`phase_rel`, `sample`, `bufs_per_sync` и отсутствие роста `phase_restart`.

Начиная с firmware 1.2.6 исправлено восстановление частоты после загрузки: применение RPI-профиля
атомарно фиксирует номинальный `TIM15 ARR` и отменяет временный фазовый импульс со старой
базой. Для штатных 600 семплов × 400 буферов/с `SYNCSTATE` должен показывать `tim15`
с `ARR` около `1144`, а не загрузочное значение `4295`.

### 4.2 Большая индикация роли на LCD

`0x41 SET_LCD_ROLE_OVERLAY` управляет крупной периодической индикацией роли.

Payload:
- `[0x00]` = запретить overlay и вернуть обычный LCD status screen
- `[0x01]` = разрешить overlay с текущими/дефолтными временами
- `[enable, period_s, duration_s]` = задать интервал между показами и длительность показа; `period_s` и `duration_s` ограничиваются диапазоном `3..5`, дефолт `4/4`

EP0 варианты:
- OUT без data stage: `bRequest=0x41`, младший байт `wValue` = `enable`
- OUT с data stage: payload `[enable]` или `[enable, period_s, duration_s]`

Поведение на LCD:
- Рисуется full-screen текст самым крупным доступным размером: `Mxx`, `Sxx` или `O  `.
- Для `MASTER` и `SLAVE` `xx` = один и тот же локальный постоянный `node_id`.
- При назначенном `device_id=0` показывается `M00`/`S00`; `M--`/`S--` означает отсутствие назначения.
- Цвет текста выбирается случайно из ярких цветов `RED/GREEN/YELLOW/BLUE/CYAN/WHITE`.
- Отрисовка выполняется только из основного цикла в низкоприоритетном LCD-обновлении; USB callback только меняет флаги.
- При выходе из overlay обычный экран очищается и перерисовывается заново.

### 4.3 Обязательная проверка host-кода для 200 Hz TX

`TX200` — логическое разрешение передачи 200 Hz. Для host-кода важны только команда,
сохранённый request и подтверждённое состояние передачи:

```text
SET_TX_ENABLE=1 -> tx_req=1, tx200=1 (передача включена)
SET_TX_ENABLE=0 -> tx_req=0, tx200=0 (передача выключена)
```

В текущей firmware действует одно правило:
`physical_tx = tx_request AND streaming`. Последняя явная команда `0x33 01/00`
сохраняет желаемое состояние, но физические выходы разрешены только при активном
USB stream. Кратковременный пропуск `HOST_RX_ACK`/SOF не меняет TX и не создаёт
пропущенный импульс. `STOP_STREAM` выключает физический TX, не стирая `tx_req`;
следующий `START_STREAM` автоматически восстанавливает его. Оптический датчик
это состояние не переключает.

Наблюдаемая комбинация `streaming=1, tx_req=0, tx200=0` означает, что устройство не
получило `SET_TX_ENABLE=1` либо какой-то путь host-кода позднее отправил
`SET_TX_ENABLE=0`/reset. Это не случайное выключение по датчику или heartbeat.

#### Точный формат команды

Для Vendor bulk OUT endpoint `0x03` raw-пакет должен иметь ровно два байта:

```text
33 01    # включить 200 Hz TX
33 00    # выключить 200 Hz TX
```

Если helper `send_cmd(cmd, payload)` сам добавляет opcode, вызывать его так:

```python
CMD_SET_TX_ENABLE = 0x33
stream.send_cmd(CMD_SET_TX_ENABLE, b"\x01")  # enable
stream.send_cmd(CMD_SET_TX_ENABLE, b"\x00")  # disable
```

Нельзя передавать helper-у payload `b"\x33\x01"`, если он уже добавляет opcode: на wire
получится ошибочный пакет `33 33 01`. При прямой записи в endpoint, наоборот, opcode обязателен:

```python
dev.write(0x03, b"\x33\x01", timeout=1000)
```

Альтернативный EP0-вариант без data stage: vendor OUT `bRequest=0x33`, младший байт
`wValue=0/1`, `wLength=0`. Не смешивать bulk framing и EP0 framing в одной функции.

#### Обязательный порядок startup/reconnect

`SET_TX_ENABLE` является persistent host-request. `STOP_STREAM` выключает
физические синхронные выходы, но не стирает request. Рекомендуемый порядок
оставляет TX-команду в конце, чтобы сразу сделать однозначный readback:

```text
HOST_RX_CLEAR
STOP_STREAM
SET_* configuration
START_STREAM
SET_TX_ENABLE desired_tx_enabled    # всегда последняя команда управления TX
GET_STATUS                          # обязательный readback
```

Проверить и исправить все ветки host-кода:

- initial connect;
- reconnect после USB exception;
- watchdog/soft-kick;
- смена profile, stream mode, frequency и channel mode;
- повторная инициализация после timeout;
- смена MASTER/SLAVE;
- shutdown старого reader и запуск нового reader.

После `STOP_STREAM` физический TX обязан выключиться, а `tx_req` — сохранить последнее
явно заданное состояние. После следующего `START_STREAM` TX автоматически вернётся
в состояние `tx_req`. Host может идемпотентно повторить желаемое значение, но нельзя
использовать локальный default `False` во время reconnect, если пользователь оставил TX включенным.
`stream_enabled` и `desired_tx_enabled` — разные состояния и не должны перезаписывать друг друга.

#### Проверка скрытой команды выключения

Найти по всему host-проекту все места, где встречаются `0x33`, `SET_TX_ENABLE`, `TX200`,
`STOP_STREAM`, `START_STREAM`, `HOST_RX_CLEAR`. `SET_TX_ENABLE=0` разрешено отправлять
только по явному действию пользователя. Cleanup, timeout, heartbeat, reconnect и создание
нового USB object не должны молча отправлять `0x33 00`.

Production host не должен скрывать ошибку записи конструкцией `except Exception: pass`.
Каждая команда должна логироваться как минимум с полями:

```text
monotonic_time, device_identity, command, payload, reason, result
```

Особенно важны причины `connect`, `reconnect`, `watchdog`, `mode_switch`, `user_enable`,
`user_disable`, `shutdown`. По логу должно быть видно, кто последним записал `0x33 00`.

#### Четыре одинаковых USB-устройства

Нельзя для каждой команды заново делать только `find(VID=0xCAFE, PID=0x4001)` и брать
первое найденное устройство. При четырех одинаковых VID/PID команда может попасть не в тот
прибор. Host обязан:

- однозначно привязать каждый прибор по USB serial number либо стабильным bus/address/port path;
- отправлять `SET_TX_ENABLE` через тот же открытый `device handle` и interface, с которого
  читается поток данного прибора;
- хранить отдельный `desired_tx_enabled` для каждого прибора;
- сериализовать записи в bulk OUT per-device lock;
- исключить второй GUI/service/reader, который одновременно управляет тем же прибором.

Нельзя хранить один глобальный `dev`, endpoint или TX state для всех четырех устройств.
При reconnect новый handle должен получить identity прежнего прибора до отправки команд.

#### Обязательный readback

После `SET_TX_ENABLE` host должен прочитать полный `GET_STATUS` (`0x30`) именно с того же
устройства и проверить:

```text
flags_runtime & 0x0001 != 0    # STREAMING
flags_runtime & 0x0010 != 0    # физический 200 Hz TX enabled
reserved3 & 0x0002 != 0        # legacy duplicate TX enabled
```

Для `desired_tx_enabled=True` оба TX-бита обязаны быть `1` при `STREAMING=1` и `0`
при `STREAMING=0`; при этом `tx_req` остаётся равным `1`.
Проверку делать сразу после команды, затем через `1 s`, `5 s` и после reconnect. Если бит не установился,
host должен записать в лог ошибку с identity прибора и последними командами; допустим один
явный повтор `SET_TX_ENABLE=1`, но нельзя запускать бесконечный toggle-таймер.

Через диагностический COM команда `OPTIC` должна показывать:

```text
stream=1 tx_req=1 tx200=1    # host включил TX, состояние исправно
stream=1 tx_req=0 tx200=0    # host оставил/повторно записал disable
```

Для точной проверки UART-строка также содержит `tx_cmd_count`, `tx_cmd_val` и
`tx_cmd_age_ms`. Поля `tx_change`, `tx_off`, `tx_change_val`, `tx_change_age_ms`
считают уже фактические переходы физического TX: при стабильной работе после включения
`tx_change` не растёт, `tx_off` остаётся неизменным. При каждом принятом `0x33` должен увеличиваться `tx_cmd_count`, а
`tx_cmd_val` должен совпасть с payload. Host не должен делать выводы о внутренней
реализации STM32: подтверждением являются `tx_req`, `tx200` и TX-биты `GET_STATUS`.
Поля `adc_wr`, `adc_rd`, `adc_drop`, `adc_pause`, `adc_pub_age_ms`, `adc_restart`,
`adc_restart_reason`, `adc_restart_ndtr`, `adc_restart_age_ms`, `adc_tc_rearm_fail`,
`adc_gap_max_ms` и `adc_gap10`
служат независимой COM-диагностикой непрерывности ADC. Поля `usb_frame_age_ms`,
`usb_txcplt`, `usb_recovery`, `usb_force_idle` и `usb_error` отделяют отсутствие
данных ADC от остановки или восстановления USB IN.

Поля `usb_evt_enq`, `usb_evt_ok`, `usb_evt_cplt`, `usb_evt_drop`, `usb_evt_q` и
`usb_evt_last` контролируют служебную очередь. При неизменных датчиках
`usb_evt_enq` не должен расти сотнями в секунду, а `usb_evt_drop` должен оставаться
неизменным. Рост `usb_evt_drop` одновременно с провалами FPS означает перегрузку
service-событиями.

Требование к host watchdog: краткий провал FPS сначала диагностировать, продолжая
держать Bulk IN read. Не отправлять `STOP_STREAM/START_STREAM` как первую реакцию:
`STOP_STREAM` по контракту немедленно выключает физический TX, поэтому такой recovery
сам создаёт наблюдаемый провал магнитного поля. Перезапуск stream допустим только после
подтверждённой длительной остановки USB и должен логироваться с причиной и временем.

Минимальный acceptance test для каждого из четырех приборов:

1. Выполнить полный startup-порядок и отправить `SET_TX_ENABLE=1`.
2. Проверить TX readback сразу, через 5 секунд и после серии `HOST_RX_ACK`.
3. Отправить `HOST_RX_CLEAR`: TX должен остаться включенным.
4. Выполнить `STOP_STREAM`: `tx_req=1`, но `tx200=0`.
5. Выполнить `START_STREAM`: `tx_req=1`, `tx200=1` без новой обязательной команды `0x33`.
6. Отправить `SET_TX_ENABLE=0`: TX-бит должен стать `0`, а STREAMING остаться `1`.
7. Снова отправить `SET_TX_ENABLE=1`: TX-бит должен стать `1` и оставаться таким.
8. Выполнить reconnect и повторить проверку identity, порядка команд и readback.

Host-код считается исправленным только после прохождения этого теста на всех четырех
устройствах одновременно.

- 0x34 SET_OPTIC_POWER: u8 0..255
- 0x39 SET_OPTIC_HOLD:
  - новый формат: u16 deciseconds
  - совместимость: legacy u8 seconds
- 0x3B SET_LED_PATTERN: u8 pattern_id
- 0x35 LED_EVENT: u8 event + u16 duration_ms
- 200 Hz marker/TX не gated по оптическим датчикам. Полный обязательный host-контракт приведен в разделе 4.3.
- Оптический 38 kHz carrier не gated по приемнику: `SET_OPTIC_POWER=255` задает максимум, чтобы фотоприемник мог сработать.
- Для bench-контроля без USB Vendor доступны COM-команды UART: `VER`, `VERSION`, `TX200 1`, `TX200 0`, `OPTP 255`, `OPTH 0..600`, `OPTIC`. `VER`/`VERSION` возвращают raw-строку версии независимо от отладочного `printf`. `TX200 1/0` меняют сохранённый host-request; физический TX дополнительно требует `stream=1`. `OPTIC` печатает `tx200` (подтверждённое состояние), `tx_req` (запрос host/manual), `power/hold_ds/rx/activity_hold/pd0/any/master_rx/local_status`. `pd0` показывает сырой вход, а `rx`, `activity_hold` и `local_status bit5` (`0x20`) — единый удержанный `optic_active`. Поля `ws_busy/ws_frames/ws_recoveries` показывают активную SPI DMA-передачу WS2812, число отправленных кадров и число автоматических восстановлений после тайм-аута 100 ms; `usb_frame_age_ms/usb_txcplt/usb_recovery/usb_force_idle/usb_error` диагностируют Vendor IN.
- Внешняя WS2812-лента имеет optic-gate: `SET_LED_PATTERN` и `LED_EVENT` сохраняют желаемое состояние, но реально светиться она может только пока активен хотя бы один оптический датчик в группе.
- В gate входят локальный `optic_active`, свежий master `master_status0 bit5` на slave и свежие `sync_status_bytes[*] bit5` от slave-узлов на master/узлах, которые их слышат. Если все эти признаки равны 0 или устарели, внешняя лента рендерится как `OFF`; onboard/system address WS2812 продолжает показывать роль.
- 0x3C SET_DET_ADC: u8 bits
  - bit0 = `DetADC1`
  - bit1 = `DetADC2`
  - остальные биты игнорируются, значение по умолчанию `0`
- 0x3D SET_RS485_ID: u8 persistent `device_id 0..31`; только локальный USB, разрешено для master/slave
- 0x3E SET_RS485_IP: `u8 ip[4]`, network order `a.b.c.d`
  - legacy-команда меняет только IP локального RPI.
- 0x42 SET_RPI_INFO: `u16 rpi_number LE + u8 ip[4]`
  - `rpi_number` не является `device_id`; строка карты имеет вид
    `device_id -> UID STM32 + rpi_number + IP RPI`.
- 0x3F REQUEST_RS485_IDENT: без payload
  - Master немедленно перезапускает проход identity. На slave команда читает уже реплицируемую
    карту и не создаёт отдельного RS485-пакета. Master также делает автоматические проходы.
- 0x40 GET_RS485_IDENT: чтение `RID1`
  - EP0 IN: `ctrl_transfer(0xC0, 0x40, selector, 0, 32)`.
  - `selector=0..31` = конкретный `device_id`, `selector=0xFF` = локальная плата.
  - `RID1.flags & 0x0080` = master, `0x0200` = номер RPI валиден,
    `0x0400` = device ID назначен.
  - Все слушающие платы постоянно составляют одинаковую карту; готовый JSON для web:
    `python HostTools/vendor_rs485_device_list.py --serial <USB_SERIAL> [--no-scan]`.

Проверка через STAT
- flags_runtime bit 0x0010: TX enabled
- flags_runtime bit 0x0020: optic active
- flags_runtime bit 0x1000: роль сохранена во Flash
- flags_runtime bit 0x2000: конфликт локального ID
- flags_runtime bit 0x4000: обнаружен второй master
- flags_runtime bit 0x8000: сохранённая роль master (иначе при 0x1000 сохранён slave)
- sync_status byte:
  - bits 0..4 = `node_id` в публичных полях `sync_local_status`/`sync_status_bytes`
  - bit 5 = optic active
  - bit 6 = `DetADC1`
  - bit 7 = `DetADC2`
- На сырой RS485-шине `master_status0 bits0..4` остаются адресом/`selector` запроса к slave.
  `master_status1` несёт `0xC0 | master_id`; legacy `0xA0 | new_id` игнорируется.
- Firmware на slave добавляет свежий статус master в `sync_seen_mask`/`sync_status_bytes`
  по его постоянному ID.
- Для хоста состояние локального узла, master optic flag и таблицы доступно двумя путями:
  по запросу `GET_STATUS` (`0x30`, читать 137 байт) и как событие `STAT` при изменении
  локального или принятого по RS485 status byte.
- Для firmware-индикации slave отдельно использует свежий `master_status0 bit5`: если master сообщил `optic_active=1`, onboard/system address WS2812 slave меняется с белого на магента/фиолетовый только при отсутствии локального срабатывания; локальный `optic_active=1` на самом slave имеет приоритет и показывает желтый. Master в этой ситуации меняется с синего на зеленый.
- Для внешних световых эффектов действует общий optic-gate: если нет локального/принятого по RS485 `optic_active`, внешняя WS2812-лента остается выключенной даже при включенных host-командах. 200 Hz marker/TX в этот gate не входит и при активном stream следует только `SET_TX_ENABLE`.

## 5. Единственный процесс DC-компенсации

STM32 имеет одну таблицу коэффициентов `[channel][parity][sample]` и один постоянно работающий процесс. RPI управляет только одним значением — `settle_ms`.

- `0x1F SET_DC_SPEED`: установить скорость обучения.
- `0x2B SAVE_DC_TO_FLASH`: сохранить текущую таблицу коэффициентов.
- `0x3A GET_DC_CONFIG`: прочитать текущую скорость и статус.
- Старые `SET_DC_ADAPT (0x1B)`, `CALIB_DC_FAST (0x1E)` и много-параметрический `SET_DC_CONFIG` больше не поддерживаются.
- Режимов `WORK/DETECT/BOOT_FAST`, acquisition/detection-таймеров, стадий pre/post и amplitude auto-freeze в прошивке нет.
- DC применяется один раз: для `AVG_ROI` — к каждому raw ROI до усреднения; для `LOSSLESS_ROI` — к копии ROI перед отправкой.
- Обучение выполняется на каждом новом raw-кадре независимо от USB stream mode.
- Обучение продолжает выполняться при остановленном USB stream; USB START/STOP не является управлением DC.
- Полученное от RPI значение действует постоянно до следующего `SET_DC_SPEED`.
- После reset скорость равна `0`; RPI обязан явно установить её при startup/reconnect. Скрытых firmware/host defaults нет.
- `settle_ms=0` останавливает только обучение. Уже накопленная таблица продолжает применяться.
- `settle_ms=1` задаёт максимальную скорость; `1000` означает 1 секунду.
- Допустимый диапазон: `0..86400000` мс. Чем больше ненулевое значение, тем медленнее адаптация.

Критическое требование для RPI: не отправлять при подключении старое значение
`100000` как неявный `WORK`/DC-default. В новом протоколе это не служебное
значение, а буквально `100000 ms`, то есть компенсация примерно за 100 секунд.
При выборе пользователем `1 s` RPI обязан отправить ровно:

```text
1F E8 03 00 00
```

Изменение значения в UI должно немедленно формировать новый `SET_DC_SPEED`.
При reconnect RPI повторяет последнее явно выбранное пользователем значение,
но не подставляет старые `work/acquisition/detection` defaults.

Начиная с firmware v1.26 шаг адаптивен для каждой точки ROI:
`corr[i] = abs(error[i]) * 8 * dt_ms / settle_ms`, с ограничением до границы deadband. Большое отклонение получает
большой шаг, возле virtual zero шаг плавно уменьшается. Дробная Q16-часть хранится отдельно для
каждого семпла, поэтому медленные значения RPI также выполняются корректно. Коэффициент 8 выбран
так, чтобы при `settle_ms=1000` максимальная ошибка 32768 LSB входила в deadband примерно за 0.95 с.
При `settle_ms=1000` и обновлении parity-банка каждые 10 мс начальный шаг составляет примерно
2620 LSB для максимального отклонения и 79 LSB для отклонения 1000; ошибка 1000 входит в
deadband примерно за 0.52 с. Прошедшее время не теряется при повторном START или перезапуске ADC.

Формат Bulk OUT строго равен 5 байтам:

```text
offset  size  field
0       1     opcode = 0x1F
1       4     settle_ms, uint32 little-endian; 0=off, 1=fastest, 1000=1s
```

Пакеты другой длины и значения больше `86400000` отвергаются. Повторная отправка того же значения допустима и не сбрасывает таблицу коэффициентов.

Канонический Python-код для RPI:

```python
import struct

CMD_SET_DC_SPEED = 0x1F
stream.send_cmd(CMD_SET_DC_SPEED, struct.pack("<I", 1_000))     # полная шкала примерно за 1 секунду
stream.send_cmd(CMD_SET_DC_SPEED, struct.pack("<I", 1))         # максимально быстро (1 мс)
stream.send_cmd(CMD_SET_DC_SPEED, struct.pack("<I", 0))        # выключить обучение
```

Ответ `GET_DC_CONFIG` имеет сигнатуру `DCCF` и фиксированную длину 40 байт:

```text
offset  size  field
0       4     "DCCF"
4       1     version = 2
5       1     enabled: settle_ms != 0
6       2     flags; bit0 = коэффициенты dirty
8       4     settle_ms
12      4     set_at_ms
16      4     adapt_updates
20      20    reserved = 0
```

Host должен проверить raw readback после команды: `version=2`, `enabled` и `settle_ms` обязаны
совпадать с последним отправленным значением. Формат распаковки:

```python
sig, version, enabled, flags, settle_ms, set_at_ms, updates, *_ = \
    struct.unpack("<4sBBHIII5I", dccf)
assert sig == b"DCCF" and version == 2 and settle_ms == 1_000
```

Для диагностики без Vendor IN команда `OPTIC` на COM выводит дополнительные
поля DC:

- `dc_ms` — фактически принятое `settle_ms`; после reset равно `0`;
- `dc_cmd` — число принятых пакетов `SET_DC_SPEED`;
- `dc_rej` — число отклонённых пакетов;
- `dc_set_age_ms` — возраст последнего принятого пакета;
- `dc_calls` — число вызовов постоянного процесса адаптации;
- `dc_updates` — число вызовов, реально изменивших коэффициенты;
- `dc_adapt_age_ms`, `dc_dt_ms` — возраст и интервал последнего шага;
- `dc_maxerr`, `dc_maxcorr`, `dc_meanerr` — ошибка и применённая коррекция.

Если после reset сначала видно `dc_ms=0 dc_cmd=0`, а затем
`dc_ms=100000 dc_cmd=1`, значение поступило входным USB-пакетом и не является
default прошивки STM32. Если пользователь меняет скорость, а `dc_cmd` не
увеличивается, RPI не отправляет команду при изменении UI.

Конфигурация `work=900000ms acquisition=5000ms detection=60000ms` устарела и не существует в новом протоколе. RPI не должен отправлять скорость периодически без необходимости: новая команда нужна только при startup/reconnect или когда действительно требуется изменить скорость.

## 6. О команде 0x2B (SAVE_DC_TO_FLASH)

Команда 0x2B активна только как USB Vendor Bulk OUT команда на IF#2. Хост Raspberry не использует COM/UART для этого процесса.

Формат команды:
- Endpoint: Vendor Bulk OUT 0x03
- Payload: один байт `2B`
- Data IN response: нет
- EP0 request: не используется

Прошивка не пишет Flash прямо из USB callback. При получении `0x2B` она ставит отложенный запрос, а фактическая запись выполняется из `Vendor_Maintenance_Task()`.

Периодическая запись DC во Flash отключена (`VND_DC_SAVE_PERIOD_MS=0` и `VND_DC_SAVE_PERIOD_FIRST_MS=0`). Raspberry должен явно отправлять `0x2B`, когда нужно сохранить обученный DC.

Рекомендуемый порядок для хоста:
1. Открыть устройство VID/PID `0xCAFE/0x4001`.
2. Перевести Vendor IF#2 в `alt=1`, чтобы был доступен Bulk OUT 0x03.
3. Установить единственную скорость DC командой `SET_DC_SPEED` и при необходимости запустить stream.
4. Дождаться окончания обучения DC на стороне хоста по своей логике измерения.
5. При необходимости остановить обучение командой `SET_DC_SPEED` со значением `0`.
6. Отправить Bulk OUT payload `2B`.
7. Подождать 100..500 мс, чтобы main loop успел выполнить Flash-запись.
8. Продолжить работу или отправить требуемую ненулевую скорость `SET_DC_SPEED`.

Минимальный PyUSB пример отправки:

```python
import usb.core
import usb.util

VID = 0xCAFE
PID = 0x4001
INTF = 2
ALT = 1
EP_OUT = 0x03

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
  raise RuntimeError("BMI30 USB device not found")

dev.set_configuration()
try:
  if dev.is_kernel_driver_active(INTF):
    dev.detach_kernel_driver(INTF)
except (NotImplementedError, usb.core.USBError):
  pass

usb.util.claim_interface(dev, INTF)
dev.set_interface_altsetting(interface=INTF, alternate_setting=ALT)
dev.write(EP_OUT, bytes([0x2B]), timeout=1000)
```

Проверка состояния по USB:
- `GET_DC_CONFIG` (`0x3A`, EP0 vendor IN, 40 байт) возвращает структуру `DCCF`.
- `DCCF.flags & 0x0004` означает `DIRTY`: текущий DC отличается от последнего сохраненного состояния или уже успел снова измениться после сохранения.
- У `0x2B` сейчас нет отдельного USB ACK с результатом Flash-записи. Не нужно ждать Bulk IN ответа на эту команду.

Политика хоста:
- Не отправлять `0x2B` периодически по таймеру.
- Отправлять `0x2B` только при явном событии: завершение калибровки, команда пользователя, штатное завершение настройки, подготовка к выключению.
- Не спамить `0x2B`; Flash имеет ограниченный ресурс, а одна команда уже сохраняет текущий DC snapshot.
- Если хосту нужно строгое подтверждение `SAVE OK/SAVE FAIL`, надо расширить USB-статус отдельными полями результата сохранения. В текущем протоколе команда является одноразовым запросом без ответа.

## 7. Быстрые команды для host-скриптов

Основной reader
- python HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 2 --block-hz 200 --frame-samples 10 --frames 80 --ab-strict

Статус через control (EP0)
- python HostTools/vendor_get_status.py --ctrl --repeat 5
- Для fault-probe снимайте минимум два статуса с паузой: `python HostTools/vendor_get_status.py --ctrl --repeat 2 --interval 1.0`

Kernel/USB log на Raspberry вокруг fault
- journalctl -k --since "YYYY-MM-DD HH:MM:SS" --until "YYYY-MM-DD HH:MM:SS"
- dmesg -T

При `USB error 5`
- сначала `python HostTools/vendor_get_status.py --ctrl --repeat 2 --interval 1.0`
- затем сохранить журнал команд хоста за 10 секунд до fault и 10 секунд после fault
- затем выполнить recovery по verdict, не отправляя безусловно `SET_STREAM_MODE=0`

Короткий статус
- python HostTools/vendor_quick_status.py --secs 5

## 8. Устранение неполадок

- IN timeout во время паузы допустим, особенно вне активной пары A/B.
- Если поток не стартует, проверьте:
  1) IF#2 действительно в alt=1
  2) отправлен корректный SET_STREAM_MODE (0x1A)
  3) реально выполнен START_STREAM (0x20)
- Для lossless/avg режимов async принудительно выключается прошивкой.
- Команды 0x31/0x32 не используйте как Vendor GET_TEMP/GET_VERSION.
