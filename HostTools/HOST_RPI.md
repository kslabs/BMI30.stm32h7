# Raspberry Pi (Debian) — Host Runbook for Vendor USB

Это краткие инструкции для запуска и теста Vendor‑интерфейса на Raspberry Pi (Debian/Ubuntu). Драйверов ставить не нужно: используется PyUSB с бекендом libusb. CDC появится как /dev/ttyACM0, Vendor‑интерфейс доступен через libusb.

- VID/PID: 0xCAFE / 0x4001
- Vendor Interface: IF#2, Bulk OUT 0x03, Bulk IN 0x83
- Строгий порядок кадров: A → B, STAT только между парами; GET_STATUS по EP0 доступен всегда

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
```

---

Примечания
- last‑buffer‑wins включён в прошивке для full‑mode: если хост отстаёт, устройство пропускает старые буферы и отправляет самый свежий, чтобы минимизировать задержку.
- EP0 GET_STATUS доступен всегда и не нарушает A/B‑последовательность.
- Структуру заголовка кадров и STAT см. в `USBprotocol.txt` и коде `HostTools/vendor_stream_read.py`.

## 10) Переключение режимов потока (LATEST vs LOSSLESS_ROI)

В прошивке есть два режима стриминга:

- `STREAM_MODE=0` (**LATEST**, «как раньше»): допускаются пропуски на стороне устройства (last-buffer-wins) — хост получает самые свежие кадры.
- `STREAM_MODE=1` (**LOSSLESS_ROI**): устройство берёт кадры строго по FIFO и отправляет **только ROI окно** (по умолчанию 280..480, 200 семплов). В этом режиме пропусков на стороне прошивки быть не должно.

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
