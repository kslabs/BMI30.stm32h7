# Hub: единый WiFi-каталог — локальный additive patch

Кандидат от 05.11.2026. Код/API ещё не установлен и Hub не перезапускался. Root отдельно создал новый пустой master и стабильный private key по SFTP с точным readback: 0 catalog scopes, existing files не перезаписывались, API не активирован. Состояние сохранено в `validation/server-catalog-created.json`; это не подтверждение работающей синхронизации. Firmware latest не менялся. Исходный Hub подтверждён через текущий container filesystem/process root: `main.py` SHA256 `d3eb53e4012c259772ba9b62a6a937150734a188a8c339a7c647dd4dc056bd9c`.

Добавляется `bmi30hub/central_store.py` и ровно четыре строки подключения в реальный `main.py`. Существующие firmware router, Bearer-проверка, Agent, база пользователей и прежние функции сохраняются. Шифрование использует уже существующий `cryptography`; новая серверная зависимость не нужна. Capture API подготовлен, но **выключен** в интеграции. Для его включения требуется отдельный review.

## Один каталог, изоляция по существующей системе

Единственный canonical master: `/var/lib/bmi30hub/data/network/credentials.json`. Внутри одного файла каталоги разделены по `system:<systems.id>`. Scope вычисляется сервером из `devices.system_id`, состояния `approved` и активной системы. Устройство без системы, с выключенной системой или чужим scope не получает пароль. Ни глобальный admin, ни само устройство не могут передать scope в запросе. Назначать устройства в общую систему должен администратор обычным способом; код ничего автоматически не назначает.

Ключ записи — `wifi:<точный SSID>`. Нет trim, casefold или Unicode normalization. Одинаковый SSID разных владельцев/систем может иметь разные PSK. AP-записи `ap:` не загружаются. Существующий LAN gossip не меняется.

Master schema2 имеет `key_id = SHA256(raw32bytekey).hexdigest()`. Binding проверяется до любого чтения/изменения каталога, включая пустую/новую систему. Для проверки и AESGCM используется один и тот же snapshot key bytes. Подмена даже валидным новым 32-байтным ключом вызывает 503 и не позволяет дописать другой scope под новым ключом. Экспортированная pure-функция `make_empty_catalog(key)` задаёт точную начальную структуру; SFTP initializer должен использовать её формулу. При fail/mismatch ключ не заменяется автоматически.

Ответ авторизованному устройству содержит исходный совместимый record: `ssid, psk, security, origin, revision, updated`. PSK выдаётся открытым только внутри HTTPS response в RAM клиента. На диске PSK зашифрован AES-256-GCM с отдельным случайным nonce; associated data включает scope и metadata записи. Стабильный server-only key: `/var/lib/bmi30hub/secrets/wifi-catalog.key`, 0600, UID 10001. Отсутствующий/испорченный ключ при существующем master приводит к отказу, не к пересозданию ключа.

Новые директории `data/network` и ключ/каталог создаются процессом UID10001:GID10002: директории 0700, файлы 0600. Существующие родительские директории не получают recursive chmod/chown. В данной серверной конфигурации `/var/lib/bmi30hub` уже является существующим RW volume. Docker image добавляет только два файла публичного кода.

## Контракт клиента

Точный машинный контракт и sanitized пример: [API_CONTRACT.json](API_CONTRACT.json).

- `GET /api/v1/wifi/catalog` — только approved-device Bearer + активный назначенный Hub system.
- `POST /api/v1/wifi/catalog/merge` — максимум 32 записи/64KiB. Существующий другой PSK заменяется только при текущем `expected_revision` и явном `explicit_replacements` либо свежем сообщении клиента об активном station-профиле и достижимости этого сервера.
- NM timestamp не доказывает актуальность пароля. `client_reported_connected` означает заявление клиента, не независимую проверку сервером. Неактивные сохранённые сети остаются unverified candidates и не заменяют существующий PSK молча.
- На 409 клиент перечитывает master и предлагает выбор либо получает свежую проверку; автоматического last-writer-wins нет.
- `revision` — серверный CAS counter, независимый от входного revision. Исходные origin/revision/updated клиента сохраняются отдельно в provenance. Нельзя бездумно сопоставлять этот counter со старым LAN gossip counter.
- Open WiFi имеет пустой PSK; WPA-PSK допускает 8..63 UTF8-байта или 64 hex; SAE — 1..63. Имя SSID — 1..32 UTF8-байта, NUL запрещён.
- API отвергает query parameters, HTTP и неавторизованные запросы; не отражает тела запросов/PSK в ошибках. Клиент должен использовать только HTTPS этого origin и запрещать redirect с Authorization.

RPi client/rollout ведёт root отдельно. Локальный persistent cache и fallback записи на USB/eMMC здесь не реализованы. Нельзя объявлять центральную синхронизацию работающей до реального deployment и проверки клиента.

## Capture prototype — отдельно, default OFF

В модуле есть owner-only create/status/list/chunk/commit/download. Chunk ≤1MiB, точный SHA256, последовательный durable ACK, idempotent retry. Commit проверяет все chunk digests, итоговый размер, число частей и полный SHA потоково. Quota возвращает 507; существующие captures автоматически не удаляются. Незавершённая запись не считается committed. Crash после записи данных до metadata допускает обрезку только не подтверждённого хвоста при retry. Новая сессия публикуется rename из скрытого staging после fsync metadata.

По умолчанию лимиты: 1GiB/capture, 8GiB/device, 32GiB/global; 128 сессий/device, 1024/global; свободный запас 256MiB. Квоты резервируют max_bytes для незавершённых сессий. При переполнении нужна отдельная административная работа; скрытого удаления нет. Продакшен main явно передаёт `enable_captures=False`, поэтому endpoint отсутствует. Это не готовая включённая система записи.

## Проверки и пределы

`test_central_store.py` исполняет настоящие FastAPI handlers и AESGCM; fake только callback approved authentication, system scope проверяется реальным SQLite query. Проверены cross-tenant, NULL/inactive scope, CAS/explicit choice/fresh verification, точные SSID, key/ciphertext tamper, atomic failure, revision boundary, init-only, path/query/input bounds, capture isolation/idempotence/quota/hash и отказ после повреждения данных. Byte reversal доказывает, что main отличается только четырьмя согласованными добавлениями.

Локально: Windows/Python3.14, FastAPI0.115.14, httpx0.28.1, cryptography46.0.3. Linux flock/fsync, UID/GID permissions, реальный existing container runtime и HTTPS canary после deployment здесь не проверялись. Независимый review отмечается отдельно; авторские PASS нельзя называть независимыми.

Финальные проверки кода: 26/26 авторских и 12/12 независимых. `independent-review.json` привязан к SHA модуля/main и теста. Peer review наблюдал исходный авторский snapshot `validation/author-reviewed-snapshot.json`; `author-validation.json` дополнительно проверен из этого постоянного каталога. Snapshot и новый отчёт имеют разные timestamps, но production source hashes совпадают. Независимый sign-off относится к WiFi; capture storage остаётся default OFF.

`SOURCE.diff` — воспроизводимый патч; `build_patch.py` отказывается при другом baseline main SHA. `candidate/Dockerfile.central-wifi` требует явный проверенный BASE_IMAGE ID/digest и наследует существующий runtime. Инструкции легитимному администратору — [DEPLOYMENT_RU.md](DEPLOYMENT_RU.md). SFTP-only ключ не используется для исполнения команд, cron, Docker internal writes или обхода ограничений.
