# Компактная таблица команд Vendor/Control/CDC

Актуально для текущей прошивки.

## 1) Vendor Bulk OUT (IF#2, EP OUT 0x03)

| Код | Команда | Payload | Типовой ответ/эффект |
|---|---|---|---|
| 0x10 | SET_WINDOWS | u16 start0,len0,start1,len1 | Применение ROI окон |
| 0x11 | SET_BLOCK_HZ | u16 hz | Настройка частоты блоков |
| 0x13 | SET_FULL_MODE | u8 | Переключение full/diag |
| 0x14 | SET_PROFILE | u8 | Смена профиля ADC |
| 0x15 | SET_ROI_US | u32 start_sample | Смена начала ROI |
| 0x16 | SET_TRUNC_SAMPLES | u16 samples | Ограничение числа выборок |
| 0x17 | SET_FRAME_SAMPLES | u16 samples | Явный размер кадра |
| 0x18 | SET_ASYNC_MODE | u8 mode | Async/strict режим |
| 0x19 | SET_CHMODE | u8 mode | A-only/B-only/both |
| 0x1A | SET_STREAM_MODE | u8 mode, опц. u8 avg_n | Выбор режима stream |
| 0x1C | SET_BUF_RATE_FINE | u16 hz | Тонкая подстройка buf rate |
| 0x1D | SET_SYNC_MODE | master/slave: `u8 mode + u64 unix_ms LE`; OFF/AUTO: u8 mode | Постоянная локальная роль; 2=off, 3/0xFF=вернуться к сохранённой |
| 0x1F | SET_DC_SPEED | u32 settle_ms LE | Единственная постоянная скорость DC; 0=learning off |
| 0x20 | START_STREAM | нет | Запуск только USB-передачи; ADC/DMA не перезапускается |
| 0x21 | STOP_STREAM | нет | Остановка только USB-передачи; ADC/DMA продолжает работать |
| 0x22 | DEVICE_RESET | нет | Soft/hard reset (зависит от сборки) |
| 0x30 | GET_STATUS | нет | STAT через IN |
| 0x32 | TOGGLE_TIM2CH3_INV | нет | Инверсия TIM2 CH3 |
| 0x33 | SET_TX_ENABLE | u8 0/1 | Host-request для 200 Hz marker/TX; при stream=1 состояние применяется непрерывно |
| 0x34 | SET_OPTIC_POWER | u8 0..255 | Настройка оптики |
| 0x35 | LED_EVENT | u8 event, u16 ms | Временный LED event |
| 0x36 | HOST_RX_ACK | u32 total_frames | Heartbeat от хоста |
| 0x37 | HOST_RX_CLEAR | нет | Сброс только heartbeat; TX request не меняется |
| 0x39 | SET_OPTIC_HOLD | u16 ds (legacy u8 s) | Время удержания opt active |
| 0x3A | GET_DC_CONFIG | нет | DCCF через IN |
| 0x3B | SET_LED_PATTERN | u8 pattern | Базовый LED pattern |
| 0x3C | SET_DET_ADC | u8 bits bit0=DetADC1 bit1=DetADC2 | Локальные DetADC-биты RS485 status |
| 0x3D | SET_RS485_ID | u8 node_id | Постоянный локальный ID 0..31, независимый от роли |
| 0x3E | SET_RS485_IP | u8 ip[4] a.b.c.d | Локальный IPv4 для RS485 identity |
| 0x3F | REQUEST_RS485_IDENT | нет | Master запускает один scan ID/IP |
| 0x40 | GET_RS485_IDENT | опц. u8 node_id | RID1 через IN, только вне stream; во время stream используйте EP0 |
| 0x41 | SET_LCD_ROLE_OVERLAY | u8 enable, опц. u8 period_s,duration_s | Большой LCD `Mxx`/`Sxx`, 3..5 s |
| 0x42 | SET_RPI_INFO | u16 rpi_number LE + IPv4[4] | Фоновые данные локального RPI |
| 0x43 | GET_RS485_SENSOR | нет в bulk; используйте EP0 | SNS1 |

## 2) Vendor EP0 Control

### IN (device -> host)

| bRequest | Команда | Ответ |
|---|---|---|
| 0x30 | GET_STATUS | STAT |
| 0x38 | GET_LCD_STATUS | LCDS (24 bytes) |
| 0x3A | GET_DC_CONFIG | DCCF (40 bytes) |
| 0x40 | GET_RS485_IDENT | RID1 (32 bytes), wValue: 0..31=device_id, 0xFF=local |
| 0x43 | GET_RS485_SENSOR | SNS1 (16 bytes), wValue: 0..31=device_id, 0xFF=local |

### OUT (host -> device)

| bRequest | Вариант | Payload |
|---|---|---|
| 0x7E | SOFT_RESET | нет |
| 0x7F | DEEP_RESET | нет |
| 0x20 | START_STREAM | нет |
| 0x21 | STOP_STREAM | нет |
| 0x3F | REQUEST_RS485_IDENT | нет |
| 0x13,0x14,0x18,0x19,0x1D,0x33,0x34,0x3B,0x3C,0x3D,0x41 | через wValue | u8; `0x1D MASTER/SLAVE` здесь запрещены (STALL) |
| 0x17 | через wValue | u16 |
| 0x39 | через wValue | u16 hold_ds |
| 0x13,0x14,0x18,0x19,0x1D,0x33,0x34,0x39,0x3B,0x3C,0x1F,0x3D,0x3E,0x41,0x42 | data stage | как в payload команды |

## 3) Форматы данных

| Ответ | Сигнатура | Размер | Где читать |
|---|---|---|---|
| STATUS | STAT | 137 bytes | GET_STATUS (EP0 для полного v6; bulk может быть короче) |
| LCD status | LCDS | 24 bytes | GET_LCD_STATUS (EP0) |
| DC config | DCCF | 40 bytes | GET_DC_CONFIG (bulk/EP0) |
| RS485 identity | RID1 | 32 bytes | GET_RS485_IDENT (EP0) |
| RS485 sensors | SNS1 | 16 bytes | GET_RS485_SENSOR (EP0) |

RS485 optic/LED:
- `optic_active = PD0 || hold_after_last_high`: `PD0=1` немедленно включает и
  повторно запускает `optic_hold_ds`; `PD0=0` таймер не запускает и не продлевает.
- Один удержанный `optic_active` используется системным LED, USB, RS485 и картой группы.
- Master передает свой `optic_active` в `master_status0 bit5`; bits0..4 этого байта остаются selector slave-слота.
- Host на slave читает master optic напрямую из `STAT v5`: `flags_runtime & 0x0080`.
- Slave добавляет свежий master status в `sync_status_bytes[master_device_id]` и использует
  `master_status0 bit5` для onboard/system address WS2812. Master меняется с синего на
  зеленый при локальном или свежем групповом `optic_active`; slave при своем локальном
  `optic_active=1` меняется с белого на желтый,
  а при master `optic_active=1` без локального срабатывания — с белого на
  магента/фиолетовый. Optic меняет только цвет; при включенном TX независимый
  TX-breathe продолжает модулировать яркость.
- Изменение controlled sensor немедленно ставит `EVT1 type=0x14 SENSOR_MAP`
  (прямая карта ID 0..31) и совместимый 136-байтовый `STAT v5` (ID 1..31).
- TX200 — persistent host-request: `physical_tx = tx_request AND streaming`. `STOP_STREAM` гасит физические выходы, не стирая request; START восстанавливает их. Краткие пропуски SOF/`HOST_RX_ACK` TX не дёргают.
- ADC/DMA работает непрерывно и не ждёт RPI/USB. При заполнении rolling FIFO удаляется самая старая непрочитанная пара и увеличивается `adc_drop`; `START_STREAM`/`STOP_STREAM`, USB reconnect и назначение роли ADC не останавливают.
- Оптический 38 kHz carrier не gated по приемнику: `SET_OPTIC_POWER=255` задает максимум для проверки фотоприемника.
- Внешняя WS2812-лента optic-gated: `SET_LED_PATTERN`/`LED_EVENT` сохраняют желаемое состояние, но физический вывод разрешен только при локальном или свежем принятом по RS485 `optic_active`; когда все optic bits = 0/устарели, внешняя лента = OFF.
- Без USB Vendor bench-контроль выполняется COM-командами UART: `TX200 1`, `TX200 0`, `OPTP 255`, `OPTH 0..600`, `OPTIC`. `OPTIC` печатает `tx200`, `tx_req`, `tx_cmd_count`, `tx_cmd_val`, `rx`, `activity_hold`, `pd0`, `local_status`, `master_status`, `master_flags`, `ws_busy/ws_frames/ws_recoveries`, `usb_frame_age_ms/usb_txcplt/usb_recovery/usb_force_idle/usb_error` и ADC-поля `adc_wr/adc_rd/adc_drop/adc_pause/adc_pub_age_ms/adc_restart/adc_restart_reason/adc_restart_ndtr/adc_restart_age_ms/adc_tc_rearm_fail`; `pd0` — сырой вход, `rx`, `activity_hold` и bit5 (`0x20`) — единый удержанный `optic_active`.

RS485 role assignment:
- Если сохранённого назначения нет, автоматического выбора master нет: все устройства остаются slave до команды RPI.
- Host задаёт локальную роль: `00 + <Q unix_ms` = master, `01 + <Q unix_ms` = slave.
- Host отдельно задаёт постоянный `device_id` командой `0x3D`; master и slave используют общий диапазон 0..31.
- Неназначенность — отдельный флаг: ID 0 является нормальным адресом M00/S00.
- Назначение идёт только через локальный USB своего RPI. Неназначенная плата молчит в RS485,
  но ADC и USB продолжают работать.
- Номер и IPv4 локального RPI передаются отдельно командой `0x42`.
- Роль и ID сохраняются во Flash и не меняются от RS485, reset или изменения состава сети.
- Автоматической нумерации/compact-assignment и selected sensor-slave нет.
- Каждый RPI читает сохранённые роль и ID своей платы через `STAT`/`LCDS`/`EVT1`; управляющий
  уровень сравнивает эти значения между RPI и показывает дубликаты ID или несколько master.
  Исправление выполняется только явными командами пользователя.
- Свободные много-байтовые объявления ID по RS485 отключены, чтобы не повреждать непрерывные
  200 Hz SYNC/status-окна.
- Master фоново обходит identity-страницы, а все платы подслушивают ответы и составляют карту
  `device_id -> UID STM32 + номер RPI + IP`. Любой RPI читает строки `RID1` `0..31` у своей платы.
- Изменение `optic/DetADC1/DetADC2` передаётся приоритетно в ближайшем 200 Hz цикле и повторяется;
  identity-опрос всегда уступает sensor-событию.

RS485 device list:
- `REQUEST_RS485_IDENT` отправляется текущему master; master и все slave сохраняют услышанные страницы номер/IP.
- Локальная строка читается selector `0xFF`, строки устройств — selector `0..31`.
  Flags `0x0080=master`, `0x0200=RPI number valid`, `0x0400=device ID assigned`.
- Host primary key — полный STM32 UID96; `device_id` является изменяемым атрибутом.
- Повторный UID96 с новым ID обновляет одну существующую запись и сразу удаляет старую привязку;
  backend/frontend не должны одновременно показывать два активных устройства с одним UID.
- Presence определяется только битом ID в `sync_seen_mask`. `RID1`, `last_seen` и offline TTL —
  кэш/история, а не основание удерживать карточку в активном списке.

DC `SET_DC_SPEED`:
- Полный Bulk пакет: `0x1F + u32 settle_ms LE`, строго 5 байт.
- `0` выключает обучение, текущая коррекция продолжает применяться.
- Диапазон `0..86400000`; значение действует до следующей команды.
- Режимов, таймеров, pre/post стадий и host-side DC-алгоритма нет.
- После reset RPI явно отправляет скорость; скрытых defaults нет.

## 4) CDC диагностические команды (не Vendor bulk)

| Код | Команда | Ответ |
|---|---|---|
| 0x31 | CMD_GET_TEMP | [0x80,0x31,temp_lo,temp_hi] |
| 0x32 | CMD_GET_VERSION | [0x80,0x32,major,minor,patch,build] |

Примечание
- 0x31/0x32 в Vendor модуле не используются как GET_TEMP/GET_VERSION.
