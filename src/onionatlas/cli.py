from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from onionatlas import __version__
from onionatlas.config import Settings
from onionatlas.domain.results import CrawlResult
from onionatlas.frontier import enqueue_url, frontier_stats, lease_tasks
from onionatlas.graph import service_neighbors
from onionatlas.importer import import_result
from onionatlas.search import search_pages
from onionatlas.storage import connect, migrate


def _database_path(args: argparse.Namespace) -> Path:
    value = getattr(args, "database", None)
    return Path(value) if value else Settings.from_env().database_path


def _connection(args: argparse.Namespace):
    connection = connect(_database_path(args)); migrate(connection); return connection


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_db_init(args):
    c = _connection(args); c.close(); print(str(_database_path(args))); return 0


def cmd_seed_add(args):
    c = _connection(args)
    try:
        o = enqueue_url(c, args.url, depth=0, priority=args.priority, evidence_type="manual", requeue_existing=True)
        _print_json({"frontier_id": o.frontier_id, "service_id": o.service_id, "url": o.canonical.url, "service_created": o.service_created, "frontier_created": o.frontier_created})
    finally: c.close()
    return 0


def cmd_frontier_stats(args):
    c = _connection(args)
    try: _print_json(frontier_stats(c))
    finally: c.close()
    return 0


def cmd_frontier_lease(args):
    c = _connection(args)
    try: _print_json([t.to_dict() for t in lease_tasks(c, worker_id=args.worker_id, limit=args.limit, lease_seconds=args.lease_seconds)])
    finally: c.close()
    return 0


def cmd_import_result(args):
    result = CrawlResult.from_dict(json.loads(Path(args.path).read_text(encoding="utf-8")))
    c = _connection(args)
    try: _print_json(asdict(import_result(c, result)))
    finally: c.close()
    return 0


def cmd_search(args):
    c = _connection(args)
    try: _print_json([asdict(hit) for hit in search_pages(c, args.query, limit=args.limit)])
    finally: c.close()
    return 0


def cmd_service_show(args):
    host = args.onion.lower().removesuffix("/")
    if host.startswith("http://") or host.startswith("https://"):
        from onionatlas.domain.urls import canonicalize_onion_url
        host = canonicalize_onion_url(host).onion_host
    c = _connection(args)
    try:
        row = c.execute("SELECT * FROM services WHERE onion_host = ?", (host,)).fetchone()
        if row is None: _print_json({"found": False, "onion_host": host}); return 1
        payload = dict(row)
        payload["evidence"] = [dict(item) for item in c.execute("SELECT evidence_type, source_url, anchor_text, observed_at, is_first_discovery FROM service_evidence WHERE service_id = ? ORDER BY observed_at DESC LIMIT 50", (row["id"],))]
        _print_json(payload)
    finally: c.close()
    return 0


def cmd_graph_neighbors(args):
    c = _connection(args)
    try: _print_json([asdict(item) for item in service_neighbors(c, args.onion)])
    finally: c.close()
    return 0


def cmd_status(args):
    c = _connection(args)
    try: _print_json({"services": int(c.execute("SELECT COUNT(*) FROM services").fetchone()[0]), "pages": int(c.execute("SELECT COUNT(*) FROM pages").fetchone()[0]), "fetches": int(c.execute("SELECT COUNT(*) FROM fetches").fetchone()[0]), "links": int(c.execute("SELECT COUNT(*) FROM links").fetchone()[0]), "frontier": frontier_stats(c)})
    finally: c.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onionatlas"); parser.add_argument("--version", action="version", version=__version__); parser.add_argument("--database")
    sub = parser.add_subparsers(dest="command", required=True)
    db = sub.add_parser("db"); dbs = db.add_subparsers(required=True); p = dbs.add_parser("init"); p.set_defaults(func=cmd_db_init)
    seed = sub.add_parser("seed"); seeds = seed.add_subparsers(required=True); p = seeds.add_parser("add"); p.add_argument("url"); p.add_argument("--priority", type=float, default=100.0); p.set_defaults(func=cmd_seed_add)
    frontier = sub.add_parser("frontier"); fs = frontier.add_subparsers(required=True); p = fs.add_parser("stats"); p.set_defaults(func=cmd_frontier_stats); p = fs.add_parser("lease"); p.add_argument("--worker-id", default="local-worker"); p.add_argument("--limit", type=int, default=2); p.add_argument("--lease-seconds", type=int, default=300); p.set_defaults(func=cmd_frontier_lease)
    p = sub.add_parser("import-result"); p.add_argument("path"); p.set_defaults(func=cmd_import_result)
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--limit", type=int, default=20); p.set_defaults(func=cmd_search)
    service = sub.add_parser("service"); ss = service.add_subparsers(required=True); p = ss.add_parser("show"); p.add_argument("onion"); p.set_defaults(func=cmd_service_show)
    graph = sub.add_parser("graph"); gs = graph.add_subparsers(required=True); p = gs.add_parser("neighbors"); p.add_argument("onion"); p.set_defaults(func=cmd_graph_neighbors)
    p = sub.add_parser("status"); p.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv); return int(args.func(args))


if __name__ == "__main__": raise SystemExit(main())
