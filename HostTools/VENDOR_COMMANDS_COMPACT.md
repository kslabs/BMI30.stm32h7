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
| 0x1B | SET_DC_ADAPT | u8 0/1 | Freeze/active DC learning |
| 0x1C | SET_BUF_RATE_FINE | u16 hz | Тонкая подстройка buf rate |
| 0x1D | SET_SYNC_MODE | u8 mode, опц. u8 node_id для slave | 0=master, 1=slave, 2=off, 3/0xFF=auto |
| 0x1E | CALIB_DC_FAST | u8/u16 frames | Выбор BOOT_FAST до следующей DC-команды |
| 0x1F | SET_DC_CONFIG | v1/v2 payload | DC timing + pre/post AVG stage config |
| 0x20 | START_STREAM | нет | Запуск передачи |
| 0x21 | STOP_STREAM | нет | Остановка передачи |
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
| 0x3D | SET_RS485_ID | u8 node_id | Временный RS485 id |
| 0x3E | SET_RS485_IP | u8 ip[4] a.b.c.d | Локальный IPv4 для RS485 identity |
| 0x3F | REQUEST_RS485_IDENT | нет | Master запускает один scan ID/IP |
| 0x40 | GET_RS485_IDENT | опц. u8 node_id | RID1 через IN, только вне stream; во время stream используйте EP0 |
| 0x41 | SET_LCD_ROLE_OVERLAY | u8 enable, опц. u8 period_s,duration_s | Большой LCD `Mxx`/`Sxx`, 3..5 s |

## 2) Vendor EP0 Control

### IN (device -> host)

| bRequest | Команда | Ответ |
|---|---|---|
| 0x30 | GET_STATUS | STAT |
| 0x38 | GET_LCD_STATUS | LCDS (24 bytes) |
| 0x3A | GET_DC_CONFIG | DCCF (40 bytes) |
| 0x40 | GET_RS485_IDENT | RID1 (32 bytes), wValue=node_id, 0=local |

### OUT (host -> device)

| bRequest | Вариант | Payload |
|---|---|---|
| 0x7E | SOFT_RESET | нет |
| 0x7F | DEEP_RESET | нет |
| 0x20 | START_STREAM | нет |
| 0x21 | STOP_STREAM | нет |
| 0x3F | REQUEST_RS485_IDENT | нет |
| 0x13,0x14,0x18,0x19,0x1D,0x33,0x34,0x3B,0x3C,0x41 | через wValue | u8 |
| 0x17 | через wValue | u16 |
| 0x39 | через wValue | u16 hold_ds |
| 0x13,0x14,0x18,0x19,0x1D,0x33,0x34,0x39,0x3B,0x3C,0x1F,0x3E,0x41 | data stage | как в payload команды |

## 3) Форматы данных

| Ответ | Сигнатура | Размер | Где читать |
|---|---|---|---|
| STATUS | STAT | 136 bytes | GET_STATUS (EP0 для полного v5; bulk может быть короче) |
| LCD status | LCDS | 24 bytes | GET_LCD_STATUS (EP0) |
| DC config | DCCF | 40 bytes | GET_DC_CONFIG (bulk/EP0) |
| RS485 identity | RID1 | 32 bytes | GET_RS485_IDENT (EP0) |

RS485 optic/LED:
- Master передает свой `optic_active` в `master_status0 bit5`; bits0..4 этого байта остаются selector slave-слота.
- Host на slave читает master optic напрямую из `STAT v5`: `flags_runtime & 0x0080`.
- Slave не добавляет master в `sync_status_bytes`, но использует свежий `master_status0 bit5` для onboard/system address WS2812: master меняется с синего на зеленый; slave при своем локальном `optic_active=1` меняется с белого на желтый, а при master `optic_active=1` без локального срабатывания меняется с белого на магента/фиолетовый.
- TX200 — persistent host-request: `SET_TX_ENABLE=1` включает передачу, `0` выключает. В текущей firmware TX следует request независимо от streaming/SOF/heartbeat/STOP; `HOST_RX_ACK`/`HOST_RX_CLEAR` его не меняют.
- Оптический 38 kHz carrier не gated по приемнику: `SET_OPTIC_POWER=255` задает максимум для проверки фотоприемника.
- Внешняя WS2812-лента optic-gated: `SET_LED_PATTERN`/`LED_EVENT` сохраняют желаемое состояние, но физический вывод разрешен только при локальном или свежем принятом по RS485 `optic_active`; когда все optic bits = 0/устарели, внешняя лента = OFF.
- Без USB Vendor bench-контроль выполняется COM-командами UART: `TX200 1`, `TX200 0`, `OPTP 255`, `OPTH 0..600`, `OPTIC`. `OPTIC` печатает `tx200`, `tx_req`, `tx_cmd_count`, `tx_cmd_val`, `rx`, `local_status`, `master_status` и `master_flags`; bit5 (`0x20`) в status byte равен `optic_active`.

DC `SET_DC_CONFIG`:
- v1 payload 20 bytes: legacy pre-average apply+learn only.
- v2 payload 24 bytes: `version, mode, flags, work_ms, detect_ms, fast_ms, pre_ms, post_ms`.
- stage flags: `0x0010 PRE_APPLY`, `0x0020 PRE_LEARN`, `0x0040 POST_APPLY`, `0x0080 POST_LEARN`.
- Коэффициенты единые для pre/post; одновременно применяется только одна стадия. Комбинации нормализуются с приоритетом `POST_LEARN > PRE_LEARN > POST_APPLY > PRE_APPLY`.
- `pre_ms`/`post_ms = 0` means use the current mode speed.
- Defaults: `WORK=5 s`, `BOOT_FAST/Acquisition=500 s`, `DETECT=10000 s`, `FREEZE/Stop=0 s`.

## 4) CDC диагностические команды (не Vendor bulk)

| Код | Команда | Ответ |
|---|---|---|
| 0x31 | CMD_GET_TEMP | [0x80,0x31,temp_lo,temp_hi] |
| 0x32 | CMD_GET_VERSION | [0x80,0x32,major,minor,patch,build] |

Примечание
- 0x31/0x32 в Vendor модуле не используются как GET_TEMP/GET_VERSION.
