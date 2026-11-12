# Штатное включение — только легитимный администратор Docker

Команды ниже — reviewable план, сейчас они не выполнялись. Сохранённый SFTP-only ключ разрешает передачу/чтение файлов, но не Docker commands. Нужен существующий разрешённый административный terminal/SSH либо действие владельца сервера. Не изменять forced-command policy, cron, Docker internal filesystem или firmware latest.

**Текущее подтверждённое состояние:** root уже создал и проверил пустой master/key по SFTP. Повторный `--init-only` на этом сервере НЕ выполнять: он правильно откажет. API ещё не активен. После build выполнить только read-only check-existing ниже, затем штатный update Hub. Init-only в пункте4 оставлен только для действительно нового сервера, на котором оба файла отсутствуют.

Точная существующая цепочка взята из server `deploy_all_approved.py`, строки14/17/21/77–79: project `bmi30prod`, Hub `bmi30prod_bmi30hub_1`, base `/opt/bmi30hub/releases/0.2.1-20260814-r33/docker-compose.production.yml`, override `/opt/bmi30hub/releases/0.2.1-20260923-r36-all-approved/deployment/docker-compose.all-approved.yml`. Эти пути и public source hashes включены в `deployment-evidence/existing-compose-chain.json`. Старый deploy helper запускать нельзя: он дополнительно меняет publisher policy; здесь его алгоритм не переиспользуется.

1. Получить имя/ID **действующего Hub** и его exact image ID, Compose project/config-file labels штатным `docker inspect` администратора. Сохранить старый image ID для rollback. Не подменять фактический production compose предполагаемым списком файлов из старого каталога. Проверить текущие source hashes по `patch-provenance.json`; при расхождении остановиться.

2. Проверить existing `/var/lib/bmi30hub` RW mount, UID10001:GID10002 процесса, свободное место, directory ownership и отсутствие чужого нового master/key. Не печатать существующие секретные config, Environment или database contents. Для своих устройств проверить/назначить одну активную Hub system через обычный admin UI. Устройства без такого назначения получат 403. Никакого автоматического глобального объединения.

3. Передать **только публичный candidate/** и manifest в отдельный каталог сервера. Построить отдельный образ, передав проверенный image ID/digest текущего Hub, например:

```sh
docker build --build-arg BASE_IMAGE=<verified-current-image-ID-or-digest> -f Dockerfile.central-wifi -t bmi30hub-linux:0.2.1-20261105-central-wifi-v1 .
```

Наследуемый образ уже содержит FastAPI, cryptography и httpx. Не выполнять apt/pip upgrade на сервере. В новом образе меняются только main.py и central_store.py. Запустить чистый import check модуля без private volumes; это не активирует API и не создаёт данных.

4. Инициализировать пустой master **один раз** штатным одноразовым контейнером нового образа с существующим production compose mount и тем же пользователем. Использовать реальные уже определённые compose-файлы плюс приложенный override. Команда приложения:

```sh
python -m bmi30hub.central_store --root /var/lib/bmi30hub --init-only
```

Контейнер должен исполняться именно как UID10001:GID10002. CLI откажет другому UID/GID/root path и при существовании key **или** master. Успех показывает только `key_exists`, scope_count и SHA пустого encrypted-catalog container; ключ/PSK не выводятся. Читать private key для проверки не требуется. Не удалять файл/ключ ради повторного запуска. Если инициализация прервалась между key и master, остановиться и проверить состояние легитимным администратором, сохранив ключ.

Для one-off init **не использовать** `compose run` с фиксированными IP живого Hub. Ему не нужна сеть; безопасная конкретная форма после build:

```sh
docker run --rm --network none --read-only --user 10001:10002 \
  --mount type=bind,src=/var/lib/bmi30hub,dst=/var/lib/bmi30hub \
  --entrypoint python bmi30hub-linux:0.2.1-20261105-central-wifi-v1 \
  -m bmi30hub.central_store --root /var/lib/bmi30hub --init-only
```

Для **текущего уже инициализированного сервера** вместо init-only выполнить read-only check-existing, используя тот же snapshot ключа, который проверяет `_catalog()`. Метод не создаёт lock-файл или директории и не печатает key/PSK/key_id:

```sh
docker run --rm --network none --read-only --user 10001:10002 \
  --mount type=bind,src=/var/lib/bmi30hub,dst=/var/lib/bmi30hub,readonly \
  --entrypoint python bmi30hub-linux:0.2.1-20261105-central-wifi-v1 \
  -c 'import json;from pathlib import Path;from bmi30hub.central_store import CentralStore;d,c=CentralStore(Path("/var/lib/bmi30hub"))._catalog();print(json.dumps({"ok":True,"key_id_match":True,"scope_count":len(d["scopes"])}))'
```

Подтверждённый SFTP readback: catalog SHA256 `22b35eb5fc74dfd2564640fd2a8a2adffe51bd72d7e3177c42c3f31e0fab7038`, scopes0, modes600 UID10001:GID10002. Это filesystem readback, fsync/power-loss durability не заявлялась. Не повторять инициализацию и не менять/удалять ключ после появления новых записей.

5. Обновить только сервис `bmi30hub` стандартным production Compose с новым override: `up -d --no-deps bmi30hub`. Не перезапускать nginx, sshd/key-manager, устройства BMI30 или firmware services. Все existing volumes/ports/UID и network isolation сохраняются. Старый Dockerfile использует proxy headers; nginx перезаписывает `X-Forwarded-Proto=https`. Проверить, что backend8000 недоступен публично и новый API действительно видит HTTPS. Не отключать `require_https` ради обхода ошибки 426.

6. После запуска: прежние Hub login/Agent/firmware status должны оставаться здоровыми и firmware latest иметь прежние bytes/hash. Новый API: anonymous401; unknown/foreign credentials403; approved без active system403; authorized own system пустой каталог. Тестировать настоящий approved-device token существующим приватным клиентом в RAM, без token/PSK в argv, shell history или логах. Проверить серверные modes/UID снаружи контейнера без вывода содержимого key/catalog. Затем отдельно включать RPi client canary на одной явно разрешённой плате; до этого central sync не заявляется работающим.

Rollback: вернуть только Hub image к сохранённому exact старому ID через штатный Compose, `--no-deps`. Не удалять/перезаписывать новый key или master, не откатывать Hub DB/user assignments и не трогать firmware latest. Старый Hub их не использует, а последующая версия должна повторно использовать тот же стабильный ключ. Backup этих двух private файлов выполняется только на сервере в защищённом admin-owned хранилище; не копировать их в репозиторий или public evidence.

Capture endpoints в этом образе намеренно выключены. Для записи требуется отдельное разрешённое включение, storage/quota review и RAM-only producer canary; текущий WiFi deployment не должен ждать этой работы.

## Команды без предположения о cwd

Новый публичный bundle предлагается stage только в новый каталог `/opt/bmi30hub/releases/0.2.1-20261105-central-wifi-v1`. Это новый путь, не утверждение, что он уже создан. После hash review и проверки прежнего image ID администратором:

```sh
BMI30_BASE_IMAGE=$(docker inspect --format '{{.Image}}' bmi30prod_bmi30hub_1)
docker build --build-arg BASE_IMAGE="$BMI30_BASE_IMAGE" \
  -f /opt/bmi30hub/releases/0.2.1-20261105-central-wifi-v1/candidate/Dockerfile.central-wifi \
  -t bmi30hub-linux:0.2.1-20261105-central-wifi-v1 \
  /opt/bmi30hub/releases/0.2.1-20261105-central-wifi-v1/candidate
docker-compose -p bmi30prod \
  -f /opt/bmi30hub/releases/0.2.1-20260814-r33/docker-compose.production.yml \
  -f /opt/bmi30hub/releases/0.2.1-20260923-r36-all-approved/deployment/docker-compose.all-approved.yml \
  -f /opt/bmi30hub/releases/0.2.1-20261105-central-wifi-v1/candidate/docker-compose.central-wifi.yml \
  config --quiet
```

После init-only/readback и согласованного canary окна выполнить ту же точную `docker-compose -p ... -f ... -f ... -f ...` цепочку с окончанием `up -d --no-deps --no-build bmi30hub`. Это штатно пересоздаёт только Hub. Перед этим сохранить прежний image ID, Hub config SHA и firmware latest SHA; не сохранять секретные содержимые в public report. После проверить HTTPS `/bmi30/health`, firmware latest bytes/hash, прежние защищённые контейнеры и scoped API. При health/source/config regression вернуть прежний image через отдельный image-only override той же цепочки; network/credentials/key и DB не откатывать и не удалять.

```sh
docker-compose -p bmi30prod \
  -f /opt/bmi30hub/releases/0.2.1-20260814-r33/docker-compose.production.yml \
  -f /opt/bmi30hub/releases/0.2.1-20260923-r36-all-approved/deployment/docker-compose.all-approved.yml \
  -f /opt/bmi30hub/releases/0.2.1-20261105-central-wifi-v1/candidate/docker-compose.central-wifi.yml \
  up -d --no-deps --no-build bmi30hub
curl --fail --silent --show-error --max-time 15 \
  --resolve www.teiots.net:443:127.0.0.1 https://www.teiots.net/bmi30/health
```
