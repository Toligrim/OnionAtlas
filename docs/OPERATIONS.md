# Эксплуатация и ресурсы

## 1. Базовая топология

Рекомендуемый стартовый deployment:

- **Control Plane**: постоянный Linux-хост с SSD, например Raspberry Pi 5-class устройство;
- **Worker**: дешёвый VPS `1 vCPU / 1 GB RAM / 10 GB disk`;
- **Tor**: локально на worker;
- **SQLite/FTS5**: только на control plane;
- **FastAPI/UI**: только на control plane;
- **Backups**: отдельно от worker.

Эта схема специально рассчитана на дешёвую эксплуатацию и простое восстановление.

---

## 2. Ожидаемая нагрузка на worker

При `concurrency=2`–`4`:

- CPU большую часть времени ждёт сеть/Tor;
- кратковременные пики до 100% одного ядра допустимы;
- RAM обычно должна укладываться в 1 GB при отсутствии браузера/Playwright;
- 1–2 GB swap рекомендуется как страховка, но swap не должен маскировать постоянный memory leak.

На worker не запускаются:

- Elasticsearch;
- PostgreSQL;
- Redis;
- Chromium;
- Playwright;
- LLM;
- vector DB;
- heavy UI.

---

## 3. Почему 10 GB диска достаточно worker

Worker не хранит долгосрочную базу.

Диск нужен для:

- ОС;
- Tor;
- Python environment;
- application logs;
- bounded result spool;
- временных файлов.

Spool должен иметь жёсткий лимит. Например, при 10 GB VPS разумно не позволять worker использовать под spool больше ~1 GB без отдельного решения.

---

## 4. Control Plane resources

SQLite + FTS5 значительно легче Elasticsearch.

Основные потребители:

- SQLite page cache;
- FTS queries/index updates;
- FastAPI;
- scheduler/importer;
- OS cache.

Для локального исследовательского deployment 8–16 GB RAM даёт большой запас. Но система не должна проектироваться так, будто память всегда доступна: correctness не зависит от удержания всей базы в RAM.

---

## 5. Главный ресурс — диск control plane

Рост определяется прежде всего количеством индексируемого текста и историей.

Политика v0.1:

Хранить:

- canonical URL;
- title/description/H1;
- normalized text;
- hashes;
- links;
- timestamps;
- observations/errors;
- provenance.

Не хранить по умолчанию:

- images;
- video/audio;
- PDFs;
- archives;
- executable files;
- screenshots;
- HAR;
- полный raw HTML каждой проверки.

Это резко снижает storage growth.

---

## 6. Throughput

Tor crawling latency нестабильна. Поэтому throughput оценивается эмпирически.

Нельзя планировать производительность как у обычного HTTP crawler.

Старт:

```text
concurrency = 2
batch_size = 4–10
max_body = 1–2 MB
```

После недельного теста измерить:

- successful fetches/hour;
- p50/p95 fetch latency;
- timeout rate;
- worker CPU/RAM;
- frontier growth rate.

Только после этого повышать concurrency.

---

## 7. Backpressure

Система должна автоматически замедляться, если:

- result spool растёт;
- control plane не успевает импортировать;
- DB disk space заканчивается;
- worker memory pressure высок;
- Tor health degraded;
- frontier состоит преимущественно из retries.

Worker не должен бесконечно принимать задачи.

---

## 8. Disk watermarks

Рекомендуемая логика:

- `<20% free` — warning;
- `<10% free` — scheduler прекращает low-priority discovery;
- `<7% free` — прекращаются новые discovery fetches, остаётся critical maintenance;
- `<5% free` — остановить запись новых crawl bodies/text и перейти в protective state.

Значения configurable.

---

## 9. systemd units

Планируемые units control plane:

```text
onionatlas-api.service
onionatlas-scheduler.service
onionatlas-discovery.service
onionatlas-maintenance.service / timer
```

Worker:

```text
tor.service
onionatlas-worker.service
```

Возможно объединение scheduler/discovery в один process в v0.1 ради простоты. Логическое разделение в коде сохраняется.

---

## 10. Startup sequence control plane

1. проверить DB доступность;
2. применить migrations;
3. выполнить lightweight integrity checks;
4. вернуть expired leases;
5. проверить disk watermark;
6. запустить API;
7. запустить scheduler;
8. запустить discovery loop;
9. принять workers.

Если migration failed, scheduler не стартует.

---

## 11. Startup sequence worker

1. проверить Tor daemon;
2. проверить SOCKS loopback;
3. проверить свободный spool;
4. проверить control-plane connectivity;
5. отправить heartbeat;
6. перейти в `ready`;
7. начать lease loop.

Никакого direct crawling до успешного Tor health check.

---

## 12. Maintenance jobs

Периодически:

- SQLite checkpoint;
- `PRAGMA optimize`;
- backup;
- log rotation;
- spool cleanup;
- expired lease cleanup;
- discovery statistics aggregation;
- FTS consistency check;
- disk usage report.

Не выполнять `VACUUM` часто без причины: это тяжёлая операция.

---

## 13. Backup policy

Минимум:

- daily DB snapshot;
- несколько recent generations;
- weekly longer-retention snapshot;
- config backup;
- restore test.

Worker backup не нужен, кроме deployment config.

---

## 14. Monitoring dashboard

Минимальный operational dashboard:

### Control plane

```text
DB size
free disk
frontier queued
frontier retry
active leases
imports/min
FTS query latency
new services 24h
novelty 24h
last backup
```

### Worker

```text
heartbeat age
Tor ready
tasks active
fetch success rate
fetch latency p95
RAM
CPU
free disk
spool bytes
```

---

## 15. Alerts

Критичные:

- no worker heartbeat;
- Tor down prolonged;
- DB integrity error;
- disk emergency threshold;
- backup failed repeatedly;
- migration failed;
- spool full;
- scheduler stopped.

Некритичные:

- source saturation;
- high timeout rate;
- temporary external discovery failure.

---

## 16. Обновление worker

Поскольку worker disposable:

1. drain;
2. stop;
3. redeploy clean version;
4. Tor health check;
5. register/heartbeat;
6. resume.

Не нужно мигрировать permanent crawler state.

---

## 17. Обновление control plane

Порядок:

1. backup;
2. stop scheduler/discovery;
3. дождаться/истечь leases;
4. deploy code;
5. apply migrations;
6. smoke tests;
7. start services;
8. verify worker resumes.

API read-only режим во время короткой migration может быть допустим позже.

---

## 18. Масштабирование workers

Сигнал к добавлению второго worker:

- frontier стабильно растёт;
- новые URL ждут значительно дольше целевого SLA;
- control plane имеет запас ресурсов;
- один worker работает почти постоянно, но не успевает.

Добавление worker не требует sharding DB.

---

## 19. Масштабирование хранилища

Перед миграцией с SQLite:

1. измерить DB size;
2. измерить query latency;
3. изучить `EXPLAIN QUERY PLAN`;
4. проверить индексы;
5. проверить размер/качество stored text;
6. настроить retention;
7. только затем рассматривать PostgreSQL.

Elasticsearch не требуется только потому, что проект является «поисковиком». FTS5 достаточен для первой итерации и существенно проще в эксплуатации.

---

## 20. Режимы скорости

Удобно иметь preset:

### low

```text
worker concurrency = 1
low discovery rate
```

### normal

```text
worker concurrency = 2–3
continuous discovery
```

### fast

```text
worker concurrency = 4–6
```

`fast` не должен быть default. Цель — устойчивый 24/7 crawling, а не максимальная кратковременная скорость.

---

## 21. Первый эксплуатационный эксперимент

После реализации v0.1:

- один control plane;
- один 1/1/10 worker;
- concurrency=2;
- фиксированный набор seed;
- 7 суток непрерывной работы.

В конце собрать:

- количество fetch;
- successful rate;
- новые services/day;
- база/FTS growth/day;
- network usage;
- worker max RAM;
- worker CPU;
- control-plane load;
- novelty curves;
- frontier backlog.

После этого tuning основывается на данных, а не предположениях.
