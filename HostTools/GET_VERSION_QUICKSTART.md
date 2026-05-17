# Команда GET_VERSION (0x32) - Быстрый старт

Быстрое руководство по запросу версии прошивки у устройства BMI30.

## Предварительно

```bash
pip install pyusb libusb-package
```

## Использование Python скрипта

### Базовый запрос версии

```bash
cd HostTools
python get_version.py
```

Ожидаемый вывод:
```
Найдено устройство: STMicroelectronics STM32H7 device (S/N: 0674FF424956...)
VID=CAFE, PID=4001
Interface 2 set to alternate setting 1

Отправка команды 0x32 (CMD_GET_VERSION) 1 раз...
------------------------------------------------------------
[1] Отправлено 1 байт
[1] Получено 6 байт: 80 32 01 02 03 00
[1] ✓ Версия прошивки: 1.2.3 (build 0)
------------------------------------------------------------
Готово!
```

### Повторные запросы с задержкой

```bash
python get_version.py --repeat 5 --delay 0.2
```

Выполнит 5 запросов версии с задержкой 200ms между ними.

## Параметры скрипта

```
--vid VID          USB Vendor ID (по умолчанию: 0xCAFE)
--pid PID          USB Product ID (по умолчанию: 0x4001)
--intf INTF        USB Interface number (по умолчанию: 2)
--timeout MS       USB read timeout в миллисекундах (по умолчанию: 1000)
--repeat N         Количество повторений запроса (по умолчанию: 1)
--delay SEC        Задержка между повторениями в секундах (по умолчанию: 0.5)
```

## Использование в коде Python

```python
import usb.core

# Поиск устройства
dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
if not dev:
    raise RuntimeError("Device not found")

# Активация interface #2
dev.set_interface_altsetting(interface_number=2, alternate_setting=1)

# Отправка команды GET_VERSION (0x32)
ep_out = 0x03
cmd = bytes([0x32])
dev.write(ep_out, cmd)

# Чтение ответа (6 байт)
ep_in = 0x83
response = dev.read(ep_in, 6, timeout=1000)

# Парсинг ответа
if response[0] == 0x80 and response[1] == 0x32:
    major, minor, patch, build = response[2], response[3], response[4], response[5]
    print(f"Версия: {major}.{minor}.{patch} (build {build})")
```

## Формат протокола

### Команда (OUT)
- **Размер**: 1 байт
- **Значение**: `0x32`

### Ответ (IN)
- **Размер**: 6 байт
- **Формат**: `[0x80, 0x32, major, minor, patch, build]`

**Примеры ответов:**
- `[0x80, 0x32, 0x01, 0x02, 0x03, 0x00]` → версия `1.2.3`
- `[0x80, 0x32, 0x02, 0x00, 0x00, 0x00]` → версия `2.0.0`

## Проверка совместимости

```python
response = dev.read(ep_in, 6, timeout=1000)
major = response[2]

if major >= 1:
    print("✓ Прошивка совместима с версией API v1.x и выше")
else:
    print("✗ Прошивка требует обновления (версия < 1.0)")
```

## Сочетание с GET_TEMP

Можно запрашивать версию и температуру в одной сессии:

```python
import usb.core
import time

dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
dev.set_interface_altsetting(interface_number=2, alternate_setting=1)

ep_out = 0x03
ep_in = 0x83

# Запросить версию
dev.write(ep_out, bytes([0x32]))
time.sleep(0.05)
version_resp = dev.read(ep_in, 6)
print(f"Версия: {version_resp[2]}.{version_resp[3]}.{version_resp[4]}")

# Запросить температуру
dev.write(ep_out, bytes([0x31]))
time.sleep(0.05)
temp_resp = dev.read(ep_in, 4)
temp_c = int.from_bytes(temp_resp[2:4], byteorder='little', signed=True)
print(f"Температура: {temp_c}°C")
```

## Отладка

Если команда не работает:

1. **Проверить USB подключение:**
   ```bash
   python -c "import usb.core; print(usb.core.find(idVendor=0xCAFE, idProduct=0x4001))"
   ```

2. **Проверить интерфейсы:**
   ```bash
   python HostTools/list_usb_interfaces.py
   ```

3. **Увеличить timeout:**
   ```bash
   python get_version.py --timeout 2000
   ```

4. **Повторить с логированием:**
   ```bash
   python get_version.py --repeat 3 --delay 1.0
   ```

## Интеграция в автоматизацию

### Запуск в PowerShell

```powershell
# Запросить версию и вывести результат
$output = python HostTools/get_version.py
if ($output -like "*Версия прошивки: *") {
    Write-Host "✓ Версия запрошена успешно"
}
```

### Запуск в Bash/Shell

```bash
# Запросить версию 3 раза
python HostTools/get_version.py --repeat 3 --delay 0.5 | grep "Версия прошивки"
```

## Примечания

- Команда может отправляться в любой момент (во время потока или в режиме ожидания)
- Время ответа типично < 10ms
- Версия в формате семантического управления версионированием (MAJOR.MINOR.PATCH)
- Build число зарезервировано для будущего использования
