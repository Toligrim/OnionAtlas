# Протокол Control Plane ↔ Worker

## 1. Цель

Worker должен быть максимально простым, заменяемым и безопасным. Он не знает бизнес-логику всей системы и не имеет доступа к основной базе.

Его контракт:

```text
получить ограниченный batch
→ fetch через Tor
→ безопасно разобрать ответ
→ вернуть структурированный результат
→ дождаться ACK
→ удалить временный результат
```

---

## 2. Модель связи

Предпочтительная модель v0.1: **worker инициирует исходящее соединение к control plane или control plane забирает результат по защищённому каналу**.

Нежелательно открывать на Raspberry/control-plane публичный endpoint без необходимости.

Допустимые реализации первой версии:

### Вариант A — pull over authenticated HTTPS

Worker:

1. `POST /worker/lease`
2. получает batch;
3. выполняет его;
4. `POST /worker/results`;
5. получает ACK.

### Вариант B — SSH/JSONL spool

Control plane кладёт/забирает задания через SSH/SFTP, worker возвращает `jsonl.gz` batches.

Этот вариант менее элегантен, но очень прост и хорош для первого vertical slice.

Транспорт должен быть заменяемым без изменения внутренней модели task/result.

---

## 3. Регистрация worker

Worker имеет стабильный случайный `worker_id`.

Heartbeat сообщает:

```json
{
  "worker_id": "worker-01",
  "version": "0.1.0",
  "status": "ready",
  "max_concurrency": 2,
  "active_tasks": 0,
  "tor_ready": true,
  "free_disk_bytes": 7000000000
}
```

Worker не должен передавать лишнюю информацию об ОС/хосте без необходимости.

---

## 4. Lease model

Control plane никогда не «навсегда» отдаёт задачу worker.

Каждая выдача содержит:

```text
task_id
attempt_id
lease_id
lease_expires_at
target URL
policy
```

Если worker пропал и lease истёк, scheduler может вернуть target в очередь.

Если старый worker потом прислал результат, importer проверяет `attempt_id/lease_id` и принимает или маркирует late result согласно политике.

---

## 5. Task schema

Пример логической схемы:

```json
{
  "task_id": "01J...",
  "attempt_id": "01J...",
  "lease_id": "01J...",
  "lease_expires_at": "2026-09-15T12:00:00Z",
  "target": {
    "url": "http://example....onion/path",
    "depth": 2
  },
  "policy": {
    "connect_timeout_ms": 30000,
    "total_timeout_ms": 90000,
    "max_redirects": 3,
    "max_body_bytes": 2097152,
    "max_text_bytes": 1048576,
    "allowed_content_types": [
      "text/html",
      "application/xhtml+xml",
      "text/plain"
    ]
  }
}
```

Control plane задаёт policy, worker её не ослабляет.

---

## 6. Worker fetch states

Результат должен различать:

- DNS/Tor resolution failure;
- Tor unavailable;
- connect timeout;
- total timeout;
- connection refused/reset;
- redirect limit;
- invalid redirect;
- disallowed content type;
- response too large;
- parser error;
- success.

Нельзя всё превращать в `offline`, иначе scheduler потеряет полезную диагностику.

---

## 7. Result schema

Пример successful result:

```json
{
  "task_id": "01J...",
  "attempt_id": "01J...",
  "lease_id": "01J...",
  "worker_id": "worker-01",
  "started_at": "...",
  "finished_at": "...",
  "success": true,
  "request": {
    "url": "http://...onion/"
  },
  "response": {
    "final_url": "http://...onion/",
    "status_code": 200,
    "content_type": "text/html",
    "charset": "utf-8",
    "body_bytes": 84022,
    "elapsed_ms": 5312,
    "redirect_chain": []
  },
  "document": {
    "title": "Example",
    "description": null,
    "h1": "Welcome",
    "normalized_text": "...",
    "content_hash": "sha256:...",
    "text_hash": "sha256:...",
    "requires_javascript": false
  },
  "links": [
    {
      "url": "http://other....onion/",
      "anchor_text": "other"
    }
  ]
}
```

Error result:

```json
{
  "task_id": "...",
  "attempt_id": "...",
  "worker_id": "worker-01",
  "success": false,
  "error": {
    "class": "connect_timeout",
    "message": "bounded diagnostic message"
  }
}
```

---

## 8. Что worker НЕ возвращает

По умолчанию запрещено возвращать:

- cookies;
- local storage;
- browser state;
- downloaded binaries;
- images;
- arbitrary response body;
- unbounded headers;
- secrets;
- Tor control credentials;
- environment dump.

Selected headers должны быть allow-listed.

---

## 9. Spool

Worker должен переживать временную недоступность control plane.

Пример структуры:

```text
/var/lib/onionatlas-worker/
  pending/
  inflight/
  results/
  acknowledged/
```

`acknowledged` можно сразу чистить или держать короткое время для диагностики.

Spool bounded:

- max bytes;
- max result count;
- max age.

Если лимит достигнут, worker перестаёт брать новую работу, пока control plane не примет backlog.

---

## 10. Idempotent delivery

Каждый result имеет устойчивый `attempt_id`.

Control plane ведёт журнал импортированных attempts.

Если worker повторяет POST после network timeout:

```text
result already imported
→ control plane возвращает ACK
→ duplicate state не создаётся
```

Exactly-once transport не требуется; достаточно at-least-once delivery + idempotent import.

---

## 11. Heartbeat и health

Worker health:

- `ready`
- `busy`
- `degraded`
- `tor_down`
- `spool_full`
- `draining`

Control plane не выдаёт новые задания worker в состоянии `tor_down`/`spool_full`.

Heartbeat interval первой версии: например 30–60 секунд.

---

## 12. Draining

Перед обновлением/перезапуском worker:

1. перевести в `draining`;
2. не брать новые tasks;
3. завершить текущие в пределах timeout;
4. доставить results;
5. остановить процесс.

Это позволяет обновлять worker без массового истечения lease.

---

## 13. Authentication

Минимум:

- TLS/SSH;
- отдельный worker credential;
- возможность revoke одного worker;
- credential не даёт database access;
- rate limit для worker API.

В production предпочтителен private overlay (например WireGuard/Tailscale) плюс application-level token/mTLS, но конкретный транспорт не должен быть зашит в domain model.

---

## 14. Версионирование протокола

Каждый request/result содержит protocol version:

```text
protocol_version = 1
```

Control plane должен отклонять неподдерживаемую major version с понятной ошибкой.

Это позволит обновлять worker независимо.

---

## 15. Security boundary

Worker считается машиной, которая регулярно взаимодействует с полностью недоверенным сетевым содержимым.

Поэтому даже при полном захвате worker атакующий в идеале получает только:

- временные текущие tasks;
- временные result batches;
- worker credential с минимальными правами;
- доступ к Tor transport данного worker.

Он не должен получить:

- основную SQLite DB;
- backup;
- полный исторический индекс;
- control-plane shell;
- credentials других workers;
- secrets внешних discovery connectors.

---

## 16. Протокол первой реализации

Чтобы не усложнять v0.1, рекомендуемый порядок:

### Vertical slice

1. один control plane;
2. один worker;
3. batch size 1–10;
4. JSON/JSONL result format;
5. lease persisted в SQLite;
6. authenticated simple transport;
7. no streaming;
8. no message broker.

RabbitMQ/Kafka/Redis Streams не нужны до появления измеримой проблемы, которую они решают.
