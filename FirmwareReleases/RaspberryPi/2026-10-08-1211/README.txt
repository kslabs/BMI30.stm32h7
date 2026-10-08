BMI30 Raspberry Pi — переносимый пакет 2026-10-08-1211

Версия в портале: 2026-10-08-120920; создание: 2026-10-08T10:09:19Z; network component: 0.1.16.
Это точные байты опубликованного BMI30 server пакета: bmi30_backup_20261008_120920.tar.gz.
Проверены серверный маркер, повторное скачивание, размер 684173 байт и SHA256:
c232edef8f6926b300570d65432367ef0c0bd447f35ac11b00e9e5e748f3215d
bmi30_latest.env сохранён без изменения исходных байтов.

Переносимая USB-система определяет ID текущей платы по физическому CM5 serial
(Device Tree или /proc/cpuinfo). Runtime ID — BMI30- и последние 9 hex символов
в верхнем регистре. Hostname, config id и serial USB-носителя не заменяют hardware
identity. Runtime policy/recovery units не привязаны к имени конкретной платы;
явный expected-host доступен для адресных команд обслуживания.

Исправлен boot-сбой channel policy на бинарных NetworkManager .nmmeta: такой
служебный файл не должен читаться как UTF-8 connection profile. Настоящие NM
connection profiles и сохранённые пользовательские запреты каналов проверяются
отдельно; набор parser cases сохранён вместе с точными исходниками тестов.
Отсутствующее legacy channel_permissions само по себе разрешает каналы.

Пакет включает канонические server updater, builder, switcher и portable assets.
Установка/откат проверяют allowlist и хеши, сохраняют settings/calibration/fleet
credentials и ограниченные права/владельцев приватной конфигурации. Offline флаг
передаётся через sudo, чтобы asset helper не запускал systemctl целевой системы.
Публичный component release.json содержит текущую версию, bundle и хеши модулей.
STM32 остаётся 1.2.55; проверенный архив: FirmwareReleases/STM32/1.2.55.

source-inputs.tar.gz сохраняет точные публичные исходники финального пакета,
service units, headers, utilities и входы тестов с отдельными SHA256.
Приватные configs/ключи/токены/runtime state и сырые диагностические логи в source
archive и evidence не включены. Официальный пакет содержит пустой config template;
RTC Python environment сохраняется на целевом устройстве и не архивируется здесь.
Это обновление приложения/служб, не полный образ USB/eMMC. Для активации нужен
актуальный backend/helper/switcher; исходники не распаковывают поверх live системы.

Подготовительные проверки: 63 ARM network/group/stability/recovery/identity
тестов, 4 Linux backend snapshot/restore, 2 sudo offline, 4 private mirror,
6 config mode, 7 asset install/rollback/allowlist, 6 Python и 8 JavaScript
version display тестов, Bash syntax builder/switcher. validation.json содержит
только проверенные summary/counts; исходные публичные fixtures — в source archive.

Предыдущий 2026-10-08-1131 установлен на четырёх roots S09/S10 USB/eMMC. Фактическая
загрузка S09 eMMC выявила сетевой сбой: UnicodeDecodeError на .nmmeta прервал policy,
а NetworkManager не стартовал из-за Requires dependency. История установки и
пакеты 1131 остаются неизменными; успешный сетевой boot для 1131 не заявляется.

verification.json нового выпуска записывает только matching release/version/hash
installation reports и отдельный фактический eMMC boot proof. До таких результатов
статус pending_final_verification. Offline файловые проверки не равны реальной загрузке
или физической переносимости USB между платами. Пользователь загружает eMMC вручную,
снимая USB и перезагружая плату; автоматические boot/timer/mailbox изменения не нужны.
Сохранение в Git не заменяет BMI30 server update.
ARCHIVE_SHA256SUMS проверяет весь каталог, кроме самого файла сумм.

Подтверждённые установки этого выпуска: 4/4 roots. Фактический eMMC boot proof: ещё ожидается.
