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
| 0x1D | SET_SYNC_MODE | u8 mode | master/slave/off |
| 0x1E | CALIB_DC_FAST | u8/u16 frames | Временный fast DC |
| 0x1F | SET_DC_CONFIG | v1 payload | Обновление DCCF параметров |
| 0x20 | START_STREAM | нет | Запуск передачи |
| 0x21 | STOP_STREAM | нет | Остановка передачи |
| 0x22 | DEVICE_RESET | нет | Soft/hard reset (зависит от сборки) |
| 0x30 | GET_STATUS | нет | STAT через IN |
| 0x32 | TOGGLE_TIM2CH3_INV | нет | Инверсия TIM2 CH3 |
| 0x33 | SET_TX_ENABLE | u8 0/1 | Управление внешним TX |
| 0x34 | SET_OPTIC_POWER | u8 0..255 | Настройка оптики |
| 0x35 | LED_EVENT | u8 event, u16 ms | Временный LED event |
| 0x36 | HOST_RX_ACK | u32 total_frames | Heartbeat от хоста |
| 0x37 | HOST_RX_CLEAR | нет | Сброс heartbeat |
| 0x39 | SET_OPTIC_HOLD | u16 ds (legacy u8 s) | Время удержания opt active |
| 0x3A | GET_DC_CONFIG | нет | DCCF через IN |
| 0x3B | SET_LED_PATTERN | u8 pattern | Базовый LED pattern |
| 0x3C | SET_DET_ADC | u8 bits bit0=DetADC1 bit1=DetADC2 | Локальные DetADC-биты RS485 status |

## 2) Vendor EP0 Control

### IN (device -> host)

| bRequest | Команда | Ответ |
|---|---|---|
| 0x30 | GET_STATUS | STAT |
| 0x38 | GET_LCD_STATUS | LCDS (24 bytes) |
| 0x3A | GET_DC_CONFIG | DCCF (40 bytes) |

### OUT (host -> device)

| bRequest | Вариант | Payload |
|---|---|---|
| 0x7E | SOFT_RESET | нет |
| 0x7F | DEEP_RESET | нет |
| 0x20 | START_STREAM | нет |
| 0x21 | STOP_STREAM | нет |
| 0x13,0x14,0x18,0x19,0x33,0x34,0x3B,0x3C | через wValue | u8 |
| 0x17 | через wValue | u16 |
| 0x39 | через wValue | u16 hold_ds |
| 0x13,0x14,0x18,0x19,0x33,0x34,0x39,0x3B,0x3C,0x1F | data stage | как в payload команды |

## 3) Форматы данных

| Ответ | Сигнатура | Размер | Где читать |
|---|---|---|---|
| STATUS | STAT | 136 bytes | GET_STATUS (bulk/EP0) |
| LCD status | LCDS | 24 bytes | GET_LCD_STATUS (EP0) |
| DC config | DCCF | 40 bytes | GET_DC_CONFIG (bulk/EP0) |

## 4) CDC диагностические команды (не Vendor bulk)

| Код | Команда | Ответ |
|---|---|---|
| 0x31 | CMD_GET_TEMP | [0x80,0x31,temp_lo,temp_hi] |
| 0x32 | CMD_GET_VERSION | [0x80,0x32,major,minor,patch,build] |

Примечание
- 0x31/0x32 в Vendor модуле не используются как GET_TEMP/GET_VERSION.
