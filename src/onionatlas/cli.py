from __future__ import annotations

import argparse
import json
from pathlib import Path

from onionatlas import __version__
from onionatlas.config import Settings
from onionatlas.frontier import enqueue_url, frontier_stats, lease_tasks
from onionatlas.storage import connect, migrate


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onionatlas")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--database", help="SQLite database path (or ONIONATLAS_DB)")
    sub = parser.add_subparsers(dest="command", required=True)

    db_parser = sub.add_parser("db")
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)
    db_init = db_sub.add_parser("init")
    db_init.set_defaults(func=cmd_db_init)

    seed_parser = sub.add_parser("seed")
    seed_sub = seed_parser.add_subparsers(dest="seed_command", required=True)
    seed_add = seed_sub.add_parser("add")
    seed_add.add_argument("url")
    seed_add.add_argument("--priority", type=float, default=100.0)
    seed_add.set_defaults(func=cmd_seed_add)

    frontier_parser = sub.add_parser("frontier")
    frontier_sub = frontier_parser.add_subparsers(dest="frontier_command", required=True)
    stats = frontier_sub.add_parser("stats")
    stats.set_defaults(func=cmd_frontier_stats)
    lease = frontier_sub.add_parser("lease")
    lease.add_argument("--worker-id", default="local-worker")
    lease.add_argument("--limit", type=int, default=2)
    lease.add_argument("--lease-seconds", type=int, default=300)
    lease.set_defaults(func=cmd_frontier_lease)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
