# Быстрый старт: Тонкая настройка частоты буферов

## Что это?

Новая возможность установки частоты следования буферов в диапазоне **200-210 Гц** с шагом **1 Гц**.  
Используется для проверки влияния переходных процессов на качество сигнала.

---

## Быстрое использование

### 1. Сборка и прошивка firmware

```bash
# Сборка
cd Debug
make all

# Прошивка (OpenOCD)
openocd -s ../scripts -f interface/stlink.cfg -f target/stm32h7x.cfg \
  -c "program {BMI30.stm32h7.elf} verify reset exit"
```

### 2. Тестирование с Python

```python
from usb_stream import BMI30Stream

# Подключение
stream = BMI30Stream(profile=0, full=True)

# Установка частоты 205 Гц
stream.set_buf_rate_fine(205)

# Проверка (опционально)
import time
time.sleep(1)  # Даём время на стабилизацию

# Работа с устройством...
stream.close()
```

### 3. Автоматический тест всех частот

```bash
cd HostTools
python test_fine_freq.py
```

Вывод:
```
[SWEEP] Testing frequencies 200-210 Hz...
  200 Hz: OK
  201 Hz: OK
  ...
  210 Hz: OK

Success rate: 11/11
```

---

## Технические детали

**Команда USB:** `0x1C` (CMD_SET_BUF_RATE_FINE)  
**Payload:** 2 байта uint16_t (little-endian)  
**Диапазон:** 200-210 Гц  
**Принцип работы:** Перенастройка ARR регистра TIM15

**Формула:**
```
ARR = (275,000,000 / (samples × freq_hz)) - 1
```

**Примеры ARR для профиля 0 (600 samples):**
- 200 Гц → ARR = 2291
- 205 Гц → ARR = 2235
- 210 Гц → ARR = 2181

---

## Файлы изменений

**Firmware:**
- `USB_DEVICE/App/usb_vendor_app.c` — обработчик команды CMD_SET_BUF_RATE_FINE
- `Core/Src/adc_stream.c` — функция `adc_stream_set_buf_rate_fine()`
- `Core/Inc/adc_stream.h` — прототип функции

**Host:**
- `usb_vendor/usb_stream.py` — метод `set_buf_rate_fine()`
- `HostTools/test_fine_freq.py` — тестовый скрипт

**Документация:**
- `FINE_FREQ_TUNING.md` — подробная документация
- `FINE_FREQ_QUICKSTART.md` — этот файл

---

## Примеры сценариев

### Проверка стабильности при смене частоты

```python
from usb_stream import BMI30Stream
import time

stream = BMI30Stream(profile=0, full=True)
stream.send_cmd(0x20)  # START_STREAM

for freq in [200, 205, 210, 205, 200]:
    print(f"Setting {freq} Hz...")
    stream.set_buf_rate_fine(freq)
    time.sleep(3)  # Сбор данных 3 секунды

stream.close()
```

### Поиск оптимальной частоты

```python
from usb_stream import BMI30Stream
import numpy as np

stream = BMI30Stream(profile=0, full=True)
stream.send_cmd(0x20)

best_freq = None
min_noise = float('inf')

for freq in range(200, 211):
    stream.set_buf_rate_fine(freq)
    time.sleep(0.5)
    
    # Считать 50 буферов
    noise_levels = []
    for _ in range(50):
        data = stream.read(timeout_s=1.0)
        if data:
            # Вычислить шум (std deviation)
            noise = np.std(data)
            noise_levels.append(noise)
    
    avg_noise = np.mean(noise_levels)
    print(f"{freq} Hz: noise = {avg_noise:.2f}")
    
    if avg_noise < min_noise:
        min_noise = avg_noise
        best_freq = freq

print(f"\nBest frequency: {best_freq} Hz (noise = {min_noise:.2f})")
stream.close()
```

---

## Диагностика

### Проверка через UART логи

После команды в UART должно появиться:
```
[ADC][RATE] Fine-tune: 205 Hz (samples=600, Fs=123000 Hz, ARR=2235)
[CMD_IND] SET_BUF_RATE_FINE 205 Hz OK
```

### Проверка реальной частоты (host-side)

```python
import time

timestamps = []
for _ in range(100):
    t = time.time()
    data = stream.read(timeout_s=1.0)
    if data:
        timestamps.append(t)

diffs = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
avg_freq = 1.0 / (sum(diffs) / len(diffs))
print(f"Measured frequency: {avg_freq:.2f} Hz")
```

---

## Ограничения

1. **Диапазон:** 200-210 Гц (жёсткая проверка в firmware)
2. **Переключение во время streaming:** Возможен пропуск 1-2 буферов при смене частоты
3. **TIM2 маркеры:** Не обновляются автоматически после изменения частоты

**Рекомендация:** Останавливайте streaming перед сменой частоты.

---

## Дополнительная информация

Полная документация: [FINE_FREQ_TUNING.md](FINE_FREQ_TUNING.md)

**Дата:** 19 января 2026  
**Версия:** 1.0
