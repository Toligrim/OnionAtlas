# OnionAtlas staging — handoff для OPS-агента

Этот документ предназначен для агента, который имеет shell-доступ к Raspberry Pi / постоянному Linux-хосту и отдельному дешёвому VPS. Цель — развернуть **staging**, доказать реальный end-to-end цикл и вернуть подробный отчёт. Не превращать этот прогон в production и не увеличивать нагрузку до прохождения всех проверок.

## Исходные данные

Репозиторий: `Toligrim/OnionAtlas`.

Рабочая ветка: `feat/v0.1-vertical-slice`.

Перед началом зафиксировать конкретный commit SHA ветки и использовать его на обоих хостах. Не устанавливать из плавающего `main` и не мержить PR.

Архитектура staging:

```text
Raspberry Pi / persistent Linux host
  ├─ OnionAtlas control plane
  ├─ SQLite + WAL + FTS5
  ├─ frontier / scheduler
  ├─ discovery / recrawl timer
  └─ private/loopback API
             ↑
             │ authenticated private transport / HTTPS
             │
Cheap VPS
  ├─ Tor daemon
  ├─ SOCKS 127.0.0.1:9050
  ├─ OnionAtlas worker
  └─ bounded result spool
```

Canonical state существует только на control plane. VPS считается disposable worker и не должен иметь доступ к SQLite control plane.

---

## Жёсткие ограничения

1. Не открывать Tor SOCKS наружу. `9050/tcp` должен слушать только loopback.
2. Не публиковать SQLite, FTS или файловую систему Raspberry в интернет.
3. Не передавать bearer token по публичному незашифрованному HTTP.
4. Не отключать content/body/text/link limits ради прохождения теста.
5. Не включать JavaScript/browser automation, Playwright, Chromium, media/PDF downloads.
6. Не добавлять Redis, RabbitMQ, Elasticsearch, Docker или другие компоненты без измеримой необходимости.
7. Worker concurrency staging = `2`.
8. Не включать массовый внешний discovery до успешного ручного seed smoke-test.
9. Не использовать сторонний discovery endpoint, если его условия использования не разрешают автоматизированное получение данных. Для первого smoke-test достаточно ручных известных seed-адресов или источника, которым оператор имеет право пользоваться.
10. Любое неожиданное изменение существующих сетевых/VPN-сервисов на Raspberry или VPS — остановка и отчёт, а не попытка «починить заодно».

---

## Фаза A — аудит до изменений

На каждом хосте записать в отчёт:

- hostname и ОС;
- архитектуру CPU;
- Python version;
- RAM/swap;
- свободное место;
- существующие listening ports;
- существующие systemd units, которые могут конфликтовать;
- состояние firewall;
- маршрутизацию/overlay, который планируется использовать между control plane и worker.

На VPS отдельно проверить, не занят ли `127.0.0.1:9050` и не установлен ли уже Tor с нестандартной конфигурацией.

Ничего не удалять автоматически.

---

## Фаза B — checkout и Python environment

На обоих хостах:

1. Клонировать репозиторий в `/opt/onionatlas` или согласованный безопасный путь.
2. Checkout **конкретного SHA** рабочей ветки.
3. Создать Python virtualenv.
4. Установить проект и зависимости.
5. Выполнить минимум:

```text
python -m compileall -q src tests
pytest
```

Если тесты не проходят — **не продолжать deploy**. Вернуть полный failure summary, название теста и traceback без секретов.

Также проверить:

```text
onionatlas --version
onionatlas --help
```

---

## Фаза C — control plane на Raspberry

Создать отдельного непривилегированного пользователя `onionatlas`.

Подготовить:

```text
/var/lib/onionatlas
/etc/onionatlas/control.env
```

Минимальная конфигурация:

```text
ONIONATLAS_DB=/var/lib/onionatlas/onionatlas.sqlite3
ONIONATLAS_WORKER_TOKEN=<случайный длинный staging secret>
ONIONATLAS_LEASE_SECONDS=300
ONIONATLAS_WORKER_BATCH_SIZE=2
ONIONATLAS_FETCH_CONNECT_TIMEOUT=30
ONIONATLAS_FETCH_TOTAL_TIMEOUT=90
ONIONATLAS_FETCH_MAX_REDIRECTS=3
ONIONATLAS_FETCH_MAX_BODY_BYTES=2097152
ONIONATLAS_FETCH_MAX_TEXT_BYTES=1048576
ONIONATLAS_FETCH_MAX_LINKS=500
ONIONATLAS_CRAWL_MAX_DEPTH=3
```

Secret не помещать в Git, shell history отчёта или итоговый deployment report.

Выполнить:

```text
onionatlas db init
onionatlas db check
onionatlas status
```

Ожидание: `quick_check=ok`, foreign-key errors отсутствуют.

Установить `deploy/systemd/onionatlas-control.service`, предварительно сверив фактические пути. API сначала держать на loopback или private interface.

После запуска проверить `/healthz` локально.

---

## Фаза D — Tor + worker на VPS

Установить Tor штатным способом для текущего дистрибутива.

Критическая проверка:

```text
SocksPort 127.0.0.1:9050
```

Проверить через `ss`/аналог, что `0.0.0.0:9050` и `[::]:9050` отсутствуют.

Создать непривилегированного пользователя `onionatlas-worker` и:

```text
/var/lib/onionatlas-worker
/etc/onionatlas/worker.env
```

Конфигурация worker:

```text
ONIONATLAS_CONTROL_URL=<private HTTPS URL или адрес private overlay>
ONIONATLAS_WORKER_TOKEN=<тот же staging secret>
ONIONATLAS_WORKER_ID=worker-01
ONIONATLAS_TOR_SOCKS=socks5://127.0.0.1:9050
ONIONATLAS_WORKER_SPOOL=/var/lib/onionatlas-worker/worker-spool
ONIONATLAS_WORKER_BATCH_SIZE=2
ONIONATLAS_WORKER_POLL_SECONDS=15
```

Если control plane доступен по HTTP только внутри WireGuard/Tailscale/private overlay, допускается:

```text
ONIONATLAS_WORKER_ALLOW_INSECURE_CONTROL=1
```

Не использовать эту опцию через публичный интернет.

До запуска daemon:

```text
onionatlas worker probe-tor
```

Затем установить и запустить `deploy/systemd/onionatlas-worker.service`.

---

## Фаза E — end-to-end smoke test

Не начинать с большого списка.

Добавить на control plane один или несколько заранее проверенных публичных v3 onion seed URL, использование которых допустимо:

```text
onionatlas seed add <seed>
onionatlas frontier stats
```

Наблюдать lifecycle:

```text
queued
→ leased worker-01
→ Tor fetch
→ result delivered
→ import transaction
→ done или retry
```

Проверить:

```text
onionatlas worker list
onionatlas frontier stats
onionatlas status
onionatlas db check
```

После успешной HTML-страницы проверить FTS по известной фразе страницы:

```text
onionatlas search "<phrase>"
```

Если страница содержит валидную onion-ссылку, проверить, что новый адрес появился во frontier автоматически, а provenance имеет тип `onion_link`.

---

## Фаза F — обязательные fault tests

### 1. Tor down

Остановить Tor на VPS на короткое время.

Ожидание:

- probe не готов;
- heartbeat worker становится `tor_down`;
- новые leases не запрашиваются;
- после восстановления Tor worker возвращается без ручного ремонта БД.

### 2. Control plane недоступен

Коротко остановить control API после получения задания.

Ожидание:

- уже полученный result остаётся в bounded worker spool;
- после возврата control plane result доставляется;
- canonical DB не повреждается.

### 3. Worker restart

Перезапустить worker во время/после lease.

Ожидание:

- незавершённый lease истекает и возвращается в очередь;
- duplicate delivery не создаёт второй fetch/revision.

### 4. Control-plane reboot/restart

Перезапустить control-plane service, а если безопасно — выполнить один тестовый reboot Raspberry.

Ожидание:

- SQLite/frontier сохраняются;
- WAL открывается корректно;
- `db check` проходит;
- worker продолжает работу после возврата API.

### 5. Backup/restore

Создать online backup:

```text
onionatlas db backup <backup-path>
```

Открыть копию отдельно и проверить `quick_check`/`foreign_key_check`. Не считать backup рабочим, пока restore/read test не выполнен.

---

## Фаза G — внешний discovery только после smoke-test

В v0.1 generic source — это кандидатный источник, а не источник истины.

Добавлять только URL/API/dataset, автоматизированное использование которого разрешено оператору. Не включать сторонние поисковые сервисы просто потому, что endpoint технически доступен.

Порядок:

```text
external source
→ candidate
→ v3 checksum validation
→ dedupe
→ provenance
→ frontier
→ собственная Tor-проверка worker
```

После одного controlled run записать:

- raw candidates;
- valid;
- unique;
- new;
- known;
- rejected;
- novelty rate.

Не включать бесконечный polling, пока не проверены rate limits/terms источника.

---

## Фаза H — наблюдение 24 часа

После успешных fault tests разрешить staging работать сутки с `concurrency=2`.

Снять минимум:

- RSS/CPU Tor;
- RSS/CPU worker;
- RSS/CPU control plane;
- SQLite/WAL size;
- disk free;
- queued/leased/retry/done;
- fetches/hour;
- success rate;
- error classes;
- p50/p95 fetch latency, если доступно из БД;
- lease expirations;
- spool count/bytes;
- new services/hour;
- new links/hour;
- recrawl behavior.

Не повышать concurrency в течение этого baseline-периода.

---

## Что вернуть разработчику

Создать `STAGING_REPORT.md` (секреты/IP при необходимости редактировать) со структурой:

```text
1. Exact commit SHA
2. Host audit: control plane
3. Host audit: worker
4. Installation result
5. Unit/integration test result
6. Tor listener verification
7. End-to-end seed result
8. FTS result
9. Link-discovery result
10. Fault tests
11. Backup/restore result
12. 24h metrics (если прогон завершён)
13. Errors/warnings
14. Changes made outside repository
15. Recommendation: proceed / fix first
```

Если обнаружен баг OnionAtlas — не маскировать его системной настройкой. Зафиксировать воспроизводимость, логи без секретов и точные шаги, чтобы баг можно было исправить кодом и добавить regression test.

## Стоп-условия

Немедленно остановить staging expansion и вернуть отчёт, если:

- SOCKS оказался публично доступным;
- worker способен писать напрямую в canonical DB;
- токен передаётся по публичному HTTP;
- SQLite integrity/foreign-key check не проходит;
- importer создаёт дубликаты при повторной delivery;
- lease теряется без автоматического recovery;
- worker spool растёт без bound;
- процесс начинает скачивать неразрешённые binary/media content types;
- наблюдается неконтролируемый рост диска/памяти;
- тесты репозитория не проходят на целевом хосте.
