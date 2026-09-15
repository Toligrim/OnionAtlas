from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from onionatlas import __version__
from onionatlas.config import Settings
from onionatlas.discovery import ensure_http_source, run_due_sources, schedule_due_recrawls
from onionatlas.domain.results import CrawlResult
from onionatlas.frontier import enqueue_url, frontier_stats, lease_tasks
from onionatlas.graph import service_neighbors
from onionatlas.importer import import_result
from onionatlas.search import search_pages
from onionatlas.storage import connect, migrate
from onionatlas.worker.client import probe_socks_listener, run_worker_forever, run_worker_once

AHMIA_RECENT_SUBMISSIONS_URL = "https://ahmia.fi/add/onionsadded/"


def _database_path(args: argparse.Namespace) -> Path:
    value = getattr(args, "database", None)
    return Path(value) if value else Settings.from_env().database_path


def _connection(args: argparse.Namespace):
    connection = connect(_database_path(args))
    migrate(connection)
    return connection


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_db_init(args: argparse.Namespace) -> int:
    connection = _connection(args)
    connection.close()
    print(str(_database_path(args)))
    return 0


def cmd_db_check(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        quick_rows = connection.execute("PRAGMA quick_check").fetchall()
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        ok = (
            len(quick_rows) == 1
            and quick_rows[0][0] == "ok"
            and not foreign_key_rows
        )
        _print_json(
            {
                "ok": ok,
                "quick_check": [row[0] for row in quick_rows],
                "foreign_key_errors": [list(row) for row in foreign_key_rows],
            }
        )
        return 0 if ok else 2
    finally:
        connection.close()


def cmd_db_backup(args: argparse.Namespace) -> int:
    source = _connection(args)
    destination_path = Path(args.path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    _print_json({"backup": str(destination_path)})
    return 0


def cmd_seed_add(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        outcome = enqueue_url(
            connection,
            args.url,
            depth=0,
            priority=args.priority,
            evidence_type="manual",
            requeue_existing=True,
        )
        _print_json(
            {
                "frontier_id": outcome.frontier_id,
                "service_id": outcome.service_id,
                "url": outcome.canonical.url,
                "service_created": outcome.service_created,
                "frontier_created": outcome.frontier_created,
            }
        )
    finally:
        connection.close()
    return 0


def cmd_frontier_stats(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        _print_json(frontier_stats(connection))
    finally:
        connection.close()
    return 0


def cmd_frontier_lease(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        tasks = lease_tasks(
            connection,
            worker_id=args.worker_id,
            limit=args.limit,
            lease_seconds=args.lease_seconds,
        )
        _print_json([task.to_dict() for task in tasks])
    finally:
        connection.close()
    return 0


def cmd_import_result(args: argparse.Namespace) -> int:
    result = CrawlResult.from_dict(json.loads(Path(args.path).read_text(encoding="utf-8")))
    connection = _connection(args)
    try:
        _print_json(asdict(import_result(connection, result)))
    finally:
        connection.close()
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        _print_json([asdict(hit) for hit in search_pages(connection, args.query, limit=args.limit)])
    finally:
        connection.close()
    return 0


def cmd_service_show(args: argparse.Namespace) -> int:
    host = args.onion.lower().removesuffix("/")
    if host.startswith("http://") or host.startswith("https://"):
        from onionatlas.domain.urls import canonicalize_onion_url

        host = canonicalize_onion_url(host).onion_host
    connection = _connection(args)
    try:
        row = connection.execute(
            "SELECT * FROM services WHERE onion_host = ?", (host,)
        ).fetchone()
        if row is None:
            _print_json({"found": False, "onion_host": host})
            return 1
        payload = dict(row)
        payload["evidence"] = [
            dict(item)
            for item in connection.execute(
                """
                SELECT evidence_type, source_url, anchor_text, observed_at, is_first_discovery
                FROM service_evidence
                WHERE service_id = ?
                ORDER BY observed_at DESC
                LIMIT 50
                """,
                (row["id"],),
            )
        ]
        _print_json(payload)
    finally:
        connection.close()
    return 0


def cmd_graph_neighbors(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        _print_json([asdict(item) for item in service_neighbors(connection, args.onion)])
    finally:
        connection.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        _print_json(
            {
                "services": int(connection.execute("SELECT COUNT(*) FROM services").fetchone()[0]),
                "pages": int(connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]),
                "fetches": int(connection.execute("SELECT COUNT(*) FROM fetches").fetchone()[0]),
                "links": int(connection.execute("SELECT COUNT(*) FROM links").fetchone()[0]),
                "workers": int(connection.execute("SELECT COUNT(*) FROM workers").fetchone()[0]),
                "discovery_sources": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM discovery_sources WHERE enabled = 1"
                    ).fetchone()[0]
                ),
                "frontier": frontier_stats(connection),
            }
        )
    finally:
        connection.close()
    return 0


def cmd_discovery_add_http(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        source_id = ensure_http_source(
            connection,
            name=args.name,
            url_template=args.url_template,
            queries=args.query or [""],
            interval_seconds=args.interval_seconds,
        )
        _print_json({"source_id": source_id, "name": args.name})
    finally:
        connection.close()
    return 0


def cmd_discovery_add_ahmia(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        source_id = ensure_http_source(
            connection,
            name="ahmia-recent-submissions",
            url_template=AHMIA_RECENT_SUBMISSIONS_URL,
            queries=[""],
            interval_seconds=args.interval_seconds,
        )
        _print_json(
            {
                "source_id": source_id,
                "name": "ahmia-recent-submissions",
                "url": AHMIA_RECENT_SUBMISSIONS_URL,
            }
        )
    finally:
        connection.close()
    return 0


def cmd_discovery_run_due(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        outcomes = asyncio.run(run_due_sources(connection, limit=args.limit))
        _print_json([asdict(item) for item in outcomes])
    finally:
        connection.close()
    return 0


def cmd_discovery_stats(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        rows = connection.execute(
            """
            SELECT
                s.id, s.name, s.type, s.state, s.enabled,
                s.last_run_at, s.next_run_at, s.cooldown_until,
                s.low_novelty_streak,
                (
                    SELECT novelty_rate
                    FROM discovery_runs r
                    WHERE r.source_id = s.id
                    ORDER BY r.id DESC
                    LIMIT 1
                ) AS last_novelty,
                (
                    SELECT new_services
                    FROM discovery_runs r
                    WHERE r.source_id = s.id
                    ORDER BY r.id DESC
                    LIMIT 1
                ) AS last_new_services
            FROM discovery_sources s
            ORDER BY s.id
            """
        ).fetchall()
        _print_json([dict(row) for row in rows])
    finally:
        connection.close()
    return 0


def cmd_recrawl_schedule(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        _print_json({"scheduled": schedule_due_recrawls(connection, limit=args.limit)})
    finally:
        connection.close()
    return 0


def cmd_worker_once(args: argparse.Namespace) -> int:
    _print_json(asdict(asyncio.run(run_worker_once(Settings.from_env()))))
    return 0


def cmd_worker_run(args: argparse.Namespace) -> int:
    asyncio.run(run_worker_forever(Settings.from_env()))
    return 0


def cmd_worker_probe(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ready = asyncio.run(probe_socks_listener(settings.tor_socks_url))
    _print_json({"tor_socks": settings.tor_socks_url, "listener_ready": ready})
    return 0 if ready else 2


def cmd_worker_list(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        rows = connection.execute(
            """
            SELECT id, status, version, last_heartbeat_at, current_leases, max_concurrency
            FROM workers
            ORDER BY id
            """
        ).fetchall()
        _print_json([dict(row) for row in rows])
    finally:
        connection.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("onionatlas.api:app", host=args.host, port=args.port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onionatlas")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--database", help="SQLite database path (or ONIONATLAS_DB)")
    sub = parser.add_subparsers(dest="command", required=True)

    db = sub.add_parser("db")
    db_sub = db.add_subparsers(required=True)
    p = db_sub.add_parser("init")
    p.set_defaults(func=cmd_db_init)
    p = db_sub.add_parser("check")
    p.set_defaults(func=cmd_db_check)
    p = db_sub.add_parser("backup")
    p.add_argument("path")
    p.set_defaults(func=cmd_db_backup)

    seed = sub.add_parser("seed")
    seed_sub = seed.add_subparsers(required=True)
    p = seed_sub.add_parser("add")
    p.add_argument("url")
    p.add_argument("--priority", type=float, default=100.0)
    p.set_defaults(func=cmd_seed_add)

    frontier = sub.add_parser("frontier")
    frontier_sub = frontier.add_subparsers(required=True)
    p = frontier_sub.add_parser("stats")
    p.set_defaults(func=cmd_frontier_stats)
    p = frontier_sub.add_parser("lease")
    p.add_argument("--worker-id", default="local-worker")
    p.add_argument("--limit", type=int, default=2)
    p.add_argument("--lease-seconds", type=int, default=300)
    p.set_defaults(func=cmd_frontier_lease)

    p = sub.add_parser("import-result")
    p.add_argument("path")
    p.set_defaults(func=cmd_import_result)

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_search)

    service = sub.add_parser("service")
    service_sub = service.add_subparsers(required=True)
    p = service_sub.add_parser("show")
    p.add_argument("onion")
    p.set_defaults(func=cmd_service_show)

    graph = sub.add_parser("graph")
    graph_sub = graph.add_subparsers(required=True)
    p = graph_sub.add_parser("neighbors")
    p.add_argument("onion")
    p.set_defaults(func=cmd_graph_neighbors)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    discovery = sub.add_parser("discovery")
    discovery_sub = discovery.add_subparsers(required=True)
    p = discovery_sub.add_parser("add-http-source")
    p.add_argument("name")
    p.add_argument("url_template")
    p.add_argument("--query", action="append")
    p.add_argument("--interval-seconds", type=int, default=21600)
    p.set_defaults(func=cmd_discovery_add_http)
    p = discovery_sub.add_parser("add-ahmia")
    p.add_argument("--interval-seconds", type=int, default=21600)
    p.set_defaults(func=cmd_discovery_add_ahmia)
    p = discovery_sub.add_parser("run-due")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_discovery_run_due)
    p = discovery_sub.add_parser("stats")
    p.set_defaults(func=cmd_discovery_stats)

    recrawl = sub.add_parser("recrawl")
    recrawl_sub = recrawl.add_subparsers(required=True)
    p = recrawl_sub.add_parser("schedule")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_recrawl_schedule)

    worker = sub.add_parser("worker")
    worker_sub = worker.add_subparsers(required=True)
    p = worker_sub.add_parser("once")
    p.set_defaults(func=cmd_worker_once)
    p = worker_sub.add_parser("run")
    p.set_defaults(func=cmd_worker_run)
    p = worker_sub.add_parser("probe-tor")
    p.set_defaults(func=cmd_worker_probe)
    p = worker_sub.add_parser("list")
    p.set_defaults(func=cmd_worker_list)

    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
