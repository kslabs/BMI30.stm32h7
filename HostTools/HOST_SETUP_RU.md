# Инструкция для хоста (Windows/Linux): запуск осциллографа каналов A/B

Эти шаги нужны, чтобы хост мог:
- Подключиться к Vendor USB интерфейсу устройства (VID=0xCAFE, PID=0x4001)
- Получать поток ADC кадров
- Отображать осциллограмму **двух каналов A и B отдельно**, по **две линии even/odd** на каждый канал (итого 4 трассы)

## 1) Что подключать

Устройство экспонирует:
- Vendor интерфейс: **IF#2**, активный altsetting **alt=1**
- Endpoints: **OUT 0x03**, **IN 0x83**

Скрипты хоста используют именно эти значения (см. HostTools/gui_oscilloscope_optimized.py).

## 2) Windows: драйвер WinUSB через Zadig (обязательно)

На Windows PyUSB нормально работает, когда Vendor интерфейс привязан к WinUSB.

1. Скачай и запусти Zadig (лучше от администратора).
2. Меню: **Options → List All Devices**.
3. В списке выбери устройство, соответствующее **Interface 2** (часто выглядит как “... (Interface 2)”).
4. В правой части выбери драйвер **WinUSB**.
5. Нажми **Install Driver** (или Replace Driver).

Важно: привязывай WinUSB именно к **Interface 2**, а не ко всему устройству целиком.

## 3) Python зависимости

### Windows (PowerShell)

Рекомендуется Python 3.10+ (проверено с Python 3.12).

Установи пакеты:
- `py -3 -m pip install --upgrade pip`
- `py -3 -m pip install pyusb libusb-package matplotlib`

`libusb-package` даёт libusb backend для PyUSB на Windows.

### Linux (Ubuntu/Debian)

Обычно достаточно:
- `python3 -m pip install --user pyusb matplotlib`

Если доступ запрещён (permissions): настрой udev правило или запускай от root (лучше всё же udev).

### Raspberry Pi (RPi OS / Debian)

Для Raspberry Pi есть отдельный runbook: HostTools/HOST_RPI.md.

Минимум для Vendor USB чтения:
- `sudo apt update`
- `sudo apt install -y python3 python3-pip python3-venv libusb-1.0-0`
- `pip3 install --user pyusb`

Для GUI осциллографа (нужен desktop/X11):
- `sudo apt install -y python3-tk python3-matplotlib`
- `pip3 install --user matplotlib`

Запуск GUI:
- `python3 HostTools/gui_oscilloscope_optimized.py --profile 0`

## 4) Быстрая проверка, что USB виден

### Проверить, что устройство находится

- Windows: `py -3 -c "import usb.core;print(usb.core.find(idVendor=0xCAFE,idProduct=0x4001))"`
- Linux: `python3 -c "import usb.core;print(usb.core.find(idVendor=0xCAFE,idProduct=0x4001))"`

Если вывод `None` — устройство не найдено (кабель/питание/драйвер/другой VID/PID).

### Посмотреть интерфейсы и altsettings

- `py -3 HostTools/list_usb_interfaces.py`

Ожидается, что у IF#2 есть alt=1 и на нём видны EP `0x03` и `0x83`.

## 5) Тест потока (без GUI)

Самый простой прогон на 10 секунд:
- `py -3 HostTools/vendor_usb_start_and_read.py --profile 0 --status-mode bulk --window-sec 10 --frame-samples 10 --abort-no-rx-sec 5`

Смысл:
- Скрипт отправит STOP→настройки→START
- Начнёт читать bulk IN
- Выведет `[STAT]` строки и итоговый `[HOST][SUMMARY]`

## 6) Запуск GUI осциллографа A/B (4 трассы)

Запуск (обычно достаточно так):
- `py -3 HostTools/gui_oscilloscope_optimized.py --profile 0`

Полезные опции:
- `--no-start` — не отправлять START/STOP (подключиться к уже запущенному потоку)
- `--async` — включить async mode (если нужно; по умолчанию режим пар A/B)

В GUI:
- Верхний график: **Channel A** (две линии: A Even и A Odd)
- Нижний график: **Channel B** (две линии: B Even и B Odd)

Even/Odd — это чётность `seq&1` каждого кадра внутри канала, а не “половина буфера”.

## 7) Частые проблемы и решения

### “Device not found”
- Проверь VID/PID (0xCAFE/0x4001)
- Попробуй переподключить USB

### “No backend available” / PyUSB не может читать
- Windows: проверь, что установлен `libusb-package` и поставлен драйвер WinUSB на Interface 2

### “Invalid endpoint address”
- Обычно означает, что интерфейс в alt=0 (без endpoints). Скрипты сами делают `set_interface_altsetting(alt=1)`, но если драйвер/права мешают — это всплывёт.

### “Resource busy” / чтение зависло
- Убедись, что второй хост-процесс (тест/GUI) не держит интерфейс.
- Закрой другие скрипты и попробуй снова.

---

Если хочешь, могу дополнить этот файл конкретными командами “под ваш сценарий” (например: запуск осциллографа без автоконфигурации, или фиксированный Ns через отдельный SET_FRAME_SAMPLES) — скажи, какой профиль/частота/размер кадра на хосте планируется по умолчанию.
