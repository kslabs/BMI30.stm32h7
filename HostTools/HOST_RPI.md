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

## 8) Быстрые команды для повторного запуска

```bash
# Список интерфейсов
python3 HostTools/list_usb_interfaces.py

# Запуск Full Mode @200 Гц (тихий вывод и строгая проверка порядка)
python3 HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 1 --block-hz 200 --frame-samples 10 --full-mode 1 --frames 800 --ab-strict --quiet

# DIAG high‑FPS тест
python3 HostTools/vendor_stream_read.py --vid 0xCAFE --pid 0x4001 --intf 2 --ep-in 0x83 --ep-out 0x03 --profile 2 --full-mode 0 --frame-samples 64 --frames 1500 --ab-strict --quiet
```

---

Примечания
- last‑buffer‑wins включён в прошивке для full‑mode: если хост отстаёт, устройство пропускает старые буферы и отправляет самый свежий, чтобы минимизировать задержку.
- EP0 GET_STATUS доступен всегда и не нарушает A/B‑последовательность.
- Структуру заголовка кадров и STAT см. в `USBprotocol.txt` и коде `HostTools/vendor_stream_read.py`.
