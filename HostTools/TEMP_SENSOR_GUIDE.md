# GET_TEMP - Получение температуры кристалла STM32H723

## Описание

Команда `0x31 (CMD_GET_TEMP)` позволяет получить текущую температуру кристалла встроенного датчика температуры (DTS) микроконтроллера STM32H723 через USB Vendor интерфейс.

## Протокол

### Запрос (OUT → устройство на EP 0x03)
- **Формат**: 1 байт
- **Значение**: `0x31` (CMD_GET_TEMP)
- **Payload**: нет
- **Пример**: `[0x31]`

### Ответ (IN ← устройство с EP 0x83)
- **Формат**: 4 байта
  - Byte 0: `0x80` (RSP_ACK - квитанция)
  - Byte 1: `0x31` (echo команды)
  - Bytes 2-3: температура в °C (signed int16, little-endian)
  
- **Пример**: 
  - Для +28°C: `[0x80, 0x31, 0x1C, 0x00]` → (0x001C = 28)
  - Для -10°C: `[0x80, 0x31, 0xF6, 0xFF]` → (0xFFF6 = -10 в дополнительном коде)

## Характеристики датчика

| Параметр | Значение |
|----------|----------|
| **Точность** | ±5°C |
| **Разрешение** | 0.5°C |
| **Диапазон** | -40°C до +85°C |
| **Время отклика** | ~1 мс |
| **Калибровка** | Использует встроенные константы TS_CAL1 (30°C) и TS_CAL2 (130°C) |

## Использование

### Python с PyUSB

```python
import usb.core

# Найти устройство
dev = usb.core.find(idVendor=0xCAFE, idProduct=0x4001)
if dev is None:
    raise RuntimeError("Device not found")

# Активировать интерфейс с endpoints
dev.set_interface_altsetting(interface_number=2, alternate_setting=1)

# Отправить команду
cmd = bytes([0x31])
dev.write(0x03, cmd, timeout=1000)

# Получить ответ
response = dev.read(0x83, 4, timeout=1000)

# Распарсить температуру
if response[0] == 0x80 and response[1] == 0x31:
    temp_raw = response[2] | (response[3] << 8)
    # Преобразовать из unsigned в signed int16
    if temp_raw & 0x8000:
        temp_c = temp_raw - 0x10000
    else:
        temp_c = temp_raw
    print(f"Temperature: {temp_c}°C")
```

### Использование готового скрипта

```bash
# Одиночное чтение
python HostTools/get_temp.py

# Чтение 10 раз подряд
python HostTools/get_temp.py --repeat 10

# Чтение каждую секунду в течение 10 секунд
python HostTools/get_temp.py --repeat 10 --interval 1

# С пользовательским таймаутом (2 секунды)
python HostTools/get_temp.py --timeout 2000
```

### Использование в интерактивном режиме

```python
from usb_vendor.usb_stream import USBStream

# Подключиться
s = USBStream()

# Внутренний хелпер (если добавить)
temp = s.get_temperature()  # требует реализации в usb_stream.py
print(f"Temperature: {temp}°C")
```

## Интеграция со streaming

Команда **не прерывает** потокъ данных (streaming). Можно использовать:

- Во время потоковой передачи для мониторинга температуры
- В режиме ожидания
- Между кадрами данных

## Примечания

### Текущее состояние

На данном этапе функция `temp_sensor_read_celsius()` в firmware возвращает **статическое значение ~28°C** (placeholder).

### TODO: Полная реализация

Для полной работы датчика необходимо:

1. **Открыть BMI30.stm32h7.ioc в STM32CubeMX**
2. **Добавить DTS (Digital Temperature Sensor):**
   - Перейти в категорию "Analog" → "Temperature Sensor"
   - Включить DTS (или добавить TEMPSENSOR канал к ADC1)
   - Убедиться, что включен `HAL_DTS_MODULE_ENABLED` в stm32h7xx_hal_conf.h
3. **Сгенерировать код**
4. **Реализовать чтение сырого значения:**
   ```c
   static uint16_t temp_sensor_read_raw(void) {
       // Чтение из DTS через ADC или встроенный DTS модуль
       // Использовать HAL_ADC_GetValue() или HAL_DTS_GetTemperature()
       uint16_t raw = 0;  // получить реальное значение
       return raw;
   }
   ```

### Калибровочные константы

Константы калибровки хранятся в памяти MCU:

| Адрес | Назначение | Значение |
|-------|-----------|---------|
| 0x1FF1E820 | TS_CAL1 (30°C) | Читается из памяти |
| 0x1FF1E824 | TS_CAL2 (130°C) | Читается из памяти |

Пример расчета температуры:

```
T = 30 + (TS_CAL1 - raw) * 100 / (TS_CAL2 - TS_CAL1)

где:
- T - температура в °C
- TS_CAL1, TS_CAL2 - калибровочные константы
- raw - сырое значение с датчика
- 100 - диапазон в °C (130 - 30)
```

## Ограничения

- Не блокирует потоковую передачу данных
- Может иметь задержку из-за другой обработки на устройстве
- Используется только встроенный датчик кристалла (нет внешних датчиков)

## Статус

✅ USB протокол реализован  
✅ Аппаратная поддержка STM32H723  
⏳ Чтение DTS требует конфигурации в CubeMX (текущий placeholder)  
✅ Хост-утилиты готовы  

