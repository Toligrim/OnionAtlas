from __future__ import annotations

import argparse
import asyncio
import json
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
from onionatlas.worker.client import run_worker_forever, run_worker_once


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
    connection = _connection(args); connection.close(); print(str(_database_path(args))); return 0


def cmd_seed_add(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        outcome = enqueue_url(connection, args.url, depth=0, priority=args.priority, evidence_type="manual", requeue_existing=True)
        _print_json({"frontier_id": outcome.frontier_id, "service_id": outcome.service_id, "url": outcome.canonical.url, "service_created": outcome.service_created, "frontier_created": outcome.frontier_created})
    finally:
        connection.close()
    return 0


def cmd_frontier_stats(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try: _print_json(frontier_stats(connection))
    finally: connection.close()
    return 0


def cmd_frontier_lease(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try: _print_json([task.to_dict() for task in lease_tasks(connection, worker_id=args.worker_id, limit=args.limit, lease_seconds=args.lease_seconds)])
    finally: connection.close()
    return 0


def cmd_import_result(args: argparse.Namespace) -> int:
    result = CrawlResult.from_dict(json.loads(Path(args.path).read_text(encoding="utf-8")))
    connection = _connection(args)
    try: _print_json(asdict(import_result(connection, result)))
    finally: connection.close()
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try: _print_json([asdict(hit) for hit in search_pages(connection, args.query, limit=args.limit)])
    finally: connection.close()
    return 0


def cmd_service_show(args: argparse.Namespace) -> int:
    host = args.onion.lower().removesuffix("/")
    if host.startswith("http://") or host.startswith("https://"):
        from onionatlas.domain.urls import canonicalize_onion_url
        host = canonicalize_onion_url(host).onion_host
    connection = _connection(args)
    try:
        row = connection.execute("SELECT * FROM services WHERE onion_host = ?", (host,)).fetchone()
        if row is None:
            _print_json({"found": False, "onion_host": host}); return 1
        payload = dict(row)
        payload["evidence"] = [dict(item) for item in connection.execute("SELECT evidence_type, source_url, anchor_text, observed_at, is_first_discovery FROM service_evidence WHERE service_id = ? ORDER BY observed_at DESC LIMIT 50", (row["id"],))]
        _print_json(payload)
    finally: connection.close()
    return 0


def cmd_graph_neighbors(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try: _print_json([asdict(item) for item in service_neighbors(connection, args.onion)])
    finally: connection.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        _print_json({"services": int(connection.execute("SELECT COUNT(*) FROM services").fetchone()[0]), "pages": int(connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]), "fetches": int(connection.execute("SELECT COUNT(*) FROM fetches").fetchone()[0]), "links": int(connection.execute("SELECT COUNT(*) FROM links").fetchone()[0]), "frontier": frontier_stats(connection)})
    finally: connection.close()
    return 0


def cmd_discovery_add_http(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        source_id = ensure_http_source(connection, name=args.name, url_template=args.url_template, queries=args.query or [""], interval_seconds=args.interval_seconds)
        _print_json({"source_id": source_id, "name": args.name})
    finally: connection.close()
    return 0


def cmd_discovery_run_due(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try: _print_json([asdict(item) for item in asyncio.run(run_due_sources(connection, limit=args.limit))])
    finally: connection.close()
    return 0


def cmd_recrawl_schedule(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try: _print_json({"scheduled": schedule_due_recrawls(connection, limit=args.limit)})
    finally: connection.close()
    return 0


def cmd_worker_once(args: argparse.Namespace) -> int:
    _print_json(asdict(asyncio.run(run_worker_once(Settings.from_env())))); return 0


def cmd_worker_run(args: argparse.Namespace) -> int:
    asyncio.run(run_worker_forever(Settings.from_env())); return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("onionatlas.api:app", host=args.host, port=args.port, reload=False); return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onionatlas")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--database", help="SQLite database path (or ONIONATLAS_DB)")
    sub = parser.add_subparsers(dest="command", required=True)

    db = sub.add_parser("db"); dbs = db.add_subparsers(required=True); p = dbs.add_parser("init"); p.set_defaults(func=cmd_db_init)
    seed = sub.add_parser("seed"); seeds = seed.add_subparsers(required=True); p = seeds.add_parser("add"); p.add_argument("url"); p.add_argument("--priority", type=float, default=100.0); p.set_defaults(func=cmd_seed_add)
    frontier = sub.add_parser("frontier"); fs = frontier.add_subparsers(required=True); p = fs.add_parser("stats"); p.set_defaults(func=cmd_frontier_stats); p = fs.add_parser("lease"); p.add_argument("--worker-id", default="local-worker"); p.add_argument("--limit", type=int, default=2); p.add_argument("--lease-seconds", type=int, default=300); p.set_defaults(func=cmd_frontier_lease)
    p = sub.add_parser("import-result"); p.add_argument("path"); p.set_defaults(func=cmd_import_result)
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--limit", type=int, default=20); p.set_defaults(func=cmd_search)
    service = sub.add_parser("service"); ss = service.add_subparsers(required=True); p = ss.add_parser("show"); p.add_argument("onion"); p.set_defaults(func=cmd_service_show)
    graph = sub.add_parser("graph"); gs = graph.add_subparsers(required=True); p = gs.add_parser("neighbors"); p.add_argument("onion"); p.set_defaults(func=cmd_graph_neighbors)
    p = sub.add_parser("status"); p.set_defaults(func=cmd_status)
    discovery = sub.add_parser("discovery"); ds = discovery.add_subparsers(required=True); p = ds.add_parser("add-http-source"); p.add_argument("name"); p.add_argument("url_template"); p.add_argument("--query", action="append"); p.add_argument("--interval-seconds", type=int, default=21600); p.set_defaults(func=cmd_discovery_add_http); p = ds.add_parser("run-due"); p.add_argument("--limit", type=int, default=5); p.set_defaults(func=cmd_discovery_run_due)
    recrawl = sub.add_parser("recrawl"); rs = recrawl.add_subparsers(required=True); p = rs.add_parser("schedule"); p.add_argument("--limit", type=int, default=100); p.set_defaults(func=cmd_recrawl_schedule)
    worker = sub.add_parser("worker"); ws = worker.add_subparsers(required=True); p = ws.add_parser("once"); p.set_defaults(func=cmd_worker_once); p = ws.add_parser("run"); p.set_defaults(func=cmd_worker_run)
    p = sub.add_parser("serve"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8080); p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv); return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
