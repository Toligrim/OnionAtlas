# OnionAtlas — план разработки

Этот документ превращает архитектурную спецификацию в последовательность небольших, проверяемых итераций. Порядок принципиален: сначала корректное персистентное состояние и безопасный сбор данных, затем автономное расширение discovery и только после этого UI.

## Текущее состояние

Ветка `feat/v0.1-vertical-slice` содержит рабочую реализацию итераций 1–5: SQLite/FTS5, persistent frontier, leases, bounded Tor worker, transactional importer, link graph, remote worker API, recrawl и generic external discovery core.

Следующий gate — **staging deployment**, а не merge в `main`.

GitHub Actions в текущем окружении репозитория несколько раз завершался до старта любого шага: jobs имели `runner_id=0`, пустое имя runner и пустой массив steps. Поэтому красный check сам по себе не является результатом выполнения `pytest`. До merge тесты должны быть реально выполнены на staging/control host (и, когда GitHub runner снова начнёт назначаться, в CI).

Точный runbook для OPS-агента: `docs/OPS_AGENT_STAGING.md`.

## Модель разработки

Разработка идёт в feature-ветках с ревью относительно `main`. Каждая итерация должна оставлять ветку в тестируемом состоянии. Обычный CI не зависит от живых `.onion`-сервисов; реальные Tor-проверки выполняются только как отдельный opt-in smoke test.

Приоритет v0.1:

```text
корректное персистентное состояние
> безопасный fetch
> идемпотентный import
> автономный link-discovery loop
> удалённый disposable worker
> external discovery / recrawl
> эксплуатационное hardening
> UI
```

## Итерация 1 — фундамент и canonical state

Цель: лёгкий Python-проект и SQLite как единственный canonical store, пригодный для Raspberry Pi ARM без Elasticsearch, Redis, браузерного runtime и LLM.

Результат:

- Python 3.12+ и `src/` layout;
- конфигурация через environment;
- SQLite: WAL, foreign keys, busy timeout;
- migration runner и начальная схема;
- FTS5-схема;
- криптографически корректная проверка Tor v3 onion hostname: версия и checksum, а не только regex на 56 символов;
- canonicalization URL;
- unit-тесты миграций, ограничений БД и URL.

Критерии выхода:

- чистая БД мигрируется;
- повторный запуск миграций безопасен;
- адрес, похожий на v3 по длине, но с неправильным checksum, отклоняется;
- unit-тесты работают без сети.

## Итерация 2 — persistent frontier и leases

Цель: очередь crawling переживает рестарты и безопасно выдаёт работу worker-ам.

Результат:

- добавление seed;
- provenance/evidence;
- persistent `frontier`;
- priority и depth;
- атомарная выдача lease через SQLite;
- уникальные `attempt_id` и `lease_id`;
- expiry/requeue потерянного lease;
- CLI для seed, статистики frontier и диагностики lease.

Критерии выхода:

- canonical-дубликаты не создают новые записи очереди;
- lease пропавшего worker возвращается в очередь;
- порядок по priority детерминирован;
- после рестарта process продолжает работу с той же очередью.

## Итерация 3 — безопасный Tor acquisition worker

Цель: заменяемый worker получает одну leased-задачу и безопасно читает onion-страницу через локальный Tor.

Результат:

- versioned task/result schema;
- разрешены только HTTP(S) v3 `.onion`;
- Tor SOCKS transport;
- ручная проверка redirect;
- clearnet redirect запрещён;
- allowlist `Content-Type`: HTML, XHTML, plain text;
- лимит response body и нормализованного текста;
- лимит количества ссылок со страницы;
- JavaScript не исполняется;
- subresources/media не загружаются;
- извлекаются title/description/h1/text/onion-links;
- ограниченная taxonomy ошибок без утечки traceback в control plane;
- unit-тесты через in-memory HTTP transport, без живого Tor.

Критерии выхода:

- HTML превращается в структурированный result;
- PDF/media, oversized body и clearnet redirect отклоняются;
- script-содержимое не индексируется как видимый текст;
- discovery отдаёт только корректные v3 onion URL.

## Итерация 4 — идемпотентный importer, FTS и graph

Цель: замкнуть первый полноценный vertical slice.

Результат:

- transactional importer;
- защита от stale lease;
- повторная доставка одного result идемпотентна;
- current state + fetch history;
- page revision создаётся только при изменении text hash;
- FTS5 + BM25;
- page-level link graph и service-neighbor queries;
- найденный onion автоматически становится frontier target;
- ограничение глубины обхода;
- retry/backoff ошибок;
- CLI для search/service/graph.

Критерий выхода:

```text
seed A
→ lease
→ structured result A
→ import
→ текст A находится через FTS5
→ связь A→B записана
→ B автоматически попадает во frontier
```

Повторная доставка того же результата не должна дублировать fetch, revision или links.

## Итерация 5 — remote worker protocol и autonomous discovery core

Цель: вынести acquisition на дешёвый VPS и обеспечить рост базы без постоянного ручного добавления адресов.

Результат:

- authenticated FastAPI worker API;
- heartbeat;
- pull-based lease;
- result ACK;
- bounded durable result spool на worker;
- worker не имеет credentials основной БД;
- конфигурируемый HTTP-regex external discovery adapter;
- аудит discovery candidates;
- provenance external candidates;
- novelty metric;
- детерминированный cooldown для низкой novelty;
- scheduler повторного обхода due-сервисов;
- systemd-шаблоны control/worker/discovery timer.

Критерии выхода:

- VPS можно уничтожить и создать заново без потери canonical state;
- при исчерпании link frontier **разрешённый оператору** external source способен добавить новые candidates;
- ранее известные сервисы автоматически возвращаются на recrawl;
- повторяющийся источник с низкой novelty сам уходит в cooldown.

External source не включается автоматически только потому, что endpoint технически доступен. Условия использования/лицензия и rate limits источника должны разрешать автоматизированное потребление.

## Итерация 6 — staging + production hardening

Это текущий этап.

Перед длительным автономным тестом выполнить на реальном Raspberry/Linux control plane + VPS worker:

- `python -m compileall -q src tests`;
- полный `pytest` на exact commit;
- интеграционный тест реального `control plane ↔ VPS worker`;
- Tor SOCKS только на loopback;
- live Tor HTML fetch;
- FTS поиск после fetch;
- automatic link discovery;
- duplicate delivery/idempotency;
- lease expiration/recovery;
- worker spool recovery после недоступности control plane;
- control-plane restart/reboot recovery;
- SQLite `quick_check` + `foreign_key_check`;
- online backup + фактическое открытие backup;
- логи без секретов;
- проверка disk/RAM baseline;
- graceful shutdown/draining по мере необходимости;
- отдельный live-Tor smoke test, не включённый в обычный CI.

Runbook: `OPS_AGENT_STAGING.md` и `STAGING_DEPLOYMENT.md`.

Любой воспроизводимый staging bug исправляется в этой feature-ветке вместе с regression test. PR остаётся незамерженным до прохождения staging gate.

## Итерация 7 — семидневный autonomous soak test

Стартовые условия:

- один Raspberry Pi/Linux control plane;
- один дешёвый VPS worker;
- concurrency worker = 2;
- ограниченный seed set;
- link discovery включён;
- минимум один внешний source — только если его автоматизированное использование разрешено;
- recrawl включён;
- никаких ручных адресов только ради поддержания роста базы.

Измеряем:

- new services/day;
- candidates и novelty каждого source;
- fetch latency/error classes;
- размер и возраст frontier;
- lease expiration;
- worker spool;
- RAM/CPU/disk;
- рост и целостность SQLite;
- восстановление после reboot/network failure.

Успех: система самостоятельно продолжает crawl/recrawl/discovery после первоначального запуска и восстанавливается после обычных отказов без ручного ремонта БД/очереди.

## Итерация 8 — минимальный UI

UI добавляется только после доказанной стабильности ядра и остаётся простым, mobile-friendly:

- dashboard;
- FTS search;
- service details и provenance;
- incoming/outgoing links;
- fetch history;
- discovery novelty/state;
- worker/frontier health.

Не планируются IDE-подобный интерфейс, редактор, tabs/explorer, тяжёлый SPA или браузерные crawler-зависимости.

## Release gate v0.1

v0.1 считается готовой после выполнения Definition of Done из `docs/V0.1_PLAN.md`: persistent frontier, safe fetch, idempotent import, автоматический link discovery, минимум один разрешённый external source, recrawl, novelty/cooldown, изолированный remote worker и успешный семидневный autonomous soak test.
