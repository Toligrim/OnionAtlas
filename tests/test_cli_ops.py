from __future__ import annotations

from argparse import Namespace

from onionatlas.cli import (
    AHMIA_RECENT_SUBMISSIONS_URL,
    cmd_db_backup,
    cmd_db_check,
    cmd_discovery_add_ahmia,
)
from onionatlas.storage import connect, migrate


def _args(db_path, **extra):
    return Namespace(database=str(db_path), **extra)


def test_db_check_and_backup(tmp_path) -> None:
    database = tmp_path / "onionatlas.sqlite3"
    connection = connect(database)
    migrate(connection)
    connection.close()

    assert cmd_db_check(_args(database)) == 0

    backup = tmp_path / "backup.sqlite3"
    assert cmd_db_backup(_args(database, path=str(backup))) == 0

    backup_connection = connect(backup)
    try:
        assert backup_connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        backup_connection.close()


def test_add_ahmia_recent_submissions_source(tmp_path) -> None:
    database = tmp_path / "onionatlas.sqlite3"
    connection = connect(database)
    migrate(connection)
    connection.close()

    assert cmd_discovery_add_ahmia(_args(database, interval_seconds=21600)) == 0

    connection = connect(database)
    try:
        row = connection.execute(
            "SELECT name, config_json FROM discovery_sources"
        ).fetchone()
        assert row["name"] == "ahmia-recent-submissions"
        assert AHMIA_RECENT_SUBMISSIONS_URL in row["config_json"]
    finally:
        connection.close()
