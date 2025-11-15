# Задачи на завтра: Исправление 90-секундного таймаута ADC

## Текущее состояние (сохранено в git)

### ✅ Что работает:
- LCD загружается за 2 секунды (было 20с)
- GUI осциллограф запускается для одного канала (A)
- Передача данных работает **90 секунд** на скорости 1000 Hz (block rate)
- Получено 1442 фрейма за 90 секунд
- Файл для запуска: `HostTools/start_gui_channel_a.py`

### ❌ Критическая проблема:
**Передача останавливается через 90 секунд!**

## Диагностика выполнена

### Симптомы:
1. GUI показывает `idle_pair` растущий после t=91s
2. Устройство зависает в бесконечном логировании `[VND] PREPARE_PAIR slot=0`
3. CDC порт отвечает, но команда DIAG не обрабатывается из-за спама логов
4. Требуется аппаратный reset для восстановления

### Найденная причина (firmware):
**Файл:** `USB_DEVICE/App/usb_vendor_app.c`
**Функция:** `vnd_prepare_pair()` (строки 902-1002)
**Строка 931:** Раннее возвращение когда `frame_wr_seq == frame_rd_seq`

```c
__disable_irq();
wr = frame_wr_seq; rd = frame_rd_seq;
if (wr == rd) { 
    __enable_irq(); 
    return;  // <-- Нет данных из ADC!
}
```

**Механизм бага:**
1. ADC/DMA работает первые 90 секунд нормально
2. Через ~90 секунд ADC перестаёт заполнять FIFO
3. `frame_wr_seq == frame_rd_seq` (нет новых данных)
4. `vnd_prepare_pair()` вызывается непрерывно из main loop (строка 1719)
5. Каждый раз логирует "PREPARE_PAIR slot=0" и возвращается
6. Бесконечный цикл → устройство зависает

## Задачи на завтра

### Приоритет 1: Найти корневую причину остановки ADC

Проверить в следующих файлах:
- `Core/Src/adc_stream.c` - настройка ADC/DMA
- `Core/Src/stm32h7xx_it.c` - обработчики прерываний
- `Core/Inc/adc_stream.h` - конфигурация буферов

**Возможные причины:**
1. **DMA останавливается после overflow** - проверить настройки DMA_CIRCULAR
2. **Блокировка прерываний** - long critical sections где-то в коде
3. **Переполнение FIFO** - frame_wr_seq оборачивается, но логика не учитывает
4. **Таймер TIM15 останавливается** - проверить генерацию TRGO
5. **Конфликт приоритетов прерываний** - DMA vs USB

### Приоритет 2: Добавить диагностику

Добавить в прошивку:
```c
// В adc_stream.c или main.c
volatile uint32_t adc_last_irq_time;
volatile uint32_t adc_irq_count;
volatile uint32_t dma_error_flags;

// В DMA_IRQHandler:
adc_last_irq_time = HAL_GetTick();
adc_irq_count++;
```

Добавить в STATUS v4:
- `adc_last_irq_time` - время последнего DMA прерывания
- `adc_irq_count` - общее количество прерываний
- `dma_error_flags` - флаги ошибок DMA

### Приоритет 3: Временное решение (workaround)

Если исправление займёт долго, можно добавить:

**Вариант A: Watchdog для ADC**
```c
// В main loop, рядом со строкой 1719:
uint32_t now = HAL_GetTick();
if (now - adc_last_irq_time > 5000) {  // 5 секунд без DMA
    // Перезапустить ADC/DMA
    HAL_ADC_Stop_DMA(&hadc1);
    HAL_ADC_Stop_DMA(&hadc2);
    __disable_irq();
    frame_wr_seq = 0;
    frame_rd_seq = 0;
    __enable_irq();
    HAL_ADC_Start_DMA(&hadc1, ...);
    HAL_ADC_Start_DMA(&hadc2, ...);
}
```

**Вариант B: Ограничить логирование**
```c
// В vnd_prepare_pair():
static uint32_t last_log_time = 0;
if (wr == rd) { 
    uint32_t now = HAL_GetTick();
    if (now - last_log_time > 1000) {  // Логировать не чаще раза в секунду
        VND_LOG("PREPARE_PAIR slot=%u", active_slot);
        last_log_time = now;
    }
    __enable_irq(); 
    return;
}
```

## Команды для тестирования

### Запуск GUI (работает 90 секунд):
```powershell
py -3 HostTools\start_gui_channel_a.py
```

### Проверка статуса устройства:
```powershell
py -3 -c "import serial, time; s=serial.Serial('COM4', 115200, timeout=2); s.write(b'GET_STATUS\r\n'); time.sleep(0.5); print(s.read(2000).decode())"
```

### Build + Flash:
```powershell
# Используй VS Code task: "BUILD+FLASH (auto-update time)"
# Или вручную:
make -C Debug all
# Flash через OpenOCD или STM32CubeProgrammer
```

## Файлы для внимания

**Firmware (исходники):**
- `USB_DEVICE/App/usb_vendor_app.c` - строка 931 (ранний возврат)
- `Core/Src/adc_stream.c` - инициализация и обработчики ADC/DMA
- `Core/Src/stm32h7xx_it.c` - прерывания DMA

**Python scripts (работающие):**
- `HostTools/start_gui_channel_a.py` - ✅ запуск GUI для канала A
- `HostTools/gui_oscilloscope.py` - GUI с флагом `--single`

**Python scripts (не работают):**
- `HostTools/run_gui_with_start.py` - после CDC RESET нет передачи
- `HostTools/gui_dual_independent.py` - краш matplotlib

## Git информация

**Branch:** rollback/diag-2025-11-02
**Last commit:** 52efd2a "WIP: GUI single channel A works 90s, then ADC stops"
**Files changed:** 92 files (99438 insertions)

**Status:** Working tree clean, ready for tomorrow's work

---

**Главная цель:** Найти и исправить причину остановки ADC после 90 секунд работы!
