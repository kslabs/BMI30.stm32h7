# STM32 1.2.59-host-tx-lcd

BIN **327960 B**, SHA256 `6188b176ddebe196bcfde8631303f33e27287c3630af249a3d666ca2f1e0d02b`.
Полный публичный source archive: **1327 regular files**, SHA256 `380db9351b2f6d1a961db75a0f4b43fa8b527fd23cc6e2c1387eeb6633755d44`.

59 наследует reviewed58v2 TX-consumption lease и добавляет исправление LCD cold startup: DISPOFF до заполнения известных строк, затем DISPON/500ms intro; runtime LCD использует ограниченный DMA budget после wire/stream/role/sync/ADC phase. Изменений формата собственного NVM, ролей, ADC/RS485/linker относительно58 нет. GPIO/SPI C-model tests не заменяют физическое измерение.

## Сборка и исходники

Архив содержит project/config/linker closure, нужное SDK подмножество, builder/spec и frozen build identity. Две полные66-unit GCC13.3.1 сборки дали одинаковый BIN. Факт новой пересборки после извлечения архива в другой каталог этим выпуском **не заявляется**. ELF/DWARF/map содержат абсолютные build paths; между каталогами их SHA может отличаться. Опубликованы ELF/map именно pinned-repeat build: ELF `ebd5078a…ffd36`, map `f829ca4b…8bcd0`.

**Канонический build source этого выпуска — полный frozen source archive внутри release.** Активные исходники main и isolated STM32 worktree этим пакетом не обновляются. Обнаруженное isolated дерево существенно старше57 (build_info1.0.0); точечный перенос11 файлов57→59 в него запрещён, поскольку дал бы смешанную сборку. Для воспроизводимости извлекайте целый архив в новый каталог; не компилируйте59 из старого active tree.

После извлечения исходников в новый каталог:

```text
python builder/build_source.py --project project --output ../NEW-build-output --toolchain PATH-TO-GNU-TOOLS/bin --cube-h7 sdk/STM32Cube_FW_H7_V1.12.1 --jobs 4
```

Flash LOAD заканчивается `0x08050118`; сектор7 `0x080E0000..0x080FFFFF` отсутствует в программируемом ELF. Сборщик не обращается к устройствам. Перед любой записью конкретной платы нужны её свежие UID, собственный полный backup, role/DC CRC, readback и неизменные option fields/NVM. Нельзя переносить дамп, роль или калибровку другой платы.

## Фактические проверки Root

Один и тот же59 BIN установлен на новом M02 и S07. APP прочитан обратно побайтно; собственный128KiB NVM совпал до/после записи и первого reset до старта Core, option fields не изменились, запись NVM/options/ролей не выполнялась. Собственные роли сохранены: M02 master2, S07 slave7. Эти значения — результаты чтения своих STM, **не настройки для переноса или шаблоны**. Core возобновлён, worker progress и собственный UID/версия/роль проверены, чужие сервисы не перезапускались. Сырые backup/option logs в выпуск не включены.

Пользователь подтвердил на S07 настоящий cold power cycle, intro и собственную роль на LCD. Отдельное Root read-only наблюдение после cold startup показало59 и растущий worker. Это пользовательское визуальное подтверждение; packager не осматривал панель и не выполнял hardware calls.

На S07 Root приостановил Core на фактические8.000197954s отдельным конечным SIGSTOP/CONT helper. Последняя ON выборка была на4936ms после последнего RX-progress ACK, первая OFF — на5289ms; это согласуется с lease5000ms. 130 выборок:118 ON,12 с физическими TX control pins в OFF clamp, затем51 поздних ON; USBgeneration10 не менялась. Чтения регистров последовательные, поэтому интервал — **sampling bracket**, не точное время переключения. Проверялись control GPIO, не RF-мощность антенны. Lease исполняется firmware main loop и не является независимым аппаратным таймером. Тест использовал Core185916 и тот же USBstreamaafe; новая Raspberry shutdown ветка квалифицируется отдельно.

Actual production C tests под ASAN/UBSAN:13 cases/70 checks TX policy и8 cases/1222 checks LCD drivers с mock HAL/SPI/GPIO. Никаких физических операций эти C tests не выполняли; сервисы Root при их запуске не изменились. Доказательства разделены на source/model, actual flash/readback, GPIO observation и user LCD observation в `review/` и `validation/`.

## Raspberry/USB — отдельный результат

Этот firmware package не устанавливает Raspberry или USB. По последнему сообщению Root native123518 уже committed на S07 с чтением central catalog1; новый M02 ещё ждёт собственного DNS/conversion. APP123519 пока только публичный APP tar: новый полный образ, физическая запись/boot USB и generic USB-to-eMMC не квалифицированы этим выпуском. Нельзя выводить такие результаты из успешной STM прошивки. Дальность35cm и вероятность детекции Casino также не квалифицированы.

Пакет подготовлен локально для review/copy Root; packager не менял Git, устройства, сеть или облако. Полные SHA всех файлов приведены в manifest и SHA256SUMS. Private NVM/fullflash/options/rawtraces/SSH routes/credentials/DPAPI и образы исключены.
