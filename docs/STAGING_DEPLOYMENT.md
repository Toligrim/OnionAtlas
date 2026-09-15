# Staging deployment: Raspberry/Linux control plane + cheap VPS worker

This runbook is for the first real OnionAtlas deployment. It deliberately optimizes for observability and rollback rather than maximum crawling speed.

## 1. Target topology

```text
Raspberry Pi / persistent Linux host
  ├─ SQLite + WAL + FTS5
  ├─ frontier / scheduler
  ├─ discovery + recrawl timer
  └─ control API on loopback/private network
             ↑
             │ authenticated HTTPS or private overlay
             │
Cheap VPS (1 vCPU / 1 GB RAM / 10 GB disk)
  ├─ Tor daemon
  ├─ SOCKS 127.0.0.1:9050 only
  ├─ OnionAtlas worker
  └─ bounded result spool
             ↓
          Tor network
```

Canonical state lives only on the control plane. The VPS is disposable.

## 2. Staging limits

Start conservatively:

```text
worker concurrency = 2
lease = 300 seconds
connect timeout = 30 seconds
total fetch timeout = 90 seconds
max body = 2 MiB
max normalized text = 1 MiB
max links per page = 500
max discovery depth = 3
```

Do not raise concurrency until measurements show the worker is actually throughput-limited.

## 3. Control-plane host

Create an unprivileged service account and directories appropriate for the distribution:

```text
/opt/onionatlas
/var/lib/onionatlas
/etc/onionatlas/control.env
```

Install Python 3.12+ and create a virtual environment. Install OnionAtlas from the exact staging branch/commit rather than a floating branch once the commit to test has been chosen.

Example environment:

```text
ONIONATLAS_DB=/var/lib/onionatlas/onionatlas.sqlite3
ONIONATLAS_WORKER_TOKEN=<long random secret>
ONIONATLAS_LEASE_SECONDS=300
ONIONATLAS_WORKER_BATCH_SIZE=2
```

Initialize and verify the database:

```text
onionatlas db init
onionatlas db check
onionatlas status
```

Install `deploy/systemd/onionatlas-control.service` and `onionatlas-discovery.{service,timer}` only after paths/users are checked for the actual host.

## 4. Worker transport

Preferred choices:

1. private overlay such as WireGuard/Tailscale plus an explicitly allowed HTTP control URL;
2. HTTPS reverse proxy in front of the loopback control API.

Do not send the bearer token over ordinary public HTTP.

By default the worker refuses non-HTTPS control URLs except loopback. For a private overlay where HTTP is deliberately accepted:

```text
ONIONATLAS_WORKER_ALLOW_INSECURE_CONTROL=1
```

Use that override only when the transport itself is private/authenticated.

## 5. VPS worker

Install Tor from the distribution package or Tor Project-supported repository. The critical boundary is:

```text
SocksPort 127.0.0.1:9050
```

Never expose the SOCKS port publicly.

Create:

```text
/opt/onionatlas
/var/lib/onionatlas-worker
/etc/onionatlas/worker.env
```

Worker environment:

```text
ONIONATLAS_CONTROL_URL=https://<control-host>
ONIONATLAS_WORKER_TOKEN=<same staging worker token>
ONIONATLAS_WORKER_ID=worker-01
ONIONATLAS_TOR_SOCKS=socks5://127.0.0.1:9050
ONIONATLAS_WORKER_SPOOL=/var/lib/onionatlas-worker/worker-spool
ONIONATLAS_WORKER_BATCH_SIZE=2
ONIONATLAS_WORKER_POLL_SECONDS=15
```

Before enabling the service:

```text
onionatlas worker probe-tor
```

This first probe only verifies that the local SOCKS listener is reachable. A later live smoke test must still prove that a request actually traverses Tor.

Then enable `deploy/systemd/onionatlas-worker.service`.

## 6. Initial discovery setup

Start with a small, known seed set manually. Do not start with tens of thousands of addresses and do not enable a third-party discovery service merely because a technically accessible endpoint exists.

```text
onionatlas seed add <known-v3-onion-url>
```

For the first staging smoke test, manual seeds are sufficient. The generic external discovery adapter should only be connected to an API, feed or dataset that the operator is authorized to consume automatically and whose rate limits/terms are understood.

Every external source remains candidate-only: a returned address is validated, deduplicated, recorded as provenance and independently checked by the OnionAtlas Tor worker.

Ahmia is useful as a reference/search service, but its current public Terms of Service prohibit scraping or replicating the service without permission. Therefore the staging runbook does **not** instruct an operator to poll Ahmia automatically unless explicit permission for that use has been obtained. See `EXTERNAL_SOURCES.md`.

## 7. First smoke test

On the control plane:

```text
onionatlas frontier stats
onionatlas worker list
onionatlas status
```

Expected lifecycle:

```text
seed queued
→ worker heartbeat ready
→ lease issued
→ worker fetch through Tor
→ result ACK
→ frontier target done/retry
→ page/fetch stored
→ FTS updated
→ discovered onion links queued
```

Verify search after a successful fetch:

```text
onionatlas search "<known word from fetched page>"
```

## 8. Failure tests before autonomous operation

Perform these deliberately in staging:

### Stop Tor on the worker

Expected: `worker probe-tor` fails; worker heartbeat becomes `tor_down`; it does not request new leases.

### Stop control plane temporarily

Expected: worker keeps already-produced results in the bounded spool; canonical DB is unaffected.

### Restart worker during a task

Expected: lease eventually expires and becomes available again; duplicate/late delivery does not create duplicate canonical fetch state.

### Reboot control plane

Expected: SQLite/frontier survive restart and work continues after services return.

### Corrupt worker result / wrong worker owner in a test fixture

Expected: importer rejects it; canonical transaction rolls back.

## 9. Backups

Before changing schema or deployment configuration:

```text
onionatlas db check
onionatlas db backup /path/to/backups/onionatlas-YYYYMMDD.sqlite3
```

The backup command uses SQLite's online backup API rather than copying a live WAL database blindly.

Periodically test that a backup opens and passes `PRAGMA quick_check`.

## 10. What to measure during the first 24 hours

At minimum:

- worker RSS and CPU;
- Tor process RSS;
- fetch success/error classes;
- median/p95 fetch latency;
- frontier queued/retry/leased counts;
- lease expirations;
- result spool count/bytes;
- SQLite size and WAL growth;
- new services/hour;
- new links/hour;
- external-source novelty;
- disk free space.

Do not tune based on a few minutes of traffic.

## 11. Gate before seven-day soak

Do not start the long autonomous test until:

- live Tor fetch has succeeded end-to-end;
- database backup/restore was tested;
- worker-loss recovery was tested;
- control-plane restart recovery was tested;
- at least one authorized discovery source can add candidates, if external discovery is enabled;
- recrawl returns completed/offline services to frontier;
- no public SOCKS port exists;
- worker token is not sent over public HTTP;
- memory/disk growth is understood for at least one day.

Only then enable continuous operation for the seven-day soak test described in `V0.1_PLAN.md`.
