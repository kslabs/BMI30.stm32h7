# Команда управления DC-адаптацией

## CMD_SET_DC_ADAPT (0x1B)

### Назначение
Управление адаптивной коррекцией DC-смещения в реальном времени. Позволяет заморозить обучение DC при детекции целевого сигнала, сохранив вычитание фонового уровня.

### Формат команды
- **Код:** `0x1B`
- **Payload:** 1 байт
  - `0x00` - **FREEZE**: прекратить обновление DC массивов (заморозить обучение)
  - `0x01` - **ACTIVE**: возобновить адаптивное обучение DC offset

### Логика работы

#### FREEZE (0x00)
- DC-адаптация отключается (`vnd_dc_adapt_enabled = 0`)
- Функция `_update_dc_offset_adaptive()` не вызывается
- **Вычитание DC продолжает работать** с текущими замороженными значениями:
  - `dc_offset_ch0_even`, `dc_offset_ch0_odd`
  - `dc_offset_ch1_even`, `dc_offset_ch1_odd`
- Массивы не изменяются и не сохраняются в файл

#### ACTIVE (0x01)
- DC-адаптация включается (`vnd_dc_adapt_enabled = 1`)
- Возобновляются вызовы `_update_dc_offset_adaptive()`
- Периодическое сохранение в файл возобновляется

### Состояние по умолчанию
**ACTIVE** (адаптация включена) - для обратной совместимости.

### Пример использования на хосте (Python)

```python
from usb_vendor.usb_stream import USBStream, CMD_SET_DC_ADAPT

stream = USBStream()

# При детекции целевого сигнала - заморозить адаптацию
if signal_detected:
    stream.send_cmd(CMD_SET_DC_ADAPT, b'\x00')  # FREEZE
    print("[DC] Адаптация заморожена - сигнал обнаружен")

# Когда сигнал пропал - возобновить адаптацию
if not signal_detected:
    stream.send_cmd(CMD_SET_DC_ADAPT, b'\x01')  # ACTIVE
    print("[DC] Адаптация возобновлена")
```

### Интеграция в BMI30.200.py

В коде хоста добавлен флаг `dc_adapt_enabled`, который проверяется перед вызовом адаптации:

```python
# Проверка флага перед адаптацией (для обоих каналов)
if getattr(self, 'dc_adapt_enabled', True) and \
   int(getattr(self, 'stream_mode', 0) or 0) in set(getattr(self, 'dc_adapt_modes', {1, 2})) and \
   len(ch0) > 0:
    # Выполнить адаптацию
    self._update_dc_offset_adaptive(ch0, dc_offset_array, len(ch0))
```

### Firmware изменения

**Файл:** `USB_DEVICE/App/usb_vendor_app.c`

1. **Добавлена константа:**
```c
#define VND_CMD_SET_DC_ADAPT 0x1Bu
```

2. **Добавлена глобальная переменная:**
```c
volatile uint8_t vnd_dc_adapt_enabled = 1; /* 1=active, 0=freeze */
```

3. **Добавлен extern в заголовок** `USB_DEVICE/App/usb_vendor_app.h`:
```c
extern volatile uint8_t vnd_dc_adapt_enabled;
```

4. **Добавлен обработчик команды:**
```c
case VND_CMD_SET_DC_ADAPT:
    if(len >= 2) {
        uint8_t enable = data[1];
        vnd_dc_adapt_enabled = (enable != 0) ? 1 : 0;
        printf("[CMD_IND] SET_DC_ADAPT %s\r\n", 
               vnd_dc_adapt_enabled ? "ACTIVE" : "FREEZE");
    }
    break;
```

5. **Добавлена проверка в функции адаптации:**
```c
static void vnd_dc_apply_and_adapt(...) {
    // ... применение DC (всегда) ...
    
    if(!gate_enabled) return;
    
    // Проверка флага от хоста
    if(!vnd_dc_adapt_enabled) {
        return; /* адаптация заморожена хостом */
    }
    
    // ... остальная логика обучения DC ...
}
```

6. **Добавлена LCD индикация** в `Core/Src/main.c`:
```c
/* Если адаптация заморожена - используем синий цвет */
if(!vnd_dc_adapt_enabled){
    color = BLUE;
} else if(vnd_dc_save_last_result == 2u){
    color = RED;
} else if(vnd_dc_save_last_result == 1u){
    color = GREEN;
} else {
    color = WHITE;
}
```

### Сценарий применения на RPI

```python
import numpy as np
from usb_vendor.usb_stream import USBStream, CMD_SET_DC_ADAPT

stream = USBStream()

while True:
    # Получить данные
    frames = stream.read_frames(timeout=1.0)
    
    # Простая детекция сигнала (пример)
    for frame in frames:
        samples = np.frombuffer(frame.payload, dtype=np.uint16)
        signal_amplitude = samples.max() - samples.min()
        
        # Если амплитуда превышает порог - это целевой сигнал
        if signal_amplitude > SIGNAL_THRESHOLD:
            # Заморозить DC чтобы не искажать сигнал
            stream.send_cmd(CMD_SET_DC_ADAPT, b'\x00')
        else:
            # Продолжить обучение фона
            stream.send_cmd(CMD_SET_DC_ADAPT, b'\x01')
```

### Преимущества
- Предотвращает искажение DC-калибровки при наличии целевого сигнала
- Сохраняет текущую DC-коррекцию для вычитания фона
- Простой протокол (1 байт команды)
- Быстрое переключение без перезапуска потока
- Не требует остановки стриминга
- **Визуальная индикация на LCD экране** - синяя полоса при заморозке

### LCD Индикация

На LCD экране в нижней части (y=79) отображается полоса прогресса DC-адаптации:

**Цвета полосы:**
- **БЕЛЫЙ** (WHITE) - идет нормальная адаптация, ожидается запись
- **СИНИЙ** (BLUE) - **адаптация заморожена** (FREEZE) по команде хоста
- **ЗЕЛЕНЫЙ** (GREEN) - последняя запись DC прошла успешно
- **КРАСНЫЙ** (RED) - последняя запись DC завершилась с ошибкой

**Поведение:**
1. При отправке `CMD_SET_DC_ADAPT 0x00` (FREEZE):
   - Полоса становится **синей**
   - Длина полосы не изменяется
   - DC массивы не обновляются, но вычитание продолжает работать

2. При отправке `CMD_SET_DC_ADAPT 0x01` (ACTIVE):
   - Полоса возвращает свой предыдущий цвет (WHITE/GREEN/RED)
   - Адаптация возобновляется
   - Полоса продолжает заполняться до момента записи

Это позволяет оператору визуально контролировать состояние DC-адаптации прямо на устройстве.

### Совместимость
- Хост: добавлена константа `CMD_SET_DC_ADAPT` в `usb_vendor/usb_stream.py`
- Хост: импорт в `BMI30.200.py` с fallback
- Firmware: обработчик в `USB_DEVICE/App/usb_vendor_app.c`
- Firmware: LCD индикация в `Core/Src/main.c` (синяя полоса при заморозке)
- Версия прошивки: собрана 13.01.2026 13:34:07

### Тестирование
Запустите тестовый скрипт:
```bash
python HostTools/test_dc_adapt.py
```

Скрипт выполнит:
1. Подключение к устройству
2. Заморозку адаптации на 5 секунд
3. Симуляцию 3 циклов детекции/потери сигнала
4. Вывод примера интеграции в код детекции
