# Raspberry Pi (Debian) — Host Runbook for Vendor USB

Это краткие инструкции для запуска и теста Vendor‑интерфейса на Raspberry Pi (Debian/Ubuntu). Драйверов ставить не нужно: используется PyUSB с бекендом libusb. CDC появится как /dev/ttyACM0, Vendor‑интерфейс доступен через libusb.

- VID/PID: 0xCAFE / 0x4001
- Vendor Interface: IF#2, Bulk OUT 0x03, Bulk IN 0x83
- Bulk‑поток может содержать A/B и сервисные пакеты STAT; GET_STATUS по EP0 доступен всегда

## 1) Установка зависимостей

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv libusb-1.0-0
pip3 install --user pyusb
```

Примечание: пакет `python3-usb` из apt тоже подойдёт, но обычно проще и свежее — `pip3 install pyusb`.

## 2) (Опционально) Правило udev, чтобы не запускать скрипты с sudo

Создайте файл `/etc/udev/rules.d/99-bmi30-vendor.rules` со следующим содержимым:

```bash
sudo tee /etc/udev/rules.d/99-bmi30-vendor.rules >/dev/null <<'RULES'
# Доступ к устройству VID=0xCAFE PID=0x4001 любому пользователю (или группе plugdev)
SUBSYSTEM=="usb", ATTR{idVendor}=="cafe", ATTR{idProduct}=="4001", MODE="0666", GROUP="plugdev", TAG+="uaccess"
# (не обязательно) Если нужно таргетировать именно интерфейс #2:
# SUBSYSTEM=="usb", ATTR{idVendor}=="cafe", ATTR{idProduct}=="4001", ATTRS{bInterfaceNumber}=="02", MODE="0666", GROUP="plugdev", TAG+="uaccess"
RULES
sudo udevadm control --reload
sudo udevadm trigger
```

После этого переподключите устройство USB.

## 3) Проверка, что устройство видно

```bash
lsusb | grep -i cafe
# Ожидаем строку вида: ID cafe:4001 ...

lsusb -t
# Убедитесь, что устройство работает в HighSpeed (480M), например: "5000M/480M" или "480M"
```

Опционально, посмотрите интерфейсы скриптом из репозитория:

```bash
python3 HostTools/list_usb_interfaces.py
# Должно показать IF#2 с endpoint'ами 0x03 (OUT) и 0x83 (IN)
```

## 4) Быстрый старт: чтение потока (Full mode, 200 Гц)

Режим full mode (реальные ADC кадры, last-buffer-wins уже включён в прошивке). Скрипт читает A/B‑пары, проверяет строгий порядок, STAT только между парами, в конце печатает FPS.

Критично: что именно запускает bulk-поток

- Для старта Vendor bulk-потока недостаточно команд DC-компенсации.
- Рабочая последовательность запуска на RPI должна включать:
  - `SetInterface(IF#2, alt=1)`
  - `SET_WINDOWS (0x10)`
  - `SET_STREAM_MODE (0x1A)`
  - при необходимости `SET_PROFILE (0x14)` и `SET_ASYNC (0x18)`
  - `START_STREAM (0x20)`
- Команда `SET_DC_SPEED (0x1F)` и чтение `GET_DC_CONFIG` по EP0 сами по себе bulk-поток не запускают.
- Если в логе есть только `0x1F` и EP0 status, STM32 отвечает на управление, но host-side код не выполнил полноценный запуск bulk stream.

```bash
python3 HostTools/vendor_stream_read.py \
  --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 \
  --profile 1 \
  --block-hz 200 \
  --frame-samples 10 \
  --full-mode 1 \
  --frames 800 \
  --ab-strict \
  --quiet
```

Ожидаемо: около ~200 пар/с на профиле 200 Гц (при готовой прошивке и подключении по HS). Параметры `--frames` и `--frame-samples` подбирайте под задачу.

Поддерживается запрос статуса через EP0, который не мешает потоку:

```bash
python3 HostTools/vendor_stream_read.py --ctrl-status --status-interval 0.5 ...
```

## 4.1) Подтверждение чтения потока на хосте (обязательно для зелёного LED)

Начиная с текущей прошивки, STM32 различает два состояния:

- `синий`:
  STM32 может передавать USB-кадры, но приложение на хосте не подтвердило, что реально читает поток
- `зелёный`:
  хост не только получает USB-трафик на уровне шины, но и приложение на RPI подтверждает, что успешно распарсило кадры

Для этого хост должен использовать две новые Vendor команды:

- `0x36 = VND_CMD_HOST_RX_ACK`
  payload: `u32 little-endian`
  это монотонный счётчик реально прочитанных и распарсенных рабочих кадров `A/B`
- `0x37 = VND_CMD_HOST_RX_CLEAR`
  payload: отсутствует
  сбрасывает host heartbeat

Важно:

- `HOST_RX_ACK` надо слать только после того, как хост действительно прочитал и распарсил рабочие кадры `A/B`
- не надо слать `HOST_RX_ACK` по таймеру без роста счётчика
- `STAT`, `TEST`, таймауты, пустые poll/read и просто факт открытого USB устройства не считаются подтверждением чтения потока
- STM32 считает host reading alive, если последний валидный `HOST_RX_ACK` был не старше примерно `1.5 s`

Рекомендуемый алгоритм на RPI:

- при подключении или перед `START_STREAM` отправить `0x37`
- после каждого успешно распарсенного кадра `A` или `B` увеличить локальный `host_rx_frames_total`
- раз в `100..250 ms`, если счётчик вырос, отправлять `0x36 + le32(host_rx_frames_total)`
- при `STOP_STREAM`, disconnect, reconnect, exception и shutdown отправлять `0x37`

Минимальный пример на Python для bulk OUT `0x03`:

```python
import struct

VND_CMD_HOST_RX_ACK = 0x36
VND_CMD_HOST_RX_CLEAR = 0x37

host_rx_frames_total = 0
last_ack_sent = 0
last_ack_count = 0

def send_host_rx_clear(dev, ep_out):
    dev.write(ep_out, bytes([VND_CMD_HOST_RX_CLEAR]), timeout=1000)

def send_host_rx_ack(dev, ep_out, total_frames):
    payload = bytes([VND_CMD_HOST_RX_ACK]) + struct.pack('<I', total_frames)
    dev.write(ep_out, payload, timeout=1000)

# перед START_STREAM / после reconnect:
send_host_rx_clear(dev, 0x03)

# после каждого успешно распарсенного A/B кадра:
host_rx_frames_total += 1

# периодически, только если count вырос:
if host_rx_frames_total != last_ack_count:
    send_host_rx_ack(dev, 0x03, host_rx_frames_total)
    last_ack_count = host_rx_frames_total

# при STOP / shutdown / exception:
send_host_rx_clear(dev, 0x03)
```

Практическое правило:

- если ваш RPI-скрипт реально читает и парсит поток, но не шлёт `0x36`, LED на STM32 будет `синим`, а не `зелёным`
- это теперь ожидаемое поведение

## 4.2) Оптика: установка мощности, времени удержания и чтение optic_active

В прошивке используются команды Vendor OUT:

- `0x34 = VND_CMD_SET_OPTIC_POWER`
- `0x39 = VND_CMD_SET_OPTIC_HOLD`
- `0x3C = VND_CMD_SET_DET_ADC`
- payload `0x34`: `u8` в диапазоне `0..255`
  - `0` = минимальная мощность/чувствительность
  - `255` = максимальная
- payload `0x39`: новый формат `u16 hold_ds` little-endian, где единица = `0.1 сек`
  - `0` = вернуть значение по умолчанию `30` (`3.0 сек`)
  - `1..600` = удерживать `optic_active=1` ещё `0.1..60.0 сек`
    после последнего принятого высокого уровня
  - старый формат `u8 seconds` тоже принимается для совместимости
- payload `0x3C`: `u8`, bit0=`DetADC1`, bit1=`DetADC2`, остальные биты игнорируются; по умолчанию оба бита равны `0`

Пример установки через ваш host-код (bulk OUT `0x03`):

```python
VND_CMD_SET_OPTIC_POWER = 0x34
VND_CMD_SET_OPTIC_HOLD = 0x39
VND_CMD_SET_DET_ADC = 0x3C

optic_power = 255     # 0..255, максимум для проверки 38 kHz TX/приемника
optic_hold_s = 1.5    # 0=default(3.0), step 0.1, max 60.0
optic_hold_ds = int(round(optic_hold_s * 10.0))
det_adc = 0x03        # bit0=DetADC1, bit1=DetADC2

dev.write(0x03, bytes([VND_CMD_SET_OPTIC_POWER, optic_power & 0xFF]), timeout=1000)
dev.write(0x03, bytes([VND_CMD_SET_OPTIC_HOLD]) + optic_hold_ds.to_bytes(2, "little"), timeout=1000)
dev.write(0x03, bytes([VND_CMD_SET_DET_ADC, det_adc & 0x03]), timeout=1000)
```

Единая логика оптического датчика:

1. `PD0=1` немедленно устанавливает `optic_active=1`.
2. Пока `PD0=1`, таймер удержания постоянно повторно запускается.
3. После перехода `PD0` в `0` обработанный `optic_active` остаётся равен `1`
   ещё `optic_hold_ds * 0.1` секунд.
4. Уровень `PD0=0` не запускает и не продлевает таймер. После истечения времени
   `optic_active` переходит в `0`.
5. Один и тот же `optic_active` используется системным адресным светодиодом,
   USB `STAT/EVT1`, RS485 status bit5 и картой всей группы.
6. На системном WS2812 локальный `optic_active=1` немедленно меняет только цвет.
   Если TX включен, обычный TX-breathe продолжает модулировать яркость этого цвета.

Как прочитать «что установлено сейчас» и обработанный сигнал:

1. Запросите `STAT` через `GET_STATUS` (`0x30`, лучше по EP0 vendor IN).
2. В пакете `STAT` используйте поля:
   - `reserved3[15:8]` = `optic_power` (текущее установленное значение 0..255)
   - `reserved3[7:2]` = legacy `optic_hold_seconds`, округлённое вверх до секунд
   - `reserved3[0]` = локальный удержанный `optic_active`
   - `flags_runtime bit5 (0x0020)` = дублирующий флаг локального `optic_active`
   - `flags_runtime bit7 (0x0080)` = master optic активен: локально на master или принят по RS485 на slave
   - `flags_runtime bit8 (0x0100)` = активен любой локальный/RS485 optic в группе
   - `flags_runtime bit9 (0x0200)` = в группе назначен выбранный sensor-slave
   - `flags_runtime bit10 (0x0400)` = это USB-устройство является выбранным sensor-slave
   - `flags_runtime bit11 (0x0800)` = от выбранного sensor-slave получен свежий активный `optic`, `DetADC1` или `DetADC2`
   - в полном `STAT v6` длиной `137` байт: offset `96`, `u16 optic_hold_ds` = точное время удержания в шагах `0.1 сек`

В `STAT v6` также отдаются состояния фотоприёмников всей sync-системы:

- offset `99`: `u8 sync_local_status`
- offset `100`: `u32 sync_seen_mask`, bit N=device_id N, N=0..31
- offset `104`: `u8 sync_node_count`
- offset `105..136`: `u8 sync_status_bytes[32]`, прямой индекс `device_id`
- формат status byte: bits `0..4=selector` у local master или `node_id` у slave/remote, bit `5=photoreceiver active`, bit `6=DetADC1`, bit `7=DetADC2`
- master optic на slave-host читается из `flags_runtime & 0x0080`; этот флаг выставляется из свежего `master_status0 bit5`, который уже передается master по RS485.
- Полный прямой формат требует `STAT v6` длиной 137 байт. Асинхронный `STAT` длиной
  136 байт сохраняет legacy v5: index/bit `0..30` означает `device_id 1..31`;
  `device_id 0` в v5 не представлен.
- При любом изменении controlled sensor firmware немедленно ставит в Bulk IN
  совместимый 136-байтовый `STAT` и `EVT1 type=0x14 SENSOR_MAP`. Payload `SENSOR_MAP`
  имеет 40 байт: `version, local_id, flags, node_count, seen_mask LE,
  status_bytes[32]` с прямым индексом `device_id 0..31`.

В проекте это уже декодируют скрипты:

- `python3 HostTools/vendor_get_status.py --ctrl --repeat 5`
- `python3 HostTools/vendor_quick_status.py --secs 5`
- `python3 HostTools/vendor_stream_read.py --optic-power 120 --optic-hold-s 1.5 ...`

В выводе будут поля:

- `optic_power=...` (какой параметр реально установлен сейчас)
- `optic_hold_ds=...` (точное время удержания в шагах 0.1 сек)
- `optic_active=0/1` (обработанный вход с удержанием после последней `1`)
- `tx_enable=0/1` (состояние внешнего TX-gate, если используется)

## 4.3) Как прочитать sync-индикатор LCD (`M/S/O`, число и цвет)

Добавлена отдельная vendor-команда:

- `0x38 = VND_CMD_GET_LCD_STATUS`
- рекомендуемый транспорт: `EP0 vendor IN`
- ответ: короткая структура `LCDS` длиной `24` байта

Самый простой способ:

```bash
python3 HostTools/read_lcd_status.py
```

Скрипт выводит:

- `raw_mode`
  это реальный режим sync-логики: `MASTER`, `SLAVE` или `OFF`
- `display_mode`
  это именно то, что сейчас рисует LCD
- `display_char`
  буква индикатора: `M`, `S` или `O`
- `display_value`
  число рядом с буквой
- `color`
  цвет индикатора на LCD
- `signal_alive`
  LCD считает, что sync сейчас жив
- `sync_ok_visual`
  LCD считает sync корректным
- `color_locked`
  зелёный уже защёлкнут антидребезгом LCD state machine
- `display_fallback`
  LCD перешёл в fallback и принудительно показывает `M00`

Что означает число:

- если `display_mode=MASTER`, `display_value` = число обнаруженных slave
- если `display_mode=SLAVE`, `display_value` = локальный номер слота/узла
- если `display_mode=OFF`, цифры на LCD пустые, а в пакете `display_value` остаётся `0`

Что означает цвет:

- `GREEN`
  sync есть и он прошёл визуальный lock
- `RED`
  sync живой, но lock ещё не набран или качество sync пока плохое
- `CYAN`
  сигнала sync нет, LCD показывает fallback `M00`

Минимальный пример на Python без готового скрипта:

```python
import struct
import usb.core
import usb.util

VID, PID = 0xCAFE, 0x4001
INTF = 2
CMD_GET_LCD_STATUS = 0x38

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    raise SystemExit("Device not found")

try:
    dev.set_configuration()
except Exception:
    pass

try:
    usb.util.claim_interface(dev, INTF)
except Exception:
    pass

raw = dev.ctrl_transfer(0xC1, CMD_GET_LCD_STATUS, 0, INTF, 24, timeout=1000)
sig, ver, raw_mode, display_mode, display_value, slave_count, node_id, color_id, display_char, color_rgb565, flags, sync_age_ms, text = \
    struct.unpack("<4sBBBBBBBBHHI4s", bytes(raw))

print(sig, ver, display_mode, display_value, chr(display_char), hex(color_rgb565), hex(flags), text)
```

Практическое правило:

- для UI ориентируйтесь на `display_mode`, `display_value`, `display_char`, `color`
- для диагностики и логики управления ориентируйтесь на `raw_mode`, `slave_count`, `node_id`, `signal_alive`, `sync_ok_visual`

## 4.4) Назначение роли/ID и список устройств RS485

Автоматического голосования и нумерации нет. Каждый RPI через локальный USB отдельно задаёт:

- роль `MASTER/SLAVE`: `0x1D + mode + u64 unix_ms LE`;
- `device_id 0..31`: `0x3D + u8 device_id`;
- номер RPI и IP: `0x42 + u16 rpi_number LE + ip[4]`.

Роль и `device_id` сохраняются во Flash. Значение ID 0 допустимо и отображается как M00/S00;
неназначенность хранится отдельным флагом. Пока ID не назначен, STM32 не передаёт в общую RS485,
но ADC/DMA и USB продолжают работать. Команда назначения не идёт по RS485: несколько новых плат
разделяются их собственными локальными USB/RPI.

Специального sensor-slave нет. Состояния каждого устройства находятся по прямому адресу
`sync_status_bytes[device_id]`. Любое изменение контролируемого sensor bit отправляется срочно
в ближайшем цикле 200 Hz и повторяется трижды.

Карта читается так:

- `0x3F REQUEST_RS485_IDENT` запускает scan на master;
- `0x40 GET_RS485_IDENT`, `wValue=0..31` читает конкретный device ID;
- `wValue=0xFF` читает локальную плату;
- `RID1` содержит UID STM32, отдельный `rpi_number`, IP и роль.

Обязательное правило для host/backend портала: полный STM32 UID96 — это постоянный уникальный
ключ платы, а `device_id` — изменяемое поле. Если тот же UID пришёл с новым ID, backend должен
обновить существующую запись и сразу удалить старую привязку ID, а не создавать второе устройство
и ждать offline TTL. Активный список строится только по `sync_seen_mask`; сохранённый `RID1` и
`last_seen` служат кэшем/историей и не подтверждают подключение. Backend и frontend обязаны
дедуплицировать строки по UID96, причём UI key также должен быть UID96. Один UID запрещено
одновременно показывать как два активных устройства даже в переходном snapshot при смене ID.

```bash
python3 HostTools/vendor_rs485_device_list.py --serial <USB_SERIAL> \
  --device-id 0 --rpi-number 12 --ip 192.168.1.12
```

## 5) DIAG режим (максимальный FPS, тестовые кадры)

DIAG отправляет синтетические кадры, паддированные до 512 Б (HS MPS), чтобы убрать лишние накладные расходы. STAT по Bulk в DIAG блокируется, порядок A→B сохраняется.

```bash
python3 HostTools/vendor_stream_read.py \
  --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 \
  --profile 2 \
  --full-mode 0 \
  --frame-samples 64 \
  --frames 1500 \
  --ab-strict \
  --quiet
```

Ожидаемо: высокая частота пар/с (>>300 FPS). Значение зависит от размера тестового кадра и платформы.

## 6) CDC (опционально) — /dev/ttyACM0

CDC‑порт доступен как /dev/ttyACM0. Для быстрого теста можно использовать `HostTools/rpi_cdc_client.py`:

```bash
python3 HostTools/rpi_cdc_client.py /dev/ttyACM0
```

Скрипт умеет: PING/ACK, настройку окон/частоты блока, START/STOP и чтение кадров CDC‑протокола.

## 7) Типичные проблемы и решения

- Permission denied / [Errno 13]:
  - Запустите с sudo или добавьте udev‑правило (см. раздел 2) и переподключите USB.
- Resource busy / интерфейс занят ядром:
  - Для Vendor IF#2 обычно драйвер ядра не назначается. Наши скрипты всё равно пытаются `detach_kernel_driver`. Если ошибка не исчезает — проверьте, что выбирается именно IF#2.
- Таймауты IN при простое:
  - Это нормально между парами. Используйте `--ctrl-status` для периодического keepalive.
- Низкая скорость/рывки:
  - Убедитесь, что устройство работает в HS (lsusb -t). Не подключайте через слабые хабы, проверьте питание RPi.
  - На время замеров не читайте/не логируйте CDC‑порт — лишний вывод снижает пропускную способность.
  - Используйте `--quiet` у скриптов на хосте.

## 8) GUI осциллограф (визуализация в реальном времени)

Для визуализации данных в реальном времени используйте GUI‑осциллограф.

Важно:
- Текущая версия GUI в репозитории использует **matplotlib** backend **TkAgg** (требуется Tkinter и X11/desktop окружение).
- Если Raspberry Pi работает headless (без GUI), используйте тестовые скрипты чтения потока (раздел 4/9) или запускайте GUI на ПК.
- Для headless с выводом окна можно использовать X11‑forwarding: `ssh -X pi@<ip>` (на клиенте должен быть X сервер).

### Установка GUI зависимостей (RPi OS / Debian)

```bash
sudo apt update
sudo apt install -y python3-tk python3-matplotlib
pip3 install --user matplotlib
```

Примечание: в некоторых сборках достаточно только `python3-matplotlib`, но `python3-tk` обязателен для TkAgg.

### Стандартная версия
```bash
python3 HostTools/gui_oscilloscope.py --ns 0 --profile 0 --watchdog
```

### Оптимизированная версия (рекомендуется для RPi)
```bash
python3 HostTools/gui_oscilloscope_optimized.py --ns 0 --profile 0 --watchdog
```

**Параметры (для совместимости):**
- `--ns 0` — автоматический выбор количества семплов для отображения
- `--profile 0` — профиль 0 (full buffer mode, 200 Гц)
- `--watchdog` — включить watchdog для автоматического переподключения при зависании устройства

GUI отображает:
- 2 графика: **Channel A** и **Channel B**
- по 2 линии на график: **even/odd** (итого 4 трассы)

Если окно не появляется:
- Проверьте, что есть графическая среда (локально подключён монитор/desktop) или используйте X11 forwarding.
  Например: `echo $DISPLAY` должен быть не пустым.

## 9) Быстрые команды для повторного запуска

```bash
# Список интерфейсов
python3 HostTools/list_usb_interfaces.py

# Запуск Full Mode @200 Гц (тихий вывод и строгая проверка порядка)
python3 HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 1 --block-hz 200 --frame-samples 10 --full-mode 1 --frames 800 --ab-strict --quiet

# DIAG high‑FPS тест
python3 HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 2 --full-mode 0 --frame-samples 64 --frames 1500 --ab-strict --quiet

# GUI осциллограф (оптимизированный)
python3 HostTools/gui_oscilloscope_optimized.py --ns 0 --profile 0 --watchdog

## 9.1) Управление 200 Hz marker/TX командами хоста

Команда `0x33 SET_TX_ENABLE` разрешает или запрещает передачу `TX200`.
Она не управляет отдельной 38 kHz оптической несущей (`0x34 SET_OPTIC_POWER`).

- payload `0x01` — включить TX;
- payload `0x00` — выключить TX;
- физический TX следует правилу `tx_request AND streaming`;
- `HOST_RX_ACK` и `HOST_RX_CLEAR` не имеют права менять TX request;
- краткий пропуск SOF/heartbeat не меняет TX;
- `STOP_STREAM` гасит физический TX, но сохраняет request для следующего START.

Обязательный порядок при запуске или переподключении:

```text
HOST_RX_CLEAR
STOP_STREAM
SET_* configuration
START_STREAM
SET_TX_ENABLE 0/1    # рекомендуемый явный apply + последующий readback
```

`SET_TX_ENABLE=1`, отправленный перед STOP, сохраняется как request; во время STOP
физический TX выключен, а после START восстанавливается автоматически. Host всё
равно должен делать readback после финального START.

## 9.2) 2026-07-13 — TX request не должен самопроизвольно сбрасываться

Прошивка хранит host-request отдельно от подтверждённого состояния TX:
`physical_tx = tx_request AND streaming`. Поэтому состояние `streaming=1,
tx_req=1, tx200=0` является ошибкой, а при `streaming=0, tx_req=1` ожидается
`tx200=0`. Переход `tx_req: 1 -> 0` означает, что пришёл `SET_TX_ENABLE=0`
или reset; `STOP_STREAM` request не стирает.

- `START_STREAM` и heartbeat-команды не заменяют явную команду хоста `0x33`.
- Исправленная прошивка больше не сбрасывает TX request на `HOST_RX_CLEAR`.
- После `STOP_STREAM` host может повторно отправить желаемое `0x33`, но достаточно сохранённого request: физическое разрешение вернётся на следующем START.
- Reconnect/retry/watchdog не должны отправлять `0x33 0x00`, если пользователь оставил TX включенным.
- Проверка: `STAT.flags_runtime & 0x0010` показывает подтверждённое состояние TX;
  COM-команда `OPTIC` дополнительно печатает `tx_req`, `tx200`, `tx_cmd_count` и `tx_cmd_val`.
- В `HostTools/vendor_stream_read.py` параметр `--tx-enable` применяется после START,
  потому что прежний порядок `TX_ENABLE -> CLEAR -> STOP -> START` всегда терял включение.

Ключевые строки для проверки в стороннем host-коде: `0x33`, `CMD_SET_TX_ENABLE`,
`START_STREAM`, `STOP_STREAM`, `HOST_RX_CLEAR`, `reconnect`, `retry`, `watchdog`.
- `init sequence`

---

## 9.3) Управление WS2812 паттернами с RPI host

Сейчас у Raspberry Pi host есть два разных способа влиять на адресные светодиоды:

- постоянный паттерн 20 динамических светодиодов можно выбрать с RPI через Vendor USB команду `0x3B`
- временный визуальный паттерн можно запустить с RPI через Vendor USB команду `0x35`

Важно:
- первый onboard/system LED остаётся под управлением STM32; прошивка накладывает на него системный статус независимо от выбранного host-паттерна
- команда `0x3B` выбирает базовый паттерн для 20 динамических LED
- команда `0x35` запускает временное событие поверх текущего базового паттерна
- внешняя лента optic-gated: команды `0x3B`/`0x35` сохраняют выбранный паттерн/событие, но 20 динамических LED реально светятся только пока активен локальный или свежий принятый по RS485 `optic_active`; без активной оптики лента рендерится как `OFF`
- оптический 38 kHz TX не gated по приемнику; для проверки приемника поставьте `0x34=255` (`OPTP 255` через COM). `0x33/TX200` относится только к 200 Hz marker/TX и при активном stream следует явной команде хоста.
- host на slave читает срабатывание master через `STAT v5`: `flags_runtime & 0x0080`.

### Команда `0x3B` (`VND_CMD_SET_LED_PATTERN`)

Формат payload:

```text
byte0 = 0x3B
byte1 = pattern_id
```

Поддерживаемые `pattern_id`:

- `0` = `OFF`
- `1` = `IDLE_BREATHE`
- `2` = `STREAMING`
- `3` = `SYNC_PULSE`
- `4` = `UART_RX`
- `5` = `TUNE`
- `6` = `RECOVERY`
- `7` = `HARD_RESET`
- `8` = `EVENT_B_UP`
- `9` = `EVENT_A_DOWN`
- `10` = `EVENT_BOTH_ALT`
- `11` = `EVENT_SPLIT_IN`
- `12` = `EVENT_SPLIT_OUT`
- `13` = `TEST_DRIP`
- `14` = `TEST_SCOPE_RGB`
- `15` = `TEST_BLUE`
- `16` = `TEST_COLOR_CYCLE`

Пример:

```python
VND_CMD_SET_LED_PATTERN = 0x3B
dev.write(0x03, bytes([VND_CMD_SET_LED_PATTERN, 13]), timeout=1000)  # TEST_DRIP
dev.write(0x03, bytes([VND_CMD_SET_LED_PATTERN, 0]), timeout=1000)   # OFF
```

Готовый helper:

```bash
python3 HostTools/send_led_event.py --pattern TEST_DRIP
python3 HostTools/send_led_event.py --pattern OFF
```

Текущий выбранный паттерн читается из `STAT v5`: offset `98`, `u8 led_pattern`.

### Команда `0x35` (`VND_CMD_LED_EVENT`)

Формат payload:

```text
byte0 = 0x35
byte1 = event_id
byte2 = duration_ms low byte
byte3 = duration_ms high byte
```

Поддерживаемые `event_id`:

- `0x01` = `CHANNEL_B` = красный паттерн вверх (`RED_UP`)
- `0x02` = `CHANNEL_A` = красный паттерн вниз (`RED_DOWN`)
- `0x03` = `BOTH` = попеременное движение вниз/вверх четырёх красных сегментов (`RED_DOWN_UP_ALT`)
- `0x04` = `SPLIT_IN` = верхняя половина вниз, нижняя вверх (`RED_SPLIT_IN`, движение к центру)
- `0x05` = `SPLIT_OUT` = верхняя половина вверх, нижняя вниз (`RED_SPLIT_OUT`, движение от центра)

Если `duration_ms = 0`, прошивка автоматически использует `1600 ms`.

### Самый простой способ на RPi: готовый скрипт

В репозитории есть [send_led_event.py](/d:/Users/Admin/Documents/Work/BMI20/STM32/BMI30.stm32h7/HostTools/send_led_event.py), который уже отправляет эту команду через Vendor IF#2.

Примеры:

```bash
# Красный паттерн вверх
python3 HostTools/send_led_event.py --event B --duration-ms 1600

# Красный паттерн вниз
python3 HostTools/send_led_event.py --event A --duration-ms 1600

# Одновременное срабатывание двух каналов: попеременно вниз/вверх
python3 HostTools/send_led_event.py --event BOTH --duration-ms 1600

# Движение к центру: верхняя половина вниз, нижняя вверх
python3 HostTools/send_led_event.py --event SPLIT_IN --duration-ms 1600

# Движение от центра: верхняя половина вверх, нижняя вниз
python3 HostTools/send_led_event.py --event SPLIT_OUT --duration-ms 1600

# Демонстрация: вверх, затем вниз
python3 HostTools/send_led_event.py --event demo --duration-ms 1600 --gap-ms 500
```

Параметры по умолчанию у скрипта:

- `VID = 0xCAFE`
- `PID = 0x4001`
- `IF = 2`
- `EP_OUT = 0x03`

### Прямой пример без helper script

```bash
python3 - <<'PY'
import struct
import usb.core
import usb.util

VID, PID = 0xCAFE, 0x4001
INTF, EP_OUT = 2, 0x03
VND_CMD_LED_EVENT = 0x35
VND_LED_EVENT_CHANNEL_B = 0x01   # RED_UP
VND_LED_EVENT_CHANNEL_A = 0x02   # RED_DOWN
VND_LED_EVENT_BOTH = 0x03        # RED_DOWN_UP_ALT
VND_LED_EVENT_SPLIT_IN = 0x04    # RED_SPLIT_IN
VND_LED_EVENT_SPLIT_OUT = 0x05   # RED_SPLIT_OUT

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    raise SystemExit("Device not found")

try:
    dev.set_configuration()
except Exception:
    pass

try:
    if dev.is_kernel_driver_active(INTF):
        dev.detach_kernel_driver(INTF)
except Exception:
    pass

try:
    usb.util.claim_interface(dev, INTF)
except Exception:
    pass

try:
    dev.set_interface_altsetting(interface=INTF, alternate_setting=1)
except Exception:
    pass

payload = struct.pack("<BBH", VND_CMD_LED_EVENT, VND_LED_EVENT_CHANNEL_B, 1600)
dev.write(EP_OUT, payload, timeout=1000)
print("Sent RED_UP for 1600 ms")
PY
```

Пример именно для события "оба канала одновременно":

```bash
python3 - <<'PY'
import struct
import usb.core

VID, PID = 0xCAFE, 0x4001
INTF, EP_OUT = 2, 0x03
VND_CMD_LED_EVENT = 0x35
VND_LED_EVENT_BOTH = 0x03

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    raise SystemExit("Device not found")

try:
    dev.set_configuration()
except Exception:
    pass

try:
    dev.set_interface_altsetting(interface=INTF, alternate_setting=1)
except Exception:
    pass

payload = struct.pack("<BBH", VND_CMD_LED_EVENT, VND_LED_EVENT_BOTH, 1600)
dev.write(EP_OUT, payload, timeout=1000)
print("Sent RED_DOWN_UP_ALT for 1600 ms")
PY
```

### Как это сочетается с локальной кнопкой `PC13`

- `PC13` переключает постоянный фон тестовых паттернов.
- Команда `0x35` временно накладывает событие `RED_UP`, `RED_DOWN` или `RED_DOWN_UP_ALT`.
- Команда `0x35` временно накладывает событие `RED_UP`, `RED_DOWN`, `RED_DOWN_UP_ALT`, `RED_SPLIT_IN` или `RED_SPLIT_OUT`.
- После окончания таймера устройство возвращается к текущему локально выбранному паттерну.

### Актуальный список локальных тестовых паттернов на устройстве

Текущий цикл по `PC13` такой:

- `DRIP`
- `RED_UP`
- `RED_DOWN`
- `RGB_SCOPE`
- `RED_BLUE_SPLIT`
- `COLOR_CYCLE`

Для `RED_UP` и `RED_DOWN` в текущей версии прошивки красный канал выставлен на максимальную яркость.

Примечания
- last‑buffer‑wins включён в прошивке для full‑mode: если хост отстаёт, устройство пропускает старые буферы и отправляет самый свежий, чтобы минимизировать задержку.
- EP0 GET_STATUS доступен всегда и не нарушает A/B‑последовательность.
- Структуру заголовка кадров и STAT см. в `USBprotocol.txt` и коде `HostTools/vendor_stream_read.py`.

## 10) Переключение режимов потока (LATEST vs LOSSLESS_ROI)

В прошивке есть два режима стриминга:

- `STREAM_MODE=0` (**LATEST**, «как раньше»): допускаются пропуски на стороне устройства (last-buffer-wins) — хост получает самые свежие кадры.
- `STREAM_MODE=1` (**LOSSLESS_ROI**): устройство берёт кадры строго по FIFO и отправляет **только ROI окно** (по умолчанию 280..480, 200 семплов). В этом режиме пропусков на стороне прошивки быть не должно.

Также есть режим усреднения:

- `STREAM_MODE=2` (**AVG_ROI**): устройство усредняет ROI по N входным буферам и отправляет **усреднённые** ROI‑кадры с пониженной частотой.

Важно про «без потерь»:
- На уровне прошивки в `LOSSLESS_ROI` кадры выбираются последовательно (FIFO), то есть устройство не перескакивает через буферы.
- Потери возможны только если хост/USB реально отваливаются (STALL/PIPE/reopen), либо хост не успевает читать. Для контроля смотрите `Gaps` в GUI или логи чтения.

### Самый простой способ на RPi: запускать `vendor_usb_start_and_read.py` с нужными параметрами

Скрипт сам делает: `STOP -> SET_* -> START -> read`.

**A) LATEST (600 семплов, допускаются пропуски):**

```bash
python3 HostTools/vendor_usb_start_and_read.py \
  --profile 0 \
  --full-mode 1 \
  --stream-mode 0 \
  --async-mode 1 \
  --ch-mode 2 \
  --win0 0 0 --win1 0 0 \
  --window-sec 30
```

**B) LOSSLESS_ROI (200 семплов из DMA[280..479], без пропусков на стороне устройства):**

```bash
python3 HostTools/vendor_usb_start_and_read.py \
  --profile 0 \
  --full-mode 1 \
  --stream-mode 1 \
  --async-mode 0 \
  --ch-mode 2 \
  --win0 280 200 --win1 0 0 \
  --window-sec 30
```

Подсказка: в `LOSSLESS_ROI` прошивка всё равно принудительно выключает async (делает пары), но лучше на хосте тоже слать `--async-mode 0`.

## 11) Усреднение на устройстве (AVG_ROI, avg_n=2..32) и строгое соблюдение частоты

Вы можете менять количество буферов для усреднения `avg_n` от 2 до 32.

### Как считается частота усреднённых пакетов

Исходная частота входных DMA‑буферов (профиль 0) — 200 Гц.

В `AVG_ROI` прошивка ведёт два независимых накопителя по parity (even/odd) и выпускает усреднённый кадр **ровно после** получения `avg_n` входных буферов соответствующей parity.
Из этого следует:

- Частота усреднённых пакетов **для одной parity**: $f_{parity}=100/avg_n$ Гц
- Частота усреднённых пакетов **для одного канала суммарно (even+odd)**: $f_{ch}=200/avg_n$ Гц

Примеры (суммарно по каналу):

- `avg_n=2`  → $200/2=100$ Гц
- `avg_n=20` → $200/20=10$ Гц
- `avg_n=32` → $200/32=6.25$ Гц

Важно: «строго соблюдалась» здесь означает, что кадр появляется не когда хост “успеет”, а по факту накопления ровно `avg_n` входных буферов. Хосту на RPi нужно только стабильно читать bulk‑IN.

### DC-компенсация в AVG_ROI и LOSSLESS_ROI

В STM32 есть одна таблица коэффициентов `[channel][parity][sample]` и один процесс адаптации.

- RPI отправляет только `SET_DC_SPEED (0x1F)` с `uint32 settle_ms` little-endian.
- Полный Bulk OUT пакет строго 5 байт.
- `0` выключает обучение, но найденная коррекция продолжает применяться.
- `1` — максимально быстро (1 мс); `1000` = 1 секунда.
- Чем больше ненулевое значение, тем медленнее.
- Ненулевое значение действует постоянно до следующей команды и не зависит от stream mode.
- После reset скорость равна `0`; startup/reconnect RPI обязан явно отправить требуемое значение.
- В `AVG_ROI` DC применяется и обучается один раз на raw ROI до усреднения.
- В `LOSSLESS_ROI` текущая поправка применяется к локальной копии ROI перед USB.
- Режимов WORK/DETECT/BOOT_FAST, таймеров acquisition/detection, pre/post и amplitude gate нет.

Пример:

```python
import struct

stream.send_cmd(0x1F, struct.pack("<I", 1_000))  # примерно 1 секунда
stream.send_cmd(0x1F, struct.pack("<I", 1))      # максимально быстро

# Остановить обучение, оставив вычитание найденной поправки:
stream.send_cmd(0x1F, struct.pack("<I", 0))
```

Автоматическая периодическая запись Flash отключена. Чтобы таблица пережила reset или отключение
питания, RPI отдельно отправляет `0x2B SAVE_DC_TO_FLASH` в выбранный им момент. Не отправляйте
эту команду на каждом кадре. Обычная прошивка без mass erase обычно сохраняет выделенный DC-сектор;
mass erase его очищает.

### Почему при большом avg_n появляется “время на повторы”

USB скорость (и базовая частота входных буферов) не меняется, но усреднённые кадры выходят реже.
Интервал между усреднёнными кадрами по каналу: $T=avg_n/200$ секунд.

Чем больше `avg_n`, тем больше у хоста “запаса” между двумя усреднёнными кадрами — в этот запас можно помещать дополнительные обмены (например, запрос повтора/повторной выдачи последнего усреднённого кадра), не влияя на выпуск следующего усреднённого результата.

### Запуск на RPi (рекомендуемый способ)

Скрипт делает `STOP -> SET_WINDOWS -> SET_STREAM_MODE(mode, avg_n) -> START -> read`.

**AVG_ROI с ROI=280..479 (200 семплов) и avg_n=20 (10 Гц на канал):**

```bash
python3 HostTools/vendor_usb_start_and_read.py \
  --profile 0 \
  --full-mode 1 \
  --stream-mode 2 \
  --avg-n 20 \
  --async-mode 0 \
  --ch-mode 2 \
  --win0 280 200 --win1 280 200 \
  --status-mode ctrl \
  --log-interval 1.0 \
  --window-sec 60
```

**Быстро прогнать диапазон avg_n (пример: 2, 4, 8, 20, 32):**

```bash
for n in 2 4 8 20 32; do
  echo "=== AVG_N=$n ==="
  python3 HostTools/vendor_usb_start_and_read.py \
    --profile 0 --full-mode 1 --stream-mode 2 --avg-n "$n" \
    --async-mode 0 --ch-mode 2 --win0 280 200 --win1 280 200 \
    --status-mode ctrl --log-interval 1.0 --window-sec 20
done
```

### Как проверить, что частота соблюдается

Для контроля используйте измеритель интервалов по timestamp (с устройства) и по времени приёма (на хосте):

```bash
python3 HostTools/vendor_measure_block_rate.py --secs 30
```

Ожидаемая частота (суммарно по каналу) — $200/avg_n$ Гц. На практике на хосте может быть небольшой джиттер доставки, но средняя частота по device‑timestamp должна совпадать.

### Переключение из своего кода (сырые байты команд по Bulk OUT 0x03)

Рекомендуемая последовательность (общая идея — короткий STOP/START, как в GUI):

**Перейти в ROI 280..480 (200):**
- `STOP`: `0x21`
- `SET_WINDOWS`: `0x10 + <u16 start0=280> + <u16 len0=200> + <u16 start1=0> + <u16 len1=0>`
- `SET_STREAM_MODE`: `0x1A 0x01`
- `SET_ASYNC_MODE`: `0x18 0x00` (строгие пары)
- `START`: `0x20`

**Вернуться в LATEST:**
- `STOP`: `0x21`
- `SET_WINDOWS`: `0x10 + (0,0,0,0)`
- `SET_STREAM_MODE`: `0x1A 0x00`
- `SET_ASYNC_MODE`: `0x18 0x01` (быстрее, независимые A/B)
- `START`: `0x20`
