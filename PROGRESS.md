# Streaming Progress & Recovery Log

(Обновлять при каждом существенном шаге. Этот файл служит "памятью" после перезапуска VS Code / чата.)

## Как собрать
```
# Сборка (Debug конфигурация)
make -C Debug main-build

# Альтернатива: просто make -C Debug (all => main-build)
```

Артефакты: `Debug/BMI30.stm32h7.elf`, `.map`, `.list`.

## Как прошить
```
make -C Debug flash_full
```
Используется openocd mass erase + program + verify + reset.

## Основные host-скрипты
```
# Старт потока + периодический STAT и чтение кадров (встроено в некоторые режимы)
py -3 HostTools/vendor_get_status.py --start

# Просто получить STAT (однократно или несколько раз)
py -3 HostTools/vendor_get_status.py

# Снифф входящих Vendor IN пакетов (сырые кадры)
py -3 HostTools/vendor_sniff_raw.py --start
# или без --start, если уже запущено ранее
py -3 HostTools/vendor_sniff_raw.py
```

## Формат кадра (напоминание)
32-байтный заголовок: magic=0xA55A (LE: 5A A5), ver=1, flags: 0x01=ADC0, 0x02=ADC1, 0x80=TEST.
`seq` общий на пару (A/B), timestamp одинаковый для пары, total_samples.

## Структура STAT v1 (64B)
Поля: sig 'STAT', version=1, cur_samples, frame_bytes, test_frames, produced_seq, sent0, sent1, dbg_tx_cplt,
DMA: dma_done0/1, frame_wr_seq, flags_runtime, flags2 (битовое поле), sending_ch, pair_idx, last_tx_len, cur_stream_seq, reserved0/2/3.

flags2 биты:
0 ep_busy
1 tx_ready
2 pending_B
3 test_in_flight
4 start_ack_done
5 start_stat_inflight
6 start_stat_planned
7 pending_status
8 simple_tx_mode
9 diag_mode_active
10 frameA_ready
11 frameB_ready

## Текущее наблюдаемое состояние (последняя сессия)
(Заполнено: initial)
- Получен burst кадров: STAT -> TEST -> несколько ADC0/ADC1 (длины 512/320).
- После ~10 пакетов поток останавливается, дальнейшие bulk IN чтения дают timeout.
- STAT после остановки: pending_B=1, sent0=1, sent1=0, dbg_tx_cplt=4.
  Интерпретация: B из первой пары не завершён / не отправлен или потерян TxCplt для A.

### Обновление (последний STAT снят py -3 HostTools/vendor_get_status.py)
```
len=64 cur_samples=912 produced_seq=0 sent0=1 sent1=1 tx_cplt=5 test_frames=1 flags2=0x196
flags2 decode: ep_busy=0 tx_ready=1 pending_B=1 test_in_flight=0 start_ack_done=1 pending_status=1 simple_tx_mode=1
frameA_ready(bit10)=0 frameB_ready(bit11)=0 (оба не готовы)
```
Изменения относительно ранее зафиксированного:
- sent1 теперь =1 (кадр B всё же ушёл и получил TXCPLT) -> dbg_tx_cplt=5.
- pending_B остался =1, несмотря на то что пара A/B (sent0=1,sent1=1) должна была закрыться.
- frameA_ready / frameB_ready отсутствуют, то есть текущая пара не подготовлена.
=> Гипотеза сбоев подтверждена: `pending_B` не сбрасывается в USBD_VND_TxCplt после отправки B либо состояние потеряло sending_channel.
Вероятно условие в USBD_VND_TxCplt не сработало (sending_channel == 1?) или sending_channel был 0xFF к моменту завершения B.

Патч: добавить проверку в таске watchdog: если `pending_B`=1 и `!vnd_ep_busy` и `sending_channel==0xFF` и `(HAL_GetTick()-vnd_last_txcplt_ms)>30` и оба кадра текущей пары не `FB_SENDING` -> лог `PEND_B_WDG` и `pending_B=0`.


## Гипотезы остановки
1. Потеря события TXCPLT для канала A приводит к тому, что state machine ждёт B (`pending_B=1`), но sending_channel уже сброшен (0xFF), и повторная попытка B не происходит (кадр ещё не готов или marked READY?).
2. Кадр B не подготовлен (статус frameB_ready=0 в flags2 бите 11) => логика не возвращается к vnd_prepare_pair() из-за pending_B=1.
3. Перекрытие статуса (STAT) или TEST кадра вмешалось и сбросило `vnd_ep_busy`, но не пнуло повторную отправку.
4. Драйвер низкого уровня не вызывает DataIn/TXCPLT (ZLP?) для первого большого IN пакета – необходимо watchdog, который повторно инициирует B или сбрасывает pending_B.

## План ближайших действий
1. Добавить watchdog в `Vendor_Stream_Task`: если `pending_B==1`, `vnd_ep_busy==0`, `sending_channel==0xFF`, и время с последнего успешного TXCPLT (`now - vnd_last_txcplt_ms`) > 50..100 мс —
   - Проверить готовность B. Если B READY — попытаться отправить.
   - Иначе (B не READY) снять `pending_B=0` и разрешить подготовку новой пары (что фактически потеряет B, но не заморозит поток).
2. Логировать событие `PEND_B_WDG` в таких случаях.
3. Пересобрать, прошить, повторить sniff.

## История изменений
- (initial) Создан файл PROGRESS.md с процедурами и гипотезами.

## 2025-10-24: Версия v1.0-working - Последняя рабочая версия ✅

**Статус:** Опубликована на GitHub с тегом `v1.0-working`

**Основные исправления:**
- ✅ **Профиль 1 (200 Гц) полностью исправлен**
  - Проблема: `vnd_prepare_pair()` использовала `adc_stream_get_debug().active_samples`, которое могло быть несинхронизировано после смены профиля
  - Решение: Заменена на прямой вызов `adc_stream_get_active_samples()` для получения актуального значения
  - Результат: Профиль 1 теперь корректно инициализирует DMA с 1360 сэмплами и отправляет кадры на частоте 200 Гц

- ✅ **Улучшена диагностика**
  - Добавлено логирование в CDC при SET_PROFILE: выводит текущий профиль, количество сэмплов, частоту
  - Добавлено логирование при START_STREAM: показывает активный профиль и параметры
  - Теперь легче отследить, какой профиль активен и какие параметры используются

**Изменённые файлы:**
- `USB_DEVICE/App/usb_vendor_app.c`:
  - Исправлена функция `vnd_prepare_pair()` для использования `adc_stream_get_active_samples()`
  - Добавлена диагностика в обработке команды `SET_PROFILE`
  - Добавлена диагностика в обработке команды `START_STREAM`

- `Core/Inc/adc_stream.h`:
  - Добавлены публичные getter-функции: `adc_stream_get_profile()`, `adc_stream_get_active_samples()`, `adc_stream_get_buf_rate()`

**GitHub Repository:**
- URL: https://github.com/kslabs/BMI30.stm32h7.git
- Ветка: main
- Тег: v1.0-working
- Commit: 4d4c788

**Как использовать эту версию:**
```bash
# Клонировать проект с последней рабочей версией
git clone https://github.com/kslabs/BMI30.stm32h7.git
cd BMI30.stm32h7

# Переключиться на конкретный тег (если нужна именно эта версия)
git checkout v1.0-working

# Собрать
make -C Debug all

# Прошить
make -C Debug flash_full

# Или через OpenOCD:
openocd -s scripts -f interface/stlink.cfg -f target/stm32h7x.cfg \
  -c "program {Debug/BMI30.stm32h7.elf} verify reset exit"
```

## 2025-10-24 (продолжение): LCD Display интеграция ✅

**Статус:** LCD дисплей ST7735 полностью интегрирован в streaming loop

**Реализованная функциональность:**
- ✅ **LCD отображает параметры потока в реальном времени**
  - Строка 1: Статус "STREAMING" (зелёный) или "STOPPED" (красный)
  - Строка 2: Частота передачи "Freq: 200 Hz" или "Freq: 300 Hz" (жёлтый)
  - Строка 3: Количество сэмплов "Samples: 1360" или "Samples: 912" (голубой)
  - Строка 4: Номер сэмпла "Start: 0" (голубой)
  - Строка 5: Счётчик отправленных кадров "Frames: XXXX" (светло-синий), обновляется каждые 500 мс
  - Строка 6: Информация о профиле "Profile1" или "Profile2" (пурпурный)

- ✅ **Периодическое обновление дисплея**
  - `stream_display_periodic_update()` вызывается в главном цикле `Vendor_Stream_Task()`
  - Обновление раз в 500 мс для избежания мерцания
  - Счётчик кадров обновляется из глобальных переменных `dbg_sent_ch0_total` и `dbg_sent_ch1_total`

- ✅ **Инициализация и управление дисплеем**
  - Дисплей инициализируется при команде START_STREAM
  - Выполняется явное обновление при START и STOP
  - Дисплей очищается при остановке потока

**Новые файлы:**
- `Core/Inc/stream_display.h` - Заголовочный файл с API дисплея
- `Core/Src/stream_display.c` - Реализация модуля LCD дисплея

**Модифицированные файлы:**
- `USB_DEVICE/App/usb_vendor_app.c`:
  - Добавлен вызов `stream_display_periodic_update()` в `Vendor_Stream_Task()`
  - Добавлено обновление дисплея при START_STREAM с инициализацией структуры `stream_info_t`
  - Добавлено обновление дисплея при STOP_STREAM со счётчиком отправленных кадров

- `Core/Inc/adc_stream.h`:
  - Уже содержит публичные getter-функции из предыдущей версии

**GitHub Commit:**
- Commit: eaf3426
- Message: "feat: Integrate LCD display updates in streaming loop"

**Как использовать эту версию:**
```bash
# Клонировать последнюю версию с LCD интеграцией
git clone https://github.com/kslabs/BMI30.stm32h7.git
cd BMI30.stm32h7

# Собрать
make -C Debug all

# Прошить
make -C Debug flash_full

# Теперь при запуске потока LCD покажет параметры и будет обновлять счётчик кадров в реальном времени
```

**VS Code задачи для быстрого запуска:**
- `"Build (Debug)"` - Собрать проект
- `"Flash (OpenOCD)"` - Прошить микроконтроллер
- `"Clone + Build + Flash"` - Клонировать с GitHub, собрать и прошить (все в одно)
- `"Clone BMI30 repo from GitHub"` - Только клонирование проекта

(Добавлять ниже датированные записи)

