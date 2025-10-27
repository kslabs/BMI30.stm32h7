# STM32 ADC Streaming - Диагностика и Решение

## Текущая Ситуация

### Что Работает ✅
- USB устройство успешно enum

ерируется (VID=0xCAFE, PID=0x4001)
- Vendor интерфейс IF#2 с endpoints 0x03/0x83 активен
- Данные передаются по USB с частотой ~200-300 Гц
- Qt-осциллограф `plot_adc_qt.py` подключается и получает фреймы

### Проблема ❌
**ADC буферы содержат в основном нули!**

Диагностика показывает:
- Большинство фреймов: `non_zero=4/926` (только заголовок, данных нет)
- Периодически: `non_zero=236/926` или `non_zero=460/926` (частичные данные)
- Ожидаем: `non_zero=926/926` (полностью заполненные буферы)

## Причины

### 1. TIM15 Конфигурация
ADC настроен с триггером от TIM15:
```c
hadc1.Init.ExternalTrigConv = ADC_EXTERNALTRIG_T15_TRGO;
hadc1.Init.ContinuousConvMode = DISABLE;
```

**TIM15 ДОЛЖЕН генерировать импульсы TRGO** для каждого преобразования ADC.

### 2. ADC Входы
Проверить, что входные каналы ADC подключены к реальным сигналам:
- ADC1 Channel 3 (pin PA6 или другой согласно .ioc)
- ADC2 Channel X

### 3. ADC Калибровка
ADC должен быть откалиброван перед запуском DMA.

## Решение

### Шаг 1: Проверка TIM15

Добавьте диагностику в `main.c` после запуска TIM15:

```c
printf("[TIM15] CR1=0x%08lX CNT=%lu ARR=%lu\r\n", 
       (unsigned long)TIM15->CR1, 
       (unsigned long)TIM15->CNT, 
       (unsigned long)TIM15->ARR);

// Должно быть: CR1 bit 0 (CEN) = 1, CNT увеличивается
```

### Шаг 2: Проверка ADC DMA

Добавьте printf в `HAL_ADC_ConvCpltCallback` (adc_stream.c):

```c
void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef* hadc) {
    if (hadc->Instance == ADC1) {
        static uint32_t cb_count = 0;
        if((cb_count++ % 100) == 0) {
            // Вывести первые сэмплы
            printf("[ADC1_CB] buf[0..3]=%u,%u,%u,%u\r\n",
                   adc1_buffers[0][0], adc1_buffers[0][1],
                   adc1_buffers[0][2], adc1_buffers[0][3]);
        }
        // ... existing code ...
    }
}
```

### Шаг 3: Проверка Входных Сигналов

Подключите тестовый сигнал (например, 1 кГц меандр 0-3.3В) к входу ADC и проверьте, появляются ли ненулевые данные.

### Шаг 4: Калибровка ADC

Убедитесь, что калибровка выполняется в `adc_stream_start()`:

```c
HAL_ADCEx_Calibration_Start(&hadc1, ADC_CALIB_OFFSET, ADC_SINGLE_ENDED);
HAL_ADCEx_Calibration_Start(&hadc2, ADC_CALIB_OFFSET, ADC_SINGLE_ENDED);
```

## Использование Qt-Осциллографа

```bash
# Запуск с диагностикой
py -3 HostTools/plot_adc_qt.py

# Кнопки:
# - Connect: Подключение к USB
# - Start: Запуск стриминга
# - Stop: Остановка
# - Display ↓/↑: Уменьшить/увеличить частоту обновления экрана
# - Clear: Очистка графиков
```

Осциллограф показывает:
- **Только ненулевые сэмплы** (исключает пустые области)
- **Sample Index** вместо времени
- **Frames received vs Displayed** для контроля нагрузки
- **Diagnostic output** каждые 100 фреймов с анализом данных

## Ожидаемый Результат

После исправления ADC/TIM15 диагностика должна показывать:

```
[FRAME 100] CH=A len=926 non_zero=926/926 min=-15000 max=15000 mean=120.5 region=[0:925] span=926
[FRAME 200] CH=B len=926 non_zero=926/926 min=-14500 max=14800 mean=95.3 region=[0:925] span=926
```

И на осциллографе должны быть видны **полные осциллограммы** с реальными сигналами ADC.

---
**Автор диагностики:** GitHub Copilot  
**Дата:** 27.10.2025  
**Файлы:** `plot_adc_qt.py`, `rpi_record_adc.py`, `usb_vendor_app.c`
