## Датчик температуры кристалла STM32H723

**Дата**: 2026-02-14 18:18 UTC
**Прошивка**: BMI30.stm32h7 
**Размер**: 2721.2 KB

---

## 📋 Обзор

STM32H723 содержит встроенный цифровой датчик температуры (DTS - Digital Temperature Sensor), который подключен к ADC3 на входе INP17. Датчик позволяет снимать температуру кристалла микропроцессора с точностью ±5°C.

### Характеристики DTS:
- **Диапазон**: -40°C до +85°C
- **Точность**: ±5°C типично
- **Разрешение**: 0.5°C
- **Калибровка**: TS_CAL1 @ 30°C (FLASH: 0x08FFF814) и TS_CAL2 @ 130°C (FLASH: 0x08FFF818)

---

## 🔧 Аппаратная конфигурация

### ADC3 инициализация:
```c
// Осуществляется в main.c
MX_ADC3_Init();  // Инициализирует ADC3 для DTS
temp_sensor_init();  // Загружает калибровочные константы из FLASH
```

**Параметры ADC3:**
- **Канал**: ADC_CHANNEL_TEMPSENSOR (DTS на ADC3_INP17)
- **Разрешение**: 12-бит
- **Время выборки**: 387.5 циклов (для стабильности)
- **Режим**: Одиночная конверсия по программному запросу (Software Start)
- **Время конверсии**: ~500 микросекунд

---

## 📡 USB протокол для запроса температуры

### Команда: GET_TEMP (0x31)

**Запрос (Host → Device):**
```
[0x03] [0x31]
```

**Ответ (Device → Host):**
```
[0x80] [0x31] [temp_lo] [temp_hi]
```

Где:
- `0x80` — флаг ответа
- `0x31` — код команды GET_TEMP
- `temp_lo, temp_hi` — температура как signed 16-бит в little-endian формате (в градусах Цельсия)

**Пример:**
- Температура +25°C: `[0x80] [0x31] [0x19] [0x00]` (0x0019 = 25)
- Температура -10°C: `[0x80] [0x31] [0xF6] [0xFF]` (0xFFF6 = -10 в signed format)

---

## 💻 Использование в коде

### Инициализация:
```c
#include "temp_sensor.h"

// В main():
temp_sensor_init();  // Загружает калибровочные значения из FLASH
```

### Чтение температуры:
```c
// Простой способ (рекомендуется):
int16_t temp_c = temp_sensor_read_celsius();  // Блокирует на ~500 мкс
if (temp_c > 60) {
    printf("[TEMP] Кристалл горячий: %d°C\r\n", temp_c);
}

// Диагностика:
if (temp_sensor_is_ready()) {
    printf("[TEMP] Датчик готов\r\n");
    temp_sensor_print_diagnostic();  // Печатает калибровочные данные и сырое ADC значение
}

// Сырое значение (если нужно):
uint16_t raw = temp_sensor_read_raw();  // Возвращает 12-бит ADC значение
```

---

## 🖥️ Использование с хост-компьютером

### Python пример:

```python
import usb.core
import usb.util

# Найти устройство
VID, PID = 0xCAFE, 0x4001
dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    raise ValueError(f"Device {VID:04X}:{PID:04X} not found")

# Запросить температуру (используя Vendor Interface #2)
OUT_EP = 0x03  # Endpoint OUT
IN_EP = 0x83   # Endpoint IN

# Отправить команду GET_TEMP (0x31)
request = bytes([0x31])
dev.write(OUT_EP, request)

# Получить ответ
response = dev.read(IN_EP, 4, timeout=1000)
resp_hdr = response[0]      # 0x80 (ответ)
resp_cmd = response[1]      # 0x31 (команда)
temp_lo = response[2]
temp_hi = response[3]

# Распаковать signed 16-бит температуру (little-endian)
import struct
temp_raw = struct.unpack('<h', bytes([temp_lo, temp_hi]))[0]
print(f"Температура кристалла: {temp_raw}°C")
```

### C# пример:

```csharp
using LibUsbDotNet;
using LibUsbDotNet.Main;

// Найти устройство
UsbDeviceFinder deviceFinder = new UsbDeviceFinder(0xCAFE, 0x4001);
IUsbDevice device = UsbDevice.OpenUsbDevice(deviceFinder);

if (device == null) {
    Console.WriteLine("Device not found");
    return;
}

// Получить interfaces
device.ClaimInterface(2);  // Vendor Interface #2

// Отправить команду GET_TEMP
byte[] request = new byte[] { 0x31 };
device.ControlTransfer(ref UsbSetupPacket, request, request.Length, out int transferred);

// Получить ответ (обычно через bulk IN)
byte[] response = new byte[4];
device.BulkTransfer(0x83, response, response.Length, out transferred);

// Распаковать температуру
int temp = (sbyte)response[3] << 8 | response[2];
Console.WriteLine($"Температура: {temp}°C");
```

---

## 📊 Калибровочные значения

Калибровочные константы хранятся в FLASH памяти и автоматически загружаются при инициализации:

| Параметр | Адрес FLASH | Значение | Предназначение |
|----------|-----------|---------|-------------|
| TS_CAL1 | 0x08FFF814 | ~1300 (типично) | ADC значение при 30°C |
| TS_CAL2 | 0x08FFF818 | ~700 (типично) | ADC значение при 130°C |

Эти значения используются для линейной интерполяции:
```
T(°C) = 30 + ((ADC_raw - TS_CAL1) * 100) / (TS_CAL2 - TS_CAL1)
```

**Пример калибровки:**
```
[DTS] Calibration: CAL1(30°C)=1350 CAL2(130°C)=750
```

---

## ⚠️ Важные замечания

1. **Блокирующая операция**: `temp_sensor_read_celsius()` занимает ~500 микросекунд (одиночная ADC конверсия).

2. **Использование в ISR**: Не вызывайте температурный датчик из прерываний с высоким приоритетом (может нарушить синхронизацию).

3. **Точность**: DTS имеет точность ±5°C. Для критичных приложений используйте внешний датчик (например, LM75).

4. **Диапазон**: За пределами -40°C и +85°C результаты могут быть неточными.

5. **Минимальный приоритет**: Инициализация ADC3 и операции чтения выполняются с минимальным приоритетом, не нарушая основной стриминг данных от BMI30.

---

## 🧪 Тестирование

Для быстрого тестирования используйте Python скрипт из `HostTools/get_temp.py`:

```bash
python3 get_temp.py
```

Скрипт:
1. Найдёт подключённое устройство (VID=0xCAFE, PID=0x4001)
2. Отправит команду GET_TEMP
3. Покажет температуру в °C

---

## 📝 Логирование

**Инициализация (при boot):**
```
[DTS] Calibration: CAL1(30°C)=1350 CAL2(130°C)=750
[ADC3][DTS] Initialized: Resolution=12b, Channel=TEMPSENSOR, SamplingTime=387.5 cycles
```

**Диагностика:**
```
[DTS] Diagnostic: CAL1=1350 CAL2=750 RAW=1200 T=42°C
```

**Ошибки:**
```
[DTS] ERROR: ADC start failed
[DTS] ERROR: conversion timeout
[DTS] ERROR: invalid calibration (CAL1=1200 >= CAL2=750)
```

---

## 🔗 Файлы

| Файл | Назначение |
|------|-----------|
| `Core/Inc/temp_sensor.h` | Заголовок с прототипами функций |
| `Core/Src/temp_sensor.c` | Реализация датчика температуры |
| `Core/Src/main.c` | Инициализация ADC3 в `MX_ADC3_Init()` |
| `HostTools/get_temp.py` | Python утилита для тестирования |
| `HostTools/TEMP_SENSOR_GUIDE.md` | Подробное руководство на английском |

---

**Версия документа**: 1.0
**Последнее обновление**: 2026-02-14
**Статус**: ✅ Протестировано на аппаратуре STM32H723VGT
