# USB Диагностические команды (GET_TEMP, GET_VERSION)

Справочник по диагностическим команда запроса информации от устройства BMI30.

## Обзор команд

| Код | Имя | Назначение | Payload OUT | Ответ IN |
|-----|-----|-----------|-------------|---------|
| 0x31 | CMD_GET_TEMP | Получить температуру кристалла | нет (1 байт) | 4 байта: [0x80, 0x31, temp_lo, temp_hi] |
| 0x32 | CMD_GET_VERSION | Получить версию прошивки | нет (1 байт) | 6 байт: [0x80, 0x32, major, minor, patch, build] |

## Требования перед использованием

```python
import usb.core

# Поиск и открытие устройства
dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)

# Активировать Vendor Interface #2 с active endpoints
dev.set_interface_altsetting(interface_number=2, alternate_setting=1)

# Endpoints
ep_out = 0x03  # OUT для команд
ep_in = 0x83   # IN для ответов
```

## CMD_GET_TEMP (0x31) - Температура кристалла

Читает встроенный датчик температуры STM32H723.

### Протокол

| Направление | Размер | Содержание | Описание |
|-------------|--------|-----------|---------|
| OUT | 1 | `0x31` | Команда |
| IN | 4 | `[0x80, 0x31, temp_lo, temp_hi]` | Ответ |

**Формат температуры**: int16_t в little-endian
- Диапазон: -40°C до +85°C
- Точность: ±5°C
- Разрешение: 0.5°C

### Пример использования

```python
# Отправить команду
dev.write(ep_out, bytes([0x31]))

# Прочитать ответ (4 байта)
response = dev.read(ep_in, 4, timeout=1000)

# Парсинг
if response[0] == 0x80 and response[1] == 0x31:
    # Преобразовать из LE int16_t
    temp_raw = response[2] | (response[3] << 8)
    if temp_raw & 0x8000:
        temp_c = temp_raw - 0x10000
    else:
        temp_c = temp_raw
    print(f"Температура: {temp_c}°C")
```

### Примеры ответов

| Ответ HEX | Интерпретация |
|-----------|--------------|
| `80 31 1C 00` | +28°C |
| `80 31 00 00` | 0°C |
| `80 31 D8 FF` | -40°C |
| `80 31 55 00` | +85°C |

## CMD_GET_VERSION (0x32) - Версия прошивки

Возвращает версию встроенного ПО в формате семантического версионирования.

### Протокол

| Направление | Размер | Содержание | Описание |
|-------------|--------|-----------|---------|
| OUT | 1 | `0x32` | Команда |
| IN | 6 | `[0x80, 0x32, major, minor, patch, build]` | Ответ |

**Форматы:**
- major (байт 2): номер основной версии (0..255)
- minor (байт 3): номер промежуточной версии (0..255)
- patch (байт 4): номер исправления версии (0..255)
- build (байт 5): номер сборки, зарезервировано (обычно 0)

### Пример использования

```python
# Отправить команду
dev.write(ep_out, bytes([0x32]))

# Прочитать ответ (6 байт)
response = dev.read(ep_in, 6, timeout=1000)

# Парсинг
if response[0] == 0x80 and response[1] == 0x32:
    major = response[2]
    minor = response[3]
    patch = response[4]
    build = response[5]
    
    version_str = f"{major}.{minor}.{patch}"
    print(f"Версия прошивки: {version_str} (build {build})")
    
    # Проверка совместимости
    if major == 1:
        print("✓ Версия API v1.x")
    elif major >= 2:
        print("✓ Версия API v2.x и выше")
    else:
        print("✗ Неизвестная версия API")
```

### Примеры ответов

| Ответ HEX | Интерпретация |
|-----------|--------------|
| `80 32 01 02 03 00` | v1.2.3 (build 0) |
| `80 32 01 00 00 00` | v1.0.0 (build 0) |
| `80 32 02 00 00 00` | v2.0.0 (build 0) |

## Комбинированное использование

Запрос обеих команд в одной сессии:

```python
import usb.core
import time

dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
dev.set_interface_altsetting(interface_number=2, alternate_setting=1)

ep_out = 0x03
ep_in = 0x83

# Получить версию
print("Запрашиваю версию...")
dev.write(ep_out, bytes([0x32]))
time.sleep(0.05)
version_resp = dev.read(ep_in, 6)
if version_resp[0] == 0x80:
    major, minor, patch = version_resp[2], version_resp[3], version_resp[4]
    print(f"Версия: {major}.{minor}.{patch}")

# Получить температуру
print("Запрашиваю температуру...")
dev.write(ep_out, bytes([0x31]))
time.sleep(0.05)
temp_resp = dev.read(ep_in, 4)
if temp_resp[0] == 0x80:
    temp_raw = temp_resp[2] | (temp_resp[3] << 8)
    if temp_raw & 0x8000:
        temp_c = temp_raw - 0x10000
    else:
        temp_c = temp_raw
    print(f"Температура: {temp_c}°C")
```

## Обработка ошибок

### Таймаут ответа

```python
try:
    response = dev.read(ep_in, 4, timeout=1000)
except usb.core.USBError as e:
    print(f"Ошибка USB: {e}")
```

### Некорректный ответ

```python
response = dev.read(ep_in, 4, timeout=1000)

if len(response) < 4:
    print("✗ Слишком короткий ответ")
elif response[0] != 0x80:
    print("✗ Неверный статус (ожидается 0x80)")
elif response[1] != 0x31:  # или 0x32 для версии
    print("✗ Неверный echo команды")
else:
    print("✓ Ответ корректен")
```

## Скрипты в HostTools

### get_temp.py - Получить температуру

```bash
python HostTools/get_temp.py
python HostTools/get_temp.py --repeat 10 --delay 0.1
```

### get_version.py - Получить версию

```bash
python HostTools/get_version.py
python HostTools/get_version.py --repeat 5
```

## Интеграция в мониторинг

### Периодический запрос версии

```python
def check_firmware_version():
    """Проверить версию прошивки один раз при подключении."""
    try:
        dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
        if not dev:
            return None
        
        dev.set_interface_altsetting(interface_number=2, alternate_setting=1)
        dev.write(0x03, bytes([0x32]))
        response = dev.read(0x83, 6)
        
        if response[0] == 0x80:
            return f"{response[2]}.{response[3]}.{response[4]}"
    except Exception as e:
        print(f"Error: {e}")
    
    return None

version = check_firmware_version()
print(f"Firmware: {version}")
```

### Периодический мониторинг температуры

```python
def monitor_temperature(duration_s=60, interval_s=5):
    """Отслеживать температуру в течение duration_s секунд."""
    import time
    import statistics
    
    temps = []
    start = time.time()
    
    try:
        dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
        if not dev:
            return
        
        dev.set_interface_altsetting(interface_number=2, alternate_setting=1)
        
        while time.time() - start < duration_s:
            dev.write(0x03, bytes([0x31]))
            response = dev.read(0x83, 4)
            
            if response[0] == 0x80:
                temp_raw = response[2] | (response[3] << 8)
                temp_c = temp_raw if temp_raw < 32768 else temp_raw - 65536
                temps.append(temp_c)
                print(f"T: {temp_c}°C")
            
            time.sleep(interval_s)
    
    except Exception as e:
        print(f"Error: {e}")
    
    if temps:
        print(f"\nСтатистика:")
        print(f"  Min: {min(temps)}°C")
        print(f"  Max: {max(temps)}°C")
        print(f"  Avg: {statistics.mean(temps):.1f}°C")
        if len(temps) > 1:
            print(f"  Stdev: {statistics.stdev(temps):.2f}°C")

# Использование
monitor_temperature(duration_s=60, interval_s=5)
```

## Примечания

- Обе команды могут отправляться в любой момент (независимо от состояния потока данных)
- Время ответа типично < 10ms для обеих команд
- Команды не требуют payload'а, только один байт команды
- Ответы всегда начинаются с 0x80 (RSP_ACK)
- Команды полезны для диагностики, мониторинга и проверки совместимости
