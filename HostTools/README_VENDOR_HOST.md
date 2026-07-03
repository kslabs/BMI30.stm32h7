# Vendor USB host: актуальная справка (Windows / PowerShell)

Документ синхронизирован с текущей прошивкой.

Важно
- В Vendor протоколе на IF#2 используется несколько путей управления: Bulk OUT, EP0 control (vendor requests), а также часть диагностических команд в CDC протоколе.
- Команды 0x31 и 0x32 в текущем Vendor коде не являются GET_TEMP/GET_VERSION.
  - 0x31 зарезервирована в Vendor модуле как GET_STATUS_IMM (служебно).
  - 0x32 в Vendor модуле используется как TOGGLE_TIM2CH3_INV.
  - GET_TEMP и GET_VERSION сейчас реализованы в CDC протоколе.

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
- 0x1B SET_DC_ADAPT
- 0x1C SET_BUF_RATE_FINE
- 0x1D SET_SYNC_MODE
- 0x1E CALIB_DC_FAST
- 0x1F SET_DC_CONFIG
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

### 2.2 EP0 Control (vendor requests)

Vendor IN (чтение):
- 0x30 GET_STATUS
- 0x38 GET_LCD_STATUS
- 0x3A GET_DC_CONFIG

Vendor OUT без data stage:
- 0x7E SOFT_RESET
- 0x7F DEEP_RESET
- 0x20 START_STREAM
- 0x21 STOP_STREAM

Vendor OUT с параметром в wValue (без data stage):
- 0x13, 0x14, 0x18, 0x19, 0x33, 0x34, 0x3B, 0x3C (u8 в младшем байте wValue)
- 0x17 (u16 в wValue)
- 0x39 (u16 hold_ds в wValue)

Vendor OUT с data stage:
- 0x13, 0x14, 0x18, 0x19, 0x33, 0x34, 0x39, 0x3B, 0x3C, 0x1F

### 2.3 CDC протокол (отдельный диагностический канал)

В CDC обработчике присутствуют:
- 0x31 CMD_GET_TEMP
- 0x32 CMD_GET_VERSION

Это не команды Vendor bulk протокола IF#2.

## 3. Форматы получаемых данных

### 3.1 STAT (GET_STATUS, 0x30)

- Сигнатура: STAT
- Актуальный размер структуры: 136 байт
- Версия в структуре: v1 с расширениями v2/v3/v4/v5

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
  - sync_status_bytes[31]

Практика
- Для полного набора полей запрашивайте 136 байт.
- Старые хосты, читающие 64 байта, получают только базовую часть.
- `GET_STATUS` через EP0 control является диагностическим каналом. Он может отвечать даже тогда, когда Bulk IN поток 0x83 не доходит до reader на Raspberry.
- `GET_STATUS` через Bulk OUT/IN использует тот же Bulk IN путь, что и поток. Для диагностики зависания потока используйте EP0 control, а Bulk-статус считайте только вспомогательным.
- Состояние оптического датчика хост читает через `GET_STATUS` (`0x30`):
  - `flags_runtime & 0x0020` = локальный оптический датчик активен.
  - `reserved3 bit0` = то же состояние в legacy packed-поле.
  - `sync_local_status bit5` = локальный RS485 status bit оптического датчика.
  - `sync_local_status bit6` = локальный `DetADC1`, `bit7` = локальный `DetADC2`.
  - `sync_status_bytes[node_id-1] bit5` = состояние оптического датчика удаленной антенны.
  - `sync_status_bytes[node_id-1] bit6/bit7` = `DetADC1/DetADC2` удаленной антенны.
- При изменении состояния оптического датчика устройство должно отправить/поставить в очередь `STAT`, чтобы хост мог получать событие без ожидания следующего опроса. Хост также может опрашивать `GET_STATUS` в любой момент.

### 3.1.1 Диагностика пропажи потока на Raspberry

Важное различие:
- EP0 `GET_STATUS` отвечает: Raspberry видит USB device/control path.
- Bulk IN 0x83 не дает A/B кадров: это может быть проблема STM32 stream pipeline, но также может быть проблема reader/libusb/reconnect на Raspberry.

Поэтому при `usb_disconnected`, длительном отсутствии A/B кадров или перед автоматическим fallback/reconnect Raspberry должен сначала снять два снимка `STAT` через EP0 control:

```text
STAT0 = GET_STATUS через EP0, 136 байт
sleep 1.0..2.0 s
STAT1 = GET_STATUS через EP0, 136 байт
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
- EP0 отвечает, но `STREAMING=0`: `stream_stopped`. Поток на STM32 остановлен. Проверить, кто отправил `STOP_STREAM`; без команды `START_STREAM` поток сам не возобновится.
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

### 3.1.2 Что делать при `USB error 5` и переходе на 600 samples

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
3. Снять два `STAT` через EP0 control по 136 байт с паузой 1..2 с.
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
STAT_LEN = 136

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    raise RuntimeError("BMI30 USB device not found")

data = bytes(dev.ctrl_transfer(0xC0, CMD_GET_STATUS, 0, 0, STAT_LEN, timeout=500))
if len(data) < 64 or data[:4] != b"STAT":
    raise RuntimeError(f"bad STAT len={len(data)}")
```

### 3.1.3 Host-side устойчивость потока без повторов

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
2. Снять два `GET_STATUS` через EP0 control по 136 байт с паузой 1..2 секунды.
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

### 3.2 ADC frame

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

### 3.3 DCCF (GET_DC_CONFIG, 0x3A)

- Сигнатура: DCCF
- Размер: 40 байт
- Поля:
  - version
  - mode
  - flags
  - work_settle_ms
  - detect_settle_ms
  - fast_settle_ms
  - fast_duration_ms: legacy wire-name; сейчас это последний `adapt_settle_ms`, не таймер
  - active_settle_ms
  - mode_enter_ms
  - fast_until_ms: legacy field, всегда 0 в модели постоянной адаптации
  - adapt_updates

### 3.4 LCDS (GET_LCD_STATUS, 0x38)

- Сигнатура: LCDS
- Размер: 24 байта
- Содержит состояние отображения sync-индикатора LCD:
  - raw/display mode
  - display value/char/color
  - rgb565
  - flags
  - sync_age_ms
  - text

### 3.5 EVT1: поток изменений вместо частого опроса

Новый рекомендуемый путь для динамических параметров - service-события по тому же Vendor Bulk IN `0x83`.
Хост читает обычный поток и, кроме ADC-кадров и `STAT`, распознает маленькие пакеты с сигнатурой `EVT1`.

Назначение:
- `GET_STATUS` остается snapshot/recovery API: снять полное состояние после reconnect, fault-probe или при старте диагностики.
- `EVT1` используется для изменений состояния без постоянного request-response polling.
- Если изменений нет, firmware все равно отправляет редкий heartbeat: `MODE_STATE` примерно раз в 30 секунд, чтобы host видел живой service path.
- Устройство не копит большой backlog событий. Если host не успевает читать, старые события могут быть заменены более свежими.
- ADC кадры имеют приоритет. События отправляются только когда Vendor IN свободен и не разрывают A/B пару.

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
4       1     local node_id, 0 если не назначен
5       1     active_status_count из RS485 status table
6       1     total_devices estimate; master = active_status_count + 1
7       1     flags: bit0=sync_signal_alive, bit1=sync_ok_visual, bit2=color_locked, bit3=host_forced, bit4=sync_ok_public
8       4     sync_seen_mask, bit0=node1 ... bit30=node31
12      1     display_value
13      1     local_status_flags: только bits 5..7 local status byte
14      2     sync_age_ds, 0.1 s units, 0xFFFF если неизвестно
```

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
1       1     flags: bit0=last_error_nonzero, bit1=usb_in_busy_or_inflight
2       2     last_error, saturated to 0xFFFF
4       4     error_counter
8       4     tx_force_idle_count
12      4     tx_drop_recovery_count
```

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

## 4. Актуальные команды оптики, TX, LED и DetADC

- 0x33 SET_TX_ENABLE: 0/1
- 0x34 SET_OPTIC_POWER: u8 0..255
- 0x39 SET_OPTIC_HOLD:
  - новый формат: u16 deciseconds
  - совместимость: legacy u8 seconds
- 0x3B SET_LED_PATTERN: u8 pattern_id
- 0x35 LED_EVENT: u8 event + u16 duration_ms
- 0x3C SET_DET_ADC: u8 bits
  - bit0 = `DetADC1`
  - bit1 = `DetADC2`
  - остальные биты игнорируются, значение по умолчанию `0`
- 0x3D SET_RS485_ID: u8 node_id
  - `0` = сбросить временный локальный id и ждать auto-нумерацию
  - `1..31` = принудительно задать временный RS485 slave id
  - команда предназначена для диагностики/ручного восстановления; штатно master сам назначает компактные id

Проверка через STAT
- flags_runtime bit 0x0010: TX enabled
- flags_runtime bit 0x0020: optic active
- sync_status byte:
  - bits 0..4 = `selector` для локального master status или `node_id` для slave/remote status
  - bit 5 = optic active
  - bit 6 = `DetADC1`
  - bit 7 = `DetADC2`
- RS485 master-цикл передает `sync_byte, master_status0, master_status1`; ответ slave передает `slave_status0, slave_status1`. В `STAT v5` сейчас наружу отдаются первые байты статуса: `sync_local_status` и `sync_status_bytes[31]`.
- Для хоста состояние доступно двумя путями: по запросу `GET_STATUS` (`0x30`, читать 136 байт) и как событие `STAT` при изменении локального status byte.

## 5. Актуальные команды DC

- 0x1B SET_DC_ADAPT: freeze/active
- 0x1E CALIB_DC_FAST: legacy запуск fast-режима на N кадров
- 0x1F SET_DC_CONFIG: полная time-based конфигурация
- 0x2B SAVE_DC_TO_FLASH: одноразовая запись текущего DC во Flash по команде хоста
- 0x3A GET_DC_CONFIG: чтение DCCF

Режимы DC
- 0 = FREEZE
- 1 = WORK
- 2 = DETECT
- 3 = BOOT_FAST

Актуальный алгоритм адаптации
- DC обучается плавным slew-rate алгоритмом, отдельно для каждого канала и parity-bank, но с общим шагом для всех 200 семплов ROI.
- Прошивка применяет DC к USB payload для 200-семплового ROI в режимах `LOSSLESS_ROI` и `AVG_ROI`. Исходные DMA/FIFO буферы не изменяются.
- Прошивка меняет DC только когда адаптация разрешена, окно ROI равно 200 семплам и amplitude-gate разрешает обучение.
- Скорость обучения задаёт Raspberry через `*_settle_ms` в `SET_DC_CONFIG`: это время, за которое максимальная ошибка 32768 LSB должна дойти до виртуального нуля 32768/deadband.
- Минимальное значение `*_settle_ms` — 1 мс. При таком значении прошивка может сделать максимально быстрый LSB-slew на ближайшем новом кадре.
- На каждом кадре все семплы ROI обрабатываются за один проход: модуль шага общий и небольшой, а знак выбирается для каждого семпла по его стороне относительно 32768. Дробная часть шага копится во времени, а не распределяется по отдельным индексам ROI.
- Пример: `settle_ms=100000` при 200 Гц даёт 20000 итераций; максимальная ошибка 32768 LSB уходит примерно по 1-2 LSB на кадр, без ломаной по соседним семплам.
- Режим адаптации не имеет таймера остановки: `WORK`, `DETECT` и `BOOT_FAST` работают постоянно до следующей команды Raspberry. Меняется только скорость адаптации.
- Повторная команда `SET_DC_CONFIG` с той же или новой скоростью не сбрасывает DC-таблицу; она только меняет текущий режим/settle time. Для полной остановки обучения используйте `FREEZE` или `SET_DC_ADAPT 0`.
- `CALIB_DC_FAST` (`0x1E`) оставлен только для совместимости: payload `frames` включает временное окно `BOOT_FAST` примерно на указанное число ADC-буферов и не перезаписывает базовый режим, выбранный `SET_DC_CONFIG`.
- Начиная с DC blob version 4 прошивка игнорирует старые сохранённые DC-коэффициенты предыдущих экспериментальных версий; после обновления компенсация стартует с нулевых коэффициентов и обучается заново.
- В GUI BMI30 принята базовая быстрая адаптация 1 сек (`fast_settle_ms=1000`). Верхние параметры `W` и `D` задают множители 1..999 для рабочей адаптации и адаптации в режиме детекции: `work_settle_ms = 1000 * W`, `detect_settle_ms = 1000 * D`.

Формат `SET_DC_CONFIG` (`0x1F`) после opcode, little-endian, 20 байт:

```text
offset  size  field
0       1     version = 1
1       1     mode: 0=FREEZE, 1=WORK, 2=DETECT, 3=BOOT_FAST
2       2     flags, сейчас 0
4       4     work_settle_ms
8       4     detect_settle_ms
12      4     fast_settle_ms
16      4     adapt_settle_ms, legacy wire-name fast_duration_ms; скорость выбранного режима, не таймер
```

Рекомендуемый быстрый старт от Raspberry:
1. Отправить `SET_DC_CONFIG` с `fast_settle_ms=1000` и рабочими значениями `work_settle_ms`/`detect_settle_ms`; режим выбрать как `BOOT_FAST`, `WORK` или `DETECT` по текущему сценарию.
2. Запустить поток/измерение в нужном режиме.
3. Читать `GET_DC_CONFIG` (`DCCF`) и смотреть `mode`, `active_settle_ms`, `adapt_updates`, `flags`.
4. Когда нужна другая скорость, отправить новый `SET_DC_CONFIG` с `mode=WORK`, `DETECT` или `BOOT_FAST`; когда нужно остановить обучение, отправить `FREEZE` или legacy `SET_DC_ADAPT 0`.

Legacy `SET_DC_ADAPT` не задаёт скорость. `SET_DC_ADAPT 0` только замораживает обучение, `SET_DC_ADAPT 1` возвращает последний не-FREEZE режим. Для управления скоростью Raspberry должен использовать `SET_DC_CONFIG`.

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
3. Настроить/запустить нужный режим DC-адаптации (`SET_DC_CONFIG`, `SET_DC_ADAPT`, `START_STREAM`), если это требуется сценарием.
4. Дождаться окончания обучения DC на стороне хоста по своей логике измерения.
5. При необходимости заморозить DC-адаптацию командой `SET_DC_ADAPT` с payload `00`.
6. Отправить Bulk OUT payload `2B`.
7. Подождать 100..500 мс, чтобы main loop успел выполнить Flash-запись.
8. Продолжить работу или вернуть DC-адаптацию в active командой `SET_DC_ADAPT` с payload `01`.

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
