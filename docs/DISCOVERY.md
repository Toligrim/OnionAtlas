# Автономный discovery и наращивание базы

## 1. Зачем discovery отделён от crawler

Crawler отвечает на вопрос: **«что находится по уже известному URL?»**

Discovery отвечает на другой вопрос: **«откуда взять новые URL, когда текущий граф ссылок перестаёт расти?»**

Если система имеет только crawler, она неизбежно приходит к состоянию:

1. все известные страницы пройдены;
2. все найденные ссылки уже известны;
3. frontier пуст;
4. рост базы прекращается.

Поэтому в OnionAtlas discovery является самостоятельным постоянным циклом.

---

## 2. Два независимых цикла

### Crawl loop

```text
frontier
  ↓
worker
  ↓
Tor
  ↓
page fetch
  ↓
parse
  ↓
new links
  ↓
control plane
  ↓
frontier
```

### Discovery loop

```text
link graph
external indexes
clearnet references
historical datasets
recrawl/offline pool
manual seeds
  ↓
DiscoverySource adapters
  ↓
candidates
  ↓
validation + dedupe + provenance
  ↓
frontier
```

Оба цикла работают параллельно.

---

## 3. Интерфейс DiscoverySource

Каждый источник должен реализовывать единый контракт.

Концептуально:

```text
source.run(context) -> DiscoveryBatch
```

`DiscoveryBatch` содержит:

- `source_id`;
- `run_id`;
- `query/topic` при наличии;
- candidates;
- observed_at;
- source metadata;
- raw_count;
- errors;
- elapsed_ms.

Каждый candidate содержит:

- candidate URL/address;
- источник;
- source URL или source document;
- discovered_at;
- optional anchor/snippet;
- confidence of extraction;
- topic/query, который его породил.

Discovery source не решает, является ли адрес новым. Это делает canonical control plane.

---

## 4. Каналы discovery v0.1

## 4.1. LinkDiscoverySource

Источник — страницы, уже загруженные OnionAtlas.

Parser извлекает все валидные v3 `.onion` URL и отправляет их control plane.

Для каждого ребра сохраняется:

```text
source_service
source_page
raw_target
canonical_target
anchor_text
first_seen
last_seen
times_seen
```

Преимущество: высокая естественная связанность.

Ограничение: замкнутые кластеры и отсутствие новых внешних ссылок приводят к насыщению.

## 4.2. ExternalIndexSource

Адаптеры к публично доступным onion indexes/search datasets.

Первоначально это может быть один источник. Архитектура должна позволять добавлять следующие без изменения scheduler.

От внешнего индекса OnionAtlas принимает только **candidate address + provenance**.

Внешнему title/category/status доверять нельзя как canonical truth. После импорта адрес проходит собственную проверку OnionAtlas.

## 4.3. ClearnetDiscoverySource

Ищет публикации onion-адресов вне Tor-сервисов: официальные сайты, публичные репозитории, документацию, исследовательские материалы и другие разрешённые публичные источники.

Важный принцип: clearnet source только сообщает candidate. Неизвестный onion затем проверяется исключительно Tor worker.

Необходимо хранить источник публикации, потому что он является важным provenance evidence.

## 4.4. DatasetSource

Импорт списков в форматах:

- TXT;
- CSV;
- JSON/JSONL.

Импорт всегда:

1. validates;
2. canonicalizes;
3. deduplicates;
4. records provenance;
5. не отмечает сервис online до реальной проверки.

## 4.5. RecrawlSource

Recrawl не создаёт неизвестные адреса, но является частью автономности: возвращает ранее недоступные или давно не проверявшиеся сервисы обратно во frontier.

Без этого база быстро превращается в одноразовый snapshot.

---

## 5. Validation pipeline кандидата

Каждый discovery candidate проходит одинаковый pipeline:

```text
raw candidate
  ↓
URL parse
  ↓
onion v3 hostname validation
  ↓
canonicalization
  ↓
dedupe by service/page
  ↓
provenance write
  ↓
priority calculation
  ↓
frontier insert/update
```

Невалидные адреса не пропадают бесследно: агрегированная статистика rejects должна сохраняться для диагностики источника.

---

## 6. Provenance — обязательное свойство

Для каждого известного сервиса необходимо ответить на вопрос:

> Откуда OnionAtlas впервые узнал этот адрес и где видел его потом?

Типы provenance:

- `manual`;
- `onion_link`;
- `clearnet_official`;
- `clearnet_reference`;
- `external_index`;
- `dataset`;
- `api`;
- future types.

Один service может иметь множество evidence records.

Пример:

```text
service X
  ├─ first seen via external index
  ├─ later linked from service A
  ├─ later linked from service B
  └─ later published on clearnet site C
```

Количество независимых источников может использоваться в ranking, но не превращается автоматически в утверждение об идентичности владельца.

---

## 7. Novelty

Для каждого discovery run считается:

```text
raw_candidates
valid_candidates
unique_candidates
new_services
known_services
new_pages
rejected
```

Базовая метрика:

```text
novelty_rate = new_services / unique_valid_services
```

Например:

```text
1000 unique candidates
220 new services
novelty = 22%
```

Позже:

```text
1000 unique candidates
8 new services
novelty = 0.8%
```

Вторая ветка практически насыщена.

---

## 8. Saturation detection

Saturation оценивается не по одному запуску, а по окну последних запусков.

Пример стартовой политики:

- `novelty > 15%` — active/high priority;
- `5–15%` — normal;
- `1–5%` — cooling;
- `<1%` несколько запусков подряд — saturated;
- saturated source получает cooldown.

Пороговые значения конфигурируются и должны быть проверены на реальных данных.

Важно различать:

- saturation конкретного запроса;
- saturation конкретного source;
- saturation link graph;
- общий global saturation.

Нельзя делать вывод «новых onion больше нет» только потому, что один источник перестал их давать.

---

## 9. Scheduler discovery-веток

Scheduler выбирает, какой discovery run запускать, используя score.

Score может включать:

- recent novelty;
- time since last run;
- failure rate;
- cost;
- rate limits;
- number of pending candidates;
- topic priority;
- cooldown state.

Концептуально:

```text
score = novelty_weight
      + age_weight
      + topic_weight
      - error_penalty
      - saturation_penalty
      - cost_penalty
```

Это не ML и не LLM. Политика должна быть прозрачной и тестируемой.

---

## 10. Тематический discovery

В v0.1 достаточно фиксированного списка research profiles/queries.

Пример профиля:

```yaml
name: technology
positive_terms:
  - privacy
  - linux
  - hosting
  - networking
  - security
negative_terms: []
```

Профиль не запрещает crawler следовать ссылкам за пределы темы. Он влияет на discovery priority и глубину индексации.

Почему нельзя просто отбрасывать нерелевантный сервис: он может быть графовым мостом к релевантному кластеру.

---

## 11. Auto-expansion терминов — v0.2 design hook

Архитектура v0.1 должна хранить достаточно статистики, чтобы позже добавить детерминированное расширение запросов.

Кандидатный pipeline:

1. взять документы высокорелевантного кластера;
2. посчитать часто встречающиеся значимые термины;
3. исключить stop words и уже использованные terms;
4. создать `candidate_term`;
5. протестировать term ограниченным discovery run;
6. измерить novelty;
7. продвигать только продуктивные terms.

Пример:

```text
seed: privacy
  ↓
новый частый термин: xmpp
  ↓
test query: xmpp
  ↓
novelty 31%
  ↓
term становится active
```

LLM для этого не обязателен.

---

## 12. Recrawl policy

Пример backoff для недоступного сервиса:

```text
failure #1  -> +1 hour
failure #2  -> +6 hours
failure #3  -> +24 hours
failure #4  -> +3 days
failure #5+ -> +7 days / adaptive
```

Для online service:

```text
new/rapidly changing -> чаще
stable -> реже
high-value/high-link service -> чаще
```

Фактические интервалы конфигурируются.

Offline никогда не означает «удалить сервис из истории».

---

## 13. Когда frontier пуст

Пустой crawl frontier не является остановкой системы.

Scheduler действует по следующему порядку:

1. проверить due recrawl;
2. запустить highest-score discovery source;
3. добавить новые candidates;
4. если candidates нет — взять следующий source;
5. если все sources saturated — ждать cooldown;
6. периодически повторять внешние discovery runs.

В состоянии полного насыщения система должна перейти в low-duty maintenance mode, а не генерировать искусственные задания.

---

## 14. Автономность после reboot

После старта control plane:

1. открыть DB;
2. выполнить migrations;
3. найти expired leases;
4. вернуть их в `QUEUED`;
5. восстановить discovery cooldown state;
6. вычислить due recrawls;
7. запустить scheduler;
8. принять worker heartbeat;
9. продолжить pipeline.

Никакого ручного повторного добавления seeds не требуется.

---

## 15. Ограничение, которое система не может преодолеть

v3 onion address невозможно практически перебрать как IPv4 range. Если сервис:

- нигде публично не публиковался;
- не связан с известным графом;
- отсутствует во внешних datasets/indexes;
- не появляется в доступном clearnet discovery;

то OnionAtlas не имеет способа «угадать» его существование.

Поэтому точная формулировка автономности:

> OnionAtlas автономно расширяет наблюдаемую публично обнаружимую часть onion-пространства из доступных источников, измеряет насыщение и поддерживает историческую актуальность базы.

---

## 16. Метрики автономности

На dashboard должны быть:

```text
new_services_24h
new_services_7d
novelty_global_24h
novelty_by_source
frontier_pending
frontier_growth_rate
recrawl_due
services_reactivated
services_became_offline
sources_active
sources_saturated
last_successful_discovery_at
```

Отдельно полезна метрика:

```text
manual_seed_dependency
```

Например доля новых сервисов за неделю, добавленных вручную. Цель зрелой системы — чтобы эта доля стремилась к нулю.

---

## 17. Критерий успешной автономности v0.1

Тест длительностью минимум 7 дней:

- стартуем с ограниченного набора seed;
- после запуска вручную новые адреса не добавляем;
- crawler работает постоянно;
- discovery jobs запускаются автоматически;
- база продолжает расти хотя бы из одного автономного источника;
- offline адреса автоматически перепроверяются;
- после reboot работа продолжается;
- каждый новый service имеет provenance;
- dashboard показывает novelty/saturation;
- при насыщении одного source scheduler переключается на другой.

Если эти условия выполнены, v0.1 считается автономной в инженерном смысле.
