BMI30 Raspberry Pi — опубликованный исторический пакет 2026-10-08-1131

Версия в портале: 2026-10-08-113125; создание: 2026-10-08T09:31:25Z.
Пакет опубликован на BMI30 server и проверен повторным скачиванием.
Размер: 684010 байт. SHA256:
02c58b4608b6af53d055c471174af9a68a2e08f4485ffc3aae954d5bc1c52e4a
Официальный архив, server marker и source-inputs.tar.gz сохранены побайтно.
Исходники — точные входы исторического network component 0.1.15; они не
пересобираются с тестами и кодом последующего выпуска.

1131 успешно установлен на всех четырёх roots: S09 USB/eMMC и S10 eMMC/USB.
Проверены 25 portable assets на каждом root; protected settings/credentials/modes
сохранены. Offline установка и работающая активная система не равны реальному boot.
После снятия USB пользователь реально загрузил S09 eMMC: детекция работала,
но сеть не поднялась. Причина подтверждена offline journal/traceback:
communication_recovery.py прочитал бинарные UUID.nmmeta как UTF-8 connection
profiles и завершился с UnicodeDecodeError. NetworkManager требовал успешную
channel-policy службу и не стартовал из-за ошибки dependency.

Выпуск заменён 2026-10-08-1211 / network 0.1.16, который исправляет отбор NM
profiles и содержит восемь parser regression tests. Успешная сетевая загрузка
S09 eMMC для 1131 не заявляется. Deployment tag должен относиться к исправленному
выпуску после его отдельной фактической boot проверки.
verification.json различает четыре установки и неуспешный реальный eMMC boot.

Исторические изменения 1131: portable CM5 hardware identity без привязки к USB
serial/hostname, generic policy/recovery units, server install/rollback portable
assets, сохранение private config modes/owners/mirrors и sudo offline флага,
отображение runtime STM32 версии отдельно от build date/time. STM32 остаётся 1.2.55.
Публичные code/test inputs имеют source hashes; private config/keys/tokens/state
и сырые журналы не включены в source archive и release evidence.
ARCHIVE_SHA256SUMS проверяет весь каталог, кроме самого файла сумм.
