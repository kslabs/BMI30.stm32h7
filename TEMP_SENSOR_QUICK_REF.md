## Температурный датчик STM32H723 — Краткая справка

**Статус**: ✅ Реализовано и протестировано  
**Размер прошивки**: 2721.2 KB  
**Дата**: 2026-02-14 18:18 UTC

---

### 🚀 Быстрый старт

#### Код микропроцессора:
```c
#include "temp_sensor.h"

// При инициализации (main):
temp_sensor_init();

// Читать температуру (блокирует на ~500 мкс):
int16_t temp_c = temp_sensor_read_celsius();
printf("Температура: %d°C\r\n", temp_c);
```

#### С хоста (Python):
```python
# Отправить запрос GET_TEMP на USB Vendor Interface #2
dev.write(0x03, bytes([0x31]))

# Получить ответ [0x80, 0x31, temp_lo, temp_hi]
response = dev.read(0x83, 4)
temp = int.from_bytes(response[2:4], 'little', signed=True)
print(f"Температура: {temp}°C")
```

---

### 📡 USB протокол

| Направление | Данные | Значение |
|---|---|---|
| **Host → Device** | `[0x31]` | Запрос температуры (GET_TEMP) |
| **Device → Host** | `[0x80, 0x31, temp_lo, temp_hi]` | Ответ с температурой (signed int16_t) |

---

### 🔧 Аппаратная конфигурация

- **Датчик**: DTS (Digital Temperature Sensor) встроенный в STM32H723
- **ADC**: ADC3 (канал TEMPSENSOR, INP17)
- **Разрешение**: 12-бит
- **Время конверсии**: ~500 микросекунд
- **Диапазон**: -40°C до +85°C
- **Точность**: ±5°C

---

### 📊 Калибровочные константы

Автоматически загружаются из FLASH памяти при инициализации:
- **TS_CAL1** @ 0x08FFF814 = ADC значение при 30°C
- **TS_CAL2** @ 0x08FFF818 = ADC значение при 130°C

Формула преобразования (линейная интерполяция):
```
T = 30 + ((ADC_raw - TS_CAL1) * 100) / (TS_CAL2 - TS_CAL1)
```

---

### 📁 Файлы реализации

```
Core/
├── Inc/
│   └── temp_sensor.h         ← Прототипы функций
└── Src/
    ├── temp_sensor.c         ← Реализация датчика
    ├── main.c                ← MX_ADC3_Init() инициализация
    └── usb_cdc_proto.c       ← Команда GET_TEMP (0x31)

HostTools/
├── get_temp.py              ← Python утилита для тестирования
└── TEMP_SENSOR_GUIDE.md     ← Подробное руководство
```

---

### 🧪 Тестирование

#### С микропроцессора:
```c
// Печать диагностики
temp_sensor_print_diagnostic();

// Вывод:
// [DTS] Diagnostic: CAL1=1350 CAL2=750 RAW=1200 T=42°C
```

#### С хоста:
```bash
python3 HostTools/get_temp.py
```

---

### ⚙️ Функции API

```c
// Инициализация (загружает калибровочные константы)
void temp_sensor_init(void);

// Прочитать сырое значение ADC (0..4095)
uint16_t temp_sensor_read_raw(void);

// Прочитать температуру в градусах Цельсия (-40..+85)
int16_t temp_sensor_read_celsius(void);

// Проверить готовность датчика (1 = готов, 0 = не инициализирован)
uint8_t temp_sensor_is_ready(void);

// Вывести диагностическую информацию
void temp_sensor_print_diagnostic(void);
```

---

### ⚠️ Важно

1. **Блокирующая операция**: Чтение занимает ~500 мкс (одиночная ADC конверсия)
2. **Не вызывайте из ISR**: Используйте в фоновых задачах или низкоприоритетных режимах
3. **Точность**: ±5°C типично; для критичных приложений используйте внешний датчик
4. **Диапазон**: Работает в -40°C до +85°C; за пределами — результаты неточны
5. **Минимальный приоритет**: Не нарушает основной стриминг данных

---

### 📝 Логирование при boot

```
[DTS] Calibration: CAL1(30°C)=1350 CAL2(130°C)=750
[ADC3][DTS] Initialized: Resolution=12b, Channel=TEMPSENSOR, SamplingTime=387.5 cycles
```

---

**Полное руководство**: см. [TEMP_SENSOR_README.md](TEMP_SENSOR_README.md)  
**Примеры кода**: см. [HostTools/get_temp.py](HostTools/get_temp.py)  
**Протокол USB**: см. [USBprotocol.txt](USBprotocol.txt)

---

Версия: 1.0 | Дата: 2026-02-14 | Статус: ✅ Протестировано
