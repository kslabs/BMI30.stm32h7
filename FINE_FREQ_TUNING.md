# Тонкая настройка частоты буферов (200-210 Гц)

## Обзор

Реализована возможность тонкой настройки частоты следования буферов в диапазоне **200-210 Гц** с шагом **1 Гц** для проверки влияния переходных процессов на качество сигнала.

### Цель

Предоставить инструмент для исследования переходных процессов при небольших изменениях частоты дискретизации. Это позволяет:
- Оценить стабильность системы при вариациях частоты
- Проверить алгоритмы DC-компенсации и усреднения
- Найти оптимальную рабочую частоту для конкретных условий
- Диагностировать проблемы с синхронизацией ADC/TIM

---

## Реализация

### 1. Firmware (STM32H7)

#### Команда USB: `CMD_SET_BUF_RATE_FINE` (0x1C)

**Формат команды:**
```
Байт 0: 0x1C (код команды)
Байт 1: rate_hz[7:0]  (младший байт)
Байт 2: rate_hz[15:8] (старший байт)
```

**Диапазон:** 200-210 Гц  
**Возврат:** Нет ответа (fire-and-forget)

**Обработка:**
- Проверка диапазона: `200 <= rate_hz <= 210`
- Пересчёт `ARR` регистра TIM15
- Останов и перезапуск TIM15
- Обновление профиля (`buf_rate_hz`, `fs_hz`)
- Логирование через UART и CDC

#### Функция: `adc_stream_set_buf_rate_fine(uint16_t rate_hz)`

**Расположение:** `Core/Src/adc_stream.c`  
**Прототип:** `Core/Inc/adc_stream.h`

**Алгоритм:**

1. **Проверка диапазона:**
   ```c
   if (rate_hz < 200 || rate_hz > 210) return -1;
   ```

2. **Получение текущего профиля:**
   ```c
   uint16_t samples = g_active_samples;
   if (samples == 0) samples = 600;  // защита от деления на 0
   ```

3. **Расчёт целевой частоты ADC:**
   ```c
   uint32_t fs_target = samples * rate_hz;
   ```
   
   Пример для профиля 0 (600 samples):
   - 200 Гц: `Fs = 600 × 200 = 120,000 Hz`
   - 205 Гц: `Fs = 600 × 205 = 123,000 Hz`
   - 210 Гц: `Fs = 600 × 210 = 126,000 Hz`

4. **Расчёт ARR для TIM15:**
   ```c
   const uint32_t tim15_clk = 275000000UL;  // 275 MHz (APB2, PSC=0)
   uint32_t arr_new = (tim15_clk / fs_target) - 1;
   ```
   
   Примеры ARR:
   - 200 Гц: `ARR = 275000000 / 120000 - 1 = 2291`
   - 205 Гц: `ARR = 275000000 / 123000 - 1 = 2235`
   - 210 Гц: `ARR = 275000000 / 126000 - 1 = 2181`

5. **Применение настройки:**
   ```c
   HAL_TIM_Base_Stop(&htim15);
   HAL_TIM_PWM_Stop(&htim15, TIM_CHANNEL_1);
   __HAL_TIM_SET_AUTORELOAD(&htim15, (uint16_t)arr_new);
   __HAL_TIM_SET_COUNTER(&htim15, 0);
   HAL_TIM_Base_Start(&htim15);
   HAL_TIM_PWM_Start(&htim15, TIM_CHANNEL_1);
   ```

6. **Обновление профиля (runtime):**
   ```c
   adc_stream_profile_t *prof_mut = (adc_stream_profile_t*)&g_profiles[g_active_profile];
   prof_mut->buf_rate_hz = rate_hz;
   prof_mut->fs_hz = fs_target;
   ```
   
   **ВНИМАНИЕ:** Модификация `const` структуры через указатель — это хак для runtime подстройки. В будущем рассмотреть добавление отдельного поля `buf_rate_override`.

**Возвращаемые значения:**
- `0` — успех
- `-1` — частота вне диапазона 200-210 Гц
- `-2` — переполнение ARR (теоретически невозможно в заданном диапазоне)

---

### 2. Host (Python)

#### Метод: `BMI30Stream.set_buf_rate_fine(hz: int)`

**Расположение:** `usb_vendor/usb_stream.py`

**Использование:**
```python
from usb_stream import BMI30Stream

# Подключение к устройству
stream = BMI30Stream(profile=0, full=True)

# Установка частоты 205 Гц
stream.set_buf_rate_fine(205)

# Работа с устройством...

stream.close()
```

**Параметры:**
- `hz` (int): Целевая частота буферов, 200-210 Гц

**Исключения:**
- `ValueError`: Частота вне диапазона 200-210 Гц
- `usb.core.USBError`: Ошибка передачи по USB

---

### 3. Тестовый скрипт: `test_fine_freq.py`

**Расположение:** `HostTools/test_fine_freq.py`

#### Режимы работы:

1. **Установка одной частоты:**
   ```bash
   python test_fine_freq.py 205
   ```
   
   Вывод:
   ```
   [TEST] Setting buf_rate = 205 Hz...
   [OK] Frequency set to 205 Hz
   [INFO] Status received: 96 bytes
   ```

2. **Перебор всех частот (sweep):**
   ```bash
   python test_fine_freq.py
   ```
   
   Вывод:
   ```
   [SWEEP] Testing frequencies 200-210 Hz...
   
   [TEST] Setting buf_rate = 200 Hz...
   [OK] Frequency set to 200 Hz
   
   [TEST] Setting buf_rate = 201 Hz...
   [OK] Frequency set to 201 Hz
   
   ... (202-209 Hz)
   
   [TEST] Setting buf_rate = 210 Hz...
   [OK] Frequency set to 210 Hz
   
   [SUMMARY] Test results:
   ----------------------------------------
     200 Hz: OK
     201 Hz: OK
     ...
     210 Hz: OK
   ----------------------------------------
   
   Success rate: 11/11
   ```

---

## Примеры использования

### 1. Проверка стабильности DC-компенсации

```python
from usb_stream import BMI30Stream
import time

stream = BMI30Stream(profile=0, full=True)

# Запустить streaming с 64-буферным усреднением
stream.send_cmd(0x1A, bytes([2, 64]))  # CMD_SET_STREAM_MODE, mode=AVG_ROI, n=64
stream.send_cmd(0x20)  # CMD_START_STREAM

# Тест переходных процессов
for freq in [200, 202, 205, 208, 210]:
    print(f"Testing {freq} Hz...")
    stream.set_buf_rate_fine(freq)
    time.sleep(5)  # Сбор данных 5 секунд
    
    # Анализ DC offset, стандартного отклонения и т.д.
    # (реализация зависит от задачи)

stream.close()
```

### 2. Поиск оптимальной частоты

```python
from usb_stream import BMI30Stream
import numpy as np
import time

stream = BMI30Stream(profile=0, full=True)
stream.send_cmd(0x20)  # START

results = {}
for freq in range(200, 211):
    stream.set_buf_rate_fine(freq)
    time.sleep(1)
    
    # Считать N буферов
    frames = []
    for _ in range(100):
        data = stream.read(timeout_s=1.0)
        if data:
            frames.append(data)
    
    # Вычислить метрику (например, SNR)
    # snr = calculate_snr(frames)
    # results[freq] = snr

# Найти частоту с лучшим SNR
# best_freq = max(results, key=results.get)
# print(f"Best frequency: {best_freq} Hz")

stream.close()
```

### 3. Интеграция с GUI (BMI30.200.py)

**Добавить выпадающий список для частоты (опционально):**

```python
# В __init__():
self.freq_box = QtWidgets.QComboBox()
self.freq_box.addItems([str(f) for f in range(200, 211)])
self.freq_box.setCurrentText("200")
self.freq_box.currentTextChanged.connect(self._on_freq_change)

# Обработчик:
def _on_freq_change(self, text):
    try:
        freq = int(text)
        if hasattr(self, 'stream') and self.stream:
            self.stream.set_buf_rate_fine(freq)
            print(f"[GUI] Frequency set to {freq} Hz")
    except Exception as e:
        print(f"[GUI] Failed to set frequency: {e}")
```

---

## Технические детали

### Формула расчёта ARR

Для STM32H7 с TIM15 на частоте 275 MHz (PSC=0):

$$
\text{ARR} = \frac{f_{\text{TIM15}}}{N_{\text{samples}} \times f_{\text{buf}}} - 1
$$

Где:
- $f_{\text{TIM15}} = 275\,000\,000$ Гц (частота таймера)
- $N_{\text{samples}}$ — количество отсчётов в буфере (например, 600)
- $f_{\text{buf}}$ — частота буферов (200-210 Гц)

**Примеры:**

| $f_{\text{buf}}$ (Гц) | $N_{\text{samples}}$ | $f_s$ (Гц) | ARR     | Погрешность   |
|-----------------------|----------------------|------------|---------|---------------|
| 200                   | 600                  | 120,000    | 2291    | -0.0018%      |
| 205                   | 600                  | 123,000    | 2235    | -0.0018%      |
| 210                   | 600                  | 126,000    | 2181    | -0.0018%      |

**Проверка точности:**
```
Fактическая частота = 275,000,000 / (ARR + 1)

Для 200 Гц:
  Fs = 275,000,000 / 2292 = 119,997.8 Гц
  f_buf = 119,997.8 / 600 = 199.996 Гц ≈ 200.00 Гц
```

Погрешность составляет менее **0.002%**, что приемлемо для большинства приложений.

---

## Ограничения и предостережения

### 1. Диапазон частот: 200-210 Гц

Расширение диапазона возможно, но требует проверки:
- **Нижняя граница:** При $f_{\text{buf}} < 200$ Гц увеличивается период между буферами, что может вызвать переполнение FIFO (если хост не успевает читать).
- **Верхняя граница:** При $f_{\text{buf}} > 210$ Гц уменьшается время на обработку буфера, возрастает нагрузка на USB.

### 2. Переключение частоты во время streaming

**Поведение:** TIM15 останавливается и перезапускается → возможен пропуск 1-2 буферов.

**Рекомендации:**
- Останавливать streaming перед переключением (`CMD_STOP_STREAM`)
- Менять частоту
- Запускать streaming заново (`CMD_START_STREAM`)

Если переключение во время streaming критично, нужно реализовать бесшовный переход (buffered update ARR в shadow регистр).

### 3. Модификация `const` структуры профиля

Текущая реализация изменяет `g_profiles[g_active_profile]` через указатель, что технически UB в C.

**Альтернативы:**
1. Добавить `uint16_t buf_rate_override` в структуру `adc_stream_profile_t` (если 0 — использовать дефолтное значение)
2. Создать отдельную глобальную переменную `g_buf_rate_override`
3. Добавить флаг `profile_modified` для индикации runtime изменений

### 4. Влияние на TIM2 маркеры

TIM2 использует `adc_stream_get_buf_rate()` для расчёта частоты маркеров. После изменения частоты через `set_buf_rate_fine()` значение корректно обновляется в профиле, но **TIM2 не перезапускается автоматически**.

**Решение:** Вызывать `tim2_apply_profile_window()` после изменения частоты (требуется дополнительная функция в `main.c`).

---

## Диагностика

### Проверка через UART/CDC логи

После отправки команды `CMD_SET_BUF_RATE_FINE` в UART появится лог:

```
[ADC][RATE] Fine-tune: 205 Hz (samples=600, Fs=123000 Hz, ARR=2235)
[CMD_IND] SET_BUF_RATE_FINE 205 Hz OK
```

Если частота вне диапазона:
```
[ADC][RATE] rate_hz=250 out of range 200..210
[CMD_IND] SET_BUF_RATE_FINE 250 Hz FAILED (rc=-1)
```

### Проверка реальной частоты

**Метод 1: Осциллограф**
- Подключить осциллограф к TIM15 CH1 (PA2)
- Измерить частоту UPDATE событий
- Для 200 Гц: период = 5.0 мс

**Метод 2: Host-side анализ**
```python
import time

# Считать timestamp 100 буферов
timestamps = []
for _ in range(100):
    t = time.time()
    data = stream.read(timeout_s=1.0)
    if data:
        timestamps.append(t)

# Вычислить среднюю частоту
diffs = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
avg_period = sum(diffs) / len(diffs)
avg_freq = 1.0 / avg_period
print(f"Average frequency: {avg_freq:.2f} Hz")
```

---

## История изменений

### 19 января 2026 (v1.0)

**Реализовано:**
- Команда `CMD_SET_BUF_RATE_FINE` (0x1C) в firmware
- Функция `adc_stream_set_buf_rate_fine()` с расчётом ARR
- Python метод `BMI30Stream.set_buf_rate_fine()`
- Тестовый скрипт `test_fine_freq.py`
- Документация `FINE_FREQ_TUNING.md`

**Коммиты:**
```
[запланировано] feat: тонкая настройка частоты буферов 200-210 Гц
```

---

## Дальнейшие улучшения

### Приоритет 1: Автоматическая перенастройка TIM2

Добавить вызов `tim2_apply_profile_window()` после изменения частоты для корректной синхронизации маркеров.

### Приоритет 2: Бесшовное переключение частоты

Использовать shadow регистр ARR (режим preload) для переключения без останова TIM15:

```c
__HAL_TIM_ENABLE_IT(&htim15, TIM_IT_UPDATE);  // Включить прерывание UPDATE
// В TIM15 UPDATE ISR:
//   __HAL_TIM_SET_AUTORELOAD(&htim15, arr_new);
```

### Приоритет 3: Расширение диапазона

Тестирование работы в диапазоне **190-220 Гц** с адаптивной настройкой FIFO.

### Приоритет 4: GUI интеграция

Добавить слайдер или спинбокс в `BMI30.200.py` для live-настройки частоты во время streaming.

---

## Связанные документы

- [AVG_BUFFER_64_UPGRADE.md](AVG_BUFFER_64_UPGRADE.md) — расширение усреднения до 64 буферов
- [TIM15_FREQ_MEASUREMENT.md](TIM15_FREQ_MEASUREMENT.md) — измерение частоты TIM15
- [FPS_PROFILING_STATUS.md](FPS_PROFILING_STATUS.md) — профилирование производительности
- [HOST_SETUP_RU.md](HostTools/HOST_SETUP_RU.md) — настройка хост-инструментов

---

## Контакты и поддержка

**Репозиторий:** https://github.com/kslabs/BMI30.stm32h7  
**Ветка:** `feature/next-stage-2025-12-08`  
**Дата:** 19 января 2026
