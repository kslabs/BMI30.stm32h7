# BMI30 Agent 0.2.7

Production-агент Raspberry Pi для регистрации в BMI30 Hub и отдельного reverse
SSH-туннеля к локальному Portal. ChatGPT на устройстве не требуется.

## Production endpoints

```text
Hub:      https://www.teiots.net/bmi30
Check-in: https://www.teiots.net/bmi30/api/v1/agent/checkin
SSH:      bmi30-tunnel@www.teiots.net:2222
```

HTTPS проверяется обычным системным CA-хранилищем. В агенте отсутствуют `-k`,
`verify=False` и отключение TLS-проверки. SSH host key впервые принимается
только из approved-ответа доверенного HTTPS Hub и затем жёстко закрепляется.

## Миграция на production-туннель

Installer сохраняет старый `bmi30-reverse-tunnel.service` и его отдельные файлы
в датированный root-only backup, затем останавливает, отключает и полностью
удаляет их. Production-туннель называется `bmi30-tunnel.service`. Компоненты
BMI20 с другими именами не изменяются.

```bash
unzip BMI30_Agent_0.2.7.zip
cd BMI30_Agent
sudo ./install_bmi30_agent.sh
```

В check-in поле `agent_version` содержит версию установленного websplit-комплекта
Raspberry, `firmware_version` — только реальную версию приложения STM32 из
`EVT1 FW_INFO`, а версия самого коннектора передаётся как `connector_version`.

По умолчанию installer останавливает старую версию только
`bmi30-agent.service`, устанавливает код и оставляет её autostart выключенным до
ручной проверки:

```bash
sudo bmi30-agent-ctl checkin
sudo bmi30-agent-ctl status
```

Первый успешный check-in обычно возвращает `pending`. После подтверждения
устройства администратором повторите check-in. При `approved` агент проверит
production host/user/port, закрепит Ed25519 host key и запустит отдельный
`bmi30-tunnel.service`.

После успешной ручной проверки:

```bash
sudo bmi30-agent-ctl enable
```

Команда ещё раз выполняет check-in и затем включает autostart агента. При
перезагрузке агент получает актуальное назначение и запускает туннель.

Установка с немедленной проверкой и включением агента:

```bash
sudo ./install_bmi30_agent.sh --enable-agent
```

## Аппаратная идентичность

- `DEVICE_ID` при каждом запуске заново вычисляется из реального 16-значного
  CPU serial Raspberry и не сохраняется в `config.json`.
- Поле `hostname`, передаваемое Hub, также строится из реального CPU serial,
  поэтому скопированный `/etc/hostname` не может подменить номер устройства.
- `local_ips` динамически берётся из `status.interfaces` активного локального
  Portal; loopback исключается. Для старого Portal без этого поля используется
  только live fallback `hostname -I`, а не сохранённый список адресов.
- Ed25519-ключ создаётся один раз в `/etc/bmi30-agent/id_ed25519`.
- Bearer API token создаётся один раз и хранится в
  `/var/lib/bmi30-agent/device_api_token` с mode `0600`.
- Ключ и token не пересоздаются после HTTP 401, обычного 409 или рестарта.
- Только документированный ответ `HTTP 409` с кодом
  `hardware_identity_mismatch`, флагом `reset_identity_required=true` и
  ожидаемым Device ID текущей платы разрешает автоматическую смену BMI30
  ключа/token и один немедленный повтор check-in.
- Приватный ключ и API token никогда не выводятся в status/logs и не входят в
  check-in JSON.

Если склонированная флешка запущена на другой Raspberry, runtime под файловой
блокировкой сравнивает `/var/lib/bmi30-agent/bound_raspberry_serial`, комментарий
SSH public key и новый аппаратный `DEVICE_ID`. Он останавливает только чужой
BMI30-туннель, сохраняет прежние identity-файлы в root-only backup и атомарно
создаёт для новой платы уникальные key/token и hardware binding. Первый check-in
новой платы будет `pending`, пока администратор Hub её не подтвердит.
Скопированный remote port и approval никогда не используются.

`--reset-identity` предназначен только для явного повторного enrollment после
проверки backup:

```bash
sudo ./install_bmi30_agent.sh --reset-identity
```

## Файлы

```text
/opt/bmi30-agent/bmi30_agent.py          агент
/opt/bmi30-agent/run_bmi30_tunnel.sh    проверяющий SSH wrapper
/etc/bmi30-agent/config.json             конфигурация, 0600
/etc/bmi30-agent/id_ed25519              отдельный закрытый ключ, 0600
/etc/bmi30-agent/id_ed25519.pub          публичный ключ
/etc/bmi30-agent/known_hosts             закреплённый production host key, 0600
/etc/bmi30-agent/tunnel.env              назначение без секретов, 0600
/var/lib/bmi30-agent/device_api_token    Bearer token, 0600
/var/lib/bmi30-agent/bound_raspberry_serial аппаратная привязка, 0600
/var/lib/bmi30-agent/identity.lock       блокировка переинициализации, 0600
/var/lib/bmi30-agent/state.json          последнее состояние, 0600
/var/backups/bmi30-agent/<UTC>/          pre-change backup и AUDIT.txt
```

Каталоги `/etc/bmi30-agent` и `/var/lib/bmi30-agent` имеют mode `0700`.
`bmi30-tunnel.service` запускает проверяющий wrapper от root, поскольку
закрытые `0700/0600` пути недоступны непривилегированному пользователю. Wrapper
каждый раз сверяет реальный CPU serial с public-key comment и берёт уникальный
remote port только из проверенного `approved`-ответа Hub в `tunnel.env`.

## Поведение Hub

- `pending`: новый production-туннель остановлен, check-in повторяется через
  `next_checkin_seconds`;
- `approved`: строгая проверка назначения и запуск/обновление туннеля;
- `blocked` / `rejected`: останавливается только `bmi30-tunnel.service`;
- HTTP 401 и обычный 409: key/token сохраняются, check-in повторяется через
  60–66 секунд;
- `409 hardware_identity_mismatch` с точной аппаратной привязкой: старое
  состояние сохраняется в backup, identity сменяется и check-in повторяется
  один раз;
- 5xx/timeout: exponential backoff, работающий туннель не уничтожается.

## Диагностика

```bash
sudo bmi30-agent-ctl status
sudo bmi30-agent-ctl logs
systemctl --no-pager --full status bmi30-tunnel.service
```

## Удаление и rollback

```bash
sudo ./uninstall_bmi30_agent.sh
```

По умолчанию identity/token/host pin сохраняются. Полное удаление новой
production identity требует `--remove-identity`.

Installer печатает `BACKUP_DIR`. Для возврата к предыдущей версии сначала
отключите новые units, затем восстановите файлы из соответствующих путей backup
и выполните `systemctl daemon-reload`. Не копируйте private key/token в отчёты.
